"""Challenge resolvers — Strategy pattern for IG verification flows.

Why this module exists:
  Instagram has multiple challenge flows that look similar on the
  surface but need different click/fill strategies:
    - Native "Check your email" (CSS `input[aria-label='Enter code']`,
      standard HTML form).
    - Meta Bloks `auth_platform/codeentry` (custom framework, listens
      for pointer events, button label nested 3 levels deep).
  Before this module, both were handled in one 150-line method inside
  InstaLogin. Adding a new flow (SMS, Authenticator app, CAPTCHA)
  meant editing that monolith. Now each flow is its own Strategy
  class and the chain tries them in order.

Contract:
  class ChallengeResolver(Protocol):
      def can_handle(self, driver) -> bool: ...
      def resolve(self, driver) -> bool: ...

  can_handle() is a cheap DOM sniff (check for a heading / URL
  pattern). resolve() does the actual IMAP fetch + fill + click.

  ChallengeResolverChain composes N resolvers and tries them in
  declared order. First `can_handle` → True gets to run `resolve`.

Wiring:
  InstaLogin holds a chain and calls `chain.try_resolve(driver)`
  from its login flow. Default chain registers EmailChallengeResolver.
  Users who hit new flows (e.g. Bloks codeentry) register a resolver
  via `chain.register(BloksCodeEntryResolver(...))`.

Adding a new resolver:
  1. Subclass ChallengeResolver.
  2. Implement can_handle() — check a specific heading/URL marker.
  3. Implement resolve() — use SelectorLoader keys if possible
     (keeps selector updates in selectors.json, not in Python).
  4. Register in the chain.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

from loguru import logger
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

try:
    from instat.constants import human_delay
    from instat.email_code import ImapConfig, fetch_instagram_code
except ImportError:
    from constants import human_delay  # type: ignore
    from email_code import ImapConfig, fetch_instagram_code  # type: ignore


class ChallengeResolver(ABC):
    """Abstract base — one flow per concrete class."""

    @abstractmethod
    def can_handle(self, driver: Any) -> bool:
        """Cheap DOM sniff. True if this resolver should try resolve().
        Must not mutate driver state."""

    @abstractmethod
    def resolve(self, driver: Any) -> bool:
        """Attempt resolution. Return True on success, False on any
        failure (caller then tries next resolver or gives up).
        May mutate driver (fill inputs, click buttons)."""


class ChallengeResolverChain:
    """Sequential composition of resolvers.

    Call order matters — first `can_handle(driver)` returning True
    gets to run `resolve`. If that returns False, the chain stops
    (does NOT try subsequent resolvers) because the driver state has
    already been mutated. The caller decides retry strategy.
    """

    def __init__(self, resolvers: Optional[List[ChallengeResolver]] = None):
        self._resolvers: List[ChallengeResolver] = list(resolvers or [])

    def register(self, resolver: ChallengeResolver) -> None:
        self._resolvers.append(resolver)

    def unregister_all(self) -> None:
        self._resolvers.clear()

    def __len__(self) -> int:
        return len(self._resolvers)

    def try_resolve(self, driver: Any) -> bool:
        """Try each registered resolver in order. Return True if one
        of them claimed to handle AND resolved successfully."""
        for resolver in self._resolvers:
            try:
                if resolver.can_handle(driver):
                    logger.info(
                        f"challenge chain: {type(resolver).__name__} "
                        "matched — resolving"
                    )
                    return bool(resolver.resolve(driver))
            except Exception as e:
                logger.warning(
                    f"challenge chain: {type(resolver).__name__} raised: "
                    f"{type(e).__name__}: {e}"
                )
                return False
        return False


# ---------------------------------------------------------------------
# Concrete resolvers
# ---------------------------------------------------------------------

class EmailChallengeResolver(ChallengeResolver):
    """IG native 'Check your email' flow.

    Detection: presence of `h2[aria-label='Check your email']` (or
    localized variants loaded from selectors.json).
    Resolution: click 'Get a new code' → IMAP poll → fill input via
    React-compatible event dispatch → click Continue with a 4-strategy
    cascade.
    """

    HEADING_KEY = "EMAIL_CHALLENGE_HEADING"
    NEW_CODE_KEY = "EMAIL_CHALLENGE_GET_NEW_CODE"
    INPUT_KEY = "EMAIL_CHALLENGE_INPUT"
    CONTINUE_KEY = "EMAIL_CHALLENGE_CONTINUE"

    def __init__(
        self, selector_loader: Any, imap_config: Any,
        timeout: int = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._selectors = selector_loader
        self._imap_config = imap_config
        self._timeout = timeout
        self._clock = clock

    # ------------------------ detection -----------------------------

    def can_handle(self, driver: Any) -> bool:
        if not self._imap_config:
            return False
        for sel in self._selectors.get_all(self.HEADING_KEY):
            try:
                driver.find_element(By.CSS_SELECTOR, sel)
                return True
            except NoSuchElementException:
                continue
        return False

    # ------------------------ resolution ----------------------------

    def resolve(self, driver: Any) -> bool:
        logger.info("Detected email verification challenge — requesting fresh code")
        started_at = self._click_get_new_code_then_mark_time(driver)

        code = self._fetch_code_via_imap(started_at)
        if not code:
            return False

        input_el = self._find_input(driver)
        if input_el is None:
            logger.warning("Email challenge input not found after detection")
            return False

        self._fill_input(driver, input_el, code)

        if not self._click_continue(driver, input_el):
            logger.warning("Could not click Continue on email challenge")
            return False

        return self._wait_challenge_gone(driver)

    # ------------------------ steps ---------------------------------

    def _click_get_new_code_then_mark_time(self, driver: Any) -> datetime:
        """Click 'Get a new code' (if present) and return the UTC
        timestamp MARKED BEFORE the click — used as IMAP since filter
        so we never pick up residual codes from prior attempts."""
        clicked = False
        for sel in self._selectors.get_all(self.NEW_CODE_KEY):
            try:
                el = driver.find_element(By.XPATH, sel)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                clicked = True
                logger.info("Clicked 'Get a new code' to trigger fresh email")
                break
            except NoSuchElementException:
                continue
        started_at = self._clock()
        if clicked:
            # Wait for IG to process + SMTP deliver.
            human_delay(6.0, variance=1.0)
        return started_at

    def _fetch_code_via_imap(self, started_at: datetime) -> Optional[str]:
        cfg = ImapConfig.from_dict(self._imap_config)
        code = fetch_instagram_code(cfg, started_at=started_at)
        if not code:
            logger.warning("IMAP fetch returned no code; falling back to block detection")
            return None
        return code

    def _find_input(self, driver: Any) -> Optional[Any]:
        for sel in self._selectors.get_all(self.INPUT_KEY):
            try:
                return driver.find_element(By.CSS_SELECTOR, sel)
            except NoSuchElementException:
                continue
        return None

    @staticmethod
    def _fill_input(driver: Any, input_el: Any, code: str) -> None:
        try:
            input_el.clear()
        except Exception:
            pass
        input_el.send_keys(code)
        # React-compatible value dispatch so IG's form state updates
        # and enables the Continue button.
        try:
            driver.execute_script(
                "const el = arguments[0]; const setter = "
                "Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; "
                "setter.call(el, arguments[1]); "
                "el.dispatchEvent(new Event('input', {bubbles: true})); "
                "el.dispatchEvent(new Event('change', {bubbles: true})); "
                "el.blur();",
                input_el, code,
            )
        except Exception as e:
            logger.debug(f"React event dispatch failed (non-fatal): {e}")
        human_delay(0.8, variance=0.2)

    def _click_continue(self, driver: Any, input_el: Any) -> bool:
        """Click Continue button with the 4-strategy cascade IG Bloks
        occasionally requires. Returns True if ANY strategy succeeded
        (i.e., the challenge heading disappeared)."""
        btn = self._find_continue_button(driver)
        if btn is None:
            # Fallback to sending Enter on the input
            try:
                input_el.send_keys(Keys.RETURN)
                human_delay(1.5, variance=0.3)
                return self._challenge_gone(driver)
            except Exception:
                return False

        for strategy in ('ancestor_js', 'native', 'js', 'enter'):
            try:
                if strategy == 'ancestor_js':
                    driver.execute_script(
                        "let el = arguments[0]; "
                        "while (el) { "
                        "  const st = window.getComputedStyle(el); "
                        "  if (st.cursor === 'pointer' && st.pointerEvents !== 'none') { "
                        "    el.click(); return; "
                        "  } "
                        "  el = el.parentElement; "
                        "} "
                        "arguments[0].click();",
                        btn,
                    )
                elif strategy == 'native':
                    btn.click()
                elif strategy == 'js':
                    driver.execute_script("arguments[0].click();", btn)
                else:
                    input_el.send_keys(Keys.RETURN)
                logger.debug(f"Continue clicked via {strategy}")
                human_delay(1.5, variance=0.3)
                if self._challenge_gone(driver):
                    return True
            except Exception as e:
                logger.debug(f"Continue click via {strategy} failed: {e}")
        return False

    def _find_continue_button(self, driver: Any) -> Optional[Any]:
        for sel in self._selectors.get_all(self.CONTINUE_KEY):
            try:
                return driver.find_element(By.XPATH, sel)
            except NoSuchElementException:
                continue
        return None

    def _challenge_gone(self, driver: Any) -> bool:
        """True if the heading that originally matched is gone."""
        for sel in self._selectors.get_all(self.HEADING_KEY):
            try:
                driver.find_element(By.CSS_SELECTOR, sel)
                return False
            except NoSuchElementException:
                continue
        return True

    def _wait_challenge_gone(self, driver: Any) -> bool:
        try:
            WebDriverWait(driver, self._timeout).until(
                lambda d: self._challenge_gone(d)
            )
            return True
        except TimeoutException:
            logger.warning("Email challenge page still present after Continue")
            return False


__all__ = [
    'ChallengeResolver', 'ChallengeResolverChain',
    'EmailChallengeResolver',
]
