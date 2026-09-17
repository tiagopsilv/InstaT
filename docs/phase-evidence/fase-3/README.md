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
