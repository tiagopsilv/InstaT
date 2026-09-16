"""Diagnostic collector — captures full page state on error events.

Why this module exists:
  Production failures (blocks, challenge rejects, scroll timeouts)
  rarely leave enough breadcrumbs in the log to fix the underlying
  issue without re-running. Screenshots alone lose DOM state; text
  logs alone miss what IG actually rendered. This collector bundles
  EVERYTHING about the browser at the moment of failure into a
  timestamped directory, so future-me can open it and reconstruct
  exactly what IG sent us.

Bundle contents per event:
  metadata.json     — event name, url (redacted), title, timestamp,
                      traceback if exception, caller context
  page_source.html  — full DOM
  screenshot.png    — viewport snapshot
  console_log.txt   — browser console (JS errors, warnings)
  cookies.json      — names/domains/expiry; `sessionid`/`csrftoken`
                      values redacted so bundles are safe to share

Design decisions:
  - Rate-limited (per event-type) to avoid spamming bundles in a
    retry loop. Default: one bundle per event-type per 30s.
  - Retention: oldest bundles pruned after N total. Default 50.
  - Never raises — failure to capture must not mask the original
    problem. All errors during capture are logged and swallowed.
"""
import json
import os
import time
import traceback as tb_mod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

_REDACT_COOKIES = {"sessionid", "csrftoken", "ds_user_id", "ig_did"}


def _redact_url(url: str) -> str:
    """Drop query + fragment (may contain bearer tokens)."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return url


def _chmod_600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


class DiagnosticCollector:
    """Collects page state when the caller reports a diagnostic event.

    Typical usage:
        dx = DiagnosticCollector()
        try:
            risky_selenium_action(driver)
        except Exception as e:
            dx.capture(driver, "login_timeout", exc=e,
                       context={"retry": 3})
            raise
    """

    def __init__(
        self,
        artifacts_dir: str = "instat/logs/diagnostics",
        *,
        rate_limit_seconds: float = 30.0,
        retention: int = 50,
    ) -> None:
        self._dir = Path(artifacts_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._rate_limit = rate_limit_seconds
        self._retention = retention
        self._last_capture: Dict[str, float] = {}

    def capture(
        self,
        driver: Any,
        event: str,
        *,
        exc: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Save a full diagnostic bundle. Returns path or None if
        rate-limited / on capture failure."""
        if self._rate_limited(event):
            return None
        try:
            bundle_dir = self._new_bundle_dir(event)
            self._write_metadata(bundle_dir, driver, event, exc, context)
            self._write_page_source(bundle_dir, driver)
            self._write_screenshot(bundle_dir, driver)
            self._write_console_log(bundle_dir, driver)
            self._write_cookies(bundle_dir, driver)
            self._prune_old_bundles()
            logger.info(
                f"Diagnostic bundle saved: {bundle_dir.name} "
                f"(event={event})"
            )
            return str(bundle_dir)
        except Exception as e:
            logger.warning(f"DiagnosticCollector: capture failed: {e}")
            return None

    # ------------------------ internals ------------------------------

    def _rate_limited(self, event: str) -> bool:
        now = time.time()
        last = self._last_capture.get(event, 0.0)
        if now - last < self._rate_limit:
            return True
        self._last_capture[event] = now
        return False

    def _new_bundle_dir(self, event: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        safe_event = "".join(c if c.isalnum() or c in "_-" else "_" for c in event)
        bundle = self._dir / f"{ts}_{safe_event}"
        bundle.mkdir(parents=True, exist_ok=True)
        return bundle

    @staticmethod
    def _write_metadata(
        bundle_dir: Path,
        driver: Any,
        event: str,
        exc: Optional[BaseException],
        context: Optional[Dict[str, Any]],
    ) -> None:
        meta: Dict[str, Any] = {
            "event": event,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "url": _redact_url(_safe_get(driver, "current_url") or ""),
            "title": _safe_get(driver, "title") or "",
            "window_size": _safe_call(
                lambda: driver.get_window_size()
            ) or {},
            "user_agent": _safe_call(
                lambda: driver.execute_script("return navigator.userAgent")
            ) or "",
            "context": context or {},
        }
        if exc is not None:
            meta["exception"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(tb_mod.format_exception(
                    type(exc), exc, exc.__traceback__,
                )),
            }
        path = bundle_dir / "metadata.json"
        # default=str so any odd object that slipped in (MagicMock in
        # tests, WebElement from driver, etc.) becomes its repr rather
        # than crashing the whole capture.
        path.write_text(
            json.dumps(meta, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        _chmod_600(path)

    @staticmethod
    def _write_page_source(bundle_dir: Path, driver: Any) -> None:
        html = _safe_get(driver, "page_source") or ""
        path = bundle_dir / "page_source.html"
        try:
            path.write_text(html, encoding="utf-8")
            _chmod_600(path)
        except Exception as e:
            logger.debug(f"diagnostics: page_source write failed: {e}")

    @staticmethod
    def _write_screenshot(bundle_dir: Path, driver: Any) -> None:
        path = bundle_dir / "screenshot.png"
        try:
            driver.save_screenshot(str(path))
            _chmod_600(path)
        except Exception as e:
            logger.debug(f"diagnostics: screenshot failed: {e}")

    @staticmethod
    def _write_console_log(bundle_dir: Path, driver: Any) -> None:
        """Firefox + Chrome expose browser console via get_log('browser').
        Tolerates the 'log type not supported' case (newer Selenium)."""
        path = bundle_dir / "console_log.txt"
        try:
            entries = driver.get_log("browser")
        except Exception as e:
            path.write_text(f"(console log unavailable: {e})\n",
                            encoding="utf-8")
            _chmod_600(path)
            return
        lines = []
        for entry in entries or []:
            try:
                lvl = entry.get("level", "INFO")
                msg = entry.get("message", "")
                ts = entry.get("timestamp", "")
                lines.append(f"[{ts}] {lvl}: {msg}")
            except Exception:
                continue
        path.write_text("\n".join(lines) or "(empty)\n", encoding="utf-8")
        _chmod_600(path)

    @staticmethod
    def _write_cookies(bundle_dir: Path, driver: Any) -> None:
        path = bundle_dir / "cookies.json"
        try:
            raw = driver.get_cookies() or []
        except Exception as e:
            path.write_text(
                json.dumps({"error": f"get_cookies failed: {e}"}),
                encoding="utf-8",
            )
            _chmod_600(path)
            return
        redacted = []
        for c in raw:
            try:
                entry = {
                    "name": c.get("name"),
                    "domain": c.get("domain"),
                    "path": c.get("path"),
                    "expiry": c.get("expiry"),
                    "secure": c.get("secure"),
                    "httpOnly": c.get("httpOnly"),
                }
                if c.get("name") in _REDACT_COOKIES:
                    entry["value"] = "<redacted>"
                else:
                    v = c.get("value", "")
                    entry["value"] = v if len(v) <= 64 else f"{v[:32]}...<truncated>"
                redacted.append(entry)
            except Exception:
                continue
        path.write_text(json.dumps(redacted, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        _chmod_600(path)

    def _prune_old_bundles(self) -> None:
        try:
            bundles = sorted(
                (p for p in self._dir.iterdir() if p.is_dir()),
                key=lambda p: p.name,
            )
            excess = len(bundles) - self._retention
            if excess <= 0:
                return
            for old in bundles[:excess]:
                try:
                    for f in old.iterdir():
                        f.unlink()
                    old.rmdir()
                except Exception as e:
                    logger.debug(f"diagnostics: prune {old.name} failed: {e}")
        except Exception as e:
            logger.debug(f"diagnostics: prune sweep failed: {e}")


def _safe_get(obj: Any, attr: str) -> Any:
    try:
        return getattr(obj, attr)
    except Exception:
        return None


def _safe_call(fn: Any) -> Any:
    try:
        return fn()
    except Exception:
        return None


__all__ = ["DiagnosticCollector"]
