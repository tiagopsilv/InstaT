"""Testes para parallel.py (ParallelCoordinator + parallel_extract)."""
import threading
import unittest
from unittest.mock import MagicMock, patch


class TestParallelCoordinator(unittest.TestCase):
    def test_ingest_triggers_stop_at_threshold(self):
        from instat.parallel import ParallelCoordinator
        coord = ParallelCoordinator(target_count=100, stop_threshold=0.9)
        coord.ingest({f'u{i}' for i in range(50)})
        self.assertFalse(coord.should_stop())
        coord.ingest({f'u{i}' for i in range(50, 90)})
        self.assertTrue(coord.should_stop())

    def test_no_target_never_stops(self):
        from instat.parallel import ParallelCoordinator
        coord = ParallelCoordinator(target_count=None)
        coord.ingest({f'u{i}' for i in range(1000)})
        self.assertFalse(coord.should_stop())

    def test_ingest_thread_safe(self):
        from instat.parallel import ParallelCoordinator
        coord = ParallelCoordinator(target_count=None)

        def worker(base):
            for i in range(500):
                coord.ingest({f'u{base}_{i}'})

        threads = [threading.Thread(target=worker, args=(b,)) for b in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(coord.shared), 2500)


class TestParallelExtract(unittest.TestCase):
    def _make_engine(self, profiles_to_return):
        eng = MagicMock()
        eng.login.return_value = True
        eng.extract.return_value = set(profiles_to_return)
        return eng

    def test_union_of_workers(self):
        from instat.parallel import parallel_extract
        call = {'n': 0}

        def factory():
            call['n'] += 1
            n = call['n']
            if n == 1:
                return self._make_engine({f'u{i}' for i in range(50)})
            return self._make_engine({f'u{i}' for i in range(40, 90)})

        result = parallel_extract(
            'target', 'followers',
            workers=2,
            default_credentials=('u', 'p'),
            target_count=None,
            engine_factory=factory,
        )
        self.assertEqual(len(set(result)), 90)

    def test_stop_signalled_when_threshold_hit(self):
        from instat.parallel import parallel_extract

        engines_made = []
        def factory():
            e = self._make_engine({f'u{i}' for i in range(100)})
            engines_made.append(e)
            return e

        parallel_extract(
            'target', 'followers',
            workers=2,
            default_credentials=('u', 'p'),
            target_count=100,
            stop_threshold=0.9,
            engine_factory=factory,
        )
        for e in engines_made:
            # should_stop callable deve ter sido passado
            call_kwargs = e.extract.call_args.kwargs
            self.assertIn('should_stop', call_kwargs)
            self.assertTrue(callable(call_kwargs['should_stop']))

    def test_accounts_rotate_across_workers(self):
        from instat.parallel import parallel_extract
        seen = []

        def factory():
            e = MagicMock()
            def login(u, p):
                seen.append(u)
                return True
            e.login.side_effect = login
            e.extract.return_value = set()
            return e

        parallel_extract(
            'target', 'followers',
            workers=3,
            default_credentials=('default', 'p'),
            accounts=[{'username': 'a1', 'password': 'p'},
                      {'username': 'a2', 'password': 'p'}],
            target_count=None,
            engine_factory=factory,
        )
        self.assertEqual(set(seen), {'a1', 'a2'})

    def test_worker_failure_does_not_crash(self):
        from instat.parallel import parallel_extract
        call = {'n': 0}
        def factory():
            call['n'] += 1
            e = MagicMock()
            e.login.return_value = True
            if call['n'] == 1:
                e.extract.side_effect = RuntimeError('boom')
            else:
                e.extract.return_value = {'u1', 'u2'}
            return e

        result = parallel_extract(
            'target', 'followers',
            workers=2,
            default_credentials=('u', 'p'),
            target_count=None,
            engine_factory=factory,
        )
        self.assertEqual(set(result), {'u1', 'u2'})


class TestSeleniumShouldStop(unittest.TestCase):
    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_should_stop_exits_loop(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value={f'u{i}' for i in range(10)}):
            result = eng._get_profiles(
                expected_count=1000, max_duration=10.0,
                should_stop=lambda: True,
            )
        # Loop deve sair no 1º check; resultado reflete 0 ou só 1 batch
        self.assertLessEqual(len(result), 10)


if __name__ == '__main__':
    unittest.main()
