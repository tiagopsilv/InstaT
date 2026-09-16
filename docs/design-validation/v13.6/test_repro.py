"""Reproduções isoladas da revisão v13.5 → v13.6.

Cada teste descreve o COMPORTAMENTO CORRIGIDO esperado e roda contra:
  - models_v135.V135: SQL literal da v13.5
  - models_v136.V136: desenho proposto na v13.6 (se existir)
Uso: python test_repro.py   → imprime tabela e grava results.json
Nada aqui toca o InstaT, Android, Instagram ou proxy.
"""
import datetime
import json
import os
import sqlite3
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NOW = 1000.0
TESTS = []


def test(tid, title):
    def deco(fn):
        TESTS.append((tid, title, fn))
        return fn
    return deco


def fresh(model_cls):
    d = tempfile.mkdtemp(prefix="v136_")
    p = os.path.join(d, "db.sqlite")
    return model_cls(p), p


def setup_job(m, job="J", acct="A", owner="w1", now=NOW, kind="cursor"):
    m.add_account(acct)
    tok = m.acquire(acct, owner, now)
    m.create_job(job, kind=kind)
    st, run = m.claim(job, tok, now)
    return tok, run


def nxt(m, job, run):
    return m.job_state(job, run).get("next_pos") or 0


# ---------------------------------------------------------------- 1 idempotência
@test("T01", "repetição exata do mesmo commit com cursor NULL não duplica")
def t01(M):
    m, _ = fresh(M)
    tok, run = setup_job(m)
    kw = dict(attempt_id="att-1", pos=0, cursor_in=None, cursor_out=None, members=[], now=NOW + 1)
    r1 = m.commit(tok, "J", run, **kw)
    r2 = m.commit(tok, "J", run, **kw)
    s = m.job_state("J", run)
    ok = m.count_pages("J") == 1 and s["suspect_total"] == 1
    return ok, f"commit1={r1} | commit2={r2} | páginas={m.count_pages('J')} | suspect_total={s['suspect_total']}"


@test("T02", "página suspect seguida de nova leitura trusted na mesma posição")
def t02(M):
    m, _ = fresh(M)
    tok, run = setup_job(m)
    m.commit(tok, "J", run, attempt_id="a0", pos=0, cursor_in=None, cursor_out="c1",
             members=[(None, "u0")], now=NOW + 1)
    p = nxt(m, "J", run)
    r1 = m.commit(tok, "J", run, attempt_id="a1", pos=p, cursor_in="c1", cursor_out="c2",
                  members=[], now=NOW + 2)
    r2 = m.commit(tok, "J", run, attempt_id="a2", pos=p, cursor_in="c1", cursor_out="c2",
                  members=[(None, "u1")], now=NOW + 3)
    s = m.job_state("J", run)
    ok = r2 == "committed:trusted" and s["cursor"] == "c2"
    return ok, f"leitura1={r1} | releitura={r2} | cursor confiável={s['cursor']!r}"


@test("T03", "contas diferentes com a mesma geração não colidem")
def t03(M):
    m, _ = fresh(M)
    m.add_account("A")
    m.add_account("B")
    ta = m.acquire("A", "wA", NOW)
    m.create_job("J")
    _, runA = m.claim("J", ta, NOW)
    m.commit(ta, "J", runA, attempt_id="a0", pos=0, cursor_in=None, cursor_out="c1",
             members=[(None, "u0")], now=NOW + 1)
    p = nxt(m, "J", runA)
    m.commit(ta, "J", runA, attempt_id="a1", pos=p, cursor_in="c1", cursor_out="c2",
             members=[], now=NOW + 2)
    m.expire("A", NOW + 100)
    tb = m.acquire("B", "wB", NOW + 100)
    st, runB = m.claim("J", tb, NOW + 100)
    ci, pos = m.resume_point("J", runB)
    rB = m.commit(tb, "J", runB, attempt_id="b1", pos=pos, cursor_in=ci, cursor_out="zz",
                  members=[(None, "u1")], now=NOW + 101)
    ok = st == "ok" and rB == "committed:trusted"
    return ok, (f"gerações A,B=({ta[2]},{tb[2]}) | claim B={st} | retomada "
                f"(cursor_in={ci!r}, pos={pos}) | commit B={rB}")


# ---------------------------------------------------------------- 2 reivindicação
@test("T04", "reivindicação por conta sem concessão é rejeitada")
def t04(M):
    m, _ = fresh(M)
    m.create_job("J")
    st, _ = m.claim("J", ("fantasma", "wX", 1), NOW)
    m.add_account("E")
    te = m.acquire("E", "wE", NOW)
    m.create_job("J2")
    m.expire("E", NOW + 1)
    st2, _ = m.claim("J2", te, NOW + 1)
    ok = st != "ok" and st2 != "ok"
    return ok, f"conta inexistente={st} | concessão expirada={st2}"


# ---------------------------------------------------------------- 3 revogação × perda
@test("T05", "revogação com requisição já em voo")
def t05(M):
    m, _ = fresh(M)
    tok, run = setup_job(m)
    sent, _ = m.can_send(tok, "J", run, NOW + 1)
    m.set_auth("A", "needs_attention")
    r = m.commit(tok, "J", run, attempt_id="a0", pos=0, cursor_in=None, cursor_out="c1",
                 members=[(None, "u0")], now=NOW + 2)
    after, why = m.can_send(tok, "J", run, NOW + 3)
    hb = m.heartbeat(tok, NOW + 3)
    s = m.job_state("J", run)
    ok = (sent and r == "committed:suspect" and s["cursor"] is None
          and not after and hb == "account_restricted")
    return ok, (f"enviou={sent} | commit da resposta={r} | cursor={s['cursor']!r} | "
                f"nova operação={after} | heartbeat={hb}")


@test("T06", "perda de posse antes do commit: nada gravado")
def t06(M):
    m, _ = fresh(M)
    tok, run = setup_job(m)
    m.expire("A", NOW + 50)
    hb = m.heartbeat(tok, NOW + 50)
    r = m.commit(tok, "J", run, attempt_id="a0", pos=0, cursor_in=None, cursor_out="c1",
                 members=[(None, "u0")], now=NOW + 50)
    ok = r.startswith("rejected") and m.count_pages("J") == 0 and m.count_members() == 0
    return ok, f"heartbeat={hb} | commit={r} | páginas={m.count_pages('J')} | membros={m.count_members()}"


# ---------------------------------------------------------------- 4 proveniência
@test("T07", "confiança histórica não contamina job novo")
def t07(M):
    m, _ = fresh(M)
    ta, r1 = setup_job(m, job="J1", acct="A", owner="wA")
    m.commit(ta, "J1", r1, attempt_id="j1", pos=0, cursor_in=None, cursor_out=None,
             members=[(None, "alice")], now=NOW + 1)
    tb, r2 = setup_job(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    m.set_auth("B", "needs_attention")
    m.commit(tb, "J2", r2, attempt_id="j2", pos=0, cursor_in=None, cursor_out=None,
             members=[(None, "alice")], now=NOW + 3)
    res = m.result_members("J2", interpretation="global")
    return "alice" not in res, f"membros confiáveis atribuídos ao J2={sorted(res)}"


@test("T10", "promoção sem pk→pk preserva proveniência do job anterior")
def t10(M):
    m, _ = fresh(M)
    ta, r1 = setup_job(m, job="J1", acct="A", owner="wA")
    m.commit(ta, "J1", r1, attempt_id="j1", pos=0, cursor_in=None, cursor_out=None,
             members=[(None, "bob")], now=NOW + 1)
    tb, r2 = setup_job(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    m.commit(tb, "J2", r2, attempt_id="j2", pos=0, cursor_in=None, cursor_out=None,
             members=[("pk9", "bob")], now=NOW + 3)
    a = m.result_members("J1", interpretation="source_job")
    b = m.result_members("J2", interpretation="source_job")
    ok = "bob" in a and "bob" in b and m.count_members() == 1
    return ok, f"J1={sorted(a)} | J2={sorted(b)} | linhas de membro={m.count_members()}"


@test("T10b", "fusão de duas linhas preserva observações e registra auditoria")
def t10b(M):
    m, _ = fresh(M)
    t1, r1 = setup_job(m, job="J1", acct="A", owner="wA")
    m.commit(t1, "J1", r1, attempt_id="j1", pos=0, cursor_in=None, cursor_out=None,
             members=[("pk7", "carol")], now=NOW + 1)
    t2, r2 = setup_job(m, job="J2", acct="B", owner="wB", now=NOW + 2)
    m.commit(t2, "J2", r2, attempt_id="j2", pos=0, cursor_in=None, cursor_out=None,
             members=[(None, "caroline")], now=NOW + 3)
    t3, r3 = setup_job(m, job="J3", acct="C", owner="wC", now=NOW + 4)
    m.commit(t3, "J3", r3, attempt_id="j3", pos=0, cursor_in=None, cursor_out=None,
             members=[("pk7", "caroline")], now=NOW + 5)
    j2 = m.result_members("J2", interpretation="source_job")
    j1 = m.result_members("J1", interpretation="source_job")
    ok = m.count_members() == 1 and m.merge_audit_count() == 1 and "caroline" in j2 and j1
    return ok, (f"linhas de membro={m.count_members()} | fusões auditadas={m.merge_audit_count()} "
                f"| J1={sorted(j1)} | J2={sorted(j2)}")


# ---------------------------------------------------------------- 5 recuperação
@test("T11", "suspeita histórica × aberta; job completa após releitura confiável")
def t11(M):
    m, _ = fresh(M)
    tok, run = setup_job(m)
    m.commit(tok, "J", run, attempt_id="a0", pos=0, cursor_in=None, cursor_out=None,
             members=[], now=NOW + 1)
    p = nxt(m, "J", run)
    r = m.commit(tok, "J", run, attempt_id="a1", pos=p, cursor_in=None, cursor_out=None,
                 members=[(None, "u0")], now=NOW + 2, counter=("exact", 1))
    s = m.job_state("J", run)
    ok = s["suspect_total"] == 1 and s["suspect_open"] == 0 and s["can_complete"] is True
    return ok, (f"releitura={r} | suspect_total={s['suspect_total']} | "
                f"suspect_open={s['suspect_open']} | pode completar={s['can_complete']}")


@test("T12", "sem nova execução automática após restrição; só após requeue")
def t12(M):
    m, _ = fresh(M)
    ta, r1 = setup_job(m, job="J", acct="A", owner="wA")
    m.set_auth("A", "needs_attention")
    m.stop_run(ta, "J", r1, "account_restricted", NOW + 1)
    m.add_account("B")
    tb = m.acquire("B", "wB", NOW + 2)
    st1, _ = m.claim("J", tb, NOW + 2)
    m.requeue("J", NOW + 3)
    st2, _ = m.claim("J", tb, NOW + 4)
    ok = st1 != "ok" and st2 == "ok"
    return ok, f"claim imediato após restrição={st1} | claim após requeue={st2}"


# ---------------------------------------------------------------- 6 progresso sem cursor
@test("T08a", "UI sem cursor: estados distintos e retry exato sem dupla contagem")
def t08a(M):
    m, _ = fresh(M)
    tok, run = setup_job(m, job="JU", kind="scan")
    s0 = m.job_state("JU", run)["progress_state"]
    kw = dict(attempt_id="r0", pos=0, cursor_in=None, cursor_out=None,
              members=[(None, "a"), (None, "b")], screen_hash="h0", now=NOW + 1)
    m.commit(tok, "JU", run, **kw)
    m.commit(tok, "JU", run, **kw)
    s1 = m.job_state("JU", run)
    ok = s0 == "not_started" and s1["progress_state"] == "in_progress" and s1["trusted_pages"] == 1
    return ok, f"antes={s0} | depois={s1['progress_state']} | páginas confiáveis={s1['trusted_pages']}"


@test("T08b", "UI: tela repetida leva a fim desconhecido, nunca a fim confirmado")
def t08b(M):
    m, _ = fresh(M)
    tok, run = setup_job(m, job="JU", kind="scan")
    m.commit(tok, "JU", run, attempt_id="r0", pos=0, cursor_in=None, cursor_out=None,
             members=[(None, "a"), (None, "b")], screen_hash="h0", now=NOW + 1)
    for i in range(3):
        m.commit(tok, "JU", run, attempt_id=f"s{i}", pos=nxt(m, "JU", run), cursor_in=None,
                 cursor_out=None, members=[(None, "a"), (None, "b")], screen_hash="h0", now=NOW + 2 + i)
    st = m.job_state("JU", run)["progress_state"]
    return st == "end_unknown", f"estado após 3 telas idênticas={st}"


@test("T08c", "UI: retomada não confunde releitura de conhecidos com fim de lista")
def t08c(M):
    m, _ = fresh(M)
    tok, run1 = setup_job(m, job="JU", kind="scan")
    known = [(None, "a"), (None, "b"), (None, "c")]
    for i, mem in enumerate(known):
        m.commit(tok, "JU", run1, attempt_id=f"k{i}", pos=nxt(m, "JU", run1), cursor_in=None,
                 cursor_out=None, members=[mem], screen_hash=f"k{i}", now=NOW + 1 + i)
    m.expire("A", NOW + 100)
    tok2 = m.acquire("A", "w2", NOW + 100)
    st, run2 = m.claim("JU", tok2, NOW + 100)
    # releitura: telas novas, mas só membros já conhecidos (a, b)
    for i in range(4):
        m.commit(tok2, "JU", run2, attempt_id=f"rp{i}", pos=nxt(m, "JU", run2), cursor_in=None,
                 cursor_out=None, members=[(None, "a"), (None, "b")], screen_hash=f"rp{i}",
                 now=NOW + 101 + i)
    during = m.job_state("JU", run2)["progress_state"]
    m.commit(tok2, "JU", run2, attempt_id="rc", pos=nxt(m, "JU", run2), cursor_in=None,
             cursor_out=None, members=[(None, "c"), (None, "d")], screen_hash="rc", now=NOW + 110)
    for i in range(3):
        m.commit(tok2, "JU", run2, attempt_id=f"e{i}", pos=nxt(m, "JU", run2), cursor_in=None,
                 cursor_out=None, members=[(None, "d")], screen_hash=f"e{i}", now=NOW + 111 + i)
    final = m.job_state("JU", run2)["progress_state"]
    ok = st == "ok" and during == "in_progress" and final == "end_confirmed"
    return ok, f"claim run2={st} | durante releitura={during} | após frontier + 3 rodadas sem novos={final}"


# ---------------------------------------------------------------- 8 backup
@test("T09", "comparação do backup usa ponto de consistência definido")
def t09(M):
    m, p = fresh(M)
    for i in range(50):
        m.add_account(f"x{i}")
    dst = p + ".bak"

    def writer():
        w = sqlite3.connect(p, isolation_level=None, timeout=0)
        try:
            w.execute("INSERT INTO accounts(account, auth_state) VALUES('tardia','ok')")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            w.close()

    info = m.backup_verify(p, dst, writer)
    return info["counts_match"] and info["integrity"] == "ok", json.dumps(info, ensure_ascii=False)


# ---------------------------------------------------------------- runner
def load(modname, clsname):
    try:
        mod = __import__(modname)
        return getattr(mod, clsname), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def main():
    started = datetime.datetime.now().isoformat(timespec="seconds")
    models = [("models_v135", "V135"), ("models_v136", "V136")]
    results = {"started_at": started, "python": sys.version.split()[0],
               "sqlite": sqlite3.sqlite_version, "models": {}}
    for modname, clsname in models:
        cls, err = load(modname, clsname)
        label = getattr(cls, "name", modname) if cls else modname
        rows = []
        print(f"\n=== {label} ===")
        if err:
            print(f"NÃO IMPLEMENTADO ({err})")
            results["models"][label] = {"error": err, "tests": []}
            continue
        for tid, title, fn in TESTS:
            try:
                ok, detail = fn(cls)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"EXCEÇÃO {type(e).__name__}: {e} | " + \
                    traceback.format_exc().strip().splitlines()[-2].strip()
            mark = "PASS" if ok else "FAIL"
            print(f"{tid:5} {mark}  {title}\n       {detail}")
            rows.append({"id": tid, "title": title, "pass": bool(ok), "detail": detail})
        n = sum(r["pass"] for r in rows)
        print(f"--- {label}: {n}/{len(rows)} passaram")
        results["models"][label] = {"tests": rows, "passed": n, "total": len(rows)}
    out = os.path.join(HERE, os.environ.get("RESULTS_FILE", "results.json"))
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nresultados gravados em {out}")


if __name__ == "__main__":
    main()
