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


class TestCascadeDisabledWarning(unittest.TestCase):
    """Low completion_threshold + multi-engine effectively disables the
    cascade — warn the caller."""

    def test_warns_on_low_threshold_with_multi_engine(self):
        from instat.extractor import InstaExtractor
        fake_a = MagicMock()
        fake_a.name = 'selenium'
        fake_a.completion_threshold = 0.90
        fake_b = MagicMock()
        fake_b.name = 'httpx'
        fake_b.completion_threshold = 0.90

        captured = []
        with patch.object(
            InstaExtractor, '_build_engines',
            return_value=[fake_a, fake_b],
        ), patch('instat.extractor.EngineManager'), \
           patch('instat.extractor.logger') as mock_logger:
            fake_a.login.return_value = True
            fake_a._driver = MagicMock()
            fake_a._login_obj = MagicMock()
            mock_logger.warning.side_effect = lambda msg, *a, **k: captured.append(
                msg % a if a else msg
            )
            InstaExtractor('u', 'p', engines=['selenium', 'httpx'],
                           completion_threshold=0.05)

        joined = "\n".join(str(c) for c in captured)
        self.assertIn('0.05', joined)
        self.assertIn('cascade', joined.lower())

    def test_no_warning_on_single_engine(self):
        from instat.extractor import InstaExtractor
        fake = MagicMock()
        fake.name = 'selenium'
        fake.completion_threshold = 0.90

        with patch.object(
            InstaExtractor, '_build_engines', return_value=[fake],
        ), patch('instat.extractor.EngineManager'), \
           patch('instat.extractor.logger') as mock_logger:
            fake.login.return_value = True
            fake._driver = MagicMock()
            fake._login_obj = MagicMock()
            InstaExtractor('u', 'p', engines=['selenium'],
                           completion_threshold=0.05)

        # No cascade-disabled warning when only one engine
        for call in mock_logger.warning.call_args_list:
            msg = str(call.args[0]) if call.args else ''
            self.assertNotIn('cascade will not trigger', msg)

    def test_no_warning_on_high_threshold_multi_engine(self):
        from instat.extractor import InstaExtractor
        fake_a = MagicMock()
        fake_a.name = 'selenium'
        fake_a.completion_threshold = 0.90
        fake_b = MagicMock()
        fake_b.name = 'httpx'
        fake_b.completion_threshold = 0.90

        with patch.object(
            InstaExtractor, '_build_engines',
            return_value=[fake_a, fake_b],
        ), patch('instat.extractor.EngineManager'), \
           patch('instat.extractor.logger') as mock_logger:
            fake_a.login.return_value = True
            fake_a._driver = MagicMock()
            fake_a._login_obj = MagicMock()
            InstaExtractor('u', 'p', engines=['selenium', 'httpx'],
                           completion_threshold=0.80)

        for call in mock_logger.warning.call_args_list:
            msg = str(call.args[0]) if call.args else ''
            self.assertNotIn('cascade will not trigger', msg)


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
        """If a retry adds NO new profiles to the union, stop — keep the union."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000

        sequence = iter([
            ['u%d' % i for i in range(500)],
            ['u%d' % i for i in range(700)],       # union = 700
            ['u%d' % i for i in range(700)],       # no new — stop
        ])

        with patch.object(ext, '_extract_with_export',
                          side_effect=lambda *a, **kw: next(sequence)), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=5, retry_wait_s=0,
            )
        self.assertEqual(len(res), 700)

    def test_retries_then_exhausts_returns_accumulated(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000

        sequence = iter([
            ['u%d' % i for i in range(100)],            # union 0..99 (100)
            ['u%d' % i for i in range(100, 200)],       # union 0..199 (200)
            ['u%d' % i for i in range(200, 300)],       # union 0..299 (300)
            ['u%d' % i for i in range(300, 400)],       # union 0..399 (400)
        ])

        with patch.object(ext, '_extract_with_export',
                          side_effect=lambda *a, **kw: next(sequence)), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=3, retry_wait_s=0,
            )

        # 4 attempts, 4 disjoint slices => accumulated 400
        self.assertEqual(len(res), 400)

    def test_total_unknown_still_returns_what_it_got(self):
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

        # No target known, 2nd call adds no new profiles → stagnation stop.
        self.assertEqual(len(calls), 2)
        self.assertEqual(sorted(res), ['a', 'b', 'c'])

    def test_regression_worst_case_preserves_best(self):
        """B2 regression: iteration 2 returns 0 after iteration 1 got 2724
        — final must NOT be 0. This is the exact bug the v2 live test hit."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 3579

        sequence = iter([
            ['u%d' % i for i in range(398)],        # it 1 → accumulated 398
            ['u%d' % i for i in range(2724)],       # it 2 → accumulated 2724
            [],                                      # it 3 → crashed, 0 new
            [],                                      # it 4 would stagnate
        ])

        with patch.object(ext, '_extract_with_export',
                          side_effect=lambda *a, **kw: next(sequence)), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.90, max_retries=3, retry_wait_s=0,
            )

        # MUST return the 2724, not 0
        self.assertEqual(len(res), 2724)

    def test_iteration_raises_keeps_accumulated(self):
        """If an iteration raises, previously-accumulated profiles are kept."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000

        sequence = iter([
            ['u%d' % i for i in range(500)],
            RuntimeError("firefox died"),
            ['u%d' % i for i in range(500, 700)],
        ])

        def maybe_raise(*a, **kw):
            v = next(sequence)
            if isinstance(v, Exception):
                raise v
            return v

        with patch.object(ext, '_extract_with_export', side_effect=maybe_raise), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=3, retry_wait_s=0,
            )

        # iteration 1 → 500, iteration 2 raised (0 new), iteration 3 → 700 total
        self.assertEqual(len(res), 700)

    def test_duplicate_profiles_across_iterations_deduped(self):
        """Acumulador deduplica entre iterações."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 100

        sequence = iter([
            ['a', 'b', 'c'],
            ['b', 'c', 'd'],     # 1 novo — mas under target, continua
            ['d', 'e'],          # 1 novo (e)
            ['d', 'e'],          # 0 novos → stop
        ])

        with patch.object(ext, '_extract_with_export',
                          side_effect=lambda *a, **kw: next(sequence)), \
             patch('instat.extractor.time.sleep'):
            res = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=5, retry_wait_s=0,
            )

        self.assertEqual(sorted(res), ['a', 'b', 'c', 'd', 'e'])

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


class TestAccountBlockedDiagnostic(unittest.TestCase):
    """until_complete logs actionable advice when coverage stays below 1%."""

    def test_warning_emitted_at_low_coverage(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 10000
        ext._engine_manager.engines = [
            MagicMock(name='selenium'), MagicMock(name='httpx'),
        ]
        ext._engine_manager.engines[0].name = 'selenium'
        ext._engine_manager.engines[1].name = 'httpx'

        sequence = iter([
            ['u%d' % i for i in range(30)],
            ['u%d' % i for i in range(30)],
            ['u%d' % i for i in range(30)],
        ])

        captured = []
        with patch.object(ext, '_extract_with_export',
                          side_effect=lambda *a, **kw: next(sequence)), \
             patch('instat.extractor.time.sleep'), \
             patch('instat.extractor.logger') as mock_logger:
            mock_logger.warning.side_effect = lambda msg, *a, **k: captured.append(
                msg % a if a else msg
            )
            ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=2, retry_wait_s=0,
            )

        joined = "\n".join(str(c) for c in captured)
        # Diagnostic should mention engines tried, shadow-rate-limit, and
        # at least one actionable suggestion.
        self.assertIn('selenium', joined)
        self.assertIn('httpx', joined)
        self.assertIn('shadow', joined.lower())

    def test_no_warning_when_coverage_above_1_percent(self):
        """Don't spam the warning when user hit a soft block but still got
        a lot of the list."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000
        ext._engine_manager.engines = [MagicMock(name='selenium')]
        ext._engine_manager.engines[0].name = 'selenium'

        sequence = iter([
            ['u%d' % i for i in range(500)],
            ['u%d' % i for i in range(500)],
        ])

        captured = []
        with patch.object(ext, '_extract_with_export',
                          side_effect=lambda *a, **kw: next(sequence)), \
             patch('instat.extractor.time.sleep'), \
             patch('instat.extractor.logger') as mock_logger:
            mock_logger.warning.side_effect = lambda msg, *a, **k: captured.append(
                msg % a if a else msg
            )
            ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=2, retry_wait_s=0,
            )

        joined = "\n".join(str(c) for c in captured)
        self.assertNotIn('shadow', joined.lower())

    def test_no_warning_when_total_unknown(self):
        """Can't compute percentage → skip the diagnostic."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = None
        ext._engine_manager.engines = [MagicMock(name='selenium')]
        ext._engine_manager.engines[0].name = 'selenium'

        captured = []
        with patch.object(ext, '_extract_with_export',
                          return_value=['a', 'b']), \
             patch('instat.extractor.time.sleep'), \
             patch('instat.extractor.logger') as mock_logger:
            mock_logger.warning.side_effect = lambda msg, *a, **k: captured.append(
                msg % a if a else msg
            )
            ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=1, retry_wait_s=0,
            )

        joined = "\n".join(str(c) for c in captured)
        self.assertNotIn('shadow', joined.lower())


if __name__ == '__main__':
    unittest.main()
