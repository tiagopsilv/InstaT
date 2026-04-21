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
    from instat.constants import PROFILE_WAIT_INTERVAL, SCROLL_PAUSE, human_delay
    from instat.engines.base import BaseEngine
    from instat.exceptions import BlockedError, ProfileNotFoundError
    from instat.login import InstaLogin
    from instat.session_cache import SessionCache
    from instat.utils import Utils
except ImportError:
    from backoff import SmartBackoff
    from checkpoint import ExtractionCheckpoint
    from config.selector_loader import SelectorLoader
    from constants import PROFILE_WAIT_INTERVAL, SCROLL_PAUSE, human_delay
    from engines.base import BaseEngine
    from exceptions import BlockedError, ProfileNotFoundError
    from login import InstaLogin
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

    def _navigate_and_get_link(self, profile_id: str, list_type: str):
        url = f"{self._base_url}/{profile_id}/"
        logger.info("Navigating to profile: {}", url)
        try:
            self._driver.get(url)
        except WebDriverException as e:
            logger.exception("Error navigating to profile: {}", e)
            return None

        # Save login modal typically appears only once after login.
        # Skip the 3s check after first navigation attempt.
        if not self._save_login_dismissed:
            close_kw = self._login_obj.close_keywords if self._login_obj else ["not now", "save"]
            Utils.dismiss_save_login_modal(self._driver, close_kw, timeout=3)
            self._save_login_dismissed = True

        selector_key_map = {
            'followers': "FOLLOWERS_LINK",
            'following': "FOLLOWING_LINK"
        }
        selector_key = selector_key_map.get(list_type)
        if not selector_key:
            logger.error("Invalid list type provided: {}", list_type)
            return None

        selector_alternatives = self._selectors.get_all(selector_key)
        link = Utils.find_element_with_fallback(self._driver, selector_alternatives, timeout=self.timeout)
        if link:
            return link

        logger.error("Could not find {} link for '{}' with any selector alternative.", list_type, profile_id)
        raise ProfileNotFoundError(f"Could not find {list_type} link for '{profile_id}'")

    def _click_link_element(self, link, list_type: str) -> bool:
        try:
            WebDriverWait(self._driver, self.timeout).until(EC.element_to_be_clickable(link))
            link.click()
            logger.debug("Clicked on the {} link.", list_type)
            return True
        except (TimeoutException, WebDriverException) as e:
            logger.debug(f"Native click failed for {list_type}: {type(e).__name__}, trying JS click...")
        try:
            self._driver.execute_script("arguments[0].click();", link)
            logger.debug("Clicked on the {} link via JS.", list_type)
            return True
        except WebDriverException as e:
            logger.warning(f"JS click also failed for {list_type}: {e}")
            return False

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
        """Extrai perfis via JS scroll direto no container do modal.

        Estratégia PERF-03:
        - Re-localiza container via JS a cada scroll (evita stale refs).
        - Scroll em 1 IPC JS + batch_read_text em 1 IPC.
        - Rate limit do IG: após ~150 perfis em burst, servidor para de entregar.
          Recovery: fecha e reabre o modal (reset do cursor de paginação).
        - Para de vez quando mesmo após reopen não trouxe novos.
        """
        start_time = time.perf_counter()
        unique_profiles = set(initial_profiles) if initial_profiles else set()
        if unique_profiles:
            logger.info(f"Starting with {len(unique_profiles)} profiles from checkpoint.")
        _last_checkpoint_count = len(unique_profiles)

        profile_selector = self._selectors.get("PROFILE_USERNAME_SPAN")
        stale_rounds = 0
        reopen_attempts = 0
        # Targets populares (milhões de followers) costumam demorar a
        # começar a render itens — se esgotar MAX_STALE_ROUNDS durante
        # o warmup, perdemos a extração antes de sequer começar.
        # Durante os primeiros `warmup_threshold` perfis, usamos um
        # limite maior (warmup_stale_rounds) antes de declarar rate-limit.
        MAX_STALE_ROUNDS = 4
        MAX_REOPEN_ATTEMPTS = 3

        while True:
            if self._is_max_duration_exceeded(start_time, max_duration):
                logger.info("Max duration reached, stopping extraction.")
                break

            if should_stop and should_stop():
                logger.info("should_stop signal received, stopping extraction.")
                break

            count_before = len(unique_profiles)

            # Scroll via JS (re-localiza o container cada vez; sem stale ref)
            self._scroll_modal_js()

            # Small delay for lazy-load to render
            human_delay(self.pause_time, variance=0.2)

            # Batch-read usernames via JS
            snapshot = Utils.batch_read_text(self._driver, profile_selector)
            unique_profiles |= snapshot

            new_added = len(unique_profiles) - count_before

            # Checkpoint incremental
            if checkpoint and (len(unique_profiles) - _last_checkpoint_count) >= self.checkpoint_interval:
                checkpoint.save(unique_profiles)
                _last_checkpoint_count = len(unique_profiles)
                logger.info(f"Checkpoint saved: {len(unique_profiles)} profiles")

            # Notify orchestrator of incremental batch (for EngineManager checkpoint sync)
            if on_batch and new_added > 0:
                try:
                    on_batch(unique_profiles)
                except Exception as e:
                    logger.debug(f"on_batch failed silently: {e}")

            if len(unique_profiles) >= expected_count:
                logger.info(f"Expected profile count reached ({len(unique_profiles)}/{expected_count}).")
                break

            if new_added == 0:
                stale_rounds += 1
                # Warmup: tolera mais rounds vazios enquanto a coleta ainda
                # é rasa. Lista nova de seguidores/seguindo em target
                # popular pode levar vários scrolls antes de devolver itens.
                in_warmup = len(unique_profiles) < self.warmup_threshold
                effective_limit = (
                    self.warmup_stale_rounds if in_warmup else MAX_STALE_ROUNDS
                )
                logger.debug(
                    f"No new profiles in this round. Stale rounds: "
                    f"{stale_rounds}/{effective_limit}"
                    f"{' (warmup)' if in_warmup else ''}"
                )
                # Telemetry for BlockPredictor (opt-in via setattr on engine).
                predictor = getattr(self, '_block_predictor', None)
                if predictor is not None:
                    try:
                        predictor.record_stale(
                            stale_count=stale_rounds,
                            max_stale=effective_limit,
                            reopen_failed=False,
                            engine=self.name,
                        )
                    except Exception as e:
                        logger.debug(f"block_predictor record_stale failed: {e}")
                if stale_rounds >= effective_limit:
                    # PERF-03 Solução G: rate limit detectado — tenta reopen modal
                    # para reset do cursor de paginação do Instagram.
                    if (profile_id and list_type
                            and reopen_attempts < MAX_REOPEN_ATTEMPTS):
                        reopen_attempts += 1
                        logger.info(
                            f"Rate limit suspected after {len(unique_profiles)} profiles. "
                            f"Reopening modal (attempt {reopen_attempts}/{MAX_REOPEN_ATTEMPTS})..."
                        )
                        reopen_ok = self._reopen_modal(profile_id, list_type)
                        if predictor is not None and not reopen_ok:
                            try:
                                predictor.record_stale(
                                    stale_count=stale_rounds,
                                    max_stale=effective_limit,
                                    reopen_failed=True,
                                    engine=self.name,
                                )
                            except Exception as e:
                                logger.debug(
                                    f"block_predictor record_stale failed: {e}"
                                )
                        if reopen_ok:
                            stale_rounds = 0
                            # Cooldown anti-detecção antes de retomar
                            human_delay(3.0, variance=1.0)
                            continue
                        else:
                            logger.warning("Reopen failed — stopping extraction.")
                            break
                    logger.info(
                        f"No new profiles after {MAX_STALE_ROUNDS} rounds "
                        f"+ {reopen_attempts} reopen attempts — end of list."
                    )
                    break
                # Backoff pequeno entre tentativas "vazias" dá tempo ao lazy-load
                human_delay(self.wait_interval, variance=0.2)
            else:
                stale_rounds = 0
                logger.info(f"Collected {len(unique_profiles)} out of {expected_count} expected profiles (+{new_added}).")

        elapsed = time.perf_counter() - start_time
        logger.info(
            f"Profile extraction completed in {elapsed:.2f}s. "
            f"Total unique profiles: {len(unique_profiles)}/{expected_count}."
        )

        # PERF-02 Solução E: se cobertura abaixo do threshold E coletou algo,
        # salva checkpoint e levanta BlockedError para permitir fallback engine
        # continuar de onde parou. Se coletou 0, deixa o return [] normal.
        coverage = (
            len(unique_profiles) / expected_count if expected_count > 0 else 1.0
        )
        if (0 < len(unique_profiles) and expected_count > 0
                and coverage < self.completion_threshold):
            if checkpoint:
                checkpoint.save(unique_profiles)
            # Notifica orquestrador dos perfis finais ANTES de levantar
            # (assim EngineManager tem `profiles` populado e retorna parcial
            # em vez de AllEnginesBlockedError)
            if on_batch:
                try:
                    on_batch(unique_profiles)
                except Exception as e:
                    logger.debug(f"on_batch final failed silently: {e}")
            logger.warning(
                f"Coverage {100*coverage:.0f}% below threshold "
                f"{100*self.completion_threshold:.0f}% "
                f"({len(unique_profiles)}/{expected_count}). "
                f"Raising BlockedError to trigger engine fallback."
            )
            raise BlockedError(
                f"selenium partial coverage: "
                f"{len(unique_profiles)}/{expected_count} ({100*coverage:.0f}%)"
            )

        return list(unique_profiles)

    def _reset_page_state(self) -> None:
        """PERF-02 Solução D: navega para about:blank para limpar DOM state
        entre chamadas sequenciais (get_followers → get_following).
        Evita modal fantasma, foco travado, handlers zumbis no Firefox."""
        try:
            self._driver.get('about:blank')
            human_delay(0.3, variance=0.1)
        except Exception as e:
            logger.debug(f"reset_page_state failed silently: {e}")

    def _reopen_modal(self, profile_id: str, list_type: str) -> bool:
        """PERF-03 Solução G: fecha e reabre o modal para contornar rate limit
        do Instagram (reset do cursor de paginação server-side).

        Retorna True em sucesso.
        """
        try:
            # 1) Fechar modal atual (se ainda aberto)
            try:
                close_btn = self._driver.find_element(
                    By.XPATH, self._selectors.get("CLOSE_MODAL_BUTTON")
                )
                close_btn.click()
            except Exception:
                # Fallback: ESC key
                try:
                    from selenium.webdriver.common.keys import Keys
                    self._driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                except Exception:
                    pass
            human_delay(1.0, variance=0.3)

            # 2) Re-navegar ao perfil (reset full state)
            self._driver.get(f"{self._base_url}/{profile_id}/")
            human_delay(2.0, variance=0.5)

            # 3) Re-clicar no link followers/following
            selector_key = "FOLLOWERS_LINK" if list_type == 'followers' else "FOLLOWING_LINK"
            selector_alternatives = self._selectors.get_all(selector_key)
            link = Utils.find_element_with_fallback(
                self._driver, selector_alternatives, timeout=self.timeout
            )
            if not link:
                logger.warning("reopen_modal: could not find list link after navigation")
                return False

            if not self._click_link_element(link, list_type):
                logger.warning("reopen_modal: could not click list link")
                return False

            # 4) Aguardar modal carregar
            try:
                WebDriverWait(self._driver, self.timeout).until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, 'div[role="dialog"]')
                    )
                )
            except TimeoutException:
                logger.warning("reopen_modal: dialog did not appear")
                return False

            logger.info("Modal reopened successfully")
            return True
        except Exception as e:
            logger.warning(f"reopen_modal failed: {e}")
            return False

    def _scroll_modal_js(self) -> None:
        """Scrolla o container do modal em UMA IPC usando JS puro.
        Re-localiza o container a cada chamada (evita stale refs).
        Fallback: window scroll se nenhum container encontrado."""
        try:
            self._driver.execute_script(
                """
                const selectors = [
                    'div[role="dialog"] div[style*="overflow"]',
                    'div[role="dialog"] ul',
                    'div[role="dialog"] div[style*="height"]'
                ];
                for (const s of selectors) {
                    const el = document.querySelector(s);
                    if (el && el.scrollHeight > el.clientHeight) {
                        el.scrollTop = el.scrollHeight;
                        return true;
                    }
                }
                // Fallback: scroll inside dialog using the dialog itself
                const dlg = document.querySelector('div[role="dialog"]');
                if (dlg) {
                    dlg.scrollTop = dlg.scrollHeight;
                    return true;
                }
                window.scrollTo(0, document.body.scrollHeight);
                return false;
                """
            )
        except Exception as e:
            logger.debug(f"scroll_modal_js error (ignored): {e}")

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
