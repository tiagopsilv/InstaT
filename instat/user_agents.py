"""User-Agent móvel coerente com o motor do navegador.

Declarar Chrome num motor Gecko (ou WebKit) é inconsistente com as APIs
que o próprio navegador expõe; cada família usa um UA da sua linha.
Formato Firefox: MDN "Firefox user agent string reference".
"""

_MOBILE_UAS = {
    'chromium': (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36"
    ),
    'firefox': (
        "Mozilla/5.0 (Android 14; Mobile; rv:142.0) Gecko/142.0 Firefox/142.0"
    ),
    'webkit': (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1"
    ),
}


def mobile_user_agent(browser_family: str) -> str:
    try:
        return _MOBILE_UAS[browser_family]
    except KeyError:
        raise ValueError(f"Unknown browser family: {browser_family!r}") from None


__all__ = ['mobile_user_agent']
