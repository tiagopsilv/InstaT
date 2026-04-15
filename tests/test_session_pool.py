import unittest
from unittest.mock import patch

from instat.session_pool import Session, SessionPool


class TestSession(unittest.TestCase):

    def test_default_is_available(self):
        s = Session(username='u', password='p')
        self.assertTrue(s.is_available)

    @patch('instat.session_pool.time')
    def test_cooldown_blocks_availability(self, mock_time):
        mock_time.time.return_value = 1000.0
        s = Session(username='u', password='p', cooldown_until=2000.0)
        self.assertFalse(s.is_available)


class TestSessionPool(unittest.TestCase):

    def test_get_available_returns_first(self):
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
        ])
        s = sp.get_available()
        self.assertIsNotNone(s)
        self.assertEqual(s.username, 'a')

    def test_round_robin_rotation(self):
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
            {'username': 'c', 'password': 'pc'},
        ])
        self.assertEqual(sp.get_available().username, 'a')
        self.assertEqual(sp.get_available().username, 'b')
        self.assertEqual(sp.get_available().username, 'c')
        self.assertEqual(sp.get_available().username, 'a')

    def test_mark_blocked_skips_session(self):
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
        ])
        sp.mark_blocked(sp.sessions[0])
        self.assertEqual(sp.get_available().username, 'b')
        self.assertEqual(sp.get_available().username, 'b')
        self.assertEqual(sp.available_count, 1)

    def test_all_blocked_true_when_all_in_cooldown(self):
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
        ])
        for s in sp.sessions:
            sp.mark_blocked(s)
        self.assertTrue(sp.all_blocked())
        self.assertIsNone(sp.get_available())
        self.assertEqual(sp.available_sessions(), [])

    @patch('instat.session_pool.time')
    def test_cooldown_expires(self, mock_time):
        mock_time.time.return_value = 1000.0
        sp = SessionPool([{'username': 'a', 'password': 'p'}])
        sp.mark_blocked(sp.sessions[0])  # cooldown_until = 1000 + 3600 = 4600
        self.assertTrue(sp.all_blocked())
        mock_time.time.return_value = 5000.0
        self.assertFalse(sp.all_blocked())
        self.assertEqual(sp.get_available().username, 'a')

    def test_mark_success_resets_state(self):
        sp = SessionPool([{'username': 'a', 'password': 'p'}])
        sp.mark_blocked(sp.sessions[0])
        sp.mark_blocked(sp.sessions[0])
        self.assertEqual(sp.sessions[0].fail_count, 2)
        sp.mark_success(sp.sessions[0])
        self.assertEqual(sp.sessions[0].fail_count, 0)
        self.assertEqual(sp.sessions[0].cooldown_until, 0.0)

    @patch('instat.session_pool.time')
    def test_different_cooldown_for_meta_vs_rate_limit(self, mock_time):
        mock_time.time.return_value = 1000.0
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
        ])
        sp.mark_blocked(sp.sessions[0], cooldown=SessionPool.DEFAULT_COOLDOWN)
        sp.mark_blocked(sp.sessions[1], cooldown=SessionPool.META_INTERSTITIAL_COOLDOWN)
        self.assertEqual(sp.sessions[0].cooldown_until, 1000.0 + 3600)
        self.assertEqual(sp.sessions[1].cooldown_until, 1000.0 + 21600)

    def test_proxy_pool_assigns_proxies(self):
        from instat.proxy import ProxyPool
        pool = ProxyPool(['http://p1:8080', 'http://p2:8080'])
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
        ], proxy_pool=pool)
        self.assertEqual(sp.sessions[0].proxy, 'http://p1:8080')
        self.assertEqual(sp.sessions[1].proxy, 'http://p2:8080')

    def test_no_proxy_pool_means_no_proxy(self):
        sp = SessionPool([{'username': 'a', 'password': 'p'}])
        self.assertIsNone(sp.sessions[0].proxy)

    def test_empty_accounts_raises(self):
        with self.assertRaises(ValueError):
            SessionPool([])

    def test_missing_username_or_password_raises(self):
        with self.assertRaises(ValueError):
            SessionPool([{'username': 'a'}])
        with self.assertRaises(ValueError):
            SessionPool([{'password': 'p'}])
        with self.assertRaises(ValueError):
            SessionPool([{'username': '', 'password': 'p'}])

    def test_total_count(self):
        sp = SessionPool([
            {'username': 'a', 'password': 'pa'},
            {'username': 'b', 'password': 'pb'},
        ])
        self.assertEqual(sp.total_count, 2)
        self.assertEqual(sp.available_count, 2)


class TestEngineManagerSessionPool(unittest.TestCase):
    """Integration: EngineManager accepts and stores session_pool."""

    def test_engine_manager_stores_session_pool(self):
        from instat.engines.engine_manager import EngineManager
        from .test_engine_manager import MockEngine
        sp = SessionPool([{'username': 'a', 'password': 'p'}])
        mgr = EngineManager([MockEngine('m1')], session_pool=sp)
        self.assertIs(mgr._session_pool, sp)

    def test_engine_manager_default_no_session_pool(self):
        from instat.engines.engine_manager import EngineManager
        from .test_engine_manager import MockEngine
        mgr = EngineManager([MockEngine('m1')])
        self.assertIsNone(mgr._session_pool)

    def test_engine_manager_accepts_both_pools(self):
        from instat.engines.engine_manager import EngineManager
        from instat.proxy import ProxyPool
        from .test_engine_manager import MockEngine
        pool = ProxyPool(['http://p1:8080'])
        sp = SessionPool([{'username': 'a', 'password': 'p'}])
        mgr = EngineManager([MockEngine('m1')], proxy_pool=pool, session_pool=sp)
        self.assertIs(mgr._proxy_pool, pool)
        self.assertIs(mgr._session_pool, sp)


if __name__ == "__main__":
    unittest.main()
