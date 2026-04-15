import os
import shutil

import pytest

try:
    import selenium  # noqa: F401
    SELENIUM_OK = True
except ImportError:
    SELENIUM_OK = False

from .fake_server import STATE, FakeInstagramServer


def _firefox_available() -> bool:
    """Detecta se Firefox está instalado no sistema."""
    candidates = [
        r'C:\Program Files\Mozilla Firefox\firefox.exe',
        r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
        '/usr/bin/firefox',
        '/usr/bin/firefox-esr',
        '/Applications/Firefox.app/Contents/MacOS/firefox',
    ]
    for path in candidates:
        if os.path.exists(path):
            return True
    return shutil.which('firefox') is not None


@pytest.fixture(scope='session')
def fake_instagram():
    """Starts a fake Instagram HTTP server for the entire test session."""
    if not SELENIUM_OK:
        pytest.skip("Selenium not available")
    if not _firefox_available():
        pytest.skip("Firefox not installed — skipping E2E tests")

    server = FakeInstagramServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def reset_state():
    """Reset fake server state between tests."""
    STATE.mode = 'normal'
    yield
    STATE.mode = 'normal'
