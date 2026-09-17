"""F4 — scheduler com exclusividade (critérios 3 a 7). Sem browser, sem rede, sem conta."""
import threading
import time
from collections import defaultdict

import pytest

from instat.exceptions import ChallengeError, RateLimitError, RestrictedError
from instat.jobstore.store import JobStore
from instat.scheduler import AccountScheduler, NoEligibleAccount


def make_store(tmp_path, accounts=("a1", "a2", "a3"), ttl=30.0):
    path = str(tmp_path / "jobs.db")
    s = JobStore(path, ttl=ttl, busy_timeout=ttl / 6)
    for a in accounts:
        s.add_account(a)
    return path, s


# ------------------------------------------------------------ JobStore: adições mínimas
def test_release_lease_only_by_current_owner_and_generation(tmp_path):
    path, s = make_store(tmp_path, ("a1",), ttl=0.6)
    old = s.acquire("a1", "p1", now=None)
    time.sleep(0.7)
    new = s.acquire("a1", "p2", now=None)
    assert new is not None and new[2] == old[2] + 1
    assert s.release_lease(old, now=None) is False      # liberação tardia do dono antigo
    assert s.lease_valid(new, now=None) is True
    assert s.release_lease(new, now=None) is True
    assert s.lease_valid(new, now=None) is False
    assert s.acquire("a1", "p3", now=None) is not None


def test_endpoint_cooldown_blocks_only_that_endpoint(tmp_path):
    path, s = make_store(tmp_path, ("a1",))
    s.set_endpoint_cooldown("a1", "followers", until=time.time() + 60, reason="rate_limited")
    assert s.acquire("a1", "p1", now=None, endpoint="followers") is None
    assert s.acquire("a1", "p1", now=None, endpoint="following") is not None


def test_acquire_exposes_lease_interval(tmp_path):
    path, s = make_store(tmp_path, ("a1",), ttl=5.0)
    tok = s.acquire("a1", "p1", now=1000.0)
    assert s.last_lease == {"account": "a1", "owner": "p1", "gen": tok[2], "start": 1000.0, "until": 1005.0}
    s.release_lease(tok, now=1002.0)
    assert s.last_release == {"account": "a1", "owner": "p1", "gen": tok[2], "at": 1002.0, "released": True}


# ------------------------------------------------------------ elegibilidade e plano (critério 4)
def test_plan_workers_limited_to_eligible_accounts(tmp_path):
    path, s = make_store(tmp_path, ("a1", "a2", "a3", "a4", "a5"))
    s.set_auth("a3", "needs_attention", now=None)
    assert s.acquire("a4", "outro-processo", now=None) is not None
    s.set_endpoint_cooldown("a5", "followers", until=time.time() + 60, reason="rate_limited")
    sch = AccountScheduler(path, owner="me", endpoint="followers")
    assert sorted(sch.eligible_accounts()) == ["a1", "a2"]
    assert sch.plan_workers(5) == 2
    assert sch.plan_workers(1) == 1
    assert sch.plan_workers(0) == 0


def test_no_eligible_account(tmp_path):
    path, s = make_store(tmp_path, ("a1",))
    s.set_auth("a1", "restricted", now=None)
    sch = AccountScheduler(path, owner="me")
    assert sch.plan_workers(3) == 0
    with pytest.raises(NoEligibleAccount):
        with sch.lease("a1"):
            pass
    report = sch.run_targets(["t1", "t2"], lambda acct, target, engine: None, requested_workers=3)
    assert report["workers"] == 0
    assert {r["status"] for r in report["targets"].values()} == {"not_processed"}
    assert report["reason"] == "no_eligible_account"


# ------------------------------------------------------------ lease e sinais (critérios 3 e 5)
def test_lease_releases_on_success_and_on_technical_error(tmp_path):
    path, s = make_store(tmp_path, ("a1",))
    sch = AccountScheduler(path, owner="me")
    with sch.lease("a1") as tok:
        assert s.lease_valid(tok, now=None)
    assert not s.lease_valid(tok, now=None)
    with pytest.raises(ValueError):
        with sch.lease("a1") as tok2:
            raise ValueError("falha técnica")
    assert not s.lease_valid(tok2, now=None)
    assert sch.eligible_accounts() == ["a1"]


@pytest.mark.parametrize("exc,state", [(ChallengeError("checkpoint"), "needs_attention"),
                                       (RestrictedError("feedback_required"), "restricted")])
def test_challenge_or_restriction_marks_account_and_time_never_releases(tmp_path, exc, state):
    path, s = make_store(tmp_path, ("a1",), ttl=0.3)
    sch = AccountScheduler(path, owner="me", ttl=0.3)
    with pytest.raises(type(exc)):
        with sch.lease("a1"):
            raise exc
    assert s.c.execute("SELECT auth_state FROM accounts WHERE account='a1'").fetchone()[0] == state
    time.sleep(0.4)
    assert sch.eligible_accounts() == []
    assert s.acquire("a1", "outro", now=None) is None
    assert s.release_account("a1", "operador", session_validated=True, now=None) == "ok"
    assert sch.eligible_accounts() == ["a1"]


def test_rate_limit_sets_endpoint_cooldown(tmp_path):
    path, s = make_store(tmp_path, ("a1",))
    sch = AccountScheduler(path, owner="me", endpoint="followers")
    with pytest.raises(RateLimitError):
        with sch.lease("a1"):
            raise RateLimitError("429", retry_after=1200)
    assert sch.eligible_accounts() == []
    assert AccountScheduler(path, owner="me", endpoint="following").eligible_accounts() == ["a1"]
    until = s.c.execute("SELECT cooldown_until FROM endpoint_cooldowns").fetchone()[0]
    assert until >= time.time() + 1100


def test_heartbeat_keeps_lease_beyond_ttl(tmp_path):
    path, s = make_store(tmp_path, ("a1",), ttl=0.6)
    sch = AccountScheduler(path, owner="me", ttl=0.6)
    with sch.lease("a1") as tok:
        time.sleep(1.5)
        assert s.lease_valid(tok, now=None)
        assert s.acquire("a1", "intruso", now=None) is None


# ------------------------------------------------------------ run_targets (critérios 4 e 6)
def test_run_targets_one_account_per_worker_and_engine_owned_by_thread(tmp_path):
    path, s = make_store(tmp_path, ("a1", "a2"))
    sch = AccountScheduler(path, owner="me")
    active, peak, lock = defaultdict(int), defaultdict(int), threading.Lock()
    workers_seen = set()

    class Engine:
        def __init__(self):
            self.created_in = threading.get_ident()
            self.used_in = set()
            self.quit_in = None

        def quit(self):
            self.quit_in = threading.get_ident()
    engines = []

    def factory():
        e = Engine()
        engines.append(e)
        return e

    def work(account, target, engine):
        engine.used_in.add(threading.get_ident())
        with lock:
            workers_seen.add(threading.get_ident())
            active[account] += 1
            peak[account] = max(peak[account], active[account])
        time.sleep(0.05)
        with lock:
            active[account] -= 1
        return f"{target}@{account}"

    report = sch.run_targets([f"t{i}" for i in range(6)], work, requested_workers=5, engine_factory=factory)
    assert report["workers"] == 2 and len(workers_seen) == 2
    assert all(v == 1 for v in peak.values())
    assert all(r["status"] == "ok" for r in report["targets"].values())
    for e in engines:
        assert e.used_in == {e.created_in} and e.quit_in == e.created_in


def test_run_targets_challenge_stops_that_account_and_is_reported(tmp_path):
    path, s = make_store(tmp_path, ("a1", "a2"))
    sch = AccountScheduler(path, owner="me")

    def work(account, target, engine):
        if account == "a1":
            raise ChallengeError("checkpoint_required")
        time.sleep(0.01)
        return "ok"

    report = sch.run_targets([f"t{i}" for i in range(4)], work, requested_workers=2)
    statuses = [r["status"] for r in report["targets"].values()]
    assert statuses.count("stopped") == 1
    stopped = next(r for r in report["targets"].values() if r["status"] == "stopped")
    assert stopped["account"] == "a1" and stopped["reason"] == "challenge"
    assert all(r["account"] == "a2" for r in report["targets"].values() if r["status"] == "ok")
    assert s.c.execute("SELECT auth_state FROM accounts WHERE account='a1'").fetchone()[0] == "needs_attention"


# ------------------------------------------------------------ legado parallel_extract (critério 7)
def _legacy_run(workers, accounts, crash_idx=None):
    from instat.parallel import parallel_extract
    active, peak, lock, n = defaultdict(int), defaultdict(int), threading.Lock(), [0]

    class FakeEngine:
        def __init__(self, crash):
            self.user, self.crash = None, crash

        def login(self, username, password, **kw):
            self.user = username
            with lock:
                active[username] += 1
                peak[username] = max(peak[username], active[username])

        def extract(self, profile_id, list_type, **kw):
            time.sleep(0.1)
            if self.crash:
                raise RuntimeError("driver morreu")
            return {f"{self.user}_x"}

        def quit(self):
            with lock:
                active[self.user] -= 1

    def factory():
        n[0] += 1
        return FakeEngine(crash_idx is not None and n[0] - 1 == crash_idx)
    out = parallel_extract("alvo", "followers", workers=workers, default_credentials=("unica", "pw"),
                           accounts=accounts, engine_factory=factory)
    return out, dict(peak), n[0]


def test_parallel_extract_never_shares_an_account():
    accounts = [{"username": "a1", "password": "p"}, {"username": "a2", "password": "p"}]
    _, peak, created = _legacy_run(4, accounts)
    assert peak == {"a1": 1, "a2": 1} and created == 2
    _, peak, created = _legacy_run(3, None)
    assert peak == {"unica": 1} and created == 1
    dup = [{"username": "a1", "password": "p"}, {"username": "a1", "password": "p"}]
    _, peak, created = _legacy_run(2, dup)
    assert peak == {"a1": 1} and created == 1


def test_parallel_extract_reports_worker_failure():
    from instat import parallel
    accounts = [{"username": f"c{i}", "password": "p"} for i in range(3)]
    _legacy_run(3, accounts, crash_idx=1)
    rep = parallel.last_report
    assert rep["workers"] == 3
    failed = [w for w in rep["per_worker"] if w["status"] == "error"]
    assert len(failed) == 1 and "driver morreu" in failed[0]["error"]
