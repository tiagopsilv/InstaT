"""
Orquestrador de extração com fallbacks completos.
Integra engines, session pool, proxy pool, checkpoint e backoff.
"""
from typing import Iterable, List, Optional, Set

from loguru import logger

try:
    from instat.backoff import SmartBackoff
    from instat.checkpoint import ExtractionCheckpoint
    from instat.engines.base import BaseEngine
    from instat.exceptions import AccountBlockedError, AllEnginesBlockedError, BlockedError, RateLimitError
    from instat.proxy import ProxyPool
    from instat.session_pool import SessionPool
except ImportError:
    from backoff import SmartBackoff
    from checkpoint import ExtractionCheckpoint
    from engines.base import BaseEngine
    from exceptions import AccountBlockedError, AllEnginesBlockedError, BlockedError, RateLimitError
    from proxy import ProxyPool
    from session_pool import SessionPool


class EngineManager:
    """
    Orquestrador de engines com fallbacks em cascata.
    - Checkpoint integrado no nível orquestrador (atravessa engines/sessions)
    - Iteração de sessions com re-login em RateLimit/AccountBlocked
    - Backoff exponencial entre falhas
    - Retorna progresso parcial sempre que houver — levanta AllEnginesBlockedError
      apenas quando nada foi coletado.
    """

    def __init__(self, engines: List[BaseEngine],
                 proxy_pool: Optional[ProxyPool] = None,
                 session_pool: Optional[SessionPool] = None,
                 default_credentials: Optional[tuple] = None):
        """
        default_credentials: (username, password) usado para login sob demanda
        em engines secundárias quando não há session_pool. Permite cascata
        Selenium→HttpxEngine sem exigir SessionPool formal.
        """
        self.engines = [e for e in engines if e.is_available]
        if not self.engines:
            raise RuntimeError('No extraction engines available')
        self._proxy_pool = proxy_pool
        self._session_pool = session_pool
        self._default_credentials = default_credentials
        self._logged_in_engines: set[int] = set()  # track which engines already logged in

        pool_info_parts = []
        if proxy_pool:
            pool_info_parts.append(f"proxy_pool={proxy_pool.total_count} proxies")
        if session_pool:
            pool_info_parts.append(f"session_pool={session_pool.total_count} accounts")
        pool_info = ", " + ", ".join(pool_info_parts) if pool_info_parts else ""

        logger.info(f"EngineManager initialized with {len(self.engines)} engines: "
                    f"{[e.name for e in self.engines]}{pool_info}")

    def extract(self, profile_id: str, list_type: str,
                max_duration: Optional[float] = None,
                exclude_engines: Optional[Iterable[str]] = None,
                rate_limit_sink: Optional[List[str]] = None,
                **kwargs) -> list:
        """
        Orquestrador completo de extração com fallbacks.

        Retorna list(profiles) parcial ou completo.
        Levanta AllEnginesBlockedError apenas se nada foi coletado.

        exclude_engines: iterable de nomes de engine a pular nesta
          chamada. Útil para `until_complete` ignorar engines que deram
          rate-limit consistente em iterações anteriores.
        rate_limit_sink: se fornecido, recebe via append() o nome de
          cada engine que levantar RateLimitError durante esta chamada.
          Permite ao chamador detectar padrões persistentes sem precisar
          parsear logs.
        """
        checkpoint = ExtractionCheckpoint(profile_id, list_type)
        profiles: Set[str] = checkpoint.load() or set()
        if profiles:
            logger.info(f"EngineManager: resumed {len(profiles)} profiles from checkpoint")

        backoff = SmartBackoff()
        excluded = set(exclude_engines or ())

        for engine in self.engines:
            if engine.name in excluded:
                logger.info(
                    f"EngineManager: skipping {engine.name} "
                    "(caller-excluded for this run)"
                )
                continue
            sessions = self._get_sessions_iter()
            for session in sessions:
                result = self._try_engine_session(
                    engine, session, profile_id, list_type,
                    profiles, checkpoint, backoff,
                    max_duration=max_duration,
                    rate_limit_sink=rate_limit_sink,
                    **kwargs
                )
                if result is not None:
                    checkpoint.clear()
                    backoff.reset()
                    return list(result)

        # Todas tentativas falharam
        if profiles:
            logger.warning(
                f"EngineManager: all engines/sessions exhausted. "
                f"Returning partial result with {len(profiles)} profiles."
            )
            return list(profiles)

        raise AllEnginesBlockedError(
            f"All engines blocked for {profile_id}/{list_type} with zero profiles collected"
        )

    def _try_cookie_handoff(self, target_engine) -> bool:
        """Inject cookies from an already-logged-in Selenium engine into
        `target_engine` via `login_with_cookies`.

        Returns True on success. False if no handoff path exists or the
        target engine raised — caller then falls back to regular login.

        Motivation: HttpxEngine form-login is frequently blocked by IG
        from burned IPs (403). When a Selenium engine in the same
        cascade has a live driver with valid cookies, we can reuse them
        verbatim — bypasses the form-login entirely and picks up where
        the browser session left off.

        Emits detailed debug logs at every early-return path — essential
        because a silent False here means the fallback login runs, which
        in production masked the fact that handoff was never actually
        firing (validated in a live run against a burned account).
        """
        target_name = getattr(target_engine, 'name', type(target_engine).__name__)
        if not hasattr(target_engine, 'login_with_cookies'):
            logger.debug(
                f"cookie handoff[{target_name}]: skipped — no "
                f"login_with_cookies method"
            )
            return False
        tried_any = False
        for other in self.engines:
            if other is target_engine:
                continue
            other_name = getattr(other, 'name', type(other).__name__)
            driver = getattr(other, '_driver', None)
            if driver is None:
                logger.debug(
                    f"cookie handoff[{target_name}]: source {other_name} "
                    f"has no live _driver"
                )
                continue
            tried_any = True
            try:
                cookies = driver.get_cookies()
            except Exception as e:
                logger.debug(
                    f"cookie handoff[{target_name}]: failed reading "
                    f"cookies from {other_name}: {e}"
                )
                continue
            if not cookies:
                # Selenium's get_cookies() is scoped to the current document.
                # _reset_page_state() navigates to about:blank after a failed
                # extraction, which makes IG cookies invisible. Navigate back
                # to the IG domain to bring them into scope.
                current = ""
                try:
                    current = driver.current_url or ""
                except Exception:
                    pass
                if 'instagram.com' not in current:
                    try:
                        logger.debug(
                            f"cookie handoff[{target_name}]: {other_name} "
                            f"is at {current[:60]!r}, re-navigating to IG "
                            "to expose cookies"
                        )
                        driver.get("https://www.instagram.com/")
                        cookies = driver.get_cookies()
                    except Exception as e:
                        logger.debug(
                            f"cookie handoff[{target_name}]: re-nav failed: {e}"
                        )
                if not cookies:
                    logger.debug(
                        f"cookie handoff[{target_name}]: {other_name} "
                        f"returned empty cookies"
                    )
                    continue
            try:
                target_engine.login_with_cookies(cookies)
                logger.info(
                    f"cookie handoff[{target_name}]: got {len(cookies)} "
                    f"cookies from {other_name}"
                )
                return True
            except Exception as e:
                logger.warning(
                    f"cookie handoff[{target_name}] from {other_name} "
                    f"failed: {type(e).__name__}: {e}"
                )
                return False
        if not tried_any:
            logger.debug(
                f"cookie handoff[{target_name}]: no peer engine had a "
                f"live _driver (checked {len(self.engines) - 1} peer(s))"
            )
        return False

    def _get_sessions_iter(self):
        """Retorna lista de Sessions disponíveis, ou [None] se sem session_pool."""
        if self._session_pool is None:
            return [None]
        available = self._session_pool.available_sessions()
        if not available:
            logger.warning("EngineManager: all sessions in cooldown")
            return []
        return available

    def _try_engine_session(self, engine, session, profile_id, list_type,
                            profiles: Set[str], checkpoint, backoff,
                            max_duration=None,
                            rate_limit_sink: Optional[List[str]] = None,
                            **kwargs):
        """
        Tenta 1 (engine, session) pair. Retorna profiles (set) em sucesso, None em falha.
        Atualiza `profiles` in-place em sucesso.
        """
        # Re-login se session fornecida
        if session is not None:
            try:
                logger.info(f"{engine.name}: logging in as {session.username}")
                engine.login(session.username, session.password,
                             proxy=session.proxy)
            except (AccountBlockedError, BlockedError) as e:
                logger.warning(f"{engine.name}: login blocked for {session.username}: {e}")
                if self._session_pool is not None:
                    self._session_pool.mark_blocked(
                        session, self._cooldown_for_error(e)
                    )
                backoff.wait()
                return None
            except Exception as e:
                logger.exception(f"{engine.name}: login failed unexpectedly: {e}")
                return None
        # Login sob demanda com credenciais default (cascata Selenium→httpx):
        # a primary engine já fez login no InstaExtractor.__init__; secundárias
        # aparecem aqui sem login. Se temos credenciais, tenta login
        # (HttpxEngine/PlaywrightEngine tentam SessionCache primeiro, então é fast path).
        elif (self._default_credentials is not None
              and id(engine) not in self._logged_in_engines):
            username, password = self._default_credentials
            # Fast path: se o engine aceita cookies de outro engine já logado
            # (ex.: httpx recebendo cookies do Selenium), injetar direto.
            # Evita um form-login extra que o IG frequentemente bloqueia em
            # httpx e preserva a sessão quente do Selenium.
            if self._try_cookie_handoff(engine):
                self._logged_in_engines.add(id(engine))
            else:
                try:
                    logger.info(f"{engine.name}: on-demand login (cascata)")
                    engine.login(username, password)
                    self._logged_in_engines.add(id(engine))
                except (AccountBlockedError, BlockedError) as e:
                    logger.warning(f"{engine.name}: on-demand login blocked: {e}")
                    return None
                except Exception as e:
                    logger.warning(f"{engine.name}: on-demand login failed: {e}")
                    return None

        # on_batch: atualiza set do orquestrador + salva checkpoint.
        # Importante: update in-place de `profiles` (set mutável) para
        # que BlockedError subsequente ainda preserve os dados via
        # `if profiles` no extract() → retorna parcial.
        def on_batch(batch):
            try:
                profiles.update(batch)
                checkpoint.save(profiles)
            except Exception as e:
                logger.debug(f"checkpoint.save failed in on_batch: {e}")

        try:
            logger.info(f"Trying engine: {engine.name}")
            new = engine.extract(
                profile_id, list_type,
                existing_profiles=profiles,
                max_duration=max_duration,
                on_batch=on_batch,
            )
            if new is not None:
                profiles |= set(new)
            return profiles
        except RateLimitError as e:
            logger.warning(f"{engine.name}: rate limited: {e}")
            checkpoint.save(profiles)
            if session is not None and self._session_pool is not None:
                self._session_pool.mark_blocked(session, SessionPool.DEFAULT_COOLDOWN)
            if rate_limit_sink is not None:
                rate_limit_sink.append(engine.name)
            backoff.wait()
            return None
        except AccountBlockedError as e:
            reason = getattr(e, 'reason', 'account blocked')
            logger.warning(f"{engine.name}: account blocked: {reason}")
            checkpoint.save(profiles)
            if session is not None and self._session_pool is not None:
                self._session_pool.mark_blocked(
                    session, SessionPool.META_INTERSTITIAL_COOLDOWN
                )
            backoff.wait()
            return None
        except BlockedError as e:
            logger.warning(f"{engine.name} blocked: {e}")
            checkpoint.save(profiles)
            return None
        except Exception as e:
            logger.exception(f"{engine.name}: unexpected error: {e}")
            checkpoint.save(profiles)
            return None

    @staticmethod
    def _cooldown_for_error(exc):
        """Retorna cooldown apropriado baseado no tipo de exceção."""
        if isinstance(exc, AccountBlockedError):
            return SessionPool.META_INTERSTITIAL_COOLDOWN
        return SessionPool.DEFAULT_COOLDOWN

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        """Tenta obter contagem com cada engine."""
        for engine in self.engines:
            try:
                return engine.get_total_count(profile_id, list_type)
            except BlockedError:
                logger.warning(f"{engine.name} blocked on get_total_count")
                continue
        return None

    def quit_all(self) -> None:
        """Fecha todas as engines."""
        for engine in self.engines:
            try:
                engine.quit()
            except Exception as e:
                logger.warning(f"Error quitting {engine.name}: {e}")
