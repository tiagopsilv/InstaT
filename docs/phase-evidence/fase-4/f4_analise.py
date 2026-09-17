"""F4 passo 1 — análise medida (engines fake; sem browser, sem rede, sem conta).

A1 parallel_extract com mais workers que contas → mesma conta em sessões simultâneas
A2 sem accounts, workers>1 → todas as sessões com a mesma credencial
A3 worker que cai no meio → falha silenciosa (set vazio), sem registro do motivo
A4 SessionPool em dois processos → a mesma conta é entregue aos dois (estado só em memória)
A5 conta marcada como bloqueada volta a ser entregue quando o cooldown expira (tempo libera)
A6 WorkerPool não limita engines ao número de contas
"""
import multiprocessing as mp
import os
import sys
import threading
import time
from collections import defaultdict
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

from loguru import logger  # noqa: E402

logger.remove()

from instat.parallel import parallel_extract  # noqa: E402
from instat.session_pool import SessionPool  # noqa: E402

active = defaultdict(int)
peak = defaultdict(int)
lock = threading.Lock()


class FakeEngine:
    def __init__(self, crash=False):
        self.user, self.crash = None, crash

    def login(self, username, password, **kw):
        self.user = username
        with lock:
            active[username] += 1
            peak[username] = max(peak[username], active[username])

    def extract(self, profile_id, list_type, **kw):
        time.sleep(0.2)
        if self.crash:
            raise RuntimeError("driver morreu no meio")
        return {f"{self.user}_x"}

    def quit(self):
        with lock:
            active[self.user] -= 1


def run(workers, accounts, default=("unica", "pw"), crash_idx=None):
    active.clear()
    peak.clear()
    n = [0]

    def factory():
        n[0] += 1
        return FakeEngine(crash=(crash_idx is not None and n[0] - 1 == crash_idx))
    out = parallel_extract("alvo", "followers", workers=workers, default_credentials=default,
                           accounts=accounts, engine_factory=factory)
    return out, dict(peak)


def _proc(q):
    pool = SessionPool([{"username": "conta_a", "password": "pw"}])
    s = pool.get_available()
    q.put(s.username if s else None)


if __name__ == "__main__":
    print("=== A1 4 workers, 2 contas")
    out, pk = run(4, [{"username": "a1", "password": "p"}, {"username": "a2", "password": "p"}])
    print(f"  sessões simultâneas por conta (pico): {pk}")

    print("=== A2 3 workers, sem accounts")
    out, pk = run(3, None)
    print(f"  sessões simultâneas por conta (pico): {pk}")

    print("=== A3 worker 1 cai durante extract")
    out, pk = run(3, [{"username": f"c{i}", "password": "p"} for i in range(3)], crash_idx=1)
    print(f"  resultado devolvido: {sorted(out)} — sem indicação de falha nem motivo")

    print("=== A4 SessionPool em dois processos")
    q = mp.Queue()
    ps = [mp.Process(target=_proc, args=(q,)) for _ in range(2)]
    [p.start() for p in ps]
    [p.join() for p in ps]
    print(f"  conta entregue a cada processo: {[q.get(), q.get()]}")

    print("=== A5 cooldown expira e libera conta bloqueada (challenge)")
    pool = SessionPool([{"username": "conta_a", "password": "pw"}])
    s = pool.get_available()
    t0 = time.time()
    pool.mark_blocked(s, SessionPool.META_INTERSTITIAL_COOLDOWN)
    print(f"  logo após bloqueio: {pool.get_available()}")
    with patch("instat.session_pool.time.time", return_value=t0 + SessionPool.META_INTERSTITIAL_COOLDOWN + 1):
        again = pool.get_available()
    print(f"  6 h depois, sem revisão manual: {again.username if again else None}")

    print("=== A6 WorkerPool")
    import inspect

    from instat.worker_pool import WorkerPool
    src = inspect.getsource(WorkerPool.add_worker)
    print(f"  add_worker verifica conta repetida ou limite por contas: {'username in' in src or 'duplicate' in src}")
