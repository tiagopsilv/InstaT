import tempfile
import unittest
from unittest.mock import MagicMock, patch

from InstaT.session_cache import SessionCache


class TestSessionCache(unittest.TestCase):

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = SessionCache(cache_dir=tmpdir)
            cookies = [{'name': 'sessionid', 'value': 'abc123'}, {'name': 'csrftoken', 'value': 'xyz'}]
            cache.save('testuser', cookies)
            loaded = cache.load('testuser')
            self.assertEqual(loaded, cookies)

    @patch('InstaT.session_cache.time')
    def test_load_expired_returns_none(self, mock_time):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_time.time.return_value = 1000.0
            cache = SessionCache(cache_dir=tmpdir)
            cache.save('testuser', [{'name': 'a', 'value': 'b'}])

            # Advance past max_age (default 3600)
            mock_time.time.return_value = 1000.0 + 3601
            loaded = cache.load('testuser')
            self.assertIsNone(loaded)

    def test_load_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = SessionCache(cache_dir=tmpdir)
            self.assertIsNone(cache.load('nouser'))

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = SessionCache(cache_dir=tmpdir)
            cache.save('testuser', [{'name': 'a', 'value': 'b'}])
            self.assertIsNotNone(cache.load('testuser'))
            cache.clear('testuser')
            self.assertIsNone(cache.load('testuser'))


class TestLoginUsesCache(unittest.TestCase):

    def setUp(self):
        self.mock_selector_map = {
            "LOGIN_USERNAME_INPUT": "input[name='username']",
            "LOGIN_PASSWORD_INPUT": "input[name='password']",
            "LOGIN_BUTTON_CANDIDATE": "//button"
        }

        self.driver_patcher = patch("instat.login.webdriver.Firefox")
        self.mock_driver = self.driver_patcher.start().return_value
        self.mock_driver.current_url = "https://www.instagram.com/"

        self.geckodriver_patcher = patch("instat.login.GeckoDriverManager")
        self.geckodriver_patcher.start()
        self.addCleanup(self.geckodriver_patcher.stop)

        self.service_patcher = patch("instat.login.Service")
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)

        selector_loader_patcher = patch("instat.login.SelectorLoader")
        mock_loader_class = selector_loader_patcher.start()
        self.addCleanup(selector_loader_patcher.stop)
        mock_loader_instance = MagicMock()
        mock_loader_instance.get.side_effect = lambda k: self.mock_selector_map.get(k, "")
        mock_loader_class.return_value = mock_loader_instance

        self.addCleanup(self.driver_patcher.stop)

    def test_login_uses_cache(self):
        """When cache has valid cookies, login should restore session without filling the form."""
        from instat.login import InstaLogin

        mock_cache = MagicMock()
        mock_cache.load.return_value = [{'name': 'sessionid', 'value': 'abc123'}]

        # current_url after restoring cookies should NOT contain /accounts/login
        self.mock_driver.current_url = "https://www.instagram.com/"

        client = InstaLogin('testuser', 'testpass', headless=True, session_cache=mock_cache)
        result = client.login()

        self.assertTrue(result)
        mock_cache.load.assert_called_once_with('testuser')
        self.mock_driver.add_cookie.assert_called_once_with({'name': 'sessionid', 'value': 'abc123'})
        # Username field should NOT have been filled (no form login)
        # We verify by checking driver.get was called with instagram.com (not /accounts/login/)
        self.mock_driver.get.assert_called_once_with('https://www.instagram.com/')

    def test_login_saves_after_success(self):
        """After a normal login (no cache), cookies should be saved."""
        from instat.login import InstaLogin

        mock_cache = MagicMock()
        mock_cache.load.return_value = None  # No cached cookies

        # After login, URL must be feed (not /accounts/login/) for _check_account_blocked
        self.mock_driver.current_url = "https://www.instagram.com/"
        self.mock_driver.get_cookies.return_value = [{'name': 'sessionid', 'value': 'new123'}]
        self.mock_driver.page_source = "<html></html>"
        self.mock_driver.title = "Instagram"

        client = InstaLogin('testuser', 'testpass', headless=True, session_cache=mock_cache)

        with patch("instat.login.WebDriverWait") as mock_wait:
            username_mock = MagicMock()
            password_mock = MagicMock()
            mock_wait.return_value.until.side_effect = [username_mock, password_mock, True]

            result = client.login()

        self.assertTrue(result)
        mock_cache.save.assert_called_once_with('testuser', [{'name': 'sessionid', 'value': 'new123'}])


if __name__ == "__main__":
    unittest.main()
