"""SessionRestorer + FormLogin — unit tests for the extracted
login-phase classes."""
import unittest
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import TimeoutException, WebDriverException

from instat.login_flow import FormLogin, SessionRestorer


# ------------------- SessionRestorer ---------------------

class TestSessionRestorer(unittest.TestCase):

    def _mk(self):
        return SessionRestorer(base_url="https://www.instagram.com")

    def test_empty_cookies_returns_false(self):
        r = self._mk()
        self.assertFalse(r.attempt(MagicMock(), []))

    def test_all_add_cookie_fail_returns_false(self):
        r = self._mk()
        driver = MagicMock()
        driver.add_cookie.side_effect = Exception("bad cookie")
        self.assertFalse(r.attempt(
            driver, [{'name': 'x', 'value': 'y'}],
        ))

    def test_success_with_valid_session(self):
        r = self._mk()
        driver = MagicMock()
        driver.current_url = "https://www.instagram.com/"
        driver.get_cookie.return_value = {'name': 'sessionid', 'value': 'abc'}
        self.assertTrue(r.attempt(
            driver, [{'name': 'sessionid', 'value': 'abc'}],
        ))

    def test_fail_when_redirected_to_login(self):
        r = self._mk()
        driver = MagicMock()
        driver.current_url = "https://www.instagram.com/accounts/login/"
        self.assertFalse(r.attempt(
            driver, [{'name': 'sessionid', 'value': 'x'}],
        ))
        driver.delete_all_cookies.assert_called()

    def test_fail_when_no_sessionid(self):
        r = self._mk()
        driver = MagicMock()
        driver.current_url = "https://www.instagram.com/"
        driver.get_cookie.return_value = None
        self.assertFalse(r.attempt(
            driver, [{'name': 'x', 'value': 'y'}],
        ))
        driver.delete_all_cookies.assert_called()

    def test_exception_during_flow_returns_false(self):
        r = self._mk()
        driver = MagicMock()
        driver.get.side_effect = RuntimeError("boom")
        self.assertFalse(r.attempt(
            driver, [{'name': 'sessionid', 'value': 'x'}],
        ))


# ---------------------- FormLogin ------------------------

class TestFormLogin(unittest.TestCase):

    def _mk(self):
        selectors = MagicMock()
        selectors.get.side_effect = lambda key: {
            'LOGIN_USERNAME_INPUT': "input[name='username']",
            'LOGIN_PASSWORD_INPUT': "input[name='password']",
            'LOGIN_BUTTON_CANDIDATE': "//button",
        }[key]
        fl = FormLogin(
            selector_loader=selectors,
            base_url="https://www.instagram.com",
            timeout=1,
        )
        return fl, selectors

    def test_login_url_uses_base_url(self):
        fl, _ = self._mk()
        self.assertEqual(
            fl.login_url,
            "https://www.instagram.com/accounts/login/",
        )

    def test_execute_happy_path(self):
        fl, _ = self._mk()
        driver = MagicMock()
        u_input = MagicMock()
        p_input = MagicMock()
        with patch('instat.login_flow.WebDriverWait') as wait, \
             patch('instat.login_flow.Utils'):
            wait.return_value.until.side_effect = [u_input, p_input, True]
            fl.execute(driver, 'u', 'pw')
        u_input.send_keys.assert_any_call('u')
        p_input.send_keys.assert_any_call('pw')

    def test_open_fails_raises(self):
        fl, _ = self._mk()
        driver = MagicMock()
        driver.get.side_effect = WebDriverException("net down")
        with self.assertRaises(Exception) as ctx:
            fl.execute(driver, 'u', 'pw')
        self.assertIn("Failed to load", str(ctx.exception))

    def test_form_fields_timeout_raises(self):
        fl, _ = self._mk()
        driver = MagicMock()
        with patch('instat.login_flow.WebDriverWait') as wait, \
             patch('instat.login_flow.Utils'):
            wait.return_value.until.side_effect = TimeoutException()
            with self.assertRaises(Exception) as ctx:
                fl.execute(driver, 'u', 'pw')
        self.assertIn("Timeout", str(ctx.exception))

    def test_send_keys_fails_raises(self):
        fl, _ = self._mk()
        driver = MagicMock()
        u_input = MagicMock()
        u_input.send_keys.side_effect = WebDriverException("no focus")
        with patch('instat.login_flow.WebDriverWait') as wait, \
             patch('instat.login_flow.Utils'):
            wait.return_value.until.side_effect = [u_input, MagicMock(), True]
            with self.assertRaises(Exception) as ctx:
                fl.execute(driver, 'u', 'pw')
        self.assertIn("enter credentials", str(ctx.exception).lower())

    def test_fallback_click_no_matching_button_raises(self):
        fl, _ = self._mk()
        driver = MagicMock()
        bad_button = MagicMock()
        bad_button.get_attribute.return_value = "Cancel"
        driver.find_elements.return_value = [bad_button]
        with patch('instat.login_flow.WebDriverWait') as wait, \
             patch('instat.login_flow.Utils'), \
             patch('instat.login_flow.human_delay'):
            # First two succeed (form fields), third times out (no redirect)
            wait.return_value.until.side_effect = [
                MagicMock(), MagicMock(), TimeoutException(),
            ]
            with self.assertRaises(Exception) as ctx:
                fl.execute(driver, 'u', 'pw')
        self.assertIn("fallback", str(ctx.exception).lower())

    def test_fallback_click_succeeds(self):
        fl, _ = self._mk()
        driver = MagicMock()
        good_button = MagicMock()
        good_button.get_attribute.return_value = "Log in"
        driver.find_elements.return_value = [good_button]
        u_input = MagicMock()
        p_input = MagicMock()
        with patch('instat.login_flow.WebDriverWait') as wait, \
             patch('instat.login_flow.Utils'), \
             patch('instat.login_flow.human_delay'):
            # Sequence: username field, password field, initial-redirect TimeoutException,
            # then post-click-redirect, then readyState check
            wait.return_value.until.side_effect = [
                u_input, p_input, TimeoutException(),
                True, True,
            ]
            fl.execute(driver, 'u', 'pw')
        good_button.click.assert_called_once()


class TestInstaLoginOrchestrator(unittest.TestCase):
    """InstaLogin.login orchestration — verifies the phase calls
    happen in order and the collaborators are wired."""

    def test_login_uses_cookie_restore_first(self):
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login.driver = MagicMock()
        login.username = 'u'
        login.password = 'pw'
        login._base_url = "https://www.instagram.com"
        login._session_cache = MagicMock()
        login._session_cache.load.return_value = [{'name': 'x', 'value': 'y'}]
        restorer = MagicMock()
        restorer.attempt.return_value = True
        login._session_restorer = restorer

        result = login.login()
        self.assertTrue(result)
        # Didn't need to hit form login
        restorer.attempt.assert_called_once()


if __name__ == '__main__':
    unittest.main()
