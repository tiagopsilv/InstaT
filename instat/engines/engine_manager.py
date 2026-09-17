"""
Orquestrador de extração com fallbacks completos.
Integra engines, session pool, proxy pool, checkpoint e backoff.
"""
from typing import Callable, Iterable, List, Optional, Set

from loguru import logger

try:
    from instat.backoff import SmartBackoff
    from instat.checkpoint import ExtractionCheckpoint
    from instat.engines.base import BaseEngine
    from instat.exceptions import (
        AccountBlockedError,
        AllEnginesBlockedError,
        BlockedError,
        ExtractionStoppedError,
        RateLimitError,
    )
    from instat.governor import Governor, GovernorStop, Signal
    from instat.proxy import ProxyPool
    from instat.session_pool import SessionPool
except ImportError:
    from backoff import SmartBackoff
    from checkpoint import ExtractionCheckpoint
    from engines.base import BaseEngine
    from exceptions import (  # type: ignore
        AccountBlockedError,
        AllEnginesBlockedError,
        BlockedError,
        ExtractionStoppedError,
        RateLimitError,
    )
    from governor import Governor, GovernorStop, Signal  # type: ignore
    from proxy import ProxyPool
    from session_pool import SessionPool


class EngineManager:
    """
    Orquestrador de engines com fallbacks em cascata.
    - Checkpoint integrado no nível orquestrador (atravessa engines/sessions)
    - Toda tentativa passa pelo Governor (roadmap §5.6): transitório repete
      com teto; erro técnico cai para o próximo engine com a mesma conta;
      429, proxy, challenge, restrição, orçamento e cancelamento PARAM a
      cascata inteira — nunca trocam de conta para insistir.
    - Retorna progresso parcial sempre que houver — levanta
      ExtractionStoppedError/AllEnginesBlockedError apenas quando nada foi
      coletado. `last_stop` guarda o motivo terminal.
    """

    def __init__(self, engines: List[BaseEngine],
                 proxy_pool: Optional[ProxyPool] = None,
                 session_pool: Optional[SessionPool] = None,
                 default_credentials: Optional[tuple] = None,
                 governor: Optional[Governor] = None):
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
        self.governor = governor or Governor()
        self.last_stop: Optional[GovernorStop] = None

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
                should_stop: Optional["Callable[[], bool]"] = None,
                with_metadata: bool = False,
                metrics_sink: Optional[dict] = None,
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
        self.last_stop = None
        begun: set = set()

        for engine in self.engines:
            if self.last_stop is not None:
                break
            if engine.name in excluded:
                logger.info(
                    f"EngineManager: skipping {engine.name} "
                    "(caller-excluded for this run)"
                )
                continue
            sessions = self._get_sessions_iter()
            for session in sessions:
                key = self._governor_key(session, list_type)
                if key not in begun:
                    self.governor.begin(key)
                    begun.add(key)
                try:
                    result = self._try_engine_session(
                        engine, session, profile_id, list_type,
                        profiles, checkpoint, backoff,
                        max_duration=max_duration,
                        rate_limit_sink=rate_limit_sink,
                        should_stop=should_stop,
                        with_metadata=with_metadata,
                        _governor_key=key,
                        **kwargs
                    )
                except GovernorStop as stop:
                    self._on_stop(stop, engine, session, profiles, checkpoint,
                                  rate_limit_sink, metrics_sink)
                    break
                if result is not None:
                    checkpoint.clear()
                    backoff.reset()
                    if metrics_sink is not None:
                        metrics_sink['engine_used'] = engine.name
                        if session is not None:
                            metrics_sink.setdefault(
                                'sessions_used', [],
                            ).append(session.username)
                    return list(result)

        # Todas tentativas falharam
        if profiles:
            logger.warning(
                f"EngineManager: all engines/sessions exhausted. "
                f"Returning partial result with {len(profiles)} profiles."
            )
            if metrics_sink is not None:
                metrics_sink['partial'] = True
            if with_metadata:
                # Engines que raised perderam seus ProfileSummary ricos;
                # sintetizamos username-only do union acumulado em
                # `profiles`. Melhor que zero perfis perdidos.
                try:
                    from instat.profile_summary import ProfileSummary
                except ImportError:
                    from profile_summary import ProfileSummary  # type: ignore
                return [ProfileSummary.from_username(u) for u in profiles]
            return list(profiles)

        if self.last_stop is not None:
            raise ExtractionStoppedError(
                f"Extraction stopped for {profile_id}/{list_type} "
                f"({self.last_stop.reason}) with zero profiles collected",
                stop=self.last_stop,
            )
        raise AllEnginesBlockedError(
            f"All engines blocked for {profile_id}/{list_type} with zero profiles collected"
        )

    def _governor_key(self, session, list_type: str):
        if session is not None:
            account = session.username
        elif self._default_credentials is not None:
            account = self._default_credentials[0]
        else:
            account = 'default'
        return (account, f'extract:{list_type}')

    def _on_stop(self, stop: GovernorStop, engine, session, profiles, checkpoint,
                 rate_limit_sink, metrics_sink) -> None:
        """Parada terminal: registra motivo e preserva parcial, sem outra conta."""
        logger.warning(
            f"EngineManager: governor stopped on {engine.name} "
            f"({stop.reason}) — no account switch, no further engines"
        )
        self.last_stop = stop
        try:
            checkpoint.save(profiles)
        except Exception as e:
            logger.debug(f"checkpoint.save failed on stop: {e}")
        if stop.signal is Signal.RATE_LIMITED and rate_limit_sink is not None:
            rate_limit_sink.append(engine.name)
        if session is not None and self._session_pool is not None:
            if stop.signal is Signal.RATE_LIMITED:
                self._session_pool.mark_blocked(session, SessionPool.DEFAULT_COOLDOWN)
            elif stop.signal in (Signal.CHALLENGE, Signal.RESTRICTED):
                self._session_pool.mark_blocked(
                    session, SessionPool.META_INTERSTITIAL_COOLDOWN
                )
        if metrics_sink is not None:
            metrics_sink['terminal_reason'] = stop.reason

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
                            should_stop: Optional[Callable[[], bool]] = None,
                            with_metadata: bool = False,
                            _governor_key=None,
                            **kwargs):
        """
        Tenta 1 (engine, session) pair. Retorna profiles (set) em sucesso,
        None em falha técnica (próximo engine). Levanta GovernorStop em
        parada terminal. Atualiza `profiles` in-place em sucesso.
        """
        key = _governor_key or self._governor_key(session, list_type)
        gov = self.governor
        # Re-login se session fornecida
        if session is not None:
            try:
                logger.info(f"{engine.name}: logging in as {session.username}")
                gov.call(key, lambda: engine.login(session.username, session.password,
                                                   proxy=session.proxy),
                         should_stop)
            except GovernorStop:
                raise
            except (AccountBlockedError, BlockedError) as e:
                logger.warning(f"{engine.name}: login failed (technical) for {session.username}: {e}")
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
                    gov.call(key, lambda: engine.login(username, password), should_stop)
                    self._logged_in_engines.add(id(engine))
                except GovernorStop:
                    raise
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
        # Em with_metadata mode, batch pode ser Set[str] (Selenium scroll
        # passa usernames) ou List[ProfileSummary] (httpx). Em ambos os
        # casos extraímos username pra alimentar o set de fallback —
        # quando a cascade inteira falha, sintetizamos username-only
        # ProfileSummary list a partir desse set. Garante que partial
        # de Selenium (mesmo com BlockedError) não seja perdido.
        def on_batch(batch):
            try:
                if with_metadata:
                    for item in batch:
                        if isinstance(item, str):
                            profiles.add(item)
                        else:
                            name = getattr(item, 'username', None)
                            if name:
                                profiles.add(name)
                else:
                    profiles.update(batch)
                checkpoint.save(profiles)
            except Exception as e:
                logger.debug(f"checkpoint.save failed in on_batch: {e}")

        try:
            logger.info(f"Trying engine: {engine.name}")
            extract_kwargs = {
                'existing_profiles': profiles,
                'max_duration': max_duration,
                'on_batch': on_batch,
            }
            # should_stop é honrado por engines que sabem como
            # checar dentro do loop interno (Selenium scroll loop).
            # Engines que ignoram silenciosamente (httpx hoje) não
            # quebram — kwarg é aceito via **kwargs ou ignorado.
            if should_stop is not None:
                extract_kwargs['should_stop'] = should_stop
            if with_metadata:
                extract_kwargs['with_metadata'] = True
            if hasattr(engine, 'bytes_sink'):
                engine.bytes_sink = lambda n, _key=key: gov.record_bytes(_key, n)
            if hasattr(engine, 'budget_check'):
                # Checado antes de cada página: a página já paga é processada.
                engine.budget_check = lambda _key=key: gov.check_budget(_key)
            new = gov.call(
                key, lambda: engine.extract(profile_id, list_type, **extract_kwargs),
                should_stop,
            )
            # with_metadata: a engine retorna List[ProfileSummary].
            # NÃO mergeamos no `profiles: Set[str]` (tipo incompatível);
            # cross-engine partial preservation só vale pro modo
            # username-only. Documentado em InstaExtractor.get_followers.
            if with_metadata:
                return new if new is not None else []
            if new is not None:
                profiles |= set(new)
            return profiles
        except GovernorStop:
            raise
        except (RateLimitError, AccountBlockedError) as e:
            # Só chega aqui se uma política customizada pedir fallback.
            logger.warning(f"{engine.name}: {type(e).__name__} treated as fallback: {e}")
            checkpoint.save(profiles)
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

    def extract_with_metrics(self, profile_id: str, list_type: str,
                             **kwargs):
        """Como extract(), mas envolve a chamada com captura de métricas
        operacionais. Retorna ExtractionResult.

        Sem custo extra além de:
          - 1 chamada `get_total_count` antes da extração (best-effort,
            pode falhar silenciosamente).
          - leitura de `risk_score()` do BlockPredictor no fim, se wired.
          - acumulação de rate-limit hits via rate_limit_sink existente.
        """
        import time as _time
        from datetime import datetime, timezone
        try:
            from instat.extraction_result import ExtractionResult
        except ImportError:
            from extraction_result import ExtractionResult  # type: ignore

        metrics_sink: dict = {}
        rate_limit_hits: List[str] = []
        kwargs.setdefault('rate_limit_sink', rate_limit_hits)
        # Permitir caller passar próprio sink — preserva referência.
        if kwargs['rate_limit_sink'] is not rate_limit_hits:
            rate_limit_hits = kwargs['rate_limit_sink']

        started_at = datetime.now(timezone.utc)
        start_perf = _time.perf_counter()

        expected_count: Optional[int] = None
        try:
            expected_count = self.get_total_count(profile_id, list_type)
        except Exception as e:
            logger.debug(
                f"extract_with_metrics: get_total_count failed: {e}"
            )

        profiles = self.extract(
            profile_id, list_type,
            metrics_sink=metrics_sink,
            **kwargs,
        )

        finished_at = datetime.now(timezone.utc)
        duration = _time.perf_counter() - start_perf
        collected = len(profiles)
        coverage = (collected / expected_count) if expected_count else None

        # BlockPredictor opcional — toma snapshot de qualquer engine
        # que tenha um wired (single shared instance é o pattern).
        bp_score: Optional[float] = None
        for eng in self.engines:
            bp = getattr(eng, '_block_predictor', None)
            if bp is None:
                continue
            try:
                bp_score = bp.risk_score()
                break
            except Exception:
                continue

        return ExtractionResult(
            profiles=profiles,
            profile_id=profile_id,
            list_type=list_type,
            collected_count=collected,
            duration_seconds=duration,
            started_at=started_at,
            finished_at=finished_at,
            expected_count=expected_count,
            coverage_pct=coverage,
            partial=metrics_sink.get('partial', False),
            engine_used=metrics_sink.get('engine_used'),
            sessions_used=metrics_sink.get('sessions_used', []),
            rate_limit_hits=len(rate_limit_hits),
            block_predictor_score=bp_score,
        )

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        """Tenta obter contagem com cada engine."""
        for engine in self.engines:
            try:
                return engine.get_total_count(profile_id, list_type)
            except BlockedError:
                logger.warning(f"{engine.name} blocked on get_total_count")
                continue
        return None

    def get_recent_posts(self, profile_id: str, limit: int = 5) -> list:
        """Cascade get_recent_posts. Engines que raise NotImplementedError
        são puladas silenciosamente — Selenium/Playwright caem nesse
        caso hoje. Retorna lista de PostMetrics da primeira engine que
        consegue.

        Faz login on-demand quando preciso (cookie handoff prioritário,
        creds default como fallback). Reusa a lógica do extract — não
        envolve SessionPool porque post fetch é leve e não precisa de
        rotação por padrão.
        """
        last_err: Optional[Exception] = None
        for engine in self.engines:
            try:
                self._ensure_engine_logged_in(engine)
            except Exception as e:
                logger.debug(
                    f"{engine.name}: skip get_recent_posts — login failed: {e}"
                )
                continue
            try:
                return engine.get_recent_posts(profile_id, limit)
            except NotImplementedError:
                logger.debug(
                    f"{engine.name}: does not implement get_recent_posts — "
                    "trying next engine"
                )
                continue
            except BlockedError as e:
                logger.warning(
                    f"{engine.name}: get_recent_posts blocked: {e}"
                )
                last_err = e
                continue
            except Exception as e:
                logger.warning(
                    f"{engine.name}: get_recent_posts unexpected: "
                    f"{type(e).__name__}: {e}"
                )
                last_err = e
                continue
        if last_err is not None:
            raise last_err
        raise NotImplementedError(
            "No engine in the cascade implements get_recent_posts. "
            "Add httpx engine: engines=['selenium', 'httpx']"
        )

    def _ensure_engine_logged_in(self, engine: BaseEngine) -> None:
        """Garante que engine está logado. Cookie handoff primeiro,
        creds default depois. Usado por get_recent_posts e qualquer
        método auxiliar que não passe pelo path de extract."""
        if id(engine) in self._logged_in_engines:
            return
        if self._try_cookie_handoff(engine):
            self._logged_in_engines.add(id(engine))
            return
        if self._default_credentials is not None:
            u, p = self._default_credentials
            engine.login(u, p)
            self._logged_in_engines.add(id(engine))
            return
        raise RuntimeError(
            f"{engine.name}: no login path available "
            "(no cookie source + no default_credentials)"
        )

    def quit_all(self) -> None:
        """Fecha todas as engines."""
        for engine in self.engines:
            try:
                engine.quit()
            except Exception as e:
                logger.warning(f"Error quitting {engine.name}: {e}")
