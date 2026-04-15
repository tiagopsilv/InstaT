# Troubleshooting

Operational guide for the most common failure modes. Check logs first: InstaT emits structured Loguru output to stderr and `InstaT/logs/insta_extractor.log`.

---

## Login failures

### `LoginError: Failed to login as <user>` — Timeout waiting for login form

**Symptom:** Firefox loads `instagram.com/accounts/login/` but the username/password fields never become visible within `timeout` seconds.

**Common causes:**
1. Instagram is showing a **challenge/checkpoint page** instead of the form (triggered by many login attempts from the same IP/account).
2. The account requires 2FA and the flow is waiting for the code page that InstaT can't fill.
3. Network latency — IG's login page is slow (rare).

**Fixes:**
- Wait 1–6 h before retrying with the same account. Challenges clear.
- Run with `headless=False` and log in manually once. Cookies get cached under `.instat_sessions/` and the next headless run reuses them.
- Use a different account or a VPN/proxy.
- Increase `timeout=30` if you suspect real latency.

### `AccountBlockedError: checkpoint required`

Instagram is demanding identity verification (SMS/email code or photo).

- Check `InstaT/logs/artifacts/<timestamp>.png` — the screenshot tells you exactly which challenge.
- Complete the challenge manually via browser on the same IP, then retry. Cookies from the manual login work.
- Don't retry in a loop — IG will escalate to a hard ban.

### `httpx: unexpected status 403` on isolated httpx login

Instagram's anti-abuse system blocks ajax form-logins from burned IPs. InstaT mitigates this by **never doing httpx form-login in production paths** — cookies always flow from a successful Selenium login via `SessionCache` or direct `login_with_cookies` handoff. If you see this in an isolated test, it's expected.

---

## Extraction failures

### `BlockedError: partial coverage: X/Y (Z%)`

The Selenium engine collected fewer than `completion_threshold` (default 90%) of the expected count. Raised intentionally so the `EngineManager` can try the next engine.

**If this happens every run:**
- Instagram is rate-limiting the scroll modal. Try `ext._engine.completion_threshold = 0.80` to accept lower coverage, or wait a few hours.
- Check the log for `"Reopening modal"` entries — PERF-03 already retries the modal up to 3 times. If reopens are failing, the account may be shadow-banned from viewing the target list.
- Run `get_followers_parallel` with `accounts=[...]` to distribute load.

### `AllEnginesBlockedError`

Every configured engine failed **and** zero profiles were collected across the whole cascade. Partial progress is always returned as a list — this exception only fires on a complete shutout.

- Check each engine's log entries. Usually one of: Selenium login blocked, httpx endpoint 429/403, Playwright not installed.
- Try later, with different accounts, through a proxy.

### Modal fails to open

Symptoms: `_click_link_element` logs `"Native click failed … trying JS click..."` repeatedly.

- Instagram occasionally renders the followers link as a non-interactable element (overlay, tooltip). PERF-02 added `_reset_page_state()` which navigates to `about:blank` between calls — ensure you're on a recent version.
- If only happens after a previous extraction in the same session, add an explicit `ext._engine._reset_page_state()` between calls.

### Extraction hangs with no new profiles

The scroll loop is running but `batch_read_text` keeps returning the same set.

- Could be rate limiting. Check log for `"Stale rounds: X/4"` — if it hits 4 and no recovery, PERF-03's modal reopen kicks in.
- If reopen runs 3× and still stale, the engine gives up and returns partial. Move on to another account or wait.

---

## Performance

### "It's too slow for my target size"

Baseline (single Selenium, 1 900-profile target): ~340 s, 99.9% coverage.

Options, fastest → most complex:

1. **`stop_threshold` < 1.0** — stops once enough coverage is reached. At 0.95 you shave ~20% time.
2. **`get_both`** — runs followers and following concurrently. On clean IP: ~max(followers_time, 90s httpx_time) instead of sum.
3. **`get_followers_parallel` with multiple accounts** — see [speedup table](USAGE.md#parallel-extraction).
4. **Lower `pause_time`** (default 0.5 s). Below 0.2 s increases detection risk.

### "It's slower with 2 workers than with 1"

Likely running 2 workers on the **same account**. Instagram de-prioritizes parallel sessions of the same user → rate-limits hit sooner on both. The fix is `accounts=[...]` with 1 account per worker.

Also check the log for `"parallel worker N failed: partial coverage"` — a worker that hits the BlockedError threshold still contributes its partial batches to the union (via `on_batch`), so wall time is fine but peak coverage may be slightly below single-worker.

---

## Data / export

### Duplicate profiles in output

All extraction paths return `list(set(...))` internally — no duplicates within a single call. If you see duplicates in a persistent store, check your `BaseExporter` implementation: `SQLiteExporter` uses `INSERT OR IGNORE`, but a custom exporter might not.

### SQLite "database is locked"

The default `SQLiteExporter` writes synchronously. If two `InstaExtractor` instances target the same DB concurrently, one blocks the other. Solutions:
- Use one DB per instance and merge after.
- Pass `timeout=30` when constructing `SQLiteExporter`.
- Switch to `JSONExporter` / `CSVExporter` for concurrent writers.

---

## Selectors / UI changes

Instagram frequently reshuffles its DOM. `InstaT/config/selectors.json` is the single source of truth. When extraction suddenly breaks after working for months:

1. Run with `headless=False` and open DevTools on the failing page.
2. Find the element that `find_element_with_fallback` can't locate (log will say which selector key).
3. Add a new alternative to the top of that key's list in `selectors.json`:

   ```json
   "FOLLOWERS_LINK": [
     "<new_selector_found_via_devtools>",
     "//a[contains(@href, '/followers/')]",
     "..."
   ]
   ```

4. Keep old selectors in the list — regional/A-B variants often coexist.

---

## Environment

### `webdriver-manager` rate-limited by GitHub

The GeckoDriver download hits GitHub's release API, which limits anonymous calls.

- Set `GH_TOKEN` env var with a personal access token (any public scope).
- Or pre-download the driver and point `webdriver-manager` at the cache.

### Firefox not found

`InstaLogin` relies on a system Firefox install. On Windows the installer at `C:\Program Files\Mozilla Firefox\firefox.exe` is auto-discovered. On Linux: `apt install firefox-esr`. On Docker: already baked into the provided image.

### Playwright not installed / browsers missing

```bash
pip install 'instat[playwright]'
playwright install chromium
```

The Playwright engine reports `is_available = False` if either package or browser is missing; `EngineManager` silently skips it and moves to the next engine.

---

## Diagnosis checklist

When opening a bug report, include:

1. `pip show instat` output (version + installed location).
2. Relevant tail of `InstaT/logs/insta_extractor.log` (scrub credentials — the logger redacts `password` but verify).
3. Any artifacts under `InstaT/logs/artifacts/` for the failing run.
4. Output of `python -m pytest tests/ -m "not e2e"` to confirm unit suite passes in your environment.
5. `selectors.json` if you modified it.
