# Architecture

InstaT separates **extraction logic** from **orchestration** so engines can be swapped, combined in fallback cascades, or run in parallel without touching user-facing APIs.

---

## Module Map

```
InstaT/
├── extractor.py              # InstaExtractor — public facade
├── async_extractor.py        # AsyncInstaExtractor — asyncio wrapper
├── parallel.py               # ParallelCoordinator + parallel_extract()
├── engines/
│   ├── base.py               # BaseEngine (ABC)
│   ├── engine_manager.py     # EngineManager — cascade orchestrator
│   ├── selenium_engine.py    # Primary (Firefox + GeckoDriver)
│   ├── playwright_engine.py  # Optional (Chromium + stealth)
│   └── httpx_engine.py       # Fallback (private mobile API)
├── login.py                  # InstaLogin — login flow + block detection
├── session_cache.py          # Cookie persistence
├── session_pool.py           # Multi-account rotation with cooldown
├── proxy.py                  # ProxyPool with cooldown
├── checkpoint.py             # Incremental progress persistence
├── backoff.py                # SmartBackoff (exponential + jitter)
├── exporters.py              # CSV / JSON / SQLite
├── exceptions.py             # Typed exception hierarchy
├── utils.py                  # Selenium helpers (JS batch read, etc.)
├── constants.py              # human_delay, timing constants
└── config/
    ├── selector_loader.py    # JSON selector loader with fallbacks
    └── selectors.json        # CSS/XPath selectors
```

---

## Layered Responsibilities

```
┌──────────────────────────────────────────────────────────┐
│  Facade         InstaExtractor / AsyncInstaExtractor     │
├──────────────────────────────────────────────────────────┤
│  Orchestration  EngineManager  |  ParallelCoordinator    │
├──────────────────────────────────────────────────────────┤
│  Engines        Selenium  |  Playwright  |  httpx        │
├──────────────────────────────────────────────────────────┤
│  Infra          SessionPool | ProxyPool | SessionCache   │
│                 Checkpoint  | SmartBackoff               │
└──────────────────────────────────────────────────────────┘
```

The facade holds no extraction logic; it resolves engines and delegates. Engines expose a uniform `BaseEngine` contract (`login`, `extract`, `get_total_count`, `quit`, `is_available`, `name`). Infra modules are stateless helpers or persistent side-effects (disk, in-memory pools).

---

## Engine Cascade (`EngineManager`)

The manager iterates `engines × sessions` and returns on the first success. Partial results are **always preserved** via a shared `profiles: Set[str]` updated by the `on_batch` callback — a failure in engine N still gives the caller whatever engines 1…N-1 collected.

```
for engine in engines:               # e.g. [selenium, httpx]
    for session in sessions_iter():  # [None] if no SessionPool
        try login(engine, session)
        run engine.extract(on_batch=update_shared_set)
        return shared_set            # success
    handle RateLimitError → mark session cooldown, next session
    handle AccountBlockedError → longer cooldown, next session
    handle BlockedError → next engine
    handle generic error → next engine
if shared_set:
    return partial                   # best-effort
raise AllEnginesBlockedError         # only when zero profiles collected
```

Key design decisions:

- **`default_credentials`** allows secondary engines to log in on demand without a formal `SessionPool` — enabling a simple Selenium → httpx cascade for a single user.
- **`_logged_in_engines`** set prevents duplicate logins when the primary engine has already authenticated in `InstaExtractor.__init__`.
- **`on_batch` updates the shared set in place**, so `BlockedError` mid-extraction doesn't lose data — the next engine resumes from what was collected.

---

## Parallel Coordination (`ParallelCoordinator`)

For single-list parallelism, N browsers work the same target profile. Because Instagram's ranking algorithm yields slightly different orderings per session/account, the **union** converges on the full list faster than any single session would on its own.

```
ParallelCoordinator
├── shared: Set[str]        (protected by threading.Lock)
├── stop_event: Event       (set when threshold reached)
├── target_count: Optional[int]
└── stop_threshold: float   (default 0.98)

For each worker thread:
  1. Spin up own SeleniumEngine
  2. Log in with own account (from accounts list, rotated)
  3. extract(..., on_batch=coord.ingest, should_stop=coord.should_stop)
  4. ingest(batch) → union + check threshold → maybe stop_event.set()
  5. should_stop() checked inside scroll loop → graceful exit
```

The Selenium scroll loop honors `should_stop()` after every batch, so stopping is bounded by ~1 scroll cycle (< 2 s typical).

**Account strategy:**

| Config | Behavior | Recommendation |
|---|---|---|
| `accounts=None` | All workers share `default_credentials` | OK for 1 worker; **risky** for ≥ 2 |
| `accounts=[a, b]`, `workers=2` | 1 account per worker | **Recommended** |
| `accounts=[a]`, `workers=3` | Rotated; 2 workers share `a` | Warned in log |

---

## Session & Proxy Pools

`SessionPool` encapsulates multiple Instagram accounts. Each call to `available_sessions()` returns sessions not currently in cooldown. `mark_blocked(session, cooldown_seconds)` puts a session on ice after a rate-limit or account-block signal.

Default cooldowns:
- `DEFAULT_COOLDOWN` = 5 min (for `RateLimitError`)
- `META_INTERSTITIAL_COOLDOWN` = 1 h (for `AccountBlockedError`)

`ProxyPool` is analogous for HTTP proxies; supports round-robin with cooldown on failure.

Both pools integrate transparently with `EngineManager`.

---

## Persistence Layers

### `SessionCache` — cookie reuse
After a successful login, Selenium's `get_cookies()` is serialized to `.instat_sessions/<username>.json` (1 h TTL). On the next run, `InstaLogin._try_restore_session` injects these cookies, skipping the login form (the biggest ban trigger).

`HttpxEngine.login_with_cookies(cookies_list)` accepts the same shape, allowing in-process handoff from Selenium — no disk round-trip required.

### `ExtractionCheckpoint` — incremental progress
Every `checkpoint_interval` new profiles (default 100), the shared set is dumped to `.instat_checkpoints/<profile_id>_<list_type>.json`. On resume, `load()` returns a set to merge into the fresh run. Expires after 24 h.

Checkpoints are maintained at the **orchestrator** level (`EngineManager`), so they survive engine switches.

### Artifacts on block
When `InstaLogin` detects a checkpoint / 2FA / Meta interstitial, it captures a timestamped screenshot and HTML dump under `InstaT/logs/artifacts/`. The raised `AccountBlockedError` carries `.reason`, `.screenshot_path`, `.html_path` for forensic review.

---

## Exception Hierarchy

```
Exception
├── LoginError                # credential or form-loading failure
├── ProfileNotFoundError      # 404 on profile
├── BlockedError              # generic engine-level block
│   ├── RateLimitError        # 429 / throttled
│   └── AccountBlockedError   # checkpoint / 2FA / interstitial
└── AllEnginesBlockedError    # cascade exhausted, zero profiles
```

Client code should catch `AccountBlockedError` distinctly (it carries actionable artifact paths) and treat `AllEnginesBlockedError` as terminal.

---

## Configuration Surface

| Where | What |
|---|---|
| `InstaExtractor(...)` kwargs | Engines, proxies, accounts, exporter, headless, timeout |
| Attributes post-construction | Scroll / retry / checkpoint tunables (see [`USAGE.md`](USAGE.md#tuning-parameters)) |
| `config/selectors.json` | All CSS/XPath selectors with fallback alternatives |
| Env vars | `INSTAT_USERNAME`, `INSTAT_PASSWORD`, `GH_TOKEN` (for GeckoDriver download) |

Selectors support two formats transparently:

```json
{
  "FOLLOWERS_LINK": [                                     // list = fallback chain
    "//a[contains(@href, '/followers/')]",
    "//a[.//span[contains(text(), 'seguidores')]]"
  ],
  "LOGIN_USERNAME_INPUT": "input[name='username']"        // string = single selector
}
```

`SelectorLoader.get_all(key)` returns the list; `find_element_with_fallback` tries them in order.

---

## Testing Strategy

- **Unit tests** (243) use `unittest.TestCase` + `MagicMock` — no real browser, no network. Run in ~90 s.
- **Integration tests** marked `@pytest.mark.e2e` require real Firefox and are deselected by default (`-m "not e2e"`).
- **Key invariants covered:** partial-progress preservation across engine failures; `should_stop` signal propagation; account rotation; checkpoint resume; selector fallback; account-block detection variants.

CI runs `ruff check`, `mypy InstaT`, `pytest -m "not e2e"` on every push.
