"""F2 passo 7 — cenários com HttpxEngine real contra servidor HTTP local roteirizado.

Relógio do governador é falso (FakeClock): as esperas não acontecem de verdade.
Nada sai da máquina: BASE_URL do engine aponta para 127.0.0.1.
Gera 01-timeline.png, 02-orcamento.png, 03-alerta.png e cenarios.json.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
WT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, WT)

from loguru import logger  # noqa: E402

logger.remove()

import httpx  # noqa: E402

import instat.engines.httpx_engine as hx  # noqa: E402
from instat.engines.engine_manager import EngineManager  # noqa: E402
from instat.exceptions import AllEnginesBlockedError  # noqa: E402
from instat.governor import FakeClock, Governor, Policy  # noqa: E402
from instat.session_pool import SessionPool  # noqa: E402

SCRIPT = []   # lista de (status, headers, body)
HITS = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        status, headers, body = SCRIPT.pop(0) if SCRIPT else (500, {}, "script vazio")
        HITS.append((self.path.split("?")[0], status))
        raw = body.encode()
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def page(names, next_id):
    return json.dumps({"users": [{"username": n} for n in names], "next_max_id": next_id})


srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
hx.BASE_URL = f"http://127.0.0.1:{srv.server_address[1]}"


def engine():
    e = hx.HttpxEngine()
    e._client = httpx.Client(timeout=5)
    return e


def run(name, script, policy, pool_accounts=None, engines=1):
    SCRIPT[:] = script
    HITS.clear()
    clock = FakeClock()
    gov = Governor(policy=policy, clock=clock, rng=lambda: 0.5)
    engs = [engine() for _ in range(engines)]
    pool = SessionPool([{"username": u, "password": "x"} for u in pool_accounts]) if pool_accounts else None
    mgr = EngineManager(engs, session_pool=pool, governor=gov,
                        default_credentials=None if pool else ("conta_teste_fake", "x"))
    logins = []
    metrics = {}
    with patch.object(hx.HttpxEngine, "_resolve_user_id", return_value="1"), \
            patch.object(hx.HttpxEngine, "login", lambda self, u, p, **k: logins.append(u)), \
            patch.object(hx, "human_delay"), \
            patch("instat.engines.engine_manager.ExtractionCheckpoint") as ck:
        ck.return_value.load.return_value = set()
        try:
            out = mgr.extract("alvo_fake", "followers", metrics_sink=metrics)
            result = f"{len(out)} perfil(is) (parcial)" if mgr.last_stop else f"{len(out)} perfil(is)"
        except AllEnginesBlockedError as e:
            result = f"{type(e).__name__}: 0 perfil"
    stop = mgr.last_stop.reason if mgr.last_stop else None
    key = next(iter(gov._keys)) if gov._keys else None
    return {
        "cenario": name, "resultado": result, "motivo_terminal": stop,
        "logins": logins, "requisicoes": HITS[:], "engines_tentados": engines,
        "uso": gov.usage(key) if key else None, "politica": vars(policy),
        "timeline": [{**e, "key": list(e["key"])} for e in gov.timeline],
    }


results = {}
results["timeline"] = run("503 com Retry-After → 200 → 429", [
    (503, {"Retry-After": "2"}, "unavailable"),
    (200, {}, page(["ana", "bruno"], "c1")),
    (429, {"Retry-After": "120"}, "rate limited"),
], Policy(max_transient_retries=3, backoff_cap_s=60, rate_limit_pause_s=900))

results["orcamento"] = run("teto de bytes", [
    (200, {}, page([f"user_{i:02d}" for i in range(0, 20)], "c1")),
    (200, {}, page([f"user_{i:02d}" for i in range(20, 40)], "c2")),
    (200, {}, page([f"user_{i:02d}" for i in range(40, 60)], "c3")),
    (200, {}, page([f"user_{i:02d}" for i in range(60, 80)], None)),
], Policy(max_bytes=1500))

results["challenge"] = run("challenge com pool de 2 contas e 2 engines", [
    (200, {}, page(["ana"], "c1")),
    (400, {}, '{"message":"checkpoint_required","status":"fail"}'),
], Policy(), pool_accounts=["conta_a_fake", "conta_b_fake"], engines=2)

results["proxy"] = run("proxy 407 TRAFFIC_EXHAUSTED com pool de 2 contas", [
    (407, {}, "TRAFFIC_EXHAUSTED"),
], Policy(), pool_accounts=["conta_a_fake", "conta_b_fake"], engines=2)

srv.shutdown()
with open(os.path.join(HERE, "cenarios.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
for k, v in results.items():
    print(f"{k:10} resultado={v['resultado']!r} motivo={v['motivo_terminal']!r} "
          f"logins={v['logins']} requisicoes={[s for _, s in v['requisicoes']]} uso={v['uso']}")
