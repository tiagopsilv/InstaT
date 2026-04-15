"""
instat package initialization.

Provides:
- InstaLogin: login automation to Instagram
- InstaExtractor: profile and follower data extractor
- utils: helper functions for WebDriver operations
"""

from . import backoff, checkpoint, constants, session_cache, utils
from .backoff import SmartBackoff
from .checkpoint import ExtractionCheckpoint
from .constants import human_delay
from .exceptions import (
    AccountBlockedError,
    AllEnginesBlockedError,
    BlockedError,
    LoginError,
    ProfileNotFoundError,
    RateLimitError,
)
from .exporters import BaseExporter, CallbackExporter, CSVExporter, JSONExporter, SQLiteExporter
from .session_cache import SessionCache
from .login import InstaLogin
from .engines import BaseEngine, EngineManager, HttpxEngine, PlaywrightEngine, SeleniumEngine
from .extractor import InstaExtractor
from .proxy import ProxyPool, ProxyState
from .session_pool import Session, SessionPool
from .async_extractor import AsyncInstaExtractor  # last: depends on extractor + exporters

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
