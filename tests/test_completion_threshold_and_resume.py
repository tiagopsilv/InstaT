"""InstaExtractor: completion_threshold kwarg and get_{followers,following}_until_complete."""
import unittest
from unittest.mock import MagicMock, patch


def _make_extractor_skeleton():
    """Build an InstaExtractor without going through __init__ (no Firefox)."""
    from instat.extractor import InstaExtractor
    ext = InstaExtractor.__new__(InstaExtractor)
    ext._exporter = None
    ext._engine = MagicMock()
    ext._engine_manager = MagicMock()
    return ext


class TestCompletionThresholdKwarg(unittest.TestCase):
    """__init__ propagates completion_threshold to every engine that supports it."""

    def test_propagates_to_engines(self):
        from instat.extractor import InstaExtractor

        fake_selenium = MagicMock()
        fake_selenium.completion_threshold = 0.90
        fake_httpx = MagicMock()
        # httpx has no completion_threshold — should be left alone
        del fake_httpx.completion_threshold

        with patch.object(
            InstaExtractor, '_build_engines',
            return_value=[fake_selenium, fake_httpx],
        ), patch('instat.extractor.EngineManager'):
            fake_selenium.login.return_value = True
            fake_selenium._driver = MagicMock()
            fake_selenium._login_obj = MagicMock()
            ext = InstaExtractor(
                'u', 'p',
                completion_threshold=0.40,
            )

        self.assertEqual(fake_selenium.completion_threshold, 0.40)
        self.assertFalse(hasattr(fake_httpx, 'completion_threshold'))
        self.assertEqual(ext._completion_threshold_override, 0.40)

    def test_none_preserves_engine_default(self):
        from instat.extractor import InstaExtractor

        fake = MagicMock()
        fake.completion_threshold = 0.90
        with patch.object(
            InstaExtractor, '_build_engines', return_value=[fake],
        ), patch('instat.extractor.EngineManager'):
            fake.login.return_value = True
            fake._driver = MagicMock()
            fake._login_obj = MagicMock()
            InstaExtractor('u', 'p')
        self.assertEqual(fake.completion_threshold, 0.90)

    def test_out_of_range_rejected(self):
        from instat.extractor import InstaExtractor
        with self.assertRaises(ValueError):
            InstaExtractor('u', 'p', completion_threshold=0)
        with self.assertRaises(ValueError):
            InstaExtractor('u', 'p', completion_threshold=1.5)
        with self.assertRaises(ValueError):
            InstaExtractor('u', 'p', completion_threshold=-0.1)


class TestUntilCompleteLoop(unittest.TestCase):

    def test_returns_early_when_target_reached(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 100

        calls = []

        def fake_extract(*a, **kw):
            calls.append(1)
            return ['u%d' % i for i in range(96)]  # 96% >= 0.95 on 1st try

        with patch.object(ext, '_extract_with_export', side_effect=fake_extract):
            res = ext.get_following_until_complete(
                'someuser', target_fraction=0.95, max_retries=5, retry_wait_s=0,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(res), 96)

    def test_retries_then_stops_on_stagnation(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000

        sequence = iter([
            ['u%d' % i for i in range(500)],   # 50% — retry
            ['u%d' % i for i in range(700)],   # 70% — retry
            ['u%d' % i for i in range(700)],   # stagnated — stop
        ])

        def fake_extract(*a, **kw):
            return next(sequence)

        with patch.object(ext, '_extract_with_export', side_effect=fake_extract), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=5, retry_wait_s=0,
            )

        self.assertEqual(len(res), 700)

    def test_retries_then_exhausts(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000

        sequence = iter([
            ['u%d' % i for i in range(100)],
            ['u%d' % i for i in range(200)],
            ['u%d' % i for i in range(300)],
            ['u%d' % i for i in range(400)],  # final attempt, still below 95%
        ])

        def fake_extract(*a, **kw):
            return next(sequence)

        with patch.object(ext, '_extract_with_export', side_effect=fake_extract), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=3, retry_wait_s=0,
            )

        # 4 calls total (initial + 3 retries), 400 returned
        self.assertEqual(len(res), 400)

    def test_total_unknown_still_returns_what_it_got(self):
        """get_total_count None → no target, single attempt."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.side_effect = RuntimeError("no total")

        calls = []

        def fake_extract(*a, **kw):
            calls.append(1)
            return ['a', 'b', 'c']

        with patch.object(ext, '_extract_with_export', side_effect=fake_extract), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=2, retry_wait_s=0,
            )

        # No target known → every iteration "succeeds" but never beats target.
        # Stagnation kicks in on 2nd call (count <= last_count).
        # So we expect exactly 2 attempts (initial + 1 retry that stagnated).
        self.assertEqual(len(calls), 2)
        self.assertEqual(res, ['a', 'b', 'c'])

    def test_rejects_bad_target_fraction(self):
        ext = _make_extractor_skeleton()
        with self.assertRaises(ValueError):
            ext.get_following_until_complete('user', target_fraction=0)
        with self.assertRaises(ValueError):
            ext.get_following_until_complete('user', target_fraction=1.5)

    def test_rejects_negative_retries(self):
        ext = _make_extractor_skeleton()
        with self.assertRaises(ValueError):
            ext.get_following_until_complete('user', max_retries=-1)

    def test_validates_profile_id(self):
        """Security invariant: profile_id must be validated, same as get_following."""
        ext = _make_extractor_skeleton()
        with self.assertRaises(ValueError):
            ext.get_following_until_complete('../admin')
        with self.assertRaises(ValueError):
            ext.get_followers_until_complete('user?q=1')


if __name__ == '__main__':
    unittest.main()
