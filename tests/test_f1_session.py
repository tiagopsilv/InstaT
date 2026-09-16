"""Fase 1 — sessão persistente confiável e identidade web.

Critérios em docs/phase-evidence/fase-1/README.md (passo 3). Tudo com
fakes: nenhuma conta real, nenhum tráfego.
"""
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from instat import login as login_mod
from instat.block_detector import BlockDetector
from instat.exceptions import AccountBlockedError
from instat.login_flow import SessionRestorer
from instat.session_cache import SessionCache, default_session_dir
from instat.session_validation import RestoreOutcome, classify_restored_session
from instat.user_agents import mobile_user_agent

BASE = "https://www.instagram.com"
ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ cache
@pytest.mark.parametrize("raw", [
    '{"cookies": [',
    json.dumps({"cookies": []}),
    json.dumps({"cookies": [], "saved_at": "ontem"}),
    json.dumps({"cookies": "abc", "saved_at": 1.0}),
    json.dumps({"cookies": [1, 2], "saved_at": 1.0}),
    json.dumps([1, 2, 3]),
    json.dumps({"version": 99, "cookies": [], "saved_at": 1.0}),
])
def test_invalid_cache_file_is_miss_not_exception(tmp_path, raw):
    (tmp_path / "u.json").write_text(raw, encoding="utf-8")
    with patch("instat.session_cache.time.time", return_value=2.0):
        assert SessionCache(cache_dir=tmp_path).load("u") is None


def test_future_saved_at_is_miss(tmp_path):
    c = SessionCache(cache_dir=tmp_path)
    c.save("u", [{"name": "sessionid", "value": "x"}])
    with patch("instat.session_cache.time.time", return_value=time.time() - 3600):
        assert c.load("u") is None


def test_ttl_boundary_expires(tmp_path):
    with patch("instat.session_cache.time.time", return_value=1000.0):
        c = SessionCache(cache_dir=tmp_path)
        c.save("u", [{"name": "sessionid", "value": "x"}])
    with patch("instat.session_cache.time.time", return_value=4599.0):
        assert c.load("u")
    with patch("instat.session_cache.time.time", return_value=4600.0):
        assert c.load("u") is None


def test_legacy_v1_file_still_loads(tmp_path):
    cookies = [{"name": "sessionid", "value": "x"}]
    (tmp_path / "u.json").write_text(json.dumps({"cookies": cookies, "saved_at": time.time()}))
    assert SessionCache(cache_dir=tmp_path).load("u") == cookies


def test_v2_schema_records_identity_and_backend(tmp_path):
    c = SessionCache(cache_dir=tmp_path)
    c.save("u", [{"name": "sessionid", "value": "x"}, {"name": "ds_user_id", "value": "42"}],
           backend="playwright-firefox")
    data = json.loads((tmp_path / "u.json").read_text())
    assert data["version"] == 2
    assert data["username"] == "u"
    assert data["ds_user_id"] == "42"
    assert data["backend"] == "playwright-firefox"
    assert c.expected_ds_user_id("u") == "42"


def test_cache_of_other_username_is_miss(tmp_path):
    c = SessionCache(cache_dir=tmp_path)
    c.save("outro", [{"name": "sessionid", "value": "x"}])
    os.replace(tmp_path / "outro.json", tmp_path / "u.json")
    assert c.load("u") is None


def test_ds_user_id_inconsistent_with_cookies_is_miss(tmp_path):
    c = SessionCache(cache_dir=tmp_path)
    c.save("u", [{"name": "sessionid", "value": "x"}, {"name": "ds_user_id", "value": "42"}])
    p = tmp_path / "u.json"
    data = json.loads(p.read_text())
    data["cookies"][1]["value"] = "43"
    p.write_text(json.dumps(data))
    assert c.load("u") is None


def test_backend_does_not_invalidate_shared_cookies(tmp_path):
    c = SessionCache(cache_dir=tmp_path)
    c.save("u", [{"name": "sessionid", "value": "x"}], backend="selenium")
    assert c.load("u")


def test_atomic_write_preserves_previous_file_on_failure(tmp_path):
    c = SessionCache(cache_dir=tmp_path)
    c.save("u", [{"name": "sessionid", "value": "old"}])
    with patch("instat.session_cache.os.replace", side_effect=OSError("disco")):
        with pytest.raises(OSError):
            c.save("u", [{"name": "sessionid", "value": "new"}])
    assert c.load("u")[0]["value"] == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["u.json"]


def test_touch_renews_saved_at(tmp_path):
    with patch("instat.session_cache.time.time", return_value=1000.0):
        c = SessionCache(cache_dir=tmp_path)
        c.save("u", [{"name": "sessionid", "value": "x"}])
    with patch("instat.session_cache.time.time", return_value=4000.0):
        c.touch("u")
    with patch("instat.session_cache.time.time", return_value=6000.0):
        assert c.load("u")


def test_default_dir_is_stable_and_env_overridable(tmp_path, monkeypatch):
    monkeypatch.delenv("INSTAT_SESSION_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert default_session_dir() == Path.home() / ".instat" / "sessions"
    monkeypatch.setenv("INSTAT_SESSION_DIR", str(tmp_path / "s"))
    assert default_session_dir() == tmp_path / "s"


def test_legacy_cwd_cache_is_read_as_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("INSTAT_SESSION_DIR", str(tmp_path / "novo"))
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".instat_sessions"
    legacy.mkdir()
    cookies = [{"name": "sessionid", "value": "x"}]
    (legacy / "u.json").write_text(json.dumps({"cookies": cookies, "saved_at": time.time()}))
    assert SessionCache().load("u") == cookies


def test_dockerfile_pins_session_dir_to_volume():
    assert re.search(r"INSTAT_SESSION_DIR=/app/\.instat_sessions",
                     (ROOT / "Dockerfile").read_text(encoding="utf-8"))


# ------------------------------------------------------------ validação
class FakeDriver:
    def __init__(self, after_url, page_source=""):
        self._after = after_url
        self.current_url = BASE + "/"
        self.page_source = page_source
        self.title = ""
        self._c = {}
        self.form_opened = False

    def get(self, url):
        self.current_url = url
        if "/accounts/login" in url:
            self.form_opened = True

    def add_cookie(self, c):
        self._c[c["name"]] = c

    def refresh(self):
        self.current_url = self._after

    def get_cookie(self, n):
        return self._c.get(n)

    def get_cookies(self):
        return list(self._c.values())

    def delete_all_cookies(self):
        self._c = {}


SESSION = [{"name": "sessionid", "value": "x"}, {"name": "ds_user_id", "value": "42"}]


@pytest.mark.parametrize("path", ["challenge/abc/", "checkpoint/", "accounts/suspended/",
                                  "auth_platform/codeentry/", "two_factor/", "accounts/login/"])
def test_restorer_rejects_non_session_states(path):
    r = SessionRestorer(BASE)
    d = FakeDriver(f"{BASE}/{path}")
    assert r.attempt(d, SESSION) is False
    expected = RestoreOutcome.LOGIN if path.startswith("accounts/login") else RestoreOutcome.BLOCKED
    assert r.last_outcome is expected


def test_restorer_rejects_other_identity():
    r = SessionRestorer(BASE)
    assert r.attempt(FakeDriver(BASE + "/"), SESSION, expected_ds_user_id="999") is False
    assert r.last_outcome is RestoreOutcome.IDENTITY_MISMATCH


def test_restorer_accepts_valid_session():
    r = SessionRestorer(BASE)
    assert r.attempt(FakeDriver(BASE + "/"), SESSION, expected_ds_user_id="42") is True
    assert r.last_outcome is RestoreOutcome.OK


def test_classifier_requires_sessionid():
    assert classify_restored_session(BASE + "/", lambda n: None, None) is RestoreOutcome.NO_SESSION


def test_block_detector_knows_suspended():
    assert "suspended" in BlockDetector.URL_INDICATORS


# ------------------------------------------------------------ login()
def _bare_login(driver, cache):
    il = object.__new__(login_mod.InstaLogin)
    il.driver = driver
    il.username, il.password = "u", "p"
    il._base_url = BASE
    il._session_cache = cache
    il._session_restorer = SessionRestorer(BASE)
    il._block_detector = BlockDetector()
    il._diagnostics = None
    il.close_keywords, il.timeout = [], 1
    il._save_block_evidence = MagicMock(return_value="N/A")
    il._try_handle_email_challenge = MagicMock(return_value=False)
    form = MagicMock()
    il._get_form_login = MagicMock(return_value=form)
    return il, form


def test_login_restore_into_challenge_raises_without_form(tmp_path):
    cache = SessionCache(cache_dir=tmp_path)
    cache.save("u", SESSION)
    il, form = _bare_login(FakeDriver(BASE + "/challenge/abc/"), cache)
    with pytest.raises(AccountBlockedError):
        il.login()
    assert not form.execute.called


def test_login_restore_runs_block_detector_on_html(tmp_path):
    cache = SessionCache(cache_dir=tmp_path)
    cache.save("u", SESSION)
    il, form = _bare_login(FakeDriver(BASE + "/", page_source="<p>Meta Verified</p>"), cache)
    with pytest.raises(AccountBlockedError):
        il.login()
    assert not form.execute.called


def test_ten_restarts_with_valid_session_open_zero_forms(tmp_path):
    now = [10_000.0]
    with patch("instat.session_cache.time.time", side_effect=lambda: now[0]):
        cache = SessionCache(cache_dir=tmp_path)
        cache.save("u", SESSION)
        forms = 0
        for _ in range(10):
            now[0] += 600
            il, form = _bare_login(FakeDriver(BASE + "/"), cache)
            with patch.object(login_mod.Utils, "dismiss_save_login_modal"):
                assert il.login() is True
            forms += int(form.execute.called)
    assert forms == 0


def test_expired_session_falls_back_to_form_once(tmp_path):
    cache = SessionCache(cache_dir=tmp_path)
    cache.save("u", SESSION)
    il, form = _bare_login(FakeDriver(BASE + "/accounts/login/"), cache)
    il.driver.current_url = BASE + "/"

    def do_form(driver, *a):
        driver.current_url = BASE + "/"
        driver._c = {c["name"]: c for c in SESSION}
    form.execute.side_effect = do_form
    with patch.object(login_mod.Utils, "dismiss_save_login_modal"):
        assert il.login() is True
    assert form.execute.call_count == 1


# ------------------------------------------------------------ UA
def test_user_agent_matches_engine_family():
    assert re.search(r"Gecko/\S+ Firefox/", mobile_user_agent("firefox"))
    assert "Chrome/" not in mobile_user_agent("firefox")
    assert "Chrome/" in mobile_user_agent("chromium")
    assert "Firefox/" not in mobile_user_agent("chromium")
    webkit = mobile_user_agent("webkit")
    assert "Safari/" in webkit and "Chrome/" not in webkit and "Firefox/" not in webkit
    for fam in ("firefox", "chromium", "webkit"):
        assert "Mobile" in mobile_user_agent(fam)
    with pytest.raises(ValueError):
        mobile_user_agent("opera")


def test_selenium_firefox_does_not_declare_chrome():
    captured = {}

    def fake_firefox(service=None, options=None):
        captured.update(options.preferences)
        return MagicMock()

    with patch.object(login_mod.webdriver, "Firefox", side_effect=fake_firefox), \
            patch.object(login_mod, "GeckoDriverManager") as gdm:
        gdm.return_value.install.return_value = "geckodriver"
        il = object.__new__(login_mod.InstaLogin)
        il._webdriver_factory, il._stealth_mode = None, "firefox"
        il.init_driver(headless=True)
    assert captured["general.useragent.override"] == mobile_user_agent("firefox")


@pytest.mark.parametrize("bt", ["chromium", "firefox", "webkit"])
def test_playwright_context_ua_per_browser_type(bt):
    from instat.engines.playwright_engine import PlaywrightEngine
    eng = PlaywrightEngine(browser_type=bt)
    assert eng._context_kwargs()["user_agent"] == mobile_user_agent(bt)


def test_playwright_restore_uses_shared_classifier():
    src = (ROOT / "instat" / "engines" / "playwright_engine.py").read_text(encoding="utf-8")
    assert "classify_restored_session" in src
    assert "'/accounts/login' not in self._page.url" not in src


# ------------------------------------------------------------ testes sem rede / marcador real
def test_login_unit_tests_stub_webdriver_manager():
    assert "GeckoDriverManager" in (ROOT / "tests" / "test_login.py").read_text(encoding="utf-8")


def test_real_marker_registered_and_excluded_from_ci():
    py = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'"real:', py)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "not real" in ci
    conftest = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "INSTAT_REAL_TESTS" in conftest
