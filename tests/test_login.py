import unittest
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import TimeoutException

from instat.login import InstaLogin


class TestInstaLogin(unittest.TestCase):
    """
    Unit tests for the InstaLogin class. These tests verify login behavior including:
    - Timeout handling
    - Fallback login attempt
    - Invalid credentials
    - Successful login flow
    """

    @classmethod
    def setUpClass(cls):
        # Define test credentials and selectors
        cls.username = "your_username"
        cls.correct_password = "your_password"
        cls.wrong_password = "wrongpass"
        cls.mock_selector_map = {
            "LOGIN_USERNAME_INPUT": "input[name='username']",
            "LOGIN_PASSWORD_INPUT": "input[name='password']",
            "LOGIN_BUTTON_CANDIDATE": "//button"
        }
        cls.login_url = "https://www.instagram.com/accounts/login/"

    def setUp(self):
        self.username = self.__class__.username
        self.correct_password = self.__class__.correct_password
        self.mock_selector_map = self.__class__.mock_selector_map
        self.login_url = self.__class__.login_url

        # Patch Firefox WebDriver and SelectorLoader
        self.driver_patcher = patch("instat.login.webdriver.Firefox")
        self.mock_driver = self.driver_patcher.start().return_value
        self.mock_driver.current_url = self.login_url

        selector_loader_patcher = patch("instat.login.SelectorLoader")
        mock_loader_class = selector_loader_patcher.start()
        self.addCleanup(selector_loader_patcher.stop)
        mock_loader_instance = MagicMock()
        mock_loader_instance.get.side_effect = lambda k: self.mock_selector_map[k]
        mock_loader_class.return_value = mock_loader_instance

        self.addCleanup(self.driver_patcher.stop)

        # Create login instance
        self.client = InstaLogin(self.username, self.correct_password, headless=True)
        self.driver = self.client.driver

    def tearDown(self):
        # Clean up browser instance
        try:
            self.driver.quit()
        except Exception:
            pass

    @patch("instat.login_flow.WebDriverWait")
    def test_timeout_wait_for_form_fields(self, mock_wait):
        """
        Should raise an exception when login form fields do not appear.
        """
        mock_wait.return_value.until.side_effect = TimeoutException()

        with self.assertRaises(Exception) as context:
            self.client.login()

        self.assertIn("Timeout waiting for login form elements", str(context.exception))

    @patch("instat.login_flow.WebDriverWait")
    def test_successful_login(self, mock_wait):
        """
        Should return True when login completes successfully.
        """
        # After login redirect, URL must change to feed
        self.mock_driver.current_url = "https://www.instagram.com/"
        self.mock_driver.page_source = "<html></html>"
        self.mock_driver.title = "Instagram"
        username_mock = MagicMock()
        password_mock = MagicMock()
        mock_wait.return_value.until.side_effect = [username_mock, password_mock, True]

        result = self.client.login()

        self.assertTrue(result)
        username_mock.send_keys.assert_any_call(self.username)
        password_mock.send_keys.assert_any_call(self.correct_password)

    @patch("instat.login_flow.WebDriverWait")
    def test_invalid_credentials(self, mock_wait):
        """
        Should raise an exception for incorrect credentials.
        """
        username_mock = MagicMock()
        password_mock = MagicMock()
        mock_wait.return_value.until.side_effect = [username_mock, password_mock, TimeoutException()]

        self.client.password = self.wrong_password

        with self.assertRaises(Exception) as context:
            self.client.login()

        self.assertIn("login", str(context.exception).lower())

    @patch("instat.login_flow.WebDriverWait")
    def test_fallback_button_click(self, mock_wait):
        # Simulate fallback login button click when RETURN key doesn't work
        mock_driver = MagicMock()
        self.client.driver = mock_driver
        self.client.timeout = 10

        # After login, URL must be feed for _check_account_blocked to pass
        mock_driver.current_url = "https://www.instagram.com/"
        mock_driver.page_source = "<html></html>"
        mock_driver.title = "Instagram"

        # Username and password fields found
        mock_username_input = MagicMock()
        mock_password_input = MagicMock()

        # After clicking login, simulate the URL changes, which satisfies WebDriverWait
        mock_wait.return_value.until.side_effect = [mock_username_input, mock_password_input, True]

        result = self.client.login()
        self.assertTrue(result)

class TestInstaLoginWebdriverFactory(unittest.TestCase):
    """init_driver should bypass Firefox setup when a webdriver_factory
    is injected — used for remote browsers (Bright Data, Browserless,
    Selenium Grid)."""

    def test_init_driver_uses_factory(self):
        # Mock SelectorLoader so InstaLogin.__init__ doesn't read disk.
        with patch("instat.login.SelectorLoader") as mock_loader_class:
            mock_loader_class.return_value = MagicMock()
            with patch("instat.login.webdriver.Firefox") as mock_firefox:
                fake_driver = MagicMock(name="remote_driver")
                factory = MagicMock(return_value=fake_driver)
                client = InstaLogin(
                    "u", "p", headless=False,
                    webdriver_factory=factory,
                )
                # Factory was called with the headless flag InstaLogin received.
                factory.assert_called_once_with(False)
                # Firefox local setup was skipped entirely.
                mock_firefox.assert_not_called()
                self.assertIs(client.driver, fake_driver)
                # Stealth tweaks ran on the injected driver.
                fake_driver.set_window_size.assert_called_once_with(375, 667)
                fake_driver.execute_script.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
