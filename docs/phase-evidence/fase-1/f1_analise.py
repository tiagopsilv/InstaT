"""F1 passo 1 — análise medida (sem Instagram, sem conta real).

Mede no código atual da branch f1 (= F0):
  A. SessionCache: leituras inválidas, fronteira de TTL, dependência do diretório corrente
  B. SessionRestorer/PlaywrightEngine: sucesso em estados de challenge e sem validação de identidade
  C. InstaLogin.login(): caminho de restauração pula o BlockDetector
  D. User-Agent: Selenium/Firefox e Playwright por browser_type
  E. Logins de formulário em 10 reinícios (sessão válida × sessão com mais de 1 h)
  F. Testes "offline" que chamam o webdriver-manager (rede)
"""
import json
import os
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

WT = r"C:\Projetos\InstaT-wt-f1"
sys.path.insert(0, WT)
os.chdir(tempfile.mkdtemp(prefix="f1_cwd_"))

from loguru import logger  # noqa: E402

logger.remove()

from instat import login as login_mod  # noqa: E402
from instat.login_flow import SessionRestorer  # noqa: E402
from instat.session_cache import SessionCache  # noqa: E402


def title(t):
    print(f"\n=== {t}")


# ------------------------------------------------------------------ A
title("A. SessionCache")
d = tempfile.mkdtemp()
cache = SessionCache(cache_dir=d)
cases = {
    "json_truncado": '{"cookies": [',
    "sem_saved_at": json.dumps({"cookies": []}),
    "saved_at_texto": json.dumps({"cookies": [], "saved_at": "ontem"}),
    "cookies_string": json.dumps({"cookies": "abc", "saved_at": time.time()}),
    "saved_at_futuro": json.dumps({"cookies": [{"name": "sessionid", "value": "x"}], "saved_at": time.time() + 86400}),
}
for name, raw in cases.items():
    open(os.path.join(d, name + ".json"), "w", encoding="utf-8").write(raw)
    try:
        r = cache.load(name)
        print(f"  {name:16} -> retornou {type(r).__name__}")
    except Exception as e:  # noqa: BLE001
        print(f"  {name:16} -> exceção {type(e).__name__}")
open(os.path.join(d, "idade_3600.json"), "w").write(json.dumps({"cookies": [1], "saved_at": 1000.0}))
with patch("instat.session_cache.time.time", return_value=4600.0):
    print(f"  idade == 3600 s    -> {cache.load('idade_3600')!r} (aceito na fronteira)")
with patch("instat.session_cache.time.time", return_value=4601.0):
    print(f"  idade == 3601 s    -> {cache.load('idade_3600')!r}")
cwd1, cwd2 = tempfile.mkdtemp(), tempfile.mkdtemp()
for cwd in (cwd1, cwd2):
    os.chdir(cwd)
    SessionCache().save("u", [{"name": "sessionid", "value": "x"}])
print(f"  diretório padrão depende do cwd: {os.path.exists(os.path.join(cwd1, '.instat_sessions', 'u.json'))} e "
      f"{os.path.exists(os.path.join(cwd2, '.instat_sessions', 'u.json'))} (dois caches diferentes)")
os.chdir(cwd1)
src = open(os.path.join(WT, "instat", "session_cache.py"), encoding="utf-8").read()
print(f"  escrita atômica (os.replace/rename no save): {'os.replace' in src or '.replace(' in src and 'tmp' in src}")


# ------------------------------------------------------------------ B
title("B. SessionRestorer (Selenium) e restauração do Playwright")


class FakeDriver:
    def __init__(self, after):
        self._after, self.current_url, self._c = after, "https://www.instagram.com/", {}

    def get(self, url):
        self.current_url = url

    def add_cookie(self, c):
        self._c[c["name"]] = c

    def refresh(self):
        self.current_url = self._after

    def get_cookie(self, n):
        return self._c.get(n)

    def delete_all_cookies(self):
        self._c = {}


r = SessionRestorer("https://www.instagram.com")
false_pos = 0
urls = ["challenge/abc/", "checkpoint/", "accounts/suspended/", "auth_platform/codeentry/", "accounts/login/", ""]
for u in urls:
    ok = r.attempt(FakeDriver("https://www.instagram.com/" + u), [{"name": "sessionid", "value": "x"}])
    bad = ok and u not in ("",)
    false_pos += int(bad)
    print(f"  /{u:28} -> sucesso={ok}{'  <- FALSO POSITIVO' if bad else ''}")
ok_other = r.attempt(FakeDriver("https://www.instagram.com/"),
                     [{"name": "sessionid", "value": "x"}, {"name": "ds_user_id", "value": "999"}])
print(f"  cookies de OUTRO usuário (ds_user_id diferente do esperado) -> sucesso={ok_other} (sem validação de identidade)")
print(f"  falsos positivos de estado: {false_pos} de 4 estados de bloqueio")
pw_src = open(os.path.join(WT, "instat", "engines", "playwright_engine.py"), encoding="utf-8").read()
needle = "'/accounts/login' not in self._page.url"
has_url_only = needle in pw_src
print(f"  Playwright: restauração decide só por URL: {has_url_only}; confere sessionid: {'sessionid' in pw_src}")


# ------------------------------------------------------------------ C
title("C. InstaLogin.login(): restauração com challenge")
il = object.__new__(login_mod.InstaLogin)
il.driver = FakeDriver("https://www.instagram.com/challenge/abc/")
il.username, il.password = "u", "p"
il._session_cache = MagicMock(load=MagicMock(return_value=[{"name": "sessionid", "value": "x"}]))
il._session_restorer = SessionRestorer("https://www.instagram.com")
il._block_detector = login_mod.BlockDetector()
il._check_account_blocked = MagicMock(side_effect=AssertionError("BlockDetector chamado"))
il._get_form_login = MagicMock()
res = il.login()
print(f"  login() retornou {res}; BlockDetector chamado: {il._check_account_blocked.called}; "
      f"formulário aberto: {il._get_form_login.called}")


# ------------------------------------------------------------------ D
title("D. User-Agent")
captured = {}


def fake_firefox(service=None, options=None):
    captured["prefs"] = dict(options.preferences)
    drv = MagicMock()
    return drv


with patch.object(login_mod.webdriver, "Firefox", side_effect=fake_firefox), \
        patch.object(login_mod, "GeckoDriverManager") as gdm:
    gdm.return_value.install.return_value = "geckodriver"
    il2 = object.__new__(login_mod.InstaLogin)
    il2._webdriver_factory, il2._stealth_mode = None, "firefox"
    il2.init_driver(headless=True)
ua = captured["prefs"].get("general.useragent.override", "")
print(f"  Selenium/Firefox (Gecko) declara UA: {ua}")
print(f"  UA contém 'Firefox': {'Firefox' in ua}; contém 'Chrome': {'Chrome' in ua}  <- incoerente com o motor Gecko")
from instat.engines import playwright_engine as pw  # noqa: E402

for bt in ("chromium", "firefox", "webkit"):
    print(f"  Playwright browser_type={bt:9} usa o mesmo MOBILE_UA (Chrome 89/Android 8): "
          f"{'Chrome/89' in pw.MOBILE_UA}")


# ------------------------------------------------------------------ E
title("E. Logins de formulário em 10 reinícios (fakes, sem rede)")


def restarts(age_s):
    d2 = tempfile.mkdtemp()
    forms = 0
    now = [10_000.0]
    with patch("instat.session_cache.time.time", side_effect=lambda: now[0]):
        c = SessionCache(cache_dir=d2)
        c.save("u", [{"name": "sessionid", "value": "x"}])
        for _ in range(10):
            now[0] += age_s
            il3 = object.__new__(login_mod.InstaLogin)
            il3.driver = FakeDriver("https://www.instagram.com/")
            il3.username, il3.password = "u", "p"
            il3._session_cache = c
            il3._session_restorer = SessionRestorer("https://www.instagram.com")
            form = MagicMock()
            il3._get_form_login = MagicMock(return_value=form)
            il3._try_handle_email_challenge = MagicMock(return_value=False)
            il3._check_account_blocked = MagicMock()
            il3.close_keywords, il3.timeout = [], 1
            with patch.object(login_mod.Utils, "dismiss_save_login_modal"):
                il3.driver.get_cookies = lambda: [{"name": "sessionid", "value": "x"}]
                il3.login()
            forms += int(form.execute.called)
    return forms


print(f"  reinícios a cada 10 min (sessão com < 1 h): formulários abertos = {restarts(600)} de 10")
print(f"  reinícios a cada 2 h (cache descartado por idade): formulários abertos = {restarts(7200)} de 10")


# ------------------------------------------------------------------ F
title("F. Testes 'offline' que chamam o webdriver-manager (rede)")
tl = open(os.path.join(WT, "tests", "test_login.py"), encoding="utf-8").read()
print(f"  tests/test_login.py substitui webdriver.Firefox: {'webdriver.Firefox' in tl}; "
      f"substitui GeckoDriverManager: {'GeckoDriverManager' in tl}")
print("  CI do PR #4 (3.13): 5 falhas 'webdriver-manager rate-limited' nesses testes")
