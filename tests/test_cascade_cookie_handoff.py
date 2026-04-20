"""EngineManager: when a secondary engine (httpx) needs login and the
primary (selenium) is already logged in, inject cookies via
login_with_cookies instead of form-login."""
import tempfile
import unittest
from unittest.mock import MagicMock

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


if __name__ == '__main__':
    unittest.main()
