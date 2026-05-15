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
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone  # noqa: F401
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
    """Sequential composition of resolvers, looped to handle multi-step
    flows.

    Each iteration scans the chain top-to-bottom. The first resolver
    whose `can_handle(driver)` returns True gets to run `resolve`; the
    chain then re-scans from the top in the next iteration, since the
    page state has changed and a different resolver may now match (e.g.
    email step → Bloks codeentry).

    Stops when no resolver matches in a pass (nothing left to do) or
    after `max_iterations` (default 4 — covers email + bloks + 2
    safety margin).
    """

    DEFAULT_MAX_ITERATIONS = 4

    def __init__(self, resolvers: Optional[List[ChallengeResolver]] = None):
        self._resolvers: List[ChallengeResolver] = list(resolvers or [])

    def register(self, resolver: ChallengeResolver) -> None:
        self._resolvers.append(resolver)

    def unregister_all(self) -> None:
        self._resolvers.clear()

    def __len__(self) -> int:
        return len(self._resolvers)

    def try_resolve(
        self, driver: Any,
        max_iterations: Optional[int] = None,
    ) -> bool:
        """Loop the chain up to `max_iterations` times. Each iteration
        runs the first matching resolver. Returns True if at least one
        resolver returned True."""
        limit = (
            max_iterations if max_iterations is not None
            else self.DEFAULT_MAX_ITERATIONS
        )
        resolved_any = False
        for iteration in range(limit):
            matched_in_iter = False
            for resolver in self._resolvers:
                try:
                    if resolver.can_handle(driver):
                        matched_in_iter = True
                        logger.info(
                            f"challenge chain (iter {iteration + 1}/{limit}): "
                            f"{type(resolver).__name__} matched — resolving"
                        )
                        success = bool(resolver.resolve(driver))
                        resolved_any = resolved_any or success
                        # State changed — break inner loop so outer
                        # iteration re-scans the chain from the top.
                        break
                except Exception as e:
                    logger.warning(
                        f"challenge chain: {type(resolver).__name__} raised: "
                        f"{type(e).__name__}: {e}"
                    )
                    return resolved_any
            if not matched_in_iter:
                # Nothing left for the chain to do — page is past
                # all challenge states (or no resolver knows it).
                break
        return resolved_any


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
        diagnostics: Any = None,
    ) -> None:
        self._selectors = selector_loader
        self._imap_config = imap_config
        self._timeout = timeout
        self._clock = clock
        self._diagnostics = diagnostics

    def _dx(self, driver: Any, event: str, *, exc: Any = None, context: Any = None) -> None:
        """Capture a diagnostic bundle if wired. Never raises."""
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.capture(driver, event=event, exc=exc, context=context)
        except Exception as e:
            logger.debug(f"EmailChallengeResolver: diagnostics capture failed: {e}")

    # ------------------------ detection -----------------------------

    def can_handle(self, driver: Any) -> bool:
        if not self._imap_config:
            return False
        # Exclude Bloks codeentry — that URL reuses the same heading text
        # but belongs to BloksCodeEntryResolver. Without this guard,
        # both resolvers match the Bloks page and EmailChallengeResolver
        # tries to re-fetch a code, ignoring that the page already
        # holds the prior (now-invalid) value.
        try:
            url = (driver.current_url or '').lower()
            if '/auth_platform/' in url:
                return False
        except Exception:
            pass
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
            self._dx(driver, "challenge_imap_no_code",
                     context={"since_utc": started_at.isoformat()})
            return False

        input_el = self._find_input(driver)
        if input_el is None:
            logger.warning("Email challenge input not found after detection")
            self._dx(driver, "challenge_input_not_found")
            return False

        self._fill_input(driver, input_el, code)

        if not self._click_continue(driver, input_el):
            logger.warning("Could not click Continue on email challenge")
            # The code was filled + every click strategy ran but the
            # heading didn't go away. Most often: IG rejected the code
            # (stale / invalid / rate-limited resend). This bundle is
            # the key to diagnosing WHY on the next change.
            self._dx(driver, "challenge_continue_failed",
                     context={"code_length": len(code)})
            return False

        return self._wait_challenge_gone(driver)

    # ------------------------ steps ---------------------------------

    def _click_get_new_code_then_mark_time(self, driver: Any) -> datetime:
        """Click 'Get a new code' (if present) and return a UTC
        timestamp used as the IMAP `SINCE` filter.

        Why a retroactive margin:
          IG frequently sends the verification email BEFORE our click
          happens — either because the challenge page already shipped
          a fresh email when it first loaded, or because an earlier
          login flow (same account, moments ago) triggered one. A
          strict `started_at = now` filter rejects that already-in-
          inbox message and the flow dead-ends at block detection.

        Window: always -10 min.
          Observed in practice: even when "Get a new code" is clicked
          successfully, IG frequently does NOT dispatch a fresh email
          (rate-limited server side — it just reuses the verification
          code already sent, which may be 5-8 min old). A -2 min
          window misses those. -10 min reliably captures the email
          IG actually sent, whether automatic on page-load or after
          our click.

        Tradeoff: may pick up a slightly stale code. IG accepts it if
        still valid; if expired, the submit fails and we land in the
        same place as before (block detection). The -10 min window
        trades a rare "stale code" attempt for the common "miss the
        real email" failure.
        """
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
        now = self._clock()
        if clicked:
            human_delay(6.0, variance=1.0)
        return now - timedelta(minutes=10)

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


class BloksCodeEntryResolver(ChallengeResolver):
    """Meta Bloks `auth_platform/codeentry` flow — second-step verification
    that often follows the standard email challenge. Different DOM
    framework (Bloks renders via `data-bloks-name`, button labels via
    `aria-label`, no `<button>` tags) but same input/IMAP shape.

    Detection: URL contains `/auth_platform/codeentry/`. The page
    deliberately reuses the "Check your email" heading from the email
    step, so DOM-based detection alone collides with EmailChallengeResolver
    — URL-matching is the clean discriminator.

    Resolution:
      - Poll IMAP for a fresh code (the email-step code was usually
        consumed; IG dispatches a second email when the page advances
        to Bloks).
      - Clear the input (Bloks pre-populates from the prior step's value
        — that pre-populated code is already invalid).
      - Refill via React-compatible event dispatch (Bloks uses synthetic
        events; plain send_keys without dispatch ignores the new value).
      - Click Continue button (`[role="button"][aria-label="Continue"]`)
        with the same 4-strategy cascade as EmailChallengeResolver.
      - Wait for URL to leave `/auth_platform/`.
    """

    URL_MARKER = '/auth_platform/codeentry/'
    INPUT_SELECTOR = 'input[aria-label="Enter code"]'
    CONTINUE_BUTTON_SELECTOR = '[role="button"][aria-label="Continue"]'

    def __init__(
        self,
        imap_config: Any,
        timeout: int = 15,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        diagnostics: Any = None,
    ) -> None:
        self._imap_config = imap_config
        self._timeout = timeout
        self._clock = clock
        self._diagnostics = diagnostics

    def _dx(self, driver: Any, event: str, *, exc: Any = None, context: Any = None) -> None:
        if self._diagnostics is None:
            return
        try:
            self._diagnostics.capture(driver, event=event, exc=exc, context=context)
        except Exception as e:
            logger.debug(f"BloksCodeEntryResolver: diagnostics capture failed: {e}")

    def can_handle(self, driver: Any) -> bool:
        if not self._imap_config:
            return False
        try:
            url = (driver.current_url or '').lower()
        except Exception:
            return False
        return self.URL_MARKER in url

    def resolve(self, driver: Any) -> bool:
        logger.info(
            "Detected Bloks codeentry challenge — fetching fresh code"
        )
        # Bloks codeentry does NOT auto-resend on page load. The page
        # shows a cooldown timer ("We can send a new code in MM:SS")
        # and turns into a clickable "Send a new code" element after
        # the cooldown elapses. We must:
        #   1. Detect the cooldown (if present), wait it out.
        #   2. Click the resend element to trigger IG's email dispatch.
        #   3. THEN poll IMAP.
        # Without this dance, IMAP polls forever — observed in a live
        # run on 2026-05-14 where 22 IMAP attempts over 120s returned
        # nothing because no fresh email was ever sent.
        request_started_at = self._clock()
        resend_clicked = self._request_fresh_code(driver)
        if not resend_clicked:
            logger.warning(
                "Bloks: could not trigger fresh code resend — "
                "polling IMAP anyway in case email is in flight"
            )

        # Window starts ~2 min ago to catch emails that landed during
        # the page transition or between cooldown wait and click.
        started_at = request_started_at - timedelta(minutes=2)
        cfg = ImapConfig.from_dict(self._imap_config)
        code = fetch_instagram_code(cfg, started_at=started_at)
        if not code:
            logger.warning(
                "BloksCodeEntryResolver: no fresh code in IMAP window"
            )
            self._dx(driver, "bloks_imap_no_code",
                     context={"since_utc": started_at.isoformat()})
            return False

        input_el = self._find_input(driver)
        if input_el is None:
            logger.warning("Bloks codeentry input not found")
            self._dx(driver, "bloks_input_not_found")
            return False

        self._clear_and_fill(driver, input_el, code)

        if not self._click_continue(driver, input_el):
            logger.warning("Bloks codeentry: Continue click failed")
            self._dx(driver, "bloks_continue_failed",
                     context={"code_length": len(code)})
            return False

        return self._wait_left_codeentry(driver)

    # ------------------------ steps ---------------------------------

    # JS to scan visible text for "MM:SS" cooldown pattern; returns
    # remaining seconds. Done in one round-trip — DOM in Selenium is
    # expensive per query.
    _COOLDOWN_JS = """
    const txt = (document.body && document.body.innerText) || '';
    const m = txt.match(/(\\d{1,2}):(\\d{2})/);
    if (!m) return 0;
    const mins = parseInt(m[1], 10);
    const secs = parseInt(m[2], 10);
    if (isNaN(mins) || isNaN(secs)) return 0;
    return mins * 60 + secs;
    """

    # JS to find and click the post-cooldown "Send a new code" element.
    # Bloks renders it as a span/div with cursor:pointer; the visible
    # text may be 'Send a new code', 'Resend code', or localised. We
    # walk up to find the clickable ancestor (the framework attaches
    # the listener at a parent level).
    _RESEND_JS = """
    const matchers = [
        'send a new code', 'send new code', 'send code',
        'resend code', 'resend',
        'reenviar', 'novo c\\u00f3digo', 'enviar novo c\\u00f3digo'
    ];
    const candidates = document.querySelectorAll('span, div');
    for (const el of candidates) {
        const t = (el.textContent || '').toLowerCase().trim();
        if (t.length === 0 || t.length > 60) continue;
        if (!matchers.some(m => t.includes(m))) continue;
        // Skip the cooldown-state variant: 'we can send a new code in 00:45'
        if (/\\d:\\d/.test(t)) continue;
        let target = el;
        for (let i = 0; i < 6 && target; i++) {
            const st = window.getComputedStyle(target);
            if (st.cursor === 'pointer' && st.pointerEvents !== 'none') {
                target.click();
                return true;
            }
            target = target.parentElement;
        }
    }
    return false;
    """

    def _parse_cooldown_seconds(self, driver: Any) -> int:
        """Return remaining cooldown seconds visible on the page, or 0."""
        try:
            value = driver.execute_script(self._COOLDOWN_JS)
            return int(value) if isinstance(value, (int, float)) else 0
        except Exception as e:
            logger.debug(f"Bloks cooldown parse failed: {e}")
            return 0

    def _click_resend(self, driver: Any) -> bool:
        """Click the 'Send a new code' element. Returns True if a
        clickable element was found and clicked."""
        try:
            return bool(driver.execute_script(self._RESEND_JS))
        except Exception as e:
            logger.debug(f"Bloks resend click failed: {e}")
            return False

    def _request_fresh_code(self, driver: Any) -> bool:
        """Full resend dance: wait cooldown, click resend element.

        Returns True if the click was executed. False if no
        cooldown/resend UI is found at all (page may have a different
        flow we don't yet handle)."""
        cooldown = self._parse_cooldown_seconds(driver)
        if cooldown > 0:
            # Add 3s slack so DOM has time to update from countdown
            # to clickable state after the timer hits 0.
            total_wait = cooldown + 3
            logger.info(
                f"Bloks: waiting {total_wait}s for resend cooldown"
            )
            time.sleep(total_wait)
        else:
            # No cooldown visible — either we're past it or the page
            # uses a different layout. Small wait still useful for DOM
            # stabilisation before scanning for clickable element.
            human_delay(2.0, variance=0.5)

        clicked = self._click_resend(driver)
        if clicked:
            logger.info(
                "Bloks: clicked 'Send a new code' — waiting for email"
            )
            # Brief wait for IG to dispatch the email before IMAP poll.
            human_delay(5.0, variance=1.5)
        return clicked

    @staticmethod
    def _find_input(driver: Any) -> Optional[Any]:
        try:
            return driver.find_element(
                By.CSS_SELECTOR, BloksCodeEntryResolver.INPUT_SELECTOR,
            )
        except NoSuchElementException:
            return None

    @staticmethod
    def _clear_and_fill(driver: Any, input_el: Any, code: str) -> None:
        # Bloks pre-populates from the email step. Clear, then fill via
        # React-compatible event dispatch so the framework picks up the
        # new value (plain send_keys without dispatch leaves the model
        # holding the old/invalid code).
        try:
            input_el.clear()
        except Exception:
            pass
        try:
            driver.execute_script(
                "const el = arguments[0]; const setter = "
                "Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set; "
                "setter.call(el, ''); "
                "el.dispatchEvent(new Event('input', {bubbles: true})); ",
                input_el,
            )
        except Exception:
            pass
        input_el.send_keys(code)
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
            logger.debug(f"Bloks fill: React event dispatch failed: {e}")
        human_delay(0.8, variance=0.2)

    def _find_continue(self, driver: Any) -> Optional[Any]:
        try:
            return driver.find_element(
                By.CSS_SELECTOR, self.CONTINUE_BUTTON_SELECTOR,
            )
        except NoSuchElementException:
            return None

    def _click_continue(self, driver: Any, input_el: Any) -> bool:
        btn = self._find_continue(driver)
        if btn is None:
            try:
                input_el.send_keys(Keys.RETURN)
                human_delay(2.0, variance=0.5)
                return self._url_left_codeentry(driver)
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
                logger.debug(f"Bloks Continue clicked via {strategy}")
                human_delay(2.0, variance=0.5)
                if self._url_left_codeentry(driver):
                    return True
            except Exception as e:
                logger.debug(f"Bloks Continue {strategy} failed: {e}")
        return False

    def _url_left_codeentry(self, driver: Any) -> bool:
        try:
            return self.URL_MARKER not in (driver.current_url or '').lower()
        except Exception:
            return False

    def _wait_left_codeentry(self, driver: Any) -> bool:
        try:
            WebDriverWait(driver, self._timeout).until(
                lambda d: self._url_left_codeentry(d)
            )
            return True
        except TimeoutException:
            logger.warning("Bloks codeentry: still on URL after Continue")
            return False


__all__ = [
    'ChallengeResolver', 'ChallengeResolverChain',
    'EmailChallengeResolver', 'BloksCodeEntryResolver',
]
