import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from loguru import logger

from instat.extractor import InstaExtractor

# Configure Loguru for detailed test logging
logger.remove()
logger.add(sys.stderr, level="DEBUG", colorize=True, format="<green>{time}</green> | <level>{message}</level>")
logger.add("instat/logs/test_insta_extractor.log", rotation="10 MB", retention="10 days", level="DEBUG", diagnose=True)

class TestInstaExtractor(unittest.TestCase):
    """
    Test cases for InstaExtractor.
    The extractor logs into Instagram automatically upon instantiation.
    """

    @classmethod
    def setUpClass(cls):
        logger.info("Initializing InstaExtractor for testing.")
        cls.username = "your_username"
        cls.valid_password = "your_password"
        cls.profile_id = "tiagopsilv"

        # Mock InstaLogin to avoid real Instagram login.
        # InstaExtractor imports InstaLogin at module level and passes it to SeleniumEngine,
        # so patching 'instat.extractor.InstaLogin' propagates through the engine.
        with patch('instat.extractor.InstaLogin') as MockLogin:
            cls.mock_login_instance = MockLogin.return_value
            cls.mock_login_instance.driver = MagicMock()
            cls.mock_login_instance.close_keywords = ["not now", "save"]
            cls.extractor = InstaExtractor(cls.username, cls.valid_password, headless=True)

        # Adjust parameters for faster test execution
        cls.extractor.max_refresh_attempts = 1
        cls.extractor.wait_interval = 0.05
        cls.extractor.additional_scroll_attempts = 1
        cls.extractor.pause_time = 0.05
        cls.extractor.max_attempts = 1

    @classmethod
    def tearDownClass(cls):
        cls.extractor.quit()
        logger.info("WebDriver closed after tests.")

    def test_parse_count_text(self):
        test_cases = [
            ("1,234", 1234),
            ("1.234", 1234),
            ("1.2k", 1200),
            ("1,2k", 1200),
            ("3M", 3000000),
            ("2,5 mil", 2500),
            ("2.5 mil", 2500),
            ("1 mi", 1000000),
            ("567", 567),
        ]
        for text, expected in test_cases:
            with self.subTest(text=text):
                result = self.extractor.parse_count_text(text)
                self.assertEqual(result, expected)

    def test_parse_count_text_invalid_input(self):
        invalid_inputs = ["abc", "123x", "10kk", "", None]
        for input_text in invalid_inputs:
            with self.subTest(input=input_text):
                with self.assertRaises((ValueError, AttributeError)):
                    self.extractor.parse_count_text(input_text)

    @patch('instat.engines.engine_manager.EngineManager.extract', return_value=['user1', 'user2'])
    def test_get_followers_mocked(self, mock_extract):
        followers = self.extractor.get_followers(self.profile_id)
        self.assertEqual(followers, ['user1', 'user2'])
        mock_extract.assert_called_once()

    @patch('instat.engines.engine_manager.EngineManager.extract', return_value=[])
    def test_get_followers_returns_empty(self, mock_extract):
        followers = self.extractor.get_followers(self.profile_id)
        self.assertEqual(followers, [], "Expected empty list when engine returns nothing.")

    @patch('instat.engines.engine_manager.EngineManager.extract', return_value=[])
    def test_handle_invalid_profile(self, mock_extract):
        result = self.extractor.get_followers("invalid_profile")
        self.assertEqual(result, [], "Expected empty list for invalid profile.")

    def test_quit_closes_driver(self):
        with patch.object(self.extractor.driver, 'quit') as mock_quit:
            self.extractor.quit()
            mock_quit.assert_called_once()

    def test_configurable_attributes_delegate_to_engine(self):
        self.extractor.pause_time = 0.99
        self.assertEqual(self.extractor._engine.pause_time, 0.99)
        self.extractor.wait_interval = 0.88
        self.assertEqual(self.extractor._engine.wait_interval, 0.88)
        # Reset
        self.extractor.pause_time = 0.05
        self.extractor.wait_interval = 0.05


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)
