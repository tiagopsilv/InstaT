import unittest
from unittest.mock import MagicMock, patch

from instat.engines.base import BaseEngine
from instat.engines.selenium_engine import SeleniumEngine


class TestSeleniumEngine(unittest.TestCase):

    def test_is_available_true(self):
        engine = SeleniumEngine()
        self.assertTrue(engine.is_available)

    def test_name_is_selenium(self):
        engine = SeleniumEngine()
        self.assertEqual(engine.name, 'selenium')

    def test_implements_base_engine(self):
        self.assertTrue(issubclass(SeleniumEngine, BaseEngine))

    @patch('instat.engines.selenium_engine.InstaLogin')
    def test_login_creates_driver(self, MockLogin):
        mock_instance = MockLogin.return_value
        mock_instance.driver = MagicMock()
        mock_instance.login.return_value = True

        engine = SeleniumEngine(headless=True, timeout=15)
        result = engine.login('user', 'pass')

        self.assertTrue(result)
        MockLogin.assert_called_once()
        self.assertIsNotNone(engine._driver)
        self.assertEqual(engine._driver, mock_instance.driver)

    def test_quit_closes_driver(self):
        engine = SeleniumEngine()
        engine._driver = MagicMock()
        engine.quit()
        engine._driver.quit.assert_called_once()

    def test_quit_no_driver_is_safe(self):
        engine = SeleniumEngine()
        engine._driver = None
        engine.quit()  # should not raise

    @patch('instat.engines.selenium_engine.InstaLogin')
    def test_extract_returns_set(self, MockLogin):
        mock_instance = MockLogin.return_value
        mock_instance.driver = MagicMock()
        mock_instance.login.return_value = True
        mock_instance.close_keywords = ["not now"]

        engine = SeleniumEngine()
        engine.login('user', 'pass')

        with patch.object(engine, '_extract_list', return_value=['a', 'b', 'c']):
            result = engine.extract('profile', 'followers')
            self.assertIsInstance(result, set)
            self.assertEqual(result, {'a', 'b', 'c'})


if __name__ == "__main__":
    unittest.main()
