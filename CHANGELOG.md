# Changelog

All notable changes to InstaT are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers follow [SemVer](https://semver.org).

## [Unreleased]

### Changed — BREAKING
- **Python mínimo passa a ser 3.12** (`requires-python = ">=3.12"`). Instalações em 3.9, 3.10 e 3.11 deixam de ser suportadas; o pip recusa instalar nessas versões. Classificadores, `ruff`, `mypy`, README e matriz de CI alinhados. 3.13 e 3.14 só serão declarados com evidência de instalação e suíte. A versão do pacote não foi alterada aqui: a quebra pede bump major no próximo release, a decidir.

### Fixed
- **Wheel sem `instat.mobile`:** `pyproject.toml` listava os pacotes à mão e omitia `instat.mobile`; instalado por wheel, `engines=["android_ui"]` virava apenas um aviso de engine desconhecida. A descoberta passa a ser automática (`packages.find`), com `namespaces = false` e exclusão de `instat.logs*`, para que diretórios de diagnóstico nunca entrem no pacote.
- **Stub mobile como engine primária** passa a falhar na construção com `NotImplementedError` citando a fase do roadmap (F7/F8), em vez de `LoginError` genérico.
- **Testes dependentes de extras opcionais** (`httpx`, `playwright`) passam a pular explicitamente quando o extra falta; o CI instala os extras para executá-los.
- **Extra `stealth` em Python 3.12:** `undetected-chromedriver` 3.5.5 importa `distutils`, removido do Python 3.12, e falhava com `ModuleNotFoundError`. O extra passa a declarar `setuptools>=60`, que fornece a camada de compatibilidade. Só a importação foi verificada; o modo stealth em execução exige Chrome e não foi testado.
- **Teste que abria navegador real:** `test_account_rotation.py::test_inherits_imap_config_engines_timeout` usava `patch(..., side_effect=InstaExtractor)`, construindo o extractor real — abria um Firefox visível, tentava login com credenciais falsas e deixava o navegador aberto, travando quem aguardava a saída da suíte. Agora o mock só captura os argumentos.
- **`ruff`:** cinco erros que já existiam antes desta mudança (ordem de imports em `diagnostics.py`, `logging_config.py` e `post_metrics.py`; variável não usada e `lambda` atribuída em `tests/test_recent_gaps.py`) corrigidos, para o job de lint passar.
- **Sessão restaurada em challenge contava como login válido (F1).** O `SessionRestorer` só verificava se a página saía de `/accounts/login`, e o `login()` pulava o `BlockDetector`. Agora a restauração exige URL limpa, `sessionid` e o mesmo `ds_user_id` salvo. Em bloqueio, levanta `AccountBlockedError`/`BlockedError` sem tentar o formulário. O Playwright usa a mesma validação.
- **Cache de sessão (F1):**
  - Arquivo inválido vira cache miss em vez de exceção, e o TTL expira na fronteira.
  - A escrita passou a ser atômica.
  - Uma restauração validada renova a idade. Antes, reinícios frequentes forçavam um novo login a cada hora.
  - O diretório padrão passa a ser `INSTAT_SESSION_DIR` ou `~/.instat/sessions`, com leitura do antigo `.instat_sessions` do cwd. A imagem Docker fixa `INSTAT_SESSION_DIR=/app/.instat_sessions`.
  - O formato v2 registra `username`, `ds_user_id` e `backend` (este último só no Playwright). O v1 continua legível.
- **User-Agent incoerente (F1):** o Firefox (Selenium e Playwright) declarava Chrome 89/Android 8. Agora cada motor usa um UA da própria família (Chrome, Firefox, Safari/iOS).
- **Testes de login sem rede (F1):** `tests/test_login.py` consultava o webdriver-manager (API do GitHub) e falhava por rate limit no CI. O marcador `real` foi registrado, é opt-in por `INSTAT_REAL_TESTS=1` e fica excluído do CI.

### Changed — política de erros (F2)
- **Governador de erros e orçamento** (`instat/governor.py`, roadmap §5.6). Toda tentativa de login ou extração do `EngineManager` passa por ele.
  - **Mudança de comportamento:**
    - 429, challenge/checkpoint, restrição (`feedback_required`/403) e erros do proxy **interrompem a cascata**: nenhuma outra conta do `SessionPool` nem outro engine é tentado com a mesma conta.
    - `until_complete` não repete e a rotação de contas de fallback não acontece após essas paradas.
    - Antes, o código trocava de conta e de engine e esperava com `time.sleep` real (medido: 270 s de espera em 4 tentativas contra um challenge).
  - **Tentativas:** falhas transitórias (timeout, conexão, 5xx) repetem no mesmo engine até 3 vezes, com backoff limitado e `Retry-After` (segundos ou HTTP-date). Erros técnicos continuam caindo para o próximo engine.
  - **Estados e orçamento:**
    - estados por (conta, operação): `paused`, que expira; `needs_attention` e `restricted`, que só saem com liberação manual e sessão validada;
    - orçamento de tentativas, bytes (httpx) e duração, com as esperas incluídas.
  - **Motivo da parada:** `EngineManager.last_stop` e `metrics_sink['terminal_reason']`. Sem nenhum perfil coletado, levanta `ExtractionStoppedError`, subclasse de `AllEnginesBlockedError`.
  - Os limites são operacionais, não limites seguros do Instagram.
- **`HttpxEngine`:**
  - distingue proxy (`ProxyError` com as causas documentadas do DataImpulse), serviço (`TransientError`), challenge (`ChallengeError`) e restrição (`RestrictedError`), todas subclasses de `BlockedError`;
  - `RateLimitError` ganhou `retry_after`.
- **`Utils.wait_for_new_profiles`:** o laço em `StaleElementReferenceException` passou a ter teto (5).

### Changed — exclusividade de contas no paralelismo (F4)
- **`parallel_extract` nunca usa a mesma conta em duas sessões simultâneas.**
  - `workers` é limitado ao número de contas **distintas** em `accounts`; sem `accounts`, roda 1 worker com a credencial default.
  - Antes, o código só avisava e repetia credenciais.
  - As falhas por worker ficam em `instat.parallel.last_report`.
- **Novo `instat.scheduler.AccountScheduler`: exclusividade entre processos sobre o `JobStore` da F5**, sem nova transação de posse.
  - **Concessão:** uma conta tem no máximo uma concessão vigente em qualquer processo; workers ≤ contas elegíveis.
  - **Sinais:**
    - challenge → `needs_attention`;
    - restrição → `restricted`;
    - nenhum dos dois é liberado pelo tempo, só manualmente com sessão validada;
    - 429 → cooldown do endpoint.
  - **Recursos:** falha técnica libera a concessão; engine criada, usada e encerrada na mesma thread.
- **`JobStore`:** `release_lease`, `lease_valid`, `set_endpoint_cooldown`, `eligible_accounts`, `last_lease` e `last_release`.

### Changed — metadados de perfil sem Selenium (F3)
- **`InstaExtractor.get_profile` não exige mais `_driver`.**
  - Delega para engines com a capacidade `profile_info`: Selenium lê o DOM como antes; `HttpxEngine` usa `web_profile_info`.
  - Engines e objetos legados que só expõem `_driver` continuam no leitor DOM original.
  - Sem engine capaz, continua `RuntimeError`, agora com mensagem que lista as capacidades de cada engine e extras não instalados (`pip install instat[httpx]`).
  - Uma parada terminal do governador (challenge, restrição, 429, proxy) levanta `ExtractionStoppedError`.
- **Capacidades explícitas:**
  - `BaseEngine.capabilities`, derivada dos métodos sobrescritos ou declarada pela engine;
  - `BaseEngine.get_profile_info`, não abstrato: subclasses existentes continuam instanciáveis;
  - novo `instat.profile_info.ProfileInfo`.
- **Teste de contrato:** a parte B não injeta mais `_FakeDriver`; o snippet público roda com engine sem `_driver`.

### Added — persistência de jobs (F5)
- **`instat.jobstore`** (roadmap §6.3): jobs, execuções, páginas, membros e observações em SQLite (WAL).
  - **Garantias:**
    - posse por concessão com geração (fencing);
    - horário lido depois do `BEGIN IMMEDIATE`;
    - idempotência por `attempt_id` + conteúdo canônico `page-v1`;
    - no máximo uma página `trusted` por posição;
    - releituras (2 adicionais) e execuções (3) limitadas;
    - restrição e challenge só liberados manualmente com sessão validada.
  - **Componentes:**
    - spool local durável de tentativas;
    - heartbeat em thread e conexão próprias;
    - `CursorWorker` (leitura → spool → commit → ack);
    - backup online em passo único com verificação pela cópia e comparação exata só em manutenção drenada;
    - migração de `profiles_seen` como `suspect` (`legacy_import`).
  - **Novas visões:** `JobStore.run_result(run_id)`, que usa só a própria execução, e `JobStore.job_view(job_id)`, com seleção, última tentativa e histórico observado rotulado.
  - **Política:** `sanity-v1` gravada por página, com limites operacionais iniciais não calibrados.
  - **Compatibilidade:** `get_followers`, `get_following`, `*_persistent` e `PersistentStore` não mudam.
- **Abertura resiliente do banco:** um `disk I/O error` transitório logo após a morte de processos (medido no Windows: some em ~0,2 s, com `integrity_check` ok) é repetido com limite na abertura.

### Added
- **Post metrics** — `extractor.get_recent_posts(profile_id, limit=N)` returns `List[PostMetrics]` (shortcode, likes_count, comments_count, timestamp, caption, hashtags, media_type, media_url) via `HttpxEngine` paginating the private `/feed/user/{user_id}/` endpoint. Feeds engagement formulas (TEP) without the consumer scraping post pages itself. Other engines raise `NotImplementedError` so the cascade falls through.
- **Rich follower metadata** — `get_followers(..., with_metadata=True)` and `get_following(..., with_metadata=True)` return `List[ProfileSummary]` instead of `List[str]`. Populates `user_id` (numeric `pk`), `full_name`, `is_verified`, `is_private`, `is_business`, `profile_pic_url` from the IG private API. Selenium/Playwright degrade gracefully to username-only summaries.
- **Audit telemetry** — `get_followers_with_metrics()` / `get_following_with_metrics()` return `ExtractionResult` wrapping the profiles with `engine_used`, `sessions_used`, `duration_seconds`, `rate_limit_hits`, `partial`, `coverage_pct`, `block_predictor_score`, `started_at`, `finished_at`. `to_dict()` is BigQuery-friendly.
- **Remote browser dependency injection** — `PlaywrightEngine(connect_endpoint=..., connect_mode='cdp'|'ws', connect_headers=...)` and `SeleniumEngine(webdriver_factory=Callable[[bool], WebDriver])` enable connecting to hosted scraping browsers (Bright Data Scraping Browser, Browserless, Selenium Grid, Playwright Server). `InstaExtractor._build_engines` accepts mixed `engines=[BaseEngine_instance | str, ...]`.
- **Bright Data provider helpers** — `brightdata_playwright_engine()` / `brightdata_selenium_engine()` ship pre-configured factories. Default `auth_mode='header'` per BD's official guidance (`Authorization: Basic <b64>`), with `'url'` mode kept for back-compat. **Public scraping only** — BD policy forbids automated IG logins.
- **`stealth_mode='undetected_chrome'`** on `SeleniumEngine` — opt-in alternative to Firefox+GeckoDriver, uses `undetected-chromedriver` to remove bot signatures (navigator.webdriver, plugins, languages mismatch, chrome.runtime quirks) IG actively probes. Requires `pip install instat[stealth]` + local Chrome.
- **`BloksCodeEntryResolver`** — handles Meta's `auth_platform/codeentry` flow (the second step after the standard email challenge). Detects cooldown countdown (`"We can send a new code in MM:SS"`), waits, clicks "Send a new code" via JS, polls IMAP, fills via React-compatible event dispatch, clicks Continue.
- **Iterative challenge chain** — `ChallengeResolverChain.try_resolve` loops up to `max_iterations=4`, re-scanning the chain after each successful resolve. Handles multi-step verification (email → Bloks → home) without exiting prematurely.
- **`should_stop` callback** — `extractor.get_followers(should_stop=callable)` propagates through `EngineManager.extract` to all engines. Selenium honors it inside scroll loop; httpx between paginated requests; Playwright between scroll batches.
- **`use_stdlib_logging=True`** kwarg + `configure_logging()` helper — reroutes Loguru records to stdlib `logging` so Cloud Logging / journald / any stdlib handler picks them up. Default behaviour unchanged.
- **Bio extraction in `get_profile()`** — 3-strategy JS probe (header section semantic scan, JSON inline `"biography":"..."`, permissive `span[dir="auto"]` scan). Populates `Profile.bio` which was always `None` before.
- **`undetected-chromedriver`** as optional dep via `pip install 'instat[stealth]'`.

### Changed
- Test count grew from 243 → 590, with new suites for post metrics, profile summary, extraction result, providers, challenge resolver chain iteration, and stealth modes.
- README highlights table expanded; install command shows `[stealth]` extra.
- `docs/USAGE.md` gains four major sections: post metrics, rich metadata + audit telemetry, stealth via undetected-chromedriver, Bright Data Scraping Browser (public-content only).
- `EmailChallengeResolver.can_handle()` now rejects `auth_platform` URLs to avoid heading-text collision with the Bloks flow.

### Fixed
- **Gap 11 — partial preservation in `with_metadata=True` mode.** When Selenium raised `BlockedError` for partial coverage and httpx fell through (rate-limited), `with_metadata=True` was returning empty (`AllEnginesBlockedError`) even though `on_batch` had received 100+ profiles. Now the username set is populated regardless of metadata mode; the partial fallback synthesises a `List[ProfileSummary]` username-only from the accumulated set.
- **`playwright_stealth` 1.x ↔ 2.x compatibility shim** in `PlaywrightEngine.login()` — `stealth_sync` was removed in `playwright_stealth>=2.0` (now `Stealth().apply_stealth_sync`). Pre-existing bug fixed; no user-facing change.
- **Bright Data Scraping Browser `pageLoadStrategy='eager'`** baked into the `brightdata_selenium_engine()` helper — default `'normal'` hangs indefinitely on Instagram with BD's full Chrome desktop (validated in InstaT live testing 2026-05-14).

## [1.0.3] — Diagnostics + Parallel + Cascade (previously unreleased work)

### Added
- **Parallel extraction on a single list.** `get_followers_parallel(profile_id, workers, accounts, stop_threshold, ...)` and `get_following_parallel(...)` run N browsers concurrently, union results via `ParallelCoordinator`, and stop all workers once `stop_threshold × total_count` is reached.
- **`ParallelCoordinator`** with thread-safe shared set and `threading.Event` for cooperative stop.
- **Concurrent `get_both(profile_id)`** — followers and following in parallel with a three-tier fallback (httpx cookie-handoff → 2nd Selenium → sequential).
- **`HttpxEngine.login_with_cookies(cookies_list)`** — in-process cookie handoff from Selenium, bypassing form-login 403s.
- **`should_stop` callable** propagated through `SeleniumEngine.extract` / `_extract_list` / `_get_profiles`, checked inside the scroll loop for graceful early exit.
- **`default_credentials` on `EngineManager`** — secondary engines auto-login via shared credentials when no `SessionPool` is configured.
- **`DiagnosticCollector`** — full page-state bundle (metadata.json, page_source.html, screenshot.png, console_log.txt, redacted cookies.json) on every error/timeout event for first-shot debugging.
- `docs/ARCHITECTURE.md`, `docs/USAGE.md`, `docs/TROUBLESHOOTING.md`, `CHANGELOG.md`.

### Changed
- Test count grew from 38 → 243, including new suites for `get_both`, parallel coordination, threshold-based partial-coverage detection, modal reopen recovery, and cookie-handoff.
- README overhauled for a multi-engine, multi-account, parallel-first workflow.

### Fixed
- Partial results are now preserved across engine failures: `on_batch` updates the shared `profiles` set in place so a `BlockedError` mid-extraction doesn't discard collected data.
- `SeleniumEngine.extract` previously ignored `existing_profiles` / `on_batch` kwargs — they are now threaded through to `_extract_list` and `_get_profiles`.
- Stale `body` reference across `driver.refresh()` — container is re-localised via JS every scroll.
- Modal not reopening on a second sequential call — `_reset_page_state()` navigates to `about:blank` between extractions.

## [1.0.2]

### Added — Performance (PERF-02 / PERF-03)
- `SeleniumEngine.completion_threshold` (default 0.90): below → `BlockedError("partial coverage")`, enabling engine cascade to take over.
- `_reopen_modal(profile_id, list_type)` — closes and reopens the followers/following modal to reset Instagram's pagination cursor after rate-limit stalls.
- JS-based batch read + scroll (`Utils.batch_read_text`, `_scroll_modal_js`): ~10× fewer IPC round trips than per-element reads.

## [1.0.1] — Backlog BL-01 … BL-20

### Added
- **Multi-engine architecture**: `SeleniumEngine`, `PlaywrightEngine`, `HttpxEngine`, orchestrated by `EngineManager`.
- **`AsyncInstaExtractor`** — `asyncio.to_thread` wrapper for concurrent pipelines.
- **`SessionPool`** / **`ProxyPool`** with cooldown rotation on `RateLimitError` / `AccountBlockedError`.
- **`BaseExporter`** + CSV / JSON / SQLite implementations; auto-export via `exporter=` constructor kwarg.
- **CLI** (`instat extract` / `instat count`) with format inferred from extension.
- **Docker image** and `docker-compose.yml`.
- Ruff + mypy + pytest in GitHub Actions CI.

## [1.0.0]

### Added
- Initial stable release: Selenium-based extractor with humanised timing, exponential backoff, incremental checkpoints, session-cookie cache, resilient selectors with fallback lists, and account-block detection (checkpoint, 2FA, Meta Verified interstitial).
- 38 unit tests covering all core modules.
