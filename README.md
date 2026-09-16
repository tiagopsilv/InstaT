# InstaT — Instagram Data Extractor

[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-590%20passing-brightgreen.svg)](tests/)

Production-grade Python library for Instagram follower/following extraction, built on a **multi-engine architecture** with automatic fallback, session persistence, and parallel orchestration.

> **Scope & ethics.** InstaT is intended for research, analytics, and workflows on accounts you own or are authorized to inspect. Automated scraping of Instagram violates the platform's Terms of Service at scale — the user bears responsibility for lawful use, LGPD/GDPR compliance, and respecting rate limits. See [Responsible Use](#responsible-use).

---

## Table of Contents

- [Highlights](#highlights)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Extraction Modes](#extraction-modes)
- [Documentation](#documentation)
- [CLI & Docker](#cli--docker)
- [Development](#development)
- [Responsible Use](#responsible-use)
- [License](#license)

---

## Highlights

| Capability | What it does |
|---|---|
| **Multi-engine** | Selenium (primary) → Playwright → httpx, orchestrated by `EngineManager` with automatic cascade on failure |
| **Remote browsers** | Plug Bright Data Scraping Browser / Browserless / Selenium Grid via `engines=[...]` dependency injection — no extraction-logic changes |
| **Stealth modes** | Firefox + GeckoDriver (default) or `stealth_mode='undetected_chrome'` via `undetected-chromedriver` for stronger bot-signature evasion |
| **Parallel extraction** | Run N browsers concurrently on the same target; shared set + stop-on-target coordinator |
| **Account & proxy rotation** | `SessionPool` and `ProxyPool` with cooldown backoff on rate-limit/block |
| **Challenge auto-resolve** | IMAP-driven `EmailChallengeResolver` + URL-driven `BloksCodeEntryResolver` handle IG's multi-step verification flows via an iterative chain |
| **Resilience** | Incremental checkpoints, session-cookie cache, smart exponential backoff, stall recovery via modal reopen |
| **Post metrics** | `get_recent_posts()` returns `PostMetrics` (likes/comments/timestamp/caption/hashtags) — feeds engagement formulas like TEP without extra scraping |
| **Rich metadata** | `get_followers(with_metadata=True)` returns `ProfileSummary` with `user_id`, `full_name`, `is_verified` etc. — stable keys for SNA / graph analysis |
| **Audit telemetry** | `get_followers_with_metrics()` returns `ExtractionResult` with `engine_used`, `coverage_pct`, `duration_seconds`, `rate_limit_hits` — ready for BigQuery audit logs |
| **Observability** | Structured Loguru logs (or stdlib via `use_stdlib_logging=True`), `DiagnosticCollector` bundles (screenshot + HTML + cookies + metadata) on every failure |
| **Export** | CSV, JSON, SQLite out of the box; pluggable `BaseExporter` for custom sinks |
| **Async** | `AsyncInstaExtractor` wraps the sync API for concurrent `profile_id` pipelines |
| **CLI & Docker** | `instat` binary, pre-built Dockerfile + compose manifest |
| **Typed & tested** | 590 unit tests, ruff + mypy in CI |

---

## Installation

```bash
pip install instat                                  # core (Selenium / Firefox only)
pip install 'instat[playwright]'                    # + Playwright engine
pip install 'instat[httpx]'                         # + httpx fallback engine (recommended — post metrics + cookie handoff)
pip install 'instat[stealth]'                       # + undetected-chromedriver (stealth_mode='undetected_chrome')
pip install 'instat[playwright,httpx,stealth,dev]'  # everything
```

**Requirements:** Python ≥ 3.12, Firefox (GeckoDriver auto-installed via `webdriver-manager`).

For Playwright: `playwright install chromium` after pip-install.

---

## Quick Start

```python
from instat import InstaExtractor

ext = InstaExtractor(
    username="your_user",
    password="your_pass",
    headless=True,
    engines=["selenium", "httpx"],  # cascade: Selenium → httpx on fail
)

target = ext.get_profile("target_profile")   # 1 page load → cheap metadata
print(f"@{target.username} · {target.full_name} · "
      f"{target.followers_count} followers · {target.posts_count} posts")

followers = target.get_followers()
following = target.get_following()
print(f"Fetched: {len(followers)} followers | {len(following)} following")

ext.quit()
```

`get_profile()` returns a `Profile` with cheap header attributes
(`full_name`, `followers_count`, `following_count`, `posts_count`,
`is_private`, `is_verified`, `profile_pic_url`, `url`) and two methods
(`get_followers()`, `get_following()`) bound to the extractor.

**Credentials via environment (recommended):**

```bash
export INSTAT_USERNAME='your_user'
export INSTAT_PASSWORD='your_pass'
```

```python
import os
ext = InstaExtractor(os.environ["INSTAT_USERNAME"], os.environ["INSTAT_PASSWORD"])
```

---

## Extraction Modes

### 1. Sequential (simplest)

```python
ext.get_followers("target")
ext.get_following("target")
```

### 2. Concurrent followers + following (`get_both`)

Runs the two lists in parallel. Followers via the primary Selenium driver; following via `HttpxEngine` reusing Selenium cookies (external API **as fallback only**, per design). Falls back to a second Selenium session, then to sequential.

```python
result = ext.get_both("target")
# {"followers": [...], "following": [...]}
```

### 3. Parallel on a single list (`get_followers_parallel`)

N workers, each with its own browser (and optionally its own Instagram account), scroll the same modal. A shared coordinator unions results and signals all workers to stop once `stop_threshold × total_count` is reached.

```python
followers = ext.get_followers_parallel(
    "target",
    workers=3,
    accounts=[                              # 1 account per worker (recommended)
        {"username": "alt1", "password": "..."},
        {"username": "alt2", "password": "..."},
        {"username": "alt3", "password": "..."},
    ],
    stop_threshold=0.98,                    # stop when 98% of target reached
    max_duration=300,                       # hard cap per worker (seconds)
)
```

> **Risk note.** Running multiple workers under the **same account** triggers a warning in the log — Instagram may block simultaneous sessions. Prefer one account per worker.

### 4. Async pipelines

```python
import asyncio
from instat import AsyncInstaExtractor

async def pull(profiles):
    async with AsyncInstaExtractor(user, pw) as ext:
        return await asyncio.gather(*(ext.get_followers(p) for p in profiles))

asyncio.run(pull(["a", "b", "c"]))
```

### 5. Export in one call

```python
ext.to_csv("target",    "followers", "out/followers.csv")
ext.to_json("target",   "following", "out/following.json", indent=2)
ext.to_sqlite("target", "followers", "out/db.sqlite", table="profiles")
```

Or inject a custom `BaseExporter` at construction time to pipe every extraction into your sink (S3, Kafka, data warehouse, etc.).

---

## Documentation

| Doc | Topic |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Multi-engine cascade, `EngineManager`, `ParallelCoordinator`, session/proxy pools, checkpoint flow |
| [`docs/USAGE.md`](docs/USAGE.md) | Complete API reference with examples for every entry point |
| [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) | Login issues, block types, selector updates, performance tuning |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |

---

## CLI & Docker

```bash
# CLI
export INSTAT_USERNAME='user' INSTAT_PASSWORD='pass'
instat extract --profile target --type followers --output out.csv
instat extract --profile target --type following --engine selenium --engine httpx \
               --proxy-file proxies.txt --max-duration 300 --output out.db
instat count   --profile target --type followers

# Docker
docker compose run --rm instat extract --profile target --type followers \
                                       --output /app/output/followers.csv
```

Exit codes: `0` success · `1` bad input · `2` auth error · `3` extraction blocked · `4` unexpected.

---

## Development

```bash
git clone https://github.com/tiagopsilv/InstaT.git
cd InstaT
pip install -e '.[playwright,httpx,dev]'
playwright install chromium

python -m pytest tests/ -m "not e2e"      # 243 unit tests (~90 s)
ruff check .                               # lint
mypy InstaT                                # types
```

Contributions welcome via PR — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for design conventions.

---

## Responsible Use

- **Compliance:** LGPD (Brazil) and GDPR (EU) classify follower/following data as personal data. Have a legal basis (consent, legitimate interest, contract) before processing.
- **Instagram TOS:** Automated collection violates the platform's terms. Use only on owned accounts, authorized audits, or public research with proper ethics review.
- **Rate limiting:** Respect cooldowns. Aggressive scraping will get accounts banned and may trigger legal action.
- **No warranty:** This library is distributed as-is under MIT. You are solely responsible for your usage.

---

## License

MIT — see [`LICENSE`](LICENSE).

## Author

**Tiago Pereira da Silva** — Data & Software Engineer.
[Email](mailto:tiagosilv@gmail.com) · [LinkedIn](https://www.linkedin.com/in/tiagopsilvatec) · [GitHub](https://github.com/tiagopsilv)
