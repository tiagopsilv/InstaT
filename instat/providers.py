"""Provider helpers — pre-configured engines for hosted browser
services (Bright Data Scraping Browser today; trivially extendable to
Browserless, Browserbase, etc.).

InstaT stays a library: these helpers return ready-to-use `BaseEngine`
instances that the caller plugs into `InstaExtractor(engines=[...])`.
The orchestration choice (primary vs fallback, when to invoke, how to
handle quota) lives in the consumer pipeline.

Why these helpers exist:
  - Each provider has a non-obvious URL format (domain, port, auth
    encoding) plus tuning knobs that are easy to get wrong (e.g. BD
    Selenium needs `pageLoadStrategy='eager'` or it hangs forever on
    Instagram, validated in InstaT smoke test 2026-05-14).
  - Wrapping the format here means a consumer only deals with their
    own credentials, not provider URL shapes.

Usage as fallback in the cascade (canonical pattern):

    from instat import InstaExtractor
    from instat.providers import brightdata_playwright_engine

    brd = brightdata_playwright_engine(
        customer_id="hl_xxxxxxxx",
        zone="scraping_browser1",
        password="<zone-password>",
    )

    ext = InstaExtractor(
        user, pw,
        engines=["selenium", brd, "httpx"],
        # cascade: local Selenium first; if BlockedError, BD Scraping
        # Browser; httpx last for post-metrics + cookie handoff.
    )
"""
import base64
from typing import Any, Callable

try:
    from instat.engines.playwright_engine import PlaywrightEngine
    from instat.engines.selenium_engine import SeleniumEngine
except ImportError:
    from engines.playwright_engine import PlaywrightEngine  # type: ignore
    from engines.selenium_engine import SeleniumEngine  # type: ignore


# Bright Data hostnames are stable. Ports differ per protocol:
#   9222 = WebSocket CDP (Playwright/Puppeteer)
#   9515 = HTTP WebDriver (Selenium)
_BRD_HOST = "brd.superproxy.io"
_BRD_CDP_PORT = 9222
_BRD_WEBDRIVER_PORT = 9515


def brightdata_basic_auth(
    customer_id: str, zone: str, password: str,
) -> str:
    """Return the `Authorization: Basic <b64>` header VALUE for BD.

    BD's recommended auth pattern (per 2026-05-15 support guidance):
        Authorization: Basic base64(brd-customer-{id}-zone-{zone}:{pw})

    Returns the value part only (the caller adds the `Basic ` prefix
    or passes it as the full header — `auth_mode='header'` builds the
    full `Basic <value>` form internally).
    """
    if not all([customer_id, zone, password]):
        raise ValueError(
            "customer_id, zone, and password are all required"
        )
    username = f"brd-customer-{customer_id}-zone-{zone}"
    blob = f"{username}:{password}".encode("utf-8")
    return base64.b64encode(blob).decode("ascii")


def brightdata_cdp_endpoint(
    customer_id: str, zone: str, password: str,
) -> str:
    """Construct BD's WebSocket CDP endpoint.

    Format follows BD docs:
        wss://brd-customer-{id}-zone-{zone}:{password}@{host}:9222
    """
    if not all([customer_id, zone, password]):
        raise ValueError(
            "customer_id, zone, and password are all required"
        )
    return (
        f"wss://brd-customer-{customer_id}-zone-{zone}:{password}"
        f"@{_BRD_HOST}:{_BRD_CDP_PORT}"
    )


def brightdata_webdriver_endpoint(
    customer_id: str, zone: str, password: str,
) -> str:
    """Construct BD's HTTP WebDriver endpoint (Selenium)."""
    if not all([customer_id, zone, password]):
        raise ValueError(
            "customer_id, zone, and password are all required"
        )
    return (
        f"https://brd-customer-{customer_id}-zone-{zone}:{password}"
        f"@{_BRD_HOST}:{_BRD_WEBDRIVER_PORT}"
    )


def brightdata_playwright_engine(
    customer_id: str,
    zone: str,
    password: str,
    *,
    timeout: int = 60_000,
    connect_mode: str = "cdp",
    auth_mode: str = "header",
) -> PlaywrightEngine:
    """Pre-configured PlaywrightEngine connecting to Bright Data
    Scraping Browser via WebSocket CDP.

    Args:
        customer_id: BD customer ID (e.g. "hl_XXXXXXXX").
        zone: BD zone name (e.g. "scraping_browser1").
        password: zone password (rotate after exposure).
        timeout: Playwright default timeout in milliseconds (BD over
            WAN benefits from a generous value; 60 s default).
        connect_mode: "cdp" (default) for the standard CDP protocol;
            "ws" for Playwright Server protocol (rarely needed for BD).
        auth_mode:
            "header" (default, BD-recommended per 2026-05-15 support
              guidance): connect to `wss://brd.superproxy.io:9222` and
              pass credentials via `Authorization: Basic <b64>` header.
              Cleaner — no creds in WebSocket URL logs.
            "url": legacy pattern with credentials embedded in the
              endpoint (`wss://brd-customer-X-zone-Y:PWD@...`). Still
              accepted by BD; preserved for backward compat.

    Returns:
        PlaywrightEngine ready to be passed to
        InstaExtractor(engines=[...]).

    Compliance note (per BD support guidance 2026-05-15):
        BD's `scraping_browser1` zone allows Instagram targeting but
        ONLY for public content. Automated logins are explicitly
        forbidden. Use this engine for public-profile / hashtag /
        explore scraping, NOT for the InstaT logged-in cascade
        (followers/following lists). For logged extraction stay on
        Selenium local + residential proxies via `proxies=[...]`.
    """
    if auth_mode not in ("header", "url"):
        raise ValueError(
            f"auth_mode must be 'header' or 'url', got {auth_mode!r}"
        )
    if auth_mode == "header":
        endpoint = f"wss://{_BRD_HOST}:{_BRD_CDP_PORT}"
        credential = brightdata_basic_auth(customer_id, zone, password)
        headers = {"Authorization": f"Basic {credential}"}
        return PlaywrightEngine(
            connect_endpoint=endpoint,
            connect_mode=connect_mode,
            connect_headers=headers,
            timeout=timeout,
        )
    return PlaywrightEngine(
        connect_endpoint=brightdata_cdp_endpoint(
            customer_id, zone, password,
        ),
        connect_mode=connect_mode,
        timeout=timeout,
    )


def brightdata_selenium_engine(
    customer_id: str,
    zone: str,
    password: str,
    *,
    timeout: int = 30,
) -> SeleniumEngine:
    """Pre-configured SeleniumEngine connecting to Bright Data
    Scraping Browser via WebDriver port 9515.

    Args:
        customer_id: BD customer ID.
        zone: BD zone name.
        password: zone password.
        timeout: WebDriverWait timeout in seconds.

    Returns:
        SeleniumEngine ready for InstaExtractor(engines=[...]).

    Why pageLoadStrategy='eager':
        BD's full Chrome desktop hangs indefinitely on `driver.get()`
        for Instagram with the default 'normal' strategy (waits for
        load event that IG never reliably emits behind heavy SPA JS).
        'eager' returns at DOMContentLoaded, which is enough for
        WebDriverWait to find login form inputs. Discovered in the
        InstaT smoke test 2026-05-14.

    `headless` parameter not exposed — controlled server-side by BD.
    """
    endpoint = brightdata_webdriver_endpoint(
        customer_id, zone, password,
    )
    factory = _make_brightdata_webdriver_factory(endpoint)
    return SeleniumEngine(
        headless=True,           # ignored by BD remote, kept for InstaLogin contract
        timeout=timeout,
        webdriver_factory=factory,
    )


def _make_brightdata_webdriver_factory(
    endpoint: str,
) -> Callable[[bool], Any]:
    """Closure capturing the BD endpoint; returns a factory matching
    the SeleniumEngine.webdriver_factory contract.

    Signature is `(headless: bool) -> WebDriver`; the headless arg is
    accepted but ignored (BD controls rendering server-side)."""

    def factory(headless: bool) -> Any:
        # `headless` accepted to match the SeleniumEngine.webdriver_factory
        # contract but unused here — BD controls rendering server-side.
        del headless
        # Imports inside the factory so that calling the helper
        # without invoking the engine doesn't require selenium
        # being importable (matches lazy-load pattern used elsewhere
        # in the codebase).
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        opts = Options()
        opts.set_capability("pageLoadStrategy", "eager")
        return webdriver.Remote(
            command_executor=endpoint,
            options=opts,
        )

    return factory


__all__ = [
    "brightdata_basic_auth",
    "brightdata_cdp_endpoint",
    "brightdata_webdriver_endpoint",
    "brightdata_playwright_engine",
    "brightdata_selenium_engine",
]
