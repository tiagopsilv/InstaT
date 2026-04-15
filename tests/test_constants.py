import unittest
from unittest.mock import MagicMock, patch

from instat.constants import (
    COOKIE_RESTORE_REFRESH_TIMEOUT,
    DISMISS_MODAL_TIMEOUT,
    ELEMENT_RETRY_DELAY,
    ELEMENTS_RETRY_WAIT,
    ELEMENTS_RETRY_WAIT_LONG,
    IGNORE_BUTTON_PRE_CLICK,
    LOADING_SPINNER_WAIT,
    LOGIN_POST_CLICK_DELAY,
    PROFILE_WAIT_INTERVAL,
    SCROLL_INNER_PAUSE,
    SCROLL_PAUSE,
    SPINNER_WAIT_TIMEOUT,
    human_delay,
)


class TestConstants(unittest.TestCase):
    """Tests that all timing constants are positive numbers."""

    def test_all_constants_are_positive(self):
        constants = [
            LOGIN_POST_CLICK_DELAY,
            IGNORE_BUTTON_PRE_CLICK,
            DISMISS_MODAL_TIMEOUT,
            SCROLL_PAUSE,
            SCROLL_INNER_PAUSE,
            PROFILE_WAIT_INTERVAL,
            ELEMENT_RETRY_DELAY,
            ELEMENTS_RETRY_WAIT,
            ELEMENTS_RETRY_WAIT_LONG,
            SPINNER_WAIT_TIMEOUT,
            LOADING_SPINNER_WAIT,
            COOKIE_RESTORE_REFRESH_TIMEOUT,
        ]
        for const in constants:
            self.assertIsInstance(const, (int, float))
            self.assertGreater(const, 0)


class TestHumanDelay(unittest.TestCase):
    """Tests for the human_delay() function."""

    @patch("instat.constants.time.sleep")
    @patch("instat.constants.random.gauss", return_value=1.0)
    def test_human_delay_calls_sleep(self, mock_gauss, mock_sleep):
        result = human_delay(1.0)
        mock_gauss.assert_called_once_with(1.0, 0.3)
        mock_sleep.assert_called_once_with(1.0)
        self.assertEqual(result, 1.0)

    @patch("instat.constants.time.sleep")
    @patch("instat.constants.random.gauss", return_value=0.01)
    def test_human_delay_floor_at_0_1(self, mock_gauss, mock_sleep):
        result = human_delay(0.5)
        mock_sleep.assert_called_once_with(0.1)
        self.assertEqual(result, 0.1)

    @patch("instat.constants.time.sleep")
    @patch("instat.constants.random.gauss", return_value=-0.5)
    def test_human_delay_floor_on_negative(self, mock_gauss, mock_sleep):
        result = human_delay(0.5)
        mock_sleep.assert_called_once_with(0.1)
        self.assertEqual(result, 0.1)

    @patch("instat.constants.time.sleep")
    @patch("instat.constants.random.gauss", return_value=2.5)
    def test_human_delay_custom_variance(self, mock_gauss, mock_sleep):
        result = human_delay(2.0, variance=0.5)
        mock_gauss.assert_called_once_with(2.0, 0.5)
        mock_sleep.assert_called_once_with(2.5)
        self.assertEqual(result, 2.5)

    @patch("instat.constants.time.sleep")
    def test_human_delay_never_returns_below_floor(self, mock_sleep):
        """Run multiple iterations to verify floor is always respected."""
        for _ in range(100):
            with patch("instat.constants.random.gauss", return_value=-10.0):
                result = human_delay(0.1)
                self.assertGreaterEqual(result, 0.1)


if __name__ == "__main__":
    unittest.main()
