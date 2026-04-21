"""Login flow strategies — extracts the two phases that InstaLogin
orchestrates: cookie-restore and form-login.

Why this module exists:
  InstaLogin.login() used to mix cookie-restore, form-fill, button
  fallback, and post-login cleanup in ~100 lines of procedural code.
  Login page redesigns, selector changes, and button text
  translations are IG's most frequent UI edits — fragile code with
  high maintenance cost. Now each phase lives in its own class with
  a narrow interface; InstaLogin.login() orchestrates in ~15 lines.

Phases modelled as classes:
  SessionRestorer.attempt(driver, username, session_cache) -> bool
    Phase 1 — try cookie cache. True if session is live after the
    restore. False means cookies were missing/invalid; caller should
    fall through to form login.

  FormLogin.execute(driver, username, password) -> None
    Phase 2 — navigate to login page, fill credentials, submit via
    Enter, fall back to button-click cascade. Raises on hard failure
    (page didn't load, credentials couldn't be entered, no login
    button matched). Does NOT check for blocks / challenges — the
    caller handles that after.

Kept out of these classes:
  - Challenge resolution → instat/challenge_resolvers.py
  - Block detection     → instat/block_detector.py
  - Save-login-modal dismiss → utility in instat.utils (called by InstaLogin)

This separation means IG changes in one area touch only the file
for that area.
"""
from typing import Any, List

from loguru import logger
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from instat.constants import LOGIN_POST_CLICK_DELAY, human_delay
    from instat.utils import Utils
except ImportError:
    from constants import LOGIN_POST_CLICK_DELAY, human_delay  # type: ignore
    from utils import Utils  # type: ignore


class SessionRestorer:
    """Phase 1: restore session from persisted cookies.

    Purpose: if we have valid cookies from a previous login, use them
    and skip the form entirely. This is both faster (~2s vs ~30s) and
    less suspicious to IG (re-logging in frequently is a flag).

    Success criteria (both must hold):
      - after refresh, URL does NOT contain '/accounts/login'
      - `sessionid` cookie is present on the driver
    Either failure → delete all cookies from driver and return False
    so caller does a clean form-login.
    """

    LOGIN_URL_MARKER = '/accounts/login'

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    def attempt(self, driver: Any, cookies: List[dict]) -> bool:
        if not cookies:
            return False
        try:
            driver.get(f'{self._base_url}/')
            added = self._add_cookies(driver, cookies)
            if added == 0:
                return False
            driver.refresh()
            if self._stuck_on_login(driver):
                self._clear_cookies(driver)
                return False
            if not self._has_sessionid(driver):
                self._clear_cookies(driver)
                return False
            logger.info('Session restored from cookie cache.')
            return True
        except Exception as e:
            logger.debug(f'Failed to restore session from cache: {e}')
            self._clear_cookies(driver)
            return False

    @staticmethod
    def _add_cookies(driver: Any, cookies: List[dict]) -> int:
        added = 0
        failed = 0
        for c in cookies:
            try:
                driver.add_cookie(c)
                added += 1
            except Exception as e:
                failed += 1
                logger.debug(f"cookie add failed: {c.get('name','?')} ({e})")
        logger.debug(f"Cookie cache: {added} added, {failed} failed")
        return added

    def _stuck_on_login(self, driver: Any) -> bool:
        try:
            return self.LOGIN_URL_MARKER in driver.current_url.lower()
        except Exception:
            return False

    @staticmethod
    def _has_sessionid(driver: Any) -> bool:
        try:
            return bool(driver.get_cookie('sessionid'))
        except Exception:
            return False

    @staticmethod
    def _clear_cookies(driver: Any) -> None:
        try:
            driver.delete_all_cookies()
        except Exception:
            pass


class FormLogin:
    """Phase 2: classic form-fill login with Enter-submit and a
    button-click fallback.

    Public method: `execute(driver, username, password)`. Raises on
    hard failure. Does NOT check for challenges or blocks afterward —
    that's the caller's job (via ChallengeResolverChain and
    BlockDetector).

    Login button detection is text-keyword based because IG's login
    button DOM shape varies between app versions and A/B tests.
    LOGIN_BUTTON_KEYWORDS lives on the class so localizations can be
    added via subclass without touching this code.
    """

    LOGIN_URL_PATH = "/accounts/login/"

    USERNAME_INPUT_KEY = "LOGIN_USERNAME_INPUT"
    PASSWORD_INPUT_KEY = "LOGIN_PASSWORD_INPUT"
    LOGIN_BUTTON_CANDIDATE_KEY = "LOGIN_BUTTON_CANDIDATE"

    # Keywords to match the login button's textContent across locales.
    LOGIN_BUTTON_KEYWORDS: List[str] = [
        "entrar", "log in", "login", "iniciar sesión",
        "connexion", "anmelden",
    ]

    def __init__(
        self, selector_loader: Any, base_url: str,
        timeout: int = 10,
    ) -> None:
        self._selectors = selector_loader
        self._base_url = base_url
        self._timeout = timeout

    @property
    def login_url(self) -> str:
        return f"{self._base_url}{self.LOGIN_URL_PATH}"

    def execute(
        self, driver: Any, username: str, password: str,
    ) -> None:
        """Run the form flow. Raises Exception on hard failure."""
        self._open_login_page(driver)
        username_input, password_input = self._wait_for_form_fields(driver)
        self._fill_and_submit(username_input, password_input, username, password)
        # The "Ignorar" bar sometimes appears. Dismiss it if present.
        Utils.click_ignore_button_if_present(
            driver, timeout=5, wait_before_click=1,
        )
        self._wait_for_redirect_or_fallback_click(driver)

    # --------------------------- steps -----------------------------

    def _open_login_page(self, driver: Any) -> None:
        try:
            logger.info("Navigating to Instagram login page")
            driver.get(self.login_url)
        except WebDriverException as e:
            logger.exception("Error loading Instagram login page")
            raise Exception("Failed to load Instagram login page.") from e

    def _wait_for_form_fields(self, driver: Any):
        wait = WebDriverWait(driver, self._timeout)
        try:
            logger.debug("Waiting for username and password fields to be visible")
            username_input = wait.until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, self._selectors.get(self.USERNAME_INPUT_KEY))
                )
            )
            password_input = wait.until(
                EC.visibility_of_element_located(
                    (By.CSS_SELECTOR, self._selectors.get(self.PASSWORD_INPUT_KEY))
                )
            )
            return username_input, password_input
        except TimeoutException as e:
            logger.exception("Timeout waiting for login form elements")
            raise Exception(
                "Login failed: Timeout waiting for login form elements."
            ) from e
        except WebDriverException as e:
            logger.exception("Error locating login form elements")
            raise Exception(
                "Login failed: WebDriver error during element location."
            ) from e

    @staticmethod
    def _fill_and_submit(
        username_input: Any, password_input: Any,
        username: str, password: str,
    ) -> None:
        try:
            logger.info("Entering login credentials")
            username_input.clear()
            username_input.send_keys(username)
            password_input.clear()
            password_input.send_keys(password)
            password_input.send_keys(Keys.RETURN)
        except WebDriverException as e:
            logger.exception("Error entering credentials")
            raise Exception("Login failed: Unable to enter credentials.") from e

    def _wait_for_redirect_or_fallback_click(self, driver: Any) -> None:
        """After Enter, IG usually redirects. If it didn't (some
        versions only accept explicit button click), try the button
        cascade."""
        try:
            logger.debug("Waiting for login result via redirect")
            login_url = self.login_url
            WebDriverWait(driver, self._timeout).until(
                lambda d: d.current_url != login_url
            )
            return
        except TimeoutException:
            logger.debug("Login via RETURN key failed, trying fallback button click...")
            self._fallback_button_click(driver)

    def _fallback_button_click(self, driver: Any) -> None:
        """Find a login-button candidate whose textContent matches
        one of LOGIN_BUTTON_KEYWORDS, click it, and wait for redirect."""
        try:
            candidates = driver.find_elements(
                By.XPATH,
                self._selectors.get(self.LOGIN_BUTTON_CANDIDATE_KEY),
            )
            logger.debug(f"Found {len(candidates)} login button candidates")
            for btn in candidates:
                try:
                    inner_text = (btn.get_attribute("textContent") or "").strip().casefold()
                    if not any(kw in inner_text for kw in self.LOGIN_BUTTON_KEYWORDS):
                        continue
                    logger.debug(
                        f"Found login button with matching text: '{inner_text}', "
                        "clicking it."
                    )
                    btn.click()
                    self._wait_for_post_click_redirect(driver)
                    human_delay(LOGIN_POST_CLICK_DELAY)
                    return
                except Exception as e:
                    logger.debug(f"Skipping one candidate button due to error: {e}")
            logger.error("No login button matched expected keywords.")
            raise Exception("Login failed: No suitable login button found.")
        except Exception as e:
            logger.exception("Fallback login button click failed")
            raise Exception(
                "Login failed: Unable to login using fallback method."
            ) from e

    def _wait_for_post_click_redirect(self, driver: Any) -> None:
        login_url = self.login_url
        WebDriverWait(driver, self._timeout).until(
            lambda d, u=login_url: d.current_url != u
        )
        WebDriverWait(driver, self._timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )


__all__ = ['SessionRestorer', 'FormLogin']
