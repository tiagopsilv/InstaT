"""WorkerPool — unit tests for the persistent pool of pre-logged-in
parallel workers, and InstaExtractor integration (set_worker /
clear_workers / _parallel reuse)."""
import unittest
from unittest.mock import MagicMock, patch


class TestWorkerPool(unittest.TestCase):

    def _mk_engine(self):
        engine = MagicMock()
        return engine

    @patch('instat.worker_pool.SeleniumEngine')
    def test_add_worker_creates_engine_and_logs_in(self, MockEngine):
        from instat.worker_pool import WorkerPool
        engine = self._mk_engine()
        MockEngine.return_value = engine

        pool = WorkerPool(headless=True, timeout=15)
        pool.add_worker('alt1', 'pw1')

        MockEngine.assert_called_once()
        engine.login.assert_called_once_with('alt1', 'pw1')
        self.assertEqual(len(pool), 1)

    @patch('instat.worker_pool.SeleniumEngine')
    def test_login_failure_does_not_add_to_pool(self, MockEngine):
        from instat.worker_pool import WorkerPool
        engine = self._mk_engine()
        engine.login.side_effect = RuntimeError('login failed')
        MockEngine.return_value = engine

        pool = WorkerPool()
        with self.assertRaises(RuntimeError):
            pool.add_worker('bad', 'pw')
        self.assertEqual(len(pool), 0)
        engine.quit.assert_called_once()

    @patch('instat.worker_pool.SeleniumEngine')
    def test_multiple_workers(self, MockEngine):
        from instat.worker_pool import WorkerPool
        engines = [self._mk_engine() for _ in range(3)]
        MockEngine.side_effect = engines

        pool = WorkerPool()
        for i in range(3):
            pool.add_worker(f'alt{i}', f'pw{i}')

        self.assertEqual(len(pool), 3)
        self.assertEqual(pool.engines(), engines)

    @patch('instat.worker_pool.SeleniumEngine')
    def test_clear_quits_every_engine(self, MockEngine):
        from instat.worker_pool import WorkerPool
        engines = [self._mk_engine() for _ in range(2)]
        MockEngine.side_effect = engines

        pool = WorkerPool()
        pool.add_worker('a', 'x')
        pool.add_worker('b', 'y')

        pool.clear()

        for e in engines:
            e.quit.assert_called_once()
        self.assertEqual(len(pool), 0)
        self.assertFalse(bool(pool))

    @patch('instat.worker_pool.SeleniumEngine')
    def test_clear_tolerates_quit_failure(self, MockEngine):
        from instat.worker_pool import WorkerPool
        engine = self._mk_engine()
        engine.quit.side_effect = Exception('driver dead')
        MockEngine.return_value = engine

        pool = WorkerPool()
        pool.add_worker('a', 'x')
        # Must not raise
        pool.clear()
        self.assertEqual(len(pool), 0)

    @patch('instat.worker_pool.SeleniumEngine')
    def test_pool_is_iterable(self, MockEngine):
        from instat.worker_pool import WorkerPool
        engines = [self._mk_engine() for _ in range(2)]
        MockEngine.side_effect = engines

        pool = WorkerPool()
        pool.add_worker('a', 'x')
        pool.add_worker('b', 'y')

        self.assertEqual(list(pool), engines)


class TestParallelExtractWithPool(unittest.TestCase):
    """parallel_extract with preloaded_engines must NOT call login/quit."""

    def test_preloaded_engines_skip_login_and_quit(self):
        from instat.parallel import parallel_extract
        e1 = MagicMock()
        e2 = MagicMock()
        e1.extract.return_value = {'a', 'b'}
        e2.extract.return_value = {'c', 'd'}

        result = parallel_extract(
            profile_id='target', list_type='followers',
            workers=99,  # overridden by len(preloaded_engines)
            default_credentials=('ignored', 'ignored'),
            preloaded_engines=[e1, e2],
        )

        self.assertEqual(set(result), {'a', 'b', 'c', 'd'})
        e1.login.assert_not_called()
        e2.login.assert_not_called()
        e1.quit.assert_not_called()
        e2.quit.assert_not_called()
        e1.extract.assert_called_once()
        e2.extract.assert_called_once()

    def test_pool_worker_failure_isolated(self):
        from instat.parallel import parallel_extract
        e1 = MagicMock()
        e2 = MagicMock()
        e1.extract.side_effect = RuntimeError('engine died')
        e2.extract.return_value = {'c', 'd'}

        result = parallel_extract(
            profile_id='target', list_type='followers',
            workers=2,
            default_credentials=('ignored', 'ignored'),
            preloaded_engines=[e1, e2],
        )
        self.assertEqual(set(result), {'c', 'd'})


class TestInstaExtractorSetWorker(unittest.TestCase):
    """InstaExtractor integration: set_worker populates the pool and
    _parallel reuses it instead of spawning fresh logins."""

    def setUp(self):
        from instat.engines.selenium_engine import SeleniumEngine
        self.primary = MagicMock(spec=SeleniumEngine)
        self.primary.name = 'selenium'
        self.primary.completion_threshold = 0.90

        # Patch _build_engines to return a single mocked engine
        self._build_patch = patch.object(
            __import__('instat.extractor', fromlist=['InstaExtractor']).InstaExtractor,
            '_build_engines',
            return_value=[self.primary],
        )
        self._build_patch.start()

    def tearDown(self):
        self._build_patch.stop()

    def _mk_extractor(self):
        from instat.extractor import InstaExtractor
        # Login on primary is called from __init__; make it a no-op
        self.primary.login.return_value = True
        return InstaExtractor('main', 'pw', headless=True, timeout=20)

    @patch('instat.extractor.WorkerPool')
    def test_set_worker_populates_pool(self, MockWorkerPool):
        ext = self._mk_extractor()
        pool = MagicMock()
        MockWorkerPool.return_value = pool

        ext.set_worker('alt1', 'pw1')
        ext.set_worker('alt2', 'pw2')

        MockWorkerPool.assert_called_once()
        self.assertEqual(pool.add_worker.call_count, 2)
        pool.add_worker.assert_any_call('alt1', 'pw1', proxy=None)
        pool.add_worker.assert_any_call('alt2', 'pw2', proxy=None)

    @patch('instat.extractor.WorkerPool')
    def test_clear_workers_delegates_to_pool(self, MockWorkerPool):
        ext = self._mk_extractor()
        pool = MagicMock()
        MockWorkerPool.return_value = pool

        ext.set_worker('alt1', 'pw1')
        ext.clear_workers()

        pool.clear.assert_called_once()

    @patch('instat.extractor.WorkerPool')
    def test_parallel_reuses_pool_engines(self, MockWorkerPool):
        ext = self._mk_extractor()
        pool = MagicMock()
        worker_engines = [MagicMock(), MagicMock()]
        pool.engines.return_value = worker_engines
        MockWorkerPool.return_value = pool

        ext.set_worker('alt1', 'pw1')
        ext.set_worker('alt2', 'pw2')

        with patch.object(ext, 'get_total_count', return_value=None), \
             patch('instat.parallel.parallel_extract',
                   return_value=['u1', 'u2']) as mock_pe:
            result = ext.get_followers_parallel('target', workers=2)

        mock_pe.assert_called_once()
        call_kwargs = mock_pe.call_args.kwargs
        self.assertEqual(call_kwargs.get('preloaded_engines'), worker_engines)
        self.assertEqual(result, ['u1', 'u2'])

    def test_parallel_without_pool_passes_none(self):
        """Without set_worker, the parallel call must not force the pool path."""
        ext = self._mk_extractor()

        with patch.object(ext, 'get_total_count', return_value=None), \
             patch('instat.parallel.parallel_extract',
                   return_value=['a']) as mock_pe:
            ext.get_followers_parallel('target', workers=2)

        call_kwargs = mock_pe.call_args.kwargs
        self.assertIsNone(call_kwargs.get('preloaded_engines'))

    @patch('instat.extractor.WorkerPool')
    def test_quit_clears_workers(self, MockWorkerPool):
        ext = self._mk_extractor()
        pool = MagicMock()
        MockWorkerPool.return_value = pool

        ext.set_worker('alt1', 'pw1')
        ext._engine_manager = MagicMock()  # keep quit() from crashing
        ext.quit()

        pool.clear.assert_called_once()


if __name__ == '__main__':
    unittest.main()
