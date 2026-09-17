"""F5 — critérios 3, 4 e 5: injeção de falhas, processo morto e disputa real entre processos.

Servidor HTTP local paginado com N = 1.000 membros conhecidos; nada sai da máquina.
"""
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

import pytest

from instat.jobstore.spool import AttemptSpool
from instat.jobstore.store import JobStore
from instat.jobstore.worker import FAULT_POINTS, CursorWorker, InjectedCrash

N, PAGE = 1000, 50
KNOWN = [f"membro_{i:04d}" for i in range(N)]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Handler(BaseHTTPRequestHandler):
    hits = 0

    def log_message(self, *a):
        pass

    def do_GET(self):
        _Handler.hits += 1
        q = parse_qs(urlparse(self.path).query)
        start = int(q.get("max_id", ["0"])[0])
        chunk = KNOWN[start:start + PAGE]
        nxt = start + PAGE
        body = json.dumps({"users": [{"pk": str(1000 + start + i), "username": u} for i, u in enumerate(chunk)],
                           "next_max_id": str(nxt) if nxt < N else None,
                           "big_list": nxt < N}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def http_fetch(base):
    def fetch(cursor_in, timeout_s):
        url = f"{base}/friendships/1/followers/" + (f"?max_id={cursor_in}" if cursor_in else "")
        data = json.loads(urlopen(url, timeout=timeout_s).read())
        return {"members": [[u["pk"], u["username"]] for u in data["users"]],
                "cursor_out": data["next_max_id"],
                "end_marker": not data["big_list"] and data["next_max_id"] is None,
                "page_ok": True}
    return fetch


def setup_job(path):
    m = JobStore(path, ttl=600.0, busy_timeout=5.0)
    m.add_account("A")
    m.create_job("J")
    tok = m.acquire("A", "w1", now=None)
    status, run = m.claim("J", tok, now=None)
    assert status == "ok"
    m.c.close()
    return tok, run


def check_integrity(path, run):
    c = sqlite3.connect(path)
    dup = c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM pages WHERE quality='trusted' "
                    "GROUP BY run_id, pos HAVING COUNT(*)>1)").fetchone()[0]
    next_pos = c.execute("SELECT next_pos FROM runs WHERE run_id=?", (run,)).fetchone()[0]
    trusted = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND quality='trusted'", (run,)).fetchone()[0]
    members = c.execute("""SELECT COUNT(*), COUNT(DISTINCT m.username) FROM observations o
                           JOIN pages p ON p.page_id=o.page_id JOIN members m ON m.member_id=o.member_id
                           WHERE p.run_id=? AND p.quality='trusted'""", (run,)).fetchone()
    member_rows = c.execute("SELECT COUNT(*), COUNT(DISTINCT member_pk) FROM members").fetchone()
    integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
    c.close()
    return {"dup_trusted": dup, "next_pos": next_pos, "trusted_pages": trusted, "observations": members[0],
            "distinct_usernames": members[1], "member_rows": member_rows[0],
            "distinct_member_pk": member_rows[1], "integrity": integrity}


# ------------------------------------------------------------ critério 3
def test_100_fault_injections_no_confirmed_page_lost_no_logical_duplicates(tmp_path, server):
    path, spool_dir = str(tmp_path / "jobs.db"), str(tmp_path / "spool")
    tok, run = setup_job(path)
    pages = N // PAGE
    plan = [(pos, point) for pos in range(pages) for point in FAULT_POINTS]
    assert len(plan) == 100
    remaining = list(plan)
    stats = {"crashes": 0, "reprocessed": 0, "duplicates": 0, "committed": 0, "recovered_from_spool": 0}
    confirmed_before_crash = set()
    for _ in range(400):
        m = JobStore(path, ttl=600.0, busy_timeout=5.0, create=False)
        pending_fault = {}

        def fault(point, pos):
            if (pos, point) in remaining and (pos, point) not in pending_fault:
                pending_fault[(pos, point)] = True
                remaining.remove((pos, point))
                raise InjectedCrash(f"{point}@{pos}")
        w = CursorWorker(m, AttemptSpool(spool_dir), http_fetch(server), tok, "J", run,
                         request_timeout_s=5.0, fault=fault)
        try:
            result = w.run()
        except InjectedCrash:
            stats["crashes"] += 1
            result = None
        for k in ("reprocessed", "duplicates", "committed", "recovered_from_spool"):
            stats[k] += w.stats[k]
        c = sqlite3.connect(path)
        confirmed_now = {r[0] for r in c.execute("SELECT attempt_id FROM pages WHERE run_id=?", (run,))}
        c.close()
        assert confirmed_before_crash <= confirmed_now, "página confirmada perdida"
        confirmed_before_crash = confirmed_now
        m.c.close()
        if result is not None:
            break
    assert stats["crashes"] == 100 and not remaining
    assert result["stop"] == "end_confirmed"
    integ = check_integrity(path, run)
    assert integ == {"dup_trusted": 0, "next_pos": pages, "trusted_pages": pages, "observations": N,
                     "distinct_usernames": N, "member_rows": N, "distinct_member_pk": N, "integrity": "ok"}
    rr = JobStore(path, create=False).run_result(run)
    assert rr["status"] == "complete" and rr["members"] == KNOWN
    assert stats["reprocessed"] > 0 and stats["duplicates"] > 0 and stats["recovered_from_spool"] > 0
    os.makedirs(os.path.join(ROOT, "docs", "phase-evidence", "fase-5"), exist_ok=True)
    out = os.environ.get("INSTAT_F5_EVIDENCE")
    if out:
        with open(os.path.join(out, "faults.json"), "w", encoding="utf-8") as f:
            json.dump({"stats": stats, "integrity": integ, "run_result": {k: rr[k] for k in (
                "status", "progress_state", "end_evidence", "collected", "suspect_open", "next_pos")}},
                f, indent=2)


# ------------------------------------------------------------ critério 4: processo morto
CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from urllib.request import urlopen
from instat.jobstore.store import JobStore
from instat.jobstore.spool import AttemptSpool
from instat.jobstore.worker import CursorWorker
path, spool, base, acct, owner, gen, run, kill_after = sys.argv[2:10]
tok = (acct, owner, int(gen))
def fetch(cursor_in, timeout_s):
    url = f"{base}/friendships/1/followers/" + (f"?max_id={cursor_in}" if cursor_in else "")
    d = json.loads(urlopen(url, timeout=timeout_s).read())
    return {"members": [[u["pk"], u["username"]] for u in d["users"]], "cursor_out": d["next_max_id"],
            "end_marker": not d["big_list"] and d["next_max_id"] is None, "page_ok": True}
def fault(point, pos):
    if point == "after_commit" and pos == int(kill_after):
        os._exit(9)   # morte real do processo, sem ack do spool nem limpeza
m = JobStore(path, ttl=600.0, busy_timeout=5.0, create=False)
w = CursorWorker(m, AttemptSpool(spool), fetch, tok, "J", int(run), request_timeout_s=5.0, fault=fault)
r = w.run()
print(json.dumps({"result": r, "stats": w.stats}))
"""


def test_killed_process_after_commit_resumes_without_loss_or_duplicate(tmp_path, server):
    path, spool = str(tmp_path / "jobs.db"), str(tmp_path / "spool")
    tok, run = setup_job(path)
    args = [sys.executable, "-c", CHILD, ROOT, path, spool, server, tok[0], tok[1], str(tok[2]), str(run)]
    p1 = subprocess.run(args + ["7"], capture_output=True, text=True, timeout=120)
    assert p1.returncode == 9, p1.stderr
    pending = AttemptSpool(spool).pending(run)
    assert len(pending) == 1 and pending[0]["pos"] == 7
    c = sqlite3.connect(path)
    assert c.execute("SELECT COUNT(*) FROM pages WHERE attempt_id=?", (pending[0]["attempt_id"],)).fetchone()[0] == 1
    c.close()
    p2 = subprocess.run(args + ["-1"], capture_output=True, text=True, timeout=120)
    assert p2.returncode == 0, p2.stderr
    out = json.loads(p2.stdout.strip().splitlines()[-1])
    assert out["result"]["stop"] == "end_confirmed"
    assert out["stats"]["recovered_from_spool"] == 1 and out["stats"]["duplicates"] == 1
    integ = check_integrity(path, run)
    assert integ["dup_trusted"] == 0 and integ["observations"] == N and integ["next_pos"] == N // PAGE


# ------------------------------------------------------------ critério 5: [E1] reexecutado
E1_CHILD = r"""
import os, sys, time, uuid
sys.path.insert(0, sys.argv[1])
from instat.jobstore.store import JobStore
path, me = sys.argv[2], sys.argv[3]
m = JobStore(path, ttl=1.2, busy_timeout=0.2, create=False)
while True:
    try:
        tok = m.acquire("A", me, now=None)
        if tok is None:
            time.sleep(0.02); continue
        st, run = m.claim("J", tok, now=None)
        if st != "ok":
            m.stop_all = True; time.sleep(0.05); continue
        cur, pos = None, 0
        for _ in range(10000):
            if m.heartbeat(tok, now=None) != "renewed":
                break
            r = m.commit(tok, "J", run, attempt_id=str(uuid.uuid4()), pos=pos, cursor_in=cur,
                         cursor_out=f"{me}-{pos}", members=[(None, f"u{me[1:]}x{pos}")], now=None)
            if r != "committed:trusted":
                break
            cur, pos = f"{me}-{pos}", pos + 1
            time.sleep(0.01)
    except Exception as e:
        if "locked" not in str(e):
            raise
        time.sleep(0.01)
"""


def test_e1_eight_processes_one_hundred_kills_real_lock_waits(tmp_path):
    path = str(tmp_path / "jobs.db")
    m = JobStore(path, ttl=1.2, busy_timeout=0.2)
    m.add_account("A")
    m.create_job("J")
    # limite de execuções não pode encerrar a disputa: o teste mede posse, não o limite
    m.c.execute("UPDATE jobs SET attempts=-1000000")
    m.c.close()
    procs = {}

    def spawn(i):
        procs[i] = subprocess.Popen([sys.executable, "-c", E1_CHILD, ROOT, path, f"p{i}"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    for i in range(8):
        spawn(i)
    rng = random.Random(42)
    kills = 0
    deadline = time.time() + 240
    while kills < 100 and time.time() < deadline:
        time.sleep(rng.uniform(0.05, 0.25))
        i = rng.randrange(8)
        if procs[i].poll() is not None:
            err = procs[i].stderr.read()
            pytest.fail(f"processo p{i} terminou sozinho: {err[-500:]}")
        procs[i].kill()
        procs[i].wait()
        kills += 1
        spawn(i)
    time.sleep(1.5)
    for p in procs.values():
        p.kill()
        p.wait()
    assert kills == 100
    c = sqlite3.connect(path)
    runs = c.execute("SELECT run_id, started_at, ended_at, next_pos FROM runs ORDER BY run_id").fetchall()
    assert len(runs) >= 5, "disputa não produziu execuções suficientes"
    for run_id, started, ended, next_pos in runs:
        trusted = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND quality='trusted'", (run_id,)).fetchone()[0]
        assert trusted == next_pos
        if ended is not None:
            late = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND committed_at>?", (run_id, ended)).fetchone()[0]
            assert late == 0, f"run {run_id}: página gravada depois do fim da execução (sem posse)"
    # execuções encerradas não se sobrepõem a páginas de outra execução do mesmo job
    spans = [(r[0], r[1], r[2]) for r in runs if r[2] is not None]
    for run_id, s, e in spans:
        overlap = c.execute("SELECT COUNT(*) FROM pages WHERE run_id<>? AND committed_at>? AND committed_at<?",
                            (run_id, s, e)).fetchone()[0]
        own = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=?", (run_id,)).fetchone()[0]
        assert not (overlap and own), f"run {run_id}: escrita concorrente de outra execução durante a posse"
    assert c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM pages WHERE quality='trusted' "
                     "GROUP BY run_id, pos HAVING COUNT(*)>1)").fetchone()[0] == 0
    c.close()
