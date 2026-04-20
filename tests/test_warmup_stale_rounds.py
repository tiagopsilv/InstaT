"""SeleniumEngine warmup: tolerate more stale rounds while collection
is shallow, before declaring rate-limit / triggering modal reopen."""
import unittest
from unittest.mock import MagicMock, patch


class TestWarmupDefaults(unittest.TestCase):

    def test_warmup_defaults_set(self):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        self.assertTrue(hasattr(eng, 'warmup_threshold'))
        self.assertTrue(hasattr(eng, 'warmup_stale_rounds'))
        self.assertEqual(eng.warmup_threshold, 200)
        self.assertEqual(eng.warmup_stale_rounds, 10)

    def test_warmup_can_be_tuned_per_instance(self):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng.warmup_threshold = 500
        eng.warmup_stale_rounds = 20
        self.assertEqual(eng.warmup_threshold, 500)
        self.assertEqual(eng.warmup_stale_rounds, 20)


class TestWarmupScrollBehavior(unittest.TestCase):
    """End-to-end-ish test of _get_profiles loop respecting warmup."""

    def _make_engine(self):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = "span._ap3a"
        return eng

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_stale_rounds_higher_limit_during_warmup(self, _hd):
        """With 0 collected profiles (warmup region), the loop should
        tolerate up to warmup_stale_rounds stale before triggering reopen."""
        from instat.exceptions import BlockedError
        eng = self._make_engine()
        eng.warmup_threshold = 200
        eng.warmup_stale_rounds = 8
        eng.completion_threshold = 0  # disable, we're testing stale logic

        reopen_calls = []

        def fake_reopen(*a, **kw):
            reopen_calls.append(1)
            return False

        eng._reopen_modal = fake_reopen

        with patch.object(eng, '_scroll_modal_js'), \
             patch('instat.engines.selenium_engine.Utils') as MockUtils:
            MockUtils.batch_read_text.return_value = set()
            try:
                eng._get_profiles(
                    expected_count=850000,
                    max_duration=None,
                    profile_id='someuser',
                    list_type='followers',
                    initial_profiles=set(),
                )
            except BlockedError:
                pass  # engine may raise on low coverage; we care about reopen count

        self.assertEqual(len(reopen_calls), 1, "reopen called exactly once")

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_stale_rounds_normal_limit_after_warmup(self, _hd):
        """Once collection crosses warmup_threshold, revert to strict
        MAX_STALE_ROUNDS=4 limit."""
        from instat.exceptions import BlockedError
        eng = self._make_engine()
        eng.warmup_threshold = 5
        eng.warmup_stale_rounds = 20
        eng.completion_threshold = 0  # disable

        reopen_calls = []

        def fake_reopen(*a, **kw):
            reopen_calls.append(1)
            return False

        eng._reopen_modal = fake_reopen

        batch_results = iter([
            {f'u{i}' for i in range(10)},
        ])

        def batch(*a, **kw):
            try:
                return next(batch_results)
            except StopIteration:
                return set()

        with patch.object(eng, '_scroll_modal_js'), \
             patch('instat.engines.selenium_engine.Utils') as MockUtils:
            MockUtils.batch_read_text.side_effect = batch
            try:
                eng._get_profiles(
                    expected_count=100,
                    max_duration=None,
                    profile_id='someuser',
                    list_type='followers',
                    initial_profiles=set(),
                )
            except BlockedError:
                pass

        self.assertEqual(len(reopen_calls), 1)


if __name__ == '__main__':
    unittest.main()
