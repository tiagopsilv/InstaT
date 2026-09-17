"""Scheduler de contas com exclusividade entre processos (roadmap F4).

Reusa as transações da F5 (`JobStore.acquire`/`heartbeat`/`release_lease`,
fencing por geração, `endpoint_cooldowns`, liberação manual) — nenhuma
transação de posse nova.

Regras:
  - uma conta, no máximo uma concessão vigente, em qualquer processo;
  - workers ≤ contas elegíveis;
  - challenge → `needs_attention`, restrição → `restricted`: só liberação manual
    com sessão validada devolve a conta; o tempo não libera;
  - 429 → cooldown do endpoint para aquela conta;
  - falha técnica libera a concessão (outra execução pode retomar);
  - a engine (browser) é criada, usada e encerrada na MESMA thread do worker
    (Playwright e Selenium não são thread-safe).
"""
import queue
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

from loguru import logger

try:
    from instat.exceptions import AccountBlockedError, ChallengeError, RateLimitError, RestrictedError
    from instat.jobstore.heartbeat import Heartbeat
    from instat.jobstore.store import JobStore, Token
except ImportError:  # pragma: no cover
    from exceptions import AccountBlockedError, ChallengeError, RateLimitError, RestrictedError  # type: ignore
    from jobstore.heartbeat import Heartbeat  # type: ignore
    from jobstore.store import JobStore, Token  # type: ignore

RATE_LIMIT_MIN_COOLDOWN_S = 900.0


class NoEligibleAccount(Exception):
    """Nenhuma conta elegível (ou a conta pedida não pôde ser concedida)."""


class AccountScheduler:
    def __init__(self, store_path: str, owner: str, endpoint: str = "followers", ttl: float = 30.0,
                 busy_timeout: Optional[float] = None, heartbeat: bool = True) -> None:
        self.store_path, self.owner, self.endpoint, self.ttl = store_path, owner, endpoint, ttl
        self.busy_timeout = ttl / 6 if busy_timeout is None else busy_timeout
        self.heartbeat = heartbeat
        self._store = JobStore(store_path, ttl=ttl, busy_timeout=self.busy_timeout, create=False)

    def _new_store(self) -> JobStore:
        return JobStore(self.store_path, ttl=self.ttl, busy_timeout=self.busy_timeout, create=False)

    # ------------------------------------------------------------ elegibilidade
    def eligible_accounts(self, now: Optional[float] = None) -> List[str]:
        return self._store.eligible_accounts(self.endpoint, now=now)

    def plan_workers(self, requested: int, now: Optional[float] = None) -> int:
        return max(0, min(int(requested), len(self.eligible_accounts(now))))

    # ------------------------------------------------------------ concessão
    @contextmanager
    def lease(self, account: str, owner: Optional[str] = None,
              store: Optional[JobStore] = None) -> Iterator[Token]:
        st = store or self._store
        who = owner or self.owner
        tok = st.acquire(account, who, now=None, endpoint=self.endpoint)
        if tok is None:
            raise NoEligibleAccount(f"account {account!r} not grantable for {self.endpoint!r}")
        hb = Heartbeat(self.store_path, tok, ttl=self.ttl, busy_timeout=self.busy_timeout).start() \
            if self.heartbeat else None
        try:
            yield tok
        except (ChallengeError, AccountBlockedError) as e:
            self._mark(st, account, "needs_attention", e)
            raise
        except RestrictedError as e:
            self._mark(st, account, "restricted", e)
            raise
        except RateLimitError as e:
            wait = max(RATE_LIMIT_MIN_COOLDOWN_S, float(e.retry_after or 0))
            st.set_endpoint_cooldown(account, self.endpoint, until=time.time() + wait, reason="rate_limited")
            logger.warning(f"scheduler: {account} rate limited on {self.endpoint}; cooldown {wait:.0f}s")
            raise
        finally:
            if hb is not None:
                hb.stop()
            st.release_lease(tok, now=None)

    @staticmethod
    def _mark(st: JobStore, account: str, state: str, exc: BaseException) -> None:
        st.set_auth(account, state, now=None)
        logger.warning(f"scheduler: {account} → {state} ({type(exc).__name__}); manual release required")

    # ------------------------------------------------------------ execução por alvos
    def run_targets(self, targets: List[str], worker_fn: Callable[[str, str, Any], Any], requested_workers: int,
                    engine_factory: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
        """Processa alvos independentes com no máximo uma conta por worker.

        worker_fn(account, target, engine) → resultado. Cada worker reserva UMA conta
        durante toda a sua vida, cria a engine na própria thread e a encerra nela.
        """
        report: Dict[str, Any] = {"targets": {t: {"status": "not_processed"} for t in targets}}
        eligible = self.eligible_accounts()
        n = self.plan_workers(requested_workers)
        report["workers"] = n
        if n == 0:
            report["reason"] = "no_eligible_account"
            return report
        pending: "queue.Queue[str]" = queue.Queue()
        for t in targets:
            pending.put(t)
        lock = threading.Lock()
        accounts_q: "queue.Queue[str]" = queue.Queue()
        for a in eligible:
            accounts_q.put(a)

        def worker(idx: int) -> None:
            store = self._new_store()
            owner = f"{self.owner}#w{idx}"
            while True:
                try:
                    account = accounts_q.get_nowait()
                except queue.Empty:
                    return
                try:
                    with self.lease(account, owner=owner, store=store):
                        engine = engine_factory() if engine_factory else None
                        try:
                            while True:
                                try:
                                    target = pending.get_nowait()
                                except queue.Empty:
                                    return
                                try:
                                    result = worker_fn(account, target, engine)
                                    rec = {"status": "ok", "account": account, "result": result}
                                except (ChallengeError, AccountBlockedError, RestrictedError, RateLimitError) as e:
                                    reason = ("challenge" if isinstance(e, (ChallengeError, AccountBlockedError))
                                              else "restricted" if isinstance(e, RestrictedError) else "rate_limited")
                                    with lock:
                                        report["targets"][target] = {"status": "stopped", "account": account,
                                                                     "reason": reason, "error": str(e)}
                                    raise
                                except Exception as e:
                                    rec = {"status": "error", "account": account,
                                           "error": f"{type(e).__name__}: {e}"}
                                with lock:
                                    report["targets"][target] = rec
                        finally:
                            if engine is not None and hasattr(engine, "quit"):
                                engine.quit()
                except NoEligibleAccount:
                    continue
                except (ChallengeError, AccountBlockedError, RestrictedError, RateLimitError):
                    return   # conta parada; este worker não troca de conta para insistir
                finally:
                    pass

        threads = [threading.Thread(target=worker, args=(i,), name=f"scheduler-w{i}") for i in range(n)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return report


__all__ = ["AccountScheduler", "NoEligibleAccount"]
