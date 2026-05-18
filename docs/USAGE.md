# Usage Reference

Complete reference for every entry point. All examples assume `INSTAT_USERNAME` and `INSTAT_PASSWORD` are set in the environment.

---

## `InstaExtractor`

Primary synchronous facade.

### Email challenge auto-resolve (IMAP)

When Instagram shows "Check your email — enter the code", InstaT can
auto-fetch the code from your inbox via IMAP:

```python
ext = InstaExtractor(
    username, password,
    imap_config={
        "host": "imap.gmail.com",
        "user": "your_email@gmail.com",
        "password": "<gmail_app_password>",  # NOT the regular password
        "port": 993,
        "timeout": 90,         # total seconds polling
        "poll_interval": 3.0,
        "since_minutes": 10,   # only consider emails newer than this
    },
)
```

For Gmail: enable 2FA → https://myaccount.google.com/apppasswords →
"App: Mail / Device: Other" → use the 16-char password here.

When the challenge appears, `InstaLogin` detects the `Check your email`
heading, polls IMAP for a recent message from `*@mail.instagram.com`
containing a 6-digit code, fills the input, and clicks Continue. Cookies
are saved to `SessionCache` so subsequent runs skip the challenge.

### Constructor

```python
InstaExtractor(
    username: str,
    password: str,
    *,
    headless: bool = True,
    timeout: int = 10,
    engines: list[str] = ["selenium"],     # any of: "selenium", "playwright", "httpx"
    proxies: list[str] | None = None,
    accounts: list[dict] | None = None,    # [{"username": ..., "password": ...}, ...]
    exporter: BaseExporter | None = None,  # auto-export after every extraction
)
```

- When `accounts` is provided, `SessionPool` handles rotation on block/rate-limit; initial login is deferred to `EngineManager`.
- When only `(username, password)` is given, primary engine logs in immediately; secondary engines log in on demand via cached cookies.
- `engines` determines the cascade order. Unavailable engines are skipped with a warning.

### Extraction methods

| Method | Returns | Notes |
|---|---|---|
| `get_profile(profile_id)` | `Profile` | 1 navigation; cheap header metadata + bound extraction methods |
| `get_followers(profile_id, max_duration=None)` | `list[str]` | Single engine cascade |
| `get_following(profile_id, max_duration=None)` | `list[str]` | Single engine cascade |
| `get_both(profile_id, max_duration=None)` | `dict[str, list[str]]` | `{"followers": [...], "following": [...]}` — runs in parallel, httpx cookie-handoff, falls back to 2nd Selenium then sequential |
| `get_followers_parallel(profile_id, workers=2, accounts=None, stop_threshold=0.98, max_duration=None, headless=True)` | `list[str]` | N browsers union |
| `get_following_parallel(...)` | `list[str]` | Same for following |
| `get_total_count(profile_id, list_type)` | `int \| None` | Read counter without extracting |

### `Profile` object

```python
target = ext.get_profile("tiagopsilv")

target.username          # str
target.url               # str — canonical profile URL
target.full_name         # str | None
target.followers_count   # int | None
target.following_count   # int | None
target.posts_count       # int | None
target.is_private        # bool | None
target.is_verified       # bool | None
target.profile_pic_url   # str | None

# Sync methods (delegate to the extractor)
target.get_followers(max_duration=None)
target.get_following(max_duration=None)

# Parallel (delegates to get_followers_parallel / get_following_parallel
# when workers >= 2)
target.get_followers(
    workers=3,
    accounts=[
        {"username": "alt1", "password": "..."},
        {"username": "alt2", "password": "..."},
        {"username": "alt3", "password": "..."},
    ],
    stop_threshold=0.98,
    max_duration=300,
)

# Async (asyncio.to_thread wrappers — accept the same kwargs)
await target.aget_followers(workers=2, max_duration=120)
await target.aget_following()
```

Implementation: 1 page load + parse of `og:description`, `og:title`, `og:image` meta tags + 2 quick `execute_script` probes for verified/private. No scroll, no heavy DOM traversal. Attributes that couldn't be parsed are `None`.

`list_type` is `"followers"` or `"following"`.

### Export convenience

```python
ext.to_csv(   profile_id, list_type, path)
ext.to_json(  profile_id, list_type, path, indent=2)
ext.to_sqlite(profile_id, list_type, db_path, table="profiles")
```

Each method extracts and writes in one call. Returns the profile list.

For automatic export on every `get_*` call, inject an exporter at construction:

```python
from instat import InstaExtractor, JSONExporter

ext = InstaExtractor(user, pw, exporter=JSONExporter("out.json"))
ext.get_followers("target")  # auto-writes
```

### Tuning parameters

Set after construction:

```python
ext.max_refresh_attempts          = 100   # page refreshes before giving up
ext.wait_interval                 = 0.5   # seconds between profile checks
ext.additional_scroll_attempts    = 1     # extra scrolls on stall
ext.pause_time                    = 0.5   # seconds between scrolls
ext.max_attempts                  = 2     # scroll attempts per cycle
ext.max_retry_without_new_profiles = 3    # retries before backoff
ext.checkpoint_interval           = 100   # save every N profiles
```

Selenium engine adds:

```python
ext._engine.completion_threshold = 0.90   # below → BlockedError (partial coverage)
```

### Lifecycle

```python
ext.quit()  # close all browsers / clients
```

---

## `AsyncInstaExtractor`

Async wrapper over `asyncio.to_thread`. Useful for concurrent pipelines on multiple target profiles.

```python
import asyncio
from instat import AsyncInstaExtractor

async def main():
    async with AsyncInstaExtractor(user, pw, engines=["selenium", "httpx"]) as ext:
        results = await asyncio.gather(
            ext.get_followers("a"),
            ext.get_followers("b"),
            ext.get_followers("c"),
        )
    return results

asyncio.run(main())
```

Note: one browser serves all concurrent calls — the async wrapper parallelizes at the task level, not the engine level. For true engine-level parallelism, use `get_followers_parallel` or instantiate multiple `InstaExtractor`s.

---

## Multi-account & proxy rotation

```python
from instat import InstaExtractor

ext = InstaExtractor(
    username="primary", password="p1",
    accounts=[
        {"username": "primary",  "password": "p1"},
        {"username": "backup1",  "password": "p2"},
        {"username": "backup2",  "password": "p3"},
    ],
    proxies=[
        "http://user:pass@proxy1:8080",
        "socks5://proxy2:1080",
    ],
    engines=["selenium", "httpx"],
)
```

`SessionPool` rotates accounts on `RateLimitError` / `AccountBlockedError`. `ProxyPool` rotates proxies on connection failure. Cooldowns ensure burned sessions rest before retry.

---

## Parallel extraction

```python
# 2 browsers, 2 accounts, high throughput
followers = ext.get_followers_parallel(
    "target",
    workers=2,
    accounts=[
        {"username": "alt1", "password": "..."},
        {"username": "alt2", "password": "..."},
    ],
    stop_threshold=0.98,  # stop all workers when 98% of total reached
    max_duration=600,     # hard cap per worker
    headless=True,
)
```

**When to parallelize**
- Target profile has > ~1 000 followers/following (otherwise overhead dominates)
- You have ≥ `workers` distinct Instagram accounts
- You want faster wall-clock time and can tolerate higher detection risk

**Expected speedup** (empirical, YMMV):
- 2 workers / 2 accounts: ~1.5× faster
- 3 workers / 3 accounts: ~2× faster
- Single account with N workers: usually no speedup, may be **slower** due to IG blocking parallel sessions of the same account

---

## Rich metadata + audit telemetry

Two opt-in flags upgrade what the extraction call returns:

### `with_metadata=True` — `List[ProfileSummary]` instead of `List[str]`

When httpx is in the cascade, each follower entry comes back with the
basic profile fields IG ships in the bulk API:

```python
summaries = ext.get_followers(
    "target_profile",
    with_metadata=True,   # opt-in; List[str] is still the default
)

for s in summaries:
    print(s.username, s.user_id, s.is_verified, s.is_business)
```

`ProfileSummary` fields: `username`, `user_id`, `full_name`,
`is_private`, `is_verified`, `is_business`, `profile_pic_url`. All
optional — `None` means "engine could not provide". Note that
`follower_count` per follower is **not** in the bulk endpoint (IG
doesn't ship it); call `get_total_count(username, ...)` per entry if
needed.

When the cascade falls back to Selenium/Playwright (DOM scraping has
no metadata), each entry becomes a `ProfileSummary(username=...)` with
everything else `None`. Pipelines that need `user_id` should include
`"httpx"` in their `engines=[...]`.

### `get_followers_with_metrics(...)` — wrap result in `ExtractionResult`

Telemetry alongside the data — pipeline writes `result.to_dict()` to
BigQuery's audit JSON column without parsing log files.

```python
result = ext.get_followers_with_metrics("target_profile")

result.profiles            # List[str] (or List[ProfileSummary] when with_metadata=True)
result.collected_count     # 4587
result.expected_count      # 5000  (from IG header; None on failure)
result.coverage_pct        # 0.917
result.partial             # True if cross-engine partial fallback fired
result.engine_used         # 'selenium' | 'httpx' | 'playwright-chromium'
result.sessions_used       # ['bot_alpha']  (empty without SessionPool)
result.duration_seconds    # 312.4
result.rate_limit_hits     # 0
result.block_predictor_score  # 0.12  (None if predictor not wired)
result.started_at / finished_at  # UTC datetimes

bq_client.insert_rows_json(
    "Influ.extraction_audit",
    [{"queue_id": qid, **result.to_dict()}],
)
```

### `should_stop` — graceful early exit

`get_followers(should_stop=callable)` polls the callable between
batches; returning `True` stops the loop after the current batch
without raising. Useful when you only need N qualified candidates:

```python
qualified = []

def stop_when_enough() -> bool:
    return len(qualified) >= 100

# Pipeline pre-filters as it consumes the eventual return; or via
# on_batch hook elsewhere — should_stop is just the stop signal.
followers = ext.get_followers(
    "huge_target",
    should_stop=stop_when_enough,
    with_metadata=True,
)
```

Honored by Selenium (per-batch check inside scroll loop) and Httpx
(between paginated requests). Playwright respects it too. Granularity
is ~1 batch / ~1 round-trip.

### `use_stdlib_logging=True` — forward to stdlib logging

InstaT writes Loguru-formatted records to stderr and a rotating file
at `instat/logs/insta_extractor.log` by default. Inside a K8s pod
that file is ephemeral. Set the flag and records get forwarded to
`logging.getLogger("instat.*")` — Cloud Logging / journald / any
stdlib handler picks them up automatically:

```python
ext = InstaExtractor(user, pw, use_stdlib_logging=True)
```

Or via the module function (call once at process start, before any
`InstaExtractor()` is constructed if you want global effect):

```python
from instat import configure_logging
configure_logging(use_stdlib=True)
```

`configure_logging(use_stdlib=False, file_log=False)` is also valid —
keeps the colored stderr sink but drops the disk file (useful in
serverless / container environments without persistent FS).

---

## Post metrics (engagement / TEP)

`get_recent_posts(profile_id, limit=N)` returns the last N posts of a
profile with the engagement fields needed to compute metrics like TEP
(Taxa de Engajamento por Post = `(likes + comments) / followers × 100`).

**InstaT does extraction only — it does not compute TEP itself.** The
consumer pipeline owns analytics decisions.

```python
from instat import InstaExtractor

ext = InstaExtractor(
    user, pw,
    engines=["selenium", "httpx"],   # httpx is required for post fetch
)

profile = ext.get_profile("target_profile")
posts = ext.get_recent_posts("target_profile", limit=5)

# TEP per post
for post in posts:
    if post.likes_count is None or post.comments_count is None:
        continue   # IG hides counts on some Reels — skip cleanly
    tep = (post.likes_count + post.comments_count) / profile.followers_count * 100
    print(f"{post.shortcode}: TEP={tep:.2f}%  hashtags={post.hashtags}")

# Mean TEP (TME) across the sample
valid = [p for p in posts if p.likes_count is not None and p.comments_count is not None]
tme = sum((p.likes_count + p.comments_count) for p in valid) / (len(valid) * profile.followers_count) * 100
```

`PostMetrics` shape (importable from `instat`):

| Field | Type | Notes |
|---|---|---|
| `shortcode` | `str` | URL slug `instagram.com/p/<shortcode>/`. Stable. |
| `likes_count` | `int \| None` | None when IG hides (some Reels). |
| `comments_count` | `int \| None` | Same as above. |
| `timestamp` | `datetime \| None` | UTC. |
| `caption` | `str \| None` | Raw text. |
| `hashtags` | `list[str]` | Lowercased, deduplicated, first-occurrence order. |
| `media_type` | `'image' \| 'video' \| 'carousel' \| None` | |
| `media_url` | `str \| None` | First image (carousel cover or post). Useful for downstream CNN. |

**Engine support.** Only `HttpxEngine` implements `get_recent_posts`
today (via the private `/feed/user/{user_id}/` endpoint). Selenium and
Playwright raise `NotImplementedError` and the `EngineManager`
cascades silently to the next engine. Always include `httpx` in your
engines list when you need post metrics:

```python
ext = InstaExtractor(user, pw, engines=["selenium", "httpx"])
```

When called against a cascade where the primary is Selenium, the
manager performs an in-process cookie handoff so httpx inherits the
authenticated session — no separate login required.

---

## Stealth via undetected-chromedriver

InstaT's `SeleniumEngine` defaults to Firefox + GeckoDriver with
`playwright-stealth`-style tweaks (just the `navigator.webdriver` flag
removal). That covers the basics but IG's bot detection has since
moved on — it probes plugin lists, languages array shape, the
`chrome.runtime` namespace, permissions API quirks, etc.

`stealth_mode='undetected_chrome'` swaps the local browser to Chrome
via [`undetected-chromedriver`](https://github.com/ultrafunkamsterdam/undetected-chromedriver),
which patches all of those vectors at the chromedriver level. Same
InstaT API, just an opt-in flag.

```python
from instat import InstaExtractor
from instat.engines.selenium_engine import SeleniumEngine

uc_engine = SeleniumEngine(
    headless=True, timeout=20,
    stealth_mode='undetected_chrome',   # default is 'firefox'
)

ext = InstaExtractor(
    user, pw,
    engines=[uc_engine, 'httpx'],
)
```

Or via the simpler path (when you don't need to combine with other
engines yet, ride the default `engines=['selenium']` but pass through):

```python
# Pattern for the future once we surface stealth_mode on InstaExtractor;
# for now, build the SeleniumEngine explicitly as above.
```

Requirements:

- `pip install instat[stealth]` — adds the optional
  `undetected-chromedriver>=3.5` dep.
- **Chrome installed locally.** undetected-chromedriver downloads a
  matching chromedriver on first launch and points at your local
  Chrome binary; we don't ship the browser.
- Linux containers: install `google-chrome-stable` from Google's apt
  repo before running.

When to prefer `undetected_chrome`:

- Account is fresh / barely-warmed and IG block-rate is high.
- You're already paying for residential / mobile proxies and want to
  squeeze maximum sessions per account.
- Selenium scroll loop hits modal-stale or 429 frequently and
  swapping the proxy didn't fix it.

When to stick with Firefox (default):

- You don't want a Chrome dependency.
- Your account pool is well-warmed and Firefox works fine.
- You're running inside a container where adding Chrome inflates
  image size significantly.

Note: `webdriver_factory` (for remote browsers) takes precedence —
if both `webdriver_factory` and `stealth_mode='undetected_chrome'`
are set, the factory wins and stealth_mode is ignored.

---

## Bright Data Scraping Browser (PUBLIC content only)

> **Policy update (2026-05-15, per BD support guidance):** Bright Data
> explicitly forbids automated Instagram logins. The `scraping_browser1`
> zone is allowed to scrape Instagram, **but only public content
> accessible in incognito** (public profiles, hashtag pages, the
> `/explore/` tree, public post URLs). Automated logins, private
> profiles, stories, DMs, anything behind auth — **violates BD policy**
> and risks zone suspension.

This means BD Scraping Browser is **not a drop-in fallback for the
InstaT logged-in cascade** (which is how `get_followers` /
`get_following` work — they require an authenticated session). For
logged extraction stay on **local Selenium + Residential Proxies**
(see `proxies=[...]` kwarg); BD Residential is sold separately and
has no per-domain compliance gating.

The remaining valid use case for BD Scraping Browser:
- Scraping public profile metadata at scale (analogous to
  `get_profile()` but via DOM scrape, no login)
- Hashtag / explore page enrichment
- Public post URLs

```python
from instat import (
    InstaExtractor,
    brightdata_playwright_engine,
)

# auth_mode='header' (default, BD-recommended) — credentials go in
# Authorization: Basic <b64> header, not in the WebSocket URL.
brd = brightdata_playwright_engine(
    customer_id="hl_xxxxxxxx",
    zone="scraping_browser1",
    password="<zone-password>",
    # auth_mode="url"     # legacy: embedded creds in URL, still supported
)

# Plug into engines list for PUBLIC scraping only. Avoid pairing with
# the logged-in cascade — running .get_followers() through this engine
# would attempt to fetch followers without auth and fail.
ext = InstaExtractor(
    user, pw,
    engines=[brd],   # public scrape only — no get_followers/get_following
)
```

For Selenium-style remote (BD's WebDriver port `:9515` instead of
WebSocket CDP), use the symmetric helper:

```python
from instat import brightdata_selenium_engine

brd_sel = brightdata_selenium_engine(
    customer_id="hl_xxxxxxxx",
    zone="scraping_browser1",
    password="<zone-password>",
)
```

This builds a `SeleniumEngine` with `webdriver.Remote` pointing at BD,
**`pageLoadStrategy='eager'`** baked in (BD's full Chrome desktop hangs
indefinitely with the default `'normal'` strategy on Instagram —
discovered in InstaT live testing 2026-05-14).

### Cost & compliance considerations

- **No automated logins** — explicit BD policy (per their 2026-05-15
  support email). Violating this can get your zone suspended.
- Only public content (incognito-visible). Private profiles, stories,
  DMs, and any behind-auth content are off-limits.
- Pricing is bandwidth-based ($8/GB at typical 2026 rates). Each
  follower extraction sends ~500KB-2MB through the remote browser.
  Reserve BD for the fallback role; primary load belongs on local
  Selenium + cheaper residential proxies routed through `proxies=[...]`.
- The HttpxEngine path (`engines=[..., "httpx"]`) does **not** go
  through BD — it uses cookie handoff from the active Selenium driver
  and talks directly to IG's private API. Free of BD bandwidth cost.

---

## Remote browser providers (advanced, custom config)

When the helpers above aren't enough — different provider, custom
endpoint, or special connect options — `engines=[...]` accepts any
`BaseEngine` instance you build directly.

### PlaywrightEngine via CDP (Bright Data / Browserless cloud)

```python
from instat import InstaExtractor
from instat.engines.playwright_engine import PlaywrightEngine

# Bright Data Scraping Browser
brd = PlaywrightEngine(
    connect_endpoint=(
        "wss://brd-customer-<ID>-zone-<ZONE>:<PASSWORD>"
        "@brd.superproxy.io:9222"
    ),
    connect_mode="cdp",   # default
)

# Browserless cloud
bl = PlaywrightEngine(
    connect_endpoint="wss://chrome.browserless.io?token=<TOKEN>",
    connect_mode="cdp",
)

ext = InstaExtractor(
    "user", "pass",
    engines=["selenium", brd],   # mistura local + remoto na cascata
)
```

`connect_endpoint` ativa o ramo remoto: o engine usa
`chromium.connect_over_cdp(endpoint)` em vez de `launch()`. `headless`
é controlado pelo provider — o kwarg local fica ignorado. Use
`connect_mode="ws"` (em vez de `"cdp"`) pra falar com um Playwright
Server (`chromium.connect(...)`).

`browser_type` precisa ser `"chromium"` quando há endpoint — Bright
Data e Browserless são chromium-only.

### SeleniumEngine via webdriver.Remote

`SeleniumEngine` aceita um `webdriver_factory: Callable[[bool],
WebDriver]`. O factory recebe o flag `headless` e devolve um driver
pronto — pode ser `webdriver.Remote` apontando pra qualquer Selenium
Grid / Bright Data / Browserless / self-hosted.

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from instat import InstaExtractor
from instat.engines.selenium_engine import SeleniumEngine

def browserless_factory(headless: bool):
    opts = Options()
    opts.set_capability("browserless:token", "<TOKEN>")
    return webdriver.Remote(
        command_executor="https://chrome.browserless.io/webdriver",
        options=opts,
    )

remote_selenium = SeleniumEngine(
    headless=True, timeout=20,
    webdriver_factory=browserless_factory,
)

ext = InstaExtractor(
    "user", "pass",
    engines=[remote_selenium, "httpx"],
)
```

InstaLogin pula todo o setup local de Firefox/GeckoDriver e usa o
driver devolvido pelo factory. O fluxo de login (form fill, challenge
resolve, block detection) e a extração rodam normalmente contra a
sessão remota.

**Notas operacionais**

- `quit()` chama `driver.quit()` no driver injetado — libera a sessão
  remota e a quota associada. Se você quer manter o driver vivo após
  a extração, devolva no factory um wrapper cujo `.quit()` seja no-op.
- Cookies do `SessionCache` continuam funcionando: o login persiste e
  o próximo run pode usar cookies cached mesmo trocando entre local e
  remoto, desde que o `username` seja o mesmo.
- `get_*_with_rotation` e `get_*_persistent` reconstroem extractors
  internamente; engines passadas como instância NÃO são reusadas
  nesses caminhos (a config remota fica opaca pra clonagem). Use
  strings (`engines=["selenium"]`) se precisar de rotação automática
  com nova conta.

---

## External API fallback (`HttpxEngine`)

By design, external Instagram API calls are only used when the browser-based cascade cannot complete. Two cookie paths keep this reliable:

1. **Disk (`SessionCache`)**: written by Selenium on login, read by httpx on the next run.
2. **In-process handoff (`login_with_cookies`)**: inside `get_both` and `_parallel`, cookies from the active Selenium driver are injected directly into the httpx client — no form-login HTTP call, bypassing the 403 that IG returns to fresh httpx sessions from burned IPs.

Fallback triggers:
- Inside `get_both`: when the httpx worker succeeds with cookies, it completes `following` in ~60–90 s for 2 000 profiles.
- Inside `get_*_parallel`: when Selenium coverage < 60% of target count, httpx is attempted to fill the gap.

---

## CLI

Installed as `instat` via `pyproject.toml` entry point.

```bash
instat extract --profile TARGET --type followers --output out.csv
instat extract --profile TARGET --type following \
               --engine selenium --engine httpx \
               --proxy-file proxies.txt \
               --max-duration 300 \
               --output out.db
instat count --profile TARGET --type followers
```

Output format inferred from file extension: `.csv` / `.json` / `.db` / `.sqlite`.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Bad input (CLI args, profile not found) |
| 2 | Auth error |
| 3 | Extraction blocked (all engines) |
| 4 | Unexpected error |

---

## Docker

```bash
docker build -t instat .

docker run --rm \
  -e INSTAT_USERNAME -e INSTAT_PASSWORD \
  -v $(pwd)/output:/app/output \
  instat extract --profile target --type followers \
                 --output /app/output/followers.csv
```

Or via compose:

```bash
docker compose run --rm instat extract --profile target --type followers \
                                       --output /app/output/followers.csv
```

The image uses headless Firefox and bakes in all optional dependencies.

---

## Exception handling

```python
from instat import (
    InstaExtractor,
    LoginError, ProfileNotFoundError,
    RateLimitError, AccountBlockedError, AllEnginesBlockedError,
)

try:
    ext = InstaExtractor(user, pw)
    followers = ext.get_followers("target")
except LoginError:
    # credentials wrong or login form couldn't load
    ...
except ProfileNotFoundError:
    # target profile doesn't exist or is private
    ...
except AccountBlockedError as e:
    # checkpoint / 2FA / Meta interstitial
    print(f"Blocked: {e.reason}")
    print(f"See {e.screenshot_path} and {e.html_path}")
except AllEnginesBlockedError:
    # every engine in the cascade failed and zero profiles collected
    ...
finally:
    ext.quit()
```

Partial results are **returned, not raised**: if engines 1 and 2 failed but engine 3 collected 500 profiles, you get a list of 500.
