"""
instat package initialization.

Provides:
- InstaLogin: login automation to Instagram
- InstaExtractor: profile and follower data extractor
- utils: helper functions for WebDriver operations
"""

from . import backoff, checkpoint, constants, session_cache, utils
from .async_extractor import AsyncInstaExtractor  # last: depends on extractor + exporters
from .backoff import SmartBackoff
from .checkpoint import ExtractionCheckpoint
from .constants import human_delay
from .email_code import ImapConfig, fetch_instagram_code
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
from .extraction_result import ExtractionResult
from .extractor import InstaExtractor
from .logging_config import configure_logging
from .login import InstaLogin
from .post_metrics import PostMetrics, parse_hashtags, parse_post_from_api
from .profile import Profile
from .profile_summary import ProfileSummary
from .providers import (
    brightdata_basic_auth,
    brightdata_cdp_endpoint,
    brightdata_playwright_engine,
    brightdata_selenium_engine,
    brightdata_webdriver_endpoint,
)
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
    "Profile",
    "PostMetrics",
    "parse_hashtags",
    "parse_post_from_api",
    "ProfileSummary",
    "ExtractionResult",
    "ImapConfig",
    "fetch_instagram_code",
    "configure_logging",
    "brightdata_basic_auth",
    "brightdata_cdp_endpoint",
    "brightdata_webdriver_endpoint",
    "brightdata_playwright_engine",
    "brightdata_selenium_engine",
]
