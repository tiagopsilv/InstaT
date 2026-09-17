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
