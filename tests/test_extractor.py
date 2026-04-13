import unittest
import sys
import time
from unittest.mock import patch, MagicMock
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

        # Mock InstaLogin to avoid real Instagram login
        with patch('instat.extractor.InstaLogin') as MockLogin:
            cls.mock_login_instance = MockLogin.return_value
            cls.mock_login_instance.driver = MagicMock()
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

    @patch('instat.extractor.InstaExtractor.get_profiles', return_value=['user1', 'user2'])
    @patch('instat.extractor.InstaExtractor._click_link_element', return_value=True)
    @patch('instat.extractor.InstaExtractor._navigate_and_get_link')
    def test_get_followers_mocked(self, mock_nav, mock_click, mock_get_profiles):
        mock_link = MagicMock()
        mock_link.text = "404\nfollowers"
        mock_nav.return_value = mock_link
        followers = self.extractor.get_followers(self.profile_id)
        self.assertEqual(followers, ['user1', 'user2'])
        mock_nav.assert_called()
        mock_get_profiles.assert_called_once()

    @patch('instat.extractor.InstaExtractor._navigate_and_get_link', return_value=None)
    def test_get_followers_nav_failure(self, mock_nav):
        followers = self.extractor.get_followers(self.profile_id)
        self.assertEqual(followers, [], "Expected empty list when navigation fails.")

    @patch('instat.extractor.InstaExtractor.get_profiles', return_value=[])
    def test_max_duration_enforcement(self, mock_get_profiles):
        start_time = time.perf_counter()
        result = self.extractor.get_profiles(100, max_duration=1)
        duration = time.perf_counter() - start_time
        self.assertLessEqual(duration, 1.5, "Duration exceeded max_duration significantly.")

    @patch('instat.extractor.InstaExtractor._navigate_and_get_link', return_value=None)
    def test_handle_invalid_profile(self, mock_nav):
        result = self.extractor.get_followers("invalid_profile")
        self.assertEqual(result, [], "Expected empty list for invalid profile.")

    def test_quit_closes_driver(self):
        with patch.object(self.extractor.driver, 'quit') as mock_quit:
            self.extractor.quit()
            mock_quit.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2, exit=False)