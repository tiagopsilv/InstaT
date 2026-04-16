import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.firefox import GeckoDriverManager

try:
    from instat.constants import LOGIN_POST_CLICK_DELAY, human_delay
except ImportError:
    from constants import LOGIN_POST_CLICK_DELAY, human_delay
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
    backtrace=True,
    diagnose=False,
    format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | "
           "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)
logger.add("instat/logs/insta_login.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=False)

# Import our generic utility
try:
    from instat.config.selector_loader import SelectorLoader
    from instat.utils import Utils
except ImportError:
    from config.selector_loader import SelectorLoader
    from utils import Utils

class MetaInterstitialError(Exception):
    """Raised when a Meta interstitial (e.g., Meta Verified / checkpoint) blocks the login process."""
    def __init__(self, message, *, url, page_title, evidence_path=None, screenshot_path=None):
        super().__init__(message)
        self.url = url
        self.page_title = page_title
        self.evidence_path = evidence_path
        self.screenshot_path = screenshot_path

class InstaLogin:
    INSTAGRAM_BASE_URL = 'https://www.instagram.com'

    # Public list of keywords used to identify the login button in any language
    keywords = ["entrar", "log in", "login", "iniciar sesión", "connexion", "anmelden"]

    # Signatures used to detect Meta interstitials
    META_VERIFIED_SIGNATURES = [
        "o meta verified está disponível para o facebook e o instagram",  # Portuguese string from provided HTML
        "meta verified",
    ]

    def __init__(self, username, password, headless=True, timeout=10,
                 session_cache=None, base_url=None, imap_config=None):
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session_cache = session_cache
        self._base_url = base_url or self.INSTAGRAM_BASE_URL
        self._imap_config = imap_config
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

        # Performance: skip heavy resources not used by selectors.
        # Reduces per-navigation time by 30-50%.
        options.set_preference("permissions.default.image", 2)  # block images
        options.set_preference("dom.ipc.plugins.enabled.libflashplayer.so", False)
        options.set_preference("media.autoplay.default", 5)  # no autoplay
        options.set_preference("network.http.pipelining", True)
        options.set_preference("network.http.proxy.pipelining", True)
        options.set_preference("network.http.pipelining.maxrequests", 8)
        # Disable telemetry/studies for faster startup
        options.set_preference("toolkit.telemetry.enabled", False)
        options.set_preference("toolkit.telemetry.unified", False)
        options.set_preference("datareporting.healthreport.uploadEnabled", False)
        options.set_preference("app.shield.optoutstudies.enabled", False)

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
        except WebDriverException:
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

    @staticmethod
    def _redact_url(url: str) -> str:
        """Remove query + fragment de URL antes de logar.

        URLs de challenge contêm tokens como ?apc=... que são bearer tokens
        de curta duração. Se forem logados em arquivo persistente, alguém
        com acesso ao log pode reusar o token dentro do TTL.
        """
        try:
            parts = urlsplit(url)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        except Exception:
            return url

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
                    f"  URL: {self._redact_url(current_url)}\n"
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
                    f"  URL: {self._redact_url(current_url)}\n"
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
                f"  URL: {self._redact_url(current_url)}\n"
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
            try:
                os.chmod(screenshot_path, 0o600)
            except (OSError, NotImplementedError):
                pass
        except Exception as e:
            logger.warning(f"Failed to save screenshot: {e}")
            screenshot_path = "N/A"

        try:
            html_path.write_text(driver.page_source or "", encoding="utf-8")
            # page_source contém tokens de sessão (csrftoken, lsd, apc)
            try:
                os.chmod(html_path, 0o600)
            except (OSError, NotImplementedError):
                pass
        except Exception as e:
            logger.warning(f"Failed to save page source: {e}")

        return str(screenshot_path)

    def _try_restore_session(self, driver, cookies: list) -> bool:
        """Tenta restaurar sessão via cookies. True em sucesso.
        Limpa cookies do driver e retorna False em falha para fallback ao form login."""
        try:
            driver.get(f'{self._base_url}/')
            added = 0
            failed = 0
            for c in cookies:
                try:
                    driver.add_cookie(c)
                    added += 1
                except Exception as e:
                    failed += 1
                    logger.debug(f"cookie add failed: {c.get('name','?')} ({e})")
            logger.debug(f"Cookie cache: {added} added, {failed} failed")

            if added == 0:
                return False

            driver.refresh()

            # Check 1: URL doesn't redirect back to login
            if '/accounts/login' in driver.current_url.lower():
                logger.debug("After refresh still on login page — cookies invalid")
                try:
                    driver.delete_all_cookies()
                except Exception:
                    pass
                return False

            # Check 2: sessionid cookie present (stronger signal than URL)
            session_cookie = driver.get_cookie('sessionid')
            if not session_cookie:
                logger.debug("No sessionid cookie after refresh — not logged in")
                try:
                    driver.delete_all_cookies()
                except Exception:
                    pass
                return False

            logger.info('Session restored from cookie cache.')
            return True
        except Exception as e:
            logger.debug(f'Failed to restore session from cache: {e}')
            try:
                driver.delete_all_cookies()
            except Exception:
                pass
            return False

    def _try_handle_email_challenge(self, driver) -> bool:
        """
        Detecta a página 'Check your email' e preenche o código via IMAP.

        Retorna True se resolveu o challenge, False se não havia challenge
        ou não foi possível resolver (no último caso, deixa o fluxo normal
        de detecção de bloqueio tratar).
        """
        if not self._imap_config:
            return False
        try:
            from instat.email_code import ImapConfig, fetch_instagram_code
        except ImportError:
            from email_code import ImapConfig, fetch_instagram_code

        # Detecta heading da página
        headings = self.selectors.get_all("EMAIL_CHALLENGE_HEADING")
        heading_found = False
        for sel in headings:
            try:
                driver.find_element(By.CSS_SELECTOR, sel)
                heading_found = True
                break
            except NoSuchElementException:
                continue
        if not heading_found:
            return False

        logger.info("Detected email verification challenge — requesting fresh code")
        cfg = ImapConfig.from_dict(self._imap_config)
        from datetime import datetime, timezone

        # Clica em "Get a new code" para forçar IG a enviar código fresh,
        # evitando pegar códigos já usados/expirados de tentativas anteriores.
        new_code_clicked = False
        for sel in self.selectors.get_all("EMAIL_CHALLENGE_GET_NEW_CODE"):
            try:
                el = driver.find_element(By.XPATH, sel)
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                new_code_clicked = True
                logger.info("Clicked 'Get a new code' to trigger fresh email")
                break
            except NoSuchElementException:
                continue

        # Marca tempo ANTES de solicitar novo código; buscaremos apenas
        # e-mails recebidos a partir daqui (garante código novo exclusivo
        # desta sessão, não um residual de testes anteriores).
        started_at = datetime.now(timezone.utc)
        if new_code_clicked:
            # Pequena espera para IG processar o clique e a SMTP entregar
            human_delay(6.0, variance=1.0)
        code = fetch_instagram_code(cfg, started_at=started_at)
        if not code:
            logger.warning("IMAP fetch returned no code; falling back to block detection")
            return False

        # Preenche o input
        input_el = None
        for sel in self.selectors.get_all("EMAIL_CHALLENGE_INPUT"):
            try:
                input_el = driver.find_element(By.CSS_SELECTOR, sel)
                break
            except NoSuchElementException:
                continue
        if input_el is None:
            logger.warning("Email challenge input not found after detection")
            return False

        try:
            input_el.clear()
        except Exception:
            pass

        # Preenche via send_keys + dispara eventos React (input + change + blur)
        # para garantir que o framework perceba o valor e habilite o Continue.
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
            logger.debug(f"React event dispatch failed (non-fatal): {e}")
        # Pequena pausa para UI re-renderizar e habilitar o botão
        human_delay(0.8, variance=0.2)

        # Clica Continue — 3 estratégias: native click, JS click, send Enter
        clicked = False
        btn = None
        for sel in self.selectors.get_all("EMAIL_CHALLENGE_CONTINUE"):
            try:
                btn = driver.find_element(By.XPATH, sel)
                break
            except NoSuchElementException:
                continue
        if btn is not None:
            # Bloks/Meta monta o handler de clique no ancestor com
            # cursor:pointer e pointer-events:auto, não no role=button em si.
            # Tentamos 4 estratégias em cascata.
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
                    clicked = True
                    logger.debug(f"Continue clicked via {strategy}")
                    # Pequeno delay e verifica se challenge sumiu — se não,
                    # próxima estratégia já.
                    human_delay(1.5, variance=0.3)
                    try:
                        driver.find_element(By.CSS_SELECTOR, headings[0])
                        # heading ainda presente → estratégia não funcionou
                        clicked = False
                        continue
                    except NoSuchElementException:
                        break
                except Exception as e:
                    logger.debug(f"Continue click via {strategy} failed: {e}")
        if not clicked:
            logger.warning("Could not click Continue on email challenge")
            return False

        # Aguarda navegação sair do challenge
        def _challenge_gone(d):
            for s in headings:
                try:
                    d.find_element(By.CSS_SELECTOR, s)
                    return False
                except NoSuchElementException:
                    continue
            return True
        try:
            WebDriverWait(driver, self.timeout).until(_challenge_gone)
            return True
        except TimeoutException:
            logger.warning("Email challenge page still present after Continue")
            return False

    def login(self):
        driver = self.driver

        # Attempt session restore from cookie cache — much faster than form login
        if self._session_cache:
            cookies = self._session_cache.load(self.username)
            if cookies:
                if self._try_restore_session(driver, cookies):
                    return True

        try:
            logger.info("Navigating to Instagram login page")
            driver.get(f"{self._base_url}/accounts/login/")
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
            login_url = f"{self._base_url}/accounts/login/"
            WebDriverWait(driver, self.timeout).until(lambda d: d.current_url != login_url)
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
                            _login_url = f"{self._base_url}/accounts/login/"
                            WebDriverWait(driver, self.timeout).until(
                                lambda d, u=_login_url: d.current_url != u
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

        # Se bateu em challenge "Check your email" e temos IMAP configurado, resolve automaticamente.
        if self._try_handle_email_challenge(driver):
            logger.info("Email challenge resolved via IMAP auto-fetch")

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
    except Exception:
        logger.exception("An error occurred during login")
    finally:
        logger.info("Quitting WebDriver")
        insta.driver.quit()
