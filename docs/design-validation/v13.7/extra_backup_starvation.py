"""Teste ADICIONAL pós-implementação (não TDD): o backup incremental conclui com só o heartbeat escrevendo?

Hipótese derivada de sqlite.org/backup.html: escrita de outra conexão reinicia o backup incremental.
Compara três cenários com o mesmo banco: incremental sem escritor, incremental só com heartbeat,
passo único com heartbeat.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_v137 as t  # noqa: E402
from models_v137 import V137  # noqa: E402


def scenario(label, pages, step_sleep, with_hb):
    m, path = t.fresh(V137)
    t._populate(m, n_pages=150)
    hb = t._Heartbeat(V137, path, interval=0.05) if with_hb else None
    if hb:
        hb.start()
        time.sleep(0.1)
    info = m.backup_online(path + ".bak", pages=pages, step_sleep_s=step_sleep, deadline_s=6.0)
    if hb:
        hb.stop.set()
        hb.join()
    n, fails, worst = t._hb_summary(hb) if hb else (0, 0, 0.0)
    print(f"{label:48} status={info['status']:8} duração={info['elapsed']:.2f}s passos={info['steps']:5} "
          f"heartbeats={n} falhas={fails} pior={worst:.3f}s")
    return info


if __name__ == "__main__":
    scenario("incremental 1 pág/passo, SEM escritor", 1, 0.002, False)
    scenario("incremental 1 pág/passo, SÓ heartbeat (50 ms)", 1, 0.002, True)
    scenario("passo único (pages=-1), com heartbeat", -1, 0.0, True)
