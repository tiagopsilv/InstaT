"""Persistent pool of logged-in SeleniumEngines.

Why this module exists:
  `get_followers_parallel` / `get_following_parallel` historically
  accept credentials per-call and each call re-logs every worker. IG
  flags frequent logins, and the form-login path is slow (~30s per
  worker vs. ~2s cookie-restore).

Design:
  WorkerPool holds N SeleniumEngine instances that have already
  authenticated. Caller registers workers up-front with
  `pool.add_worker(username, password)`; each parallel extraction
  reuses the same drivers. Sessions survive across extractions.

Lifetime:
  Drivers stay alive until `clear()` is called or the process ends.
  Use `clear()` to release browsers on shutdown. On process exit with
  drivers still running, Selenium will usually clean up — but calling
  `clear()` explicitly is safer.

Thread-safety:
  Registration and clearing are guarded. Engines themselves are NOT
  thread-safe internally — only hand each engine to one thread at a
  time. ParallelCoordinator already respects this (one engine per
  worker thread).
"""
import threading
from typing import Any, List, Optional

from loguru import logger

try:
    from instat.engines.selenium_engine import SeleniumEngine
except ImportError:
    from engines.selenium_engine import SeleniumEngine  # type: ignore


class WorkerPool:
    """Persistent pool of pre-authenticated SeleniumEngines.

    Typical usage (from InstaExtractor):
        ext.set_worker('alt1', 'pw1')
        ext.set_worker('alt2', 'pw2')
        followers = ext.get_followers_parallel('some_target')
        # later, same pool:
        following = ext.get_following_parallel('some_target')
        ext.clear_workers()  # shutdown
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout: int = 20,
        base_url: Optional[str] = None,
        imap_config: Any = None,
    ) -> None:
        self._engines: List[SeleniumEngine] = []
        self._headless = headless
        self._timeout = timeout
        self._base_url = base_url
        self._imap_config = imap_config
        self._lock = threading.Lock()

    def add_worker(
        self,
        username: str,
        password: str,
        *,
        proxy: Optional[str] = None,
    ) -> SeleniumEngine:
        """Create a SeleniumEngine, log it in, and add to the pool.

        Returns the engine so the caller can inspect it. The login is
        synchronous — blocks until authentication finishes (or raises
        on failure; a failed engine is NOT added to the pool)."""
        engine = SeleniumEngine(
            headless=self._headless,
            timeout=self._timeout,
            base_url=self._base_url,
            imap_config=self._imap_config,
            proxy=proxy,
        )
        try:
            engine.login(username, password)
        except Exception:
            try:
                engine.quit()
            except Exception:
                pass
            raise
        with self._lock:
            self._engines.append(engine)
        logger.info(
            f"WorkerPool: added worker '{username}' "
            f"(pool size: {len(self._engines)})"
        )
        return engine

    def engines(self) -> List[SeleniumEngine]:
        """Snapshot of live engines. Safe to iterate outside the lock."""
        with self._lock:
            return list(self._engines)

    def __len__(self) -> int:
        with self._lock:
            return len(self._engines)

    def __iter__(self):
        return iter(self.engines())

    def __bool__(self) -> bool:
        return len(self) > 0

    def clear(self) -> None:
        """Quit all drivers and empty the pool."""
        with self._lock:
            engines = self._engines
            self._engines = []
        for e in engines:
            try:
                e.quit()
            except Exception as ex:
                logger.debug(f"WorkerPool: engine.quit() ignored: {ex}")
        if engines:
            logger.info(f"WorkerPool: cleared {len(engines)} workers")


__all__ = ["WorkerPool"]
