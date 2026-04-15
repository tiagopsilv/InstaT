import json
import logging
import os
from typing import List


class SelectorLoader:
    """
    Loads CSS/XPath selectors from a JSON file with support for fallback alternatives.

    Selector values can be:
    - A string: single selector (backward compatible)
    - A list of strings: tried in order until one works (resilient to Instagram UI changes)

    Example selectors.json:
        {
            "LOGIN_USERNAME_INPUT": "input[name='username']",
            "FOLLOWERS_LINK": [
                "//a[contains(@href, '/followers/')]",
                "//a[.//span[contains(text(),'seguidores')]]"
            ]
        }
    """
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "selectors.json")

        self.selectors = {}
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                self.selectors = json.load(f)
        except FileNotFoundError:
            logging.warning(f"Selector config file not found at: {config_path}. Proceeding with empty config.")
        except json.JSONDecodeError as e:
            logging.error(f"Failed to parse JSON in selector config: {e}. Proceeding with empty config.")
        except Exception as e:
            logging.error(f"Unexpected error loading selector config: {e}. Proceeding with empty config.")

    def get(self, key: str) -> str:
        """
        Get the primary selector by key. For lists, returns the first entry.
        Raises KeyError if not found.
        """
        if key not in self.selectors:
            raise KeyError(f"Selector '{key}' not found in selectors config.")
        value = self.selectors[key]
        if isinstance(value, list):
            return value[0]
        return value

    def get_all(self, key: str) -> List[str]:
        """
        Get all selector alternatives for a key as a list.
        Single-string values are wrapped in a list for uniform handling.
        Raises KeyError if not found.
        """
        if key not in self.selectors:
            raise KeyError(f"Selector '{key}' not found in selectors config.")
        value = self.selectors[key]
        if isinstance(value, list):
            return value
        return [value]


__all__ = ["SelectorLoader"]
