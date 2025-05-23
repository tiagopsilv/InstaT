"""
InstaT package initialization.

Provides:
- InstaLogin: login automation to Instagram
- InstaExtractor: profile and follower data extractor
- utils: helper functions for WebDriver operations
"""

from .login import InstaLogin
from .extractor import InstaExtractor
from . import utils

__all__ = [
    "InstaLogin",
    "InstaExtractor",
    "utils"
]