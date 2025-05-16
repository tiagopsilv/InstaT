"""
Configuration loader for InstaT.

Provides:
- SelectorLoader: utility class to load CSS/XPath selectors from JSON
- selectors: preloaded default selectors as dictionary
"""

import os
import json
from .selector_loader import SelectorLoader

DEFAULT_SELECTORS_PATH = os.path.join(os.path.dirname(__file__), "selectors.json")

# Safe loader with fallback
try:
    selectors = SelectorLoader().load(DEFAULT_SELECTORS_PATH)
except Exception as e:
    import logging
    logging.warning(f"Could not load selectors.json: {e}. Using empty fallback.")
    selectors = {}

__all__ = ["SelectorLoader", "selectors"]