"""Testes dos fixes de performance PERF-01."""
import unittest
from unittest.mock import MagicMock, patch


class TestFix1SpinnerSkipWhenAbsent(unittest.TestCase):
    """FIX 1: se LOADING_SPINNER não existir no DOM, não faz WebDriverWait."""

    @patch('instat.utils.human_delay', return_value=0)
    @patch('instat.utils.WebDriverWait')
    def test_no_spinner_no_webdriverwait_called_with_old_5s(self, MockWait, _hd):
        from instat.utils import Utils
        driver = MagicMock()
        driver.find_elements.return_value = []  # no spinner in DOM

        with patch.object(Utils, 'batch_read_text', return_value=set()):
            Utils.wait_for_new_profiles(
                driver, scrollable_element=MagicMock(),
                profile_selector='span._ap3a',
                existing_profiles=set(),
                wait_interval=0, additional_scroll_attempts=1,
            )

        # Assert that NO WebDriverWait call used the old hardcoded 5s timeout.
        for call in MockWait.call_args_list:
            args = call.args
            if len(args) >= 2:
                timeout_arg = args[1]
                self.assertNotEqual(
                    timeout_arg, 5,
                    "No WebDriverWait should use timeout=5 (old spinner wait)"
                )

    @patch('instat.utils.human_delay', return_value=0)
    def test_spinner_check_uses_find_elements_first(self, _hd):
        """Spinner check should call find_elements BEFORE WebDriverWait."""
        from instat.utils import Utils
        driver = MagicMock()
        driver.find_elements.return_value = []

        with patch.object(Utils, 'batch_read_text', return_value=set()):
            Utils.wait_for_new_profiles(
                driver, scrollable_element=MagicMock(),
                profile_selector='span._ap3a',
                existing_profiles=set(),
                wait_interval=0, additional_scroll_attempts=1,
            )

        # find_elements was called at least once (to check spinner presence)
        self.assertTrue(driver.find_elements.called)


class TestFix2FirefoxPreferences(unittest.TestCase):
    """FIX 2: Firefox options desabilitam imagens + outras otimizações."""

    @patch('instat.login.Service')
    @patch('instat.login.GeckoDriverManager')
    @patch('instat.login.webdriver.Firefox')
    @patch('instat.login.webdriver.FirefoxOptions')
    def test_image_loading_disabled(self, MockOpts, _ff, _gd, _svc):
        _gd.return_value.install.return_value = '/fake/gecko'
        opts_instance = MockOpts.return_value
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login._base_url = 'https://www.instagram.com'
        login.init_driver(headless=True)

        # Verify set_preference was called with image=2 (block images)
        prefs = [call.args for call in opts_instance.set_preference.call_args_list]
        pref_dict = {name: value for (name, value) in prefs}
        self.assertEqual(pref_dict.get("permissions.default.image"), 2)

    @patch('instat.login.Service')
    @patch('instat.login.GeckoDriverManager')
    @patch('instat.login.webdriver.Firefox')
    @patch('instat.login.webdriver.FirefoxOptions')
    def test_telemetry_disabled(self, MockOpts, _ff, _gd, _svc):
        _gd.return_value.install.return_value = '/fake/gecko'
        opts_instance = MockOpts.return_value
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login._base_url = 'https://www.instagram.com'
        login.init_driver(headless=True)

        prefs = [call.args for call in opts_instance.set_preference.call_args_list]
        pref_dict = {name: value for (name, value) in prefs}
        self.assertEqual(pref_dict.get("toolkit.telemetry.enabled"), False)
        self.assertEqual(pref_dict.get("datareporting.healthreport.uploadEnabled"), False)


class TestFix3SessionRestoreValidation(unittest.TestCase):
    """FIX 3: _try_restore_session valida sessionid cookie antes de confiar."""

    def test_restore_fails_without_sessionid_cookie(self):
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login._base_url = 'https://www.instagram.com'
        driver = MagicMock()
        driver.current_url = 'https://www.instagram.com/'
        driver.get_cookie.return_value = None  # no sessionid
        result = login._try_restore_session(driver, [{'name': 'x', 'value': 'y'}])
        self.assertFalse(result)
        driver.delete_all_cookies.assert_called()

    def test_restore_succeeds_with_sessionid(self):
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login._base_url = 'https://www.instagram.com'
        driver = MagicMock()
        driver.current_url = 'https://www.instagram.com/'
        driver.get_cookie.return_value = {'name': 'sessionid', 'value': 'abc'}
        result = login._try_restore_session(driver, [{'name': 'sessionid', 'value': 'abc'}])
        self.assertTrue(result)

    def test_restore_fails_when_all_cookies_fail_to_add(self):
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login._base_url = 'https://www.instagram.com'
        driver = MagicMock()
        driver.add_cookie.side_effect = Exception("domain mismatch")
        result = login._try_restore_session(driver, [{'name': 'x', 'value': 'y'}])
        self.assertFalse(result)
        # refresh should NOT be called when zero cookies added
        driver.refresh.assert_not_called()

    def test_restore_fails_when_redirected_to_login(self):
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login._base_url = 'https://www.instagram.com'
        driver = MagicMock()
        driver.current_url = 'https://www.instagram.com/accounts/login/'
        result = login._try_restore_session(driver, [{'name': 'x', 'value': 'y'}])
        self.assertFalse(result)
        driver.delete_all_cookies.assert_called()


class TestFix4SaveLoginDismissSkipped(unittest.TestCase):
    """FIX 4: dismiss_save_login_modal chamado UMA VEZ, skip em navegações subsequentes."""

    @patch('instat.engines.selenium_engine.Utils')
    def test_dismiss_called_once(self, MockUtils):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get_all.return_value = [
            "//a[contains(@href, '/followers/')]"
        ]
        eng._login_obj = MagicMock(close_keywords=['not now'])
        MockUtils.find_element_with_fallback.return_value = MagicMock()

        eng._navigate_and_get_link('p1', 'followers')
        eng._navigate_and_get_link('p2', 'followers')
        eng._navigate_and_get_link('p3', 'followers')

        # dismiss_save_login_modal chamado apenas 1 vez
        self.assertEqual(MockUtils.dismiss_save_login_modal.call_count, 1)

    def test_flag_initialized_false(self):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        self.assertFalse(eng._save_login_dismissed)


class TestFix5BatchReadText(unittest.TestCase):
    """FIX 5: batch_read_text faz 1 IPC em vez de N."""

    def test_batch_read_returns_set(self):
        from instat.utils import Utils
        driver = MagicMock()
        driver.execute_script.return_value = ['alice', 'bob', '', 'carol']
        result = Utils.batch_read_text(driver, 'span._ap3a')
        self.assertEqual(result, {'alice', 'bob', 'carol'})
        driver.execute_script.assert_called_once()

    def test_batch_read_fallback_on_js_error(self):
        from instat.utils import Utils
        driver = MagicMock()
        driver.execute_script.side_effect = Exception("JS failed")
        fake_elem = MagicMock()
        fake_elem.text = 'alice'
        driver.find_elements.return_value = [fake_elem]
        result = Utils.batch_read_text(driver, 'span')
        self.assertEqual(result, {'alice'})

    def test_batch_read_empty_returns_empty_set(self):
        from instat.utils import Utils
        driver = MagicMock()
        driver.execute_script.return_value = []
        result = Utils.batch_read_text(driver, 'span')
        self.assertEqual(result, set())

    def test_batch_read_none_returns_empty_set(self):
        from instat.utils import Utils
        driver = MagicMock()
        driver.execute_script.return_value = None
        result = Utils.batch_read_text(driver, 'span')
        self.assertEqual(result, set())


if __name__ == '__main__':
    unittest.main()
