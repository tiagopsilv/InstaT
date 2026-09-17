"""F4 — critérios 1, 2, 3 e 5: disputa real entre processos separados (SQLite compartilhado).

Intervalos de posse vêm dos horários das próprias transações (acquire/release),
então a verificação de sobreposição é exata na ordem serial do banco.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from instat.jobstore.store import JobStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TTL = 0.5
ACCOUNTS = ["a1", "a2"]

CHILD = r"""
import json, os, random, sys, time, uuid
sys.path.insert(0, sys.argv[1])
from instat.jobstore.store import JobStore
path, me, iterations, log_path, slow_every = sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], int(sys.argv[6])
TTL = 0.5
m = JobStore(path, ttl=TTL, busy_timeout=TTL / 6, create=False)
rng = random.Random(me)
out = open(log_path, "a", encoding="utf-8")
def log(**kw):
    out.write(json.dumps(kw) + "\n"); out.flush()
def retry(fn):
    for _ in range(50):
        try:
            return fn()
        except Exception as e:
            if "locked" not in str(e):
                raise
            time.sleep(0.005)
    raise RuntimeError("database locked após 50 tentativas")
if slow_every < 0:
    # modo "hold": adquire uma conta, registra e segura a concessão até ser morto
    while True:
        for acct in ("a1", "a2"):
            tok = retry(lambda: m.acquire(acct, me, now=None))
            if tok is not None:
                log(ev="hold", **m.last_lease)
                time.sleep(3600)
        time.sleep(0.01)
done = 0
granted = 0
while done < iterations:
    acct = rng.choice(["a1", "a2", "a_bloqueada"])
    tok = retry(lambda: m.acquire(acct, me, now=None))
    done += 1
    if tok is None:
        log(ev="miss", acct=acct)
        continue
    lease = dict(m.last_lease)
    granted += 1
    job = f"J_{acct}"
    st, run = retry(lambda: m.claim(job, tok, now=None))
    slow = slow_every and granted % slow_every == 0
    if slow:
        time.sleep(TTL + 0.25)
        if st == "ok":
            r = retry(lambda: m.commit(tok, job, run, attempt_id=str(uuid.uuid4()), pos=0, cursor_in=None,
                                       cursor_out=None, members=[(None, "late")], end_marker=True, now=None))
        else:
            r = "no_claim"
        log(ev="lease", **lease, end=lease["until"], slow=True, late_commit=r, claim=st)
        continue
    time.sleep(rng.uniform(0, 0.003))
    m.last_release = None
    if st == "ok":
        # stop_run também encerra a concessão (F5); a 1ª liberação efetiva define o fim
        retry(lambda: m.stop_run(tok, job, run, "technical_error", now=None))
    ended_by_stop = m.last_release if (m.last_release and m.last_release["released"]) else None
    released = retry(lambda: m.release_lease(tok, now=None))
    rel = ended_by_stop or (m.last_release if released else None)
    end = min(rel["at"], lease["until"]) if rel else lease["until"]
    log(ev="lease", **lease, end=end, slow=False, claim=st)
"""


def _setup(path):
    m = JobStore(path, ttl=TTL, busy_timeout=TTL / 6)
    for a in ACCOUNTS + ["a_bloqueada"]:
        m.add_account(a)
        m.create_job(f"J_{a}")
    m.c.execute("UPDATE jobs SET attempts=-1000000")   # o teste mede posse, não o limite de execuções
    m.set_auth("a_bloqueada", "needs_attention", now=None)
    t_blocked = m.c.execute("SELECT restricted_at FROM accounts WHERE account='a_bloqueada'").fetchone()[0]
    m.close()
    return t_blocked


def _run(tmp_path, procs, iterations, slow_every=15, kill_one=False):
    path = str(tmp_path / f"d{procs}.db")
    t_blocked = _setup(path)
    logs = [str(tmp_path / f"p{procs}_{i}.jsonl") for i in range(procs)]
    modes = [(-1 if kill_one and i == 0 else slow_every) for i in range(procs)]
    ps = [subprocess.Popen([sys.executable, "-c", CHILD, ROOT, path, f"p{i}", str(iterations), logs[i],
                            str(modes[i])], stderr=subprocess.PIPE, text=True) for i in range(procs)]
    killed = None
    if kill_one:
        # p0 roda em modo "hold": o kill acontece com a concessão comprovadamente detida
        deadline = time.time() + 30
        while time.time() < deadline and killed is None:
            if os.path.exists(logs[0]):
                with open(logs[0], encoding="utf-8") as f:
                    holds = [json.loads(line) for line in f if '"hold"' in line]
                if holds:
                    ps[0].kill()
                    h = holds[0]
                    killed = {"at": time.time(), "account": h["account"], "gen": h["gen"],
                              "lease_until": h["until"]}
            time.sleep(0.005)
    for p in ps:
        p.wait(timeout=600)
    errs = [p.stderr.read() for i, p in enumerate(ps) if p.returncode != 0 and not (kill_one and i == 0)]
    events = []
    for lp in logs:
        if os.path.exists(lp):
            with open(lp, encoding="utf-8") as f:
                events += [json.loads(line) for line in f if line.strip()]
    return path, t_blocked, events, errs, killed


def _check(events, t_blocked):
    # concessão "hold" do processo morto: nunca liberada, termina em lease_until
    leases = [e for e in events if e["ev"] == "lease"] +         [{**e, "end": e["until"]} for e in events if e["ev"] == "hold"]
    overlaps = 0
    for acct in ACCOUNTS + ["a_bloqueada"]:
        iv = sorted((e["start"], e["end"], e["owner"], e["gen"]) for e in leases if e["account"] == acct)
        for (s1, e1, o1, g1), (s2, e2, o2, g2) in zip(iv, iv[1:]):
            if s2 < e1 - 1e-9:
                overlaps += 1
    blocked_grants = sum(1 for e in leases if e["account"] == "a_bloqueada" and e["start"] >= t_blocked)
    late = [e["late_commit"] for e in leases if e.get("slow") and e.get("claim") == "ok"]
    return {"leases": len(leases), "overlaps": overlaps, "blocked_grants": blocked_grants,
            "late_commits": len(late), "late_accepted": sum(1 for r in late if not r.startswith("rejected:"))}


@pytest.mark.parametrize("procs,iterations", [(2, 167), (3, 111), (4, 84)])
def test_dispute_between_processes_no_double_lease(tmp_path, procs, iterations):
    # 2×167 + 3×111 + 4×84 = 1.003 iterações no total das três configurações
    path, t_blocked, events, errs, _ = _run(tmp_path, procs, iterations)
    assert not errs, errs
    assert sum(1 for e in events if e["ev"] in ("lease", "miss")) == procs * iterations
    r = _check(events, t_blocked)
    out = os.environ.get("INSTAT_F4_EVIDENCE")
    if out:
        with open(os.path.join(out, f"disputa_{procs}proc.json"), "w", encoding="utf-8") as f:
            json.dump({"procs": procs, "iterations_per_proc": iterations, "summary": r, "t_blocked": t_blocked,
                       "leases": [e for e in events if e["ev"] == "lease"]}, f)
    assert r["leases"] > 0
    assert r["overlaps"] == 0, r
    assert r["blocked_grants"] == 0, r
    assert r["late_commits"] > 0 and r["late_accepted"] == 0, r


def test_killed_process_lease_recovered_after_ttl_and_restriction_kept(tmp_path):
    path, t_blocked, events, errs, killed = _run(tmp_path, 2, 1500, slow_every=0, kill_one=True)
    assert not errs, errs
    assert killed is not None, "p0 nunca foi visto com concessão vigente"
    r = _check(events, t_blocked)
    assert r["overlaps"] == 0 and r["blocked_grants"] == 0
    # recuperação: p1 obtém A MESMA conta que p0 detinha, só depois do fim daquela concessão
    same = [e for e in events if e["ev"] == "lease" and e["owner"] == "p1" and e["account"] == killed["account"]
            and e["start"] > killed["at"]]
    assert same, "o sobrevivente não readquiriu a conta do processo morto"
    assert min(e["start"] for e in same) >= killed["lease_until"] - 1e-9
    c = sqlite3.connect(path)
    assert c.execute("SELECT auth_state FROM accounts WHERE account='a_bloqueada'").fetchone()[0] == "needs_attention"
    c.close()
