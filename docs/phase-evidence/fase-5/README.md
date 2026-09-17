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

## Passo 4 — TDD

Os testes foram escritos a partir do passo 3 e confirmados em vermelho antes da implementação, no commit `6a6ad53`. Log: [`04-tdd-vermelho.log.txt`](04-tdd-vermelho.log.txt).

**Leitura crítica do vermelho.** Ele prova apenas a **ausência do módulo** `instat.jobstore`, não uma falha por cenário. É a mesma fraqueza que o roadmap aponta na v13.6. Por isso, depois da implementação, a sensibilidade dos testes foi provada por **mutações**: [`f5_mutacoes.py`](f5_mutacoes.py), logs [`05-mutacoes-rodada1.log.txt`](05-mutacoes-rodada1.log.txt) e [`05-mutacoes-rodada2.log.txt`](05-mutacoes-rodada2.log.txt).

| Mutação | Detectada por |
|---|---|
| M1 commit sem checar validade da concessão | T06 e rejeições |
| M2 horário lido antes do `BEGIN IMMEDIATE` | I7 |
| M3 sem verificação de `attempt_id` repetido | 100 injeções e processo morto |
| M4 ack do spool antes do commit | processo morto |
| M5 limite de releituras desligado | L01 e rejeições |
| M6 liberação sem sessão validada | L04 e rejeições |
| M7 heartbeat renova conta revogada | T05, heartbeat e rejeições |
| M8 backup permitido com transação aberta | **travou** (teste não termina em 180 s), o mesmo travamento de [E2] |

- **Rodada 1:** a M6 original não foi aplicada, porque o texto de busca não existia no código; foi substituída por outra mutação da mesma invariante (I4). A M8 foi parada manualmente enquanto travava.
- **Rodada 2:** M6 e M8 com limite de 180 s. Resultado final: **8/8 detectadas**.

**Arquivos:**
- `tests/jobstore/e3_scenarios.py` + `test_e3.py`: os 43 cenários [E3] portados. Mudanças só de harness:
  - raiz do repositório no `sys.path`;
  - C01 com `busy_timeout = 5.0`, porque 10.0 violaria `≤ ttl/6`, que a implementação agora valida;
  - runner removido.
- `tests/jobstore/test_f5_impl.py`: política, canônico, validação, I7, rejeições (100 tentativas), `policy_version`, spool, heartbeat em thread, backup com trabalho ativo, migração, contrato de `job_view`, assinaturas legadas e abertura resiliente.
- `tests/jobstore/test_f5_faults.py`: 100 injeções com servidor HTTP local e N = 1.000, processo morto por `os._exit` e [E1].

## Passo 5 — Execução

Pacote `instat/jobstore/`:

| Módulo | Conteúdo |
|---|---|
| `policy` | `SANITY_V1` |
| `canonical` | `page-v1` |
| `schema` | DDL da §6.3.11, `user_version = 1` |
| `store` | `JobStore` |
| `spool` | `AttemptSpool` |
| `heartbeat` | `Heartbeat` |
| `worker` | `CursorWorker` |
| `migrate` | `import_legacy_profiles_seen` |

Nenhum retorno público existente mudou.

**Revisão do modelo v13.7 aplicada na implementação:**
- `now=None` → horário lido depois do `BEGIN IMMEDIATE`;
- `_tx` com ROLLBACK em qualquer exceção;
- validação de `ttl`/`busy_timeout` e do timeout de requisição do worker;
- spool com intenção antes do envio e resposta antes do commit;
- heartbeat em thread com conexão própria e posse não verificável após `ttl/2`;
- migração legada `suspect`, que não consome `max_runs` nem ocupa `current_run`, com backup verificado antes.

**Correções e desvios durante a execução:**
1. **Harness do teste I7.** A conexão que segurava o bloqueio era criada numa thread e usada em outra (`ProgrammingError`). Passou a ser criada na própria thread; a expectativa não mudou.
2. **Prints 04 e 07, 1ª geração.**
   - O `can_send` foi lido depois de a concessão expirar e mostrava `lease_lost` em vez de `account_restricted`. O roteiro foi corrigido para ler logo após a revogação; as duas leituras ficam registradas.
   - O banco pequeno (cópia em 0 s) não demonstrava concorrência; foi adicionada uma carga de volume rotulada (51,7 MB).
3. **Observação não analisada.** A geração do print 07 que rodou **em paralelo** com as mutações (várias cópias do E1 com 8 processos) terminou com heartbeat `lease_lost` e 182 de 198 escritas não `trusted`. Isolada, a mesma seção deu 22 renovações e 0 falhas; na geração final, 34 renovações, 0 falhas e 0 escritas rejeitadas. **Hipótese não confirmada:** contenção de CPU atrasou o heartbeat além do `ttl` de 3 s; nesse caso, recusar escrita sem posse é o comportamento exigido. A causa exata das 182 recusas **não foi verificada**.
4. **`disk I/O error` transitório após matar processos (Windows).**
   - **Onde apareceu:** o E1 falhou em 3.13 ao abrir o banco logo após os 100 encerramentos.
   - **Diagnóstico** (4 repetições): em 3 de 4 casos, a 1ª abertura deu `disk I/O error` e abriu normalmente 0,21 s depois, com `integrity_check = ok`. É transitório, não corrupção.
   - **Correção 1:** abertura com repetição limitada, só para `disk I/O error` (6 tentativas, ~3,2 s), com testes vermelhos antes ([`06-desvio-disk-io-vermelho.log.txt`](06-desvio-disk-io-vermelho.log.txt)).
   - **Recorrência** ([`06-e1-rep5-313-falha.log.txt`](06-e1-rep5-313-falha.log.txt)): o erro voltou no comando seguinte à abertura, porque a sonda `PRAGMA foreign_keys` não lê o arquivo.
   - **Correção 2:** a sonda passou a ler `sqlite_master`, com teste vermelho antes ([`06-desvio-sonda-vermelho.log.txt`](06-desvio-sonda-vermelho.log.txt)).
   - **Resultado:** E1 com 12/12 em 3.13 e 6/6 em 3.12 ([`06-e1-repeticoes-313.log.txt`](06-e1-repeticoes-313.log.txt)).
   - **Risco residual:** o erro transitório pode aparecer num comando posterior de uma conexão já aberta; o worker deve tratá-lo como `technical_error`.
   - **Ajuste no teste E1:** passou a abrir o banco por `JobStore` (o mesmo caminho de um worker que reinicia) e ganhou a asserção de `integrity_check`.
5. **Falha não identificada.** Uma execução de `tests/jobstore` levou 45 min e teve 1 falha, mas só o resumo final foi capturado. A reexecução isolada, com durações, deu 70/70 em 33 s. A causa **não foi identificada**. A hipótese mais provável é o mesmo `disk I/O error` do E1, mas isso **não foi verificado**.
6. **Formatação.** `e3_scenarios.py` recebeu `# ruff: noqa: E702` para manter os cenários literais. `SANITY_V1` foi tipado como `Dict[str, Any]` (mypy).

## Passo 6 — Testes

| Verificação | Resultado | Log |
|---|---|---|
| Suíte 3.12 (`not e2e/mobile/real`) | 831 passed, 0 failed | [`06-suite-312.log.txt`](06-suite-312.log.txt) |
| Suíte 3.13 | 831 passed, 0 failed | [`06-suite-313.log.txt`](06-suite-313.log.txt) |
| E1 repetido após a correção | 3.13: 12/12; 3.12: 6/6 | [`06-e1-repeticoes-313.log.txt`](06-e1-repeticoes-313.log.txt) |
| Mutações | 8/8 detectadas | `05-mutacoes-*.log.txt` |
| ruff / mypy | limpos | [`06-ruff.log.txt`](06-ruff.log.txt), [`06-mypy.log.txt`](06-mypy.log.txt) |

**Aceite:**

| # | Critério | Status | Evidência |
|---|---|---|---|
| 1 | 43 cenários [E3] contra a implementação | ✅ | `test_e3.py` (43 + contagem) |
| 2 | Rejeições em 100% (10 tipos × 10); unicidade nunca vira sucesso | ✅ | `test_rejections_hold_in_100_percent_of_attempts`, `test_unique_violation_never_becomes_success` |
| 3 | 100 falhas, N = 1.000: zero páginas confirmadas perdidas, zero duplicatas, reprocessamento reportado | ✅ | `faults.json`: 100 quedas, 40 reprocessamentos, 20 commits repetidos → duplicata, 1.000/1.000, `complete` (print 03) |
| 4 | Processo morto após o commit retoma sem perda nem duplicata | ✅ | `test_killed_process_…` (`os._exit(9)` real) |
| 5 | [E1] 8 processos, 100 encerramentos, espera real | ✅ (após correção) | invariantes de posse, `next_pos` e `integrity_check`; intermitência corrigida (item 4 do passo 5) |
| 6 | I7 com espera real pelo bloqueio | ✅ | `test_now_is_read_after_waiting_for_the_lock`; mutação M2 detectada |
| 7 | Heartbeat real: restrição, perda de posse, `ttl/2` | ✅ | 3 testes + print 04 |
| 8 | Backup online com workers ativos; `BackupMisuse`; manutenção drenada | ✅ | teste + print 07 (51,7 MB, 34 renovações, 0 falhas, 0 escritas rejeitadas, `digest_equal = true`, `nao_drenado`) |
| 9 | Migração legada `suspect`, backup verificado antes | ✅ | `test_legacy_import_is_suspect_and_never_trusted` |
| 10 | `run_result` só da própria execução; `job_view` conforme §6.3.9 | ✅ | E3 N01/N02/N03/N09 + print 06 |
| 11 | Contrato legado intacto | ✅ | teste de assinaturas + suíte existente |
| 12 | Toda página com `policy_version` | ✅ | `test_every_page_has_policy_version` |
| 13 | 3.12, 3.13, ruff e mypy | ✅ | tabela acima |

**Observações e limites:**
- No `run_result` de uma execução encerrada por `technical_error` antes do fim, `reasons` mostra `in_progress` (o estado de progresso), e o motivo fica em `stop_reason`. É o comportamento do modelo, mantido porque os cenários [E3] não o cobrem. Registrado para revisão.
- **Não testado:** Instagram, conta real, proxy, Android e integração do `JobStore` com engines reais e `InstaExtractor` (F7). A `sanity-v1` segue **não calibrada**. O servidor de teste é local e sintético. O E2E com Firefox não foi reexecutado.
- O relato [S37] **não foi arquivado** (passo 2).

## Passo 7 — Prints (lidos)

| Print | Leitura |
|---|---|
| [`01-antes-crash.png`](01-antes-crash.png) | 4 páginas `trusted` confirmadas (`next_pos` 4); spool com a tentativa da posição 3 e sua resposta; queda após o commit e antes do ack |
| [`02-retomada.png`](02-retomada.png) | 6 páginas (`next_pos` 6), spool vazio; commit repetido do spool 1 → duplicata 1; 2 páginas novas; `end_confirmed`; `complete`; 300 membros iguais ao conjunto |
| [`03-integridade.png`](03-integridade.png) | `integrity ok`; 0 duplicatas; `next_pos` 20 × 20; 1.000/1.000; `complete`; 100 quedas; 40 reprocessamentos; 20 → 20 duplicatas; nota sobre commits sem queda posterior = 0 |
| [`04-heartbeat-e-motivos.png`](04-heartbeat-e-motivos.png) | conta_a: 3 renovações → `account_restricted`; conta_b: 2 → `lease_lost`; `can_send` ok → `account_restricted` → `lease_lost`; reivindicação e liberação sem sessão rejeitadas |
| [`05-progresso-sem-cursor.png`](05-progresso-sem-cursor.png) | sequências **sintéticas**: `no_end_evidence` → parcial; contador exato → `k_rounds_and_exact_counter` → `complete`; tela repetida → `screen_stuck`; lacuna + marcador → parcial `continuity_gap` |
| [`06-selecao-e-historico.png`](06-selecao-e-historico.png) | seleção run 2 `complete` (ana, carla); última tentativa run 3 parcial `technical_error`, datas separadas; histórico ana, bruno, carla com rótulo; sem campo de remoção |
| [`07-backup-online.png`](07-backup-online.png) | 51,7 MB; passo único 0,23 s; 98 escritas durante cópia e verificação; 0 de 159 não `trusted`; heartbeat 34/0; verificação pela cópia ok (38 páginas); origem terminou com 159; `nao_drenado`; drenada `digest_equal = True`; `BackupMisuse` |
