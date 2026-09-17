# Pendência — B02 intermitente no CI (latência do heartbeat)

**Origem:** 1ª execução do CI da F3 (run `35217993911`, Linux, Python 3.13).
- **Medido:** o cenário B02 da F5 (backup incremental com heartbeat e escritor) teve pior latência de heartbeat de **0,529 s**, e o limite do cenário é `< 0,5 s`.
- **Normal no resto:** 0 falhas de heartbeat, status `timeout` correto.
- **Reexecução:** passou. Os CIs das F4 e F5 também passaram.

## Medição local

Script: [`b02_diagnostico.py`](b02_diagnostico.py). Dados: [`b02_313.json`](b02_313.json). Reproduz o B02 com instrumentação por heartbeat.

| Ambiente | Detalhe |
|---|---|
| Plataforma | Windows 11 |
| Python | 3.13 |
| Repetições | 15 por condição, 60 no total |

| Condição | Pior latência (máx.) | Mediana do pior | Execuções com pior ≥ 0,1 s | ≥ 0,5 s | Falhas |
|---|---|---|---|---|---|
| base | 0,041 s | 0,025 s | 0 | 0 | 0 |
| CPU saturada (processos em laço ocupado = nº de CPUs) | 0,172 s | 0,044 s | 1 | 0 | 0 |
| heartbeat com `wal_autocheckpoint=0` | 0,078 s | 0,031 s | 0 | 0 | 0 |
| CPU saturada + `wal_autocheckpoint=0` | 0,090 s | 0,038 s | 0 | 0 | 0 |

## Leitura

- **Não reproduzido localmente:** nenhuma das 60 execuções chegou perto de 0,5 s.
- **H2 (contenção de CPU/escalonamento):** coerente. Saturar a CPU multiplicou o pior caso por ~4 (0,041 → 0,172 s), mas não alcançou o valor do CI.
- **H1 (checkpoint automático no commit do heartbeat):** **sem evidência a favor.** Desligar o autocheckpoint do heartbeat não reduziu o pior caso.
  - **Limitação do instrumento:** a coluna "checkpoint feito pelo heartbeat" usa a queda do tamanho do arquivo WAL, mas checkpoint passivo **não encolhe** o arquivo. Esse sinal é inválido e não foi usado na conclusão.
- **H3 (latência de `fsync`/disco do runner compartilhado):** não medida. Em WAL com `synchronous=FULL` (padrão do SQLite), cada commit sincroniza o disco, e o disco do runner não está sob controle. Continua como hipótese.

## Situação

**Causa não confirmada.** O limite `< 0,5 s` do B02 **não foi alterado**, porque isso mudaria a expectativa do cenário.

Caminhos possíveis, que dependem de decisão:
1. **Manter o limite** e tratar como intermitência conhecida: reexecutar o job no CI quando ocorrer, e registrar.
2. **Medir no CI:** rodar este diagnóstico num PR "somente CI" para obter a distribuição real no runner e só então decidir o limite com dados.
3. **Revisar o limite** com base nessa distribuição. Mudança de expectativa, exige aprovação.
