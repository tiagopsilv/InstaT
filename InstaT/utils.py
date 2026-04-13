import time
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException, NoSuchElementException, TimeoutException
from loguru import logger
from typing import Set, List
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# Load selector loader
try:
    from instat.config.selector_loader import SelectorLoader
except ImportError:
    from config.selector_loader import SelectorLoader

try:
    from instat.constants import human_delay, ELEMENT_RETRY_DELAY
except ImportError:
    from constants import human_delay, ELEMENT_RETRY_DELAY

class Utils:
    selectors = SelectorLoader()

    @staticmethod
    def find_element_with_fallback(driver, selectors_list, by_auto=True, timeout=5):
        """
        Tenta encontrar um elemento usando uma lista de seletores alternativos.
        Detecta automaticamente se é XPath (começa com //) ou CSS.
        Retorna o primeiro elemento encontrado ou None.
        """
        for selector in selectors_list:
            try:
                by = By.XPATH if selector.startswith('//') else By.CSS_SELECTOR
                element = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((by, selector))
                )
                if element:
                    logger.debug(f"Element found with selector: {selector[:60]}")
                    return element
            except (TimeoutException, NoSuchElementException):
                logger.debug(f"Selector not found, trying next: {selector[:60]}")
                continue
            except Exception as e:
                logger.debug(f"Error with selector {selector[:60]}: {e}")
                continue
        logger.warning(f"No element found with any of {len(selectors_list)} selector alternatives.")
        return None

    @staticmethod
    def click_ignore_button_if_present(driver, timeout=5, wait_before_click=3):
        """
        Clicks the 'Ignorar' button if it is present on the page, after waiting for a specified duration.

        :param driver: Selenium WebDriver instance.
        :param timeout: Max time to wait for the button to appear.
        :param wait_before_click: Seconds to wait before clicking the button if it's found.
        :return: True if the button was clicked, False if it was not found or could not be clicked.

        """
        try:
            logger.debug("Checking if the 'Ignorar' button is present.")
            ignore_button = WebDriverWait(driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, Utils.selectors.get("IGNORE_BUTTON")))
            )
            
            if ignore_button:
                logger.info(f"'Ignorar' button detected. Waiting for {wait_before_click} seconds before clicking.")
                human_delay(wait_before_click)  # Waiting before clicking
                
                ignore_button.click()
                logger.info("Successfully clicked the 'Ignorar' button.")
                return True
            else:
                logger.info("'Ignorar' button not found.")
                return False

        except TimeoutException:
            logger.info("'Ignorar' button not found within the timeout period.")
            return False
        except NoSuchElementException:
            logger.info("'Ignorar' button not found on the page.")
            return False
        except Exception as e:
            logger.exception(f"An error occurred while trying to click the 'Ignorar' button: {e}")
            return False

    @staticmethod
    def find_element_safe(driver, by, value, max_retries=3):
        for attempt in range(max_retries):
            try:
                element = driver.find_element(by, value)
                return element
            except (StaleElementReferenceException, NoSuchElementException) as e:
                logger.warning(f"Attempt {attempt + 1}/{max_retries}: Error finding element - {e}")
                human_delay(ELEMENT_RETRY_DELAY)
        logger.error("Failed to find element after multiple attempts.")
        return None
    
    @staticmethod
    def find_elements_safe(driver, by, value, max_retries=3, wait_time=0.3):
        for attempt in range(max_retries):
            try:
                elements = driver.find_elements(by, value)
                if elements:
                    return elements
            except (StaleElementReferenceException, NoSuchElementException) as e:
                logger.warning(f"Attempt {attempt + 1}/{max_retries}: {e}")
            human_delay(wait_time)
        return []

    @staticmethod
    def dynamic_scroll_element(driver: WebDriver, element, item_selector: str, pause_time: float = 0.5, max_attempts: int = 2):
        attempts = 0
        try:
            is_body = element.tag_name.lower() == 'body'
        except Exception:
            is_body = False

        while attempts < max_attempts:
            try:
                items = Utils.find_elements_safe(driver, By.CSS_SELECTOR, item_selector)
                if items:
                    if is_body:
                        driver.execute_script("arguments[0].scrollIntoView(true);", items[-1])
                    else:
                        driver.execute_script(
                            'arguments[0].scrollTop = arguments[0].scrollHeight', element
                        )
                human_delay(pause_time)
                attempts += 1

            except Exception as e:
                logger.exception(f"Error during dynamic scrolling: {e}")
                break

    @staticmethod
    def wait_for_new_profiles(
        driver: WebDriver,
        scrollable_element,
        profile_selector: str,
        existing_profiles: Set[str],
        wait_interval: float = 0.5,
        additional_scroll_attempts: int = 2
    ) -> bool:
        """
        Scrolls and waits until no new profiles are loaded in the scrollable element.
        Stops early if no new profiles are detected.
        """
        logger.debug("Starting wait loop for new profiles based on actual content change.")
        
        while True:
            try:

                # First: if loading spinner exists, wait for it to disappear
                try:
                    # Wait until the loading spinner (aria-label="Carregando...") disappears
                    WebDriverWait(driver, 5).until_not(
                        EC.presence_of_element_located((By.XPATH, Utils.selectors.get("LOADING_SPINNER")))
                    )
                    logger.debug("Loading spinner has disappeared, page is ready.")
                except TimeoutException:
                    logger.debug("Timeout waiting for loading spinner to disappear. Proceeding anyway.")

                profile_elements = Utils.find_elements_safe(
                    driver,
                    By.CSS_SELECTOR,
                    profile_selector,
                    max_retries=2,
                    wait_time=0.7
                )
                current_profiles_snapshot = {el.text.strip() for el in profile_elements if el.text.strip()}

                # Check if any new profiles have appeared
                if not current_profiles_snapshot.issubset(existing_profiles):
                    logger.debug("New profiles detected. Exiting wait loop.")
                    return True

                logger.debug("No new profiles detected. Scrolling again and retrying...")

                # Perform scrolling as part of the process
                Utils.dynamic_scroll_element(
                    driver,
                    scrollable_element,
                    item_selector=profile_selector,
                    pause_time=0.4,
                    max_attempts=additional_scroll_attempts
                )

                # Small wait before next round
                human_delay(wait_interval)

                # No update, break out
                profile_elements_after_scroll = Utils.find_elements_safe(
                    driver,
                    By.CSS_SELECTOR,
                    profile_selector,
                    max_retries=2,
                    wait_time=0.7
                )
                snapshot_after_scroll = {el.text.strip() for el in profile_elements_after_scroll if el.text.strip()}
                if snapshot_after_scroll.issubset(existing_profiles):
                    logger.debug("No new profiles detected after scroll. Breaking loop.")
                    break

            except StaleElementReferenceException:
                logger.warning("StaleElementReferenceException encountered during profile extraction. Retrying...")

        logger.debug(f"No profiles found during loop. Performing final scrolling ({additional_scroll_attempts} attempts).")
        
    @staticmethod
    def dismiss_save_login_modal(driver: WebDriver, close_keywords: List[str], timeout: int = 6) -> bool:
        """
        Dismisses the 'Save your login info?' modal.
        Strategy: try targeted selectors first (Not now, Close), then generic ones.
        """
        logger.debug("Checking for 'Save login info' modal.")
        button_selectors = Utils.selectors.get_all("SAVE_LOGIN_INFO_BUTTON")

        # Fase 1: seletores específicos (primeiros da lista) — clique direto, sem filtro de texto
        for selector in button_selectors[:2]:
            by = By.XPATH if selector.startswith('//') else By.CSS_SELECTOR
            try:
                WebDriverWait(driver, timeout).until(
                    lambda d, s=selector, b=by: len(d.find_elements(b, s)) > 0
                )
                elements = driver.find_elements(by, selector)
                if elements:
                    logger.debug(f"Found targeted dismiss element with selector: {selector[:60]}. Clicking...")
                    try:
                        elements[0].click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", elements[0])
                    human_delay(1.0)
                    logger.debug("Modal 'Save login info' dismissed via targeted selector.")
                    return True
            except TimeoutException:
                continue
            except Exception as e:
                logger.debug(f"Targeted selector failed: {e}")
                continue

        # Fase 2: seletores genéricos — filtro por keyword no texto
        for selector in button_selectors[2:]:
            by = By.XPATH if selector.startswith('//') else By.CSS_SELECTOR
            try:
                all_buttons = driver.find_elements(by, selector)
                for button in all_buttons:
                    try:
                        text = button.text.strip().lower()
                        if any(keyword in text for keyword in close_keywords):
                            logger.debug(f"Found dismiss button with text: '{text}'. Clicking...")
                            try:
                                button.click()
                            except Exception:
                                driver.execute_script("arguments[0].click();", button)
                            human_delay(1.0)
                            logger.debug("Modal 'Save login info' dismissed via keyword match.")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        logger.warning("Could not detect or close 'Save login info' modal with any selector.")
        return False
