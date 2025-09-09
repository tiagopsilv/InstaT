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
import re
from datetime import datetime
from pathlib import Path

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
logger.add("instat/logs/insta_login.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=True)

# Import our generic utility
try:
    from instat.utils import Utils
    from instat.config.selector_loader import SelectorLoader
except ImportError:
    from utils import Utils
    from config.selector_loader import SelectorLoader

class MetaInterstitialError(Exception):
    """Raised when a Meta interstitial (e.g., Meta Verified / checkpoint) blocks the login process."""
    def __init__(self, message, *, url, page_title, evidence_path=None, screenshot_path=None):
        super().__init__(message)
        self.url = url
        self.page_title = page_title
        self.evidence_path = evidence_path
        self.screenshot_path = screenshot_path

class InstaLogin:
    # Public list of keywords used to identify the login button in any language
    keywords = ["entrar", "log in", "login", "iniciar sesión", "connexion", "anmelden"]

    # Signatures used to detect Meta interstitials
    META_VERIFIED_SIGNATURES = [
        "o meta verified está disponível para o facebook e o instagram",  # Portuguese string from provided HTML
        "meta verified",
    ]

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

    def _check_for_meta_interstitial(self):
        """Check if a Meta interstitial (Meta Verified, checkpoint, etc.) appeared after login."""
        driver = self.driver
        page_title = (driver.title or "").strip()
        current_url = driver.current_url
        html = driver.page_source or ""
        html_lc = html.casefold()

        matched = None
        for sig in self.META_VERIFIED_SIGNATURES:
            if sig in html_lc:
                matched = sig
                break

        looks_like_fb_title = page_title.casefold().startswith("facebook")

        if matched:
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            artifacts_dir = Path("instat/logs/artifacts")
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            evidence_path = artifacts_dir / f"meta_interstitial_{ts}.html"
            screenshot_path = artifacts_dir / f"meta_interstitial_{ts}.png"

            try:
                with open(evidence_path, "w", encoding="utf-8") as f:
                    f.write(html)
            except Exception as e:
                logger.warning(f"Failed to save page source: {e}")

            try:
                driver.save_screenshot(str(screenshot_path))
            except Exception as e:
                logger.warning(f"Failed to save screenshot: {e}")

            # 🔴 Log now clearly says manual email verification is required
            logger.error(
                "Meta/Instagram interstitial detected after login.\n"
                f"- URL: {current_url}\n"
                f"- Title: {page_title}\n"
                f"- Matched signature: '{matched}'\n"
                f"- Title looks like Facebook? {looks_like_fb_title}\n"
                f"- Page source: {evidence_path}\n"
                f"- Screenshot: {screenshot_path}\n"
                "⚠️ Action required: Check the registered e-mail for verification instructions.\n"
            )

            raise MetaInterstitialError(
                "Login blocked by Meta/Instagram interstitial. "
                "Manual action required: verify the account through the registered e-mail.",
                url=current_url,
                page_title=page_title,
                evidence_path=str(evidence_path),
                screenshot_path=str(screenshot_path),
            )

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

        self._check_for_meta_interstitial()

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
