"""BlockPredictor Phase 1 — signal aggregation + hook integration."""
import time
import unittest
from threading import Thread
from unittest.mock import MagicMock, patch

from instat.block_predictor import BlockPredictor


class TestEmptyPredictor(unittest.TestCase):

    def test_zero_score_on_empty(self):
        p = BlockPredictor()
        self.assertEqual(p.risk_score(), 0.0)
        snap = p.snapshot()
        self.assertEqual(snap['score'], 0.0)
        self.assertEqual(snap['request_count'], 0)
        self.assertEqual(snap['stale_count'], 0)
        self.assertIsNone(snap['baseline_latency_s'])


class TestErrorRateSignal(unittest.TestCase):

    def test_error_rate_rises_with_429s(self):
        p = BlockPredictor()
        for _ in range(10):
            p.record_request(200, 0.3, response_size=8000)
        score_healthy = p.risk_score()

        for _ in range(5):
            p.record_request(429, 0.2, response_size=400)

        self.assertGreater(p.risk_score(), score_healthy)
        snap = p.snapshot()
        self.assertIn('error_rate', snap['signals'])
        self.assertAlmostEqual(snap['signals']['error_rate'], 5 / 15,
                               places=2)

    def test_all_errors_caps_at_one(self):
        p = BlockPredictor()
        for _ in range(20):
            p.record_request(429, 0.1)
        self.assertLessEqual(p.risk_score(), 1.0)
        self.assertGreater(p.risk_score(), 0.5)


class TestLatencySpikeSignal(unittest.TestCase):

    def test_baseline_learned_from_healthy_samples(self):
        p = BlockPredictor(baseline_latency_samples=10)
        # 12 healthy samples
        for _ in range(12):
            p.record_request(200, 0.30)
        snap = p.snapshot()
        self.assertIsNotNone(snap['baseline_latency_s'])
        self.assertAlmostEqual(snap['baseline_latency_s'], 0.30, places=2)

    def test_latency_spike_triggers_signal(self):
        p = BlockPredictor(baseline_latency_samples=10)
        # Healthy baseline with some variance
        import random
        random.seed(42)
        for _ in range(15):
            p.record_request(200, 0.3 + random.uniform(-0.05, 0.05))
        baseline_score = p.risk_score()

        # Add spiked-latency samples
        for _ in range(5):
            p.record_request(200, 1.5)  # 5x baseline

        self.assertGreater(p.risk_score(), baseline_score)
        snap = p.snapshot()
        self.assertIn('latency_spike', snap['signals'])
        self.assertGreater(snap['signals']['latency_spike'], 0)


class TestEmptyResponseSignal(unittest.TestCase):

    def test_tiny_responses_count(self):
        p = BlockPredictor()
        for _ in range(10):
            p.record_request(200, 0.3, response_size=8000)
        for _ in range(5):
            p.record_request(200, 0.3, response_size=100)

        snap = p.snapshot()
        self.assertIn('empty_response_rate', snap['signals'])
        self.assertAlmostEqual(snap['signals']['empty_response_rate'],
                               5 / 15, places=2)

    def test_unknown_size_ignored(self):
        """response_size=None should not count toward empty rate."""
        p = BlockPredictor()
        for _ in range(10):
            p.record_request(200, 0.3)  # size unknown
        snap = p.snapshot()
        self.assertNotIn('empty_response_rate', snap['signals'])


class TestStaleSignals(unittest.TestCase):

    def test_stale_severity(self):
        p = BlockPredictor()
        # Recent stale: 4/4 (severity 1.0), 3/4, 2/4
        p.record_stale(4, 4, engine='selenium')
        p.record_stale(3, 4, engine='selenium')
        p.record_stale(2, 4, engine='selenium')
        snap = p.snapshot()
        self.assertIn('stale_severity', snap['signals'])
        # Mean of last 5 (we only have 3): (1.0 + 0.75 + 0.5) / 3 = 0.75
        self.assertAlmostEqual(snap['signals']['stale_severity'], 0.75,
                               places=2)

    def test_reopen_failures(self):
        p = BlockPredictor()
        p.record_stale(4, 4, reopen_failed=False)
        p.record_stale(4, 4, reopen_failed=True)
        p.record_stale(4, 4, reopen_failed=True)
        snap = p.snapshot()
        self.assertAlmostEqual(snap['signals']['reopen_fail_rate'], 2 / 3,
                               places=2)


class TestScoringAndThreshold(unittest.TestCase):

    def test_should_cooldown_respects_threshold(self):
        p = BlockPredictor()
        # Mix of healthy + errors → partial error_rate signal
        for _ in range(5):
            p.record_request(200, 0.3, response_size=8000)
        for _ in range(10):
            p.record_request(429, 0.1)
        # Score moderate; tune thresholds around the actual value.
        score = p.risk_score()
        self.assertGreater(score, 0)
        self.assertLess(score, 1)
        self.assertTrue(p.should_cooldown(threshold=max(0.01, score - 0.1)))
        self.assertFalse(p.should_cooldown(threshold=min(0.999, score + 0.1)))

    def test_invalid_threshold_rejected(self):
        p = BlockPredictor()
        with self.assertRaises(ValueError):
            p.should_cooldown(threshold=0)
        with self.assertRaises(ValueError):
            p.should_cooldown(threshold=1.5)

    def test_score_bounded(self):
        """Even with all signals maxed, score stays ≤ 1."""
        p = BlockPredictor(baseline_latency_samples=5)
        for _ in range(6):
            p.record_request(200, 0.1)
        # Hammer everything
        for _ in range(50):
            p.record_request(500, 10.0, response_size=0)
        for _ in range(20):
            p.record_stale(10, 4, reopen_failed=True)
        self.assertLessEqual(p.risk_score(), 1.0)
        self.assertGreaterEqual(p.risk_score(), 0.0)


class TestWindowRolling(unittest.TestCase):

    def test_window_bounded(self):
        p = BlockPredictor(window_requests=5)
        for _ in range(20):
            p.record_request(429, 0.1)
        snap = p.snapshot()
        self.assertEqual(snap['request_count'], 5)
        self.assertLessEqual(len(snap['last_status_codes']), 10)


class TestThreadSafety(unittest.TestCase):
    """Parallel workers hit the same predictor — must not crash or
    produce garbage scores under contention."""

    def test_concurrent_recording(self):
        p = BlockPredictor(window_requests=1000)

        def worker():
            for _ in range(100):
                p.record_request(200, 0.3, response_size=5000)
                p.record_stale(2, 4)

        threads = [Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = p.snapshot()
        self.assertEqual(snap['request_count'], 400)
        self.assertEqual(snap['stale_count'], 20)  # capped by default window
        self.assertGreaterEqual(p.risk_score(), 0)
        self.assertLessEqual(p.risk_score(), 1)


class TestSeleniumEngineIntegration(unittest.TestCase):
    """SeleniumEngine calls predictor.record_stale if attribute is set."""

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_stale_events_flow_to_predictor(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        from instat.exceptions import BlockedError

        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = "span._ap3a"
        eng.warmup_threshold = 0
        eng.warmup_stale_rounds = 2
        eng.completion_threshold = 0
        eng._reopen_modal = MagicMock(return_value=False)
        predictor = BlockPredictor()
        eng._block_predictor = predictor

        with patch.object(eng, '_scroll_modal_js'), \
             patch('instat.engines.selenium_engine.Utils') as MockUtils:
            MockUtils.batch_read_text.return_value = set()
            try:
                eng._get_profiles(
                    expected_count=1000,
                    max_duration=None,
                    profile_id='x',
                    list_type='followers',
                    initial_profiles=set(),
                )
            except BlockedError:
                pass

        snap = predictor.snapshot()
        # Stale events should have been recorded
        self.assertGreater(snap['stale_count'], 0)
        # At least one reopen_failed=True because we stubbed reopen to False
        self.assertGreater(snap['signals'].get('reopen_fail_rate', 0), 0)


class TestHttpxEngineHook(unittest.TestCase):
    """HttpxEngine._on_response sends to predictor."""

    def test_on_response_records_if_predictor_set(self):
        from instat.engines.httpx_engine import HttpxEngine
        eng = HttpxEngine()
        predictor = BlockPredictor()
        eng._block_predictor = predictor

        fake_resp = MagicMock()
        fake_resp.status_code = 429
        fake_resp.elapsed.total_seconds.return_value = 0.123
        fake_resp.headers = {'content-length': '8192'}

        eng._on_response(fake_resp)

        snap = predictor.snapshot()
        self.assertEqual(snap['request_count'], 1)
        self.assertEqual(snap['last_status_codes'], [429])

    def test_on_response_noop_without_predictor(self):
        from instat.engines.httpx_engine import HttpxEngine
        eng = HttpxEngine()
        # No _block_predictor attribute at all
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        # Should not raise
        eng._on_response(fake_resp)


if __name__ == '__main__':
    unittest.main()
