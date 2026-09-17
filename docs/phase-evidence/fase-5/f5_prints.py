"""F5 passo 7 — dados dos prints a partir da implementação real (sem Android, sem Instagram).

Gera prints.json; f5_render.py transforma em PNG.
"""
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

from instat.jobstore.heartbeat import Heartbeat  # noqa: E402
from instat.jobstore.spool import AttemptSpool  # noqa: E402
from instat.jobstore.store import JobStore  # noqa: E402
from instat.jobstore.worker import CursorWorker, InjectedCrash  # noqa: E402

OUT = {}
N, PAGE = 300, 50
KNOWN = [f"membro_{i:03d}" for i in range(N)]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        s = int(parse_qs(urlparse(self.path).query).get("max_id", ["0"])[0])
        nxt = s + PAGE
        b = json.dumps({"users": [{"pk": str(s + i), "username": u} for i, u in enumerate(KNOWN[s:nxt])],
                        "next_max_id": str(nxt) if nxt < N else None}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def fetch(cursor, timeout):
    d = json.loads(urlopen(f"{BASE}/f/" + (f"?max_id={cursor}" if cursor else ""), timeout=timeout).read())
    return {"members": [[u["pk"], u["username"]] for u in d["users"]], "cursor_out": d["next_max_id"],
            "end_marker": d["next_max_id"] is None, "page_ok": True}


def db_state(path, run, spool):
    c = sqlite3.connect(path)
    pages = [dict(zip(("pos", "attempt", "quality", "n"), (r[0], r[1][:8], r[2], r[3]))) for r in c.execute(
        "SELECT pos, attempt_id, quality, n_items FROM pages WHERE run_id=? ORDER BY page_id", (run,))]
    next_pos = c.execute("SELECT next_pos FROM runs WHERE run_id=?", (run,)).fetchone()[0]
    c.close()
    pend = [{"attempt": e["attempt_id"][:8], "pos": e["pos"], "com_resposta": e["response"] is not None}
            for e in AttemptSpool(spool).pending(run)]
    return {"pages": pages, "next_pos": next_pos, "spool_pendente": pend}


# ------------------------------------------------------------ 01/02 crash e retomada
d = tempfile.mkdtemp()
path, spool = os.path.join(d, "jobs.db"), os.path.join(d, "spool")
m = JobStore(path, ttl=600, busy_timeout=5)
m.add_account("conta_teste_fake")
m.create_job("J1")
tok = m.acquire("conta_teste_fake", "worker-1", now=None)
_, run = m.claim("J1", tok, now=None)


def crash_at(point, pos):
    if (point, pos) == ("after_commit", 3):
        raise InjectedCrash("queda simulada após o commit da posição 3, antes do ack do spool")


w1 = CursorWorker(m, AttemptSpool(spool), fetch, tok, "J1", run, request_timeout_s=5, fault=crash_at)
try:
    w1.run()
except InjectedCrash as e:
    OUT["antes_crash"] = {"falha": str(e), "stats_worker1": w1.stats, **db_state(path, run, spool)}
m.close()
m2 = JobStore(path, ttl=600, busy_timeout=5, create=False)
w2 = CursorWorker(m2, AttemptSpool(spool), fetch, tok, "J1", run, request_timeout_s=5)
res = w2.run()
rr = m2.run_result(run)
OUT["retomada"] = {"resultado_worker2": res, **db_state(path, run, spool),
                   "run_result": {k: rr[k] for k in ("status", "progress_state", "end_evidence", "collected",
                                                     "suspect_open", "next_pos")},
                   "membros_iguais_ao_conjunto": rr["members"] == KNOWN}
ver = m2.verify_backup(path)
OUT["integridade_crash"] = ver

# ------------------------------------------------------------ 04 heartbeat e motivos
d4 = tempfile.mkdtemp()
p4 = os.path.join(d4, "hb.db")
s4 = JobStore(p4, ttl=0.9, busy_timeout=0.15)
for a in ("conta_a", "conta_b"):
    s4.add_account(a)
s4.create_job("JH")
tok_a = s4.acquire("conta_a", "w1", now=None)
_, run_a = s4.claim("JH", tok_a, now=None)
hb_a = Heartbeat(p4, tok_a, ttl=0.9, busy_timeout=0.15).start()
time.sleep(0.7)
ok_before = s4.can_send(tok_a, "JH", run_a, now=None)
s4.set_auth("conta_a", "needs_attention", now=None)
ok_right_after = s4.can_send(tok_a, "JH", run_a, now=None)
time.sleep(0.6)
hb_a.stop()
tok_b = s4.acquire("conta_b", "w2", now=None)
hb_b = Heartbeat(p4, tok_b, ttl=0.9, busy_timeout=0.15).start()
time.sleep(0.4)
s4.c.execute("UPDATE accounts SET lease_gen=lease_gen+1, lease_owner='outro' WHERE account='conta_b'")
time.sleep(0.6)
hb_b.stop()
s4.request_cancel("JH", now=None)
OUT["heartbeat"] = {
    "conta_a": {"renovacoes": hb_a.renewals, "status_final": hb_a.status, "falhas": hb_a.failures},
    "conta_b": {"renovacoes": hb_b.renewals, "status_final": hb_b.status, "falhas": hb_b.failures},
    "can_send_antes_da_revogacao": list(ok_before),
    "can_send_logo_apos_revogacao": list(ok_right_after),
    "can_send_depois_de_expirar": list(s4.can_send(tok_a, "JH", run_a, now=None)),
    "reivindicacao_conta_revogada": s4.claim("JH", tok_a, now=None)[0],
    "liberacao_sem_sessao_validada": s4.release_account("conta_a", "operador", False, now=None),
}

# ------------------------------------------------------------ 05 progresso sem cursor (sequências sintéticas)
NOW = 1000.0


def scan_job(name, screens, counter=None, marker_last=False, restart_after=None):
    dd = tempfile.mkdtemp()
    s = JobStore(os.path.join(dd, "s.db"))
    s.add_account("A")
    s.create_job("JS", kind="scan")
    t = s.acquire("A", "w", NOW)
    _, r = s.claim("JS", t, NOW)
    rows = []
    for i, names in enumerate(screens):
        if restart_after is not None and i == restart_after:
            s.restart_segment(t, "JS", r, NOW + i + 0.5)
            rows.append({"tela": "reinício do app", "resultado": "novo segmento"})
        pos = s.resume_point("JS", r)[1]
        res = s.commit(t, "JS", r, attempt_id=f"{name}-{i}", pos=pos, cursor_in=None, cursor_out=None,
                       members=[(None, n) for n in names], screen_hash="h" + "-".join(names), now=NOW + 1 + i,
                       end_marker=marker_last and i == len(screens) - 1,
                       counter=counter if i == len(screens) - 1 else None)
        st = s.c.execute("SELECT progress_state, end_evidence, rounds_without_new, stuck_rounds, continuity_gaps, "
                         "segment FROM runs WHERE run_id=?", (r,)).fetchone()
        rows.append({"tela": ",".join(names), "resultado": res, "estado": st[0], "evidencia": st[1],
                     "sem_novos": st[2], "repetidas": st[3], "lacunas": st[4], "segmento": st[5]})
        if st[0] in ("end_confirmed", "end_unknown"):
            break
    rr = s.run_result(r)
    return {"telas": rows, "status": rr["status"], "motivos": rr["reasons"]}


base = [["ana", "bia"], ["bia", "caio"], ["caio", "dani"]]
OUT["scan"] = {
    "sem_evidencia_de_fim": scan_job("u6a", base + [["dani", "eva"], ["eva", "ana"], ["ana", "bia"], ["bia", "caio"]]),
    "contador_exato_compativel": scan_job("u6b", base + [["dani", "ana"], ["ana", "bia"], ["bia", "caio"]],
                                          counter=["exact", 4]),
    "tela_repetida": scan_job("t8b", [["ana", "bia"], ["ana", "bia"], ["ana", "bia"], ["ana", "bia"]]),
    "lacuna_de_continuidade": scan_job("u3", [["ana", "bia"], ["caio", "dani"], ["dani", "eva"]], marker_last=True),
}

# ------------------------------------------------------------ 06 seleção e histórico (N01 + N02)
d6 = tempfile.mkdtemp()
s6 = JobStore(os.path.join(d6, "v.db"))
s6.add_account("A")
s6.create_job("JV")


def one_run(t0, names, marker):
    t = s6.acquire("A", f"w{t0}", t0)
    st, r = s6.claim("JV", t, t0)
    s6.commit(t, "JV", r, attempt_id=f"a{t0}", pos=0, cursor_in=None, cursor_out=None if marker else "c",
              members=[(None, n) for n in names], end_marker=marker, now=t0 + 1)
    if not marker:
        s6.stop_run(t, "JV", r, "technical_error", t0 + 2)
    return r


r1 = one_run(1000.0, ["ana", "bruno"], True)
s6.requeue("JV", by="operador", now=1100.0)
r2 = one_run(1200.0, ["ana", "carla"], True)
s6.requeue("JV", by="operador", now=1300.0)
r3 = one_run(1400.0, ["ana"], False)
v = s6.job_view("JV")
OUT["job_view"] = {"execucoes": {r1: "ana,bruno (fim confirmado)", r2: "ana,carla (fim confirmado)",
                                 r3: "ana (falha técnica, parcial)"},
                   "view": v, "run_results": {r: {k: s6.run_result(r)[k] for k in ("status", "members", "reasons")}
                                              for r in (r1, r2, r3)}}

# ------------------------------------------------------------ 07 backup online
d7 = tempfile.mkdtemp()
p7 = os.path.join(d7, "b.db")
s7 = JobStore(p7, ttl=3.0, busy_timeout=0.5)
s7.add_account("A")
s7.create_job("JB")
# carga de volume (inserção direta, rotulada): torna a cópia mensurável
s7.c.execute("BEGIN")
s7.c.executemany("INSERT INTO members(target, list_type, member_pk, username, first_seen_at, last_seen_at) "
                 "VALUES('carga','followers',?,?,0,0)", ((str(i), f"carga_{i:07d}") for i in range(400_000)))
s7.c.execute("COMMIT")
tok7 = s7.acquire("A", "w1", now=None)
_, run7 = s7.claim("JB", tok7, now=None)
hb7 = Heartbeat(p7, tok7, ttl=3.0, busy_timeout=0.5, interval=0.05)
stop = threading.Event()
writes = []


def writer():
    w = JobStore(p7, ttl=3.0, busy_timeout=0.5, create=False)
    pos, cur = 0, None
    while not stop.is_set():
        r = w.commit(tok7, "JB", run7, attempt_id=f"w{pos}", pos=pos, cursor_in=cur, cursor_out=f"c{pos}",
                     members=[(None, f"user{pos}_{i}") for i in range(20)], now=None)
        writes.append((time.time(), r))
        pos, cur = pos + 1, f"c{pos}"
        time.sleep(0.01)


th = threading.Thread(target=writer)
hb7.start()
th.start()
time.sleep(0.5)
t_b0 = time.time()
online = s7.backup_online(os.path.join(d7, "copia.db"), deadline_s=10)
t_b1 = time.time()
time.sleep(0.3)
stop.set()
th.join()
renew_before = hb7.renewals
hb7.stop()
not_drained = s7.backup_maintenance(os.path.join(d7, "manut1.db"))
s7.stop_run(tok7, "JB", run7, "technical_error", now=None)
drained = s7.backup_maintenance(os.path.join(d7, "manut2.db"))
src_pages = sqlite3.connect(p7).execute("SELECT COUNT(*) FROM pages").fetchone()[0]
s7.c.execute("BEGIN IMMEDIATE")
try:
    s7.backup_from_connection(s7.c, os.path.join(d7, "x.db"))
    misuse = "permitido (ERRO)"
except Exception as e:  # noqa: BLE001
    misuse = type(e).__name__
s7.c.execute("ROLLBACK")
OUT["backup"] = {
    "tamanho_banco_mb": round(os.path.getsize(p7) / 1e6, 1),
    "online": {k: online[k] for k in ("status", "elapsed", "steps")}, "verificacao": online["verification"],
    "escritas_durante_copia": sum(1 for t, _ in writes if t_b0 <= t <= t_b1),
    "escritas_total": len(writes), "escritas_nao_trusted": sum(1 for _, r in writes if r != "committed:trusted"),
    "heartbeat": {"renovacoes": renew_before, "falhas": hb7.failures, "status": hb7.status},
    "paginas_origem_ao_final": src_pages,
    "manutencao_nao_drenada": not_drained, "manutencao_drenada": {k: drained.get(k) for k in (
        "status", "digest_equal", "compared_with_origin_in_maintenance_window")},
    "backup_com_transacao_aberta": misuse,
}

srv.shutdown()
with open(os.path.join(HERE, "prints.json"), "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)
print(json.dumps({k: (v if k in ("heartbeat",) else "ok") for k, v in OUT.items()}, ensure_ascii=False))
