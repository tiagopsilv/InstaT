"""F2 passo 1 — análise medida (fakes; sem rede, sem Instagram, sem sleeps reais).

Mede no código da branch f2 (= F1 entregue):
  M1 restrição/challenge numa conta → quantas outras contas o EngineManager tenta
  M2 429 numa conta → contas tentadas e esperas de backoff
  M3 challenge sem pool → a mesma conta continua em outro engine
  M4 httpx: 407 (5 causas), 403/502/503 do proxy, 5xx, timeout → exceção resultante
  M5 SmartBackoff: teto de tentativas e Retry-After
  M6 until_complete com engine sempre bloqueado → tentativas e segundos de espera
"""
import os
import sys
from unittest.mock import MagicMock, patch

WT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, WT)

from loguru import logger  # noqa: E402

logger.remove()

import instat.engines.engine_manager as em  # noqa: E402
from instat.exceptions import AccountBlockedError, BlockedError, RateLimitError  # noqa: E402
from instat.session_pool import SessionPool  # noqa: E402


def title(t):
    print(f"\n=== {t}")


class FakeEngine:
    def __init__(self, name, exc_factory):
        self.name, self._exc, self.logins, self.extracts = name, exc_factory, [], 0
        self.is_available = True

    def login(self, username, password, **kw):
        self.logins.append(username)

    def extract(self, *a, **kw):
        self.extracts += 1
        raise self._exc()


def pool3():
    return SessionPool([{"username": f"acc{i}", "password": "pw"} for i in range(3)])


with patch.object(em.ExtractionCheckpoint, "load", return_value=set()), \
        patch.object(em.ExtractionCheckpoint, "save"), patch.object(em.ExtractionCheckpoint, "clear"), \
        patch.object(em.SmartBackoff, "wait") as wait:
    title("M1 challenge/restrição com pool de 3 contas")
    eng = FakeEngine("selenium", lambda: AccountBlockedError("challenge", reason="challenge"))
    try:
        em.EngineManager([eng], session_pool=pool3()).extract("alvo", "followers")
    except Exception as e:
        print(f"  resultado: {type(e).__name__}")
    print(f"  contas usadas após a 1ª restrição: {len(set(eng.logins)) - 1} (login em {eng.logins})")
    print(f"  esperas de backoff: {wait.call_count}")

    title("M2 429 com pool de 3 contas")
    wait.reset_mock()
    eng = FakeEngine("httpx", lambda: RateLimitError("429"))
    try:
        em.EngineManager([eng], session_pool=pool3()).extract("alvo", "followers")
    except Exception as e:
        print(f"  resultado: {type(e).__name__}")
    print(f"  tentativas multiplicadas por outras contas: {len(eng.logins) - 1} (login em {eng.logins})")
    print(f"  esperas de backoff: {wait.call_count}")

    title("M3 challenge sem pool, 2 engines")
    e1 = FakeEngine("selenium", lambda: BlockedError("playwright blocked: /challenge/"))
    e2 = FakeEngine("httpx", lambda: RateLimitError("429"))
    try:
        em.EngineManager([e1, e2]).extract("alvo", "followers")
    except Exception as e:
        print(f"  resultado: {type(e).__name__}")
    print(f"  após challenge no 1º engine, o 2º engine (mesma conta) tentou: {e2.extracts > 0}")

title("M4 httpx: classificação de respostas")
from instat.engines.httpx_engine import HttpxEngine  # noqa: E402


def resp(status, headers=None, text=""):
    r = MagicMock(status_code=status, headers=headers or {}, text=text)
    r.json.side_effect = ValueError
    return r


cases = [
    ("407 NO_USER", resp(407, text="NO_USER")),
    ("407 TRAFFIC_EXHAUSTED", resp(407, text="TRAFFIC_EXHAUSTED")),
    ("407 THREADS_EXHAUSTED", resp(407, text="THREADS_EXHAUSTED")),
    ("407 PORT_NOT_ALLOWED", resp(407, text="PORT_NOT_ALLOWED")),
    ("407 USER_BLOCKED", resp(407, text="USER_BLOCKED")),
    ("403 PORT_BLOCKED (proxy)", resp(403, text="PORT_BLOCKED")),
    ("502 NO_HOST_CONNECTION (proxy)", resp(502, text="NO_HOST_CONNECTION")),
    ("503 NO_RAY (proxy)", resp(503, text="NO_RAY")),
    ("500 do serviço", resp(500)),
    ("429 Retry-After: 120", resp(429, {"Retry-After": "120"})),
]
for label, r in cases:
    eng = HttpxEngine()
    eng._client = MagicMock(get=MagicMock(return_value=r))
    with patch.object(eng, "_resolve_user_id", return_value="1"):
        try:
            eng.extract("alvo", "followers")
            out = "sem exceção"
        except Exception as e:
            out = type(e).__name__
            extra = f" retry_after={getattr(e, 'retry_after', '—')}" if "429" in label else ""
            out += extra
    print(f"  {label:32} -> {out}")
eng = HttpxEngine()
eng._client = MagicMock(get=MagicMock(side_effect=TimeoutError("read timeout")))
with patch.object(eng, "_resolve_user_id", return_value="1"):
    try:
        eng.extract("alvo", "followers")
    except Exception as e:
        print(f"  {'timeout':32} -> {type(e).__name__}")

title("M5 SmartBackoff")
from instat.backoff import SmartBackoff  # noqa: E402

with patch("instat.backoff.human_delay") as hd:
    b = SmartBackoff()
    for _ in range(50):
        b.wait()
print(f"  50 chamadas de wait() aceitas sem teto de tentativas: {hd.call_count == 50}")
import inspect  # noqa: E402

print(f"  wait() aceita Retry-After: {'retry_after' in inspect.signature(SmartBackoff.wait).parameters}")
print(f"  espera usa sleep real (human_delay → time.sleep): {'time.sleep' in inspect.getsource(sys.modules['instat.constants'])}")

title("M6 until_complete com engine sempre bloqueado")
import instat.extractor as ex  # noqa: E402

slept = []
fake = object.__new__(ex.InstaExtractor)
fake._engine_manager = MagicMock()
fake._engine_manager.get_total_count.return_value = 1000
fake._extract_with_export = MagicMock(side_effect=AccountBlockedError("challenge", reason="challenge"))
fake._log_account_blocked_diagnostic = MagicMock()
with patch.object(ex.time, "sleep", side_effect=slept.append):
    fake._extract_until_complete("alvo", "followers", 0.9, 3, 90.0, None)
print(f"  challenge em toda tentativa: {fake._extract_with_export.call_count} tentativas, "
      f"{len(slept)} esperas somando {sum(slept):.0f} s de sleep real")
