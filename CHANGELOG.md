# Changelog

All notable changes to InstaT are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); version numbers follow [SemVer](https://semver.org).

## [Unreleased]

### Added
- **Parallel extraction on a single list.** `get_followers_parallel(profile_id, workers, accounts, stop_threshold, ...)` and `get_following_parallel(...)` run N browsers concurrently, union results via `ParallelCoordinator`, and stop all workers once `stop_threshold × total_count` is reached.
- **`ParallelCoordinator`** with thread-safe shared set and `threading.Event` for cooperative stop.
- **Concurrent `get_both(profile_id)`** — followers and following in parallel with a three-tier fallback (httpx cookie-handoff → 2nd Selenium → sequential).
- **`HttpxEngine.login_with_cookies(cookies_list)`** — in-process cookie handoff from Selenium, bypassing form-login 403s.
- **`should_stop` callable** propagated through `SeleniumEngine.extract` / `_extract_list` / `_get_profiles`, checked inside the scroll loop for graceful early exit.
- **`default_credentials` on `EngineManager`** — secondary engines auto-login via shared credentials when no `SessionPool` is configured.
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
