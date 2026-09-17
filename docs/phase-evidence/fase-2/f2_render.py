"""Renderiza os prints da F2 a partir de cenarios.json (gerado por f2_prints.py)."""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(HERE, "cenarios.json"), encoding="utf-8"))

RED, YEL, GRN = "#f7d4d4", "#fff2cc", "#d9f2d9"


def table(ax, header, rows, colors=None, widths=None, fontsize=10):
    ax.axis("off")
    t = ax.table(cellText=rows, colLabels=header, loc="upper center", cellLoc="left",
                 colWidths=widths)
    t.auto_set_font_size(False)
    t.set_fontsize(fontsize)
    t.scale(1, 1.5)
    for j in range(len(header)):
        t[0, j].set_facecolor("#dde3ea")
        t[0, j].set_text_props(weight="bold")
    for i, c in enumerate(colors or [], start=1):
        if c:
            for j in range(len(header)):
                t[i, j].set_facecolor(c)


def timeline_rows(scn):
    rows, colors = [], []
    for e in scn["timeline"]:
        detail = []
        for k in ("n", "signal", "cause", "action", "delay_s", "reason", "error"):
            if e.get(k) not in (None, "", 0, 0.0):
                detail.append(f"{k}={e[k]}")
        rows.append([f"{e['t']:.1f}", e["key"][0], e["event"], e["state"], "  ".join(detail)])
        colors.append(RED if e["event"] == "stop" else YEL if e["event"] == "error" else
                      GRN if e["event"] == "success" else None)
    return rows, colors


def footer(fig, scn, y=0.02):
    fig.text(0.01, y, f"requisições ao servidor local: {[s for _, s in scn['requisicoes']]}   "
                      f"logins: {scn['logins']}   resultado: {scn['resultado']}   "
                      f"motivo terminal: {scn['motivo_terminal']}",
             fontsize=10, family="monospace")
    fig.text(0.01, y - 0.05, "attempt n=1 é o login sob demanda da conta (também passa pelo governador); "
                             "as demais são páginas de seguidores.", fontsize=10)


# 01 timeline
s = R["timeline"]
rows, colors = timeline_rows(s)
fig, ax = plt.subplots(figsize=(15, 1.2 + 0.42 * len(rows)))
fig.suptitle("F2 · 01 timeline — HttpxEngine real × servidor local; relógio falso (t em segundos)",
             fontsize=13, x=0.01, ha="left")
table(ax, ["t", "conta", "evento", "estado", "detalhe"], rows, colors, [0.05, 0.14, 0.08, 0.1, 0.63])
footer(fig, s)
fig.savefig(os.path.join(HERE, "01-timeline.png"), dpi=110, bbox_inches="tight")

# 02 orçamento
b = R["orcamento"]
u, pol = b["uso"], b["politica"]
fig, ax = plt.subplots(figsize=(14, 3.8))
fig.suptitle("F2 · 02 orçamento por chave (conta, operação) — parada por budget:bytes",
             fontsize=13, x=0.01, ha="left")
rows = [
    ["tentativas", str(u["attempts"]), str(pol["max_attempts"]), "dentro"],
    ["bytes (Content-Length)", str(u["bytes"]), str(pol["max_bytes"]),
     "ESTOUROU → parada antes da próxima página" if u["bytes"] > pol["max_bytes"] else "dentro"],
    ["duração (relógio falso, s)", f"{u['elapsed_s']:.1f}", "sem teto", "não configurado"],
    ["páginas pedidas", str(len(b['requisicoes'])), "—", "4ª página não foi pedida"],
    ["perfis mantidos", b['resultado'], "—", "a 3ª página, já paga, foi processada"],
]
table(ax, ["medida", "uso", "teto", "situação"], rows,
      [GRN, RED, YEL, GRN, GRN], [0.26, 0.2, 0.1, 0.5], fontsize=11)
footer(fig, b)
fig.savefig(os.path.join(HERE, "02-orcamento.png"), dpi=110, bbox_inches="tight")

# 03 alerta
fig, axes = plt.subplots(2, 1, figsize=(15, 7.5))
fig.suptitle("F2 · 03 alertas — challenge e proxy param a cascata; nenhuma troca de conta nem de engine",
             fontsize=13, x=0.01, ha="left")
for ax, name in zip(axes, ("challenge", "proxy")):
    sc = R[name]
    stop = [e for e in sc["timeline"] if e["event"] == "stop"][-1]
    rows = [
        ["cenário", sc["cenario"]],
        ["contas no pool / logins feitos", f"2 / {sc['logins']}"],
        ["engines httpx configurados", str(sc["engines_tentados"])],
        ["requisições ao servidor (todas)", str([s for _, s in sc["requisicoes"]])],
        ["motivo terminal", str(sc["motivo_terminal"])],
        ["estado da chave", f"{stop['key'][0]} → {sc['uso']['state']}"],
        ["resultado", sc["resultado"]],
    ]
    table(ax, ["campo", "valor"], rows,
          [None, GRN, GRN, None, RED, RED if sc["uso"]["state"] != "active" else YEL, None],
          [0.3, 0.7], fontsize=11)
fig.savefig(os.path.join(HERE, "03-alerta.png"), dpi=110, bbox_inches="tight")
print("ok")
