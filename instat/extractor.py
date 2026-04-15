# Built-in
import re
import sys
import time
from typing import Dict, List, Optional

# Third-party
from loguru import logger

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
logger.add("instat/logs/insta_extractor.log", rotation="10 MB", retention="10 days", level="DEBUG", backtrace=True, diagnose=False)

try:
    from instat.engines.engine_manager import EngineManager
    from instat.engines.selenium_engine import SeleniumEngine
    from instat.exceptions import LoginError
    from instat.exporters import BaseExporter, CSVExporter, JSONExporter, SQLiteExporter
    from instat.login import InstaLogin
    from instat.proxy import ProxyPool
    from instat.session_pool import SessionPool
except ImportError:
    from engines.engine_manager import EngineManager
    from engines.selenium_engine import SeleniumEngine
    from exceptions import LoginError
    from exporters import BaseExporter, CSVExporter, JSONExporter, SQLiteExporter
    from login import InstaLogin
    from proxy import ProxyPool
    from session_pool import SessionPool


class InstaExtractor:
    """
    Facade for Instagram data extraction.

    Delegates to SeleniumEngine via EngineManager. Maintains the same public API
    as the original monolithic implementation for full backward compatibility.

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
    - max_refresh_attempts (int): Max page refreshes. Default: 100.
    - wait_interval (float): Wait between profile checks (seconds). Default: 0.5.
    - additional_scroll_attempts (int): Extra scroll attempts. Default: 1.
    - pause_time (float): Pause between scrolls (seconds). Default: 0.5.
    - max_attempts (int): Scroll attempts per cycle. Default: 2.
    - checkpoint_interval (int): Save checkpoint every N profiles. Default: 100.

    """

    def __init__(self, username: str, password: str,
                 headless: bool = True, timeout: int = 10,
                 proxies: Optional[List[str]] = None,
                 accounts: Optional[List[Dict[str, str]]] = None,
                 engines: Optional[List[str]] = None,
                 exporter: Optional[BaseExporter] = None) -> None:
        """
        engines: lista de nomes ['selenium', 'playwright', 'httpx']. Default: ['selenium'].
        exporter: exporter opcional chamado após cada extração bem-sucedida.
        """
        self.username = username
        self.password = password
        self.timeout = timeout
        self._exporter = exporter

        proxy_pool = None
        if proxies:
            proxy_pool = ProxyPool(proxies)
            logger.info(f"InstaExtractor: using proxy pool with {len(proxies)} proxies")

        session_pool = None
        if accounts:
            session_pool = SessionPool(accounts, proxy_pool=proxy_pool)
            logger.info(f"InstaExtractor: using session pool with {len(accounts)} accounts")

        # Resolver lista de engines
        engine_names = engines or ['selenium']
        engine_instances = self._build_engines(engine_names, headless, timeout)
        primary_engine = engine_instances[0]

        logger.info("Initializing InstaExtractor with username: {}", username)

        # Login inicial APENAS se não houver session_pool.
        # Com session_pool, o EngineManager faz login sob demanda por session.
        if session_pool is None:
            try:
                primary_engine.login(username, password)
                self._engine = primary_engine
                self.driver = getattr(primary_engine, '_driver', None)
                self.insta_login = getattr(primary_engine, '_login_obj', None)
                logger.info("Logged in successfully as {}", username)
            except Exception as e:
                logger.exception("Login failed during InstaExtractor initialization: {}", e)
                raise LoginError(f"Failed to login as {username}") from e
        else:
            self._engine = primary_engine
            self.driver = None
            self.insta_login = None
            logger.info("InstaExtractor: multi-account mode, login deferred to EngineManager")

        self._engine_manager = EngineManager(
            engine_instances,
            proxy_pool=proxy_pool,
            session_pool=session_pool,
            default_credentials=(username, password),
        )
        # Primary engine já fez login — não tentar de novo
        if session_pool is None:
            self._engine_manager._logged_in_engines.add(id(primary_engine))

    def _build_engines(self, names: List[str], headless: bool, timeout: int):
        """
        Converte nomes em instâncias de engines.
        Filtra engines indisponíveis silenciosamente.
        Levanta RuntimeError se nenhuma engine usável.
        """
        try:
            from instat.engines.playwright_engine import PlaywrightEngine
        except ImportError:
            from engines.playwright_engine import PlaywrightEngine

        built = []
        for name in names:
            if name == 'selenium':
                built.append(SeleniumEngine(
                    headless=headless, timeout=timeout, _login_class=InstaLogin
                ))
            elif name == 'playwright':
                eng = PlaywrightEngine(headless=headless, timeout=timeout * 1000)
                if eng.is_available:
                    built.append(eng)
                else:
                    logger.warning("playwright requested but not installed — skipping")
            elif name == 'httpx':
                try:
                    from instat.engines.httpx_engine import HttpxEngine
                except ImportError:
                    from engines.httpx_engine import HttpxEngine
                eng = HttpxEngine(timeout=timeout)
                if eng.is_available:
                    built.append(eng)
                else:
                    logger.warning("httpx requested but not installed — skipping")
            else:
                logger.warning(f"Unknown engine name: {name}")
        if not built:
            raise RuntimeError(f"No usable engines from {names}")
        return built

    # --- Configurable attributes delegated to engine ---

    @property
    def max_refresh_attempts(self):
        return self._engine.max_refresh_attempts

    @max_refresh_attempts.setter
    def max_refresh_attempts(self, v):
        self._engine.max_refresh_attempts = v

    @property
    def wait_interval(self):
        return self._engine.wait_interval

    @wait_interval.setter
    def wait_interval(self, v):
        self._engine.wait_interval = v

    @property
    def additional_scroll_attempts(self):
        return self._engine.additional_scroll_attempts

    @additional_scroll_attempts.setter
    def additional_scroll_attempts(self, v):
        self._engine.additional_scroll_attempts = v

    @property
    def pause_time(self):
        return self._engine.pause_time

    @pause_time.setter
    def pause_time(self, v):
        self._engine.pause_time = v

    @property
    def max_attempts(self):
        return self._engine.max_attempts

    @max_attempts.setter
    def max_attempts(self, v):
        self._engine.max_attempts = v

    @property
    def max_retry_without_new_profiles(self):
        return self._engine.max_retry_without_new_profiles

    @max_retry_without_new_profiles.setter
    def max_retry_without_new_profiles(self, v):
        self._engine.max_retry_without_new_profiles = v

    @property
    def checkpoint_interval(self):
        return self._engine.checkpoint_interval

    @checkpoint_interval.setter
    def checkpoint_interval(self, v):
        self._engine.checkpoint_interval = v

    # --- Public API ---

    def get_profile(self, profile_id: str):
        """
        Navega ao perfil 1 vez e extrai metadata barato do header
        (contadores, full_name, verified/private, profile_pic_url).

        Retorna Profile ligado a este extractor — use
        profile.get_followers() / profile.get_following().
        """
        try:
            from instat.profile import Profile, parse_profile_from_meta
        except ImportError:
            from profile import Profile, parse_profile_from_meta

        driver = getattr(self._engine, '_driver', None)
        if driver is None:
            raise RuntimeError("get_profile requires a Selenium-based engine")

        url = f"https://www.instagram.com/{profile_id}/"
        driver.get(url)

        def _meta(prop: str) -> str:
            try:
                el = driver.find_element('css selector', f'meta[property="{prop}"]')
                return el.get_attribute('content') or ''
            except Exception:
                return ''

        og_desc = _meta('og:description')
        og_title = _meta('og:title')
        og_image = _meta('og:image')

        counts = parse_profile_from_meta(og_desc)

        # og:title: "Full Name (@username) • Instagram photos and videos"
        full_name = None
        if og_title:
            m = re.match(r'^(.*?)\s*\(@', og_title)
            if m:
                full_name = m.group(1).strip() or None

        is_verified = None
        try:
            is_verified = bool(driver.execute_script(
                "return !!document.querySelector('svg[aria-label=\"Verified\"]');"
            ))
        except Exception:
            pass

        is_private = None
        try:
            is_private = bool(driver.execute_script(
                "return document.body.innerText.toLowerCase().includes"
                "('this account is private') || "
                "document.body.innerText.toLowerCase().includes('conta privada');"
            ))
        except Exception:
            pass

        return Profile(
            username=profile_id,
            url=url,
            full_name=full_name,
            bio=None,
            followers_count=counts.get('followers_count'),
            following_count=counts.get('following_count'),
            posts_count=counts.get('posts_count'),
            is_private=is_private,
            is_verified=is_verified,
            profile_pic_url=og_image or None,
            _extractor=self,
        )

    def get_followers(self, profile_id: str, max_duration: Optional[float] = None) -> List[str]:
        """Returns a list of followers for the given profile id."""
        return self._extract_with_export(profile_id, 'followers', max_duration)

    def get_following(self, profile_id: str, max_duration: Optional[float] = None) -> List[str]:
        """Returns a list of accounts that the given profile id is following."""
        return self._extract_with_export(profile_id, 'following', max_duration)

    def get_followers_parallel(self, profile_id: str,
                               workers: int = 2,
                               accounts: Optional[List[Dict[str, str]]] = None,
                               stop_threshold: float = 0.98,
                               max_duration: Optional[float] = None,
                               headless: bool = True) -> List[str]:
        """Extrai followers com N SeleniumEngines em paralelo (união)."""
        return self._parallel(profile_id, 'followers', workers, accounts,
                              stop_threshold, max_duration, headless)

    def get_following_parallel(self, profile_id: str,
                               workers: int = 2,
                               accounts: Optional[List[Dict[str, str]]] = None,
                               stop_threshold: float = 0.98,
                               max_duration: Optional[float] = None,
                               headless: bool = True) -> List[str]:
        """Extrai following com N SeleniumEngines em paralelo (união)."""
        return self._parallel(profile_id, 'following', workers, accounts,
                              stop_threshold, max_duration, headless)

    def _parallel(self, profile_id: str, list_type: str, workers: int,
                  accounts, stop_threshold, max_duration, headless) -> List[str]:
        try:
            from instat.parallel import parallel_extract
        except ImportError:
            from parallel import parallel_extract
        target = self.get_total_count(profile_id, list_type)
        try:
            result = parallel_extract(
                profile_id, list_type,
                workers=workers,
                default_credentials=(self.username, self.password),
                accounts=accounts,
                stop_threshold=stop_threshold,
                target_count=target,
                max_duration=max_duration,
                headless=headless,
                timeout=self.timeout,
            )
        except Exception as e:
            logger.warning(f"parallel extraction failed ({e}); falling back to sequential")
            result = self._extract_with_export(profile_id, list_type, max_duration)

        # Fallback API externa se cobertura < 60%
        if target and len(result) < target * 0.6:
            logger.warning(
                f"parallel: only {len(result)}/{target} — trying httpx fallback"
            )
            try:
                try:
                    from instat.engines.httpx_engine import HttpxEngine
                except ImportError:
                    from engines.httpx_engine import HttpxEngine
                eng = HttpxEngine(timeout=self.timeout)
                driver = getattr(self._engine, '_driver', None)
                if driver is not None and eng.is_available:
                    eng.login_with_cookies(driver.get_cookies())
                    extra = eng.extract(profile_id, list_type,
                                        existing_profiles=set(result),
                                        max_duration=max_duration)
                    result = list(set(result) | set(extra))
                    eng.quit()
            except Exception as e:
                logger.warning(f"httpx fallback failed: {e}")

        if self._exporter is not None:
            try:
                self._exporter.export(result, {
                    'profile_id': profile_id, 'list_type': list_type,
                    'count': len(result), 'timestamp': time.time(),
                })
            except Exception as e:
                logger.debug(f"exporter failed: {e}")
        return result

    def get_both(self, profile_id: str,
                 max_duration: Optional[float] = None) -> Dict[str, List[str]]:
        """
        Extrai followers e following em paralelo.

        Estratégia:
        - Worker A: engine principal (Selenium) → followers
        - Worker B: HttpxEngine via cookies do driver Selenium → following
        - Se worker B falhar (403/rate limit), fallback: 2º SeleniumEngine pra following
        - Se também falhar: sequencial no engine principal

        Retorna {'followers': [...], 'following': [...]}.
        """
        from concurrent.futures import ThreadPoolExecutor

        def _do_followers():
            return self._extract_with_export(profile_id, 'followers', max_duration)

        def _do_following_httpx():
            try:
                from instat.engines.httpx_engine import HttpxEngine
            except ImportError:
                from engines.httpx_engine import HttpxEngine
            eng = HttpxEngine(timeout=self.timeout)
            if not eng.is_available:
                raise RuntimeError('httpx not installed')
            driver = getattr(self._engine, '_driver', None)
            if driver is None:
                raise RuntimeError('no selenium driver for cookie handoff')
            cookies = driver.get_cookies()
            eng.login_with_cookies(cookies)
            try:
                result = eng.extract(profile_id, 'following',
                                     max_duration=max_duration)
                return list(result)
            finally:
                eng.quit()

        def _do_following_selenium2():
            """Fallback: 2º SeleniumEngine em paralelo (sessão separada)."""
            eng = SeleniumEngine(headless=True, timeout=self.timeout,
                                 _login_class=InstaLogin)
            eng.login(self.username, self.password)
            try:
                return list(eng.extract(profile_id, 'following',
                                        max_duration=max_duration))
            finally:
                eng.quit()

        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_followers = ex.submit(_do_followers)
            fut_following = ex.submit(_do_following_httpx)

            followers = fut_followers.result()

            try:
                following = fut_following.result()
                logger.info(f"get_both: following via httpx ({len(following)} profiles)")
            except Exception as e:
                logger.warning(f"get_both: httpx following failed ({e}); trying 2nd Selenium")
                try:
                    following = _do_following_selenium2()
                    logger.info(f"get_both: following via 2nd Selenium ({len(following)} profiles)")
                except Exception as e2:
                    logger.warning(f"get_both: 2nd Selenium failed ({e2}); falling back to sequential")
                    following = self._extract_with_export(
                        profile_id, 'following', max_duration
                    )
        return {'followers': followers, 'following': following}

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        """Returns the total number of followers or following."""
        return self._engine_manager.get_total_count(profile_id, list_type)

    def _extract_with_export(self, profile_id: str, list_type: str,
                             max_duration: Optional[float]) -> List[str]:
        """Extrai e, se self._exporter configurado, exporta com metadata."""
        start_ts = time.time()
        start_perf = time.perf_counter()
        result = self._engine_manager.extract(
            profile_id, list_type, max_duration=max_duration
        )
        duration = time.perf_counter() - start_perf

        if self._exporter is not None:
            metadata = {
                'profile_id': profile_id,
                'list_type': list_type,
                'count': len(result),
                'timestamp': start_ts,
                'duration_seconds': duration,
            }
            try:
                self._exporter.export(result, metadata)
                logger.info(
                    f"Exporter {type(self._exporter).__name__} saved {len(result)} profiles"
                )
            except Exception as e:
                logger.exception(f"Exporter failed: {e}")
        return result

    def _extract_and_export_once(self, profile_id: str, list_type: str,
                                 max_duration: Optional[float],
                                 exporter: BaseExporter) -> List[str]:
        """Usa um exporter efêmero sem sobrescrever self._exporter."""
        start_ts = time.time()
        start_perf = time.perf_counter()
        result = self._engine_manager.extract(
            profile_id, list_type, max_duration=max_duration
        )
        duration = time.perf_counter() - start_perf
        metadata = {
            'profile_id': profile_id,
            'list_type': list_type,
            'count': len(result),
            'timestamp': start_ts,
            'duration_seconds': duration,
        }
        exporter.export(result, metadata)
        return result

    def to_csv(self, profile_id: str, list_type: str, path: str,
               max_duration: Optional[float] = None) -> List[str]:
        """Extrai e salva em CSV numa única chamada. Retorna a lista de perfis."""
        return self._extract_and_export_once(
            profile_id, list_type, max_duration, CSVExporter(path)
        )

    def to_json(self, profile_id: str, list_type: str, path: str,
                max_duration: Optional[float] = None, indent: int = 2) -> List[str]:
        """Extrai e salva em JSON numa única chamada. Retorna a lista de perfis."""
        return self._extract_and_export_once(
            profile_id, list_type, max_duration, JSONExporter(path, indent=indent)
        )

    def to_sqlite(self, profile_id: str, list_type: str, db_path: str,
                  table: str = 'profiles',
                  max_duration: Optional[float] = None) -> List[str]:
        """Extrai e salva em SQLite com dedup. Retorna a lista de perfis."""
        return self._extract_and_export_once(
            profile_id, list_type, max_duration,
            SQLiteExporter(db_path, table=table)
        )

    @staticmethod
    def parse_count_text(text: str) -> int:
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

    def quit(self) -> None:
        """Closes the underlying WebDriver instance."""
        self._engine_manager.quit_all()


if __name__ == '__main__':
    username = "your_username"
    password = "your_password"
    extractor = InstaExtractor(username, password, headless=False)
    try:
        followers = extractor.get_followers("tiagopsilv", max_duration=30.0)
        print("Followers:", followers)
        following = extractor.get_following("tiagopsilv")
        print("Following:", following)
    finally:
        extractor.quit()
