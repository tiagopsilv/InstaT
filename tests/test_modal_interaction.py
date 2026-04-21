"""ModalInteraction — unit tests for the followers/following modal
lifecycle encapsulation."""
import unittest
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException

from instat.exceptions import ProfileNotFoundError
from instat.modal_interaction import ModalInteraction


def _mk(driver=None, selectors=None, dismiss_cb=None):
    d = driver or MagicMock()
    s = selectors or MagicMock()
    return ModalInteraction(
        driver=d, selectors=s, timeout=1,
        base_url="https://www.instagram.com",
        dismiss_save_login=dismiss_cb,
    )


class TestOpen(unittest.TestCase):

    def test_open_navigates_and_returns_link(self):
        driver = MagicMock()
        selectors = MagicMock()
        selectors.get_all.return_value = ["//a[@href*='/followers/']"]
        modal = _mk(driver=driver, selectors=selectors)
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value='link_el',
        ):
            link = modal.open('tiagopsilv', 'followers')
        driver.get.assert_called_once_with(
            "https://www.instagram.com/tiagopsilv/"
        )
        self.assertEqual(link, 'link_el')

    def test_open_invokes_dismiss_callback(self):
        dismissed = []
        modal = _mk(dismiss_cb=lambda: dismissed.append(True))
        selectors = modal._selectors
        selectors.get_all.return_value = ['x']
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value='x',
        ):
            modal.open('u', 'followers')
        self.assertEqual(dismissed, [True])

    def test_open_with_no_dismiss_callback_skips(self):
        modal = _mk(dismiss_cb=None)  # no callback
        modal._selectors.get_all.return_value = ['x']
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value='x',
        ):
            # Must not raise
            modal.open('u', 'followers')

    def test_open_driver_exception_returns_none(self):
        driver = MagicMock()
        driver.get.side_effect = WebDriverException("bad")
        modal = _mk(driver=driver)
        self.assertIsNone(modal.open('u', 'followers'))

    def test_open_invalid_list_type_returns_none(self):
        modal = _mk()
        self.assertIsNone(modal.open('u', 'bogus_type'))

    def test_open_no_link_raises_profile_not_found(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        modal._selectors.get_all.return_value = ['x']
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value=None,
        ):
            with self.assertRaises(ProfileNotFoundError):
                modal.open('u', 'followers')


class TestClickLink(unittest.TestCase):

    def test_native_click_succeeds(self):
        modal = _mk()
        link = MagicMock()
        with patch('instat.modal_interaction.WebDriverWait'):
            self.assertTrue(modal.click_link(link, 'followers'))
        link.click.assert_called_once()

    def test_native_fails_js_succeeds(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        link = MagicMock()
        with patch('instat.modal_interaction.WebDriverWait') as wait:
            wait.return_value.until.side_effect = TimeoutException()
            self.assertTrue(modal.click_link(link, 'followers'))
        driver.execute_script.assert_called_once()

    def test_both_fail_returns_false(self):
        driver = MagicMock()
        driver.execute_script.side_effect = WebDriverException("x")
        modal = _mk(driver=driver)
        link = MagicMock()
        with patch('instat.modal_interaction.WebDriverWait') as wait:
            wait.return_value.until.side_effect = TimeoutException()
            self.assertFalse(modal.click_link(link, 'followers'))


class TestWaitDialog(unittest.TestCase):

    def test_true_when_dialog_appears(self):
        modal = _mk()
        with patch('instat.modal_interaction.WebDriverWait') as wait:
            wait.return_value.until.return_value = True
            self.assertTrue(modal.wait_dialog())

    def test_false_on_timeout(self):
        modal = _mk()
        with patch('instat.modal_interaction.WebDriverWait') as wait:
            wait.return_value.until.side_effect = TimeoutException()
            self.assertFalse(modal.wait_dialog())


class TestClose(unittest.TestCase):

    def test_close_clicks_button(self):
        driver = MagicMock()
        btn = MagicMock()
        driver.find_element.return_value = btn
        modal = _mk(driver=driver)
        modal._selectors.get.return_value = '//close'
        with patch('instat.modal_interaction.human_delay'):
            modal.close()
        btn.click.assert_called_once()

    def test_close_falls_back_to_escape(self):
        driver = MagicMock()
        driver.find_element.side_effect = NoSuchElementException()
        modal = _mk(driver=driver)
        modal._selectors.get.return_value = '//close'
        with patch('instat.modal_interaction.human_delay'):
            modal.close()
        driver.switch_to.active_element.send_keys.assert_called_once()


class TestResetPage(unittest.TestCase):

    def test_navigates_to_about_blank(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        with patch('instat.modal_interaction.human_delay'):
            modal.reset_page()
        driver.get.assert_called_once_with('about:blank')

    def test_reset_swallows_exception(self):
        driver = MagicMock()
        driver.get.side_effect = Exception("dead")
        modal = _mk(driver=driver)
        # Must not raise
        modal.reset_page()


class TestReopen(unittest.TestCase):

    def test_full_reopen_happy_path(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        modal._selectors.get_all.return_value = ['x']
        modal._selectors.get.return_value = '//close'
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value=MagicMock(),
        ), patch(
            'instat.modal_interaction.WebDriverWait'
        ) as wait, patch('instat.modal_interaction.human_delay'):
            wait.return_value.until.return_value = True
            self.assertTrue(modal.reopen('u', 'followers'))

    def test_reopen_returns_false_if_link_missing(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        modal._selectors.get_all.return_value = ['x']
        modal._selectors.get.return_value = '//close'
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value=None,
        ), patch('instat.modal_interaction.human_delay'):
            self.assertFalse(modal.reopen('u', 'followers'))

    def test_reopen_returns_false_if_dialog_never_appears(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        modal._selectors.get_all.return_value = ['x']
        modal._selectors.get.return_value = '//close'
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value=MagicMock(),
        ), patch(
            'instat.modal_interaction.WebDriverWait'
        ) as wait, patch('instat.modal_interaction.human_delay'):
            wait.return_value.until.side_effect = TimeoutException()
            self.assertFalse(modal.reopen('u', 'followers'))


class TestOpenAndClick(unittest.TestCase):

    def test_open_and_click_end_to_end(self):
        driver = MagicMock()
        modal = _mk(driver=driver)
        modal._selectors.get_all.return_value = ['x']
        with patch(
            'instat.modal_interaction.Utils.find_element_with_fallback',
            return_value=MagicMock(),
        ), patch(
            'instat.modal_interaction.WebDriverWait'
        ) as wait:
            wait.return_value.until.return_value = True
            self.assertTrue(modal.open_and_click('u', 'followers'))


if __name__ == '__main__':
    unittest.main()
