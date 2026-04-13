# InstaT - Intelligent Instagram Data Extractor

**InstaT** is a Python library (`pip install instat`) for automated Instagram data extraction. It logs into Instagram, navigates to any public profile, and extracts follower/following lists via dynamic scrolling with Selenium.

Built for reliability: humanized timing, exponential backoff, incremental checkpoints, session caching, resilient selectors, and account-block detection.

---

## Features

- **Automated Login** with retry logic, fallback button detection, and cookie session cache
- **Mobile Emulation** (Nexus 5 UA, 375x667) to reduce detection
- **Humanized Timing** via Gaussian delays (no fixed sleep patterns)
- **Smart Backoff** with exponential + jitter on stalled scrolling
- **Incremental Checkpoints** - crash at profile 4500/5000? Resume from checkpoint
- **Session Cache** - reuses cookies to avoid re-login (biggest ban trigger)
- **Resilient Selectors** - each selector has fallback alternatives in JSON
- **Account Block Detection** - detects checkpoint, 2FA, Meta interstitial, and logs actionable instructions
- **External Selector Config** - update `selectors.json` when Instagram changes UI, no code changes needed
- **38 Unit Tests** covering all modules

---

## Installation

```bash
git clone https://github.com/tiagopsilv/InstaT.git
cd InstaT
pip install -e .
```

Requires **Python 3.8+** and **Firefox** (GeckoDriver is managed automatically).

---

## Quick Start

```python
from instat import InstaExtractor

extractor = InstaExtractor(
    username="your_username",
    password="your_password",
    headless=True,
    timeout=15
)

followers = extractor.get_followers("target_profile", max_duration=120.0)
following = extractor.get_following("target_profile", max_duration=120.0)
count = extractor.get_total_count("target_profile", list_type="followers")

print(f"Followers: {len(followers)}")
print(f"Following: {len(following)}")
print(f"Total count: {count}")

extractor.quit()
```

---

## Exception Handling

```python
from instat import InstaExtractor, LoginError, ProfileNotFoundError, AccountBlockedError

try:
    extractor = InstaExtractor(username="user", password="pass")
    followers = extractor.get_followers("target")
except LoginError:
    print("Login failed. Check credentials.")
except AccountBlockedError as e:
    print(f"Account blocked: {e.reason}")
    print(f"Action: see screenshot at {e.screenshot_path}")
except ProfileNotFoundError:
    print("Profile not found or private.")
```

---

## Architecture

```
InstaT/
├── __init__.py            # Package exports
├── constants.py           # Centralized timing constants + human_delay()
├── backoff.py             # SmartBackoff with exponential + jitter
├── checkpoint.py          # Incremental extraction checkpoints
├── session_cache.py       # Cookie session persistence
├── login.py               # InstaLogin - authentication + block detection
├── extractor.py           # InstaExtractor - scrolling + extraction
├── utils.py               # Selenium helpers (find_element_with_fallback, etc.)
├── exceptions.py          # LoginError, ProfileNotFoundError, AccountBlockedError
├── config/
│   ├── selector_loader.py # JSON loader with get_all() for fallback lists
│   └── selectors.json     # CSS/XPath selectors with alternatives
├── tests/
│   ├── test_constants.py
│   ├── test_backoff.py
│   ├── test_checkpoint.py
│   ├── test_session_cache.py
│   ├── test_selector_loader.py
│   ├── test_extractor.py
│   └── test_login.py
└── examples/
    └── example_usage.py
```

### Key Design Decisions

| Module | Purpose |
|--------|---------|
| `constants.py` | All timing values centralized. `human_delay(base, variance)` adds Gaussian noise to every sleep, preventing detectable patterns. |
| `backoff.py` | When scrolling stalls, delays escalate: 2s, 4s, 8s... up to 5min. Jitter via `uniform(0.5, 1.5)` prevents regularity. Resets on success. |
| `checkpoint.py` | Every 100 new profiles, progress is saved to `.instat_checkpoints/`. On crash/block, next run resumes automatically. Expires after 24h. |
| `session_cache.py` | After login, cookies are saved to `.instat_sessions/`. Next run skips the login form entirely. Expires after 1h. |
| `selector_loader.py` | Selectors can be a string or a list of alternatives. `get_all()` returns the list; `find_element_with_fallback()` tries each one. |
| `login.py` | Detects 7 types of account blocks (checkpoint, 2FA, Meta Verified, etc.), saves screenshot + HTML as evidence, raises `AccountBlockedError` with actionable instructions. Falls back to cached GeckoDriver if GitHub API rate-limits `webdriver-manager`. |

---

## Selectors Configuration

Selectors support **fallback alternatives** as JSON arrays. When Instagram changes its UI, update only `selectors.json`:

```json
{
    "FOLLOWERS_LINK": [
        "//a[contains(@href, '/followers/')]",
        "//a[.//span[contains(text(),'seguidores') or contains(text(),'followers')]]",
        "//a[contains(text(),'seguidores') or contains(text(),'followers')]"
    ],
    "LOGIN_USERNAME_INPUT": "input[name='username']"
}
```

Single strings are backward compatible. The `SelectorLoader.get()` returns the first alternative; `get_all()` returns all.

---

## Tuning Parameters

```python
extractor.pause_time = 0.3                # Seconds between scrolls (default: 0.5)
extractor.wait_interval = 0.3             # Wait for new profiles (default: 0.5)
extractor.max_attempts = 3                # Scroll attempts per cycle (default: 2)
extractor.additional_scroll_attempts = 2  # Extra scrolls on stall (default: 1)
extractor.max_refresh_attempts = 50       # Max page refreshes (default: 100)
extractor.max_retry_without_new_profiles = 5  # Retries before backoff (default: 3)
extractor.checkpoint_interval = 50        # Save every N profiles (default: 100)
```

---

## Running Tests

```bash
python -m pytest tests/ -v
```

All 38 tests use `unittest.TestCase` with `MagicMock` - no real browser needed.

---

## Common Issues

| Problem | Solution |
|---------|----------|
| Login fails with 2FA | Set `headless=False`, complete verification manually, then reuse cached session |
| `AccountBlockedError` raised | Check the screenshot in `instat/logs/artifacts/`, follow the logged instructions |
| Empty results | Increase `max_duration`, check if profile is private |
| Selectors outdated | Update `InstaT/config/selectors.json` with current Instagram markup |
| GeckoDriver rate limit | Set `GH_TOKEN` env var, or let the library use the cached geckodriver |
| Firefox not found | Install Firefox, or check the path |

---

## Changelog (v2.0)

| Task | Description |
|------|-------------|
| BL-01 | Centralized timing constants + Gaussian `human_delay()` replacing all `time.sleep()` |
| BL-02 | `SmartBackoff` with exponential delay + jitter for retry logic |
| BL-03 | `ExtractionCheckpoint` for incremental progress persistence |
| BL-04 | `SessionCache` for cookie-based session reuse |
| BL-05 | Modal scroll container detection with body fallback |
| BL-06 | Dead code cleanup: removed `.bak` files, unused imports, legacy methods |
| Fixes | Resilient selectors with fallback lists, JS click fallback, account block detection, "Save login" modal dismiss across navigations, `diagnose=False` to prevent credential leaks in logs |

---

## About the Developer

**Tiago Pereira da Silva** - Data Science & Analytics Specialist with 19+ years of experience.

- MBA Data Science - USP/Esalq | MBA Eng. Software - FIAP
- Expertise: Python, Selenium, Playwright, ETL, Machine Learning, Web Scraping

[tiagosilv@gmail.com](mailto:tiagosilv@gmail.com) | [LinkedIn](https://www.linkedin.com/in/tiagopsilvatec) | [GitHub](https://github.com/tiagopsilv)

---

## License

MIT License - see `LICENSE` file for terms.
