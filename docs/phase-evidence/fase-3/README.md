# Fase 3 — Metadados independentes de Selenium

Branch: `f3/metadados-sem-selenium`, criada a partir de `293d582` (F5 entregue). Ordem aprovada: F5 → F3 → F6 → F7.
Autorização: nenhuma conta real, nenhum tráfego e nenhum browser. Só adapters fake e respostas simuladas. Nenhum aval novo é necessário.

## Passo 1 — Análise (medida)

Script: [`f3_analise.py`](f3_analise.py). Log: [`01-analise.log.txt`](01-analise.log.txt).

| # | Achado | Medição |
|---|---|---|
| M1 | `get_profile()` exige `_driver` | engine sem `_driver` → `RuntimeError: get_profile requires a Selenium-based engine` |
| M2 | Engine que tem os metadados, mas não tem driver, também falha | mesmo `RuntimeError`: não existe capacidade de leitura de perfil fora do Selenium |
| M3 | A parte B do teste de contrato trava o acoplamento | com `_FakeDriver` injetado → ok; removido o `_driver` da engine fake → `RuntimeError` |
| M4 | Subclasse legada de `BaseEngine` (só os abstratos) é instanciável hoje | ok; precisa continuar |
| M5 | Pontos que dependem de `_driver` fora das engines | `extractor.py:238-247` (`self.driver`, `self.insta_login`); `:397` (`get_profile`); `:1175` e `:1223` (handoff de cookies do Selenium para httpx em `get_followers_parallel`/`get_both`); `engine_manager.py:247-308` (`_try_cookie_handoff`) |

**Propriedades delegadas ao Selenium em `get_profile`** (`extractor.py:383-505`):
- `og:description` → contadores;
- `og:title` → `full_name`;
- `og:image` → `profile_pic_url`;
- bio (script de 3 estratégias);
- `is_verified` (svg);
- `is_private` (texto da página).

**Escopo:** M5 fica **fora** desta fase. O handoff de cookies Selenium → httpx é otimização de sessão, e o `extractor.driver` é atributo público legado. Os dois são mantidos como estão.

## Passo 2 — Pesquisa

- **Padrão do projeto** (`instat/engines/base.py:37`, `get_recent_posts`): método **não abstrato** com default `NotImplementedError`, e o `EngineManager` pula para a próxima engine. Assim uma capacidade nova não quebra subclasses legadas.
- **API privada web** (já usada pelo `HttpxEngine._resolve_user_id` e por `get_total_count`): `users/web_profile_info` devolve o objeto `user` com os campos do cabeçalho. Leitura de código; **formato real não reverificado nesta fase**, sem tráfego.
- **Adapters** [S7]: o `User` do instagrapi expõe `username`, `full_name`, `biography`, `follower_count`, `following_count`, `media_count`, `is_private`, `is_verified` e `profile_pic_url`. É o mesmo conjunto que `Profile` já tem, então o contrato de dados comum é viável para as F7/F8. Fontes:
  - [instagrapi — User](https://subzeroid.github.io/instagrapi/usage-guide/user.html)
  - [user.md](https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md)
- **uiautomator2** [S14]: leitura de perfil pela UI é da F7; aqui só se garante que o contrato não exige driver.

## Passo 3 — Pré-análise (antes dos testes)

### Desenho

1. **`BaseEngine.get_profile_info(profile_id) -> ProfileInfo`.**
   - **Não abstrato.** O default levanta `NotImplementedError`, então subclasses legadas continuam instanciáveis.
   - `ProfileInfo` (`instat/profile_info.py`) é uma dataclass com os campos de `Profile` sem `_extractor`: `username`, `url`, `full_name`, `bio`, `followers_count`, `following_count`, `posts_count`, `is_private`, `is_verified`, `profile_pic_url`.
2. **`BaseEngine.capabilities -> frozenset[str]`** (capacidades explícitas).
   - O default é derivado: `{"extract", "total_count"}`, mais `"profile_info"` / `"recent_posts"` quando a subclasse sobrescreve o método.
   - Uma engine pode declarar o conjunto explicitamente (registro opt-in). Os stubs mobile declaram `frozenset()`.
3. **Leitores:**
   - `SeleniumEngine.get_profile_info`: o código atual de `get_profile`, movido sem mudar o resultado.
   - `HttpxEngine.get_profile_info`: `web_profile_info`; um 404 levanta `ProfileNotFoundError`; os erros usam a classificação da F2.
   - Leitor DOM compartilhado em `instat/profile_readers.py` (`read_profile_from_driver(driver, profile_id)`), usado pelo Selenium **e** pelo caminho legado do item 4.
4. **`InstaExtractor.get_profile`** (delegação interna, na ordem):
   1. engines da cascata com `"profile_info"` em `capabilities`, começando pela primária, via `EngineManager.get_profile_info` (com governador: parada terminal interrompe; `NotImplementedError`/técnico passam à próxima);
   2. **compatibilidade:** uma engine primária que não é `BaseEngine` com capacidade, mas expõe `_driver` não nulo (engines e mocks antigos), usa o leitor DOM (default preservado);
   3. nenhuma → `RuntimeError` (**mesmo tipo de antes**), com mensagem explicativa: engines configuradas, capacidades de cada uma, engines pedidas mas puladas por extra não instalado (`pip install instat[httpx]` / `[playwright]`), e sugestão de incluir `selenium` ou `httpx`.
5. **Contrato público:** `get_profile(profile_id) -> Profile`, atributos e `Profile.get_followers/get_following` sem mudança.

### Critérios de aceite

1. **Parte B do contrato reescrita:** o snippet-contrato roda ponta a ponta com engine fake **sem atributo `_driver`** (asserção `not hasattr`), que implementa `get_profile_info`. O teste confere que a chamada atravessou `InstaExtractor.get_profile` → `EngineManager` → `engine.get_profile_info` (contador na engine), **sem mockar a API pública**.
2. **Subclasse legada** (só os abstratos) continua instanciável e não declara `profile_info`. Um `get_profile` só com ela dá `RuntimeError` com o nome da engine e a capacidade ausente na mensagem.
3. **Caminho legado** com `_driver` (`tests/test_profile.py` atual, com mock) continua passando sem alteração.
4. **Cascata:** primária sem capacidade + secundária capaz → usa a secundária. Challenge na capaz → para (sem próxima engine) e propaga a parada.
5. **Httpx:** o fixture JSON de `web_profile_info` vira `Profile` com todos os campos; 404 → `ProfileNotFoundError`.
6. **Selenium:** `SeleniumEngine.get_profile_info` com driver fake dá o mesmo resultado que o `get_profile` legado dava (mesmos metadados).
7. **Matriz de capacidades:**
   - selenium: `extract`, `total_count`, `profile_info`;
   - httpx: `extract`, `total_count`, `profile_info`, `recent_posts`;
   - playwright: `extract`, `total_count`;
   - android_ui / mobile_api: vazio.
8. **Mensagem explicativa** quando o extra não está instalado (`engines=["selenium", "httpx"]` com httpx indisponível e Selenium sem capacidade simulada) cita `pip install instat[httpx]`.
9. Suíte completa em 3.12 e 3.13; ruff e mypy limpos.

**Fora de escopo:** handoff de cookies do Selenium (M5), leitura de perfil pela UI Android (F7) e pela API móvel (F8), tráfego real.

### Prints (caminho resolvido aqui)

Relatório renderizado a partir de execuções reais do código com adapters fake, sem conta:

| Print | Conteúdo |
|---|---|
| `01-contrato-legado.png` | snippet-contrato com engine fake sem `_driver` e engine legada com `_driver`: mesmos campos |
| `02-perfil-sem-driver.png` | `get_profile` via httpx fake (resposta simulada) e cascata primária sem capacidade → secundária |
| `03-capacidades.png` | matriz de capacidades por engine e mensagens de erro explicativas |

## Passo 4 — TDD

- **Vermelho 1** ([`04-tdd-vermelho.log.txt`](04-tdd-vermelho.log.txt), commit `b5569c2`): só **ausência do módulo** `instat.profile_info`, a mesma fraqueza registrada na F5.
- **Vermelho 2, comportamental** ([`04b-tdd-vermelho-comportamental.log.txt`](04b-tdd-vermelho-comportamental.log.txt), commit `ad36b5c`): foi adicionado **só** o tipo `ProfileInfo`, sem comportamento. Resultado: 12 testes falhando por comportamento (capacidade inexistente, cascata, httpx, Selenium, matriz, mensagem e parte B do contrato).
- **Teste que passava vacuamente:** `test_explicit_capabilities_opt_in_overrides_derivation` passava no vermelho 2 porque `get_profile` já levantava `RuntimeError` por qualquer motivo. Foi reforçado, exigindo que `get_profile_info` **não** seja chamado e que a mensagem mostre a declaração explícita. Contra o código antigo, a versão reforçada falha ([`05-reforco-explicit-vermelho.log.txt`](05-reforco-explicit-vermelho.log.txt)).
- **Arquivos:** `tests/test_f3_profile_capability.py` e a parte B de `tests/test_public_api_contract.py`, reescrita com engine sem `_driver` e contador da delegação real. A parte A ficou intacta.

## Passo 5 — Execução

| Arquivo | Mudança |
|---|---|
| `instat/profile_info.py` | `ProfileInfo` (dataclass congelada) |
| `instat/engines/base.py` | `get_profile_info` (não abstrato, default `NotImplementedError`) e `capabilities` derivada, sobrescrevível (opt-in) |
| `instat/profile_readers.py` | `read_profile_from_driver`: o código de `get_profile`, movido sem alterar a lógica |
| `instat/engines/selenium_engine.py` | `get_profile_info` via leitor DOM; sem driver → `BlockedError` |
| `instat/engines/httpx_engine.py` | `get_profile_info` via `web_profile_info` (404 → `ProfileNotFoundError`; status classificados pela F2) |
| `instat/engines/engine_manager.py` | `get_profile_info`: cascata só entre engines com a capacidade, pelo governador; parada terminal → `ExtractionStoppedError` |
| `instat/extractor.py` | `get_profile` delega (capacidade → caminho legado `_driver` → `RuntimeError` explicativo); `_build_engines` registra extras pulados |
| `instat/mobile/engines.py` | stubs declaram `capabilities = frozenset()` |

**Correções durante a execução:**
1. **`BlockedError` não importado** em `selenium_engine.py` (ruff F821 / mypy). Sem driver, o resultado seria `NameError` em vez de erro técnico. Nenhum teste cobria esse caminho. Foi escrito um teste vermelho antes ([`06-desvio-blockederror-vermelho.log.txt`](06-desvio-blockederror-vermelho.log.txt)) e depois corrigido o import.
2. **mypy** em `extractor.py`: a expressão da capacidade foi reescrita, sem mudar o comportamento.
3. **Prints corrigidos após a leitura:**
   - coluna e cabeçalhos cortados;
   - rótulo do caminho cortado;
   - quebra de linha no meio de palavras;
   - nota sobre `is_private`/`is_verified` (`None` × `False`).

## Passo 6 — Testes

| Verificação | Resultado | Log |
|---|---|---|
| Suíte 3.12 (`not e2e/mobile/real`) | 844 passed, 0 failed | [`06-suite-312.log.txt`](06-suite-312.log.txt) |
| Suíte 3.13 | 844 passed, 0 failed | [`06-suite-313.log.txt`](06-suite-313.log.txt) |
| Contratos mobile (`tests/mobile`) | 24 passed (saída -q sem resumo: 24 pontos) | [`06-mobile-contratos.log.txt`](06-mobile-contratos.log.txt) |
| ruff / mypy | limpos | [`06-ruff.log.txt`](06-ruff.log.txt), [`06-mypy.log.txt`](06-mypy.log.txt) |

**Aceite:**

| # | Critério | Status | Evidência |
|---|---|---|---|
| 1 | Parte B com engine **sem `_driver`** atravessando a delegação real | ✅ | `test_contract_snippet_runs_end_to_end_with_fake_engine` (`not hasattr`, contador `['target']`); print 01 |
| 2 | Subclasse legada instanciável; mensagem cita engine e capacidade | ✅ | 2 testes; print 03 |
| 3 | Caminho legado com `_driver` inalterado | ✅ | `tests/test_profile.py` sem mudança; print 01 |
| 4 | Cascata para a capaz; challenge para a cascata | ✅ | 3 testes; print 02 |
| 5 | Httpx: `web_profile_info` → todos os campos; 404 | ✅ | 2 testes com resposta **simulada**; print 02 |
| 6 | Selenium dá o mesmo resultado que o legado | ✅ | teste + leitor compartilhado |
| 7 | Matriz de capacidades | ✅ | `test_capability_matrix`; print 03 |
| 8 | Mensagem cita `pip install instat[httpx]` | ✅ | teste; print 03 |
| 9 | 3.12, 3.13, ruff e mypy | ✅ | tabela acima |

"**A nova capacidade funciona sem Selenium, sem que o teste vire mock da própria API pública**": os testes chamam `InstaExtractor.get_profile` real, com `EngineManager` e governador reais. Só a engine é fake (adapter), e o contador prova que a chamada atravessou a delegação.

**Não testado:**
- formato real de `web_profile_info` (resposta simulada, sem tráfego);
- Selenium real e Instagram;
- leitura de perfil por Android UI (F7) e API móvel (F8);
- E2E com Firefox não reexecutado.

**Fora do escopo, mantido:** handoff de cookies do Selenium (M5) e atributo público `extractor.driver`.

## Passo 7 — Prints (lidos)

| Print | Leitura |
|---|---|
| [`01-contrato-legado.png`](01-contrato-legado.png) | engine sem `_driver` e legado com `_driver`: mesmos user, full_name, 1894/1892/123 e pic; `privado`/`verificado` None × False (nota explica); `hasattr = False`; delegação `['target']`; followers 3 e following 2 |
| [`02-perfil-sem-driver.png`](02-perfil-sem-driver.png) | httpx com resposta simulada: Alvo da Silva, 1894/1892/123, `False`/`True`, pic HD; bio `'bio\nlinha 2'`; requisição a `web_profile_info`; cascata legada → capaz (Nome Capaz 42/7/3, 1 chamada); challenge → `ExtractionStoppedError reason=challenge`, 0 chamadas à 2ª |
| [`03-capacidades.png`](03-capacidades.png) | selenium: extract/profile_info/total_count; playwright-chromium: extract/total_count; httpx: + recent_posts; android_ui e mobile_api: vazio; subclasse legada: extract/total_count; mensagens completas, sem cortes, citando capacidades e `pip install instat[httpx]` |

## Aceite de CI

Run `35217993911` no PR #12 (somente CI, fechado sem merge), em Linux:

| Execução | lint-and-type | test 3.12 | test 3.13 | build |
|---|---|---|---|---|
| 1ª | ✅ | ✅ | ❌ | pulado |
| reexecução dos jobs que falharam | ✅ | ✅ | ✅ | ✅ |

**A falha da 1ª execução não é código da F3.** Foi o cenário **B02 da F5** (`tests/jobstore/e3_scenarios.py`, backup incremental com heartbeat e escritor):

- **Medido:** pior latência do heartbeat = 0,529 s, acima do limite do cenário (`< 0,5 s`); status `timeout` correto, 0 falhas de heartbeat, 16 renovações, 175 escritas.
- **Reexecução:** passou.
- **Situação: intermitência não investigada.** Não se sabe se a latência veio de carga do runner compartilhado ou de contenção real entre heartbeat e escritor. O limite **não foi alterado**, porque isso mudaria a expectativa.
- **Pendência:** medir a distribuição da latência do heartbeat no B02 (várias repetições, local e CI) antes de decidir.

