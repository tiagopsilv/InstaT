"""Heartbeat da concessão em thread e conexão próprias (roadmap I6, §6.3.12).

A cada `ttl/3` renova a concessão numa transação que também classifica o
motivo quando não renova (`account_restricted` ou `lease_lost`). Sem renovação
bem-sucedida há mais de `ttl/2`, a posse é tratada como não verificável: o
worker não deve enviar nem gravar.
"""
import sqlite3
import threading
import time
from typing import Callable, Optional

try:
    from instat.jobstore.store import JobStore, Token, validate_timing
except ImportError:  # pragma: no cover
    from jobstore.store import JobStore, Token, validate_timing  # type: ignore


class Heartbeat:
    def __init__(self, path: str, tok: Token, *, ttl: float, busy_timeout: float,
                 interval: Optional[float] = None, clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic) -> None:
        validate_timing(ttl, busy_timeout)
        self.path, self.tok, self.ttl, self.busy_timeout = path, tok, float(ttl), float(busy_timeout)
        self.interval = interval if interval is not None else ttl / 3
        self.clock, self.mono = clock, mono
        self.status = "not_started"
        self.renewals = 0
        self.failures = 0
        self.last_error: Optional[str] = None
        self.connection_thread_id: Optional[int] = None
        self._last_success: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._store: Optional[JobStore] = None

    def _own_store(self) -> JobStore:
        if self._store is None:
            self._store = JobStore(self.path, ttl=self.ttl, busy_timeout=self.busy_timeout, create=False,
                                   clock=self.clock)
            self.connection_thread_id = threading.get_ident()
        return self._store

    def beat_once(self) -> str:
        try:
            result = self._own_store().heartbeat(self.tok, now=None)
        except sqlite3.OperationalError as e:
            self.failures += 1
            self.last_error = str(e)
            return self.status
        self.status = result
        if result == "renewed":
            self.renewals += 1
            self._last_success = self.mono()
        return result

    def verifiable(self) -> bool:
        """Posse verificável: última renovação bem-sucedida há no máximo ttl/2."""
        return (self.status == "renewed" and self._last_success is not None
                and self.mono() - self._last_success <= self.ttl / 2)

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if self.beat_once() in ("account_restricted", "lease_lost"):
                    return
                self._stop.wait(self.interval)
        finally:
            if self._store is not None:
                self._store.close()
                self._store = None

    def start(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, name="jobstore-heartbeat", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.ttl + 5)


__all__ = ["Heartbeat"]
