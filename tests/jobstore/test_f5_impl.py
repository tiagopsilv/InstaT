"""F5 — critérios 2, 6, 7, 8, 9, 10, 11 e 12 (docs/phase-evidence/fase-5/README.md)."""
import inspect
import json
import math
import os
import sqlite3
import threading
import time

import pytest

from instat.jobstore.canonical import content_hash, normalize_username
from instat.jobstore.heartbeat import Heartbeat
from instat.jobstore.migrate import import_legacy_profiles_seen
from instat.jobstore.policy import HISTORY_LABEL, SANITY_V1
from instat.jobstore.spool import AttemptSpool
from instat.jobstore.store import BackupMisuse, JobStore

NOW = 1000.0


def store(tmp_path, **kw):
    return JobStore(str(tmp_path / "jobs.db"), **kw)


def job_run(m, job="J", acct="A", owner="w1", now=NOW, kind="cursor"):
    m.add_account(acct)
    m.create_job(job, kind=kind)
    tok = m.acquire(acct, owner, now)
    status, run = m.claim(job, tok, now)
    assert status == "ok"
    return tok, run


def mem(*names):
    return [(None, n) for n in names]


# ------------------------------------------------------------ política e canônico
def test_policy_is_sanity_v1_fixed_in_pre_analysis():
    assert SANITY_V1 == {
        "version": "sanity-v1", "K": 3, "S": 3, "replay_ratio": 0.95, "frontier_confirm_screens": 2,
        "counter_tol_abs_min": 2, "counter_tol_rel": 0.01, "counter_stale_s": 1800,
        "max_rereads_per_pos": 2, "max_runs": 3,
    }
    assert "não é lista atual" in HISTORY_LABEL


def test_canonical_normalization():
    assert normalize_username("  Aná ") == normalize_username("ANÁ")
    h1 = content_hash(1, 0, None, "c1", [(None, " Ana ")], True, False, False, False, None, None)
    h2 = content_hash(1, 0, None, "c1", [(None, "ana")], True, False, False, False, None, None)
    assert h1 == h2 and len(h1) == 64


# ------------------------------------------------------------ validação (§6.3.12)
@pytest.mark.parametrize("kw", [dict(ttl=0), dict(ttl=-1), dict(ttl=math.inf), dict(ttl=math.nan),
                                dict(ttl=30, busy_timeout=5.1)])
def test_store_rejects_invalid_ttl_and_busy_timeout(tmp_path, kw):
    with pytest.raises(ValueError):
        store(tmp_path, **kw)


def test_store_accepts_busy_timeout_at_ttl_over_6(tmp_path):
    store(tmp_path, ttl=30, busy_timeout=5.0)


def test_schema_version_wal_and_foreign_keys(tmp_path):
    m = store(tmp_path)
    c = sqlite3.connect(m.path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert m.c.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ------------------------------------------------------------ I7: now depois do bloqueio
def test_now_is_read_after_waiting_for_the_lock(tmp_path):
    m = store(tmp_path, ttl=12.0, busy_timeout=2.0)
    m.add_account("A")
    tok = m.acquire("A", "w1", now=None)
    t0 = time.time()
    m.c.execute("UPDATE accounts SET lease_until=? WHERE account='A'", (t0 + 1.0,))
    holder = sqlite3.connect(m.path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    released = threading.Event()

    def release_later():
        time.sleep(1.5)
        holder.execute("COMMIT")
        released.set()
    threading.Thread(target=release_later).start()
    time.sleep(0.1)
    result = m.heartbeat(tok, now=None)
    released.wait(5)
    assert result == "lease_lost", "now lido antes do bloqueio teria renovado uma concessão já expirada"


# ------------------------------------------------------------ critério 2: rejeições
def test_rejections_hold_in_100_percent_of_attempts(tmp_path):
    kinds = ["sem_posse", "sem_concessao", "sem_sessao_validada", "renovacao_revogada", "execucao_nao_corrente",
             "segunda_trusted", "attempt_conteudo_diferente", "attempt_outra_execucao", "quarta_releitura",
             "quarta_execucao"]
    outcomes = {k: [] for k in kinds}
    for i in range(100):
        k = kinds[i % len(kinds)]
        d = tmp_path / f"r{i}"
        d.mkdir()
        m = store(d)
        tok, run = job_run(m)
        if k == "sem_posse":
            m.expire("A", NOW + 1)
            r = m.commit(tok, "J", run, attempt_id="a", pos=0, cursor_in=None, cursor_out="c",
                         members=mem("ana"), now=NOW + 1)
            outcomes[k].append(r == "rejected:posse")
        elif k == "sem_concessao":
            m.add_account("B")
            r = m.claim("J", ("B", "x", 1), NOW)[0]
            outcomes[k].append(r == "rejected:solicitante_sem_concessao")
        elif k == "sem_sessao_validada":
            m.set_auth("A", "restricted", now=NOW)
            outcomes[k].append(m.release_account("A", "tiago", False, NOW + 1) == "rejected:sessao_nao_validada")
        elif k == "renovacao_revogada":
            m.set_auth("A", "needs_attention", now=NOW)
            outcomes[k].append(m.heartbeat(tok, NOW + 1) == "account_restricted")
        elif k == "execucao_nao_corrente":
            m.stop_run(tok, "J", run, "technical_error", NOW + 1)
            tok2 = m.acquire("A", "w2", NOW + 2)
            _, run2 = m.claim("J", tok2, NOW + 2)
            r = m.commit(tok2, "J", run, attempt_id="a", pos=0, cursor_in=None, cursor_out="c",
                         members=mem("ana"), now=NOW + 3)
            outcomes[k].append(r.startswith("rejected:") and run2 != run)
        elif k == "segunda_trusted":
            m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c",
                     members=mem("ana"), now=NOW + 1)
            r = m.commit(tok, "J", run, attempt_id="a2", pos=0, cursor_in=None, cursor_out="c",
                         members=mem("ana"), now=NOW + 2)
            outcomes[k].append(r.startswith("rejected:"))
        elif k == "attempt_conteudo_diferente":
            m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c",
                     members=mem(), now=NOW + 1)
            r = m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c",
                         members=mem("bruno"), now=NOW + 2)
            outcomes[k].append(r == "error:attempt_id_reutilizado_com_conteudo_diferente")
        elif k == "attempt_outra_execucao":
            m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c",
                     members=mem("ana"), now=NOW + 1)
            m.stop_run(tok, "J", run, "technical_error", NOW + 2)
            tok2 = m.acquire("A", "w2", NOW + 3)
            _, run2 = m.claim("J", tok2, NOW + 3)
            r = m.commit(tok2, "J", run2, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c",
                         members=mem("ana"), now=NOW + 4)
            outcomes[k].append(r == "error:attempt_id_de_outra_execucao")
        elif k == "quarta_releitura":
            rs = [m.commit(tok, "J", run, attempt_id=f"s{j}", pos=0, cursor_in=None, cursor_out="c",
                           members=mem(), now=NOW + 1 + j) for j in range(4)]
            outcomes[k].append(rs[:3] == ["committed:suspect"] * 3 and rs[3] == "rejected:limite_de_releituras")
        elif k == "quarta_execucao":
            ok = True
            for j in range(3):
                if j:
                    tokj = m.acquire("A", f"w{j}", NOW + 10 * j)
                    ok &= m.claim("J", tokj, NOW + 10 * j)[0] == "ok"
                    tok = tokj
                m.stop_run(tok, "J", m.c.execute("SELECT current_run FROM jobs").fetchone()[0],
                           "technical_error", NOW + 10 * j + 1)
            tok4 = m.acquire("A", "w9", NOW + 100)
            ok &= m.claim("J", tok4, NOW + 100)[0] == "rejected:limite_de_execucoes"
            ok &= m.requeue("J", by="tiago", now=NOW + 101) == "rejected:limite_de_execucoes"
            outcomes[k].append(ok)
        m.c.close()
    summary = {k: f"{sum(v)}/{len(v)}" for k, v in outcomes.items()}
    assert all(all(v) and v for v in outcomes.values()), summary


def test_unique_violation_never_becomes_success(tmp_path):
    m = store(tmp_path)
    tok, run = job_run(m)
    m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c",
             members=mem("ana"), now=NOW + 1)
    # força a mesma posição por baixo da verificação de next_pos
    m.c.execute("UPDATE runs SET next_pos=0, trusted_cursor=NULL WHERE run_id=?", (run,))
    with pytest.raises(sqlite3.IntegrityError):
        m.commit(tok, "J", run, attempt_id="a2", pos=0, cursor_in=None, cursor_out="c",
                 members=mem("ana"), now=NOW + 2)
    assert m.c.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
    assert not m.c.in_transaction


def test_every_page_has_policy_version(tmp_path):
    m = store(tmp_path)
    tok, run = job_run(m)
    m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c", members=mem(), now=NOW + 1)
    m.commit(tok, "J", run, attempt_id="a2", pos=0, cursor_in=None, cursor_out="c", members=mem("a"), now=NOW + 2)
    assert {r[0] for r in m.c.execute("SELECT policy_version FROM pages")} == {"sanity-v1"}


# ------------------------------------------------------------ spool
def test_spool_atomic_pending_and_ack(tmp_path):
    sp = AttemptSpool(str(tmp_path / "spool"))
    sp.begin("a1", run_id=7, pos=0, cursor_in=None)
    sp.begin("a2", run_id=7, pos=1, cursor_in="c1")
    sp.record_response("a2", {"members": [[None, "ana"]], "cursor_out": "c2"}, received_at=5.0)
    (tmp_path / "spool" / ".tmp-garbage.json.tmp").write_text("{")
    pend = {e["attempt_id"]: e for e in sp.pending(7)}
    assert set(pend) == {"a1", "a2"}
    assert pend["a1"]["response"] is None
    assert pend["a2"]["response"]["cursor_out"] == "c2"
    assert sp.pending(8) == []
    sp.ack("a1")
    sp.ack("a2")
    assert sp.pending(7) == []
    assert not [p for p in os.listdir(tmp_path / "spool") if p.endswith(".json")]


# ------------------------------------------------------------ heartbeat real (thread)
def _hb_store(tmp_path, ttl=0.9):
    m = store(tmp_path, ttl=ttl, busy_timeout=ttl / 6)
    m.add_account("A")
    tok = m.acquire("A", "w1", now=None)
    return m, tok


def test_heartbeat_thread_renews_and_detects_restriction(tmp_path):
    m, tok = _hb_store(tmp_path)
    hb = Heartbeat(m.path, tok, ttl=0.9, busy_timeout=0.15)
    hb.start()
    time.sleep(1.0)
    assert hb.renewals >= 2 and hb.status == "renewed" and hb.verifiable()
    m.set_auth("A", "restricted", now=time.time())
    time.sleep(0.6)
    hb.stop()
    assert hb.status == "account_restricted"
    assert not hb.verifiable()


def test_heartbeat_thread_detects_lease_lost(tmp_path):
    m, tok = _hb_store(tmp_path)
    hb = Heartbeat(m.path, tok, ttl=0.9, busy_timeout=0.15)
    hb.start()
    time.sleep(0.4)
    m.c.execute("UPDATE accounts SET lease_gen=lease_gen+1, lease_owner='w2' WHERE account='A'")
    time.sleep(0.6)
    hb.stop()
    assert hb.status == "lease_lost"


def test_heartbeat_unverifiable_after_half_ttl_without_success(tmp_path):
    m, tok = _hb_store(tmp_path)
    fake = [time.monotonic()]
    hb = Heartbeat(m.path, tok, ttl=0.9, busy_timeout=0.15, mono=lambda: fake[0])
    assert hb.beat_once() == "renewed" and hb.verifiable()
    fake[0] += 0.46
    assert not hb.verifiable()


def test_heartbeat_uses_own_connection_in_own_thread(tmp_path):
    m, tok = _hb_store(tmp_path)
    hb = Heartbeat(m.path, tok, ttl=0.9, busy_timeout=0.15)
    hb.start()
    time.sleep(0.4)
    hb.stop()
    assert hb.connection_thread_id is not None and hb.connection_thread_id != threading.get_ident()


# ------------------------------------------------------------ backup online com trabalho ativo
def test_backup_online_with_heartbeat_and_writer(tmp_path):
    m = store(tmp_path, ttl=3.0, busy_timeout=0.5)
    m.add_account("A")
    m.create_job("J")
    tok = m.acquire("A", "w1", now=None)
    _, run = m.claim("J", tok, now=None)
    hb = Heartbeat(m.path, tok, ttl=3.0, busy_timeout=0.5, interval=0.05)
    stop = threading.Event()
    writes = []

    def writer():
        w = JobStore(m.path, ttl=3.0, busy_timeout=0.5, create=False)
        pos, cur = 0, None
        while not stop.is_set():
            r = w.commit(tok, "J", run, attempt_id=f"w{pos}", pos=pos, cursor_in=cur, cursor_out=f"c{pos}",
                         members=[(None, f"user{pos}_{i}") for i in range(20)], now=None)
            writes.append(r)
            pos, cur = pos + 1, f"c{pos}"
            time.sleep(0.01)
    t = threading.Thread(target=writer)
    hb.start()
    t.start()
    time.sleep(0.3)
    info = m.backup_online(str(tmp_path / "copy.db"), deadline_s=10)
    time.sleep(0.2)
    stop.set()
    t.join()
    hb.stop()
    assert info["status"] == "ok" and info["verification"]["ok"]
    assert info["verification"]["compared_with_live_origin"] is False
    assert hb.failures == 0 and hb.status == "renewed"
    assert writes and all(w == "committed:trusted" for w in writes)


def test_backup_refused_from_connection_with_open_transaction(tmp_path):
    m = store(tmp_path)
    m.c.execute("BEGIN IMMEDIATE")
    with pytest.raises(BackupMisuse):
        m.backup_from_connection(m.c, str(tmp_path / "x.db"))
    m.c.execute("ROLLBACK")


# ------------------------------------------------------------ migração legada (§6.3.11)
def test_legacy_import_is_suspect_and_never_trusted(tmp_path):
    from instat.persistent_store import PersistentStore
    legacy = PersistentStore(str(tmp_path / "legacy.db"))
    legacy.add_batch("alvo", "followers", ["ana", "bruno"], "conta1")
    m = store(tmp_path)
    info = import_legacy_profiles_seen(m, legacy.path, backup_dir=str(tmp_path / "bk"), now=NOW)
    assert info["status"] == "ok" and info["imported"] == 2
    assert info["backup"]["verification"]["ok"] and os.path.exists(info["backup"]["path"])
    job = info["jobs"][0]
    view = m.job_view(job)
    assert view["selected_members"] == [] and view["selected_run_status"] == "partial"
    assert view["history_observed"] == {}
    q = m.c.execute("SELECT quality, reason, policy_version FROM pages").fetchall()
    assert q == [("suspect", "legacy_import", "sanity-v1")]
    assert m.c.execute("SELECT attempts, current_run FROM jobs WHERE job_id=?", (job,)).fetchone() == (0, None)
    # dado legado continua legível e o job pode receber execução real sem requeue
    m.add_account("A")
    tok = m.acquire("A", "w1", NOW + 1)
    assert m.claim(job, tok, NOW + 1)[0] == "ok"
    assert set(PersistentStore(legacy.path).get_all("alvo", "followers")) == {"ana", "bruno"}


# ------------------------------------------------------------ resultado (§6.3.9)
def test_job_view_contract_fields(tmp_path):
    m = store(tmp_path)
    tok, run = job_run(m)
    m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out=None,
             members=mem("ana"), end_marker=True, now=NOW + 1)
    v = m.job_view("J")
    for k in ("selected_run_id", "selected_run_status", "selected_run_at", "selected_members",
              "selected_suspect_open", "last_attempt_run_id", "last_attempt_status", "last_attempt_state",
              "last_attempt_stop_reason", "last_attempt_at", "history_observed", "history_label"):
        assert k in v
    assert not any("remov" in k or "unfollow" in k for k in v)
    assert m.run_result(run)["status"] == "complete"
    assert json.dumps(v)


# ------------------------------------------------------------ contrato legado (critério 11)
def test_legacy_public_contract_signatures_unchanged():
    from instat.extractor import InstaExtractor as E
    from instat.persistent_store import PersistentStore as P
    expected = {
        "get_followers": "(self, profile_id: str, max_duration: Optional[float] = None, should_stop=None, "
                         "with_metadata: bool = False)",
        "get_following": "(self, profile_id: str, max_duration: Optional[float] = None, should_stop=None, "
                         "with_metadata: bool = False)",
    }
    for n, sig in expected.items():
        assert str(inspect.signature(getattr(E, n))) == sig
    for n in ("get_followers_persistent", "get_following_persistent"):
        assert str(inspect.signature(getattr(E, n))).endswith("-> Dict[str, object]")
    assert str(inspect.signature(P.get_all)) == "(self, profile_id: str, list_type: str) -> List[str]"
    assert str(inspect.signature(P.add_batch)) == \
        "(self, profile_id: str, list_type: str, usernames, source_account: str) -> int"
