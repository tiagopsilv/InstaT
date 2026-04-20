"""EngineManager: when a secondary engine (httpx) needs login and the
primary (selenium) is already logged in, inject cookies via
login_with_cookies instead of form-login."""
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from instat.engines.engine_manager import EngineManager
from instat.exceptions import BlockedError


def _fake_selenium(cookies=None, name='selenium'):
    eng = MagicMock()
    eng.name = name
    eng._driver = MagicMock()
    eng._driver.get_cookies.return_value = cookies or [
        {'name': 'sessionid', 'value': 'abc123'},
        {'name': 'csrftoken', 'value': 'xyz'},
    ]
    eng.extract.side_effect = BlockedError("partial coverage 500/1000")
    return eng


def _fake_httpx(name='httpx'):
    eng = MagicMock()
    eng.name = name
    # No _driver attribute — don't let MagicMock auto-create one
    del eng._driver
    eng.login_with_cookies = MagicMock(return_value=True)
    eng.login = MagicMock()
    eng.extract.return_value = {'user1', 'user2', 'user3'}
    return eng


class TestCookieHandoff(unittest.TestCase):

    def test_httpx_receives_cookies_not_form_login(self):
        selenium = _fake_selenium()
        httpx = _fake_httpx()

        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager(
                [selenium, httpx],
                default_credentials=('u', 'p'),
            )
            # Simulate selenium already logged in (facade did it)
            mgr._logged_in_engines.add(id(selenium))

            # Patch checkpoint to use tmp dir, avoid persistent state
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                result = mgr.extract('target', 'followers')
            finally:
                em.ExtractionCheckpoint = original_cls

        httpx.login_with_cookies.assert_called_once()
        handoff_arg = httpx.login_with_cookies.call_args[0][0]
        self.assertEqual(
            handoff_arg,
            [{'name': 'sessionid', 'value': 'abc123'},
             {'name': 'csrftoken', 'value': 'xyz'}],
        )
        httpx.login.assert_not_called()
        # result should contain httpx's collected profiles
        self.assertEqual(set(result), {'user1', 'user2', 'user3'})

    def test_handoff_failure_falls_back_to_form_login(self):
        selenium = _fake_selenium()
        httpx = _fake_httpx()
        # Make handoff raise — caller should fall back
        httpx.login_with_cookies.side_effect = RuntimeError("cookies invalid")

        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager(
                [selenium, httpx],
                default_credentials=('u', 'p'),
            )
            mgr._logged_in_engines.add(id(selenium))
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                mgr.extract('target', 'followers')
            finally:
                em.ExtractionCheckpoint = original_cls

        httpx.login_with_cookies.assert_called_once()
        # Should have fallen back to form login
        httpx.login.assert_called_once_with('u', 'p')

    def test_no_selenium_driver_no_handoff(self):
        """Se nenhum engine tem _driver, nada de handoff — form login normal."""
        # A "selenium" mock without a _driver (e.g. not logged in yet)
        selenium = MagicMock()
        selenium.name = 'selenium'
        del selenium._driver
        selenium.extract.side_effect = BlockedError("x")

        httpx = _fake_httpx()

        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager(
                [selenium, httpx],
                default_credentials=('u', 'p'),
            )
            mgr._logged_in_engines.add(id(selenium))
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                mgr.extract('target', 'followers')
            finally:
                em.ExtractionCheckpoint = original_cls

        httpx.login_with_cookies.assert_not_called()
        httpx.login.assert_called_once_with('u', 'p')

    def test_engine_without_login_with_cookies_skipped(self):
        """PlaywrightEngine (sem login_with_cookies) não deve receber handoff."""
        selenium = _fake_selenium()
        playwright = MagicMock()
        playwright.name = 'playwright'
        del playwright.login_with_cookies  # no such method
        del playwright._driver
        playwright.extract.return_value = set()

        with tempfile.TemporaryDirectory() as tmp:
            mgr = EngineManager(
                [selenium, playwright],
                default_credentials=('u', 'p'),
            )
            mgr._logged_in_engines.add(id(selenium))
            import instat.engines.engine_manager as em
            original_cls = em.ExtractionCheckpoint
            em.ExtractionCheckpoint = lambda p, lt: original_cls(
                p, lt, checkpoint_dir=tmp
            )
            try:
                mgr.extract('target', 'followers')
            finally:
                em.ExtractionCheckpoint = original_cls

        playwright.login.assert_called_once_with('u', 'p')


class TestHandoffDiagnosticLogging(unittest.TestCase):
    """Every early-return path in _try_cookie_handoff must log."""

    def test_no_login_with_cookies_logs_reason(self):
        from instat.engines.engine_manager import EngineManager
        import instat.engines.engine_manager as em_mod

        eng_target = MagicMock()
        eng_target.name = 'playwright'
        del eng_target.login_with_cookies
        eng_source = MagicMock()
        eng_source.name = 'selenium'
        eng_source._driver = MagicMock()

        mgr = EngineManager([eng_source, eng_target])
        with patch.object(em_mod, 'logger') as mock_logger:
            result = mgr._try_cookie_handoff(eng_target)
        self.assertFalse(result)
        # Must have logged SOMETHING explaining why
        debug_msgs = [str(c.args[0]) if c.args else '' for c in mock_logger.debug.call_args_list]
        self.assertTrue(
            any('no login_with_cookies' in m for m in debug_msgs),
            f"expected 'no login_with_cookies' log, got {debug_msgs}",
        )

    def test_no_peer_driver_logs_reason(self):
        from instat.engines.engine_manager import EngineManager
        import instat.engines.engine_manager as em_mod

        eng_target = MagicMock()
        eng_target.name = 'httpx'
        eng_target.login_with_cookies = MagicMock()
        eng_source = MagicMock()
        eng_source.name = 'selenium'
        del eng_source._driver  # no live driver

        mgr = EngineManager([eng_source, eng_target])
        with patch.object(em_mod, 'logger') as mock_logger:
            result = mgr._try_cookie_handoff(eng_target)
        self.assertFalse(result)
        eng_target.login_with_cookies.assert_not_called()
        debug_msgs = [str(c.args[0]) if c.args else '' for c in mock_logger.debug.call_args_list]
        self.assertTrue(
            any('no live _driver' in m or 'no peer engine' in m for m in debug_msgs),
            f"expected diagnostic log, got {debug_msgs}",
        )

    def test_real_httpx_engine_has_login_with_cookies(self):
        """Regression: HttpxEngine exposes login_with_cookies directly.
        Earlier analysis suspected hasattr was returning False — this
        nails down the real class contract."""
        try:
            from instat.engines.httpx_engine import HttpxEngine
        except ImportError:
            self.skipTest("httpx not installed")
        eng = HttpxEngine()
        self.assertTrue(hasattr(eng, 'login_with_cookies'))
        self.assertTrue(callable(eng.login_with_cookies))


if __name__ == '__main__':
    unittest.main()
