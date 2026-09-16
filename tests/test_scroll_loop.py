"""ScrollLoop — unit tests for the extracted scroll-collect loop."""
import unittest
from unittest.mock import MagicMock, patch

from instat.exceptions import BlockedError
from instat.scroll_loop import ScrollLoop


def _mk_selectors(css='span._ap3a'):
    sel = MagicMock()
    sel.get.return_value = css
    return sel


def _mk_loop(reopen=None, **overrides):
    defaults = dict(
        driver=MagicMock(),
        selectors=_mk_selectors(),
        reopen_modal=reopen or (lambda *_a, **_kw: True),
        pause_time=0,
        wait_interval=0,
        warmup_threshold=0,
        warmup_stale_rounds=2,
        checkpoint_interval=100,
        completion_threshold=0.0,
    )
    defaults.update(overrides)
    return ScrollLoop(**defaults)


class TestScrollLoopTermination(unittest.TestCase):

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_reaches_expected_count_and_returns(self, _hd):
        loop = _mk_loop(completion_threshold=0.0)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {'a', 'b', 'c'}
            result = loop.run(expected_count=3, max_duration=None)
        self.assertEqual(set(result), {'a', 'b', 'c'})

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_empty_batch_exits_after_stale_rounds_without_profile_id(self, _hd):
        """No profile_id → cannot reopen, so stale limit ends loop."""
        loop = _mk_loop(warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            result = loop.run(expected_count=100, max_duration=None)
        self.assertEqual(result, [])

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_max_duration_exceeded_breaks_loop(self, _hd):
        import time
        loop = _mk_loop(warmup_stale_rounds=100)
        # Forge a long loop: batch always returns empty, but max_duration
        # will fire because time.perf_counter advances past 0.
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            t0 = time.perf_counter()
            loop.run(expected_count=100, max_duration=0.001)
            # Just ensure it returned quickly
            self.assertLess(time.perf_counter() - t0, 5.0)

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_should_stop_signal_breaks_loop(self, _hd):
        stop_after = {'n': 0}

        def should_stop():
            stop_after['n'] += 1
            return stop_after['n'] >= 2

        loop = _mk_loop()
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {'x'}
            result = loop.run(
                expected_count=1000, max_duration=None,
                should_stop=should_stop,
            )
        # Should have stopped without collecting 1000 profiles
        self.assertLess(len(result), 1000)


class TestScrollLoopReopenCascade(unittest.TestCase):

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_reopen_called_on_stale_threshold_warmup(self, _hd):
        reopen_calls = []

        def reopen(*_a, **_kw):
            reopen_calls.append(1)
            return False  # reopen fails → loop breaks

        loop = _mk_loop(reopen=reopen, warmup_threshold=1000,
                        warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            loop.run(
                expected_count=100, max_duration=None,
                profile_id='p', list_type='followers',
            )
        self.assertEqual(len(reopen_calls), 1)

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_reopen_resets_stale_counter_on_success(self, _hd):
        reopen_calls = []

        def reopen(*_a, **_kw):
            reopen_calls.append(1)
            return True

        # Empty snapshots forever — expect up to MAX_REOPEN_ATTEMPTS=3 reopens
        loop = _mk_loop(reopen=reopen, warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            loop.run(
                expected_count=100, max_duration=None,
                profile_id='p', list_type='followers',
            )
        self.assertEqual(len(reopen_calls), ScrollLoop.MAX_REOPEN_ATTEMPTS)

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_no_reopen_without_profile_id(self, _hd):
        reopen_calls = []

        def reopen(*_a, **_kw):
            reopen_calls.append(1)
            return True

        loop = _mk_loop(reopen=reopen, warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            loop.run(expected_count=100, max_duration=None)
        self.assertEqual(len(reopen_calls), 0)


class TestScrollLoopCheckpointAndOnBatch(unittest.TestCase):

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_checkpoint_saved_at_interval(self, _hd):
        ckpt = MagicMock()
        loop = _mk_loop(checkpoint_interval=5)
        # First iteration returns 10 profiles → triggers checkpoint
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {
                f'u{i}' for i in range(10)
            }
            loop.run(expected_count=10, max_duration=None, checkpoint=ckpt)
        ckpt.save.assert_called()

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_on_batch_called_when_new_added(self, _hd):
        seen = []

        def on_batch(profiles):
            seen.append(len(profiles))

        loop = _mk_loop()
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {'a'}
            loop.run(expected_count=1, max_duration=None, on_batch=on_batch)
        self.assertGreaterEqual(len(seen), 1)

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_on_batch_failure_is_swallowed(self, _hd):
        def on_batch(_):
            raise RuntimeError("consumer crashed")

        loop = _mk_loop()
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {'a'}
            # Must not propagate
            loop.run(expected_count=1, max_duration=None, on_batch=on_batch)


class TestScrollLoopPartialCoverage(unittest.TestCase):

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_partial_coverage_raises_blocked_error(self, _hd):
        loop = _mk_loop(completion_threshold=0.90, warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {
                f'u{i}' for i in range(50)
            }
            with self.assertRaises(BlockedError) as cm:
                loop.run(expected_count=100, max_duration=None)
        self.assertIn("partial coverage", str(cm.exception))
        self.assertIn("50/100", str(cm.exception))

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_full_coverage_does_not_raise(self, _hd):
        loop = _mk_loop(completion_threshold=0.90)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {
                f'u{i}' for i in range(100)
            }
            result = loop.run(expected_count=100, max_duration=None)
        self.assertEqual(len(result), 100)

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_zero_collected_does_not_raise(self, _hd):
        loop = _mk_loop(completion_threshold=0.90, warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            result = loop.run(expected_count=100, max_duration=None)
        self.assertEqual(result, [])

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_partial_coverage_saves_checkpoint_before_raising(self, _hd):
        ckpt = MagicMock()
        loop = _mk_loop(completion_threshold=0.90, warmup_stale_rounds=2)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = {
                f'u{i}' for i in range(50)
            }
            with self.assertRaises(BlockedError):
                loop.run(
                    expected_count=100, max_duration=None,
                    checkpoint=ckpt,
                )
        ckpt.save.assert_called()


class TestScrollLoopBlockPredictor(unittest.TestCase):

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_stale_events_sent_to_predictor(self, _hd):
        predictor = MagicMock()
        loop = _mk_loop(warmup_stale_rounds=2, block_predictor=predictor)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            loop.run(expected_count=100, max_duration=None)
        self.assertTrue(predictor.record_stale.called)

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_predictor_failure_is_swallowed(self, _hd):
        predictor = MagicMock()
        predictor.record_stale.side_effect = RuntimeError("boom")
        loop = _mk_loop(warmup_stale_rounds=2, block_predictor=predictor)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.return_value = set()
            loop.run(expected_count=100, max_duration=None)


class TestScrollLoopStaleCounterResets(unittest.TestCase):

    @patch('instat.scroll_loop.human_delay', return_value=0)
    def test_new_profile_resets_stale_counter(self, _hd):
        reopen_calls = []

        def reopen(*_a, **_kw):
            reopen_calls.append(1)
            return False

        # Sequence: empty, empty, new_item (resets), empty, empty, empty
        # With warmup_stale_rounds=3, reopen should fire only on the
        # final empty streak (after the reset).
        batches = iter([
            set(), set(), {'a'}, set(), set(), set(), set(), set(),
        ])
        loop = _mk_loop(reopen=reopen, warmup_threshold=1000,
                        warmup_stale_rounds=3)
        with patch('instat.scroll_loop.Utils') as MockUtils, \
             patch.object(ScrollLoop, '_scroll_modal_js'):
            MockUtils.batch_read_text.side_effect = lambda *_a, **_kw: (
                next(batches, set())
            )
            loop.run(
                expected_count=1000, max_duration=None,
                profile_id='p', list_type='followers',
            )
        self.assertGreaterEqual(len(reopen_calls), 1)


if __name__ == '__main__':
    unittest.main()
