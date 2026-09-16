"""Testes ADICIONAIS escritos DEPOIS do modelo v13.6 — não contam como TDD.

Cobrem a exigência "não tratar toda violação de UNIQUE como sucesso idempotente".
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_repro import NOW, fresh, nxt, setup_job  # noqa: E402


def x01(M):
    """mesmo attempt_id com conteúdo diferente é erro, não sucesso"""
    m, _ = fresh(M)
    tok, run = setup_job(m)
    r1 = m.commit(tok, "J", run, attempt_id="att", pos=0, cursor_in=None, cursor_out="c1",
                  members=[(None, "u0")], now=NOW + 1)
    r2 = m.commit(tok, "J", run, attempt_id="att", pos=0, cursor_in=None, cursor_out="c1",
                  members=[(None, "OUTRO")], now=NOW + 2)
    ok = r1 == "committed:trusted" and r2.startswith("error")
    return ok, f"primeiro={r1} | mesmo attempt, conteúdo diferente={r2}"


def x02(M):
    """segunda leitura trusted da mesma posição (outra tentativa) é rejeitada, não aceita em silêncio"""
    m, _ = fresh(M)
    tok, run = setup_job(m)
    r1 = m.commit(tok, "J", run, attempt_id="a1", pos=0, cursor_in=None, cursor_out="c1",
                  members=[(None, "u0")], now=NOW + 1)
    r2 = m.commit(tok, "J", run, attempt_id="a2", pos=0, cursor_in=None, cursor_out="c1",
                  members=[(None, "u0")], now=NOW + 2)
    ok = r1 == "committed:trusted" and r2.startswith("rejected") and not r2.startswith("idempotente")
    return ok, f"primeira={r1} | segunda leitura da posição já confiável={r2}"


def main():
    from models_v135 import V135
    from models_v136 import V136
    out = {}
    for cls in (V135, V136):
        rows = []
        for fn in (x01, x02):
            ok, detail = fn(cls)
            rows.append({"id": fn.__name__.upper(), "title": fn.__doc__, "pass": ok, "detail": detail})
            print(f"{cls.name:26} {fn.__name__.upper()} {'PASS' if ok else 'FAIL'}  {fn.__doc__}\n    {detail}")
        out[cls.name] = rows
    with open(os.path.join(HERE, "results_extra.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
