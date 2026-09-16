"""Validação positiva de uma sessão restaurada de cookies.

Compartilhada por Selenium (SessionRestorer) e Playwright: estar fora de
/accounts/login não basta — challenge, checkpoint e suspensão também
saem da tela de login. Sucesso exige URL limpa, `sessionid` presente e,
quando conhecido, o mesmo `ds_user_id` salvo no cache.
"""
from enum import Enum
from typing import Callable, Optional

try:
    from instat.block_detector import BlockDetector
except ImportError:
    from block_detector import BlockDetector  # type: ignore


class RestoreOutcome(Enum):
    OK = "ok"
    NO_COOKIES = "no_cookies"
    LOGIN = "login"
    BLOCKED = "blocked"
    NO_SESSION = "no_session"
    IDENTITY_MISMATCH = "identity_mismatch"
    ERROR = "error"


LOGIN_URL_MARKER = '/accounts/login'


def classify_restored_session(
    url: str,
    get_cookie_value: Callable[[str], Optional[str]],
    expected_ds_user_id: Optional[str],
) -> RestoreOutcome:
    url = (url or "").lower()
    if any(ind in url for ind in BlockDetector.URL_INDICATORS):
        return RestoreOutcome.BLOCKED
    if LOGIN_URL_MARKER in url:
        return RestoreOutcome.LOGIN
    if not get_cookie_value('sessionid'):
        return RestoreOutcome.NO_SESSION
    if expected_ds_user_id and get_cookie_value('ds_user_id') != str(expected_ds_user_id):
        return RestoreOutcome.IDENTITY_MISMATCH
    return RestoreOutcome.OK


__all__ = ['RestoreOutcome', 'classify_restored_session']
