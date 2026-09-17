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
