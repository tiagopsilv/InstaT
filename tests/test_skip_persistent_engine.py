"""P4: until_complete excludes an engine that rate-limits in
consecutive iterations. Prevents burning wall-clock on a hard-
blocked endpoint while keeping the Selenium pass running."""
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from instat.engines.engine_manager import EngineManager
from instat.exceptions import RateLimitError


class TestEngineManagerExcludeEngines(unittest.TestCase):
    """EngineManager.extract honours exclude_engines kwarg."""

    def test_exclude_engines_skips_named_engines(self):
        eng_a = MagicMock()

        eng_a.name = 'selenium'
        eng_a.extract.return_value = {'u1', 'u2'}
        eng_b = MagicMock()

        eng_b.name = 'httpx'
        eng_b.extract.return_value = {'u99'}

        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager([eng_a, eng_b])
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                result = mgr.extract('t', 'followers',
                                     exclude_engines=['httpx'])
            finally:
                em.ExtractionCheckpoint = original_cls

        # selenium ran; httpx was skipped
        eng_a.extract.assert_called_once()
        eng_b.extract.assert_not_called()
        self.assertEqual(set(result), {'u1', 'u2'})

    def test_exclude_engines_empty_iter_is_noop(self):
        eng_a = MagicMock()

        eng_a.name = 'selenium'
        eng_a.extract.return_value = {'u1'}

        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager([eng_a])
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                mgr.extract('t', 'followers', exclude_engines=[])
            finally:
                em.ExtractionCheckpoint = original_cls
        eng_a.extract.assert_called_once()

    def test_rate_limit_sink_captures_engine_names(self):
        eng_a = MagicMock()

        eng_a.name = 'selenium'
        eng_a.extract.return_value = {'u1'}
        eng_b = MagicMock()

        eng_b.name = 'httpx'
        eng_b.extract.side_effect = RateLimitError('429')

        sink = []
        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager([eng_b, eng_a])  # httpx first so it gets tried
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                mgr.extract('t', 'followers', rate_limit_sink=sink)
            finally:
                em.ExtractionCheckpoint = original_cls

        self.assertIn('httpx', sink)
        # selenium DID succeed, no rate limit
        self.assertNotIn('selenium', sink)


class TestUntilCompleteExcludesAfterStreak(unittest.TestCase):

    def _make_ext(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor.__new__(InstaExtractor)
        ext._exporter = None
        ext._engine = MagicMock()
        ext._engine_manager = MagicMock()
        ext._engine_manager.engines = [
            type('E', (), {'name': 'selenium'})(),
            type('E', (), {'name': 'httpx'})(),
        ]
        return ext

    def test_excluded_after_two_consecutive_rate_limits(self):
        ext = self._make_ext()
        ext._engine_manager.get_total_count.return_value = 1000

        # Each call: engine_manager.extract populates rate_limit_sink if
        # provided. Simulate httpx rate-limiting every time, selenium
        # returning growing slices.
        call_seq = [
            (['u%d' % i for i in range(100)], ['httpx']),
            (['u%d' % i for i in range(200)], ['httpx']),  # streak=2 → exclude
            (['u%d' % i for i in range(300)], []),
        ]
        # After exclusion, httpx shouldn't be in the sink anymore because
        # engine_manager shouldn't try it.
        calls = iter(call_seq)
        exclude_args = []

        def fake_extract(profile_id, list_type, max_duration,
                         _exclude_engines=None, _rate_limit_sink=None):
            exclude_args.append(set(_exclude_engines or ()))
            items, rl = next(calls)
            if _rate_limit_sink is not None:
                _rate_limit_sink.extend(rl)
            return items

        with patch.object(ext, '_extract_with_export',
                          side_effect=fake_extract), \
             patch('instat.extractor.time.sleep'):
            result = ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=2, retry_wait_s=0,
            )

        # 1st call: no exclusions (streak just started)
        self.assertEqual(exclude_args[0], set())
        # 2nd call: httpx still NOT excluded (only after streak == 2 increment)
        self.assertEqual(exclude_args[1], set())
        # 3rd call: httpx excluded
        self.assertEqual(exclude_args[2], {'httpx'})
        # Final result union of all iterations
        self.assertEqual(len(result), 300)

    def test_streak_resets_when_engine_recovers(self):
        ext = self._make_ext()
        ext._engine_manager.get_total_count.return_value = 1000

        call_seq = [
            (['u%d' % i for i in range(100)], ['httpx']),  # streak httpx=1
            (['u%d' % i for i in range(200)], []),          # streak reset
            (['u%d' % i for i in range(300)], ['httpx']),   # streak=1 again
        ]
        calls = iter(call_seq)
        exclude_args = []

        def fake_extract(profile_id, list_type, max_duration,
                         _exclude_engines=None, _rate_limit_sink=None):
            exclude_args.append(set(_exclude_engines or ()))
            items, rl = next(calls)
            if _rate_limit_sink is not None:
                _rate_limit_sink.extend(rl)
            return items

        with patch.object(ext, '_extract_with_export',
                          side_effect=fake_extract), \
             patch('instat.extractor.time.sleep'):
            ext.get_following_until_complete(
                'user', target_fraction=0.95, max_retries=2, retry_wait_s=0,
            )

        # httpx never hit 2-in-a-row → never excluded
        for excl in exclude_args:
            self.assertEqual(excl, set())


if __name__ == '__main__':
    unittest.main()
