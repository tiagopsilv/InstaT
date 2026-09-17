# Fase 2 — Governador de erros e orçamento, antes da coleta

Branch: `f2/governador-erros-orcamento`, criada a partir de `cbee709` (F1 entregue), seguindo a ordem aprovada (F1 → F2).
Autorização: nenhuma conta real, nenhum proxy e nenhum tráfego. Tudo roda com fakes e servidor local. Nenhum aval novo é necessário.

## Passo 1 — Análise (medida)

Script: [`f2_analise.py`](f2_analise.py). Log: [`01-analise.log.txt`](01-analise.log.txt). O mapa do código, com linhas, foi feito por leitura (resumo abaixo).

| # | Achado | Medição / local |
|---|---|---|
| M1 | Challenge ou restrição numa conta faz o `EngineManager` trocar de conta | pool de 3 contas: login em `acc0`, `acc1` e `acc2` (**2 trocas**), com 3 esperas de backoff (`engine_manager.py:266-273`, `:362-371`) |
| M2 | 429 multiplica tentativas por outras contas | pool de 3 contas: **2 contas extras** tentadas após o 429 (`:353-361`) |
| M3 | Challenge sem pool continua em outro engine com a mesma conta | o 2º engine é tentado após `/challenge/` no 1º (`:372-375`) |
| M4 | O httpx não distingue proxy, serviço e restrição | 407 (5 causas), 403/502/503 do proxy, 500 e timeout → todos `BlockedError`. 429 → `RateLimitError` **sem** `Retry-After` |
| M5 | `SmartBackoff` sem teto de tentativas e sem `Retry-After` | 50 `wait()` aceitos; espera = `time.sleep` real |
| M6 | `until_complete` insiste em challenge | 4 tentativas e 3 esperas, **270 s** de sleep real, com challenge em todas |
| L1 | A rotação de contas de fallback é disparada por cobertura, não pela causa | `extractor.py:790-796`: um challenge na conta principal leva à próxima conta (leitura de código, não medido) |
| L2 | Laço sem teto | `utils.py:171-217` `wait_for_new_profiles`: `StaleElementReferenceException` repete sem limite (leitura) |
| L3 | Não há orçamento de tentativas nem de bytes. `max_duration` reinicia a cada engine e não conta as esperas | `engine_manager.py`, `extractor.py:517-520` (leitura) |
| L4 | `BlockPredictor.should_cooldown` não tem chamador | telemetria passiva (leitura) |

## Passo 2 — Pesquisa

- **`Retry-After`** (RFC 9110 §10.2.3; RFC 6585 para 429) pode vir em segundos **ou** como HTTP-date, e o cliente precisa aceitar as duas formas. Fontes:
  - [MDN Retry-After](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)
  - [http.dev Retry-After](https://http.dev/retry-after)
- **Erros do DataImpulse** [S19]:
  - 407: `NO_USER`, `TRAFFIC_EXHAUSTED`, `THREADS_EXHAUSTED`, `PORT_NOT_ALLOWED`, `USER_BLOCKED`;
  - 403: `PORT_BLOCKED`, `SITE_PERMANENTLY_BLOCKED`;
  - 502: `NO_HOST_CONNECTION`;
  - 503: `NO_RAY`.

  Ou seja, **o proxy também devolve 403, 502 e 503**: o código de status sozinho não separa proxy de Instagram. A documentação **não diz em que campo** o motivo chega (corpo, frase de status ou cabeçalho). Isso é hipótese: o classificador procura o token no corpo, nos cabeçalhos e na frase de status, e um 407 sem token vira `proxy:unknown_407`. Fontes:
  - [DataImpulse — Errors](https://docs.dataimpulse.com/errors)
  - [DataImpulse — 407](https://dataimpulse.com/blog/http-error-407/)
- **Backoff:** exponencial limitado, com jitter e `Retry-After` quando houver (§5.6). Cotas de APIs oficiais não são transportadas.

## Passo 3 — Pré-análise (critérios definidos antes dos testes)

### Sinais e ações

O novo `instat/governor.py` implementa a tabela da §5.6.

| Sinal | Origem | Ação | Estado da chave `(conta, operação)` | Troca de conta | Outro engine, mesma conta |
|---|---|---|---|---|---|
| `transient` | timeout, conexão, 5xx sem token de proxy | retry no mesmo engine, até `max_transient_retries` (3); espera `min(cap, base·2^n)` ± jitter, ou `Retry-After` se ≤ cap | `active` | não | só depois de esgotar |
| `technical` | `BlockedError` genérico (parse, modal, cobertura) | fallback para o próximo engine | `active` | não | sim |
| `rate_limited` | 429 / `RateLimitError` | **para a operação**; pausa a chave até `agora + max(Retry-After, pausa mínima 900 s)` | `paused` | **não** | **não** |
| `proxy:<causa>` | 407 (5 causas + `unknown_407`), 403 `PORT_BLOCKED`/`SITE_PERMANENTLY_BLOCKED`, 502 `NO_HOST_CONNECTION`, 503 `NO_RAY` | para; motivo `proxy:<causa>`; **nunca** marca a conta | inalterado | não | não |
| `challenge` | `AccountBlockedError`; URL/corpo com challenge/checkpoint | para; **sem laço de recuperação** | `needs_attention` | **não** | **não** |
| `restricted` | `feedback_required`, 403 sem token de proxy | para; congela até revisão | `restricted` | **não** | **não** |
| `cancelled` | `should_stop()` | para e preserva parcial | inalterado | — | — |

A liberação de `paused`, `needs_attention` e `restricted` é **manual**: `release(chave, sessao_validada=True)`. Tempo sozinho só libera `paused`, e **não** libera `needs_attention` nem `restricted` (decisão 6 da v13.7).

### Orçamento por chave `(conta, operação)`

- `max_attempts`: 50 por chamada de extração.
- `max_bytes`: opcional; conta o `Content-Length` real do httpx. Selenium e Playwright não medem bytes, e isso fica registrado como limitação.
- `max_duration_s`: relógio monotônico, **incluindo esperas**.
- Estourar qualquer limite para com o motivo `budget:<limite>`.
- Toda tentativa passa por `before_attempt()`, que recusa uma chave pausada ou congelada e verifica o orçamento, e por `record()`, que classifica e decide.

### Relógio e cancelamento

- `Clock` é injetável (`monotonic()`, `sleep()`). Os testes usam `FakeClock`: zero esperas reais.
- A espera do governador é fatiada e checa `should_stop` a cada fatia.

### Integração

1. **`EngineManager`**:
   - classifica cada exceção de login e extração;
   - `challenge`, `restricted`, `rate_limited` e `proxy` **interrompem a cascata inteira** (nenhuma sessão ou engine seguinte);
   - `technical` e `transient` esgotado seguem para o próximo engine com a mesma conta;
   - devolve o parcial, ou `ExtractionStoppedError` (subclasse de `AllEnginesBlockedError`, compatível) quando nada foi coletado;
   - `last_stop` guarda o motivo terminal;
   - `metrics_sink['terminal_reason']`.
2. **`HttpxEngine`**:
   - proxy → `ProxyError(cause)`;
   - 5xx, timeout e conexão → `TransientError`;
   - 429 → `RateLimitError(retry_after=…)`;
   - `ProxyError` e `TransientError` herdam de `BlockedError` (compatibilidade);
   - bytes informados ao governador.
3. **`InstaExtractor`**:
   - `until_complete` para **sem esperar** diante de um motivo terminal que não seja `technical`/`transient`;
   - a rotação para contas de fallback **não acontece** quando a parada foi `challenge`, `restricted`, `rate_limited` ou `proxy`.
4. **`utils.wait_for_new_profiles`**: limite de repetições em `StaleElementReferenceException`.

Testes existentes que codificam a política antiga (troca de conta ou engine após restrição) serão **alterados com justificativa**, citando a §5.6. A lista vai no passo 5.

### Critérios de aceite

1. Para cada sinal da tabela, com `FakeClock`, as tentativas ficam dentro do teto configurado. `transient` ≤ 1 + 3 retries. Os demais: 1 tentativa.
2. **Zero** trocas de conta (sessão ou rotação de fallback) e zero engines extras após `challenge`, `restricted`, `rate_limited` ou `proxy`.
3. Challenge não entra em laço: 1 tentativa em `EngineManager`, `until_complete` e rotação somados.
4. `Retry-After` em segundos e em HTTP-date é respeitado. Acima do cap vira pausa, não espera.
5. As 5 causas de 407 e os 4 erros de proxy 403/502/503 viram `proxy:<causa>` e nunca `restricted`/`challenge`. Um 500 sem token vira `transient`.
6. O teto de bytes e o de duração (com esperas) param com `budget:*`.
7. O cancelamento durante a espera para em ≤ 1 fatia e preserva o parcial.
8. Zero esperas reais nos testes unitários do governador.
9. Suíte completa em 3.12 e 3.13, ruff e mypy limpos.

### Prints (caminho resolvido aqui)

Um servidor HTTP local roteirizado responde ao `HttpxEngine` real. O relógio é controlado e a timeline é renderizada em PNG.

- `01-timeline.png`: sequência 503 + `Retry-After` → 200 → 429 → parada, com horários do relógio falso, sinal, ação e estado.
- `02-orcamento.png`: tentativas e bytes por chave × teto, e parada por `budget:bytes`.
- `03-alerta.png`: challenge e `proxy:TRAFFIC_EXHAUSTED`, com o motivo terminal e "nenhuma troca de conta".

Cada print será lido depois de gerado.

**Fora de escopo:**
- Chave por IP observado (F6/F10).
- Página vazia como `suspect` e cursor ausente (F5, I10).
- Tráfego real, conta real e proxy real.

## Passo 4 — TDD

Os testes foram escritos a partir dos critérios do passo 3 e confirmados em vermelho antes da implementação, no commit `2e9f91c`.

- Log: [`04-tdd-vermelho.log.txt`](04-tdd-vermelho.log.txt). A coleta falha porque `ChallengeError`, `governor` e os demais símbolos ainda não existiam.
- `tests/test_f2_governor.py` tem hoje 80 casos coletados:
  - classificação HTTP e de exceções;
  - governador com `FakeClock`;
  - `EngineManager`, `InstaExtractor` e `HttpxEngine`;
  - laço de stale.
- Uma fixture autouse transforma qualquer `time.sleep` real em falha.
- **3 testes adicionados depois do vermelho** (marcados no arquivo): `check_budget` no meio da tentativa e parada do httpx entre páginas, que cobrem o ajuste 1 do passo 5; e pausa com `Retry-After` acima do cap, que cobre o desvio do critério 4, com vermelho próprio registrado.

## Passo 5 — Execução

- **`instat/governor.py`** (novo):
  - `classify_http` e `classify_exception`, com `Retry-After` em segundos ou HTTP-date;
  - `Policy`, `Clock`/`FakeClock` e `Governor` (`begin`, `before_attempt`, `on_error`, `wait` fatiado, `call`, `check_budget`, `release`);
  - timeline de decisões.
- **`instat/exceptions.py`:**
  - `ChallengeError`, `RestrictedError`, `ProxyError(cause)` e `TransientError(status, retry_after)`, todas subclasses de `BlockedError`;
  - `RateLimitError(retry_after)`;
  - `ExtractionStoppedError`, subclasse de `AllEnginesBlockedError`.
- **`EngineManager`:**
  - login e extração passam por `governor.call`;
  - uma parada terminal interrompe a cascata inteira, com `last_stop` e `metrics_sink['terminal_reason']`;
  - o parcial é preservado;
  - `SessionPool.mark_blocked` continua sendo aplicado à conta afetada;
  - as esperas do `SmartBackoff` saíram do caminho de erro, já que o governador decide a espera.
- **`HttpxEngine`:**
  - `_raise_for_status` e `_request_error` classificam as respostas;
  - `bytes_sink` informa `Content-Length`;
  - `budget_check` é chamado antes de cada página.
- **`InstaExtractor`:** `until_complete` e a rotação de fallback respeitam `last_stop` terminal (sem retry, sem espera, sem outra conta).
- **`utils.wait_for_new_profiles`:** limite `MAX_STALE_RETRIES = 5`.

Ajustes e correções durante a execução:

1. **Orçamento de bytes no httpx.** Uma única tentativa do httpx pagina a lista inteira, então checar só em `before_attempt` não parava a coleta. Na primeira versão, a checagem ficava dentro da contabilização de bytes e **descartava a página já baixada**: 40 perfis mantidos com 60 recebidos, visto no cenário do print 02. A checagem passou para o início de cada página (`budget_check`): 60 perfis mantidos e a 4ª página não é pedida.
2. **Testes existentes com a política antiga** foram alterados com justificativa no próprio teste (§5.6):
   - `test_fallback_integration.py::test_session1_blocked_session2_succeeds`, renomeado para `…_rate_limited_does_not_switch_to_session2`: antes afirmava que a conta 2 assumia após um 429;
   - `test_skip_persistent_engine.py::test_rate_limit_sink_captures_engine_names`: antes afirmava que o engine seguinte assumia após um 429.
3. **Prints corrigidos após a leitura:**
   - texto cortado no 02;
   - "None" como teto;
   - tentativa n=1 (login) sem explicação no 01.

## Passo 6 — Testes

| Verificação | Resultado | Log |
|---|---|---|
| Suíte 3.12 com extras (`not e2e/mobile/real`) | 758 passed, 0 failed | [`06-suite-312.log.txt`](06-suite-312.log.txt) |
| Suíte 3.13 | 758 passed, 0 failed | [`06-suite-313.log.txt`](06-suite-313.log.txt) |
| ruff (`instat`, `tests` e scripts da fase) | limpo | [`06-ruff.log.txt`](06-ruff.log.txt) |
| mypy | limpo | [`06-mypy.log.txt`](06-mypy.log.txt) |
| Cenários com HttpxEngine real × servidor local | 4 de 4 conforme critério | [`07-cenarios.log.txt`](07-cenarios.log.txt), [`cenarios.json`](cenarios.json) |

Aceite:

| Critério | Status | Evidência |
|---|---|---|
| 1. Tentativas dentro do teto por sinal (relógio falso) | ✅ | transitório = 1 + 3; terminais = 1 (`test_terminal_signals_stop_after_one_attempt`, `test_transient_retries_are_finite…`) |
| 2. Zero trocas de conta ou engine após challenge, restrição, 429 ou proxy | ✅ | `test_engine_manager_no_account_switch_nor_extra_engine` (4 sinais); `test_rotation_never_switches_account…` (4 sinais); print 03 com logins `['conta_a_fake']` |
| 3. Challenge sem laço | ✅ | `until_complete`: 1 tentativa e 0 esperas, contra 4 tentativas e 270 s na medição do passo 1 |
| 4. `Retry-After` em segundos e HTTP-date; acima do cap vira pausa | ✅ | Testes de parse e de cap; print 01 (espera de 2,0 s pelo 503). **Desvio encontrado no passo 6 e corrigido:** acima do cap o governador parava sem marcar `paused`. Teste vermelho em [`05-desvio-criterio4-vermelho.log.txt`](05-desvio-criterio4-vermelho.log.txt); agora a chave fica `paused` pelo `Retry-After` (`test_retry_after_above_cap_pauses_key_until_retry_after`). |
| 5. 407 × 5, proxy 403/502/503 → `proxy:*`; 500 → `transient` | ✅ | 27 casos parametrizados (token no corpo, no cabeçalho e na frase de status) |
| 6. Tetos de bytes e duração | ✅ | `budget:bytes` (print 02) e `budget:duration` contando as esperas |
| 7. Cancelamento durante espera | ✅ | para em ≤ 1 fatia e preserva o parcial |
| 8. Zero esperas reais nos testes do governador | ✅ | a fixture autouse falha com qualquer `time.sleep` |
| 9. 3.12, 3.13, ruff e mypy | ✅ | tabela acima |

**Não testado nesta fase:**
- Instagram real e proxy real. O formato real do erro do DataImpulse continua hipótese; um 407 sem token vira `proxy:unknown_407`.
- Playwright e Selenium produzindo `TransientError`: eles ainda levantam `BlockedError` genérico, que é classificado como `technical`, ou challenge pela URL.
- Orçamento de bytes em Selenium e Playwright: não medem bytes.
- A suíte E2E com Firefox **não foi reexecutada** nesta fase.
- O CI desta branch.

## Passo 7 — Prints (lidos)

| Print | O que mostra | Leitura |
|---|---|---|
| [`01-timeline.png`](01-timeline.png) | eventos por tempo do relógio falso | login t=0 → página 503 (`transient`, retry, delay 2,0) → t=2,0 página 200 → 429 (`rate_limited`, `stop`) → estado `paused`. Requisições [503, 200, 429]; 2 perfis parciais |
| [`02-orcamento.png`](02-orcamento.png) | uso × teto por chave | bytes 1596 > 1500 → `budget:bytes`; 3 páginas pedidas, 4ª não; 60 perfis mantidos; tentativas 2 (login + extração) |
| [`03-alerta.png`](03-alerta.png) | challenge e proxy com pool de 2 contas e 2 engines | challenge: requisições [200, 400], login só `conta_a_fake`, `needs_attention`, 1 perfil parcial. Proxy: [407], `proxy:TRAFFIC_EXHAUSTED`, chave continua `active` (não é marca da conta), `ExtractionStoppedError` com 0 perfil |
