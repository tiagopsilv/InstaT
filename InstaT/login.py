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
    WebDriverException
)
from selenium.common.exceptions import StaleElementReferenceException
import time
from datetime import datetime
from pathlib import Path

try:
    from instat.constants import human_delay, LOGIN_POST_CLICK_DELAY
except ImportError:
    from constants import human_delay, LOGIN_POST_CLICK_DELAY
try:
    from instat.session_cache import SessionCache
except ImportError:
    from session_cache import SessionCache
try:
    from instat.exceptions import AccountBlockedError
except ImportError:
    from exceptions import AccountBlockedError

# Setup Loguru logger for advanced logging
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
logger.add("instat/logs/insta_login.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=False)

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

    def __init__(self, username, password, headless=True, timeout=10, session_cache=None):
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session_cache = session_cache
        logger.info("Initializing InstaLogin instance")
        self.driver = self.init_driver(headless)
        self.close_keywords = ["not now", "agora não", "salvar", "save", "skip", "not now", "ahora no", "jetzt nicht"]
        self.selectors = SelectorLoader()
    
    @staticmethod
    def _find_cached_geckodriver() -> str:
        """Procura geckodriver no cache local do webdriver-manager."""
        wdm_dir = Path.home() / ".wdm" / "drivers" / "geckodriver"
        if not wdm_dir.exists():
            return None
        executables = sorted(wdm_dir.rglob("geckodriver*"), reverse=True)
        for exe in executables:
            if exe.is_file() and exe.suffix in ('', '.exe'):
                return str(exe)
        return None

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

        # Tenta webdriver-manager; se falhar (rate limit), usa cache local
        geckodriver_path = None
        try:
            logger.debug("Installing GeckoDriver using webdriver-manager")
            geckodriver_path = GeckoDriverManager().install()
        except Exception as e:
            logger.warning(f"webdriver-manager failed ({type(e).__name__}), searching local cache...")
            geckodriver_path = self._find_cached_geckodriver()
            if geckodriver_path:
                logger.info(f"Using cached geckodriver: {geckodriver_path}")
            else:
                logger.error("No cached geckodriver found. Install Firefox and geckodriver manually.")
                raise Exception(
                    "Failed to obtain geckodriver: webdriver-manager rate-limited and no local cache found. "
                    "Set GH_TOKEN env var or install geckodriver manually."
                ) from e

        try:
            driver = webdriver.Firefox(service=Service(geckodriver_path), options=options)
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

    # Mapa de indicadores de bloqueio: padrão na URL -> (reason legível, ação sugerida)
    BLOCK_INDICATORS = {
        'challenge':      ("Desafio de segurança (challenge)", "Abra o Instagram no navegador e resolva o desafio manualmente."),
        'checkpoint':     ("Checkpoint de verificação", "Verifique o e-mail ou SMS associado à conta e confirme a identidade."),
        'auth_platform':  ("Verificação de plataforma Meta", "Acesse o e-mail da conta e siga as instruções de verificação."),
        'codeentry':      ("Código de verificação exigido (2FA/e-mail)", "Insira o código enviado por e-mail/SMS no Instagram."),
        'two_factor':     ("Autenticação de dois fatores (2FA)", "Use o app autenticador ou código SMS para completar o login."),
        'suspicious':     ("Atividade suspeita detectada", "Faça login manual no navegador para desbloquear a conta."),
        'consent':        ("Consentimento obrigatório (GDPR/termos)", "Aceite os termos de uso no navegador manualmente."),
    }

    def _check_account_blocked(self, driver):
        """
        Verificação unificada de bloqueio pós-login.
        Detecta: checkpoint, 2FA, Meta interstitial, credenciais inválidas, consent.
        Salva screenshot como evidência e levanta AccountBlockedError com motivo detalhado.
        """
        current_url = driver.current_url
        url_lower = current_url.lower()

        # 1. Detecção por URL (checkpoint, 2FA, challenge, etc.)
        for indicator, (reason, action) in self.BLOCK_INDICATORS.items():
            if indicator in url_lower:
                screenshot_path = self._save_block_evidence(driver, indicator)
                logger.error(
                    f"CONTA BLOQUEADA: {reason}\n"
                    f"  URL: {current_url}\n"
                    f"  Titulo: {driver.title}\n"
                    f"  Acao: {action}\n"
                    f"  Evidencia: {screenshot_path}"
                )
                raise AccountBlockedError(
                    f"Conta bloqueada: {reason}. {action}",
                    reason=reason,
                    url=current_url,
                    screenshot_path=screenshot_path,
                )

        # 2. Detecção por conteúdo HTML (Meta Verified, etc.)
        html_lc = (driver.page_source or "").casefold()
        page_title = (driver.title or "").strip()

        for sig in self.META_VERIFIED_SIGNATURES:
            if sig in html_lc:
                reason = "Intersticial Meta Verified"
                action = "Verifique o e-mail da conta para instruções de verificação Meta."
                screenshot_path = self._save_block_evidence(driver, "meta_interstitial")
                logger.error(
                    f"CONTA BLOQUEADA: {reason}\n"
                    f"  URL: {current_url}\n"
                    f"  Titulo: {page_title}\n"
                    f"  Assinatura detectada: '{sig}'\n"
                    f"  Acao: {action}\n"
                    f"  Evidencia: {screenshot_path}"
                )
                raise AccountBlockedError(
                    f"Conta bloqueada: {reason}. {action}",
                    reason=reason,
                    url=current_url,
                    screenshot_path=screenshot_path,
                )

        # 3. Detecção de página de login ainda ativa (credenciais inválidas ou loop)
        if '/accounts/login' in url_lower:
            reason = "Credenciais inválidas ou login em loop"
            action = "Verifique username/password. A conta pode estar desativada."
            screenshot_path = self._save_block_evidence(driver, "login_failed")
            logger.error(
                f"CONTA BLOQUEADA: {reason}\n"
                f"  URL: {current_url}\n"
                f"  Acao: {action}\n"
                f"  Evidencia: {screenshot_path}"
            )
            raise AccountBlockedError(
                f"Conta bloqueada: {reason}. {action}",
                reason=reason,
                url=current_url,
                screenshot_path=screenshot_path,
            )

    def _save_block_evidence(self, driver, tag: str) -> str:
        """Salva screenshot e HTML como evidência de bloqueio."""
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        artifacts_dir = Path("instat/logs/artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = artifacts_dir / f"blocked_{tag}_{ts}.png"
        html_path = artifacts_dir / f"blocked_{tag}_{ts}.html"

        try:
            driver.save_screenshot(str(screenshot_path))
        except Exception as e:
            logger.warning(f"Failed to save screenshot: {e}")
            screenshot_path = "N/A"

        try:
            html_path.write_text(driver.page_source or "", encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save page source: {e}")

        return str(screenshot_path)

    def login(self):
        driver = self.driver

        # Tentativa de restaurar sessão via cache de cookies
        if self._session_cache:
            cookies = self._session_cache.load(self.username)
            if cookies:
                try:
                    driver.get('https://www.instagram.com/')
                    for c in cookies:
                        driver.add_cookie(c)
                    driver.refresh()
                    if '/accounts/login' not in driver.current_url:
                        logger.info('Session restored from cookie cache.')
                        return True
                    logger.debug('Cached cookies expired or invalid, proceeding with normal login.')
                except Exception as e:
                    logger.debug(f'Failed to restore session from cache: {e}')

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
                            human_delay(LOGIN_POST_CLICK_DELAY)
                            break
                    except Exception as e:
                        logger.debug(f"Skipping one candidate button due to error: {e}")
                else:
                    logger.error("No login button matched expected keywords.")
                    raise Exception("Login failed: No suitable login button found.")

            except Exception as e:
                logger.exception("Fallback login button click failed")
                raise Exception("Login failed: Unable to login using fallback method.") from e

        # Validação unificada de bloqueio de conta
        self._check_account_blocked(driver)

        # Handle "Save your login info?" modal using utility method
        Utils.dismiss_save_login_modal(driver, self.close_keywords, self.timeout)

        logger.info("Login successful!")

        if self._session_cache:
            self._session_cache.save(self.username, driver.get_cookies())
            logger.debug('Session cookies saved to cache.')

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
