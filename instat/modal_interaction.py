"""ModalInteraction — abstracts follower/following modal lifecycle.

Why this module exists:
  Opening the followers/following modal requires three steps that IG
  changes independently:
    1. Find the correct link on the profile page (selector variants).
    2. Click it (native vs JS fallback — link may be covered by
       overlays, tooltips, etc.).
    3. Wait for the dialog `div[role="dialog"]` to appear.
  On top of that, SeleniumEngine has a separate `reopen` flow that
  performs steps 1-3 AGAIN after closing the current modal to reset
  IG's server-side pagination cursor when rate-limit is suspected.
  These flows were inline in SeleniumEngine; selectors were scattered,
  error handling was duplicated, and adding (say) a "retry open with
  slightly different URL" variant meant touching the main extract
  method.

This module encapsulates the three-step lifecycle and the reopen
sequence behind a single class that only knows about a WebDriver
and a SelectorLoader. SeleniumEngine delegates.

Contract:
  ModalInteraction(driver, selectors, timeout, base_url,
                   dismiss_save_login=None)
    open(profile_id, list_type) -> link_element | None
        Navigates to profile, dismisses save-login modal (first
        time only via `dismiss_save_login` callback), returns the
        link element for followers/following (not yet clicked).
        Raises ProfileNotFoundError if no link found.
    click_link(link, list_type) -> bool
        Clicks the link with native → JS cascade.
    wait_dialog() -> bool
        Waits for the modal dialog to appear.
    open_and_click(profile_id, list_type) -> bool
        Convenience: open + click_link + wait_dialog in one call.
    close() -> None
        Closes the modal (CLOSE_MODAL_BUTTON with ESC fallback).
    reset_page() -> None
        Navigate driver to about:blank to clear DOM state (PERF-02).
    reopen(profile_id, list_type) -> bool
        Full reopen cycle: close → re-navigate → re-click → wait
        dialog. Returns True on success.

No behavioural change — same selector keys, same timeouts, same
error paths. Just moved out of SeleniumEngine.
"""
from typing import Any, Callable, Optional

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
    from instat.constants import human_delay
    from instat.exceptions import ProfileNotFoundError
    from instat.utils import Utils
except ImportError:
    from constants import human_delay  # type: ignore
    from exceptions import ProfileNotFoundError  # type: ignore
    from utils import Utils  # type: ignore


# Selector keys — match selectors.json.
FOLLOWERS_LINK_KEY = "FOLLOWERS_LINK"
FOLLOWING_LINK_KEY = "FOLLOWING_LINK"
CLOSE_MODAL_BUTTON_KEY = "CLOSE_MODAL_BUTTON"
DIALOG_CSS = 'div[role="dialog"]'


class ModalInteraction:
    """Encapsulates open / close / reopen of the followers/following
    modal. Collaborates with a WebDriver + SelectorLoader only."""

    def __init__(
        self, driver: Any, selectors: Any,
        timeout: int = 10,
        base_url: str = "https://www.instagram.com",
        dismiss_save_login: Optional[Callable[[], None]] = None,
    ) -> None:
        self._driver = driver
        self._selectors = selectors
        self._timeout = timeout
        self._base_url = base_url
        # Callable that dismisses IG's "Save your login info?" modal if
        # present. Owner (SeleniumEngine) controls the once-per-session
        # flag; this class calls the callable without trying to be clever.
        self._dismiss_save_login = dismiss_save_login

    # ----------------------- primary API ----------------------------

    def open(self, profile_id: str, list_type: str) -> Optional[Any]:
        """Navigate to profile, dismiss save-login if provided, find
        and return the link element for the requested list type.
        Raises ProfileNotFoundError if the link can't be found."""
        url = f"{self._base_url}/{profile_id}/"
        logger.info("Navigating to profile: {}", url)
        try:
            self._driver.get(url)
        except WebDriverException as e:
            logger.exception("Error navigating to profile: {}", e)
            return None

        if self._dismiss_save_login is not None:
            try:
                self._dismiss_save_login()
            except Exception as e:
                logger.debug(f"dismiss_save_login callback failed: {e}")

        return self._find_list_link(profile_id, list_type)

    def click_link(self, link: Any, list_type: str) -> bool:
        """Native click first; fall back to JS click on failure."""
        try:
            WebDriverWait(self._driver, self._timeout).until(
                EC.element_to_be_clickable(link)
            )
            link.click()
            logger.debug("Clicked on the {} link.", list_type)
            return True
        except (TimeoutException, WebDriverException) as e:
            logger.debug(
                f"Native click failed for {list_type}: "
                f"{type(e).__name__}, trying JS click..."
            )
        try:
            self._driver.execute_script("arguments[0].click();", link)
            logger.debug("Clicked on the {} link via JS.", list_type)
            return True
        except WebDriverException as e:
            logger.warning(f"JS click also failed for {list_type}: {e}")
            return False

    def wait_dialog(self) -> bool:
        """Wait for the modal dialog to appear; True on success."""
        try:
            WebDriverWait(self._driver, self._timeout).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, DIALOG_CSS)
                )
            )
            return True
        except TimeoutException:
            return False

    def open_and_click(self, profile_id: str, list_type: str) -> bool:
        """Open → click → wait. Returns True on success."""
        link = self.open(profile_id, list_type)
        if link is None:
            return False
        if not self.click_link(link, list_type):
            return False
        return self.wait_dialog()

    # ----------------------- close & reset --------------------------

    def close(self) -> None:
        """Close the modal via its close button; fall back to ESC."""
        try:
            close_btn = self._driver.find_element(
                By.XPATH, self._selectors.get(CLOSE_MODAL_BUTTON_KEY)
            )
            close_btn.click()
        except Exception:
            try:
                self._driver.switch_to.active_element.send_keys(Keys.ESCAPE)
            except Exception:
                pass
        human_delay(1.0, variance=0.3)

    def reset_page(self) -> None:
        """Navigate to about:blank to clear DOM state between sequential
        calls. See PERF-02 note."""
        try:
            self._driver.get('about:blank')
            human_delay(0.3, variance=0.1)
        except Exception as e:
            logger.debug(f"reset_page failed silently: {e}")

    def reopen(self, profile_id: str, list_type: str) -> bool:
        """Full reopen: close → re-navigate → re-click → wait dialog.
        Used by extractors when rate-limit is suspected to reset IG's
        server-side pagination cursor. Returns True on success."""
        try:
            self.close()
            # Re-navigate to profile.
            self._driver.get(f"{self._base_url}/{profile_id}/")
            human_delay(2.0, variance=0.5)

            link = self._find_list_link(profile_id, list_type,
                                        raise_on_missing=False)
            if link is None:
                logger.warning("reopen: could not find list link after navigation")
                return False
            if not self.click_link(link, list_type):
                logger.warning("reopen: could not click list link")
                return False
            if not self.wait_dialog():
                logger.warning("reopen: dialog did not appear")
                return False
            logger.info("Modal reopened successfully")
            return True
        except Exception as e:
            logger.warning(f"reopen failed: {e}")
            return False

    # ------------------------ internals -----------------------------

    def _find_list_link(
        self, profile_id: str, list_type: str,
        raise_on_missing: bool = True,
    ) -> Optional[Any]:
        selector_key = self._list_selector_key(list_type)
        if not selector_key:
            return None
        selectors = self._selectors.get_all(selector_key)
        link = Utils.find_element_with_fallback(
            self._driver, selectors, timeout=self._timeout,
        )
        if link:
            return link
        msg = (
            f"Could not find {list_type} link for '{profile_id}'"
        )
        if raise_on_missing:
            logger.error(msg + " with any selector alternative.")
            raise ProfileNotFoundError(msg)
        return None

    @staticmethod
    def _list_selector_key(list_type: str) -> Optional[str]:
        if list_type == 'followers':
            return FOLLOWERS_LINK_KEY
        if list_type == 'following':
            return FOLLOWING_LINK_KEY
        logger.error("Invalid list type provided: {}", list_type)
        return None


__all__ = ['ModalInteraction']
