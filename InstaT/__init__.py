"""
instat package initialization.

Provides:
- InstaLogin: login automation to Instagram
- InstaExtractor: profile and follower data extractor
- utils: helper functions for WebDriver operations
"""

from .extractor import InstaExtractor
from .login import InstaLogin
from .exceptions import LoginError, ProfileNotFoundError, RateLimitError, AccountBlockedError
from .constants import human_delay
from .backoff import SmartBackoff
from .checkpoint import ExtractionCheckpoint
from .session_cache import SessionCache
from . import utils
from . import constants
from . import backoff
from . import checkpoint
from . import session_cache

__all__ = [
    "InstaLogin",
    "InstaExtractor",
    "LoginError",
    "ProfileNotFoundError",
    "RateLimitError",
    "AccountBlockedError",
    "utils",
    "constants",
    "backoff",
    "human_delay",
    "SmartBackoff",
    "ExtractionCheckpoint",
    "checkpoint",
    "SessionCache",
    "session_cache",
]