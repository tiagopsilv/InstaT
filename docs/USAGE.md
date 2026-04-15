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
