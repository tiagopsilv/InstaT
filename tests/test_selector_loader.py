import unittest
import json
from unittest.mock import mock_open, patch
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from InstaT.config.selector_loader import SelectorLoader


class TestSelectorLoader(unittest.TestCase):

    def test_load_valid_json(self):
        mock_data = '{"FOLLOW_BUTTON": "//button[@type=\\"button\\"]"}'
        with patch("builtins.open", mock_open(read_data=mock_data)):
            loader = SelectorLoader("fake_path.json")
            self.assertEqual(loader.get("FOLLOW_BUTTON"), "//button[@type=\"button\"]")

    def test_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            loader = SelectorLoader("nonexistent.json")
            self.assertEqual(loader.selectors, {}, "Should fallback to empty config on file not found")

    def test_invalid_json(self):
        with patch("builtins.open", mock_open(read_data="{invalid_json}")), \
             patch("json.load", side_effect=json.JSONDecodeError("Expecting value", "{invalid_json}", 0)):
            loader = SelectorLoader("bad.json")
            self.assertEqual(loader.selectors, {}, "Should fallback to empty config on JSON parse error")

    def test_missing_key_raises_keyerror(self):
        mock_data = '{"LOGIN": "//input[@name=\\"username\\"]"}'
        with patch("builtins.open", mock_open(read_data=mock_data)):
            loader = SelectorLoader("dummy_path.json")
            with self.assertRaises(KeyError):
                loader.get("NON_EXISTENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
