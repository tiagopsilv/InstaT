"""
Coordenador paralelo para extração de followers/following.

N SeleniumEngines trabalham em paralelo sobre o mesmo perfil alvo.
Suas coletas são unificadas via set compartilhado. Quando a união
atinge `stop_threshold * target_count`, `stop_event` é sinalizado
e todos os workers saem graciosamente via `should_stop` callback.

Uso de contas:
- Se `accounts` fornecido: cada worker usa conta da rotação.
- Se não: todos usam credenciais default (RISCO: IG pode bloquear
  múltiplas sessões simultâneas da mesma conta).

API externa (HttpxEngine) NÃO é usada aqui — apenas fallback via
quem chama (ex.: get_both → parallel → httpx).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Set

from loguru import logger

try:
    from instat.engines.selenium_engine import SeleniumEngine
    from instat.login import InstaLogin
except ImportError:
    from engines.selenium_engine import SeleniumEngine
    from login import InstaLogin


class ParallelCoordinator:
    """Estado compartilhado entre workers paralelos."""

    def __init__(self, target_count: Optional[int], stop_threshold: float = 0.98):
        self.shared: Set[str] = set()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.target_count = target_count
        self.stop_threshold = stop_threshold

    def ingest(self, batch: Set[str]) -> int:
        """Merge batch no set compartilhado; retorna total atual."""
        with self.lock:
            self.shared.update(batch)
            total = len(self.shared)
        if self.target_count and total >= self.target_count * self.stop_threshold:
            if not self.stop_event.is_set():
                logger.info(f"ParallelCoordinator: {total}/{self.target_count} reached — signalling stop")
                self.stop_event.set()
        return total

    def should_stop(self) -> bool:
        return self.stop_event.is_set()

    def snapshot(self) -> List[str]:
        with self.lock:
            return list(self.shared)


def parallel_extract(
    profile_id: str,
    list_type: str,
    workers: int,
    default_credentials: tuple,
    accounts: Optional[List[Dict[str, str]]] = None,
    stop_threshold: float = 0.98,
    target_count: Optional[int] = None,
    max_duration: Optional[float] = None,
    engine_factory: Optional[Callable[[], SeleniumEngine]] = None,
    headless: bool = True,
    timeout: int = 20,
) -> List[str]:
    """
    Executa N SeleniumEngines em paralelo sobre o mesmo perfil alvo.
    Une coletas num set compartilhado e para quando atinge threshold.

    Retorna lista dos perfis coletados pela união.
    """
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if accounts is None:
        accounts = []
    if not accounts and not default_credentials:
        raise ValueError("must provide accounts or default_credentials")

    if accounts and len(accounts) < workers:
        logger.warning(
            f"parallel_extract: {workers} workers with only {len(accounts)} accounts "
            "— sessions will share credentials (risk of IG block)"
        )

    coord = ParallelCoordinator(target_count, stop_threshold)

    def _resolve_creds(idx: int) -> tuple:
        if accounts:
            acc = accounts[idx % len(accounts)]
            return acc['username'], acc['password']
        return default_credentials

    def _worker(idx: int) -> Set[str]:
        username, password = _resolve_creds(idx)
        engine = (engine_factory() if engine_factory
                  else SeleniumEngine(headless=headless, timeout=timeout,
                                      _login_class=InstaLogin))
        try:
            engine.login(username, password)
            def on_batch(batch):
                coord.ingest(batch)
            result = engine.extract(
                profile_id, list_type,
                existing_profiles=coord.snapshot(),
                max_duration=max_duration,
                on_batch=on_batch,
                should_stop=coord.should_stop,
            )
            coord.ingest(set(result))
            return set(result)
        except Exception as e:
            logger.warning(f"parallel worker {idx} failed: {e}")
            return set()
        finally:
            try:
                engine.quit()
            except Exception:
                pass

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker, i) for i in range(workers)]
        for _ in as_completed(futures):
            pass
    elapsed = time.perf_counter() - start
    total = len(coord.shared)
    logger.info(
        f"parallel_extract: {workers} workers collected {total} unique "
        f"{list_type} for {profile_id} in {elapsed:.1f}s"
    )
    return coord.snapshot()
