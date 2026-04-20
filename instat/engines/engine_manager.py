"""
Orquestrador de extração com fallbacks completos.
Integra engines, session pool, proxy pool, checkpoint e backoff.
"""
from typing import List, Optional, Set

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
                max_duration: Optional[float] = None, **kwargs) -> list:
        """
        Orquestrador completo de extração com fallbacks.

        Retorna list(profiles) parcial ou completo.
        Levanta AllEnginesBlockedError apenas se nada foi coletado.
        """
        checkpoint = ExtractionCheckpoint(profile_id, list_type)
        profiles: Set[str] = checkpoint.load() or set()
        if profiles:
            logger.info(f"EngineManager: resumed {len(profiles)} profiles from checkpoint")

        backoff = SmartBackoff()

        for engine in self.engines:
            sessions = self._get_sessions_iter()
            for session in sessions:
                result = self._try_engine_session(
                    engine, session, profile_id, list_type,
                    profiles, checkpoint, backoff,
                    max_duration=max_duration, **kwargs
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
        """
        if not hasattr(target_engine, 'login_with_cookies'):
            return False
        for other in self.engines:
            if other is target_engine:
                continue
            driver = getattr(other, '_driver', None)
            if driver is None:
                continue
            try:
                cookies = driver.get_cookies()
            except Exception as e:
                logger.debug(
                    f"cookie handoff: failed reading cookies from "
                    f"{other.name}: {e}"
                )
                continue
            if not cookies:
                continue
            try:
                target_engine.login_with_cookies(cookies)
                logger.info(
                    f"cookie handoff: {target_engine.name} got "
                    f"{len(cookies)} cookies from {other.name}"
                )
                return True
            except Exception as e:
                logger.warning(
                    f"cookie handoff from {other.name} to "
                    f"{target_engine.name} failed: {e}"
                )
                return False
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
                            max_duration=None, **kwargs):
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
