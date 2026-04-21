# Built-in
import re
import sys
import time
from typing import Dict, List, Optional

# Instagram username rules: 1-30 chars, ASCII letters/digits/dot/underscore.
# Prevents path traversal ('../admin'), URL query/fragment injection
# ('x?y=1', 'x#frag'), and unicode confusables from reaching
# f"https://www.instagram.com/{profile_id}/" construction.
_PROFILE_ID_RE = re.compile(r'^[A-Za-z0-9._]{1,30}$')


def _validate_profile_id(profile_id: str) -> None:
    if not isinstance(profile_id, str) or not _PROFILE_ID_RE.match(profile_id):
        raise ValueError(
            f"Invalid profile_id {profile_id!r}: must match "
            f"[A-Za-z0-9._]{{1,30}} (Instagram username rules)."
        )

# Third-party
from loguru import logger

# Configure Loguru logger for this module
logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    colorize=True,
    backtrace=True,
    diagnose=False,
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
                 exporter: Optional[BaseExporter] = None,
                 imap_config=None,
                 completion_threshold: Optional[float] = None) -> None:
        """
        engines: lista de nomes ['selenium', 'playwright', 'httpx']. Default: ['selenium'].
        exporter: exporter opcional chamado após cada extração bem-sucedida.
        imap_config: dict com host/user/password/port/... para resolver o
          challenge 'Check your email' do Instagram via IMAP automaticamente.
        completion_threshold: fração mínima (0..1) da contagem esperada que
          o engine aceita como sucesso. Abaixo levanta BlockedError para a
          cascata tentar o próximo engine. Default None mantém o default do
          engine (SeleniumEngine usa 0.90).

          IMPORTANTE: valores baixos (< 0.5) efetivamente DESABILITAM
          a cascata multi-engine — o primeiro engine vai considerar
          qualquer coverage acima do threshold como sucesso e nunca
          passará a bola para o próximo. Um warning é emitido quando
          essa combinação é detectada. Para coletar "o que der", use
          `completion_threshold=None` (default) + `get_*_until_complete`
          ao invés disso — o wrapper acumula parciais entre retries.
        """
        self.username = username
        self.password = password
        self.timeout = timeout
        self._exporter = exporter
        self._imap_config = imap_config
        # Stored for `get_*_with_rotation` to instantiate new extractors
        # with consistent configuration when rotating through fallback
        # accounts. Not part of the public API.
        self._headless = headless
        self._engine_names = list(engines or ['selenium'])
        if completion_threshold is not None and not (0 < completion_threshold <= 1):
            raise ValueError(
                f"completion_threshold must be in (0, 1], got {completion_threshold}"
            )
        self._completion_threshold_override = completion_threshold

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

        # Propaga imap_config para engines Selenium (único capaz de resolver
        # challenge via browser).
        if imap_config is not None:
            for eng in engine_instances:
                if hasattr(eng, '_imap_config'):
                    eng._imap_config = imap_config

        # Propaga completion_threshold se o usuário passou.
        if self._completion_threshold_override is not None:
            for eng in engine_instances:
                if hasattr(eng, 'completion_threshold'):
                    eng.completion_threshold = self._completion_threshold_override
            if (len(engine_instances) > 1
                    and self._completion_threshold_override < 0.5):
                logger.warning(
                    f"completion_threshold={self._completion_threshold_override} "
                    "< 0.5 combined with multi-engine cascade "
                    f"{[e.name for e in engine_instances]}: the first engine "
                    "will accept low coverage as success and the cascade "
                    "will not trigger. Use get_*_until_complete for "
                    "best-effort partial collection."
                )

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
        _validate_profile_id(profile_id)
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
        """Returns a list of followers for the given profile id.

        max_duration: segundos de budget via `time.perf_counter`. No
          Windows, isso inclui tempo em que o SO dormiu — rodar uma
          extração através de um ciclo sleep/wake do notebook dispara
          max_duration imediatamente ao acordar. Evite rodar
          overnight em laptops sem inibir o sono.
        """
        _validate_profile_id(profile_id)
        return self._extract_with_export(profile_id, 'followers', max_duration)

    def get_following(self, profile_id: str, max_duration: Optional[float] = None) -> List[str]:
        """Returns a list of accounts that the given profile id is following.

        max_duration: ver nota em `get_followers`.
        """
        _validate_profile_id(profile_id)
        return self._extract_with_export(profile_id, 'following', max_duration)

    def _extract_until_complete(self, profile_id: str, list_type: str,
                                target_fraction: float, max_retries: int,
                                retry_wait_s: float,
                                max_duration: Optional[float]) -> List[str]:
        """Loop de retry com acumulador cross-iteration.

        Mantém a união de perfis coletados em TODAS as iterações em
        `accumulated`. Cada call de `_extract_with_export` pode retornar
        menos que antes (rate-limit, modal quebrado, navegador travado)
        — o acumulador garante que o resultado final nunca regride.

        Para quando: cobertura acumulada >= target, iterações esgotadas,
        ou uma iteração não adiciona nenhum perfil novo ao acumulado.

        NOTA: `EngineManager.checkpoint.clear()` dispara em qualquer
        retorno não-None, então `engine_manager.extract` não retoma
        naturalmente entre iterações. O acumulador aqui é justamente
        o contorno dessa tensão de semântica.
        """
        if not 0 < target_fraction <= 1:
            raise ValueError(
                f"target_fraction must be in (0, 1], got {target_fraction}"
            )
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries}")

        total = None
        try:
            total = self._engine_manager.get_total_count(profile_id, list_type)
        except Exception as e:
            logger.debug(f"until_complete: get_total_count failed: {e}")

        target = int((total or 0) * target_fraction)
        accumulated: set = set()
        # P4: skip engine that has rate-limited in 2+ consecutive
        # iterations. Saves wall-clock (~1-2s per rate-limited engine
        # per iteration) and avoids reinforcing IG's pattern detection.
        rate_limit_streak: Dict[str, int] = {}
        excluded_engines: set = set()
        STREAK_BEFORE_EXCLUDE = 2
        for attempt in range(max_retries + 1):
            if attempt > 0:
                logger.info(
                    f"until_complete: retry {attempt}/{max_retries} after "
                    f"{retry_wait_s}s (have {len(accumulated)}"
                    f"{' /' + str(total) if total else ''})"
                )
                time.sleep(retry_wait_s)
            raised = False
            rate_limited_this_iter: List[str] = []
            try:
                iteration = self._extract_with_export(
                    profile_id, list_type, max_duration,
                    _exclude_engines=excluded_engines or None,
                    _rate_limit_sink=rate_limited_this_iter,
                )
            except Exception as e:
                logger.warning(
                    f"until_complete: attempt {attempt} raised "
                    f"{type(e).__name__}: {e} — keeping accumulated "
                    f"({len(accumulated)})"
                )
                iteration = []
                raised = True
            before = len(accumulated)
            accumulated.update(iteration)
            after = len(accumulated)
            # Update rate-limit streak. Engines that did NOT rate-limit
            # this iteration reset their streak. Newly-offending engines
            # get incremented. At STREAK_BEFORE_EXCLUDE, exclude.
            limited_set = set(rate_limited_this_iter)
            for name in list(rate_limit_streak.keys()):
                if name not in limited_set:
                    del rate_limit_streak[name]
            for name in limited_set:
                rate_limit_streak[name] = rate_limit_streak.get(name, 0) + 1
                if (rate_limit_streak[name] >= STREAK_BEFORE_EXCLUDE
                        and name not in excluded_engines):
                    excluded_engines.add(name)
                    logger.info(
                        f"until_complete: excluding '{name}' from further "
                        f"retries — rate-limited "
                        f"{rate_limit_streak[name]}× in a row"
                    )
            if total and after >= target:
                logger.info(
                    f"until_complete: hit target {after}/{total} "
                    f"(>= {target_fraction:.0%})"
                )
                return sorted(accumulated)
            # Stagnation check: só conta quando a iteração executou
            # completamente (sem raise) e ainda assim não trouxe nada novo.
            if attempt > 0 and not raised and after == before:
                logger.info(
                    f"until_complete: no new profiles after retry (stuck at "
                    f"{after}) — stopping to avoid burning rate-limit"
                )
                self._log_account_blocked_diagnostic(
                    list_type, after, total, attempt + 1,
                )
                return sorted(accumulated)
        self._log_account_blocked_diagnostic(
            list_type, len(accumulated), total, max_retries + 1,
        )
        return sorted(accumulated)

    def _log_account_blocked_diagnostic(
        self, list_type: str, collected: int, total: Optional[int],
        attempts: int,
    ) -> None:
        """Emit actionable advice when coverage is catastrophically low.

        Triggered only when we have a total and collected < 1% of it.
        The message guides the user toward the only realistic fixes:
        different account, proxy pool, or long cooldown.
        """
        if not total or collected >= max(1, int(total * 0.01)):
            return
        engines = [e.name for e in self._engine_manager.engines]
        pct = (collected / total) if total else 0
        logger.warning(
            f"until_complete: {list_type} coverage {collected}/{total} "
            f"({100*pct:.2f}%) after {attempts} attempt(s) across "
            f"engines {engines}. This pattern usually means the "
            "Instagram account is shadow-rate-limited for this specific "
            "target. Consider: (a) different IG account, (b) add proxies, "
            "(c) cool down for 6+ hours before retrying."
        )

    def get_followers_until_complete(
        self, profile_id: str, *,
        target_fraction: float = 0.95,
        max_retries: int = 3,
        retry_wait_s: float = 90.0,
        max_duration: Optional[float] = None,
    ) -> List[str]:
        """Extrai followers com retries auto-resume até atingir
        target_fraction da contagem total, estagnar, ou esgotar retries."""
        _validate_profile_id(profile_id)
        return self._extract_until_complete(
            profile_id, 'followers',
            target_fraction, max_retries, retry_wait_s, max_duration,
        )

    def get_following_until_complete(
        self, profile_id: str, *,
        target_fraction: float = 0.95,
        max_retries: int = 3,
        retry_wait_s: float = 90.0,
        max_duration: Optional[float] = None,
    ) -> List[str]:
        """Extrai following com retries auto-resume até atingir
        target_fraction da contagem total, estagnar, ou esgotar retries."""
        _validate_profile_id(profile_id)
        return self._extract_until_complete(
            profile_id, 'following',
            target_fraction, max_retries, retry_wait_s, max_duration,
        )

    # ---------------- Rotation (account/proxy fallback) ----------------

    @staticmethod
    def _validate_fallback_accounts(
        fallback_accounts: Optional[List[Dict[str, str]]],
    ) -> List[Dict[str, str]]:
        """Fail-fast validation. No silent drops."""
        if not fallback_accounts:
            return []
        out = []
        for i, acc in enumerate(fallback_accounts):
            if not isinstance(acc, dict):
                raise ValueError(
                    f"fallback_accounts[{i}]: expected dict, got {type(acc).__name__}"
                )
            u = acc.get('username')
            p = acc.get('password')
            if not (isinstance(u, str) and u and isinstance(p, str) and p):
                raise ValueError(
                    f"fallback_accounts[{i}]: requires non-empty 'username' "
                    "and 'password' strings"
                )
            out.append({'username': u, 'password': p})
        return out

    def _extract_with_rotation(
        self, profile_id: str, list_type: str,
        fallback_accounts: Optional[List[Dict[str, str]]],
        fallback_proxies: Optional[List[str]],
        target_fraction: float,
        max_retries_per_account: int,
        retry_wait_s: float,
        max_duration: Optional[float],
    ) -> List[str]:
        _validate_profile_id(profile_id)
        fallbacks = self._validate_fallback_accounts(fallback_accounts)
        proxies_list = list(fallback_proxies or [])
        if not 0 < target_fraction <= 1:
            raise ValueError(
                f"target_fraction must be in (0, 1], got {target_fraction}"
            )

        accumulated: set = set()

        # Phase 1: current account (self)
        first = self._extract_until_complete(
            profile_id, list_type,
            target_fraction, max_retries_per_account,
            retry_wait_s, max_duration,
        )
        accumulated.update(first)

        total: Optional[int] = None
        try:
            total = self._engine_manager.get_total_count(profile_id, list_type)
        except Exception as e:
            logger.debug(f"rotation: get_total_count failed: {e}")

        # Precompute so mypy sees a concrete int target (or None = never hit).
        target_count: Optional[int] = (
            int(total * target_fraction) if total else None
        )

        def _target_hit() -> bool:
            return target_count is not None and len(accumulated) >= target_count

        if _target_hit() or not fallbacks:
            return sorted(accumulated)

        # Phase 2: rotate through fallback accounts. Each one gets its
        # own InstaExtractor (fresh login, fresh driver, fresh session).
        # Proxy pairing is positional and optional.
        for idx, acc in enumerate(fallbacks):
            proxy = proxies_list[idx] if idx < len(proxies_list) else None
            logger.info(
                f"rotation: trying fallback account #{idx + 1}/"
                f"{len(fallbacks)} '{acc['username']}' "
                f"{'with proxy ' + proxy if proxy else 'no proxy'} "
                f"(have {len(accumulated)}"
                f"{' /' + str(total) if total else ''})"
            )
            try:
                alt = self._build_rotation_extractor(
                    acc['username'], acc['password'], proxy,
                )
            except Exception as e:
                logger.warning(
                    f"rotation: account '{acc['username']}' setup failed: "
                    f"{type(e).__name__}: {e} — skipping"
                )
                continue
            try:
                new_profiles = alt._extract_until_complete(
                    profile_id, list_type,
                    target_fraction, max_retries_per_account,
                    retry_wait_s, max_duration,
                )
                accumulated.update(new_profiles)
                if _target_hit():
                    logger.info(
                        f"rotation: target reached via '{acc['username']}' "
                        f"({len(accumulated)}/{total})"
                    )
                    return sorted(accumulated)
            except Exception as e:
                logger.warning(
                    f"rotation: '{acc['username']}' extraction failed: "
                    f"{type(e).__name__}: {e}"
                )
            finally:
                try:
                    alt.quit()
                except Exception:
                    pass

        if total and len(accumulated) < int(total * target_fraction):
            logger.warning(
                f"rotation: exhausted {len(fallbacks) + 1} account(s), "
                f"final coverage {len(accumulated)}/{total} "
                f"({100 * len(accumulated) / total:.1f}%). "
                "Consider more accounts or longer cooldown."
            )
        return sorted(accumulated)

    def _build_rotation_extractor(
        self, username: str, password: str, proxy: Optional[str],
    ) -> "InstaExtractor":
        """Factory for fresh extractors during rotation.

        Inherits imap_config, engines, completion_threshold, timeout and
        headless from `self`. Each call performs a full login (may
        trigger IMAP challenge). Caller is responsible for .quit().
        """
        return InstaExtractor(
            username=username, password=password,
            headless=self._headless,
            timeout=self.timeout,
            proxies=[proxy] if proxy else None,
            engines=list(self._engine_names),
            imap_config=self._imap_config,
            completion_threshold=self._completion_threshold_override,
        )

    def get_followers_with_rotation(
        self, profile_id: str, *,
        fallback_accounts: Optional[List[Dict[str, str]]] = None,
        fallback_proxies: Optional[List[str]] = None,
        target_fraction: float = 0.90,
        max_retries_per_account: int = 2,
        retry_wait_s: float = 60.0,
        max_duration: Optional[float] = None,
    ) -> List[str]:
        """Extrai followers com rotação serial por contas/proxies.

        Roda o extractor atual via `get_followers_until_complete`. Se a
        cobertura final ficar abaixo de `target_fraction` (shadow-rate-
        limit padrão), repete com cada `fallback_accounts` em sequência
        — cada uma em um InstaExtractor novo (novo login). Retorna a
        união acumulada entre todos.

        fallback_accounts: lista de {'username', 'password'} a tentar
          após self. Passwords ficam apenas em memória. Segurança
          idêntica à kwarg `accounts` do construtor.
        fallback_proxies: pareamento posicional com fallback_accounts.
          Menos proxies que contas = resto das contas sem proxy.
        """
        return self._extract_with_rotation(
            profile_id, 'followers',
            fallback_accounts, fallback_proxies,
            target_fraction, max_retries_per_account,
            retry_wait_s, max_duration,
        )

    def get_following_with_rotation(
        self, profile_id: str, *,
        fallback_accounts: Optional[List[Dict[str, str]]] = None,
        fallback_proxies: Optional[List[str]] = None,
        target_fraction: float = 0.90,
        max_retries_per_account: int = 2,
        retry_wait_s: float = 60.0,
        max_duration: Optional[float] = None,
    ) -> List[str]:
        """Idem `get_followers_with_rotation` para following."""
        return self._extract_with_rotation(
            profile_id, 'following',
            fallback_accounts, fallback_proxies,
            target_fraction, max_retries_per_account,
            retry_wait_s, max_duration,
        )

    # ---------- Persistent multi-run accrual (large targets) ----------

    def _extract_persistent(
        self, profile_id: str, list_type: str,
        store_path: str,
        fallback_accounts: Optional[List[Dict[str, str]]],
        fallback_proxies: Optional[List[str]],
        max_retries_per_account: int,
        retry_wait_s: float,
        max_duration: Optional[float],
    ) -> Dict[str, object]:
        _validate_profile_id(profile_id)
        try:
            from instat.persistent_store import PersistentStore
        except ImportError:
            from persistent_store import PersistentStore  # type: ignore

        store = PersistentStore(store_path)
        before_count = store.count(profile_id, list_type)
        run_started_at = time.time()

        # Decide which extract flavor to run: with_rotation if fallbacks
        # given, else until_complete.
        if fallback_accounts:
            this_run = self._extract_with_rotation(
                profile_id, list_type,
                fallback_accounts, fallback_proxies,
                target_fraction=1.0,   # persistent mode never wants
                                       # to short-circuit on target —
                                       # we take whatever we can.
                max_retries_per_account=max_retries_per_account,
                retry_wait_s=retry_wait_s,
                max_duration=max_duration,
            )
            # rotation itself reuses self.username, then fallbacks.
            # We record self's contribution under self.username and
            # fallbacks' contributions under their own names — but
            # we only see the union here, no per-account split.
            # Simpler: tag everything as 'rotation:<primary>'.
            store.add_batch(
                profile_id, list_type, this_run,
                source_account=f"rotation:{self.username}",
            )
        else:
            this_run = self._extract_until_complete(
                profile_id, list_type,
                target_fraction=1.0,
                max_retries=max_retries_per_account,
                retry_wait_s=retry_wait_s,
                max_duration=max_duration,
            )
            store.add_batch(
                profile_id, list_type, this_run,
                source_account=self.username,
            )

        after_count = store.count(profile_id, list_type)
        delta = after_count - before_count
        delta_list = store.get_delta_since(
            profile_id, list_type, run_started_at,
        )

        total_target = None
        try:
            total_target = self._engine_manager.get_total_count(
                profile_id, list_type,
            )
        except Exception:
            pass

        stats = store.stats(profile_id, list_type)
        pct = None
        if total_target:
            pct = 100 * after_count / total_target
            logger.info(
                f"persistent[{list_type}]: run added {delta} new "
                f"(this call collected {len(this_run)}). "
                f"Store now {after_count} / target {total_target} "
                f"({pct:.2f}%). Sources: {stats['source_accounts']}"
            )
        else:
            logger.info(
                f"persistent[{list_type}]: run added {delta} new "
                f"(this call collected {len(this_run)}). "
                f"Store now {after_count}. "
                f"Sources: {stats['source_accounts']}"
            )

        return {
            'store_path': store.path,
            'store_total': after_count,
            'store_total_before_run': before_count,
            'this_run_collected': len(this_run),
            'this_run_new': delta,
            'this_run_new_usernames': delta_list,
            'target_total': total_target,
            'target_coverage_pct': pct,
            'source_accounts': stats['source_accounts'],
        }

    def get_followers_persistent(
        self, profile_id: str, *,
        store_path: str,
        fallback_accounts: Optional[List[Dict[str, str]]] = None,
        fallback_proxies: Optional[List[str]] = None,
        max_retries_per_account: int = 2,
        retry_wait_s: float = 60.0,
        max_duration: Optional[float] = None,
    ) -> Dict[str, object]:
        """Extrai followers acumulando em SQLite persistente entre runs.

        Uso típico: schedule uma chamada por dia (cron / GitHub Actions
        / Airflow). Cada chamada adiciona o que conseguir ao store,
        sem expiry. Alvos de 850k tornam-se viáveis ao longo de semanas
        sem queimar contas.

        store_path: arquivo SQLite. Será criado com chmod 0600 no 1º run.
        Outros kwargs: repassados para until_complete / with_rotation.

        Retorna dict com métricas da run + totais acumulados.
        """
        return self._extract_persistent(
            profile_id, 'followers', store_path,
            fallback_accounts, fallback_proxies,
            max_retries_per_account, retry_wait_s, max_duration,
        )

    def get_following_persistent(
        self, profile_id: str, *,
        store_path: str,
        fallback_accounts: Optional[List[Dict[str, str]]] = None,
        fallback_proxies: Optional[List[str]] = None,
        max_retries_per_account: int = 2,
        retry_wait_s: float = 60.0,
        max_duration: Optional[float] = None,
    ) -> Dict[str, object]:
        """Idem `get_followers_persistent` para following."""
        return self._extract_persistent(
            profile_id, 'following', store_path,
            fallback_accounts, fallback_proxies,
            max_retries_per_account, retry_wait_s, max_duration,
        )

    def get_followers_parallel(self, profile_id: str,
                               workers: int = 2,
                               accounts: Optional[List[Dict[str, str]]] = None,
                               stop_threshold: float = 0.98,
                               max_duration: Optional[float] = None,
                               headless: bool = True) -> List[str]:
        """Extrai followers com N SeleniumEngines em paralelo (união)."""
        _validate_profile_id(profile_id)
        return self._parallel(profile_id, 'followers', workers, accounts,
                              stop_threshold, max_duration, headless)

    def get_following_parallel(self, profile_id: str,
                               workers: int = 2,
                               accounts: Optional[List[Dict[str, str]]] = None,
                               stop_threshold: float = 0.98,
                               max_duration: Optional[float] = None,
                               headless: bool = True) -> List[str]:
        """Extrai following com N SeleniumEngines em paralelo (união)."""
        _validate_profile_id(profile_id)
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
        _validate_profile_id(profile_id)
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
        _validate_profile_id(profile_id)
        return self._engine_manager.get_total_count(profile_id, list_type)

    def _extract_with_export(self, profile_id: str, list_type: str,
                             max_duration: Optional[float],
                             _exclude_engines=None,
                             _rate_limit_sink=None) -> List[str]:
        """Extrai e, se self._exporter configurado, exporta com metadata.

        _exclude_engines / _rate_limit_sink: kwargs internos usados por
        `_extract_until_complete` para pular engines que deram
        rate-limit repetido e observar quais engines continuam dando
        rate-limit nesta iteração. Underscore-prefixed porque não
        são API pública — não documentar fora daqui.
        """
        start_ts = time.time()
        start_perf = time.perf_counter()
        extract_kwargs = {}
        if _exclude_engines:
            extract_kwargs['exclude_engines'] = _exclude_engines
        if _rate_limit_sink is not None:
            extract_kwargs['rate_limit_sink'] = _rate_limit_sink
        result = self._engine_manager.extract(
            profile_id, list_type, max_duration=max_duration,
            **extract_kwargs,
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
