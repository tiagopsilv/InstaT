"""
PlaywrightEngine: engine de extração via Playwright + stealth.
Dependência OPCIONAL — instalar com: pip install instat[playwright]
Implementa BaseEngine. Fallback automático quando Selenium é bloqueado.
"""
from typing import Callable, Optional, Set

from loguru import logger

try:
    from instat.constants import SCROLL_PAUSE, human_delay
    from instat.engines.base import BaseEngine
    from instat.exceptions import BlockedError, ProfileNotFoundError
    from instat.session_cache import SessionCache
except ImportError:
    from constants import SCROLL_PAUSE, human_delay
    from engines.base import BaseEngine
    from exceptions import BlockedError, ProfileNotFoundError
    from session_cache import SessionCache

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 8.0; Nexus 5 Build/OPR6.170623.013) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.72 Mobile Safari/537.36"
)


class PlaywrightEngine(BaseEngine):
    """
    Engine baseada em Playwright + stealth.
    Suporta chromium, webkit, firefox via param browser_type.
    Mais rápida que Selenium (CDP), com stealth para reduzir detecção.
    """

    @property
    def name(self) -> str:
        return f'playwright-{self._browser_type}'

    @property
    def is_available(self) -> bool:
        try:
            import playwright  # noqa: F401
            import playwright_stealth  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self, browser_type: str = 'chromium', headless: bool = True,
                 timeout: int = 10000, proxy: Optional[str] = None):
        if browser_type not in ('chromium', 'webkit', 'firefox'):
            raise ValueError(f"Invalid browser_type: {browser_type}")
        self._browser_type = browser_type
        self._headless = headless
        self._timeout = timeout
        self._proxy = proxy
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._session_cache = SessionCache()

    def login(self, username: str, password: str, **kwargs) -> bool:
        # Imports aqui (não no topo) para que is_available funcione sem playwright
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync

        self._playwright = sync_playwright().start()
        browser_launcher = getattr(self._playwright, self._browser_type)
        self._browser = browser_launcher.launch(headless=self._headless)

        ctx_kwargs = {'user_agent': MOBILE_UA, 'viewport': {'width': 375, 'height': 667}}
        if self._proxy:
            ctx_kwargs['proxy'] = {'server': self._proxy}

        # Tentar restaurar sessão via cookies cached
        cookies = self._session_cache.load(username)
        if cookies:
            try:
                self._context = self._browser.new_context(**ctx_kwargs)
                self._context.add_cookies(cookies)
                self._page = self._context.new_page()
                stealth_sync(self._page)
                self._page.goto('https://www.instagram.com/')
                if '/accounts/login' not in self._page.url:
                    logger.info(f'{self.name}: session restored from cookie cache')
                    return True
                logger.debug(f'{self.name}: cached cookies invalid, doing form login')
                self._context.close()
                self._context = None
                self._page = None
            except Exception as e:
                logger.debug(f'{self.name}: cache restore failed: {e}')

        # Form login
        self._context = self._browser.new_context(**ctx_kwargs)
        self._page = self._context.new_page()
        stealth_sync(self._page)

        try:
            self._page.goto('https://www.instagram.com/accounts/login/',
                            timeout=self._timeout)
            self._page.fill('input[name="username"]', username)
            self._page.fill('input[name="password"]', password)
            self._page.keyboard.press('Enter')
            self._page.wait_for_function(
                "() => !window.location.href.includes('/accounts/login/')",
                timeout=self._timeout
            )
        except Exception as e:
            logger.error(f'{self.name}: login failed: {e}')
            raise BlockedError(f'{self.name} login failed') from e

        # Detect block (checkpoint, 2FA, etc) via URL pattern
        current_url = self._page.url.lower()
        block_patterns = ['challenge', 'checkpoint', 'auth_platform',
                          'codeentry', 'two_factor', 'suspicious']
        if any(p in current_url for p in block_patterns):
            logger.error(f'{self.name}: blocked at URL {current_url}')
            raise BlockedError(f'{self.name} blocked: {current_url}')

        # Save cookies
        try:
            self._session_cache.save(username, self._context.cookies())
            logger.info(f'{self.name}: login successful, cookies cached')
        except Exception as e:
            logger.debug(f'{self.name}: failed to save cookies: {e}')
        return True

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None) -> Set[str]:
        import time
        if not self._page:
            raise BlockedError(f'{self.name} not logged in')

        link_selector_map = {
            'followers': 'a[href*="/followers/"]',
            'following': 'a[href*="/following/"]',
        }
        if list_type not in link_selector_map:
            raise ValueError(f"Invalid list_type: {list_type}")

        collected = set(existing_profiles) if existing_profiles else set()
        intercepted = set()  # perfis vindos de XHR
        start_time = time.perf_counter()

        # XHR_PATTERNS: substrings de URL que indicam endpoint de followers/following
        XHR_PATTERNS = ['/friendships/', '/followers/', '/following/']

        def handle_response(response):
            """Callback chamado para CADA response. Nunca propaga exceção."""
            try:
                url = getattr(response, 'url', '')
                if getattr(response, 'status', 0) != 200:
                    return
                if not any(p in url for p in XHR_PATTERNS):
                    return
                try:
                    data = response.json()
                except Exception:
                    return
                if not isinstance(data, dict):
                    return
                users = data.get('users', [])
                if not isinstance(users, list):
                    return
                added = 0
                for user in users:
                    if isinstance(user, dict):
                        username = user.get('username', '')
                        if username:
                            intercepted.add(username)
                            added += 1
                if added > 0:
                    logger.debug(f'{self.name}: XHR +{added} profiles (intercepted total: {len(intercepted)})')
            except Exception as e:
                logger.debug(f'{self.name}: handle_response error (ignored): {e}')

        # Registrar handler ANTES de navegar para capturar todas as XHRs
        self._page.on('response', handle_response)

        try:
            # Navigate to profile
            try:
                self._page.goto(f'https://www.instagram.com/{profile_id}/',
                                timeout=self._timeout)
            except Exception as e:
                raise BlockedError(f'{self.name}: failed to navigate to {profile_id}') from e

            # Click followers/following link
            link_sel = link_selector_map[list_type]
            try:
                self._page.click(link_sel, timeout=self._timeout)
            except Exception as e:
                raise ProfileNotFoundError(
                    f'{self.name}: {list_type} link not found for {profile_id}') from e

            # Wait for modal
            try:
                self._page.wait_for_selector('div[role="dialog"]', timeout=self._timeout)
            except Exception:
                raise BlockedError(f'{self.name}: modal did not appear')

            # Scroll loop with XHR + DOM merge
            previous_total = -1
            stale_rounds = 0
            xhr_check_rounds = 0
            xhr_active = True
            profile_link_sel = 'div[role="dialog"] a[role="link"][href^="/"][href$="/"]'
            XHR_INACTIVE_THRESHOLD = 3

            while True:
                if max_duration and (time.perf_counter() - start_time) > max_duration:
                    logger.info(f'{self.name}: max_duration reached')
                    break

                intercepted_before = len(intercepted)

                # DOM scrape (sempre executar — fonte secundária / fallback)
                dom_profiles = set()
                try:
                    links = self._page.query_selector_all(profile_link_sel)
                    for link in links:
                        href = link.get_attribute('href') or ''
                        parts = href.strip('/').split('/')
                        if parts and parts[0]:
                            dom_profiles.add(parts[0])
                except Exception as e:
                    logger.debug(f'{self.name}: DOM scrape error: {e}')

                # MERGE: existing collected | intercepted (XHR) | dom_profiles
                collected = collected | intercepted | dom_profiles

                if on_batch:
                    on_batch(collected)

                # XHR health check
                xhr_added = len(intercepted) - intercepted_before
                if xhr_added == 0:
                    xhr_check_rounds += 1
                    if xhr_active and xhr_check_rounds >= XHR_INACTIVE_THRESHOLD:
                        xhr_active = False
                        logger.warning(
                            f'{self.name}: XHR interception inactive after '
                            f'{XHR_INACTIVE_THRESHOLD} rounds. Falling back to DOM-only mode.'
                        )
                else:
                    xhr_check_rounds = 0

                # Stale detection sobre o total mergeado
                if len(collected) == previous_total:
                    stale_rounds += 1
                    if stale_rounds >= 3:
                        logger.info(f'{self.name}: stale, stopping')
                        break
                else:
                    stale_rounds = 0
                    previous_total = len(collected)

                # Scroll inside modal via JS (dispara nova XHR)
                try:
                    self._page.evaluate(
                        """() => {
                            const dialog = document.querySelector('div[role="dialog"]');
                            if (dialog) {
                                const scrollable = dialog.querySelector('div[style*="overflow"]') || dialog;
                                scrollable.scrollTop = scrollable.scrollHeight;
                            }
                        }"""
                    )
                except Exception as e:
                    logger.debug(f'{self.name}: scroll error: {e}')

                human_delay(SCROLL_PAUSE)

            dom_contribution = len(collected) - len(intercepted) - len(existing_profiles or set())
            logger.info(
                f'{self.name}: extracted {len(collected)} profiles '
                f'(XHR: {len(intercepted)}, DOM contribution: {max(0, dom_contribution)})'
            )
            return collected

        finally:
            # Sempre desregistrar handler para evitar vazamento de listener
            try:
                self._page.remove_listener('response', handle_response)
            except Exception:
                pass

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        import re
        if not self._page:
            return None
        if list_type not in ('followers', 'following'):
            return None
        try:
            self._page.goto(f'https://www.instagram.com/{profile_id}/',
                            timeout=self._timeout)
            link_sel = f'a[href*="/{list_type}/"]'
            link = self._page.query_selector(link_sel)
            if not link:
                return None
            text = (link.inner_text() or '').strip()
            match = re.search(r'([\d.,]+)\s*([kKmM]?)', text)
            if not match:
                return None
            num_str, suffix = match.groups()
            num = float(num_str.replace(',', '.').replace('.', ''))
            if suffix.lower() == 'k':
                num *= 1_000
            elif suffix.lower() == 'm':
                num *= 1_000_000
            return int(num)
        except Exception as e:
            logger.debug(f'{self.name}: get_total_count failed: {e}')
            return None

    def quit(self) -> None:
        try:
            if self._context:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
