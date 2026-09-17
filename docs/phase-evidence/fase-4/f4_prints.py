"""F4 passo 7 — prints a partir de execuções reais da disputa (processos locais + SQLite).

Usa o mesmo harness dos testes (tests/test_f4_dispute.py). Sem browser, sem rede, sem conta.
"""
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import test_f4_dispute as D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from instat.jobstore.store import JobStore  # noqa: E402

OUT = {}

# ------------------------------------------------------------ 01 e 02: disputa 2 processos
tmp = pathlib.Path(tempfile.mkdtemp())
path, t_blocked, events, errs, _ = D._run(tmp, 2, 167)
summary = D._check(events, t_blocked)
leases = [e for e in events if e["ev"] == "lease"]
OUT["disputa2"] = {"summary": summary, "errs": errs, "misses": sum(1 for e in events if e["ev"] == "miss"),
                   "leases": leases, "t_blocked": t_blocked}

# ------------------------------------------------------------ 03: processo morto
tmp3 = pathlib.Path(tempfile.mkdtemp())
path3, t_blocked3, events3, errs3, killed = D._run(tmp3, 2, 1500, slow_every=0, kill_one=True)
s3 = D._check(events3, t_blocked3)
l3 = [e for e in events3 if e["ev"] == "lease"] + [{**e, "end": e["until"]} for e in events3 if e["ev"] == "hold"]
p0_last = max((e for e in l3 if e["owner"] == "p0"), key=lambda e: e["start"], default=None)
p1_after = sorted((e for e in l3 if e["owner"] == "p1" and e["account"] == killed["account"]
                   and e["start"] > killed["at"]), key=lambda e: e["start"])
# liberação tardia com o token real de p0 no instante do kill (lido do banco pelo harness)
m = JobStore(path3, ttl=D.TTL, busy_timeout=D.TTL / 6, create=False)
old_tok = (killed["account"], "p0", killed["gen"])
live = m.c.execute("SELECT lease_until FROM accounts WHERE account=?", (killed["account"],)).fetchone()[0] or 0
time.sleep(max(0.0, live - time.time()) + 0.05)
new = m.acquire(killed["account"], "p_novo", now=None)
late_release = {"token_de_p0_no_kill": list(old_tok), "adquiriu_nova": new is not None,
                "release_do_token_antigo": m.release_lease(old_tok, now=None),
                "posse_nova_continua_valida": m.lease_valid(new, now=None) if new else None}
blocked_try = m.acquire("a_bloqueada", "p_novo", now=None)
state = sqlite3.connect(path3).execute("SELECT auth_state FROM accounts WHERE account='a_bloqueada'").fetchone()[0]
OUT["morto"] = {"summary": s3, "errs": errs3, "killed_at": killed, "p0_last": p0_last,
                "p1_primeiras_apos_morte": p1_after[:3], "liberacao_tardia": late_release,
                "a_bloqueada_concedida_agora": blocked_try is not None, "a_bloqueada_estado": state}

with open(os.path.join(HERE, "prints.json"), "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)

# ------------------------------------------------------------ render
COLORS = {"p0": "#4c78a8", "p1": "#f58518", "p2": "#54a24b", "p3": "#b279a2"}


def timeline(ax, leases, t0, window, title):
    accounts = ["a1", "a2", "a_bloqueada"]
    for e in leases:
        if e["start"] - t0 > window:
            continue
        y = accounts.index(e["account"])
        ax.barh(y, e["end"] - e["start"], left=e["start"] - t0, height=0.6, color=COLORS[e["owner"]],
                edgecolor="black" if e.get("slow") else None, linewidth=1.2 if e.get("slow") else 0,
                hatch="//" if e.get("slow") else None)
    ax.set_yticks(range(len(accounts)), accounts)
    ax.set_xlim(0, window)
    ax.set_xlabel("segundos desde a 1ª concessão")
    ax.set_title(title, loc="left", fontsize=11)


d = OUT["disputa2"]
t0 = min(e["start"] for e in d["leases"])
fig, axes = plt.subplots(2, 1, figsize=(15, 7), gridspec_kw={"height_ratios": [2, 1]})
fig.suptitle("F4 · 01 pool — 2 processos disputando 2 contas (+1 em needs_attention), SQLite compartilhado",
             x=0.01, ha="left")
timeline(axes[0], d["leases"], t0, 3.0, "posse por conta nos primeiros 3 s (cor = processo; hachurado = worker lento)")
axes[0].legend(handles=[Patch(color=COLORS["p0"], label="p0"), Patch(color=COLORS["p1"], label="p1")],
               loc="upper right")
s = d["summary"]
axes[1].axis("off")
rows = [["concessões registradas (todas)", s["leases"]], ["tentativas sem concessão", d["misses"]],
        ["intervalos sobrepostos na mesma conta", s["overlaps"]],
        ["concessões de a_bloqueada após needs_attention", s["blocked_grants"]],
        ["erros de processo", len(d["errs"])]]
t = axes[1].table(cellText=[[a, str(b)] for a, b in rows], colLabels=["verificação (execução inteira)", "valor"],
                  loc="upper left", cellLoc="left", colWidths=[0.5, 0.2])
t.auto_set_font_size(False)
t.set_fontsize(10)
t.scale(1, 1.5)
fig.savefig(os.path.join(HERE, "01-pool.png"), dpi=110, bbox_inches="tight")
plt.close(fig)

slow = [e for e in d["leases"] if e.get("slow")]
fig, axes = plt.subplots(2, 1, figsize=(15, 6.5), gridspec_kw={"height_ratios": [1.4, 1.2]})
fig.suptitle("F4 · 02 concessão expirada — worker lento passa do ttl (0,5 s); commit tardio", x=0.01, ha="left")
if slow:
    e = slow[0]
    around = [x for x in d["leases"] if x["account"] == e["account"] and e["start"] - 0.2 <= x["start"] <= e["until"] + 1.0]
    timeline(axes[0], around, e["start"] - 0.2, 1.8,
             f"conta {e['account']}: {e['owner']} (hachurado) expira; outro processo assume depois do fim")
rows = [[x["owner"], x["account"], f"{x['start'] - t0:.3f}", f"{x['until'] - t0:.3f}", x["late_commit"]] for x in slow[:8]]
axes[1].axis("off")
t = axes[1].table(cellText=rows, colLabels=["processo lento", "conta", "início (s)", "concessão até (s)",
                                            "commit após sleep ttl+0,25 s"], loc="upper left", cellLoc="left",
                  colWidths=[0.12, 0.08, 0.1, 0.14, 0.3])
t.auto_set_font_size(False)
t.set_fontsize(10)
t.scale(1, 1.4)
axes[1].text(0, -0.15, f"commits tardios: {s['late_commits']}, aceitos: {s['late_accepted']}",
             transform=axes[1].transAxes, fontsize=10)
fig.savefig(os.path.join(HERE, "02-lease-expirado.png"), dpi=110, bbox_inches="tight")
plt.close(fig)

mo = OUT["morto"]
fig, axes = plt.subplots(2, 1, figsize=(15, 6.5), gridspec_kw={"height_ratios": [1.4, 1.3]})
fig.suptitle("F4 · 03 worker obsoleto — p0 morto (kill) DETENDO concessão; recuperação e restrição mantida",
             x=0.01, ha="left")
kd = mo["killed_at"]
k = kd["at"]
window = [x for x in l3 if k - 0.6 <= x["start"] <= k + 1.2]
timeline(axes[0], window, k - 0.6, 1.8, f"azul = concessão de p0 (morto); vermelho = kill (detinha {kd['account']}); tracejado = fim da concessão de p0")
axes[0].axvline(0.6, color="red")
axes[0].axvline(0.6 + kd["lease_until"] - k, color="black", linestyle="--")
axes[1].axis("off")
lt = mo["liberacao_tardia"] or {}
first = mo["p1_primeiras_apos_morte"][0] if mo["p1_primeiras_apos_morte"] else None
rows = [["concessão de p0 no kill", f"{kd['account']} gen {kd['gen']}, vigente até {kd['lease_until'] - k:+.3f} s"],
        ["1ª concessão de p1 na MESMA conta", f"{first['start'] - k:+.3f} s (≥ fim da de p0: "
                                               f"{first['start'] >= kd['lease_until']})" if first else "nenhuma"],
        ["release_lease com token antigo de p0", lt.get("release_do_token_antigo")],
        ["nova posse adquirida / continua válida", f"{lt.get('adquiriu_nova')} / {lt.get('posse_nova_continua_valida')}"],
        ["sobreposições / concessões de a_bloqueada", f"{mo['summary']['overlaps']} / {mo['summary']['blocked_grants']}"],
        ["a_bloqueada concedida agora? / estado", f"{mo['a_bloqueada_concedida_agora']} / {mo['a_bloqueada_estado']}"]]
t = axes[1].table(cellText=[[a, str(b)] for a, b in rows], colLabels=["verificação", "resultado"],
                  loc="upper left", cellLoc="left", colWidths=[0.35, 0.45])
t.auto_set_font_size(False)
t.set_fontsize(10)
t.scale(1, 1.4)
fig.savefig(os.path.join(HERE, "03-worker-obsoleto.png"), dpi=110, bbox_inches="tight")
plt.close(fig)
print(json.dumps({"disputa2": s, "morto": mo["summary"], "liberacao_tardia": lt}, ensure_ascii=False))
