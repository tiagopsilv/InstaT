import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from loguru import logger
from selenium import webdriver
from selenium.common.exceptions import (
    WebDriverException,
)
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager

# (constants no longer needed here — moved to login_flow.py)
try:
    from instat.exceptions import AccountBlockedError
except ImportError:
    from exceptions import AccountBlockedError
try:
    from instat.block_detector import BlockDetector, BlockInfo
except ImportError:
    from block_detector import BlockDetector, BlockInfo  # type: ignore
try:
    from instat.challenge_resolvers import (
        ChallengeResolverChain,
        EmailChallengeResolver,
    )
except ImportError:
    from challenge_resolvers import (  # type: ignore
        ChallengeResolverChain,
        EmailChallengeResolver,
    )
try:
    from instat.login_flow import FormLogin, SessionRestorer
except ImportError:
    from login_flow import FormLogin, SessionRestorer  # type: ignore

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

    def __init__(self, username, password, headless=True, timeout=10,
                 session_cache=None, base_url=None, imap_config=None,
                 block_detector=None, challenge_chain=None):
        self.username = username
        self.password = password
        self.timeout = timeout
        self._session_cache = session_cache
        self._base_url = base_url or self.INSTAGRAM_BASE_URL
        self._imap_config = imap_config
        # block_detector is swappable: default instance uses the
        # builtin URL/HTML rules; callers can inject a subclass with
        # extra_checks() to add new detection paths without touching
        # the login flow.
        self._block_detector = block_detector or BlockDetector()
        logger.info("Initializing InstaLogin instance")
        self.driver = self.init_driver(headless)
        self.close_keywords = ["not now", "agora não", "salvar", "save", "skip", "not now", "ahora no", "jetzt nicht"]
        self.selectors = SelectorLoader()
        # challenge_chain is swappable: by default the chain contains
        # EmailChallengeResolver wired to our selectors + imap_config.
        # Callers can pass a chain with additional resolvers (new IG
        # flows) without editing this class.
        if challenge_chain is None:
            challenge_chain = self._default_challenge_chain()
        self._challenge_chain = challenge_chain
        # Login-flow collaborators. Both are stateless; sharing across
        # login() calls is fine.
        self._session_restorer = SessionRestorer(base_url=self._base_url)
        self._form_login = FormLogin(
            selector_loader=self.selectors,
            base_url=self._base_url,
            timeout=self.timeout,
        )

    def _default_challenge_chain(self) -> ChallengeResolverChain:
        """Factory for the builtin chain. Override in subclass or pass
        a custom chain via constructor to add resolvers."""
        return ChallengeResolverChain([
            EmailChallengeResolver(
                selector_loader=self.selectors,
                imap_config=self._imap_config,
                timeout=self.timeout,
            ),
        ])

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

    # Back-compat re-exports (kept for external callers that reached in).
    # The canonical source of truth moved to BlockDetector.
    BLOCK_INDICATORS = BlockDetector.URL_INDICATORS

    def _check_account_blocked(self, driver):
        """
        Verificação unificada de bloqueio pós-login. Delega a detecção
        ao `BlockDetector` e trata o resultado (screenshot + log + raise).
        Detecção e reação separadas — mudanças nos padrões do IG tocam
        apenas block_detector.py.
        """
        info = self._block_detector.check(driver)
        if info is None:
            return
        self._handle_block(driver, info)

    def _handle_block(self, driver, info: BlockInfo) -> None:
        """Screenshot + log + raise. Shared reaction path for every
        detection kind so the log shape stays consistent."""
        tag = self._tag_for_kind(info.kind, info.indicator)
        screenshot_path = self._save_block_evidence(driver, tag)
        self._log_block(info, screenshot_path)
        raise AccountBlockedError(
            f"Conta bloqueada: {info.reason}. {info.action}",
            reason=info.reason,
            url=info.url,
            screenshot_path=screenshot_path,
        )

    @staticmethod
    def _tag_for_kind(kind: str, indicator: str) -> str:
        if kind == 'html':
            return "meta_interstitial"
        if kind == 'login_loop':
            return "login_failed"
        # kind == 'url' — use the URL fragment itself (challenge, checkpoint…)
        return indicator or "blocked"

    def _log_block(self, info: BlockInfo, screenshot_path: str) -> None:
        parts = [
            f"CONTA BLOQUEADA: {info.reason}",
            f"  URL: {self._redact_url(info.url)}",
        ]
        if info.title:
            parts.append(f"  Titulo: {info.title}")
        if info.kind == 'html':
            parts.append(f"  Assinatura detectada: '{info.indicator}'")
        parts.append(f"  Acao: {info.action}")
        parts.append(f"  Evidencia: {screenshot_path}")
        logger.error("\n".join(parts))

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

    def _get_session_restorer(self) -> SessionRestorer:
        """Lazy accessor — tolerates tests that bypass __init__."""
        if getattr(self, '_session_restorer', None) is None:
            self._session_restorer = SessionRestorer(base_url=self._base_url)
        return self._session_restorer

    def _get_form_login(self) -> FormLogin:
        """Lazy accessor — tolerates tests that bypass __init__."""
        if getattr(self, '_form_login', None) is None:
            self._form_login = FormLogin(
                selector_loader=self.selectors,
                base_url=self._base_url,
                timeout=self.timeout,
            )
        return self._form_login

    def _try_restore_session(self, driver, cookies: list) -> bool:
        """Back-compat wrapper — delegates to SessionRestorer."""
        return self._get_session_restorer().attempt(driver, cookies)

    def _try_handle_email_challenge(self, driver) -> bool:
        """Delegates to the challenge resolver chain.

        Kept as a method for call-site compatibility; the actual
        logic per-flow lives in instat/challenge_resolvers.py.
        """
        return self._challenge_chain.try_resolve(driver)

    def login(self):
        """Orquestrador do fluxo de login em 6 fases.

        Cada fase vive em um módulo dedicado — mudanças pontuais do IG
        (layout, selectors, padrões de challenge, etc.) se concentram
        em um único arquivo, sem rebuscar esta função.
        """
        driver = self.driver

        # Phase 1: restore from cookie cache (fast path, preferred).
        if self._try_cookie_restore(driver):
            return True

        # Phase 2: classic form login.
        self._get_form_login().execute(driver, self.username, self.password)

        # Phase 3: resolve any email/Bloks challenge.
        if self._try_handle_email_challenge(driver):
            logger.info("Email challenge resolved via IMAP auto-fetch")

        # Phase 4: raise if account is in a blocked state.
        self._check_account_blocked(driver)

        # Phase 5: dismiss "Save your login info?" modal if present.
        Utils.dismiss_save_login_modal(
            driver, self.close_keywords, self.timeout,
        )

        logger.info("Login successful!")

        # Phase 6: persist cookies for next run's cookie-cache path.
        if self._session_cache:
            self._session_cache.save(self.username, driver.get_cookies())
            logger.debug('Session cookies saved to cache.')

        return True

    def _try_cookie_restore(self, driver) -> bool:
        if not self._session_cache:
            return False
        cookies = self._session_cache.load(self.username)
        if not cookies:
            return False
        return self._session_restorer.attempt(driver, cookies)

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
