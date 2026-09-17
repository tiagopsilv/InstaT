"""Renderiza os 7 prints da F5 a partir de prints.json, faults.json e dos logs de teste."""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(os.path.join(HERE, "prints.json"), encoding="utf-8"))
F = json.load(open(os.path.join(HERE, "faults.json"), encoding="utf-8"))
RED, YEL, GRN, HEAD = "#f7d4d4", "#fff2cc", "#d9f2d9", "#dde3ea"


def table(ax, header, rows, colors=None, widths=None, fs=10, title=None):
    ax.axis("off")
    if title:
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    t = ax.table(cellText=[[str(c) for c in r] for r in rows], colLabels=header, loc="upper left",
                 cellLoc="left", colWidths=widths)
    t.auto_set_font_size(False)
    t.set_fontsize(fs)
    t.scale(1, 1.45)
    for j in range(len(header)):
        t[0, j].set_facecolor(HEAD)
        t[0, j].set_text_props(weight="bold")
    for i, c in enumerate(colors or [], start=1):
        if c:
            for j in range(len(header)):
                t[i, j].set_facecolor(c)


def save(fig, name):
    fig.savefig(os.path.join(HERE, name), dpi=110, bbox_inches="tight")
    plt.close(fig)


# 01 antes do crash
a = P["antes_crash"]
fig, axes = plt.subplots(2, 1, figsize=(12, 6.2), gridspec_kw={"height_ratios": [3, 1.3]})
fig.suptitle("F5 · 01 antes do crash — servidor local paginado (300 membros, 50 por página)", x=0.01, ha="left")
table(axes[0], ["pos", "attempt_id", "qualidade", "itens"],
      [[p["pos"], p["attempt"], p["quality"], p["n"]] for p in a["pages"]],
      [GRN] * len(a["pages"]), [0.1, 0.3, 0.3, 0.3], title=f"páginas CONFIRMADAS no banco (next_pos = {a['next_pos']})")
table(axes[1], ["attempt_id no spool", "pos", "com resposta"],
      [[e["attempt"], e["pos"], e["com_resposta"]] for e in a["spool_pendente"]], [YEL],
      [0.4, 0.2, 0.4], title=f"falha: {a['falha']}")
save(fig, "01-antes-crash.png")

# 02 retomada
r = P["retomada"]
fig, axes = plt.subplots(2, 1, figsize=(12, 7))
fig.suptitle("F5 · 02 retomada — processo/conexão nova relê o spool e continua", x=0.01, ha="left")
table(axes[0], ["pos", "attempt_id", "qualidade", "itens"],
      [[p["pos"], p["attempt"], p["quality"], p["n"]] for p in r["pages"]],
      [GRN] * len(r["pages"]), [0.1, 0.3, 0.3, 0.3],
      title=f"páginas após retomada (next_pos = {r['next_pos']}; spool pendente = {len(r['spool_pendente'])})")
w2 = r["resultado_worker2"]
rows = [["commit repetido do spool (mesmo attempt_id)", f"{w2['recovered_from_spool']} → duplicata {w2['duplicates']}"],
        ["páginas novas confirmadas", w2["committed"]], ["reprocessamentos (resposta perdida)", w2["reprocessed"]],
        ["parada", w2["stop"]], ["run_result", f"{r['run_result']['status']} / {r['run_result']['end_evidence']} / "
                                               f"{r['run_result']['collected']} membros"],
        ["membros == conjunto conhecido", r["membros_iguais_ao_conjunto"]]]
table(axes[1], ["medida", "valor"], rows, [GRN, GRN, None, GRN, GRN, GRN], [0.5, 0.5], title="worker 2")
save(fig, "02-retomada.png")

# 03 integridade (100 injeções, N = 1000)
i, s = F["integrity"], F["stats"]
fig, ax = plt.subplots(figsize=(13, 5.6))
fig.suptitle("F5 · 03 integridade — 100 falhas injetadas (5 pontos × 20 posições), N = 1.000 membros conhecidos",
             x=0.01, ha="left")
rows = [
    ["PRAGMA integrity_check", i["integrity"], "ok"],
    ["páginas trusted duplicadas por (run, pos)", i["dup_trusted"], "0"],
    ["next_pos × páginas trusted", f"{i['next_pos']} × {i['trusted_pages']}", "iguais (20)"],
    ["observações trusted / usernames distintos", f"{i['observations']} / {i['distinct_usernames']}", "1000 / 1000"],
    ["linhas em members / member_pk distintos", f"{i['member_rows']} / {i['distinct_member_pk']}", "1000 / 1000"],
    ["run_result", f"{F['run_result']['status']} ({F['run_result']['end_evidence']})", "complete"],
    ["quedas injetadas", s["crashes"], "100"],
    ["reprocessamentos (resposta não gravada no spool)", s["reprocessed"], "> 0, reportado"],
    ["commits repetidos pelo spool → duplicata", f"{s['recovered_from_spool']} → {s['duplicates']}", "> 0"],
    ["commits sem queda posterior", s["committed"], "0 aqui (ver nota)"],
]
colors = [GRN] * 6 + [None, YEL, YEL, None]
table(ax, ["verificação", "obtido", "esperado"], rows, colors, [0.42, 0.22, 0.36], fs=10)
fig.text(0.01, 0.02, "nota: cada posição sofreu queda after_commit depois do commit; o commit foi reconhecido "
                     "como duplicata na retomada (20 → 20), por isso 'commits sem queda posterior' = 0.", fontsize=9)
save(fig, "03-integridade.png")

# 04 heartbeat e motivos
h = P["heartbeat"]
fig, ax = plt.subplots(figsize=(13, 4.6))
fig.suptitle("F5 · 04 heartbeat (thread + conexão próprias, ttl 0,9 s) e motivos", x=0.01, ha="left")
rows = [
    ["conta_a: renovações, depois revogada (needs_attention)", f"{h['conta_a']['renovacoes']} renovações → "
     f"{h['conta_a']['status_final']} (falhas {h['conta_a']['falhas']})"],
    ["conta_b: outra geração assume a concessão", f"{h['conta_b']['renovacoes']} renovações → "
     f"{h['conta_b']['status_final']} (falhas {h['conta_b']['falhas']})"],
    ["can_send antes da revogação", h["can_send_antes_da_revogacao"]],
    ["can_send logo após revogação", h["can_send_logo_apos_revogacao"]],
    ["can_send depois da concessão expirar", h["can_send_depois_de_expirar"]],
    ["reivindicação pela conta revogada", h["reivindicacao_conta_revogada"]],
    ["liberação sem sessão validada", h["liberacao_sem_sessao_validada"]],
]
table(ax, ["situação", "resultado (verde = comportamento exigido)"], rows, [GRN] * 7, [0.45, 0.55], fs=10)
save(fig, "04-heartbeat-e-motivos.png")

# 05 progresso sem cursor
fig, axes = plt.subplots(4, 1, figsize=(14, 13))
fig.suptitle("F5 · 05 progresso sem cursor — sequências SINTÉTICAS (não Android); K = S = 3", x=0.01, ha="left")
labels = {"sem_evidencia_de_fim": "K telas sem novos, sem contador → fim desconhecido",
          "contador_exato_compativel": "K telas sem novos + contador exato compatível → fim confirmado",
          "tela_repetida": "tela repetida S vezes → travada",
          "lacuna_de_continuidade": "tela sem sobreposição + marcador de fim → parcial por lacuna"}
for ax, (k, v) in zip(axes, P["scan"].items()):
    rows = [[t["tela"], t.get("resultado", ""), t.get("estado", ""), t.get("evidencia") or "", t.get("sem_novos", ""),
             t.get("repetidas", ""), t.get("lacunas", "")] for t in v["telas"]]
    table(ax, ["tela", "commit", "estado", "evidência", "sem novos", "repetidas", "lacunas"], rows, None,
          [0.16, 0.16, 0.14, 0.22, 0.1, 0.1, 0.1], fs=9,
          title=f"{labels[k]}  →  run_result: {v['status']} {v['motivos']}")
save(fig, "05-progresso-sem-cursor.png")

# 06 seleção e histórico
jv = P["job_view"]
v = jv["view"]
fig, axes = plt.subplots(2, 1, figsize=(13, 7.5), gridspec_kw={"height_ratios": [1.2, 2]})
fig.suptitle("F5 · 06 seleção e histórico — run 1 Ana/Bruno, run 2 Ana/Carla, run 3 falha técnica", x=0.01, ha="left")
table(axes[0], ["run", "leitura", "run_result (só a própria execução)"],
      [[r, jv["execucoes"][r], f"{x['status']} {x['members']} {x['reasons']}"] for r, x in jv["run_results"].items()],
      None, [0.08, 0.35, 0.57])
rows = [["selected_run_id / status", f"{v['selected_run_id']} / {v['selected_run_status']}"],
        ["selected_members", v["selected_members"]],
        ["last_attempt (separada)", f"run {v['last_attempt_run_id']} {v['last_attempt_status']} "
                                    f"stop={v['last_attempt_stop_reason']}"],
        ["datas: seleção × última tentativa", f"{v['selected_run_at']} × {v['last_attempt_at']}"],
        ["history_observed", list(v["history_observed"])],
        ["history_label", v["history_label"]],
        ["campo de remoção/unfollow", "inexistente"]]
table(axes[1], ["campo de job_view", "valor"], rows, [GRN, GRN, YEL, None, None, YEL, GRN], [0.3, 0.7])
save(fig, "06-selecao-e-historico.png")

# 07 backup online
b = P["backup"]
fig, ax = plt.subplots(figsize=(13, 6))
fig.suptitle("F5 · 07 backup online — passo único por conexão própria, com heartbeat e escritor ativos", x=0.01,
             ha="left")
ver = b["verificacao"]
rows = [
    ["tamanho do banco (inclui carga de volume rotulada)", f"{b['tamanho_banco_mb']} MB"],
    ["backup_online", f"{b['online']['status']}, {b['online']['steps']} passo, {b['online']['elapsed']:.2f} s"],
    ["escritas durante cópia + verificação", b["escritas_durante_copia"]],
    ["escritas não trusted no total", f"{b['escritas_nao_trusted']} de {b['escritas_total']}"],
    ["heartbeat durante o período", f"{b['heartbeat']['renovacoes']} renovações, {b['heartbeat']['falhas']} falhas, "
                                    f"{b['heartbeat']['status']}"],
    ["verificação pela cópia", f"ok={ver['ok']} integrity={ver['integrity']} dup={ver['dup_trusted']} "
                               f"next_pos≠={ver['next_pos_mismatch']} marcador={ver['marker']['pages']} páginas"],
    ["comparado com origem viva", f"{ver['compared_with_live_origin']} (origem terminou com "
                                  f"{b['paginas_origem_ao_final']} páginas)"],
    ["manutenção sem drenar (recusa exigida)", b["manutencao_nao_drenada"]["status"]],
    ["manutenção drenada", f"{b['manutencao_drenada']['status']}, digest_equal={b['manutencao_drenada']['digest_equal']}"],
    ["backup a partir de conexão com transação aberta", b["backup_com_transacao_aberta"]],
]
table(ax, ["verificação", "resultado"], rows, [None, GRN, GRN, GRN, GRN, GRN, YEL, GRN, GRN, GRN], [0.42, 0.58])
save(fig, "07-backup-online.png")
print("ok")
