"""Renderiza a evidência da revisão v13.7 a partir dos JSON (sem digitar resultados)."""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
red = json.load(open(os.path.join(HERE, "results_red2_v136.json"), encoding="utf-8"))
green = json.load(open(os.path.join(HERE, "results_green_v137.json"), encoding="utf-8"))
by_id = {r["id"]: r for r in red["tests"]}
rows = [(g["id"], g["title"], by_id[g["id"]]["status"], g["status"]) for g in green["tests"]]
colors = {"PASS": "#d9f2d9", "FAIL": "#f7d4d4", "API_AUSENTE": "#fff2cc"}

fig, ax = plt.subplots(figsize=(15, 0.9 + 0.245 * len(rows)))
ax.axis("off")
table = ax.table(cellText=[[r[0], r[1], r[2], r[3]] for r in rows],
                 colLabels=["ID", "Comportamento exigido (v13.7)", "modelo v13.6 (antes)", "modelo v13.7 (depois)"],
                 colWidths=[0.05, 0.69, 0.13, 0.13], loc="upper center", cellLoc="left")
table.auto_set_font_size(False)
table.set_fontsize(8)
for (r, col), cell in table.get_celld().items():
    if r == 0:
        cell.set_text_props(weight="bold")
    elif col in (2, 3):
        cell.set_facecolor(colors.get(cell.get_text().get_text(), "white"))
ax.set_title(
    f"Revisão v13.7 — modelos SQLite isolados (SQLite {green['sqlite']}, Python {green['python']}); sem InstaT/Android/Instagram/proxy\n"
    f"antes (v13.6, {red['started_at']}): {red['counts']}   |   depois (v13.7, {green['started_at']}): {green['counts']}\n"
    "API_AUSENTE = método/coluna inexistente no modelo antigo; não é falha comportamental",
    fontsize=9, loc="left")
out = os.path.join(HERE, "05-revisao-v13.7.png")
fig.savefig(out, dpi=120, bbox_inches="tight")
print(out)
