"""F1 — restauração de sessão com Firefox real contra o fake server.

Nada de Instagram nem conta real. Com INSTAT_F1_PRINT_DIR definido, salva
os prints 01-restaurada, 02-expirada e 03-challenge nesse diretório.
"""
import os
from pathlib import Path

import pytest

from instat.exceptions import AccountBlockedError
from instat.login import InstaLogin
from instat.session_cache import SessionCache

from .fake_server import STATE

pytestmark = pytest.mark.e2e

PRINT_DIR = os.environ.get("INSTAT_F1_PRINT_DIR")


def _login(fake, cache):
    return InstaLogin('testuser', 'testpass', headless=True, timeout=10,
                      session_cache=cache, base_url=fake.base_url)


def _print(driver, name, lines):
    if not PRINT_DIR:
        return
    driver.execute_script(
        "const d=document.createElement('div');"
        "d.style.cssText='position:fixed;left:0;right:0;bottom:0;z-index:99999;"
        "background:#111;color:#fff;font:14px/1.4 monospace;padding:8px;white-space:pre-wrap';"
        "d.textContent=arguments[0];document.body.appendChild(d);",
        "\n".join(lines),
    )
    Path(PRINT_DIR).mkdir(parents=True, exist_ok=True)
    driver.save_screenshot(str(Path(PRINT_DIR) / f"{name}.png"))


@pytest.fixture
def cache(tmp_path):
    return SessionCache(cache_dir=tmp_path)


def _seed_session(fake, cache):
    il = _login(fake, cache)
    try:
        assert il.login() is True
    finally:
        il.driver.quit()
    assert cache.load('testuser')
    return STATE.login_posts


def test_valid_session_restores_without_form(fake_instagram, cache):
    posts = _seed_session(fake_instagram, cache)
    il = _login(fake_instagram, cache)
    try:
        assert il.login() is True
        assert STATE.login_posts == posts
        assert il._session_restorer.last_outcome.value == 'ok'
        ua = il.driver.execute_script("return navigator.userAgent")
        assert 'Firefox/' in ua and 'Chrome/' not in ua
        _print(il.driver, "01-restaurada", [
            "F1 · sessão restaurada (fake server, Firefox real)",
            f"URL: {il.driver.current_url}",
            f"resultado: {il._session_restorer.last_outcome.value}",
            f"POSTs de login no restart: {STATE.login_posts - posts}",
            f"UA: {ua}",
        ])
    finally:
        il.driver.quit()


def test_revoked_session_falls_back_to_form_once(fake_instagram, cache):
    posts = _seed_session(fake_instagram, cache)
    STATE.revoked_sessions.update(STATE.session_cookies)
    il = _login(fake_instagram, cache)
    try:
        assert il.login() is True
        assert STATE.login_posts == posts + 1
        _print(il.driver, "02-expirada", [
            "F1 · sessão revogada no servidor → 1 login de formulário",
            f"URL final: {il.driver.current_url}",
            f"restauração: {il._session_restorer.last_outcome.value}",
            f"POSTs de login: {STATE.login_posts - posts}",
        ])
    finally:
        il.driver.quit()


def test_restored_session_in_challenge_raises_without_form(fake_instagram, cache):
    posts = _seed_session(fake_instagram, cache)
    STATE.mode = 'challenge'
    il = _login(fake_instagram, cache)
    try:
        with pytest.raises(AccountBlockedError) as exc:
            il.login()
        assert STATE.login_posts == posts
        assert '/challenge/' in il.driver.current_url
        _print(il.driver, "03-challenge", [
            "F1 · restauração caiu em challenge → para, sem formulário",
            f"URL: {il.driver.current_url.split('?')[0]}",
            f"restauração: {il._session_restorer.last_outcome.value}",
            f"exceção: AccountBlockedError ({exc.value.reason})",
            f"POSTs de login: {STATE.login_posts - posts}",
        ])
    finally:
        il.driver.quit()
