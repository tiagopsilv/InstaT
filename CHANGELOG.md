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
