"""Fase 2 — governador de erros e orçamento.

Critérios em docs/phase-evidence/fase-2/README.md (passo 3). Relógio falso,
fakes e nenhuma espera real.
"""
import time
from email.utils import formatdate
from unittest.mock import MagicMock, patch

import pytest

from instat.exceptions import (
    AccountBlockedError,
    AllEnginesBlockedError,
    BlockedError,
    ChallengeError,
    ExtractionStoppedError,
    ProxyError,
    RateLimitError,
    RestrictedError,
    TransientError,
)
from instat.governor import (
    FakeClock,
    Governor,
    GovernorStop,
    KeyState,
    Policy,
    Signal,
    classify_exception,
    classify_http,
    parse_retry_after,
)

KEY = ("acc0", "extract:followers")


@pytest.fixture(autouse=True)
def no_real_sleep():
    with patch("time.sleep", side_effect=AssertionError("sleep real em teste unitário")):
        yield


# ------------------------------------------------------------ classificação HTTP
PROXY_CASES = [
    (407, "NO_USER"), (407, "TRAFFIC_EXHAUSTED"), (407, "THREADS_EXHAUSTED"),
    (407, "PORT_NOT_ALLOWED"), (407, "USER_BLOCKED"),
    (403, "PORT_BLOCKED"), (403, "SITE_PERMANENTLY_BLOCKED"),
    (502, "NO_HOST_CONNECTION"), (503, "NO_RAY"),
]


@pytest.mark.parametrize("status,token", PROXY_CASES)
@pytest.mark.parametrize("where", ["body", "header", "reason"])
def test_proxy_errors_are_never_instagram_restrictions(status, token, where):
    body = f"error {token}" if where == "body" else ""
    headers = {"X-Error": token} if where == "header" else {}
    reason = f"Proxy {token}" if where == "reason" else ""
    c = classify_http(status, headers, body, reason_phrase=reason)
    assert c.signal is Signal.PROXY
    assert c.cause == token


def test_407_without_token_is_unknown_proxy():
    c = classify_http(407, {}, "")
    assert (c.signal, c.cause) == (Signal.PROXY, "unknown_407")


def test_service_5xx_is_transient():
    assert classify_http(500, {}, "").signal is Signal.TRANSIENT
    c = classify_http(503, {"Retry-After": "7"}, "", now_epoch=0)
    assert c.signal is Signal.TRANSIENT and c.retry_after == 7


def test_429_with_and_without_retry_after():
    assert classify_http(429, {}, "").retry_after is None
    c = classify_http(429, {"Retry-After": "120"}, "")
    assert (c.signal, c.retry_after) == (Signal.RATE_LIMITED, 120)


def test_retry_after_http_date_and_garbage():
    now = 1_700_000_000.0
    assert parse_retry_after(formatdate(now + 30, usegmt=True), now) == pytest.approx(30, abs=1)
    assert parse_retry_after(formatdate(now - 30, usegmt=True), now) == 0
    assert parse_retry_after("-5", now) is None
    assert parse_retry_after("amanhã", now) is None


def test_instagram_challenge_feedback_and_403():
    assert classify_http(400, {}, '{"message":"checkpoint_required"}').signal is Signal.CHALLENGE
    assert classify_http(400, {}, '{"message":"challenge_required"}').signal is Signal.CHALLENGE
    assert classify_http(400, {}, '{"message":"feedback_required"}').signal is Signal.RESTRICTED
    assert classify_http(403, {}, "Forbidden").signal is Signal.RESTRICTED
    assert classify_http(200, {}, "{}") is None


def test_classify_exceptions():
    assert classify_exception(RateLimitError("x", retry_after=5)).retry_after == 5
    assert classify_exception(AccountBlockedError("x", reason="challenge")).signal is Signal.CHALLENGE
    assert classify_exception(ChallengeError("x")).signal is Signal.CHALLENGE
    assert classify_exception(BlockedError("pw blocked: https://i/challenge/abc")).signal is Signal.CHALLENGE
    assert classify_exception(RestrictedError("x")).signal is Signal.RESTRICTED
    assert classify_exception(ProxyError("x", cause="NO_RAY")).cause == "NO_RAY"
    assert classify_exception(TransientError("x")).signal is Signal.TRANSIENT
    assert classify_exception(TimeoutError()).signal is Signal.TRANSIENT
    assert classify_exception(ConnectionError()).signal is Signal.TRANSIENT
    assert classify_exception(BlockedError("coverage 40%")).signal is Signal.TECHNICAL
    assert classify_exception(ValueError("parse")).signal is Signal.TECHNICAL


def test_new_errors_stay_compatible():
    for cls in (ProxyError, TransientError, ChallengeError, RestrictedError):
        assert issubclass(cls, BlockedError)
    assert issubclass(ExtractionStoppedError, AllEnginesBlockedError)


# ------------------------------------------------------------ governador
def gov(**policy):
    return Governor(policy=Policy(**policy), clock=FakeClock(), rng=lambda: 0.5)


def failing(exc, counter):
    def fn():
        counter.append(1)
        raise exc
    return fn


def test_transient_retries_are_finite_and_use_fake_clock():
    g, calls = gov(max_transient_retries=3, backoff_base_s=1, backoff_cap_s=60), []
    g.begin(KEY)
    with pytest.raises(TransientError):
        g.call(KEY, failing(TransientError("503"), calls))
    assert len(calls) == 4
    assert g.clock.monotonic() == pytest.approx(1 + 2 + 4)


def test_retry_after_within_cap_is_honored_above_cap_stops():
    g, calls = gov(max_transient_retries=1, backoff_cap_s=60), []
    g.begin(KEY)
    with pytest.raises(TransientError):
        g.call(KEY, failing(TransientError("503", retry_after=45), calls))
    assert g.clock.monotonic() == pytest.approx(45)
    g2, calls2 = gov(max_transient_retries=3, backoff_cap_s=60), []
    g2.begin(KEY)
    with pytest.raises(GovernorStop) as e:
        g2.call(KEY, failing(TransientError("503", retry_after=600), calls2))
    assert len(calls2) == 1 and g2.clock.monotonic() == 0
    assert e.value.reason == "transient:retry_after_above_cap"


@pytest.mark.parametrize("exc,reason,state", [
    (RateLimitError("429", retry_after=120), "rate_limited", KeyState.PAUSED),
    (AccountBlockedError("c", reason="challenge"), "challenge", KeyState.NEEDS_ATTENTION),
    (RestrictedError("feedback_required"), "restricted", KeyState.RESTRICTED),
    (ProxyError("407", cause="TRAFFIC_EXHAUSTED"), "proxy:TRAFFIC_EXHAUSTED", KeyState.ACTIVE),
])
def test_terminal_signals_stop_after_one_attempt(exc, reason, state):
    g, calls = gov(), []
    g.begin(KEY)
    with pytest.raises(GovernorStop) as e:
        g.call(KEY, failing(exc, calls))
    assert len(calls) == 1
    assert e.value.reason == reason
    assert g.state(KEY) is state
    assert g.clock.monotonic() == 0


def test_technical_error_asks_for_fallback_without_retry():
    g, calls = gov(), []
    g.begin(KEY)
    with pytest.raises(BlockedError):
        g.call(KEY, failing(BlockedError("coverage"), calls))
    assert len(calls) == 1 and g.state(KEY) is KeyState.ACTIVE


def test_paused_key_refuses_attempts_until_pause_ends():
    g = gov(rate_limit_pause_s=900)
    g.begin(KEY)
    with pytest.raises(GovernorStop):
        g.call(KEY, failing(RateLimitError("429", retry_after=60), []))
    with pytest.raises(GovernorStop) as e:
        g.before_attempt(KEY)
    assert e.value.reason == "paused"
    g.clock.advance(899)
    with pytest.raises(GovernorStop):
        g.before_attempt(KEY)
    g.clock.advance(2)
    g.before_attempt(KEY)
    assert g.state(KEY) is KeyState.ACTIVE


def test_retry_after_longer_than_min_pause_extends_pause():
    g = gov(rate_limit_pause_s=900)
    g.begin(KEY)
    with pytest.raises(GovernorStop):
        g.call(KEY, failing(RateLimitError("429", retry_after=3600), []))
    g.clock.advance(1000)
    assert g.state(KEY) is KeyState.PAUSED


@pytest.mark.parametrize("exc", [AccountBlockedError("c", reason="challenge"), RestrictedError("r")])
def test_needs_attention_and_restricted_do_not_expire_with_time(exc):
    g = gov()
    g.begin(KEY)
    with pytest.raises(GovernorStop):
        g.call(KEY, failing(exc, []))
    g.clock.advance(10 * 86400)
    with pytest.raises(GovernorStop):
        g.before_attempt(KEY)
    with pytest.raises(ValueError):
        g.release(KEY, session_validated=False)
    g.release(KEY, session_validated=True)
    g.before_attempt(KEY)


def test_state_is_per_account_and_operation():
    g = gov()
    g.begin(KEY)
    with pytest.raises(GovernorStop):
        g.call(KEY, failing(RateLimitError("429"), []))
    g.begin(("acc0", "extract:following"))
    g.before_attempt(("acc0", "extract:following"))


def test_attempt_budget():
    g, calls = gov(max_attempts=3, max_transient_retries=10, backoff_cap_s=1), []
    g.begin(KEY)
    with pytest.raises(GovernorStop) as e:
        g.call(KEY, failing(TransientError("x"), calls))
    assert len(calls) == 3 and e.value.reason == "budget:attempts"


def test_byte_budget():
    g = gov(max_bytes=1000)
    g.begin(KEY)
    g.before_attempt(KEY)
    g.record_bytes(KEY, 600)
    g.before_attempt(KEY)
    g.record_bytes(KEY, 600)
    with pytest.raises(GovernorStop) as e:
        g.before_attempt(KEY)
    assert e.value.reason == "budget:bytes"
    assert g.usage(KEY)["bytes"] == 1200


def test_duration_budget_counts_waits():
    g, calls = gov(max_duration_s=5, max_transient_retries=10, backoff_base_s=2, backoff_cap_s=2), []
    g.begin(KEY)
    with pytest.raises(GovernorStop) as e:
        g.call(KEY, failing(TransientError("x"), calls))
    assert e.value.reason == "budget:duration"
    assert len(calls) == 3


def test_cancel_during_wait_stops_within_one_slice():
    g = gov(max_transient_retries=3, backoff_base_s=30, backoff_cap_s=30, sleep_slice_s=1)
    stop_at = [5]
    g.begin(KEY)
    with pytest.raises(GovernorStop) as e:
        g.call(KEY, failing(TransientError("x"), []),
               should_stop=lambda: g.clock.monotonic() >= stop_at[0])
    assert e.value.reason == "cancelled"
    assert g.clock.monotonic() <= 6


def test_jitter_is_bounded():
    for r in (0.0, 1.0):
        g = Governor(policy=Policy(backoff_base_s=10, backoff_cap_s=60, jitter=0.2,
                                   max_transient_retries=1), clock=FakeClock(), rng=lambda r=r: r)
        g.begin(KEY)
        with pytest.raises(TransientError):
            g.call(KEY, failing(TransientError("x"), []))
        assert 8 <= g.clock.monotonic() <= 12


def test_timeline_records_every_decision():
    g = gov()
    g.begin(KEY)
    g.call(KEY, lambda: "ok")
    with pytest.raises(GovernorStop):
        g.call(KEY, failing(RateLimitError("429", retry_after=10), []))
    events = [(e["event"], e.get("signal")) for e in g.timeline]
    assert ("success", None) in events
    assert ("error", "rate_limited") in events
    assert all("t" in e and "key" in e and "state" in e for e in g.timeline)


# ------------------------------------------------------------ EngineManager
import instat.engines.engine_manager as em  # noqa: E402
from instat.session_pool import SessionPool  # noqa: E402


class FakeEngine:
    def __init__(self, name, behaviour):
        self.name, self._b = name, list(behaviour)
        self.is_available = True
        self.logins, self.extracts = [], 0

    def login(self, username, password, **kw):
        self.logins.append(username)

    def extract(self, profile_id, list_type, on_batch=None, **kw):
        self.extracts += 1
        step = self._b.pop(0) if len(self._b) > 1 else self._b[0]
        if isinstance(step, BaseException):
            if on_batch:
                on_batch({f"parcial_{self.extracts}"})
            raise step
        return set(step)


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def pool3():
    return SessionPool([{"username": f"acc{i}", "password": "pw"} for i in range(3)])


@pytest.mark.parametrize("exc,reason", [
    (AccountBlockedError("c", reason="challenge"), "challenge"),
    (RestrictedError("feedback_required"), "restricted"),
    (RateLimitError("429"), "rate_limited"),
    (ProxyError("407", cause="USER_BLOCKED"), "proxy:USER_BLOCKED"),
])
def test_engine_manager_no_account_switch_nor_extra_engine(cwd, exc, reason):
    e1, e2 = FakeEngine("selenium", [exc]), FakeEngine("httpx", [{"x"}])
    metrics = {}
    mgr = em.EngineManager([e1, e2], session_pool=pool3(), governor=gov())
    out = mgr.extract("alvo", "followers", metrics_sink=metrics)
    assert e1.logins == ["acc0"]
    assert e2.extracts == 0 and e2.logins == []
    assert out == ["parcial_1"]
    assert metrics["terminal_reason"] == reason
    assert mgr.last_stop.reason == reason


def test_engine_manager_login_challenge_does_not_switch_account(cwd):
    class LoginBlocked(FakeEngine):
        def login(self, username, password, **kw):
            self.logins.append(username)
            raise AccountBlockedError("c", reason="challenge")
    e = LoginBlocked("selenium", [{"x"}])
    mgr = em.EngineManager([e], session_pool=pool3(), governor=gov())
    with pytest.raises(ExtractionStoppedError) as err:
        mgr.extract("alvo", "followers")
    assert e.logins == ["acc0"]
    assert err.value.reason == "challenge"


def test_engine_manager_technical_error_falls_back_same_account(cwd):
    e1, e2 = FakeEngine("selenium", [BlockedError("coverage")]), FakeEngine("httpx", [{"a", "b"}])
    mgr = em.EngineManager([e1, e2], governor=gov())
    assert sorted(mgr.extract("alvo", "followers")) == ["a", "b", "parcial_1"]
    assert mgr.last_stop is None


def test_engine_manager_transient_retries_same_engine(cwd):
    g = gov(max_transient_retries=3)
    e = FakeEngine("httpx", [TransientError("503"), TransientError("503"), {"ok"}])
    mgr = em.EngineManager([e], governor=g)
    assert "ok" in mgr.extract("alvo", "followers")
    assert e.extracts == 3
    assert g.clock.monotonic() > 0


def test_engine_manager_zero_profiles_stop_raises_compatible_error(cwd):
    class Silent(FakeEngine):
        def extract(self, *a, **kw):
            self.extracts += 1
            raise RateLimitError("429")
    mgr = em.EngineManager([Silent("httpx", [None])], governor=gov())
    with pytest.raises(AllEnginesBlockedError):
        mgr.extract("alvo", "followers")


def test_engine_manager_cancellation_preserves_partial(cwd):
    g = gov(max_transient_retries=3, backoff_base_s=30, backoff_cap_s=30)
    e = FakeEngine("httpx", [TransientError("503")])
    mgr = em.EngineManager([e], governor=g)
    out = mgr.extract("alvo", "followers", should_stop=lambda: g.clock.monotonic() > 0)
    assert out == ["parcial_1"]
    assert mgr.last_stop.reason == "cancelled"


def test_engine_manager_default_governor_exists():
    mgr = em.EngineManager([FakeEngine("httpx", [{"x"}])])
    assert isinstance(mgr.governor, Governor)


# ------------------------------------------------------------ InstaExtractor
import instat.extractor as ex  # noqa: E402


def bare_extractor(stop):
    fake = object.__new__(ex.InstaExtractor)
    fake._engine_manager = MagicMock()
    fake._engine_manager.get_total_count.return_value = 1000
    fake._engine_manager.last_stop = stop
    fake._log_account_blocked_diagnostic = MagicMock()
    return fake


def test_until_complete_stops_without_waiting_on_terminal_stop():
    stop = GovernorStop("challenge", KEY, Signal.CHALLENGE)
    fake = bare_extractor(stop)
    fake._extract_with_export = MagicMock(side_effect=ExtractionStoppedError("x", stop=stop))
    slept = []
    with patch.object(ex.time, "sleep", side_effect=slept.append):
        fake._extract_until_complete("alvo", "followers", 0.9, 3, 90.0, None)
    assert fake._extract_with_export.call_count == 1 and slept == []


def test_until_complete_stops_when_partial_returned_with_terminal_stop():
    stop = GovernorStop("rate_limited", KEY, Signal.RATE_LIMITED)
    fake = bare_extractor(stop)
    fake._extract_with_export = MagicMock(return_value=["a"])
    slept = []
    with patch.object(ex.time, "sleep", side_effect=slept.append):
        assert fake._extract_until_complete("alvo", "followers", 0.9, 3, 90.0, None) == ["a"]
    assert fake._extract_with_export.call_count == 1 and slept == []


@pytest.mark.parametrize("reason,signal", [
    ("challenge", Signal.CHALLENGE), ("restricted", Signal.RESTRICTED),
    ("rate_limited", Signal.RATE_LIMITED), ("proxy:NO_RAY", Signal.PROXY),
])
def test_rotation_never_switches_account_after_terminal_stop(reason, signal):
    fake = bare_extractor(GovernorStop(reason, KEY, signal))
    fake._extract_until_complete = MagicMock(return_value=["a"])
    fake._build_rotation_extractor = MagicMock()
    out = fake._extract_with_rotation("alvo", "followers",
                                      [{"username": "alt", "password": "pw"}], None,
                                      0.9, 1, 0.0, None)
    assert out == ["a"]
    fake._build_rotation_extractor.assert_not_called()


# ------------------------------------------------------------ httpx
def _httpx_engine(response):
    pytest.importorskip("httpx")
    from instat.engines.httpx_engine import HttpxEngine
    eng = HttpxEngine()
    eng._client = MagicMock(get=MagicMock(return_value=response))
    return eng


def _resp(status, headers=None, text="", reason=""):
    r = MagicMock(status_code=status, headers=headers or {}, text=text, reason_phrase=reason,
                  content=text.encode())
    r.json.side_effect = ValueError
    return r


@pytest.mark.parametrize("status,text,exc", [
    (407, "TRAFFIC_EXHAUSTED", ProxyError),
    (503, "NO_RAY", ProxyError),
    (500, "", TransientError),
    (429, "", RateLimitError),
    (403, "Forbidden", RestrictedError),
    (400, '{"message":"checkpoint_required"}', ChallengeError),
])
def test_httpx_maps_statuses(status, text, exc):
    eng = _httpx_engine(_resp(status, text=text))
    with patch.object(eng, "_resolve_user_id", return_value="1"):
        with pytest.raises(exc):
            eng.extract("alvo", "followers")


def test_httpx_timeout_is_transient_and_429_carries_retry_after():
    eng = _httpx_engine(_resp(429, {"Retry-After": "120"}))
    with patch.object(eng, "_resolve_user_id", return_value="1"):
        with pytest.raises(RateLimitError) as e:
            eng.extract("alvo", "followers")
    assert e.value.retry_after == 120
    eng._client.get.side_effect = TimeoutError("read")
    with patch.object(eng, "_resolve_user_id", return_value="1"):
        with pytest.raises(TransientError):
            eng.extract("alvo", "followers")


def test_httpx_reports_bytes_to_governor():
    body = '{"users": [{"username": "a"}], "next_max_id": null}'
    r = _resp(200, {"Content-Length": str(len(body))}, text=body)
    r.json.side_effect = None
    r.json.return_value = {"users": [{"username": "a"}], "next_max_id": None}
    eng = _httpx_engine(r)
    sink = []
    eng.bytes_sink = sink.append
    with patch.object(eng, "_resolve_user_id", return_value="1"):
        eng.extract("alvo", "followers")
    assert sum(sink) == len(body)


# ------------------------------------------------------------ laço sem teto
def test_wait_for_new_profiles_stale_loop_is_bounded():
    from selenium.common.exceptions import StaleElementReferenceException

    from instat.utils import Utils
    driver = MagicMock()
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) > 50:
            raise AssertionError("laço sem teto")
        raise StaleElementReferenceException("stale")
    driver.find_elements.return_value = []
    with patch.object(Utils, "batch_read_text", side_effect=boom), \
            patch.object(Utils, "dynamic_scroll_element"), \
            patch("instat.utils.human_delay"):
        Utils.wait_for_new_profiles(driver, MagicMock(), "span", set())
    assert len(calls) <= 10
