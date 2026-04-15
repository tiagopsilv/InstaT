import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from instat.proxy import ProxyPool, ProxyState


class TestProxyState(unittest.TestCase):

    def test_default_is_available(self):
        p = ProxyState(url='http://x:1')
        self.assertTrue(p.is_available)

    @patch('instat.proxy.time')
    def test_cooldown_blocks_availability(self, mock_time):
        mock_time.time.return_value = 1000.0
        p = ProxyState(url='http://x:1', cooldown_until=2000.0)
        self.assertFalse(p.is_available)

    @patch('instat.proxy.time')
    def test_cooldown_expired_makes_available(self, mock_time):
        mock_time.time.return_value = 3000.0
        p = ProxyState(url='http://x:1', cooldown_until=2000.0)
        self.assertTrue(p.is_available)


class TestProxyPool(unittest.TestCase):

    def test_round_robin_rotation(self):
        pool = ProxyPool(['http://p1:8080', 'http://p2:8080', 'http://p3:8080'])
        self.assertEqual(pool.get_next(), 'http://p1:8080')
        self.assertEqual(pool.get_next(), 'http://p2:8080')
        self.assertEqual(pool.get_next(), 'http://p3:8080')
        self.assertEqual(pool.get_next(), 'http://p1:8080')

    def test_empty_pool_returns_none(self):
        pool = ProxyPool([])
        self.assertIsNone(pool.get_next())
        self.assertEqual(pool.available_count, 0)
        self.assertEqual(pool.total_count, 0)

    def test_mark_failed_starts_cooldown(self):
        pool = ProxyPool(['http://p1:8080', 'http://p2:8080'])
        pool.mark_failed('http://p1:8080')
        self.assertEqual(pool.available_count, 1)
        self.assertEqual(pool.get_next(), 'http://p2:8080')
        self.assertEqual(pool.get_next(), 'http://p2:8080')

    @patch('instat.proxy.time')
    def test_cooldown_expires_and_returns(self, mock_time):
        mock_time.time.return_value = 1000.0
        pool = ProxyPool(['http://p1:8080'])
        pool.mark_failed('http://p1:8080')  # cooldown_until = 2800
        self.assertEqual(pool.available_count, 0)
        mock_time.time.return_value = 3000.0
        self.assertEqual(pool.available_count, 1)
        self.assertEqual(pool.get_next(), 'http://p1:8080')

    def test_get_next_none_when_all_in_cooldown(self):
        pool = ProxyPool(['http://p1:8080', 'http://p2:8080'])
        pool.mark_failed('http://p1:8080')
        pool.mark_failed('http://p2:8080')
        self.assertIsNone(pool.get_next())

    def test_mark_success_resets_state(self):
        pool = ProxyPool(['http://p1:8080'])
        pool.mark_failed('http://p1:8080')
        pool.mark_failed('http://p1:8080')
        state = pool._proxies[0]
        self.assertEqual(state.fail_count, 2)
        pool.mark_success('http://p1:8080')
        self.assertEqual(state.fail_count, 0)
        self.assertEqual(state.cooldown_until, 0.0)

    @patch('instat.proxy.time')
    def test_mark_failed_custom_cooldown(self, mock_time):
        mock_time.time.return_value = 1000.0
        pool = ProxyPool(['http://p1:8080'])
        pool.mark_failed('http://p1:8080', cooldown=60)
        self.assertEqual(pool._proxies[0].cooldown_until, 1060.0)

    def test_mark_failed_unknown_url_is_safe(self):
        pool = ProxyPool(['http://p1:8080'])
        pool.mark_failed('http://unknown:9999')
        self.assertEqual(pool.available_count, 1)

    def test_mark_success_unknown_url_is_safe(self):
        pool = ProxyPool(['http://p1:8080'])
        pool.mark_success('http://unknown:9999')
        self.assertEqual(pool._proxies[0].fail_count, 0)

    def test_from_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write('http://p1:8080\n')
            f.write('http://p2:8080\n')
            f.write('\n')
            f.write('  http://p3:8080  \n')
            tmp_path = f.name
        try:
            pool = ProxyPool.from_file(tmp_path)
            self.assertEqual(pool.total_count, 3)
            self.assertEqual(pool.get_next(), 'http://p1:8080')
            self.assertEqual(pool.get_next(), 'http://p2:8080')
            self.assertEqual(pool.get_next(), 'http://p3:8080')
        finally:
            Path(tmp_path).unlink()

    def test_constructor_filters_empty_strings(self):
        pool = ProxyPool(['http://p1:8080', '', '  ', None, 'http://p2:8080'])
        self.assertEqual(pool.total_count, 2)


class TestEngineManagerProxyPool(unittest.TestCase):
    """Integration: EngineManager accepts and stores proxy_pool."""

    def test_engine_manager_stores_proxy_pool(self):
        from instat.engines.engine_manager import EngineManager
        from .test_engine_manager import MockEngine
        pool = ProxyPool(['http://p1:8080'])
        mgr = EngineManager([MockEngine('m1')], proxy_pool=pool)
        self.assertIs(mgr._proxy_pool, pool)

    def test_engine_manager_default_no_proxy_pool(self):
        from instat.engines.engine_manager import EngineManager
        from .test_engine_manager import MockEngine
        mgr = EngineManager([MockEngine('m1')])
        self.assertIsNone(mgr._proxy_pool)


class TestSeleniumEngineProxy(unittest.TestCase):

    def test_selenium_engine_stores_proxy(self):
        from instat.engines.selenium_engine import SeleniumEngine
        e = SeleniumEngine(proxy='http://p1:8080')
        self.assertEqual(e._proxy, 'http://p1:8080')

    def test_selenium_engine_default_no_proxy(self):
        from instat.engines.selenium_engine import SeleniumEngine
        e = SeleniumEngine()
        self.assertIsNone(e._proxy)


if __name__ == "__main__":
    unittest.main()
