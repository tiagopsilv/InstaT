"""
SeleniumEngine: engine de extração via Selenium/Firefox.
Implementa BaseEngine. Encapsula toda a lógica de login, scrolling,
modal e profile extraction que antes estava em InstaExtractor.
"""
import re
import time
from typing import Callable, List, Optional, Set

from loguru import logger
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

try:
    from instat.backoff import SmartBackoff
    from instat.checkpoint import ExtractionCheckpoint
    from instat.config.selector_loader import SelectorLoader
    from instat.constants import PROFILE_WAIT_INTERVAL, SCROLL_PAUSE
    from instat.engines.base import BaseEngine
    from instat.login import InstaLogin
    from instat.modal_interaction import ModalInteraction
    from instat.scroll_loop import ScrollLoop
    from instat.session_cache import SessionCache
    from instat.utils import Utils
except ImportError:
    from backoff import SmartBackoff
    from checkpoint import ExtractionCheckpoint
    from config.selector_loader import SelectorLoader
    from constants import PROFILE_WAIT_INTERVAL, SCROLL_PAUSE
    from engines.base import BaseEngine
    from login import InstaLogin
    from modal_interaction import ModalInteraction  # type: ignore
    from scroll_loop import ScrollLoop  # type: ignore
    from session_cache import SessionCache
    from utils import Utils


class SeleniumEngine(BaseEngine):
    """
    Engine de extração via Selenium/Firefox.
    Reutiliza InstaLogin, Utils, SelectorLoader sem duplicação.
    """

    @property
    def name(self) -> str:
        return 'selenium'

    @property
    def is_available(self) -> bool:
        try:
            import selenium  # noqa: F401
            return True
        except ImportError:
            return False

    INSTAGRAM_BASE_URL = 'https://www.instagram.com'

    def __init__(self, headless=True, timeout=10, _login_class=None,
                 proxy: Optional[str] = None, base_url: Optional[str] = None,
                 imap_config=None, **kwargs):
        self.headless = headless
        self.timeout = timeout
        self._login_class = _login_class or InstaLogin
        self._proxy = proxy  # stored for BL-13 integration with FirefoxOptions
        self._base_url = base_url or self.INSTAGRAM_BASE_URL
        self._imap_config = imap_config
        self._login_obj = None
        self._driver = None
        self._modal: Optional[ModalInteraction] = None  # built lazily post-login
        self._session_cache = SessionCache()
        self._selectors = SelectorLoader()
        self._save_login_dismissed = False  # PERF-01 Fix 4: skip dismiss after first call

        # Extraction parameters (same defaults as old InstaExtractor)
        self.max_refresh_attempts = 100
        self.wait_interval = PROFILE_WAIT_INTERVAL
        self.additional_scroll_attempts = 1
        self.pause_time = SCROLL_PAUSE
        self.max_attempts = 2
        self.max_retry_without_new_profiles = 3
        # Warmup anti-falso-positivo para targets populares. Até
        # `warmup_threshold` perfis coletados, tolera até
        # `warmup_stale_rounds` rounds vazios antes de disparar reopen
        # (default MAX_STALE_ROUNDS=4). Valores pequenos preservam o
        # comportamento atual; grandes evitam "rate limit suspected" a
        # 50 profiles num alvo de 850k.
        self.warmup_threshold = 200
        self.warmup_stale_rounds = 10
        self._backoff = SmartBackoff()
        self.checkpoint_interval = 100
        # PERF-02: cobertura abaixo deste threshold levanta BlockedError
        # para permitir fallback para próxima engine (checkpoint preservado).
        # 0.90 = precisa coletar >= 90% do expected_count para considerar sucesso.
        self.completion_threshold = 0.90

    def login(self, username, password, **kwargs):
        self._login_obj = self._login_class(
            username, password,
            headless=self.headless, timeout=self.timeout,
            session_cache=self._session_cache,
            base_url=self._base_url,
            imap_config=self._imap_config,
        )
        self._login_obj.login()
        self._driver = self._login_obj.driver
        return True

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None,
                should_stop: Optional[Callable[[], bool]] = None) -> Set[str]:
        result = self._extract_list(
            profile_id, list_type, max_duration,
            existing_profiles=existing_profiles,
            on_batch=on_batch,
            should_stop=should_stop,
        )
        return set(result)

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        link = self._navigate_and_get_link(profile_id, list_type)
        if not link:
            return None
        try:
            parts = link.text.split()
            raw = parts[0]
            if len(parts) > 1 and parts[1].lower() in ("k", "m", "mi", "mil"):
                raw += parts[1]
            total_count = self._parse_count(raw)
            logger.debug("Parsed total {}: {}", list_type, total_count)
            return total_count
        except (ValueError, IndexError) as e:
            logger.exception("Error parsing {} count: {}", list_type, e)
            return None

    def quit(self):
        if self._driver:
            logger.info("Quitting WebDriver.")
            self._driver.quit()

    # --- Extraction logic (moved from InstaExtractor) ---

    @staticmethod
    def _parse_count(text: str) -> int:
        if not text:
            raise ValueError("Input is None or empty")
        txt = re.sub(r"\s+", "", text.lower())
        if not any(suffix in txt for suffix in ["k", "m", "mi", "mil"]):
            txt = txt.replace(".", "").replace(",", "")
        m = re.fullmatch(r"(\d+)[\.,]?(\d+)?(k|m|mi|mil)?", txt)
        if not m:
            raise ValueError(f"Unrecognized count format: '{text}'")
        int_part, decimal_part, suffix = m.groups()
        int_val = int(int_part)
        dec_val = int(decimal_part) if decimal_part else 0
        multiplier = 1
        if suffix == "k":
            multiplier = 1_000
        elif suffix in ("m", "mi"):
            multiplier = 1_000_000
        elif suffix == "mil":
            multiplier = 1_000
        total = int_val * multiplier
        if decimal_part:
            decimal_digits = len(decimal_part)
            factor = multiplier // (10 ** decimal_digits)
            total += dec_val * factor
        return total

    def _get_modal(self) -> ModalInteraction:
        """Lazy-built ModalInteraction. Requires self._driver (post-login)."""
        if getattr(self, '_modal', None) is None:
            self._modal = ModalInteraction(
                driver=self._driver,
                selectors=self._selectors,
                timeout=self.timeout,
                base_url=self._base_url,
                dismiss_save_login=self._dismiss_save_login_once,
            )
        return self._modal

    def _dismiss_save_login_once(self) -> None:
        """Callback plugged into ModalInteraction. Runs the
        save-login-info dismiss only on the first navigation per
        session (PERF-01 Fix 4)."""
        if self._save_login_dismissed:
            return
        close_kw = (
            self._login_obj.close_keywords if self._login_obj
            else ["not now", "save"]
        )
        Utils.dismiss_save_login_modal(self._driver, close_kw, timeout=3)
        self._save_login_dismissed = True

    def _navigate_and_get_link(self, profile_id: str, list_type: str):
        """Back-compat wrapper — delegates to ModalInteraction.open."""
        return self._get_modal().open(profile_id, list_type)

    def _click_link_element(self, link, list_type: str) -> bool:
        """Back-compat wrapper — delegates to ModalInteraction.click_link."""
        return self._get_modal().click_link(link, list_type)

    def _extract_list(self, profile_id: str, list_type: str,
                      max_duration: Optional[float],
                      existing_profiles: Optional[Set[str]] = None,
                      on_batch: Optional[Callable] = None,
                      should_stop: Optional[Callable[[], bool]] = None) -> List[str]:
        ckpt = ExtractionCheckpoint(profile_id, list_type)
        # Merge: existing_profiles (do EngineManager) + checkpoint (do disco)
        # Isso permite fallback entre engines preservar progresso.
        existing = set(existing_profiles) if existing_profiles else set()
        ckpt_loaded = ckpt.load() or set()
        existing |= ckpt_loaded
        if existing:
            logger.info(
                f"Resuming with {len(existing)} profiles "
                f"({len(ckpt_loaded)} from checkpoint, "
                f"{len(existing) - len(ckpt_loaded)} from prior engines)."
            )

        link = self._navigate_and_get_link(profile_id, list_type)
        if not link:
            return list(existing) if existing else []

        try:
            parts = link.text.split()
            raw = parts[0]
            if len(parts) > 1 and parts[1].lower() in ("k", "m", "mi", "mil"):
                raw += parts[1]
            total_count = self._parse_count(raw)
            logger.debug("Parsed total {}: {}", list_type, total_count)
        except (ValueError, IndexError) as e:
            logger.exception("Error parsing {} count: {}", list_type, e)
            return list(existing) if existing else []

        if not self._click_link_element(link, list_type):
            return list(existing) if existing else []

        try:
            usernames = self._get_profiles(
                total_count, max_duration,
                initial_profiles=existing, checkpoint=ckpt,
                on_batch=on_batch,
                profile_id=profile_id, list_type=list_type,
                should_stop=should_stop,
            )
        except Exception:
            logger.exception("Extraction failed. Progress saved in checkpoint.")
            # PERF-02 Solução D: reset de estado mesmo em falha,
            # para que próxima chamada (get_following após get_followers) não herde
            # modal aberto, foco travado, ou DOM inconsistente.
            self._reset_page_state()
            raise
        finally:
            if existing:
                ckpt.save(existing)

        ckpt.clear()

        try:
            close_button = WebDriverWait(self._driver, self.timeout).until(
                EC.element_to_be_clickable(
                    (By.XPATH, self._selectors.get("CLOSE_MODAL_BUTTON"))
                )
            )
            close_button.click()
            logger.debug("Closed the modal dialog.")
        except (TimeoutException, WebDriverException) as e:
            logger.exception("Error closing modal dialog: {}", e)

        # PERF-02 Solução D: reset de estado da página para a próxima chamada
        # sequencial (ex: get_followers → get_following na mesma instância).
        self._reset_page_state()

        return usernames

    def _get_profiles(self, expected_count: int, max_duration: Optional[float],
                      initial_profiles: Optional[set] = None,
                      checkpoint: Optional[ExtractionCheckpoint] = None,
                      on_batch: Optional[Callable] = None,
                      profile_id: Optional[str] = None,
                      list_type: Optional[str] = None,
                      should_stop: Optional[Callable[[], bool]] = None) -> List[str]:
        """Thin delegate — scroll-loop logic lives in ScrollLoop."""
        return self._build_scroll_loop().run(
            expected_count,
            max_duration,
            initial_profiles=initial_profiles,
            checkpoint=checkpoint,
            on_batch=on_batch,
            profile_id=profile_id,
            list_type=list_type,
            should_stop=should_stop,
        )

    def _build_scroll_loop(self) -> ScrollLoop:
        """Factory — reads current engine config + optional
        block_predictor (opt-in via setattr) at call time. Binds
        `self._reopen_modal` so test patches on the wrapper propagate."""
        return ScrollLoop(
            driver=self._driver,
            selectors=self._selectors,
            reopen_modal=self._reopen_modal,
            pause_time=self.pause_time,
            wait_interval=self.wait_interval,
            warmup_threshold=self.warmup_threshold,
            warmup_stale_rounds=self.warmup_stale_rounds,
            checkpoint_interval=self.checkpoint_interval,
            completion_threshold=self.completion_threshold,
            engine_name=self.name,
            block_predictor=getattr(self, "_block_predictor", None),
        )

    def _reset_page_state(self) -> None:
        """Back-compat wrapper — delegates to ModalInteraction.reset_page."""
        self._get_modal().reset_page()

    def _reopen_modal(self, profile_id: str, list_type: str) -> bool:
        """Back-compat wrapper — delegates to ModalInteraction.reopen."""
        return self._get_modal().reopen(profile_id, list_type)

    def _scroll_modal_js(self) -> None:
        """Back-compat wrapper — delegates to ScrollLoop."""
        self._build_scroll_loop()._scroll_modal_js()

    def _get_scrollable_container(self):
        try:
            modal = Utils.find_element_safe(
                self._driver, By.CSS_SELECTOR,
                self._selectors.get('MODAL_SCROLL_CONTAINER'),
                max_retries=2
            )
            if modal:
                logger.debug('Using modal scroll container.')
                return modal
            logger.debug('Modal container not found, falling back to body.')
            body = Utils.find_element_safe(self._driver, By.TAG_NAME, "body")
            if not body:
                logger.error("Failed to find body element.")
            return body
        except Exception as e:
            logger.exception("Error finding scrollable container: {}", e)
            return None

    def _is_max_duration_exceeded(self, start_time, max_duration):
        if max_duration is None:
            return False
        elapsed = time.perf_counter() - start_time
        if elapsed > max_duration:
            logger.warning("Max duration ({:.1f}s) exceeded.", max_duration)
            return True
        return False

    def _perform_dynamic_scroll(self, body):
        Utils.dynamic_scroll_element(
            self._driver, body,
            item_selector=self._selectors.get("PROFILE_USERNAME_SPAN"),
            pause_time=self.pause_time,
            max_attempts=self.max_attempts
        )

    def _wait_for_new_profiles(self, body, unique_profiles):
        Utils.wait_for_new_profiles(
            driver=self._driver,
            scrollable_element=body,
            profile_selector=self._selectors.get("PROFILE_USERNAME_SPAN"),
            existing_profiles=unique_profiles,
            wait_interval=self.wait_interval,
            additional_scroll_attempts=self.additional_scroll_attempts
        )

    def _extract_visible_profiles(self, unique_profiles: set) -> bool:
        new_profiles_found = False
        try:
            profile_elements = Utils.find_elements_safe(
                self._driver, By.CSS_SELECTOR, self._selectors.get("PROFILE_USERNAME_SPAN")
            )
            for element in profile_elements:
                try:
                    profile_name = element.text.strip()
                    if profile_name and profile_name not in unique_profiles:
                        unique_profiles.add(profile_name)
                        new_profiles_found = True
                except StaleElementReferenceException:
                    continue
        except StaleElementReferenceException:
            logger.warning("StaleElementReferenceException encountered. Retrying extraction.")
        return new_profiles_found

    def _handle_profile_count(self, new_profiles_found, previous_count, try_count, refresh_attempts):
        current_count = previous_count + (1 if new_profiles_found else 0)
        if current_count > previous_count:
            logger.debug(f"Found new profiles, total now {current_count}")
            self._backoff.reset()
            return refresh_attempts, 0, current_count

        try_count += 1
        logger.debug(f"No new profiles detected. Retry attempt {try_count}/{self.max_retry_without_new_profiles}")

        if try_count > self.max_retry_without_new_profiles:
            refresh_attempts += 1
            delay = self._backoff.wait()
            logger.info(f"No new profiles after several attempts. Backoff {delay:.1f}s (attempt {self._backoff.attempt}), refreshing page.")
            self._driver.refresh()
            return refresh_attempts, 0, 0

        return refresh_attempts, try_count, previous_count
