import json
import os
import logging

class SelectorLoader:
    """
    Loads a set of CSS or XPath selectors from a JSON file.

    This class is intended to centralize selector definitions used throughout the InstaT package.
    It attempts to load a selectors.json configuration file at initialization and provides
    access to the selectors via the `get` method.

    If the file does not exist or is malformed, it safely falls back to an empty configuration
    while logging the error.
    """
    def __init__(self, config_path: str = None):
        """
        Initializes the SelectorLoader.

        :param config_path: Optional path to the JSON file with selector definitions.
                            If not provided, defaults to 'selectors.json' in the current directory.
        """
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
        Get the selector by key. Raises KeyError if not found.

        :param key: The key of the selector to retrieve
        :return: The selector string (CSS or XPath)
        """
        if key not in self.selectors:
            raise KeyError(f"Selector '{key}' not found in selectors config.")
        return self.selectors[key]


__all__ = ["SelectorLoader"]