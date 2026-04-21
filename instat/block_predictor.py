"""BlockPredictor — passive telemetry for detecting IG pre-block patterns.

Phase 1 MVP: aggregates HTTP and scroll signals into a risk_score ∈ [0, 1].
Observational only — does NOT trigger any cooldown / action. The caller
reads `risk_score()` or `snapshot()` and decides. This is intentional:
we need 1-2 weeks of real-run data before picking a threshold.

Signals covered:
  1. error_rate        — fraction of recent HTTP responses with status >= 400
  2. latency_spike     — z-score of recent OK responses vs healthy baseline
  3. empty_response    — fraction of 200-OK responses smaller than an
                          anomaly threshold (IG returns tiny JSON when
                          soft-throttling)
  7. stale_severity    — mean(stale_count / max_stale) over recent rounds
  8. reopen_fail_rate  — fraction of modal reopen attempts that failed

Weights are fixed in Phase 1 and chosen by hand. A Phase 3 work would
train them via logistic regression against labelled block events.

Thread-safe via a single Lock — record_* may be called from multiple
engine threads (parallel workers).
"""
import statistics
import time
from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Any, Deque, Dict, Optional

# Anything smaller is suspicious for IG's user/followers responses.
# Real pages deliver ~8-15 KB of JSON per page.
EMPTY_RESPONSE_BYTES = 500

# Relative weights for risk aggregation. Higher = stronger pull toward
# "about to block". Chosen by hand; revisit after real-run telemetry.
_WEIGHTS: Dict[str, float] = {
    'error_rate': 2.5,
    'latency_spike': 1.5,
    'empty_response_rate': 2.0,
    'stale_severity': 1.0,
    'reopen_fail_rate': 2.0,
}


@dataclass
class RequestEvent:
    timestamp: float
    status_code: int
    latency_s: float
    response_size: Optional[int] = None
    engine: str = ""


@dataclass
class StaleEvent:
    timestamp: float
    stale_count: int
    max_stale: int
    reopen_failed: bool = False
    engine: str = ""


class BlockPredictor:
    """Rolling-window aggregator of pre-block signals.

    Construction kwargs are sizing knobs; defaults are reasonable for
    a single-extractor run. For parallel workers, a single shared
    instance is recommended — all engines emit into the same window.
    """

    def __init__(
        self,
        window_requests: int = 50,
        window_stale: int = 20,
        baseline_latency_samples: int = 20,
        empty_response_bytes: int = EMPTY_RESPONSE_BYTES,
    ) -> None:
        self._lock = Lock()
        self._req: Deque[RequestEvent] = deque(maxlen=window_requests)
        self._stale: Deque[StaleEvent] = deque(maxlen=window_stale)
        self._baseline_samples = baseline_latency_samples
        self._empty_threshold = empty_response_bytes
        self._latency_baseline: Optional[float] = None
        self._latency_stdev: Optional[float] = None

    # ------------------------- recording API -------------------------

    def record_request(
        self, status_code: int, latency_s: float,
        response_size: Optional[int] = None,
        engine: str = "",
    ) -> None:
        """Record one HTTP response. Called by engines after each request."""
        event = RequestEvent(
            timestamp=time.time(),
            status_code=status_code,
            latency_s=max(0.0, latency_s),
            response_size=response_size,
            engine=engine,
        )
        with self._lock:
            self._req.append(event)
            self._maybe_update_baseline()

    def record_stale(
        self, stale_count: int, max_stale: int,
        reopen_failed: bool = False,
        engine: str = "",
    ) -> None:
        """Record a stale-round event from the SeleniumEngine scroll loop."""
        with self._lock:
            self._stale.append(StaleEvent(
                timestamp=time.time(),
                stale_count=int(stale_count),
                max_stale=max(1, int(max_stale)),
                reopen_failed=bool(reopen_failed),
                engine=engine,
            ))

    # ------------------------- scoring API --------------------------

    def risk_score(self) -> float:
        """Return risk ∈ [0, 1]. Higher = closer to observed block."""
        with self._lock:
            signals = self._compute_signals_locked()
        if not signals:
            return 0.0
        weighted_sum = 0.0
        total_weight = 0.0
        for name, value in signals.items():
            w = _WEIGHTS.get(name, 1.0)
            weighted_sum += value * w
            total_weight += w
        if total_weight == 0:
            return 0.0
        return max(0.0, min(1.0, weighted_sum / total_weight))

    def snapshot(self) -> Dict[str, Any]:
        """Structured state suitable for logging / telemetry dashboards."""
        with self._lock:
            signals = self._compute_signals_locked()
            last_status = [e.status_code for e in list(self._req)[-10:]]
            recent_stale_ratio = [
                s.stale_count / max(1, s.max_stale)
                for s in list(self._stale)[-5:]
            ]
        return {
            'score': self._score_from_signals(signals),
            'signals': signals,
            'request_count': len(self._req),
            'stale_count': len(self._stale),
            'baseline_latency_s': self._latency_baseline,
            'baseline_latency_stdev_s': self._latency_stdev,
            'last_status_codes': last_status,
            'recent_stale_ratios': recent_stale_ratio,
        }

    def should_cooldown(self, threshold: float = 0.7) -> bool:
        """Convenience: True if current risk_score >= threshold.

        Phase 1 semantics: caller still decides what to DO with this
        (log, pause, rotate account). The predictor never mutates
        extraction state on its own.
        """
        if not 0 < threshold <= 1:
            raise ValueError(
                f"threshold must be in (0, 1], got {threshold}"
            )
        return self.risk_score() >= threshold

    # ----------------------- internal helpers -----------------------

    def _maybe_update_baseline(self) -> None:
        """Learn the healthy-latency baseline from the first N OK requests.
        Must be called while holding self._lock."""
        if self._latency_baseline is not None:
            return
        healthy = [e.latency_s for e in self._req if e.status_code == 200]
        if len(healthy) >= self._baseline_samples:
            self._latency_baseline = statistics.mean(healthy)
            self._latency_stdev = (
                statistics.stdev(healthy) if len(healthy) > 1 else 0.0
            )

    def _compute_signals_locked(self) -> Dict[str, float]:
        """Compute all available signals. Must be called while holding
        self._lock."""
        out: Dict[str, float] = {}

        if self._req:
            errors = sum(1 for e in self._req if e.status_code >= 400)
            out['error_rate'] = errors / len(self._req)

            size_samples = [
                e for e in self._req
                if e.response_size is not None and e.status_code == 200
            ]
            if size_samples:
                tiny = sum(
                    1 for e in size_samples
                    if (e.response_size or 0) < self._empty_threshold
                )
                out['empty_response_rate'] = tiny / len(size_samples)

        if (self._latency_baseline is not None
                and self._latency_stdev
                and self._latency_stdev > 0):
            recent_ok = [
                e.latency_s for e in list(self._req)[-10:]
                if e.status_code == 200
            ]
            if recent_ok:
                recent_mean = statistics.mean(recent_ok)
                z = (
                    recent_mean - self._latency_baseline
                ) / self._latency_stdev
                # Normalize: z=0 → 0, z=3 → 1, clipped.
                out['latency_spike'] = max(0.0, min(1.0, z / 3.0))

        if self._stale:
            recent = list(self._stale)[-5:]
            out['stale_severity'] = statistics.mean(
                s.stale_count / max(1, s.max_stale) for s in recent
            )
            failures = sum(1 for s in self._stale if s.reopen_failed)
            out['reopen_fail_rate'] = failures / len(self._stale)

        return out

    @staticmethod
    def _score_from_signals(signals: Dict[str, float]) -> float:
        if not signals:
            return 0.0
        weighted_sum = 0.0
        total_weight = 0.0
        for name, value in signals.items():
            w = _WEIGHTS.get(name, 1.0)
            weighted_sum += value * w
            total_weight += w
        if total_weight == 0:
            return 0.0
        return max(0.0, min(1.0, weighted_sum / total_weight))


__all__ = ['BlockPredictor', 'RequestEvent', 'StaleEvent']
