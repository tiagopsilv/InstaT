# Fase 4 — Scheduler com exclusividade (paralelismo web)

Branch: `f4/scheduler-exclusividade`, criada a partir de `da433e4` (F3 entregue).

**Ordem:** a F6 continua bloqueada pela F6a (APK/tradução ARM/conta Google). A ordem aprovada permite a F4 de forma independente após a F5.

**Autorização:** nenhuma conta real, browser ou tráfego. Engines fake, processos locais e SQLite. Nenhum aval novo é necessário.

## Passo 1 — Análise (medida)

Script: [`f4_analise.py`](f4_analise.py). Log: [`01-analise.log.txt`](01-analise.log.txt).

| # | Achado | Medição |
|---|---|---|
| A1 | Mais workers que contas → a mesma conta em sessões simultâneas | `parallel_extract(workers=4, 2 contas)`: pico de **2 sessões simultâneas por conta** (`parallel.py:107-111` só avisa) |
| A2 | Sem `accounts`, todos os workers usam a mesma credencial | `workers=3`: pico de **3 sessões** da conta única |
| A3 | Queda de worker é silenciosa | worker que cai → resultado parcial sem indicação nem motivo (`parallel.py:137-139`) |
| A4 | Exclusividade só em memória | `SessionPool` em 2 processos: **a mesma conta entregue aos dois** |
| A5 | O tempo libera conta com challenge | `mark_blocked(META_INTERSTITIAL_COOLDOWN)`: 6 h depois, a conta volta a ser entregue sem revisão manual (contraria I4 e a decisão 6) |
| A6 | `WorkerPool.add_worker` não verifica conta repetida nem limite | leitura de código confirmada pelo script |

## Passo 2 — Pesquisa

- **twscrape** [S20], só como referência arquitetural:
  - seleção de conta e bloqueio por fila atômicos em SQLite;
  - processos que usam o mesmo banco compartilham a rotação;
  - bloqueio por operação até o reset.

  **Divergência deliberada:** o twscrape tenta **outra conta** quando uma é limitada. Aqui isso é proibido para restrição, challenge e 429 (F2, §5.6). Cotas de outra plataforma **não** são copiadas.

  Fontes:
  - [twscrape (GitHub)](https://github.com/vladkens/twscrape)
  - [twscrape (PyPI)](https://pypi.org/project/twscrape/)
- **Playwright** [S10]: "Playwright's API is not thread-safe… create a playwright instance per thread". Fontes:
  - [Playwright Python — Library](https://playwright.dev/python/docs/library)
  - [issue #470](https://github.com/microsoft/playwright-python/issues/470)
- **Selenium:** o `WebDriver` também não é compartilhável entre threads (o `WorkerPool` já documenta "one engine per thread").
- **Reuso:** as transações de concessão da F5 (`JobStore.acquire`/`heartbeat`, fencing por geração, `endpoint_cooldowns`, liberação manual) **não são reimplementadas**.

## Passo 3 — Pré-análise (antes dos testes)

### Desenho

1. **`instat/scheduler.py` — `AccountScheduler(store: JobStore, owner, endpoint)`.**
   - `eligible_accounts(now=None)`: contas com `auth_state = ok`, sem concessão vigente e sem cooldown para o endpoint. É o mesmo predicado de `acquire`, por consulta de leitura.
   - `plan_workers(requested)`: `min(requested, len(elegíveis))`, sempre ≥ 0.
   - `lease(account)`: context manager.
     - `acquire` → `Heartbeat` (F5) → ao sair, `release`.
     - Uma exceção técnica também libera.
     - Challenge/restrição → `set_auth` (`needs_attention`/`restricted`) e libera; **o tempo não libera a conta**.
   - `run_targets(targets, worker_fn, accounts, requested_workers)`: unidade de paralelismo = **alvos independentes**.
     - Cada worker pega uma conta distinta, processa um alvo por vez e devolve um relatório por alvo (`ok` / motivo).
     - Workers ≤ contas elegíveis.
2. **Adições mínimas ao `JobStore`** (sem nova transação de posse):
   - `release_lease(tok, now)`: encerra a concessão só se conta, dono e geração conferem;
   - `lease_valid(tok, now)`;
   - `set_endpoint_cooldown(acct, endpoint, until, reason)`.
3. **Dono dos objetos de browser:** o worker cria a engine **na própria thread** (factory), usa e chama `quit()` na **mesma thread**. Nenhuma engine é compartilhada.
4. **`parallel_extract` (legado):**
   - com `accounts`: `workers` é limitado ao número de contas **distintas**, sem repetir credencial;
   - sem `accounts`: `workers` é limitado a 1;
   - o aviso de risco vira limitação efetiva, e o relatório de falhas por worker fica acessível (`last_report`).
   - **Mudança de comportamento** a registrar no CHANGELOG: menos paralelismo, nunca a mesma conta duas vezes.

### Critérios de aceite

1. **Disputa entre processos:** 1.000 iterações com 2, 3 e 4 processos separados sobre as mesmas contas (SQLite compartilhado).
   - Cada iteração: aquisição e trabalho curto, em que um worker "lento" ultrapassa o ttl, seguido de liberação.
   - Cada processo registra `[início, fim]` de posse (fim = liberação ou `lease_until`, o que vier primeiro).
   - **Zero** intervalos sobrepostos com duas concessões ativas para a mesma conta.
2. **Commit tardio rejeitado:** o worker lento que perdeu a concessão tenta gravar (`JobStore.commit` com o token antigo) → `rejected:posse` em 100% das vezes.
3. **Conta em `needs_attention` nunca concedida** durante a disputa, depois de marcada, mesmo após o ttl e o cooldown expirarem.
4. **Workers ≤ contas elegíveis:** `plan_workers` e `run_targets` com 5 pedidos e 2 elegíveis → 2 workers; com 0 elegíveis → 0 e relatório "sem conta elegível".
5. **Recuperação:**
   - processo morto (`kill`) com concessão → outra conta ou processo readquire após o ttl;
   - liberação tardia do processo antigo (token velho) não afeta a nova posse;
   - challenge continua respeitado depois da recuperação.
6. **Dono do browser:** a factory e o `quit()` da engine rodam na mesma thread; nenhuma engine é usada por duas threads (teste com engine fake que registra a thread).
7. **Legado:** `parallel_extract` com 4 workers e 2 contas → pico de 1 sessão por conta; sem `accounts` → 1 worker; falha de worker fica visível em `last_report`.
8. Suíte completa em 3.12 e 3.13; ruff e mypy limpos.

**Fora de escopo:**
- integração com Android/proxy (F6/F7);
- quotas por IP observado (F6/F10);
- `SessionPool` em memória (continua existindo para o caminho sequencial; o scheduler é o caminho entre processos);
- múltiplas contas reais.

### Prints (caminho resolvido aqui)

Renderizados a partir dos logs reais da disputa (processos locais, serviço fake):

| Print | Conteúdo |
|---|---|
| `01-pool.png` | timeline de posse por conta × processo (dois processos), sem sobreposição |
| `02-lease-expirado.png` | worker lento: concessão expira, outro processo assume, commit tardio rejeitado |
| `03-worker-obsoleto.png` | processo morto, readquisição após ttl, liberação tardia sem efeito, `needs_attention` nunca concedida |

## Passo 4 — TDD

- **Vermelho** (commit `7d22fdd`):
  - [`04-tdd-vermelho.log.txt`](04-tdd-vermelho.log.txt): `instat.scheduler` ausente.
  - [`04b-tdd-vermelho-disputa.log.txt`](04b-tdd-vermelho-disputa.log.txt): a disputa entre processos falha por `JobStore` sem `last_lease` (API ausente).
  - **Leitura crítica:** as duas provam só **API ausente**, não comportamento. Por isso a sensibilidade foi provada por **mutações** depois da implementação.
- **Arquivos:**
  - `tests/test_f4_scheduler.py`: `JobStore` (liberação por dono e geração, cooldown por endpoint, intervalo), elegibilidade/plano, lease e sinais, `run_targets`, `parallel_extract` legado;
  - `tests/test_f4_dispute.py`: processos reais sobre SQLite compartilhado.
- **Mutações** ([`f4_mutacoes.py`](f4_mutacoes.py)):
  - **Rodada 1** ([`05-mutacoes.log.txt`](05-mutacoes.log.txt)): 7/8. A M7 original (só `auth_state='ok' OR 1=1` em `acquire`) sobreviveu. A cláusula `restricted_at`/`auth_validated_at` da mesma consulta continuava bloqueando, então era um **mutante equivalente**, sem efeito no comportamento.
  - **Rodada 2** ([`05-mutacoes-rodada2-M7.log.txt`](05-mutacoes-rodada2-M7.log.txt)): M7 retirando as duas guardas → detectada.
  - **Rodada final** sobre o harness definitivo ([`05-mutacoes-final.log.txt`](05-mutacoes-final.log.txt)): **8/8 detectadas**.

| Mutação | Detectada por |
|---|---|
| M1 `acquire` concede com concessão vigente | disputa entre processos (sobreposição) |
| M2 `release_lease` sem conferir dono/geração | liberação tardia |
| M3 challenge não marca a conta | lease + `run_targets` |
| M4 `plan_workers` sem limite | plano e `run_targets` |
| M5 `parallel_extract` sem limite | legado |
| M6 commit sem checar validade da concessão | disputa (commit tardio aceito) |
| M7 conta em `needs_attention` concedida | lease + disputa |
| M8 scheduler não libera ao sair | lease |

## Passo 5 — Execução

| Arquivo | Mudança |
|---|---|
| `instat/jobstore/store.py` | adições sem nova transação de posse: `last_lease`/`last_release` (horários das próprias transações), `release_lease` (só dono e geração vigentes), `lease_valid`, `set_endpoint_cooldown` (nunca encurta), `eligible_accounts` (mesmo predicado de `acquire`); `stop_run` registra em `last_release` quando encerra a concessão |
| `instat/scheduler.py` (novo) | `AccountScheduler`: `eligible_accounts`, `plan_workers`, `lease` (heartbeat da F5; challenge → `needs_attention`; restrição → `restricted`; 429 → cooldown do endpoint; libera sempre), `run_targets` (1 conta por worker, engine criada/usada/encerrada na mesma thread, conta parada não é trocada) |
| `instat/parallel.py` | workers limitados a contas **distintas** (sem `accounts` → 1); `parallel.last_report` com falhas por worker |

**Correções durante a execução** (todas em harness ou testes legados, nenhuma mudança de expectativa):
1. **Falsa sobreposição de 126/130** na 1ª execução da disputa. O child chamava `stop_run` (que **já encerra a concessão** na F5) antes de `release_lease`; o `release_lease` devolvia `False` e o harness usava `acquire + ttl` como fim. Foi confirmado no código, e `stop_run` passou a registrar o fim em `last_release`.
2. **Configuração sem worker lento efetivo:** a lentidão era contada por iteração, incluindo misses. Passou a ser contada por concessão obtida (a cada 15).
3. **Teste de processo morto fraco** (visto na leitura do print 03, "1ª concessão de p1 após o kill: nenhuma"):
   - o sobrevivente já tinha terminado antes do kill;
   - depois, uma corrida do observador (p0 liberava e p1 adquiria entre a leitura do banco e o kill) causou uma falha falsa.

   **Solução determinística:** p0 roda em modo *hold* (adquire, registra e segura até morrer), e o teste exige que p1 obtenha **a mesma conta** só depois do `lease_until` de p0. Três execuções seguidas: 3/3.
4. **Testes legados de `test_parallel.py`** (`test_union_of_workers`, `test_worker_failure_does_not_crash`) usavam 2 workers com a **mesma credencial default**, o comportamento que a F4 proíbe. Passaram a usar duas contas distintas, mantendo o propósito (união e resiliência), com justificativa no código. Em `test_stop_signalled_when_threshold_hit`, com credencial única, agora roda 1 worker; o teste continua passando, mas verifica só esse worker.
5. **Print 03:** a concessão *hold* de p0 não era desenhada; foi incluída.
6. **ruff:** ordem de imports (autofix).

## Passo 6 — Testes

| Verificação | Resultado | Log |
|---|---|---|
| Suíte 3.12 (`not e2e/mobile/real`) | 862 passed, 0 failed | [`06-suite-312.log.txt`](06-suite-312.log.txt) |
| Suíte 3.13 | 862 passed, 0 failed | [`06-suite-313.log.txt`](06-suite-313.log.txt) |
| Disputa entre processos, 3 execuções seguidas | 3/3 (4 testes cada) | passo 5, item 3 |
| Mutações | 8/8 | `05-mutacoes-final.log.txt` |
| ruff / mypy | limpos | [`06-ruff.log.txt`](06-ruff.log.txt), [`06-mypy.log.txt`](06-mypy.log.txt) |

**Aceite:**

| # | Critério | Status | Evidência |
|---|---|---|---|
| 1 | 1.000 iterações, 2 a 4 processos, zero concessões duplas | ✅ | 2×167 + 3×111 + 4×84 = 1.003 iterações; `overlaps = 0` (intervalos dos horários das transações); M1 detectada |
| 2 | Commit tardio rejeitado em 100% | ✅ | todos `rejected:posse` (print 02: 10 de 10 na execução do print); M6 detectada |
| 3 | `needs_attention` nunca concedida | ✅ | `blocked_grants = 0`, inclusive após ttl; M7 detectada |
| 4 | Workers ≤ contas elegíveis | ✅ | `plan_workers(5)` com 2 elegíveis = 2; 0 elegíveis → relatório `no_eligible_account`; M4 detectada |
| 5 | Recuperação após processo morto; liberação tardia sem efeito; restrição mantida | ✅ | modo *hold*; p1 pega a mesma conta só após o fim da concessão de p0; `release_lease(token antigo) = False`; `needs_attention` mantida (print 03) |
| 6 | Engine criada, usada e encerrada na mesma thread | ✅ | `test_run_targets_one_account_per_worker_and_engine_owned_by_thread` |
| 7 | `parallel_extract`: nunca a mesma conta 2×; falha visível | ✅ | pico 1 por conta; `last_report`; M5 detectada |
| 8 | 3.12, 3.13, ruff e mypy | ✅ | tabela acima |

**Não testado:**
- browsers reais (Selenium/Playwright) sob o scheduler;
- contas e Instagram reais;
- várias máquinas acessando o mesmo SQLite (SQLite em rede não é suportado para isso; ficaria para a F10).

`InstaExtractor` ainda não usa o `AccountScheduler`; a integração com engines reais fica para a F7/F10.

## Passo 7 — Prints (lidos)

| Print | Leitura |
|---|---|
| [`01-pool.png`](01-pool.png) | 3 s de posse por conta com p0 (azul) e p1 (laranja) alternando sem sobreposição; workers lentos hachurados; `a_bloqueada` sem nenhuma barra; tabela: 0 sobreposições, 0 concessões bloqueadas, 0 erros |
| [`02-lease-expirado.png`](02-lease-expirado.png) | worker lento segura até o `lease_until` (0,5 s); o outro processo só assume depois; 8 linhas de commits tardios, todas `rejected:posse`; "commits tardios: N, aceitos: 0" |
| [`03-worker-obsoleto.png`](03-worker-obsoleto.png) | concessão de p0 (azul) do kill (vermelho) até o fim (tracejado); p1 só pega a mesma conta depois do tracejado (`≥ fim da de p0: True`); `release_lease` com token antigo = False; nova posse válida; `a_bloqueada` não concedida (`needs_attention`) |

## Aceite de CI

Run `35237933020` no PR #14 (somente CI, fechado sem merge), em Linux: lint-and-type ✅, test 3.12 ✅, test 3.13 ✅, build ✅, todos na primeira execução. A disputa entre processos também rodou no CI.
