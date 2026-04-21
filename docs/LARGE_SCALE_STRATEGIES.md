# Strategies for Large Targets (>50k followers)

Instagram enforces a practical per-account quota of ~500-2000 profiles
per fresh session before shadow-rate-limiting the `(account, target)`
pair for 6-24h. Everything below the app layer (cascade, warmup,
proxies, cookie handoff) only smooths the bump — it doesn't remove
the wall. For targets with tens or hundreds of thousands of followers
you need a different shape of approach.

This document surveys eight strategies, ranked by implementation cost
vs. practical reach. Already-implemented items link to the code.

---

## Quick decision matrix

| Need | Recommendation |
|---|---|
| "I want all 850k in one afternoon" | Impossible via scraping. Third-party data provider (#7) or Graph API (#8 if applicable). |
| "I want to build a dataset over time on my schedule" | Persistent accrual (#1 — **implemented**). |
| "I only need statistical analysis, not the full list" | Sampling + rotation (#5). |
| "I want ~10k once and move on" | Multi-account rotation (#2 — **implemented**) + persistent store for the run. |
| "I want to track daily follower churn on a profile I manage" | Graph API (#8) + diff (#6). |

---

## 1. Multi-run persistent accrual ⭐ implemented

**`get_followers_persistent(profile_id, store_path=...)`** —
schedules itself via cron/CI. Each call takes whatever it can (one
account's quota), writes delta to SQLite (no expiry), returns a
summary including `target_coverage_pct`.

**Math (1 account, natural rate-limit):**
- ~1500 new profiles/day after recovery from rate-limit (rough)
- 850k target → ~550 days with 1 account
- 850k / 10 accounts rotated → ~55 days
- 850k / 50 accounts rotated → ~11 days

**When to use**: you control the clock, uptime of a CI/scheduler is
acceptable, and the target is valuable enough to justify weeks of
accrual. Combines cleanly with #2.

**Cost**: zero extra beyond 1+ IG accounts + a scheduler.

---

## 2. Multi-account sequential rotation ⭐ implemented

**`get_followers_with_rotation(profile_id, fallback_accounts=[...])`**
— primary account runs first, then each fallback account in
sequence. Each rotation = fresh login + new driver + new session.
Optional `fallback_proxies` paired positionally.

**Math**: N-account pool yields approximately N × (single-account
quota). Not quite linear because each fresh login has ~15s
overhead and triggers a fresh IG challenge ~20% of the time.

**When to use**: single-run job, 5-50 accounts available, target
small enough to fit in one wall-clock window (~10 minutes
per account).

**Cost**: N IG accounts. Each should be aged ≥2 weeks and
ideally used for normal feed/story activity during that time.
Cold-new accounts take a 10-50x quota hit.

---

## 3. Parallel workers with account pool ⭐ already in InstaT

**`get_followers_parallel(profile_id, workers=N, accounts=[...])`**
— N browsers simultaneously against the same modal, one account
per worker, coordinator dedupes and stops when `stop_threshold`
of total is reached.

**Math**: on a clean IP, 2-3 workers give 1.5-2x wall-clock speedup
over single. Above 3 workers IG flags "parallel same target",
diminishing returns.

**When to use**: target has >1000 followers, you need results in
one shot, and you can afford N browsers simultaneously.

**Cost**: machine resources (RAM for N Firefoxes), N-1 extra
accounts beyond the primary. See
[`docs/USAGE.md#parallel-extraction`](USAGE.md#parallel-extraction).

**Limitation for large targets**: even 5 workers × 5 accounts caps
at ~5-10k profiles in one run because each worker hits its own
quota. For 850k you'd need to combine with #1.

---

## 4. XHR/GraphQL interception (not implemented)

Instead of scrolling the modal and reading DOM nodes, intercept
the underlying GraphQL requests Instagram makes for pagination.
Selenium Wire or Playwright's request interception can capture
the raw JSON responses which contain per-page `{username, id,
full_name, is_verified}` — no DOM scraping, no `span._ap3a`
selector to break on UI redesigns, and no IPC roundtrips per
profile.

**Prior art in repo**: `tests/test_xhr_interception.py` exists
as a prototype — not wired into the extraction loop.

**Expected gain**: 5-10x speed per iteration (no scroll pauses,
no stale element, no `batch_read_text`). Reduced rate-limit
signal because the browser looks more human (the page loads in
chunks without the user scrolling).

**Risk**: GraphQL endpoint shape changes silently and often.
Would need a second layer of graceful fallback — if interception
misses, fall back to DOM scroll.

**Effort**: medium (4-6 hours of implementation + a test harness
that can mock IG's GraphQL responses).

**Recommend**: only if DOM-based extraction becomes unreliable;
right now it works and is easier to maintain.

---

## 5. Sampling instead of full extraction (not implemented)

For analytics workloads you rarely need every follower. A random
sample of 5000 from 850k gives margin of error ±1.4% at 95%
confidence for any proportion. IG serves followers in a deterministic
order (roughly recency-weighted); you can approximate random
sampling by:

- Scrolling for 30s then taking a mid-batch slice
- Rotating which accounts scroll (each sees a different ordering
  when mutual-friend edges are considered)

**Expected gain**: 1000x faster for "is this influencer audience
real" type questions. Full union never reached, but the statistics
are sound.

**Effort**: small (~100 lines). Could add `sample_size=5000` kwarg
that exits the scroll when `len(unique) >= sample_size` AND the
modal has loaded at least a few pages.

**Trade-off**: useless for "export my audience" use cases where
you need every handle.

---

## 6. Delta tracking (partially covered by #1)

For targets you want to monitor over time (churn, growth, unfollows),
run daily and record diffs: `new_today = today_union - yesterday_union`
and `churned_today = yesterday_union - today_union`. The current
`PersistentStore` tracks `first_seen_at` and `last_seen_at` but
doesn't prune rows that disappear from the live list.

**Proposed extension**: add a `mark_seen_now(...)` method and a
`get_inactive(profile_id, threshold_days)` query to identify
usernames that haven't been observed recently — i.e. likely
unfollowed.

**When to use**: audience retention analytics, bot-cleanup
detection, public-figure monitoring.

**Effort**: small — extend `PersistentStore` with 2 methods.

---

## 7. Third-party data providers (not applicable to InstaT itself)

Services like **Modash.io**, **HypeAuditor**, **Kolsquare**,
**InfluencerDB** maintain cached follower snapshots on tens of
thousands of public profiles. They handle the scaling problem at
their infrastructure level with dedicated account farms and deep
pockets.

**When to use**: commercial use cases where $500-$5000/month for
on-demand access beats building an extraction pipeline. You get
deeper data too (fake-follower detection, audience demographics,
engagement rates, brand affinity).

**Trade-off**: vendor lock-in, no ability to scrape niche
profiles they don't cover.

**For InstaT project**: out of scope — InstaT's whole point is
self-hosted extraction. Listed here only so users have the
option on the table.

---

## 8. Instagram Graph API — the one legitimate way (not implemented)

If the target account is one you own or manage (creator/business
account), Meta's Graph API provides a legitimate
`GET /me/followers` endpoint with pagination, stable schema, and
no rate-limiting beyond normal app quotas.

**Requirements**:
- Business/Creator account (free conversion)
- Facebook Developer app + Meta review (simple for read-only)
- OAuth user token

**When to use**: monitoring your own or your client's accounts.
This is the only strategy that's not a ToS gray area.

**Effort to integrate into InstaT**: medium. A new
`GraphApiEngine(BaseEngine)` that accepts an access token and
handles pagination. Would fit cleanly into the existing cascade
— `engines=['graph', 'selenium']` — with Graph as the primary
for accounts where you have a token.

**Recommend**: worth adding if you have even one user managing
their own accounts; also opens the door to richer metadata
(follower timestamps, demographic aggregates, messaging stats).

---

## 9. Bonus — adaptive scroll pace (small win)

Current `SeleniumEngine` uses fixed `pause_time=0.5s` between
scrolls. IG tolerance varies by account age and target
popularity. An adaptive loop that:

- Starts at 0.3s between scrolls
- Doubles pause_time each stale round
- Halves it (floor 0.3s) each round that returns >10 new

…would typically extract 20-40% more before rate-limit. Already
partially related to the `warmup_stale_rounds` work but not yet
dynamic mid-extraction.

**Effort**: small. Could be a property on `SeleniumEngine` toggled
by kwarg. Risk: harder to tune across account types.

---

## Summary — what to actually build next

**In order of value-per-effort for someone hitting the 850k wall:**

1. **#1 + #2 together** — multi-account rotation invoked from
   a persistent accrual wrapper. Already shipped; users just
   need to combine them. Document in `docs/USAGE.md`.
2. **#6 delta tracking** — small extension, opens up the most
   common analytics use case (growth/churn monitoring).
3. **#4 XHR interception** — only when DOM scrape starts
   breaking. Not yet.
4. **#8 Graph API engine** — attract enterprise users with
   legitimate use cases; clean separation from the scraping
   engines.

Everything else is either already covered by existing code,
out of scope for this project, or a last-resort fallback.
