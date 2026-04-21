"""BlockDetector — isolates IG blocking-state detection.

Why this module exists:
  Instagram periodically changes the URL patterns / HTML signatures /
  DOM redirects used to signal "this account is being challenged".
  Before this module, the detection logic was mixed into InstaLogin,
  making updates risky — one bad regex could break login for every
  user. Now the patterns live here as data, detection is a pure
  function of `driver`, and adding a new signature is a one-line
  change without touching the login flow.

Design choices:
  - `check(driver)` returns `Optional[BlockInfo]` — None if clean, an
    instance if blocked. Never raises.
  - The caller (InstaLogin) decides what to DO (raise, cooldown, log).
    Separation of detection from reaction.
  - Rules are class-level data (URL_INDICATORS, HTML_SIGNATURES) so
    subclasses or instances can extend/override without monkey-patching.
  - No dependency on InstaLogin or any engine — only needs
    `driver.current_url`, `driver.title`, `driver.page_source`.

Extension guide:
  1. New URL pattern: add to URL_INDICATORS.
  2. New HTML signature: add to HTML_SIGNATURES.
  3. New detection mechanism (e.g. cookies, headers): subclass and
     override `extra_checks(driver)` which is called after the builtin
     checks and may also return a BlockInfo.
"""
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class BlockInfo:
    """Shape of a positive block-detection result.

    kind: categorical label of what triggered the detection; useful for
      metrics and for callers that want to react differently per type.
      Current values: 'url', 'html', 'login_loop'. New detectors should
      define new kinds with descriptive names.
    indicator: the concrete string that matched (url fragment or
      html substring). Do NOT include secret tokens (they're stripped
      by the caller before logging).
    """
    reason: str
    action: str
    kind: str
    indicator: str
    url: str
    title: str = ""


class BlockDetector:
    """Classifies whether the current Selenium document is in a
    blocked state. Rules are data; detection is a pure function."""

    # URL fragment → (reason, suggested action to user).
    # Matched case-insensitively as substring of driver.current_url.
    URL_INDICATORS: Dict[str, Tuple[str, str]] = {
        'challenge': (
            "Desafio de segurança (challenge)",
            "Abra o Instagram no navegador e resolva o desafio manualmente.",
        ),
        'checkpoint': (
            "Checkpoint de verificação",
            "Verifique o e-mail ou SMS associado à conta e confirme a identidade.",
        ),
        'auth_platform': (
            "Verificação de plataforma Meta",
            "Acesse o e-mail da conta e siga as instruções de verificação.",
        ),
        'codeentry': (
            "Código de verificação exigido (2FA/e-mail)",
            "Insira o código enviado por e-mail/SMS no Instagram.",
        ),
        'two_factor': (
            "Autenticação de dois fatores (2FA)",
            "Use o app autenticador ou código SMS para completar o login.",
        ),
        'suspicious': (
            "Atividade suspeita detectada",
            "Faça login manual no navegador para desbloquear a conta.",
        ),
        'consent': (
            "Consentimento obrigatório (GDPR/termos)",
            "Aceite os termos de uso no navegador manualmente.",
        ),
    }

    # Lowercase substrings. Matched case-insensitively against
    # driver.page_source. Each entry: (signature, reason, action).
    HTML_SIGNATURES: List[Tuple[str, str, str]] = [
        (
            "o meta verified está disponível para o facebook e o instagram",
            "Intersticial Meta Verified",
            "Verifique o e-mail da conta para instruções de verificação Meta.",
        ),
        (
            "meta verified",
            "Intersticial Meta Verified",
            "Verifique o e-mail da conta para instruções de verificação Meta.",
        ),
    ]

    # The login-page-retained marker. Separate from URL_INDICATORS so
    # it runs AFTER html checks (a challenge that redirects through
    # /accounts/login briefly shouldn't be misread as credential loop).
    LOGIN_LOOP_MARKER = '/accounts/login'
    LOGIN_LOOP_REASON = "Credenciais inválidas ou login em loop"
    LOGIN_LOOP_ACTION = "Verifique username/password. A conta pode estar desativada."

    def check(self, driver: Any) -> Optional[BlockInfo]:
        """Return a BlockInfo if the driver is in a blocked state,
        else None. Never raises — a broken driver just returns None."""
        url = self._safe_url(driver)
        if not url:
            return None
        info = self._check_url(url, driver)
        if info is not None:
            return info
        info = self._check_html(url, driver)
        if info is not None:
            return info
        info = self.extra_checks(driver)
        if info is not None:
            return info
        return self._check_login_loop(url, driver)

    # --------------------- hooks for extension ---------------------

    def extra_checks(self, driver: Any) -> Optional[BlockInfo]:
        """Override point for custom detection (cookies, headers, new
        HTML shapes). Called between HTML-signature checks and the
        login-loop fallback. Default implementation is a no-op."""
        return None

    # ---------------------- internal helpers ----------------------

    @staticmethod
    def _safe_url(driver: Any) -> str:
        try:
            return driver.current_url or ""
        except Exception:
            return ""

    @staticmethod
    def _safe_title(driver: Any) -> str:
        try:
            return (driver.title or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _safe_page_source(driver: Any) -> str:
        try:
            return (driver.page_source or "").casefold()
        except Exception:
            return ""

    def _check_url(self, url: str, driver: Any) -> Optional[BlockInfo]:
        url_lower = url.lower()
        for indicator, (reason, action) in self.URL_INDICATORS.items():
            if indicator in url_lower:
                return BlockInfo(
                    reason=reason,
                    action=action,
                    kind='url',
                    indicator=indicator,
                    url=url,
                    title=self._safe_title(driver),
                )
        return None

    def _check_html(self, url: str, driver: Any) -> Optional[BlockInfo]:
        html = self._safe_page_source(driver)
        if not html:
            return None
        for sig, reason, action in self.HTML_SIGNATURES:
            if sig in html:
                return BlockInfo(
                    reason=reason,
                    action=action,
                    kind='html',
                    indicator=sig,
                    url=url,
                    title=self._safe_title(driver),
                )
        return None

    def _check_login_loop(self, url: str, driver: Any) -> Optional[BlockInfo]:
        if self.LOGIN_LOOP_MARKER in url.lower():
            return BlockInfo(
                reason=self.LOGIN_LOOP_REASON,
                action=self.LOGIN_LOOP_ACTION,
                kind='login_loop',
                indicator=self.LOGIN_LOOP_MARKER,
                url=url,
                title=self._safe_title(driver),
            )
        return None


__all__ = ['BlockDetector', 'BlockInfo']
