import unittest
import tempfile
from unittest.mock import patch

from InstaT.checkpoint import ExtractionCheckpoint


class TestExtractionCheckpoint(unittest.TestCase):

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt = ExtractionCheckpoint('testuser', 'followers', checkpoint_dir=tmpdir)
            profiles = {'user1', 'user2', 'user3'}
            ckpt.save(profiles)
            loaded = ckpt.load()
            self.assertEqual(loaded, profiles)

    def test_load_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt = ExtractionCheckpoint('nouser', 'followers', checkpoint_dir=tmpdir)
            self.assertIsNone(ckpt.load())

    def test_clear_removes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt = ExtractionCheckpoint('testuser', 'following', checkpoint_dir=tmpdir)
            ckpt.save({'a', 'b'})
            self.assertIsNotNone(ckpt.load())
            ckpt.clear()
            self.assertIsNone(ckpt.load())

    @patch('InstaT.checkpoint.time')
    def test_expired_checkpoint_returns_none(self, mock_time):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Save at t=1000
            mock_time.time.return_value = 1000.0
            ckpt = ExtractionCheckpoint('testuser', 'followers', checkpoint_dir=tmpdir)
            ckpt.save({'user1'})

            # Load at t=1000 + 86401 (>24h)
            mock_time.time.return_value = 1000.0 + 86401
            loaded = ckpt.load()
            self.assertIsNone(loaded)
            # File should be removed
            self.assertFalse(ckpt._file.exists())

    def test_save_overwrites_previous(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ckpt = ExtractionCheckpoint('testuser', 'followers', checkpoint_dir=tmpdir)
            ckpt.save({'a'})
            self.assertEqual(ckpt.load(), {'a'})
            ckpt.save({'a', 'b'})
            self.assertEqual(ckpt.load(), {'a', 'b'})


if __name__ == "__main__":
    unittest.main()
