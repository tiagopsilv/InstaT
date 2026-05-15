"""ExtractionResult — bundle profiles + per-run telemetry."""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from instat.extraction_result import ExtractionResult


class TestExtractionResultDataclass(unittest.TestCase):

    def _result(self, **overrides):
        base = dict(
            profiles=['a', 'b', 'c'],
            profile_id='target',
            list_type='followers',
            collected_count=3,
            duration_seconds=12.345,
            started_at=datetime(2026, 5, 14, 12, tzinfo=timezone.utc),
            finished_at=datetime(2026, 5, 14, 12, 0, 12, tzinfo=timezone.utc),
        )
        base.update(overrides)
        return ExtractionResult(**base)

    def test_to_dict_excludes_profiles(self):
        r = self._result(
            expected_count=5, coverage_pct=0.6, partial=True,
            engine_used='httpx', sessions_used=['bot_a'],
            rate_limit_hits=2, block_predictor_score=0.34,
        )
        d = r.to_dict()
        self.assertNotIn('profiles', d)
        self.assertEqual(d['profile_id'], 'target')
        self.assertEqual(d['collected_count'], 3)
        self.assertEqual(d['expected_count'], 5)
        self.assertEqual(d['coverage_pct'], 0.6)
        self.assertTrue(d['partial'])
        self.assertEqual(d['engine_used'], 'httpx')
        self.assertEqual(d['sessions_used'], ['bot_a'])
        self.assertEqual(d['rate_limit_hits'], 2)
        self.assertEqual(d['block_predictor_score'], 0.34)
        self.assertEqual(d['duration_seconds'], 12.345)
        self.assertIn('started_at', d)
        self.assertIn('finished_at', d)


class TestEngineManagerExtractWithMetrics(unittest.TestCase):
    """Manager.extract_with_metrics produces ExtractionResult correctly."""

    def _setup_manager(self, profiles_returned, total_count=None,
                       engine_name='mock'):
        from instat.engines.engine_manager import EngineManager
        from instat.engines.base import BaseEngine

        class StubEngine(BaseEngine):
            @property
            def name(self_): return engine_name
            @property
            def is_available(self_): return True
            def login(self_, u, p, **kw): return True
            def extract(self_, *a, **kw):
                return profiles_returned
            def get_total_count(self_, *a, **kw): return total_count
            def quit(self_): pass

        eng = StubEngine()
        mgr = EngineManager([eng], default_credentials=('u', 'p'))
        mgr._logged_in_engines.add(id(eng))
        return mgr, eng

    def _unique_target(self):
        # Unique per-test profile_id to avoid leftover checkpoint state
        # in `.instat_checkpoints/` polluting count/coverage assertions.
        import uuid
        return f"er_test_{uuid.uuid4().hex[:8]}"

    def test_basic_metrics_captured(self):
        mgr, _ = self._setup_manager(
            profiles_returned={'a', 'b', 'c'},
            total_count=10,
            engine_name='httpx',
        )
        target = self._unique_target()
        result = mgr.extract_with_metrics(target, 'followers')
        self.assertEqual(result.collected_count, 3)
        self.assertEqual(result.expected_count, 10)
        self.assertEqual(result.engine_used, 'httpx')
        self.assertAlmostEqual(result.coverage_pct, 0.3)
        self.assertFalse(result.partial)
        self.assertEqual(result.rate_limit_hits, 0)
        self.assertGreater(result.duration_seconds, 0)
        self.assertEqual(result.profile_id, target)
        self.assertEqual(result.list_type, 'followers')

    def test_no_total_count_keeps_coverage_none(self):
        mgr, _ = self._setup_manager(
            profiles_returned={'a'},
            total_count=None,
            engine_name='selenium',
        )
        result = mgr.extract_with_metrics(self._unique_target(), 'followers')
        self.assertIsNone(result.expected_count)
        self.assertIsNone(result.coverage_pct)


if __name__ == '__main__':
    unittest.main()
