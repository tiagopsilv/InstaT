"""Cenários [E3] portados de docs/design-validation/v13.7/test_v137.py (F5).

Portados SEM mudar expectativas. Mudanças de harness, todas registradas em
docs/phase-evidence/fase-5/README.md:
  - HERE aponta para a raiz do repositório (o subprocesso do C01 importa
    instat.jobstore.store);
  - C01 usa busy_timeout=5.0 (era 10.0): a §6.3.12 exige busy_timeout <= ttl/6
    e o construtor da implementação valida isso (ttl padrão 30 s);
  - runner main() removido (pytest em tests/jobstore/test_e3.py).
"""
import datetime
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NOW = 1000.0
TESTS = []


class ApiAusente(Exception):
    pass


def test(tid, title):
    def deco(fn):
        TESTS.append((tid, title, fn))
        return fn
    return deco


# ------------------------------------------------------------------ utilitários
def fresh(M, **kw):
    d = tempfile.mkdtemp(prefix="v137_")
    p = os.path.join(d, "db.sqlite")
    return M(p, **kw), p


def reopen(M, path, **kw):
    try:
        return M(path, create=False, **kw)
    except TypeError as e:
        raise ApiAusente(f"construtor sem create=False ({e})")


def need(m, name):
    if not hasattr(m, name):
        raise ApiAusente(f"método {name} ausente")
    return getattr(m, name)


def set_auth(m, acct, state, now, restricted_until=None):
    try:
        return m.set_auth(acct, state, now=now, restricted_until=restricted_until)
    except TypeError:
        if restricted_until is not None:
            raise ApiAusente("set_auth sem restricted_until/now")
        return m.set_auth(acct, state)


def requeue(m, job, now, by="tiago"):
    try:
        r = m.requeue(job, by=by, now=now)
    except TypeError:
        r = m.requeue(job, now)
    return "ok" if r is None else r


def sql(m, q, args=()):
    try:
        return m.c.execute(q, args).fetchall()
    except sqlite3.OperationalError as e:
        if "no such" in str(e):
            raise ApiAusente(f"schema sem suporte: {e}")
        raise


def run_field(m, run, col):
    return sql(m, f"SELECT {col} FROM runs WHERE run_id=?", (run,))[0][0]


def run_state(m, job, run):
    if hasattr(m, "run_result"):
        return m.run_result(run)
    s = m.job_state(job, run)
    s = dict(s)
    s["status"] = "complete" if s.get("can_complete") else "partial"
    s["members"] = None
    return s


def job_view(m, job):
    return need(m, "job_view")(job)


def selected_members(m, job):
    if hasattr(m, "job_view"):
        return set(m.job_view(job)["selected_members"])
    return set(m.result_members(job))


def job_with_run(m, job="J", acct="A", owner="w1", now=NOW, kind="cursor"):
    m.add_account(acct)
    tok = m.acquire(acct, owner, now)
    m.create_job(job, kind=kind)
    st, run = m.claim(job, tok, now)
    assert st == "ok", st
    return tok, run


def nxt(m, job, run):
    return run_field(m, run, "next_pos")


def mem(*names):
    return [(None, n) for n in names]


def commit(m, tok, job, run, **kw):
    kw.setdefault("cursor_in", None)
    kw.setdefault("cursor_out", None)
    return m.commit(tok, job, run, **kw)


def scan_screens(m, tok, job, run, screens, t0):
    out = []
    for i, (label, members, extra) in enumerate(screens):
        out.append(commit(m, tok, job, run, attempt_id=f"{run}-{label}", pos=nxt(m, job, run),
                          members=mem(*members), screen_hash=label, now=t0 + i, **extra))
    return out


# ================================================================== existentes revisados
@test("T01", "repetição exata do mesmo commit com cursor NULL não duplica")
def t01(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    kw = dict(attempt_id="att-1", pos=0, members=[], now=NOW + 1)
    r1, r2 = commit(m, tok, "J", run, **kw), commit(m, tok, "J", run, **kw)
    n = sql(m, "SELECT COUNT(*) FROM pages")[0][0]
    return n == 1 and r2.startswith("duplicate"), f"commit1={r1} | commit2={r2} | páginas={n}"


@test("T02", "suspect seguida de nova leitura trusted na mesma posição")
def t02(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    commit(m, tok, "J", run, attempt_id="a0", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 1)
    r1 = commit(m, tok, "J", run, attempt_id="a1", pos=1, cursor_in="c1", cursor_out="c2", members=[], now=NOW + 2)
    r2 = commit(m, tok, "J", run, attempt_id="a2", pos=1, cursor_in="c1", cursor_out="c2", members=mem("u1"), now=NOW + 3)
    cur = run_field(m, run, "trusted_cursor")
    return r2 == "committed:trusted" and cur == "c2", f"{r1} | {r2} | cursor={cur!r}"


@test("T03", "contas com a mesma geração coexistem na mesma posição lógica")
def t03(M):
    m, _ = fresh(M)
    m.add_account("A")
    m.add_account("B")
    ta = m.acquire("A", "wA", NOW)
    m.create_job("J")
    _, runA = m.claim("J", ta, NOW)
    commit(m, ta, "J", runA, attempt_id="a0", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 1)
    commit(m, ta, "J", runA, attempt_id="a1", pos=1, cursor_in="c1", cursor_out="c2", members=[], now=NOW + 2)
    m.expire("A", NOW + 100)
    tb = m.acquire("B", "wB", NOW + 100)
    st, runB = m.claim("J", tb, NOW + 100)
    ci, pos = m.resume_point("J", runB)
    rB = commit(m, tb, "J", runB, attempt_id="b1", pos=pos, cursor_in=ci, cursor_out="zz", members=mem("u1"), now=NOW + 101)
    both = sql(m, """SELECT COUNT(*) FROM pages p JOIN runs r ON r.run_id=p.run_id
                     WHERE r.job_id='J' AND p.pos=0 AND p.quality='trusted'""")[0][0]
    ok = ta[2] == tb[2] and st == "ok" and rB == "committed:trusted" and both == 2
    return ok, f"gerações=({ta[2]},{tb[2]}) | claim B={st} | commit B={rB} | trusted em pos 0={both}"


@test("T04", "reivindicação sem concessão válida é rejeitada")
def t04(M):
    m, _ = fresh(M)
    m.create_job("J")
    st1, _ = m.claim("J", ("fantasma", "wX", 1), NOW)
    m.add_account("E")
    te = m.acquire("E", "wE", NOW)
    m.create_job("J2")
    m.expire("E", NOW + 1)
    st2, _ = m.claim("J2", te, NOW + 1)
    return st1 != "ok" and st2 != "ok", f"inexistente={st1} | expirada={st2}"


@test("T05", "restrição com requisição em voo: suspect, sem nova operação")
def t05(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    sent, _ = m.can_send(tok, "J", run, NOW + 1)
    set_auth(m, "A", "needs_attention", NOW + 1.5)
    r = commit(m, tok, "J", run, attempt_id="a0", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 2)
    after, why = m.can_send(tok, "J", run, NOW + 3)
    hb = m.heartbeat(tok, NOW + 3)
    ok = sent and r == "committed:suspect" and not after and why == "account_restricted" and hb == "account_restricted"
    return ok, f"enviou={sent} | commit={r} | nova={after}/{why} | heartbeat={hb}"


@test("T06", "perda de posse antes do commit: nada gravado")
def t06(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    m.expire("A", NOW + 50)
    hb = m.heartbeat(tok, NOW + 50)
    r = commit(m, tok, "J", run, attempt_id="a0", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 50)
    n = sql(m, "SELECT COUNT(*) FROM pages")[0][0]
    return r.startswith("rejected") and n == 0 and hb == "lease_lost", f"heartbeat={hb} | commit={r} | páginas={n}"


@test("T07", "confiança de outro job não conta no job novo")
def t07(M):
    m, _ = fresh(M)
    ta, r1 = job_with_run(m, job="J1", acct="A", owner="wA")
    commit(m, ta, "J1", r1, attempt_id="j1", pos=0, members=mem("alice"), end_marker=True, now=NOW + 1)
    tb, r2 = job_with_run(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    set_auth(m, "B", "needs_attention", NOW + 2.5)
    commit(m, tb, "J2", r2, attempt_id="j2", pos=0, members=mem("alice"), now=NOW + 3)
    s = selected_members(m, "J2")
    return "alice" not in s, f"selecionados J2={sorted(s)}"


@test("T08a", "UI: estados distintos e retry exato sem dupla contagem")
def t08a(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m, job="JU", kind="scan")
    s0 = run_field(m, run, "progress_state")
    kw = dict(attempt_id="r0", pos=0, members=mem("a", "b"), screen_hash="h0", now=NOW + 1)
    commit(m, tok, "JU", run, **kw)
    commit(m, tok, "JU", run, **kw)
    s1 = run_field(m, run, "progress_state")
    tp = sql(m, "SELECT COUNT(*) FROM pages WHERE run_id=? AND quality='trusted'", (run,))[0][0]
    return s0 == "not_started" and s1 == "in_progress" and tp == 1, f"{s0} → {s1} | páginas trusted={tp}"


@test("T08b", "UI: tela repetida → fim desconhecido")
def t08b(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", run, [("h0", ["a", "b"], {})], NOW + 1)
    for i in range(3):
        commit(m, tok, "JU", run, attempt_id=f"s{i}", pos=nxt(m, "JU", run), members=mem("a", "b"),
               screen_hash="h0", now=NOW + 2 + i)
    st = run_field(m, run, "progress_state")
    return st == "end_unknown", f"estado={st}"


def _t08c(M, with_marker):
    m, _ = fresh(M)
    tok, run1 = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", run1, [("k0", ["a", "b"], {}), ("k1", ["b", "c"], {})], NOW + 1)
    m.expire("A", NOW + 100)
    tok2 = m.acquire("A", "w2", NOW + 100)
    st, run2 = m.claim("JU", tok2, NOW + 100)
    scan_screens(m, tok2, "JU", run2, [(f"rp{i}", ["a", "b"], {}) for i in range(4)], NOW + 101)
    during = run_field(m, run2, "progress_state")
    tail = [("rc", ["b", "c", "d"], {}), ("e0", ["c", "d"], {}), ("e1", ["c", "d"], {}),
            ("e2", ["c", "d"], {"end_marker": with_marker})]
    scan_screens(m, tok2, "JU", run2, tail, NOW + 110)
    return st, during, run_field(m, run2, "progress_state")


@test("T08c", "UI: retomada; fim só com evidência positiva")
def t08c(M):
    st, during, final_marker = _t08c(M, True)
    _, _, final_no_marker = _t08c(M, False)
    ok = st == "ok" and during == "in_progress" and final_marker == "end_confirmed" and final_no_marker == "end_unknown"
    return ok, f"claim={st} | releitura={during} | com marcador={final_marker} | sem marcador={final_no_marker}"


@test("T10", "promoção sem pk→pk preserva proveniência")
def t10(M):
    m, _ = fresh(M)
    ta, r1 = job_with_run(m, job="J1", acct="A", owner="wA")
    commit(m, ta, "J1", r1, attempt_id="j1", pos=0, members=[(None, "bob")], now=NOW + 1)
    tb, r2 = job_with_run(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    commit(m, tb, "J2", r2, attempt_id="j2", pos=0, members=[("pk9", "bob")], now=NOW + 3)
    a, b = selected_members(m, "J1"), selected_members(m, "J2")
    n = sql(m, "SELECT COUNT(*) FROM members")[0][0]
    return "bob" in a and "bob" in b and n == 1, f"J1={sorted(a)} | J2={sorted(b)} | membros={n}"


@test("T10b", "fusão de duas linhas com auditoria")
def t10b(M):
    m, _ = fresh(M)
    t1, r1 = job_with_run(m, job="J1", acct="A", owner="wA")
    commit(m, t1, "J1", r1, attempt_id="j1", pos=0, members=[("pk7", "carol")], now=NOW + 1)
    t2, r2 = job_with_run(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    commit(m, t2, "J2", r2, attempt_id="j2", pos=0, members=[(None, "caroline")], now=NOW + 3)
    t3, r3 = job_with_run(m, job="J3", acct="C", owner="wC", now=NOW + 4)
    commit(m, t3, "J3", r3, attempt_id="j3", pos=0, members=[("pk7", "caroline")], now=NOW + 5)
    n = sql(m, "SELECT COUNT(*) FROM members")[0][0]
    merges = sql(m, "SELECT COUNT(*) FROM member_merges")[0][0]
    j2 = selected_members(m, "J2")
    return n == 1 and merges == 1 and "caroline" in j2, f"membros={n} | fusões={merges} | J2={sorted(j2)}"


@test("T11", "suspeita resolvida por releitura; job completa com marcador")
def t11(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    commit(m, tok, "J", run, attempt_id="a0", pos=0, members=[], now=NOW + 1)
    r = commit(m, tok, "J", run, attempt_id="a1", pos=0, members=mem("u0"), end_marker=True,
               counter=("exact", 1), now=NOW + 2)
    s = run_state(m, "J", run)
    ok = s["suspect_open"] == 0 and s["status"] == "complete"
    return ok, f"releitura={r} | suspect_open={s['suspect_open']} | status={s['status']}"


@test("T11b", "backend com cursor: cursor ausente sem sinal de fim não é fim")
def t11b(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    commit(m, tok, "J", run, attempt_id="a0", pos=0, members=mem("u0"), now=NOW + 1)
    st = run_field(m, run, "progress_state")
    return st == "end_unknown", f"estado={st}"


@test("T12", "após restrição, nova execução só com requeue manual")
def t12(M):
    m, _ = fresh(M)
    ta, r1 = job_with_run(m, acct="A", owner="wA")
    set_auth(m, "A", "needs_attention", NOW + 0.5)
    m.stop_run(ta, "J", r1, "account_restricted", NOW + 1)
    m.add_account("B")
    tb = m.acquire("B", "wB", NOW + 2)
    st1, _ = m.claim("J", tb, NOW + 2)
    rq = requeue(m, "J", NOW + 3)
    st2, _ = m.claim("J", tb, NOW + 4)
    return st1 != "ok" and rq == "ok" and st2 == "ok", f"sem requeue={st1} | requeue={rq} | depois={st2}"


@test("X01", "mesmo attempt_id com conteúdo diferente é erro")
def x01(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    r1 = commit(m, tok, "J", run, attempt_id="att", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 1)
    r2 = commit(m, tok, "J", run, attempt_id="att", pos=0, cursor_out="c1", members=mem("outro"), now=NOW + 2)
    return r1 == "committed:trusted" and r2.startswith("error"), f"{r1} | {r2}"


@test("X02", "segunda leitura trusted da mesma posição é rejeitada")
def x02(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    r1 = commit(m, tok, "J", run, attempt_id="a1", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 1)
    r2 = commit(m, tok, "J", run, attempt_id="a2", pos=0, cursor_out="c1", members=mem("u0"), now=NOW + 2)
    return r1 == "committed:trusted" and r2.startswith("rejected"), f"{r1} | {r2}"


# ================================================================== resultado e proveniência
def _complete_run(m, tok, job, run, members, t, counter=None, label="p"):
    return commit(m, tok, job, run, attempt_id=f"{run}-{label}", pos=nxt(m, job, run),
                  members=mem(*members), end_marker=True, counter=counter, now=t)


@test("N01", "run 1 Ana/Bruno, run 2 Ana/Carla: histórico, seleção e sem 'unfollow'")
def n01(M):
    m, _ = fresh(M)
    tok, r1 = job_with_run(m)
    _complete_run(m, tok, "J", r1, ["ana", "bruno"], NOW + 1)
    rq = requeue(m, "J", NOW + 2)
    st, r2 = m.claim("J", tok, NOW + 3)
    if st != "ok":
        return False, f"claim da run 2 rejeitado: {st} (requeue={rq})"
    _complete_run(m, tok, "J", r2, ["ana", "carla"], NOW + 4)
    v = job_view(m, "J")
    rr1 = need(m, "run_result")(r1)
    ok = (st == "ok" and v["selected_run_id"] == r2 and set(v["selected_members"]) == {"ana", "carla"}
          and set(v["history_observed"]) == {"ana", "bruno", "carla"}
          and set(rr1["members"]) == {"ana", "bruno"}
          and not any("remov" in k or "unfollow" in k for k in v))
    return ok, (f"requeue={rq} claim={st} | selecionada={v['selected_run_id']} {sorted(v['selected_members'])} "
                f"| histórico={sorted(v['history_observed'])} | run1={sorted(rr1['members'])}")


@test("N02", "última tentativa falha após run válida: seleção e datas separadas")
def n02(M):
    m, _ = fresh(M)
    tok, r1 = job_with_run(m)
    _complete_run(m, tok, "J", r1, ["ana", "bruno"], NOW + 1)
    requeue(m, "J", NOW + 2)
    _, r2 = m.claim("J", tok, NOW + 3)
    commit(m, tok, "J", r2, attempt_id="x", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 4)
    m.stop_run(tok, "J", r2, "technical_error", NOW + 5)
    v = job_view(m, "J")
    ok = (v["selected_run_id"] == r1 and v["selected_run_status"] == "complete" and v["selected_run_at"] == NOW + 1
          and v["last_attempt_run_id"] == r2 and v["last_attempt_stop_reason"] == "technical_error"
          and v["last_attempt_at"] == NOW + 5)
    return ok, json.dumps({k: v[k] for k in ("selected_run_id", "selected_run_status", "selected_run_at",
                                             "last_attempt_run_id", "last_attempt_stop_reason", "last_attempt_at")})


@test("N03", "membro confiável no histórico, só suspeito na run atual")
def n03(M):
    m, _ = fresh(M)
    tok, r1 = job_with_run(m)
    _complete_run(m, tok, "J", r1, ["ana", "bruno"], NOW + 1)
    requeue(m, "J", NOW + 2)
    _, r2 = m.claim("J", tok, NOW + 3)
    commit(m, tok, "J", r2, attempt_id="p0", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 4)
    set_auth(m, "A", "needs_attention", NOW + 4.5)
    commit(m, tok, "J", r2, attempt_id="p1", pos=1, cursor_in="c1", cursor_out="c2", members=mem("bruno"), now=NOW + 5)
    m.stop_run(tok, "J", r2, "account_restricted", NOW + 6)
    rr2 = need(m, "run_result")(r2)
    v = job_view(m, "J")
    ok = set(rr2["members"]) == {"ana"} and "bruno" in v["history_observed"] and v["selected_run_id"] == r1
    return ok, f"run2={sorted(rr2['members'])} | histórico={sorted(v['history_observed'])} | selecionada={v['selected_run_id']}"


@test("N04", "commit repetido após crash (nova conexão) não duplica nada")
def n04(M):
    m1, path = fresh(M)
    tok, run = job_with_run(m1)
    r1 = commit(m1, tok, "J", run, attempt_id="att-1", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 1)
    m2 = reopen(M, path)
    r2 = commit(m2, tok, "J", run, attempt_id="att-1", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 2)
    pages = sql(m2, "SELECT COUNT(*) FROM pages")[0][0]
    obs = sql(m2, "SELECT COUNT(*) FROM observations")[0][0]
    np_ = run_field(m2, run, "next_pos")
    ok = r1 == "committed:trusted" and r2 == "duplicate:trusted" and pages == 1 and obs == 1 and np_ == 1
    return ok, f"{r1} | após crash={r2} | páginas={pages} observações={obs} next_pos={np_}"


@test("N05", "canônico: variação de caixa/espaço é a mesma tentativa; conteúdo diferente é erro")
def n05(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    r1 = commit(m, tok, "J", run, attempt_id="a", pos=0, cursor_out="c1", members=[(None, "Ana ")], now=NOW + 1)
    r2 = commit(m, tok, "J", run, attempt_id="a", pos=0, cursor_out="c1", members=[(None, "ana")], now=NOW + 2)
    r3 = commit(m, tok, "J", run, attempt_id="a", pos=0, cursor_out="c1", members=[(None, "carla")], now=NOW + 3)
    return r2.startswith("duplicate") and r3.startswith("error"), f"{r1} | canônico igual={r2} | diferente={r3}"


@test("N07", "fusão não transfere confiança entre execuções")
def n07(M):
    m, _ = fresh(M)
    t1, r1 = job_with_run(m, job="J1", acct="A", owner="wA")
    commit(m, t1, "J1", r1, attempt_id="j1", pos=0, members=[("pk7", "carol")], end_marker=True, now=NOW + 1)
    t2, r2 = job_with_run(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    set_auth(m, "B", "needs_attention", NOW + 2.5)
    commit(m, t2, "J2", r2, attempt_id="j2", pos=0, members=[(None, "caroline")], now=NOW + 3)
    t3, r3 = job_with_run(m, job="J3", acct="C", owner="wC", now=NOW + 4)
    commit(m, t3, "J3", r3, attempt_id="j3", pos=0, members=[("pk7", "caroline")], end_marker=True, now=NOW + 5)
    j2, j1 = selected_members(m, "J2"), selected_members(m, "J1")
    merges = sql(m, "SELECT COUNT(*) FROM member_merges")[0][0]
    return not j2 and "caroline" in j1 and merges == 1, f"J2={sorted(j2)} | J1={sorted(j1)} | fusões={merges}"


@test("N08", "attempt_id reutilizado em outra execução é erro, não duplicata")
def n08(M):
    m, _ = fresh(M)
    ta, r1 = job_with_run(m, job="J1", acct="A", owner="wA")
    commit(m, ta, "J1", r1, attempt_id="att-x", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 1)
    tb, r2 = job_with_run(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    r = commit(m, tb, "J2", r2, attempt_id="att-x", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 3)
    return r.startswith("error"), f"resultado={r}"


@test("N09", "run parcial não completa por união histórica")
def n09(M):
    m, _ = fresh(M)
    tok, r1 = job_with_run(m)
    ten = [f"m{i}" for i in range(10)]
    _complete_run(m, tok, "J", r1, ten, NOW + 1, counter=("exact", 10))
    requeue(m, "J", NOW + 2)
    st, r2 = m.claim("J", tok, NOW + 3)
    if st != "ok":
        return False, f"claim da run 2 rejeitado: {st}"
    _complete_run(m, tok, "J", r2, ["m0"], NOW + 4, counter=("exact", 10))
    s2 = run_state(m, "J", r2)
    v = job_view(m, "J")
    return s2["status"] == "partial" and v["selected_run_id"] == r1, f"run2={s2['status']} | selecionada={v['selected_run_id']}"


# ================================================================== UI
def _two_run_scan(M, run1_screens):
    m, _ = fresh(M)
    tok, r1 = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", r1, run1_screens, NOW + 1)
    m.expire("A", NOW + 100)
    tok2 = m.acquire("A", "w2", NOW + 100)
    st, r2 = m.claim("JU", tok2, NOW + 100)
    assert st == "ok", st
    return m, tok2, r2


@test("U01", "inserção isolada durante a releitura não encerra a releitura")
def u01(M):
    m, tok, r2 = _two_run_scan(M, [("k0", ["a", "b"], {}), ("k1", ["b", "c"], {}), ("k2", ["c", "d"], {})])
    scan_screens(m, tok, "JU", r2, [("s0", ["a", "b"], {}), ("s1", ["b", "x", "c"], {})], NOW + 101)
    after_insert = run_field(m, r2, "replay_done")
    scan_screens(m, tok, "JU", r2, [("s2", ["c", "d"], {})], NOW + 110)
    after_cover = run_field(m, r2, "replay_done")
    return after_insert == 0 and after_cover == 1, f"após inserção={after_insert} | após cobertura={after_cover}"


@test("U02", "remoção e reordenação entre telas: sem duplicata nem lacuna")
def u02(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", run, [("h0", ["a", "b", "c"], {}), ("h1", ["c", "b", "d"], {}), ("h2", ["d", "e"], {})], NOW + 1)
    gaps = run_field(m, run, "continuity_gaps")
    n = sql(m, "SELECT COUNT(*) FROM members")[0][0]
    return gaps == 0 and n == 5, f"lacunas={gaps} | membros={n}"


@test("U03", "tela sem sobreposição com a anterior impede completar")
def u03(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", run, [("h0", ["a", "b"], {}), ("h1", ["c", "d"], {}),
                                     ("h2", ["d", "e"], {"end_marker": True})], NOW + 1)
    s = run_state(m, "JU", run)
    gaps = run_field(m, run, "continuity_gaps")
    return gaps == 1 and s["status"] == "partial", f"lacunas={gaps} | status={s['status']}"


@test("U04", "reinício do app abre novo segmento sem consumir execução")
def u04(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", run, [("h0", ["a", "b"], {}), ("h1", ["b", "c"], {})], NOW + 1)
    r = need(m, "restart_segment")(tok, "JU", run, NOW + 5)
    seg, rd, rwn = (run_field(m, run, c) for c in ("segment", "replay_done", "rounds_without_new"))
    attempts = sql(m, "SELECT attempts FROM jobs WHERE job_id='JU'")[0][0]
    ok = r == "ok" and seg == 1 and rd == 0 and rwn == 0 and attempts == 1
    return ok, f"restart={r} | segmento={seg} releitura_concluída={rd} sem_novos={rwn} | execuções={attempts}"


@test("U06", "K telas sem novos: desconhecido sem evidência; confirmado com contador exato compatível")
def u06(M):
    screens = [("h0", ["a", "b"], {}), ("h1", ["b", "c"], {}), ("h2", ["b", "c"], {}),
               ("h3", ["b", "c"], {}), ("h4", ["b", "c"], {})]
    m, _ = fresh(M)
    tok, run = job_with_run(m, job="JU", kind="scan")
    scan_screens(m, tok, "JU", run, screens, NOW + 1)
    no_ev = run_field(m, run, "progress_state")
    m2, _ = fresh(M)
    tok2, run2 = job_with_run(m2, job="JU", kind="scan")
    screens2 = screens[:-1] + [("h4", ["b", "c"], {"counter": ("exact", 3)})]
    scan_screens(m2, tok2, "JU", run2, screens2, NOW + 1)
    with_counter = run_field(m2, run2, "progress_state")
    return no_ev == "end_unknown" and with_counter == "end_confirmed", f"sem evidência={no_ev} | contador exato={with_counter}"


# ================================================================== motivos de parada
@test("D03", "cancelamento: sem nova operação; resposta em voo com qualidade normal")
def d03(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    need(m, "request_cancel")("J", NOW + 1)
    sent, why = m.can_send(tok, "J", run, NOW + 1.5)
    r = commit(m, tok, "J", run, attempt_id="a0", pos=0, cursor_out="c1", members=mem("ana"), now=NOW + 2)
    m.stop_run(tok, "J", run, "cancel_requested", NOW + 3)
    tok2 = m.acquire("A", "w2", NOW + 4)                     # concessão nova: a recusa deve vir da falta de requeue
    st, _ = m.claim("J", tok2, NOW + 4)
    ok = not sent and why == "cancel_requested" and r == "committed:trusted" and st != "ok"
    return ok, f"envio={sent}/{why} | em voo={r} | nova run sem requeue={st}"


@test("D04", "falha técnica: nova execução automática dentro do limite")
def d04(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    m.stop_run(tok, "J", run, "technical_error", NOW + 1)
    tok2 = m.acquire("A", "w2", NOW + 2)
    st, _ = m.claim("J", tok2, NOW + 2)
    attempts = sql(m, "SELECT attempts FROM jobs WHERE job_id='J'")[0][0]
    return st == "ok" and attempts == 2, f"claim={st} | execuções={attempts}"


@test("D05", "worker obsoleto não grava após outra execução assumir")
def d05(M):
    m, _ = fresh(M)
    ta, r1 = job_with_run(m, acct="A", owner="wA")
    m.expire("A", NOW + 50)
    m.add_account("B")
    tb = m.acquire("B", "wB", NOW + 50)
    st, r2 = m.claim("J", tb, NOW + 50)
    x1 = commit(m, ta, "J", r1, attempt_id="old", pos=0, cursor_out="c1", members=mem("u"), now=NOW + 51)
    ta2 = m.acquire("A", "wA", NOW + 52)
    x2 = commit(m, ta2, "J", r1, attempt_id="old2", pos=0, cursor_out="c1", members=mem("u"), now=NOW + 53)
    n = sql(m, "SELECT COUNT(*) FROM pages WHERE run_id=?", (r1,))[0][0]
    return st == "ok" and x1.startswith("rejected") and x2.startswith("rejected") and n == 0, f"B={st} | antigo={x1} | reobtido={x2} | páginas run1={n}"


# ================================================================== limites
def _exhaust_rereads(m, tok, run):
    rs = [commit(m, tok, "J", run, attempt_id="r1", pos=0, members=[], now=NOW + 1),
          commit(m, tok, "J", run, attempt_id="r1", pos=0, members=[], now=NOW + 1),
          commit(m, tok, "J", run, attempt_id="r2", pos=0, members=[], now=NOW + 2),
          commit(m, tok, "J", run, attempt_id="r3", pos=0, members=[], now=NOW + 3),
          commit(m, tok, "J", run, attempt_id="r4", pos=0, members=[], now=NOW + 4)]
    return rs


@test("L01", "duas releituras adicionais por posição; duplicata não consome")
def l01(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    rs = _exhaust_rereads(m, tok, run)
    ok = rs[1].startswith("duplicate") and rs[3] == "committed:suspect" and rs[4].startswith("rejected:limite_de_releituras")
    return ok, " | ".join(rs)


@test("L02", "releituras esgotadas: sem ciclo automático; só requeue manual")
def l02(M):
    m, _ = fresh(M)
    tok, run = job_with_run(m)
    _exhaust_rereads(m, tok, run)
    m.stop_run(tok, "J", run, "reread_limit", NOW + 10)
    tok2 = m.acquire("A", "w2", NOW + 11)
    st1, _ = m.claim("J", tok2, NOW + 11)
    rq = requeue(m, "J", NOW + 12)
    st2, _ = m.claim("J", tok2, NOW + 13)
    return st1 != "ok" and rq == "ok" and st2 == "ok", f"automático={st1} | requeue={rq} | manual={st2}"


@test("L03", "três execuções por job: quarta e requeue rejeitados")
def l03(M):
    m, _ = fresh(M)
    m.add_account("A")
    m.create_job("J")
    results = []
    for i in range(4):
        tok = m.acquire("A", f"w{i}", NOW + 10 * i)
        st, run = m.claim("J", tok, NOW + 10 * i)
        results.append(st)
        if st == "ok":
            m.stop_run(tok, "J", run, "technical_error", NOW + 10 * i + 1)
    rq = requeue(m, "J", NOW + 100)
    ok = results[:3] == ["ok"] * 3 and results[3] != "ok" and rq != "ok"
    return ok, f"claims={results} | requeue={rq}"


@test("L04", "restrição: tempo não libera; liberação manual exige sessão validada")
def l04(M):
    m, _ = fresh(M)
    m.add_account("A")
    set_auth(m, "A", "needs_attention", NOW, restricted_until=NOW + 10)
    t1 = m.acquire("A", "w", NOW + 100)
    rel = need(m, "release_account")
    r1 = rel("A", by="tiago", session_validated=False, now=NOW + 101)
    t2 = m.acquire("A", "w", NOW + 102)
    r2 = rel("A", by="tiago", session_validated=True, now=NOW + 103)
    t3 = m.acquire("A", "w", NOW + 104)
    ok = t1 is None and r1 != "ok" and t2 is None and r2 == "ok" and t3 is not None
    return ok, f"após tempo={t1} | sem validação={r1}/{t2} | validada={r2}/{t3 is not None}"


# ================================================================== backup
def _populate(m, n_pages=150, per_page=20):
    tok, run = job_with_run(m, job="JB", acct="PB", owner="wp", kind="cursor")
    cur = None
    for p in range(n_pages):
        nxt_c = f"c{p + 1}"
        assert m.heartbeat(tok, NOW + p) == "renewed"          # mantém a concessão viva durante a carga
        r = commit(m, tok, "JB", run, attempt_id=f"pb{p}", pos=p, cursor_in=cur, cursor_out=nxt_c,
                   members=mem(*[f"u{p}_{k}" for k in range(per_page)]), now=NOW + p)
        assert r == "committed:trusted", f"carga página {p}: {r}"
        cur = nxt_c
    return tok, run


class _Heartbeat(threading.Thread):
    def __init__(self, M, path, interval=0.02):
        super().__init__(daemon=True)
        setup = reopen(M, path, busy_timeout=2.0)          # conexão só para preparar, na thread principal
        setup.add_account("HB")
        self.tok = setup.acquire("HB", "hb", time.time())
        self.M, self.path = M, path
        self.interval, self.stop, self.results = interval, threading.Event(), []

    def run(self):
        self.m = reopen(self.M, self.path, busy_timeout=2.0)  # conexão própria da thread do heartbeat
        while not self.stop.is_set():
            t0 = time.perf_counter()
            try:
                r = self.m.heartbeat(self.tok, time.time())
            except Exception as e:  # noqa: BLE001
                r = f"erro:{type(e).__name__}"
            self.results.append((r, time.perf_counter() - t0))
            time.sleep(self.interval)


class _Writer(threading.Thread):
    def __init__(self, path):
        super().__init__(daemon=True)
        self.c = sqlite3.connect(path, isolation_level=None, timeout=2.0, check_same_thread=False)
        self.c.execute("CREATE TABLE IF NOT EXISTS bench(x INTEGER)")
        self.stop, self.n, self.errors = threading.Event(), 0, 0

    def run(self):
        while not self.stop.is_set():
            try:
                self.c.execute("INSERT INTO bench VALUES(?)", (self.n,))
                self.n += 1
            except sqlite3.OperationalError:
                self.errors += 1
            time.sleep(0.002)


def _hb_summary(hb):
    fails = [r for r, _ in hb.results if r != "renewed"]
    worst = max((dt for _, dt in hb.results), default=0.0)
    return len(hb.results), len(fails), worst


@test("B01", "backup online (passo único) com heartbeat e escritor ativos")
def b01(M):
    m, path = fresh(M)
    _populate(m)
    backup = need(m, "backup_online")
    hb, wr = _Heartbeat(M, path), _Writer(path)
    hb.start(); wr.start()
    time.sleep(0.1)
    info = backup(path + ".bak", pages=-1, deadline_s=10.0)
    time.sleep(0.1)
    hb.stop.set(); wr.stop.set(); hb.join(); wr.join()
    n, fails, worst = _hb_summary(hb)
    ok = info["status"] == "ok" and info["verification"]["ok"] and fails == 0 and wr.n > 0
    return ok, f"backup={info['status']} verif={info['verification']['ok']} | heartbeats={n} falhas={fails} pior={worst:.3f}s | escritas={wr.n}"


@test("B02", "backup incremental mais lento que o heartbeat: heartbeat não bloqueia; prazo explícito")
def b02(M):
    m, path = fresh(M)
    _populate(m)
    backup = need(m, "backup_online")
    hb, wr = _Heartbeat(M, path, interval=0.02), _Writer(path)
    hb.start(); wr.start()
    t0 = time.perf_counter()
    info = backup(path + ".bak", pages=1, step_sleep_s=0.004, deadline_s=1.0)
    elapsed = time.perf_counter() - t0
    hb.stop.set(); wr.stop.set(); hb.join(); wr.join()
    n, fails, worst = _hb_summary(hb)
    status_ok = (info["status"] == "timeout" and not os.path.exists(path + ".bak")
                 and not os.path.exists(path + ".bak.partial")) or \
                (info["status"] == "ok" and info["verification"]["ok"])
    ok = elapsed > 0.02 and fails == 0 and worst < 0.5 and status_ok
    return ok, (f"status={info['status']} duração={elapsed:.2f}s | heartbeats={n} falhas={fails} "
                f"pior={worst:.3f}s | .bak existe={os.path.exists(path + '.bak')} | escritas={wr.n}")


@test("B03", "janela de manutenção: exige drenagem; digest igual à origem")
def b03(M):
    m, path = fresh(M)
    maint = need(m, "backup_maintenance")
    tok, run = _populate(m, n_pages=20)                      # última renovação em NOW+19 → concessão até NOW+49
    r1 = maint(path + ".bak1", now=NOW + 40)                 # concessão e execução ainda vigentes
    m.stop_run(tok, "JB", run, "cancel_requested", NOW + 41) # drena: encerra execução e libera concessão
    r2 = maint(path + ".bak2", now=NOW + 42)
    ok = r1["status"].startswith("rejected") and r2["status"] == "ok" and r2["digest_equal"]
    return ok, f"sem drenagem={r1['status']} | drenado={r2['status']} digest_igual={r2.get('digest_equal')}"


@test("B04", "backup a partir de conexão com transação aberta é recusado sem travar")
def b04(M):
    m, path = fresh(M)
    guard = need(m, "backup_from_connection")
    m.c.execute("BEGIN IMMEDIATE")
    box = {}

    def target():
        try:
            guard(m.c, path + ".bak")
            box["r"] = "executou"
        except Exception as e:  # noqa: BLE001
            box["r"] = type(e).__name__

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(3.0)
    hung = th.is_alive()
    if not hung:
        m.c.execute("ROLLBACK")
    return (not hung) and box.get("r") == "BackupMisuse", f"travou={hung} | resultado={box.get('r')}"


@test("B05", "verificação usa o ponto de consistência da cópia, não a origem viva")
def b05(M):
    m, path = fresh(M)
    _populate(m, n_pages=40)
    backup = need(m, "backup_online")
    wr = _Writer(path)
    wr.start()
    time.sleep(0.05)
    info = backup(path + ".bak", pages=-1, deadline_s=10.0)
    time.sleep(0.1)
    wr.stop.set(); wr.join()
    origin_bench = sqlite3.connect(path).execute("SELECT COUNT(*) FROM bench").fetchone()[0]
    copy_bench = sqlite3.connect(path + ".bak").execute("SELECT COUNT(*) FROM bench").fetchone()[0]
    ver = info["verification"]
    ok = (info["status"] == "ok" and ver["ok"] and ver.get("compared_with_live_origin") is False
          and "marker" in ver and origin_bench > copy_bench)
    return ok, f"verif={ver} | bench origem={origin_bench} cópia={copy_bench}"


# ================================================================== concorrência entre processos
@test("C01", "4 processos disputam o mesmo job: exatamente um vence")
def c01(M):
    m, path = fresh(M)
    reopen(M, path)  # garante suporte a create=False antes de lançar processos
    for i in range(4):
        m.add_account(f"P{i}")
    m.create_job("J")
    mod, cls = M.__module__, M.__name__
    code = ("import sys; sys.path.insert(0, r'%s'); import %s as mm; "
            "m = mm.%s(r'%s', create=False, busy_timeout=5.0); "
            "t = m.acquire('P'+sys.argv[1], 'proc'+sys.argv[1], %r); "
            "print(m.claim('J', t, %r)[0])") % (HERE, mod, cls, path, NOW, NOW)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(i)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for i in range(4)]
    outs = [p.communicate(timeout=60) for p in procs]
    results = [o[0].strip() or ("ERRO " + o[1].strip().splitlines()[-1]) for o in outs]
    return results.count("ok") == 1, f"resultados={results}"


