import sys
import unittest
from unittest.mock import MagicMock, patch

from instat.engines.base import BaseEngine
from instat.engines.playwright_engine import PlaywrightEngine
from instat.exceptions import BlockedError


class TestPlaywrightEngine(unittest.TestCase):

    def test_implements_base_engine(self):
        self.assertTrue(issubclass(PlaywrightEngine, BaseEngine))

    def test_name_includes_browser_type(self):
        self.assertEqual(PlaywrightEngine(browser_type='chromium').name, 'playwright-chromium')
        self.assertEqual(PlaywrightEngine(browser_type='webkit').name, 'playwright-webkit')
        self.assertEqual(PlaywrightEngine(browser_type='firefox').name, 'playwright-firefox')

    def test_invalid_browser_type_raises(self):
        with self.assertRaises(ValueError):
            PlaywrightEngine(browser_type='invalid')

    def test_is_available_false_without_playwright(self):
        # Force ImportError by removing playwright from sys.modules and blocking import
        original = {k: sys.modules.get(k) for k in ('playwright', 'playwright_stealth')}
        sys.modules['playwright'] = None  # None triggers ImportError on import
        sys.modules['playwright_stealth'] = None
        try:
            e = PlaywrightEngine()
            self.assertFalse(e.is_available)
        finally:
            for k, v in original.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_is_available_true_when_playwright_present(self):
        mock_pw = MagicMock()
        mock_stealth = MagicMock()
        with patch.dict(sys.modules, {
            'playwright': mock_pw,
            'playwright_stealth': mock_stealth
        }):
            e = PlaywrightEngine()
            self.assertTrue(e.is_available)

    def test_quit_no_browser_is_safe(self):
        e = PlaywrightEngine()
        e.quit()  # should not raise

    def test_quit_closes_browser_context_playwright(self):
        e = PlaywrightEngine()
        e._browser = MagicMock()
        e._context = MagicMock()
        e._playwright = MagicMock()
        e.quit()
        e._context.close.assert_called_once()
        e._browser.close.assert_called_once()
        e._playwright.stop.assert_called_once()

    def test_extract_without_login_raises_blocked(self):
        e = PlaywrightEngine()
        e._page = None
        with self.assertRaises(BlockedError):
            e.extract('profile', 'followers')

    def test_extract_invalid_list_type_raises(self):
        e = PlaywrightEngine()
        e._page = MagicMock()
        with self.assertRaises(ValueError):
            e.extract('profile', 'invalid_type')

    def test_login_imports_stealth(self):
        # Mock sync_playwright and stealth_sync; verify stealth_sync is called on the page
        mock_page = MagicMock()
        mock_page.url = 'https://www.instagram.com/'

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_context.cookies.return_value = []

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_browser_launcher = MagicMock()
        mock_browser_launcher.launch.return_value = mock_browser

        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium = mock_browser_launcher

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_instance

        mock_stealth_sync = MagicMock()

        pw_sync_module = MagicMock()
        pw_sync_module.sync_playwright = mock_sync_pw

        pw_stealth_module = MagicMock()
        pw_stealth_module.stealth_sync = mock_stealth_sync

        with patch.dict(sys.modules, {
            'playwright': MagicMock(),
            'playwright.sync_api': pw_sync_module,
            'playwright_stealth': pw_stealth_module,
        }):
            e = PlaywrightEngine(browser_type='chromium')
            # Mock SessionCache to skip cache restore path
            e._session_cache = MagicMock()
            e._session_cache.load.return_value = None

            result = e.login('user', 'pass')
            self.assertTrue(result)
            mock_stealth_sync.assert_called_once_with(mock_page)

    def test_login_raises_blocked_on_checkpoint_url(self):
        mock_page = MagicMock()
        mock_page.url = 'https://www.instagram.com/challenge/abc123'

        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_context.cookies.return_value = []

        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_browser_launcher = MagicMock()
        mock_browser_launcher.launch.return_value = mock_browser

        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium = mock_browser_launcher

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_instance

        pw_sync_module = MagicMock()
        pw_sync_module.sync_playwright = mock_sync_pw

        pw_stealth_module = MagicMock()
        pw_stealth_module.stealth_sync = MagicMock()

        with patch.dict(sys.modules, {
            'playwright': MagicMock(),
            'playwright.sync_api': pw_sync_module,
            'playwright_stealth': pw_stealth_module,
        }):
            e = PlaywrightEngine(browser_type='chromium')
            e._session_cache = MagicMock()
            e._session_cache.load.return_value = None
            with self.assertRaises(BlockedError):
                e.login('user', 'pass')


if __name__ == "__main__":
    unittest.main()
