import sys
from loguru import logger
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
    UnexpectedAlertPresentException
)
from selenium.common.exceptions import StaleElementReferenceException
import time
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Setup Loguru logger for advanced logging
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
logger.add("InstaT/logs/insta_login.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=True)

# Import our generic utility
try:
    from InstaT.utils import Utils
    from InstaT.config.selector_loader import SelectorLoader
except ImportError:
    from utils import Utils
    from config.selector_loader import SelectorLoader


class InstaLogin:
    # Public list of keywords used to identify the login button in any language
    keywords = ["entrar", "log in", "login", "iniciar sesión", "connexion", "anmelden"]

    def __init__(self, username, password, headless=True, timeout=10):
        self.username = username
        self.password = password
        self.timeout = timeout
        logger.info("Initializing InstaLogin instance")
        self.driver = self.init_driver(headless)
        self.close_keywords = ["not now", "agora não", "salvar", "save", "skip"]
        self.selectors = SelectorLoader()
    
    def init_driver(self, headless):
        logger.debug("Setting up Firefox options with mobile user agent")
        options = webdriver.FirefoxOptions()
        mobile_user_agent = (
            "Mozilla/5.0 (Linux; Android 8.0; Nexus 5 Build/OPR6.170623.013) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.72 Mobile Safari/537.36"
        )
        options.set_preference("general.useragent.override", mobile_user_agent)
        if headless:
            logger.debug("Enabling headless mode")
            options.add_argument('--headless')
        
        try:
            logger.debug("Installing GeckoDriver using webdriver-manager")
            driver = webdriver.Firefox(service=Service(GeckoDriverManager().install()), options=options)
        except WebDriverException as e:
            logger.exception("Error initializing WebDriver")
            raise Exception("Failed to initialize WebDriver.") from e

        driver.set_window_size(375, 667)
        try:
            driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            logger.debug("Removed webdriver flag from navigator")
        except WebDriverException as e:
            logger.exception("Error executing script to remove webdriver flag")
        return driver

    def login(self):
        driver = self.driver
        try:
            logger.info("Navigating to Instagram login page")
            driver.get("https://www.instagram.com/accounts/login/")
        except WebDriverException as e:
            logger.exception("Error loading Instagram login page")
            raise Exception("Failed to load Instagram login page.") from e

        wait = WebDriverWait(driver, self.timeout)
        try:
            logger.debug("Waiting for username and password fields to be visible")
            username_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, self.selectors.get("LOGIN_USERNAME_INPUT"))))
            password_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, self.selectors.get("LOGIN_PASSWORD_INPUT"))))
        except TimeoutException as e:
            logger.exception("Timeout waiting for login form elements")
            raise Exception("Login failed: Timeout waiting for login form elements.") from e
        except WebDriverException as e:
            logger.exception("Error locating login form elements")
            raise Exception("Login failed: WebDriver error during element location.") from e

        try:
            logger.info("Entering login credentials")
            username_input.clear()
            username_input.send_keys(self.username)
            password_input.clear()
            password_input.send_keys(self.password)
            password_input.send_keys(Keys.RETURN)
        except WebDriverException as e:
            logger.exception("Error entering credentials")
            raise Exception("Login failed: Unable to enter credentials.") from e

        # Check for the "Ignorar" button after login
        Utils.click_ignore_button_if_present(driver, timeout=5, wait_before_click=1)

        try:
            logger.debug("Waiting for login result via redirect")
            WebDriverWait(driver, self.timeout).until(lambda d: d.current_url != "https://www.instagram.com/accounts/login/")
        except TimeoutException:
            logger.debug("Login via RETURN key failed, trying fallback button click...")

            try:
                login_buttons = driver.find_elements(By.XPATH, self.selectors.get("LOGIN_BUTTON_CANDIDATE"))
                logger.debug(f"Found {len(login_buttons)} login button candidates")

                for btn in login_buttons:
                    try:
                        inner_text = btn.get_attribute("textContent").strip().casefold()
                        if any(keyword in inner_text for keyword in self.keywords):
                            logger.debug(f"Found login button with matching text: '{inner_text}', clicking it.")
                            btn.click()
                            # Wait until the URL changes 
                            WebDriverWait(driver, self.timeout).until(
                                lambda d: d.current_url != "https://www.instagram.com/accounts/login/"
                            )
                            # Wait until page is fully loaded
                            WebDriverWait(driver, self.timeout).until(
                                lambda d: d.execute_script("return document.readyState") == "complete"
                            )
                            time.sleep(3) 
                            break
                    except Exception as e:
                        logger.debug(f"Skipping one candidate button due to error: {e}")
                else:
                    logger.error("No login button matched expected keywords.")
                    raise Exception("Login failed: No suitable login button found.")

            except Exception as e:
                logger.exception("Fallback login button click failed")
                raise Exception("Login failed: Unable to login using fallback method.") from e

        # Handle "Save your login info?" modal using utility method
        Utils.dismiss_save_login_modal(driver, self.close_keywords, self.timeout)

        logger.info("Login successful!")
        return True

if __name__ == '__main__':
    # Replace 'your_username' and 'your_password' with your actual credentials
    insta = InstaLogin('your_username', 'your_password', headless=False)
    try:
        insta.login()
    except Exception as e:
        logger.exception("An error occurred during login")
    finally:
        logger.info("Quitting WebDriver")
        insta.driver.quit()
