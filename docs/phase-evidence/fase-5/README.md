# Fase 5 — Resultados e checkpoints atômicos

Branch: `f5/resultados-checkpoints-atomicos`, criada a partir de `4e33341` (F2 entregue). Segue a ordem aprovada F1 → F2 → F5.

**Autorização:**
- Nenhuma conta real, proxy, tráfego ou Android. A §6.3 já dispensa aval novo: "sem conta real e sem tráfego pago".
- **Direção aprovada:** decisões A–F e solução C (16/09/2026). **SQL não certificado**: o modelo v13.7 é ponto de partida, não prova.

## Passo 1 — Análise (medida)

Script: [`f5_analise.py`](f5_analise.py). Log: [`01-analise.log.txt`](01-analise.log.txt).

| # | Achado | Medição |
|---|---|---|
| A1 | A união entre execuções vira "lista" | execução 1 Ana/Bruno, execução 2 Ana/Carla → `get_all` = ana, bruno, carla. Bruno continua sem nenhum sinal de que não foi reobservado |
| A2 | Sem identidade de tentativa | a mesma página gravada duas vezes e uma releitura legítima são indistinguíveis (1 novo, depois 0) |
| A3 | Sem qualidade de página | uma página vazia não deixa registro, motivo nem política |
| A4 | Sem posse | duas contas gravam o mesmo alvo ao mesmo tempo, sem erro |
| A5 | Sem proveniência | só existe a tabela `profiles_seen` (`profile_id, list_type, username, first/last_seen, source_account`); não há run, page, attempt, quality nem `policy_version` |
| A6 | Cobertura calculada sobre a união | `extractor._extract_persistent` informa `target_coverage_pct = store_total / target` (leitura de código): o histórico apresentado como cobertura |

**Revisão do modelo v13.7 contra a §6.3** (leitura de `docs/design-validation/v13.7/models_v137.py`). Lacunas que a implementação precisa fechar, sem herdar o resultado do modelo:

| Lacuna | Onde a §6.3 exige |
|---|---|
| `now` vem sempre do chamador; não é lido **depois** do `BEGIN IMMEDIATE` | I7, §6.3.2 |
| sem spool durável da tentativa | §6.3.3 |
| heartbeat é método, não tarefa com conexão própria e regra `lease_ttl/2` | I6, §6.3.12 |
| sem validação de `lease_ttl`, `busy_timeout ≤ ttl/6` e timeout de requisição `< ttl/2` | §6.3.12 |
| sem migração de `profiles_seen` como `suspect`/`legacy_import` | §6.3.11 |
| sem worker que conecte leitura remota, spool, verificação antes do envio e commit | §6.3.7, §6.3.8 |
| cenários [E1] (8 processos, 100 encerramentos, espera real) não reexecutados | F5 passo 4 |

## Passo 2 — Pesquisa

- **Fontes registradas no roadmap:**
  - SQLite: transações e `BEGIN IMMEDIATE` [S9], `UNIQUE` com `NULL` [S38], backup API [S39], WAL [S43];
  - `sqlite3` do Python [S26];
  - idempotência por chave, no modelo da Stripe [S40];
  - posse e reprocessamento: Kafka, Kleppmann, SQS, Temporal [S31–S34].
- **Relato [S37]:** cópia datada **não arquivada nesta fase**, porque o arquivamento exigiria baixar e publicar conteúdo de terceiros no repositório. O relato continua citado só como relato, e nenhuma regra depende exclusivamente dele (§6.3.10). *Pendência registrada.*
- **Escrita atômica do spool:** temporário no mesmo diretório + `fsync` + `os.replace`, mesma técnica da F1.

## Passo 3 — Pré-análise (antes dos testes)

### Módulo e nomes públicos

Novo pacote `instat/jobstore/`. **Nenhum retorno público existente muda.**

| Arquivo | Conteúdo |
|---|---|
| `policy.py` | `SANITY_V1` (tabela abaixo) e `HISTORY_LABEL` |
| `canonical.py` | `normalize_username`, `canonical_page` (`page-v1`), `content_hash` |
| `schema.py` | DDL da §6.3.11; `PRAGMA user_version = 1`; WAL; `foreign_keys` |
| `store.py` | `JobStore` |
| `spool.py` | `AttemptSpool` (arquivo por tentativa, escrita atômica, `pending()`, `ack()`) |
| `heartbeat.py` | `Heartbeat` (thread + conexão própria, a cada `ttl/3`; classifica `renewed`/`account_restricted`/`lease_lost`; posse não verificável após `ttl/2`) |
| `worker.py` | `CursorWorker`: `can_send` → gera `attempt_id` → fetch → spool → commit → ack; retoma o spool pendente após crash |
| `migrate.py` | `import_legacy_profiles_seen` (backup verificado antes; `legacy_import` como `suspect`) |

**Métodos públicos novos do `JobStore`:**
- `add_account`, `set_auth`, `release_account`, `acquire`, `heartbeat`;
- `create_job`, `claim`, `request_cancel`, `requeue`, `can_send`, `stop_run`, `restart_segment`, `commit`;
- `run_result`, `job_view`;
- `backup_online`, `verify_backup`, `backup_maintenance`.

Os nomes e assinaturas seguem o modelo v13.7, para que os 43 cenários rodem **sem adaptador**. A exposição em `InstaExtractor` fica para a F7, junto com o engine que produz páginas; na F5 os retornos de `get_followers`/`get_following` ficam intactos.

### Horário (I7)

Todo método que grava aceita `now=None`. Com `None`, `now = clock()` é lido **depois** de obter o `BEGIN IMMEDIATE`. Um `now` explícito serve só para testes determinísticos.

### Validação (§6.3.12)

`JobStore(path, ttl, busy_timeout)` rejeita com `ValueError`:
- `ttl` não finito ou ≤ 0;
- `busy_timeout > ttl/6`.

`CursorWorker` rejeita timeout de requisição `≥ ttl/2`.

### `sanity-v1` — fixada aqui

Valores copiados da §6.3.10. São limites operacionais iniciais, **não calibrados**, porque ainda não há dados da F6b, que está bloqueada.

| Parâmetro | Valor | Unidade |
|---|---|---|
| `K` | 3 | telas |
| `S` | 3 | telas |
| `replay_ratio` | 0,95 | razão |
| `frontier_confirm_screens` | 2 | telas |
| `counter_tolerance` | max(2, ⌈1% do limite superior⌉) | membros |
| `counter_stale_s` | 1800 | s |
| `max_rereads_per_pos` | 2 (adicionais) | tentativas |
| `max_runs` | 3 | execuções |

Sem dados da F6b, **sem evidência o resultado é `end_unknown`/`partial`**.

### Critérios de aceite (da §8 F5, com o caminho de medição)

1. **43 cenários [E3]** portados (`tests/jobstore/e3_scenarios.py`, cópia dos cenários do `test_v137.py`) passam contra `instat.jobstore.JobStore`. `API_AUSENTE` conta como falha.
2. **Rejeições em 100% das tentativas**, cada uma com teste:
   - commit sem posse;
   - reivindicação sem concessão ou sem sessão validada;
   - renovação de conta revogada;
   - gravação por execução não corrente;
   - segunda leitura confiável da mesma posição;
   - `attempt_id` com conteúdo diferente ou de outra execução;
   - 4ª releitura e 4ª execução;
   - nenhuma `IntegrityError` convertida em sucesso.
3. **Injeção de falhas:**
   - **Carga:** 100 falhas contra um servidor HTTP local paginado com N = 1.000 membros conhecidos, 50 por página.
   - **Pontos de falha:** antes do envio; após a resposta e antes do spool; após o spool e antes do commit; dentro da transação do commit; após o commit e antes do ack.
   - **Resultado exigido:** zero páginas confirmadas perdidas, zero duplicatas lógicas, reprocessamento reportado, e a execução termina com os 1.000 membros exatos e `end_confirmed` pelo marcador de fim do servidor.
4. **Processo novo:** um processo morto por `kill` após o commit não perde a página. O processo novo retoma pelo spool sem duplicar.
5. **[E1] reexecutado:** 8 processos disputando a mesma conta, 100 encerramentos forçados, espera real pelo bloqueio. No máximo uma concessão vigente, nenhuma gravação sem posse, `next_pos` igual ao número de páginas `trusted`.
6. **I7:** com outra conexão segurando o bloqueio por 1,5 s e concessão de 1 s, a aquisição que esperou lê `now` depois e não trata a concessão expirada como válida.
7. **Heartbeat real (thread):**
   - classifica `account_restricted` quando a conta é revogada;
   - classifica `lease_lost` quando outra geração assume;
   - trata a posse como não verificável sem renovação por mais de `ttl/2`.
8. **Backup online com workers e heartbeat ativos:**
   - heartbeat sem falha durante a cópia;
   - cópia verificada pelo próprio ponto de consistência;
   - backup a partir de conexão com transação aberta → `BackupMisuse`;
   - prazo estourado → `.partial` removido;
   - manutenção drenada → `digest_equal = true`; não drenada → `rejected:nao_drenado`.
9. **Migração:** `profiles_seen` importado como `suspect`/`legacy_import`, sem contar como confiável em `run_result` nem em `job_view.selected_members`. Backup verificado antes.
10. **Resultado:** `run_result` usa só a própria execução; `job_view` com seleção, última tentativa e histórico rotulado (§6.3.9).
11. **Contrato legado:** `PersistentStore`, `get_followers`, `get_following` e `*_persistent` sem mudança de assinatura nem de retorno (testes existentes + teste de assinatura).
12. Toda página grava `policy_version = 'sanity-v1'`.
13. Suíte completa em 3.12 e 3.13; ruff e mypy limpos.

**Fora de escopo:**
- Integração do `JobStore` com engines reais e `InstaExtractor` (F7).
- Calibração da `sanity-v1` (F6b/F7).
- Portabilidade de cursor entre contas: nenhuma promessa.
- Ampliação manual do limite de execuções (não especificada).

### Prints (caminho resolvido aqui)

Sequências sintéticas e o servidor local, sem Android. Renderizados a partir de logs e JSON reais dos testes:

| Print | Conteúdo |
|---|---|
| `01-antes-crash.png` | páginas confirmadas e spool pendente no instante da falha |
| `02-retomada.png` | o processo novo retoma pelo spool; reprocessamento contado |
| `03-integridade.png` | `integrity_check`, trusted por `(run, pos)`, `next_pos` × páginas, duplicatas |
| `04-heartbeat-e-motivos.png` | classificações do heartbeat e motivos de parada |
| `05-progresso-sem-cursor.png` | sequências sintéticas scan: releitura, lacuna, tela repetida, fim |
| `06-selecao-e-historico.png` | `job_view` N01/N02: seleção, última tentativa e histórico rotulado |
| `07-backup-online.png` | backup em passo único com heartbeat e escritores; verificação; manutenção |
