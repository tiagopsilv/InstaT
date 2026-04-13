# Built-in
import sys
import time
import re
from typing import List, Optional

# Third-party
from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException, StaleElementReferenceException

# Configure Loguru logger for this module
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
)
logger.add("InstaT/logs/insta_extractor.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=False)

# Local project modules
try:
    from instat.login import InstaLogin
    from instat.config.selector_loader import SelectorLoader
except ImportError:
    from login import InstaLogin
    from config.selector_loader import SelectorLoader
try:
    from instat.constants import human_delay, SCROLL_PAUSE, PROFILE_WAIT_INTERVAL, LOGIN_POST_CLICK_DELAY
except ImportError:
    from constants import human_delay, SCROLL_PAUSE, PROFILE_WAIT_INTERVAL, LOGIN_POST_CLICK_DELAY
try:
    from instat.backoff import SmartBackoff
except ImportError:
    from backoff import SmartBackoff
try:
    from instat.checkpoint import ExtractionCheckpoint
except ImportError:
    from checkpoint import ExtractionCheckpoint
try:
    from instat.session_cache import SessionCache
except ImportError:
    from session_cache import SessionCache
try:
    from instat.utils import Utils
except ImportError:
    from utils import Utils
try:
    from instat.exceptions import LoginError, ProfileNotFoundError
except ImportError:
    from exceptions import LoginError, ProfileNotFoundError

selectors = SelectorLoader()

class InstaExtractor:
    """
    Extracts data from Instagram using automated scrolling and profile collection.

    The extractor logs in using the provided credentials and offers methods to extract followers, 
    following lists, and profile names via dynamic scrolling. This class is designed to be versatile, 
    allowing the user to customize various parameters that control the extraction process. Parameters 
    are now attributes of the class, making them easy to set, get, and modify directly.

    Usage Example:
    --------------
    extractor = InstaExtractor(username="your_username", password="your_password", headless=False)
    
    # Set parameters (Optional - uses defaults if not provided)
    extractor.max_refresh_attempts = 10
    extractor.wait_interval = 0.5
    extractor.additional_scroll_attempts = 3
    extractor.pause_time = 0.5
    extractor.max_attempts = 2
    
    followers = extractor.get_followers("target_profile")
    following = extractor.get_following("target_profile")
    extractor.quit()
    
    Available Attributes for Configuration:
    ---------------------------------------
    - max_refresh_attempts (int): Maximum number of times the page will be refreshed if the desired profiles are not found. Default: 5.
    - wait_interval (float): Time (in seconds) to wait between attempts to find new profiles. Default: 0.5.
    - additional_scroll_attempts (int): Extra scroll attempts to ensure complete profile extraction. Default: 1.
    - pause_time (float): Time (in seconds) to pause between scrolls. Default: 0.5.
    - max_attempts (int): Maximum attempts for scrolling to find new profiles. Default: 2.

    """

    def __init__(self, username: str, password: str, headless: bool = True, timeout: int = 10) -> None:
        self.username = username
        self.password = password
        self.timeout = timeout

        # Default Parameters
        self.max_refresh_attempts = 100
        self.wait_interval = PROFILE_WAIT_INTERVAL
        self.additional_scroll_attempts = 1
        self.pause_time = SCROLL_PAUSE
        self.max_attempts = 2
        self.max_retry_without_new_profiles = 3
        self._backoff = SmartBackoff()
        self.checkpoint_interval = 100
        self._session_cache = SessionCache()

        logger.info("Initializing InstaExtractor with username: {}", username)
        try:
            self.insta_login = InstaLogin(username, password, headless=headless, timeout=timeout, session_cache=self._session_cache)
            self.insta_login.login()
            self.driver = self.insta_login.driver
            logger.info("Logged in successfully as {}", username)
        except Exception as e:
            logger.exception("Login failed during InstaExtractor initialization: {}", e)
            raise LoginError(f"Failed to login as {username}") from e

    @staticmethod
    def parse_count_text(text: str) -> int:
        import re
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
        """
        Navega até o perfil e retorna o elemento do link (seguidores ou seguindo).
        Usa seletores com fallback para resiliência contra mudanças de UI.
        """
        url = f"https://www.instagram.com/{profile_id}/"
        logger.info("Navigating to profile: {}", url)
        try:
            self.driver.get(url)
        except WebDriverException as e:
            logger.exception("Error navigating to profile: {}", e)
            return None

        # Dismiss "Save login info?" modal if it reappears after navigation
        Utils.dismiss_save_login_modal(
            self.driver,
            self.insta_login.close_keywords if hasattr(self, 'insta_login') else ["not now", "save"],
            timeout=3
        )

        selector_key_map = {
            'followers': "FOLLOWERS_LINK",
            'following': "FOLLOWING_LINK"
        }
        selector_key = selector_key_map.get(list_type)
        if not selector_key:
            logger.error("Invalid list type provided: {}", list_type)
            return None

        selector_alternatives = selectors.get_all(selector_key)
        link = Utils.find_element_with_fallback(self.driver, selector_alternatives, timeout=self.timeout)
        if link:
            return link

        logger.error("Could not find {} link for '{}' with any selector alternative.", list_type, profile_id)
        raise ProfileNotFoundError(f"Could not find {list_type} link for '{profile_id}'")
        
    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        """
        Obtém o número total de seguidores ou seguindo.
        """
        link = self._navigate_and_get_link(profile_id, list_type)
        if not link:
            return None

        try:
            parts = link.text.split()
            raw = parts[0]
            if len(parts) > 1 and parts[1].lower() in ("k", "m", "mi", "mil"):
                raw += parts[1]
            total_count = self.parse_count_text(raw)
            logger.debug("Parsed total {}: {}", list_type, total_count)
            return total_count
        except ValueError as e:
            logger.exception("Error parsing {} count: {}", list_type, e)
            return None

    def _click_link_element(self, link, list_type: str) -> bool:
        """Clica num elemento de link já encontrado. Tenta nativo, depois JS."""
        try:
            WebDriverWait(self.driver, self.timeout).until(EC.element_to_be_clickable(link))
            link.click()
            logger.debug("Clicked on the {} link.", list_type)
            return True
        except (TimeoutException, WebDriverException) as e:
            logger.debug(f"Native click failed for {list_type}: {type(e).__name__}, trying JS click...")

        try:
            self.driver.execute_script("arguments[0].click();", link)
            logger.debug("Clicked on the {} link via JS.", list_type)
            return True
        except WebDriverException as e:
            logger.warning(f"JS click also failed for {list_type}: {e}")
            return False

    def _click_list_link(self, profile_id: str, list_type: str) -> bool:
        """
        Clica no link de seguidores ou seguindo.
        Tenta click nativo; se ElementNotInteractable, tenta JS click.
        Se o primeiro elemento não é clicável, tenta alternativas.
        """
        link = self._navigate_and_get_link(profile_id, list_type)
        if not link:
            return False

        # Tenta click nativo
        try:
            WebDriverWait(self.driver, self.timeout).until(EC.element_to_be_clickable(link))
            link.click()
            logger.debug("Clicked on the {} link.", list_type)
            return True
        except (TimeoutException, WebDriverException) as e:
            logger.debug(f"Native click failed for {list_type}: {type(e).__name__}, trying JS click...")

        # Fallback: JS click no mesmo elemento
        try:
            self.driver.execute_script("arguments[0].click();", link)
            logger.debug("Clicked on the {} link via JS.", list_type)
            return True
        except WebDriverException as e:
            logger.debug(f"JS click failed for {list_type}: {type(e).__name__}, trying alternative elements...")

        # Fallback: buscar todos os elementos e tentar cada um
        selector_key = "FOLLOWERS_LINK" if list_type == 'followers' else "FOLLOWING_LINK"
        for sel in selectors.get_all(selector_key):
            by = By.XPATH if sel.startswith('//') else By.CSS_SELECTOR
            try:
                elements = self.driver.find_elements(by, sel)
                for elem in elements:
                    try:
                        elem.click()
                        logger.debug("Clicked on {} link via alternative selector.", list_type)
                        return True
                    except WebDriverException:
                        try:
                            self.driver.execute_script("arguments[0].click();", elem)
                            logger.debug("Clicked on {} link via JS on alternative.", list_type)
                            return True
                        except WebDriverException:
                            continue
            except WebDriverException:
                continue

        logger.error("Could not click {} link after all attempts.", list_type)
        return False

        
    def _extract_list(self, profile_id: str, list_type: str, max_duration: Optional[float]) -> List[str]:
        """
        Extracts followers or following by opening the modal and then calling get_profiles.
        Uses ExtractionCheckpoint to persist and resume progress.
        """
        ckpt = ExtractionCheckpoint(profile_id, list_type)
        existing = ckpt.load() or set()
        if existing:
            logger.info(f"Resuming from checkpoint: {len(existing)} profiles already collected.")

        # Navega ao perfil e obtém o link (uma única navegação)
        link = self._navigate_and_get_link(profile_id, list_type)
        if not link:
            return list(existing) if existing else []

        # Extrai contagem do texto do link (sem re-navegar)
        try:
            parts = link.text.split()
            raw = parts[0]
            if len(parts) > 1 and parts[1].lower() in ("k", "m", "mi", "mil"):
                raw += parts[1]
            total_count = self.parse_count_text(raw)
            logger.debug("Parsed total {}: {}", list_type, total_count)
        except (ValueError, IndexError) as e:
            logger.exception("Error parsing {} count: {}", list_type, e)
            return list(existing) if existing else []

        # Clica no link (sem re-navegar — usa o link já encontrado)
        if not self._click_link_element(link, list_type):
            return list(existing) if existing else []

        try:
            usernames = self.get_profiles(total_count, max_duration, initial_profiles=existing, checkpoint=ckpt)
        except Exception:
            logger.exception("Extraction failed. Progress saved in checkpoint.")
            raise
        finally:
            if existing:
                ckpt.save(existing)

        ckpt.clear()

        try:
            close_button = WebDriverWait(self.driver, self.timeout).until(
                EC.element_to_be_clickable(
                    (By.XPATH, selectors.get("CLOSE_MODAL_BUTTON"))
                )
            )
            close_button.click()
            logger.debug("Closed the modal dialog using the new button selector.")
        except (TimeoutException, WebDriverException) as e:
            logger.exception("Error closing modal dialog using the new button selector: {}", e)

        return usernames

    def get_followers(self, profile_id: str, max_duration: Optional[float] = None) -> List[str]:
        """
        Returns a list of followers for the given profile id.
        
        :param profile_id: Instagram profile username.
        :return: List of followers.
        """
        return self._extract_list(profile_id, 'followers', max_duration)

    def get_following(self, profile_id: str, max_duration: Optional[float] = None) -> List[str]:
        """
        Returns a list of accounts that the given profile id is following.
        
        :param profile_id: Instagram profile username.
        :return: List of following usernames.
        """
        return self._extract_list(profile_id, 'following', max_duration)

    def get_profiles(self, expected_count: int, max_duration: Optional[float],
                     initial_profiles: Optional[set] = None,
                     checkpoint: Optional[ExtractionCheckpoint] = None) -> List[str]:
        """
        Extracts unique Instagram profile names from a dynamically loaded followers or following modal.
        Scrolls incrementally through the modal dialog, ensuring the complete extraction of all profiles.
        :param expected_count: The number of profiles expected to be extracted.
        :param initial_profiles: Optional set of profiles from a previous checkpoint.
        :param checkpoint: Optional ExtractionCheckpoint for incremental saving.
        :return: List of unique profile usernames.
        """
        start_time = time.perf_counter()
        unique_profiles = set(initial_profiles) if initial_profiles else set()
        if unique_profiles:
            logger.info(f"Starting with {len(unique_profiles)} profiles from checkpoint.")
        _last_checkpoint_count = len(unique_profiles)
        refresh_attempts, try_count, previous_count = 0, 0, 0

        body = self._get_scrollable_container()
        if body is None:
            return []

        while refresh_attempts < self.max_refresh_attempts:
            if self._is_max_duration_exceeded(start_time, max_duration):
                break

            self._perform_dynamic_scroll(body)
            self._wait_for_new_profiles(body, unique_profiles)

            new_profiles_found = self._extract_visible_profiles(unique_profiles)

            # Checkpoint incremental
            if checkpoint and (len(unique_profiles) - _last_checkpoint_count) >= self.checkpoint_interval:
                checkpoint.save(unique_profiles)
                _last_checkpoint_count = len(unique_profiles)
                logger.info(f"Checkpoint saved: {len(unique_profiles)} profiles")

            if len(unique_profiles) >= expected_count:
                logger.info("Expected profile count reached.")
                break

            refresh_attempts, try_count, previous_count = self._handle_profile_count(
                new_profiles_found, previous_count, try_count, refresh_attempts
            )

            logger.info(f"Collected {len(unique_profiles)} out of {expected_count} expected profiles.")

        elapsed = time.perf_counter() - start_time
        logger.info(f"Profile extraction completed in {elapsed:.2f} seconds. Total unique profiles: {len(unique_profiles)}")
        return list(unique_profiles)

    def _get_scrollable_container(self):
        """Tenta encontrar o container de scroll do modal. Fallback para body."""
        try:
            modal = Utils.find_element_safe(
                self.driver, By.CSS_SELECTOR,
                selectors.get('MODAL_SCROLL_CONTAINER'),
                max_retries=2
            )
            if modal:
                logger.debug('Using modal scroll container.')
                return modal
            logger.debug('Modal container not found, falling back to body.')
            body = Utils.find_element_safe(self.driver, By.TAG_NAME, "body")
            if not body:
                logger.error("Failed to find body element. Exiting profile extraction.")
            return body
        except Exception as e:
            logger.exception("Error finding scrollable container: {}", e)
            return None

    def _is_max_duration_exceeded(self, start_time, max_duration):
        if max_duration is None:
            return False
        elapsed = time.perf_counter() - start_time
        if elapsed > max_duration:
            logger.warning("Max duration (%.1fs) exceeded.", max_duration)
            return True
        return False

    def _perform_dynamic_scroll(self, body):
        Utils.dynamic_scroll_element(
            self.driver,
            body,
            item_selector=selectors.get("PROFILE_USERNAME_SPAN"),
            pause_time=self.pause_time,
            max_attempts=self.max_attempts
        )

    def _wait_for_new_profiles(self, body, unique_profiles):
        Utils.wait_for_new_profiles(
            driver=self.driver,
            scrollable_element=body,
            profile_selector=selectors.get("PROFILE_USERNAME_SPAN"),
            existing_profiles=unique_profiles,
            wait_interval=self.wait_interval,
            additional_scroll_attempts=self.additional_scroll_attempts
        )

    def _extract_visible_profiles(self, unique_profiles: set) -> bool:
        new_profiles_found = False
        try:
            profile_elements = Utils.find_elements_safe(
                self.driver, By.CSS_SELECTOR, selectors.get("PROFILE_USERNAME_SPAN")
            )
            for element in profile_elements:
                try:
                    profile_name = element.text.strip()
                    if profile_name and profile_name not in unique_profiles:
                        unique_profiles.add(profile_name)
                        new_profiles_found = True
                except StaleElementReferenceException:
                    logger.debug("StaleElementReferenceException accessing profile element. Skipping.")
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
            self.driver.refresh()
            return refresh_attempts, 0, 0

        return refresh_attempts, try_count, previous_count
        
    def quit(self) -> None:
        """
        Closes the underlying WebDriver instance.
        """
        logger.info("Quitting WebDriver.")
        self.driver.quit()


if __name__ == '__main__':
    # Example usage:
    username = "your_username"
    password = "your_password"
    extractor = InstaExtractor(username, password, headless=False)
    try:
        # Extract followers/following from a profile modal (using full-page scroll extraction logic)
        followers = extractor.get_followers("tiagopsilv", max_duration=30.0)
        print("Followers:", followers)
        following = extractor.get_following("tiagopsilv")
        print("Following:", following)
    finally:
        extractor.quit()
