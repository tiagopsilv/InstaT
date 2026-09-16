"""ExtractionResult — bundle of profiles + per-run metrics.

Returned by `InstaExtractor.get_followers_with_metrics(...)` and the
`get_following` variant. Wraps the profiles list with operational
telemetry consumers (the `instagram-data-pipeline` project) want to
record alongside the data:

  - which engine actually served the result (cascade outcome)
  - which sessions/accounts were used (auditing)
  - how long it took (perf monitoring)
  - rate-limit / partial-coverage signals (quality gates)
  - block-predictor risk score at extraction end (early-warning)

Pipeline stores these fields in BigQuery audit columns
(profile_queue.metadata or a dedicated `extraction_audit` table) so
operators can query "which engine is failing today" / "which account
hit rate-limit the most this week" without parsing log files.

Scope: extraction-only. Same boundary rule as the other modules — no
analytics, no business decisions baked in.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional


@dataclass
class ExtractionResult:
    """Per-run extraction telemetry.

    Fields:
      profiles: the actual data (List[str] or List[ProfileSummary]
        depending on with_metadata mode of the call).
      profile_id: target the run extracted from (followers/following of).
      list_type: 'followers' or 'following'.
      expected_count: total count from the IG header (None if engine
        couldn't read it — common on block-state pages).
      collected_count: len(profiles).
      coverage_pct: collected / expected (None when expected unknown).
      partial: True if the result came from cross-engine partial
        preservation (some engine raised but earlier batches saved).
      engine_used: name of the engine that returned the final result
        ('selenium', 'httpx', 'playwright-chromium', ...).
      sessions_used: usernames whose session served calls during this
        run. Empty when no SessionPool is wired.
      duration_seconds: wall-clock perf_counter elapsed.
      rate_limit_hits: how many times any engine raised RateLimitError
        during the run.
      started_at / finished_at: UTC timestamps.
      block_predictor_score: snapshot of BlockPredictor.risk_score()
        at the end of the run (None if no predictor wired).
    """
    profiles: List[Any]
    profile_id: str
    list_type: str
    collected_count: int
    duration_seconds: float
    started_at: datetime
    finished_at: datetime
    expected_count: Optional[int] = None
    coverage_pct: Optional[float] = None
    partial: bool = False
    engine_used: Optional[str] = None
    sessions_used: List[str] = field(default_factory=list)
    rate_limit_hits: int = 0
    block_predictor_score: Optional[float] = None

    def to_dict(self) -> dict:
        """Serialise for storage in BigQuery JSON column or audit table.

        The `profiles` field is intentionally omitted — it's the bulk
        payload and lives in its own table. This dict is the audit
        sidecar.
        """
        return {
            'profile_id': self.profile_id,
            'list_type': self.list_type,
            'collected_count': self.collected_count,
            'expected_count': self.expected_count,
            'coverage_pct': self.coverage_pct,
            'partial': self.partial,
            'engine_used': self.engine_used,
            'sessions_used': list(self.sessions_used),
            'duration_seconds': round(self.duration_seconds, 3),
            'rate_limit_hits': self.rate_limit_hits,
            'started_at': self.started_at.isoformat(),
            'finished_at': self.finished_at.isoformat(),
            'block_predictor_score': self.block_predictor_score,
        }


__all__ = ['ExtractionResult']
