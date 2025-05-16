import sys
import time
from typing import List, Optional
from loguru import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.common.exceptions import StaleElementReferenceException
import re
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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
logger.add("InstaT/logs/insta_extractor.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=True)

# Import InstaLogin (adjust the import according to your project structure)
try:
    from InstaT.login import InstaLogin
    from InstaT.config.selector_loader import SelectorLoader
except ImportError:
    from login import InstaLogin
    from config.selector_loader import SelectorLoader

selectors = SelectorLoader()

# Import our generic scrolling utility
try:
    from InstaT.utils import Utils
except ImportError:
    from utils import Utils


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

    --------------------------------------------------------------------------
    About the developer:
    This solution was created by Tiago Pereira da Silva, a passionate and highly skilled 
    Data & Automation Specialist with experience in financial systems, Python development, 
    and web scraping at scale. 

    Tiago is currently open to new freelance opportunities and job offers (remote or hybrid),
    especially in the fields of data engineering, automation, and digital intelligence.

    🔗 LinkedIn: https://www.linkedin.com/in/tiagopsilvatec/
    💻 GitHub: https://github.com/tiagopsilv
    📧 Contact: tiagosilv@gmail.com
    --------------------------------------------------------------------------

    """

    def __init__(self, username: str, password: str, headless: bool = True, timeout: int = 10) -> None:
        self.username = username
        self.password = password
        self.timeout = timeout

        # Default Parameters
        self.max_refresh_attempts = 100
        self.wait_interval = 0.5
        self.additional_scroll_attempts = 1
        self.pause_time = 0.5
        self.max_attempts = 2
        self.max_retry_without_new_profiles = 3 

        logger.info("Initializing InstaExtractor with username: {}", username)
        try:
            self.insta_login = InstaLogin(username, password, headless=headless, timeout=timeout)
            self.insta_login.login()
            self.driver = self.insta_login.driver
            logger.info("Logged in successfully as {}", username)
        except Exception as e:
            logger.exception("Login failed during InstaExtractor initialization: {}", e)
            raise

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

    def _open_list_modal(self, profile_id: str, list_type: str) -> Optional[int]:
        """
        Navigate to the profile page and click on the followers/following link.
        Returns the total count if successful, or None on failure.
        """
        url = f"https://www.instagram.com/{profile_id}/"
        logger.info("Navigating to profile: {}", url)
        try:
            self.driver.get(url)
        except WebDriverException as e:
            logger.exception("Error navigating to profile: {}", e)
            return None

        xpath_map = {
            'followers': selectors.get("FOLLOWERS_LINK"),
            'following': selectors.get("FOLLOWING_LINK")
        }
        xpath = xpath_map.get(list_type)
        if not xpath:
            logger.error("Invalid list type provided: {}", list_type)
            return None

        try:
            link = WebDriverWait(self.driver, self.timeout).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            # join number + suffix if split
            parts = link.text.split()
            raw = parts[0]
            if len(parts) > 1 and parts[1].lower() in ("k","m","mi","mil"):
                raw += parts[1]
            total_count = self.parse_count_text(raw)
            logger.debug("Parsed total {}: {}", list_type, total_count)
            link.click()
            logger.debug("Clicked on the {} link.", list_type)
            return total_count

        except (TimeoutException, WebDriverException, ValueError) as e:
            logger.exception("Error opening {} list modal: {}", list_type, e)
            return None
        
    def _extract_list(self, profile_id: str, list_type: str, max_duration: Optional[float]) -> List[str]:
        """
        Extracts followers or following by opening the modal and then calling get_profiles.
        """
        total_count = self._open_list_modal(profile_id, list_type)
        if total_count is None:
            return []

        usernames = self.get_profiles(total_count, max_duration)

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

    def get_profiles(self, expected_count: int, max_duration: Optional[float]) -> List[str]:
        """
        Extracts unique Instagram profile names from a dynamically loaded followers or following modal.
        Scrolls incrementally through the modal dialog, ensuring the complete extraction of all profiles.
        :param expected_count: The number of profiles expected to be extracted.
        :return: List of unique profile usernames.
        """
        start_time = time.perf_counter()
        unique_profiles = set()
        refresh_attempts, try_count, previous_count = 0, 0, 0

        body = self._get_scrollable_body()
        if body is None:
            return []

        while refresh_attempts < self.max_refresh_attempts:
            if self._is_max_duration_exceeded(start_time, max_duration):
                break

            self._perform_dynamic_scroll(body)
            self._wait_for_new_profiles(body, unique_profiles)

            new_profiles_found = self._extract_visible_profiles(unique_profiles)

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

    def _get_scrollable_body(self):
        try:
            body = Utils.find_element_safe(self.driver, By.TAG_NAME, "body")
            if not body:
                logger.error("Failed to find body element. Exiting profile extraction.")
            return body
        except Exception as e:
            logger.exception("Error finding body element: {}", e)
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
            return refresh_attempts, 0, current_count

        try_count += 1
        logger.debug(f"No new profiles detected. Retry attempt {try_count}/{self.max_retry_without_new_profiles}")

        if try_count > self.max_retry_without_new_profiles:
            refresh_attempts += 1
            logger.info("No new profiles after several attempts, refreshing page.")
            self.driver.refresh()
            time.sleep(3)
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
