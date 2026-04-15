"""
instat package initialization.

Provides:
- InstaLogin: login automation to Instagram
- InstaExtractor: profile and follower data extractor
- utils: helper functions for WebDriver operations
"""

from . import backoff, checkpoint, constants, session_cache, utils
from .async_extractor import AsyncInstaExtractor
from .backoff import SmartBackoff
from .checkpoint import ExtractionCheckpoint
from .constants import human_delay
from .engines import BaseEngine, EngineManager, HttpxEngine, PlaywrightEngine, SeleniumEngine
from .exceptions import (
    AccountBlockedError,
    AllEnginesBlockedError,
    BlockedError,
    LoginError,
    ProfileNotFoundError,
    RateLimitError,
)
from .exporters import BaseExporter, CallbackExporter, CSVExporter, JSONExporter, SQLiteExporter
from .extractor import InstaExtractor
from .login import InstaLogin
from .proxy import ProxyPool, ProxyState
from .session_cache import SessionCache
from .session_pool import Session, SessionPool

__all__ = [
    "InstaLogin",
    "InstaExtractor",
    "AsyncInstaExtractor",
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
    "BaseEngine",
    "EngineManager",
    "SeleniumEngine",
    "PlaywrightEngine",
    "HttpxEngine",
    "BlockedError",
    "AllEnginesBlockedError",
    "ProxyPool",
    "ProxyState",
    "SessionPool",
    "Session",
    "BaseExporter",
    "CSVExporter",
    "JSONExporter",
    "SQLiteExporter",
    "CallbackExporter",
]
