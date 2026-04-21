"""get_{followers,following}_persistent — accrual across simulated runs."""
import os
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch


def _make_ext():
    from instat.extractor import InstaExtractor
    ext = InstaExtractor.__new__(InstaExtractor)
    ext._exporter = None
    ext._engine = MagicMock()
    ext._engine_manager = MagicMock()
    ext._imap_config = None
    ext._headless = True
    ext._engine_names = ['selenium']
    ext.timeout = 10
    ext._completion_threshold_override = None
    ext.username = 'primary'
    ext.password = 'pw'
    ext._engine_manager.engines = [
        type('E', (), {'name': 'selenium'})()
    ]
    return ext


class TestPersistentExtractFlow(unittest.TestCase):

    def test_first_run_populates_store(self):
        ext = _make_ext()
        ext._engine_manager.get_total_count.return_value = 10000

        with patch.object(ext, '_extract_until_complete',
                          return_value=['u1', 'u2', 'u3']):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "store.sqlite")
                res = ext.get_followers_persistent(
                    'target', store_path=path,
                )
        self.assertEqual(res['this_run_collected'], 3)
        self.assertEqual(res['this_run_new'], 3)
        self.assertEqual(res['store_total'], 3)
        self.assertEqual(res['store_total_before_run'], 0)
        self.assertEqual(sorted(res['this_run_new_usernames']),
                         ['u1', 'u2', 'u3'])
        self.assertEqual(res['target_total'], 10000)
        self.assertIn('primary', res['source_accounts'])

    def test_second_run_only_counts_deltas(self):
        ext = _make_ext()
        ext._engine_manager.get_total_count.return_value = 100

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "store.sqlite")

            # run 1: 3 profiles
            with patch.object(ext, '_extract_until_complete',
                              return_value=['u1', 'u2', 'u3']):
                r1 = ext.get_followers_persistent(
                    'target', store_path=path,
                )
            self.assertEqual(r1['store_total'], 3)
            # Give the clock a tick to advance — mocks are sub-ms fast
            # and run_started_at can collide with run 1's insertion ts.
            time.sleep(0.05)

            # run 2: 2 overlapping, 2 new
            with patch.object(ext, '_extract_until_complete',
                              return_value=['u2', 'u3', 'u4', 'u5']):
                r2 = ext.get_followers_persistent(
                    'target', store_path=path,
                )
            self.assertEqual(r2['store_total_before_run'], 3)
            self.assertEqual(r2['store_total'], 5)
            self.assertEqual(r2['this_run_new'], 2)
            self.assertEqual(sorted(r2['this_run_new_usernames']),
                             ['u4', 'u5'])

    def test_rotation_when_fallback_accounts_given(self):
        """fallback_accounts provided → use _extract_with_rotation path."""
        ext = _make_ext()
        ext._engine_manager.get_total_count.return_value = 100

        with patch.object(ext, '_extract_with_rotation',
                          return_value=['a', 'b']) as rot, \
             patch.object(ext, '_extract_until_complete',
                          return_value=['should_not_be_called']) as uc:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "s.sqlite")
                res = ext.get_followers_persistent(
                    'target',
                    store_path=path,
                    fallback_accounts=[{'username': 'alt', 'password': 'p'}],
                )
        rot.assert_called_once()
        uc.assert_not_called()
        self.assertEqual(res['store_total'], 2)
        # Source tagged as rotation:<primary>
        self.assertIn('rotation:primary', res['source_accounts'])

    def test_no_rotation_when_no_fallbacks(self):
        ext = _make_ext()
        ext._engine_manager.get_total_count.return_value = 100

        with patch.object(ext, '_extract_until_complete',
                          return_value=['a']) as uc, \
             patch.object(ext, '_extract_with_rotation',
                          return_value=['should_not_be_called']) as rot:
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "s.sqlite")
                ext.get_followers_persistent('target', store_path=path)
        uc.assert_called_once()
        rot.assert_not_called()

    def test_profile_id_validated(self):
        ext = _make_ext()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.sqlite")
            with self.assertRaises(ValueError):
                ext.get_followers_persistent('../admin', store_path=path)

    def test_target_coverage_pct_computed(self):
        ext = _make_ext()
        ext._engine_manager.get_total_count.return_value = 1000

        with patch.object(ext, '_extract_until_complete',
                          return_value=[f'u{i}' for i in range(250)]):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "s.sqlite")
                res = ext.get_followers_persistent(
                    'target', store_path=path,
                )
        self.assertEqual(res['store_total'], 250)
        self.assertAlmostEqual(res['target_coverage_pct'], 25.0, places=1)

    def test_total_unknown_yields_none_pct(self):
        ext = _make_ext()
        ext._engine_manager.get_total_count.side_effect = RuntimeError(
            "no total"
        )
        with patch.object(ext, '_extract_until_complete',
                          return_value=['a']):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "s.sqlite")
                res = ext.get_followers_persistent(
                    'target', store_path=path,
                )
        self.assertIsNone(res['target_total'])
        self.assertIsNone(res['target_coverage_pct'])


if __name__ == '__main__':
    unittest.main()
