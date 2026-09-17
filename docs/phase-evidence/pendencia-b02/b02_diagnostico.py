"""Pendência B02 — distribuição da latência do heartbeat (medição, sem mudar código).

Reproduz o cenário B02 (tests/jobstore/e3_scenarios.py) com instrumentação:
  - latência de cada heartbeat e tamanho do WAL antes/depois (queda do WAL = checkpoint);
  - condições: base | CPU saturada (processos em laço ocupado = nº de CPUs) |
    heartbeat com wal_autocheckpoint=0 (heartbeat nunca faz checkpoint);
  - N repetições por condição.
Hipóteses: H1 checkpoint automático dentro do commit do heartbeat; H2 contenção de CPU.
"""
import json
import multiprocessing as mp
import os
import sqlite3
import statistics
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "jobstore"))

import e3_scenarios as E3  # noqa: E402

from instat.jobstore.store import JobStore  # noqa: E402


def burn(stop_at):
    while time.time() < stop_at:
        pass


def wal_size(path):
    try:
        return os.path.getsize(path + "-wal")
    except OSError:
        return 0


def one_run(hb_autocheckpoint=True):
    d = tempfile.mkdtemp(prefix="b02_")
    path = os.path.join(d, "db.sqlite")
    m = JobStore(path)
    E3._populate(m)
    s = JobStore(path, ttl=30.0, busy_timeout=5.0, create=False)
    s.add_account("HB")
    tok = s.acquire("HB", "hb", time.time())
    samples, stop = [], threading.Event()

    def heartbeat():
        h = JobStore(path, ttl=30.0, busy_timeout=5.0, create=False)
        if not hb_autocheckpoint:
            h.c.execute("PRAGMA wal_autocheckpoint=0")
        while not stop.is_set():
            w0 = wal_size(path)
            t0 = time.perf_counter()
            r = h.heartbeat(tok, time.time())
            dt = time.perf_counter() - t0
            samples.append({"dt": dt, "r": r, "wal_before": w0, "wal_after": wal_size(path)})
            time.sleep(0.02)

    wr = E3._Writer(path)
    th = threading.Thread(target=heartbeat, daemon=True)
    th.start()
    wr.start()
    info = m.backup_online(path + ".bak", pages=1, step_sleep_s=0.004, deadline_s=1.0)
    stop.set()
    wr.stop.set()
    th.join()
    wr.join()
    worst = max(samples, key=lambda x: x["dt"])
    return {"n": len(samples), "status": info["status"], "fails": sum(1 for x in samples if x["r"] != "renewed"),
            "p50": statistics.median(x["dt"] for x in samples), "worst": worst["dt"],
            "worst_checkpoint": worst["wal_after"] < worst["wal_before"],
            "checkpoints_in_hb": sum(1 for x in samples if x["wal_after"] < x["wal_before"]),
            "over_05": sum(1 for x in samples if x["dt"] >= 0.5),
            "over_01": sum(1 for x in samples if x["dt"] >= 0.1)}


def condition(name, reps, stress=False, hb_autocheckpoint=True):
    rows = []
    for _ in range(reps):
        procs = []
        if stress:
            stop_at = time.time() + 30
            procs = [mp.Process(target=burn, args=(stop_at,)) for _ in range(os.cpu_count() or 2)]
            [p.start() for p in procs]
            time.sleep(0.3)
        try:
            rows.append(one_run(hb_autocheckpoint))
        finally:
            for p in procs:
                p.terminate()
                p.join()
    worsts = sorted(r["worst"] for r in rows)
    summary = {
        "condicao": name, "repeticoes": reps,
        "pior_max": round(worsts[-1], 3), "pior_p50": round(statistics.median(worsts), 3),
        "execucoes_com_pior_>=0.5s": sum(1 for w in worsts if w >= 0.5),
        "execucoes_com_pior_>=0.1s": sum(1 for w in worsts if w >= 0.1),
        "pior_coincide_com_checkpoint": sum(1 for r in rows if r["worst_checkpoint"]),
        "checkpoints_feitos_pelo_heartbeat": sum(r["checkpoints_in_hb"] for r in rows),
        "falhas_heartbeat": sum(r["fails"] for r in rows),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return summary, rows


if __name__ == "__main__":
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    out = {"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "cpus": os.cpu_count()}
    for name, kw in (("base", {}), ("cpu_saturada", {"stress": True}),
                     ("hb_sem_autocheckpoint", {"hb_autocheckpoint": False}),
                     ("cpu_saturada+hb_sem_autocheckpoint", {"stress": True, "hb_autocheckpoint": False})):
        s, rows = condition(name, reps, **kw)
        out[name] = {"summary": s, "runs": rows}
    with open(os.path.join(HERE, f"b02_{sys.version_info.major}{sys.version_info.minor}.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
