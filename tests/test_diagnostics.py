"""DiagnosticCollector — unit tests for the page-state bundle
collector used on every error/timeout event."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from instat.diagnostics import DiagnosticCollector


def _mk_driver(
    url="https://www.instagram.com/accounts/login/?tok=abc",
    title="Instagram",
    page_source="<html><body>x</body></html>",
    cookies=None,
    console_log=None,
    window_size=None,
    ua="mozilla-mobile",
):
    d = MagicMock()
    type(d).current_url = url
    type(d).title = title
    type(d).page_source = page_source
    d.get_cookies.return_value = cookies if cookies is not None else []
    d.get_log.return_value = console_log if console_log is not None else []
    d.get_window_size.return_value = window_size or {"width": 375, "height": 667}
    d.execute_script.return_value = ua
    d.save_screenshot.return_value = True
    return d


class TestDiagnosticCollectorBundle(unittest.TestCase):

    def test_capture_creates_full_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(artifacts_dir=tmp, rate_limit_seconds=0)
            driver = _mk_driver(cookies=[
                {"name": "sessionid", "value": "SHOULD_BE_REDACTED",
                 "domain": ".instagram.com", "path": "/", "expiry": 1,
                 "secure": True, "httpOnly": True},
                {"name": "foo", "value": "bar",
                 "domain": ".instagram.com", "path": "/"},
            ])
            path = dx.capture(driver, "login_timeout", context={"retry": 3})
            self.assertIsNotNone(path)

            bundle = Path(path)
            self.assertTrue((bundle / "metadata.json").exists())
            self.assertTrue((bundle / "page_source.html").exists())
            self.assertTrue((bundle / "console_log.txt").exists())
            self.assertTrue((bundle / "cookies.json").exists())

            meta = json.loads((bundle / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["event"], "login_timeout")
            self.assertEqual(meta["context"], {"retry": 3})
            # URL query string must be stripped
            self.assertNotIn("tok=", meta["url"])

            cookies = json.loads((bundle / "cookies.json").read_text(encoding="utf-8"))
            sessionid = next(c for c in cookies if c["name"] == "sessionid")
            self.assertEqual(sessionid["value"], "<redacted>")
            foo = next(c for c in cookies if c["name"] == "foo")
            self.assertEqual(foo["value"], "bar")

            driver.save_screenshot.assert_called_once()

    def test_capture_with_exception_serializes_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(artifacts_dir=tmp, rate_limit_seconds=0)
            driver = _mk_driver()
            try:
                raise ValueError("boom")
            except ValueError as e:
                path = dx.capture(driver, "challenge_failed", exc=e)
            meta = json.loads(
                (Path(path) / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(meta["exception"]["type"], "ValueError")
            self.assertEqual(meta["exception"]["message"], "boom")
            self.assertIn("Traceback", meta["exception"]["traceback"])

    def test_capture_never_raises_on_driver_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(artifacts_dir=tmp, rate_limit_seconds=0)
            driver = MagicMock()
            driver.save_screenshot.side_effect = Exception("screenshot dead")
            driver.get_cookies.side_effect = Exception("cookies dead")
            driver.get_log.side_effect = Exception("console dead")
            type(driver).page_source = ""
            type(driver).current_url = ""
            type(driver).title = ""
            # Must not raise; metadata + stubs for failed pieces.
            path = dx.capture(driver, "anything")
            self.assertIsNotNone(path)


class TestDiagnosticCollectorRateLimit(unittest.TestCase):

    def test_rate_limit_suppresses_second_capture_same_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(artifacts_dir=tmp,
                                     rate_limit_seconds=60)
            driver = _mk_driver()
            p1 = dx.capture(driver, "stale")
            p2 = dx.capture(driver, "stale")  # same event, within window
            self.assertIsNotNone(p1)
            self.assertIsNone(p2)

    def test_rate_limit_is_per_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(artifacts_dir=tmp,
                                     rate_limit_seconds=60)
            driver = _mk_driver()
            a = dx.capture(driver, "stale")
            b = dx.capture(driver, "block")
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)


class TestDiagnosticCollectorRetention(unittest.TestCase):

    def test_prune_drops_oldest_over_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(
                artifacts_dir=tmp,
                rate_limit_seconds=0,
                retention=3,
            )
            driver = _mk_driver()
            # Capture 5, retention 3 → 2 old ones must be pruned
            paths = []
            for _ in range(5):
                paths.append(dx.capture(driver, "e"))
                time.sleep(0.005)  # ensure timestamp ordering is stable
            remaining = sorted(Path(tmp).iterdir())
            self.assertEqual(len(remaining), 3)
            # Kept must be the three newest
            self.assertEqual(
                [p.name for p in remaining],
                [Path(paths[i]).name for i in (2, 3, 4)],
            )


class TestDiagnosticCollectorURLRedaction(unittest.TestCase):

    def test_query_and_fragment_are_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            dx = DiagnosticCollector(artifacts_dir=tmp, rate_limit_seconds=0)
            driver = _mk_driver(
                url="https://x.com/a/b?token=secret&x=1#frag",
            )
            path = dx.capture(driver, "e")
            meta = json.loads((Path(path) / "metadata.json").read_text("utf-8"))
            self.assertEqual(meta["url"], "https://x.com/a/b")


class TestScrollLoopIntegration(unittest.TestCase):
    """ScrollLoop must call into diagnostics on stale + reopen + partial."""

    @patch("instat.scroll_loop.human_delay", return_value=0)
    def test_stale_triggering_reopen_captures(self, _hd):
        from instat.scroll_loop import ScrollLoop
        dx = MagicMock()
        sel = MagicMock()
        sel.get.return_value = "span"
        loop = ScrollLoop(
            driver=MagicMock(),
            selectors=sel,
            reopen_modal=lambda *a, **kw: False,  # force break
            warmup_threshold=0, warmup_stale_rounds=2,
            completion_threshold=0.0,
            diagnostics=dx,
        )
        with patch("instat.scroll_loop.Utils") as MU, \
             patch.object(ScrollLoop, "_scroll_modal_js"):
            MU.batch_read_text.return_value = set()
            loop.run(expected_count=100, max_duration=None,
                     profile_id="p", list_type="followers")
        events = [c.kwargs.get("event") for c in dx.capture.call_args_list]
        self.assertIn("scroll_stale_triggering_reopen", events)
        self.assertIn("scroll_reopen_failed", events)

    @patch("instat.scroll_loop.human_delay", return_value=0)
    def test_partial_coverage_captures(self, _hd):
        from instat.exceptions import BlockedError
        from instat.scroll_loop import ScrollLoop
        dx = MagicMock()
        sel = MagicMock()
        sel.get.return_value = "span"
        loop = ScrollLoop(
            driver=MagicMock(),
            selectors=sel,
            reopen_modal=lambda *a, **kw: True,
            warmup_threshold=0, warmup_stale_rounds=2,
            completion_threshold=0.90,
            diagnostics=dx,
        )
        with patch("instat.scroll_loop.Utils") as MU, \
             patch.object(ScrollLoop, "_scroll_modal_js"):
            MU.batch_read_text.return_value = {f"u{i}" for i in range(50)}
            with self.assertRaises(BlockedError):
                loop.run(expected_count=100, max_duration=None)
        events = [c.kwargs.get("event") for c in dx.capture.call_args_list]
        self.assertIn("scroll_partial_coverage", events)


if __name__ == "__main__":
    unittest.main()
