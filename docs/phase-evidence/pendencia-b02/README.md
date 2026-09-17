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

## Medição no CI (decisão do Tiago: medir antes de decidir o limite)

- **Origem:** PR #15 (somente CI, fechado sem merge) e workflow temporário `b02-diagnostico.yml` na branch `ci/b02-diagnostico`.
- **Run:** `35239204440`.
- **Runner:** GitHub `ubuntu-latest`, 4 vCPU, SQLite 3.45.1.
- **Dados:** [`b02_ci_312.json`](b02_ci_312.json), [`b02_ci_313.json`](b02_ci_313.json).

| Python | Condição | Pior (máx.) | Mediana do pior | ≥ 0,1 s | ≥ 0,5 s | Falhas |
|---|---|---|---|---|---|---|
| 3.12 | base | 0,003 s | 0,001 s | 0 | 0 | 0 |
| 3.12 | CPU saturada | 0,063 s | 0,003 s | 0 | 0 | 0 |
| 3.12 | `wal_autocheckpoint=0` | 0,019 s | 0,001 s | 0 | 0 | 0 |
| 3.12 | CPU saturada + `wal_autocheckpoint=0` | 0,070 s | 0,003 s | 0 | 0 | 0 |
| 3.13 | base | 0,019 s | 0,002 s | 0 | 0 | 0 |
| 3.13 | CPU saturada | 0,036 s | 0,004 s | 0 | 0 | 0 |
| 3.13 | `wal_autocheckpoint=0` | 0,009 s | 0,002 s | 0 | 0 | 0 |
| 3.13 | CPU saturada + `wal_autocheckpoint=0` | 0,035 s | 0,003 s | 0 | 0 | 0 |

**Cenário B02 original repetido:** 40/40 passaram em 3.12 e 40/40 em 3.13.

**Leitura:**
- **Não reproduzido:** 200 execuções instrumentadas e 80 do B02 original no runner. O maior valor medido no CI foi **0,070 s**, cerca de 7× abaixo do limite; o local, 0,172 s com CPU saturada.
- **O caso isolado de 0,529 s continua sem causa confirmada.** O mais compatível com os dados é um evento raro do runner compartilhado (pausa de escalonamento ou de disco), não um comportamento sistemático do heartbeat ou do backup. **H1 segue sem evidência**, e H2/H3 não foram isoladas.

## Situação

**Causa não confirmada; distribuição medida.** O limite `< 0,5 s` do B02 **não foi alterado**: com o pior caso medido no CI em 0,070 s, os dados não justificam afrouxar a expectativa.

**Recomendação com base nos dados:** manter o limite e tratar como intermitência rara conhecida (caminho 1). Se voltar a ocorrer, reexecutar o job e registrar a data e o valor aqui; duas ocorrências novas reabrem a investigação (H3, disco do runner).

Caminhos possíveis, que dependem de decisão:
1. **Manter o limite** e tratar como intermitência conhecida: reexecutar o job no CI quando ocorrer, e registrar.
2. **Medir no CI:** rodar este diagnóstico num PR "somente CI" para obter a distribuição real no runner e só então decidir o limite com dados.
3. **Revisar o limite** com base nessa distribuição. Mudança de expectativa, exige aprovação.
