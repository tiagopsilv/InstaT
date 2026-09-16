"""Provider helpers — Bright Data engine factories.

These tests pin the BD URL format and the SeleniumEngine page-load
strategy fix learned from the live smoke test (2026-05-14). If any of
these break, look at BD's docs first — they may have changed
host/port/path conventions."""
import importlib.util
import unittest
from unittest.mock import MagicMock, patch

from instat.engines.playwright_engine import PlaywrightEngine
from instat.engines.selenium_engine import SeleniumEngine
from instat.providers import (
    brightdata_basic_auth,
    brightdata_cdp_endpoint,
    brightdata_playwright_engine,
    brightdata_selenium_engine,
    brightdata_webdriver_endpoint,
)


class TestBrightDataEndpointConstruction(unittest.TestCase):

    def test_cdp_endpoint_format(self):
        url = brightdata_cdp_endpoint(
            customer_id="hl_TEST1234",
            zone="scraping_browser1",
            password="abc123",
        )
        self.assertEqual(
            url,
            "wss://brd-customer-hl_TEST1234-zone-scraping_browser1:abc123"
            "@brd.superproxy.io:9222",
        )

    def test_webdriver_endpoint_format(self):
        url = brightdata_webdriver_endpoint(
            customer_id="hl_TEST1234",
            zone="scraping_browser1",
            password="abc123",
        )
        self.assertEqual(
            url,
            "https://brd-customer-hl_TEST1234-zone-scraping_browser1:abc123"
            "@brd.superproxy.io:9515",
        )

    def test_missing_credentials_raise(self):
        with self.assertRaises(ValueError):
            brightdata_cdp_endpoint("", "zone", "pw")
        with self.assertRaises(ValueError):
            brightdata_cdp_endpoint("id", "", "pw")
        with self.assertRaises(ValueError):
            brightdata_cdp_endpoint("id", "zone", "")
        with self.assertRaises(ValueError):
            brightdata_webdriver_endpoint("", "zone", "pw")


class TestBrightDataBasicAuth(unittest.TestCase):
    """Header-mode auth — BD's 2026-05-15 recommended pattern."""

    def test_basic_auth_encodes_credentials(self):
        # Decoded should be brd-customer-<id>-zone-<zone>:<pw>.
        import base64
        encoded = brightdata_basic_auth(
            customer_id="hl_x", zone="scraping_browser1", password="p",
        )
        decoded = base64.b64decode(encoded).decode("ascii")
        self.assertEqual(
            decoded,
            "brd-customer-hl_x-zone-scraping_browser1:p",
        )

    def test_basic_auth_missing_credentials_raise(self):
        with self.assertRaises(ValueError):
            brightdata_basic_auth("", "zone", "p")
        with self.assertRaises(ValueError):
            brightdata_basic_auth("id", "", "p")
        with self.assertRaises(ValueError):
            brightdata_basic_auth("id", "zone", "")


class TestBrightDataPlaywrightEngine(unittest.TestCase):

    def test_returns_playwright_engine(self):
        eng = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        self.assertIsInstance(eng, PlaywrightEngine)

    def test_default_uses_header_auth_canonical_endpoint(self):
        # New default is header mode (BD-recommended).
        eng = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        self.assertEqual(
            eng._connect_endpoint,
            "wss://brd.superproxy.io:9222",
        )
        self.assertIsNotNone(eng._connect_headers)
        self.assertIn("Authorization", eng._connect_headers)
        self.assertTrue(
            eng._connect_headers["Authorization"].startswith("Basic ")
        )

    def test_url_mode_embeds_credentials_in_endpoint(self):
        # Backward-compat: explicit auth_mode='url' uses the embedded form.
        eng = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
            auth_mode="url",
        )
        self.assertEqual(
            eng._connect_endpoint,
            "wss://brd-customer-hl_x-zone-z:p@brd.superproxy.io:9222",
        )
        # No headers in URL mode.
        self.assertIsNone(eng._connect_headers)

    def test_invalid_auth_mode_raises(self):
        with self.assertRaises(ValueError):
            brightdata_playwright_engine(
                customer_id="hl_x", zone="z", password="p",
                auth_mode="invalid",
            )

    def test_default_connect_mode_is_cdp(self):
        eng = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        self.assertEqual(eng._connect_mode, "cdp")

    def test_timeout_propagated(self):
        eng = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
            timeout=120_000,
        )
        self.assertEqual(eng._timeout, 120_000)

    def test_connect_mode_override(self):
        eng = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
            connect_mode="ws",
        )
        self.assertEqual(eng._connect_mode, "ws")


class TestBrightDataSeleniumEngine(unittest.TestCase):

    def test_returns_selenium_engine(self):
        eng = brightdata_selenium_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        self.assertIsInstance(eng, SeleniumEngine)

    def test_factory_is_set(self):
        eng = brightdata_selenium_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        self.assertIsNotNone(eng._webdriver_factory)
        self.assertTrue(callable(eng._webdriver_factory))

    def test_factory_calls_remote_with_eager_strategy(self):
        # The fix learned from smoke test 2026-05-14: BD Chrome desktop
        # hangs forever with default 'normal' page_load_strategy on
        # Instagram. Pin this in a test so it doesn't regress.
        eng = brightdata_selenium_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        with patch("selenium.webdriver.Remote") as mock_remote, \
             patch("selenium.webdriver.chrome.options.Options") as mock_opts_cls:
            mock_opts = MagicMock()
            mock_opts_cls.return_value = mock_opts
            eng._webdriver_factory(headless=True)
            mock_opts.set_capability.assert_called_once_with(
                "pageLoadStrategy", "eager",
            )
            # Endpoint passed to webdriver.Remote must be the BD URL.
            kwargs = mock_remote.call_args.kwargs
            self.assertEqual(
                kwargs["command_executor"],
                "https://brd-customer-hl_x-zone-z:p@brd.superproxy.io:9515",
            )

    def test_factory_ignores_headless_arg(self):
        # BD controls rendering server-side; the headless flag from
        # InstaLogin must not change behaviour.
        eng = brightdata_selenium_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        with patch("selenium.webdriver.Remote") as mock_remote, \
             patch("selenium.webdriver.chrome.options.Options"):
            eng._webdriver_factory(headless=False)
            eng._webdriver_factory(headless=True)
            self.assertEqual(mock_remote.call_count, 2)


class TestProvidersIntegrateWithExtractor(unittest.TestCase):
    """End-to-end: a BD engine returned by the helper plugs into
    InstaExtractor(engines=[...]) without further setup."""

    @unittest.skipUnless(importlib.util.find_spec("playwright"), "extra opcional playwright não instalado")
    def test_brightdata_engine_accepted_in_engines_list(self):
        from unittest.mock import patch
        from instat.extractor import InstaExtractor

        brd = brightdata_playwright_engine(
            customer_id="hl_x", zone="z", password="p",
        )
        # Mock primary login so we don't actually launch a browser.
        with patch("instat.extractor.InstaLogin") as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor(
                "u", "p", headless=True,
                engines=["selenium", brd, "httpx"],
            )
        try:
            engine_names = [e.name for e in ext._engine_manager.engines]
            self.assertIn("selenium", engine_names)
            # Playwright engine name includes browser type.
            self.assertTrue(
                any(n.startswith("playwright") for n in engine_names),
                f"expected playwright-* in {engine_names}",
            )
        finally:
            ext.quit()


if __name__ == "__main__":
    unittest.main()
