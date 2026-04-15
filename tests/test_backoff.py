import unittest
from unittest.mock import patch

from instat.backoff import SmartBackoff


@patch("instat.backoff.human_delay", return_value=1.0)
class TestSmartBackoff(unittest.TestCase):

    def test_wait_increments_attempt(self, mock_hd):
        b = SmartBackoff()
        self.assertEqual(b.attempt, 0)
        b.wait()
        self.assertEqual(b.attempt, 1)
        b.wait()
        self.assertEqual(b.attempt, 2)
        b.wait()
        self.assertEqual(b.attempt, 3)

    def test_reset_zeroes_attempt(self, mock_hd):
        b = SmartBackoff()
        b.wait()
        b.wait()
        self.assertEqual(b.attempt, 2)
        b.reset()
        self.assertEqual(b.attempt, 0)

    def test_max_delay_capped(self, mock_hd):
        b = SmartBackoff(base=2.0, max_delay=300.0, jitter=False)
        b.attempt = 100  # 2^100 would be astronomical
        delay = b.wait()
        self.assertLessEqual(delay, 300.0)

    def test_jitter_varies_delay(self, mock_hd):
        """Run wait() 10x at the same attempt level, verify not all delays are identical."""
        delays = []
        for _ in range(10):
            b = SmartBackoff(base=2.0, max_delay=300.0, jitter=True)
            b.attempt = 3
            delays.append(b.wait())
        # With jitter, not all delays should be identical
        self.assertGreater(len(set(delays)), 1, "Jitter should produce varying delays")

    def test_context_manager_resets(self, mock_hd):
        with SmartBackoff() as b:
            b.wait()
            b.wait()
            self.assertEqual(b.attempt, 2)
        self.assertEqual(b.attempt, 0)

    def test_no_jitter_deterministic(self, mock_hd):
        b = SmartBackoff(base=2.0, max_delay=300.0, jitter=False)
        # attempt 0: delay = 2^0 = 1.0
        delay0 = b.wait()
        self.assertEqual(delay0, 1.0)
        # attempt 1: delay = 2^1 = 2.0
        delay1 = b.wait()
        self.assertEqual(delay1, 2.0)
        # attempt 2: delay = 2^2 = 4.0
        delay2 = b.wait()
        self.assertEqual(delay2, 4.0)


if __name__ == "__main__":
    unittest.main()
