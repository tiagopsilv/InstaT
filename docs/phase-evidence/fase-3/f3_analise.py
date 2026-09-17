"""F3 passo 1 — análise medida (fakes; sem rede, sem browser, sem conta).

M1 get_profile com engine sem `_driver` (ex.: httpx) → erro
M2 engine que SABE ler perfil (tem dados de perfil) mas sem `_driver` → mesmo erro
M3 parte B do teste de contrato: sem o `_FakeDriver` injetado, o snippet quebra
M4 subclasse legada de BaseEngine (só métodos abstratos) é instanciável hoje
M5 inventário de pontos que dependem de `_driver`/Selenium fora das engines
"""
import os
import re
import subprocess
import sys
from typing import Optional, Set

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from loguru import logger  # noqa: E402

logger.remove()

from instat import InstaExtractor  # noqa: E402
from instat.engines.base import BaseEngine  # noqa: E402


class LegacyEngine(BaseEngine):
    """Subclasse escrita contra a interface atual (só abstratos)."""

    def __init__(self, data=None):
        self._data = data or {"followers": ["a"], "following": ["b"]}

    def login(self, username, password, **kw):
        return True

    def extract(self, profile_id, list_type, existing_profiles: Optional[Set[str]] = None,
                max_duration=None, on_batch=None):
        return set(self._data[list_type])

    def get_total_count(self, profile_id, list_type):
        return len(self._data[list_type])

    def quit(self):
        pass

    @property
    def name(self):
        return "legacy-fake"

    @property
    def is_available(self):
        return True


class ProfileCapableEngine(LegacyEngine):
    """Tem os metadados do perfil em memória, mas nenhum `_driver`."""

    profile_data = {"username": "alvo", "full_name": "Alvo", "followers_count": 10}

    @property
    def name(self):
        return "profile-capable-fake"


def try_get_profile(engine):
    ext = InstaExtractor(username="u", password="p", headless=True, engines=[engine])
    try:
        p = ext.get_profile("alvo")
        return f"ok: {p.username} {p.full_name} {p.followers_count}"
    except Exception as e:  # noqa: BLE001
        return f"{type(e).__name__}: {e}"


print("=== M1 engine sem _driver (legada, sem capacidade de perfil)")
print("  ", try_get_profile(LegacyEngine()))
print("=== M2 engine com dados de perfil, sem _driver")
print("  ", try_get_profile(ProfileCapableEngine()))

print("=== M3 parte B do contrato sem o _FakeDriver")
import test_public_api_contract as C  # noqa: E402

meta = {"og:description": "1,894 Followers, 1,892 Following, 123 Posts - @target",
        "og:title": "Target Name (@target) • Instagram photos and videos"}
fake = C._FakeEngine(followers=["alice"], following=["dave"], meta=meta)
print("   com _driver injetado:", try_get_profile(fake))
del fake._driver
print("   sem _driver:          ", try_get_profile(fake))

print("=== M4 subclasse legada instanciável")
print("  ", type(LegacyEngine()).__name__, "ok")

print("=== M5 inventário de dependências de _driver fora de selenium/playwright/login")
out = subprocess.run(["git", "grep", "-n", "-E", r"_driver|self\.driver|insta_login", "--", "instat/*.py",
                      "instat/engines/engine_manager.py"], cwd=ROOT, capture_output=True, text=True).stdout
for line in out.splitlines():
    if re.search(r"modal_interaction|scroll_loop|login_flow|login\.py|selenium_engine|playwright_engine", line):
        continue
    print("  ", line.strip()[:150])
