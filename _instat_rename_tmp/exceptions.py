# instat/exceptions.py

class LoginError(Exception):
    """Exceção para falhas de login no Instagram."""
    pass

class ProfileNotFoundError(Exception):
    """Exceção para perfis não encontrados ou privados."""
    pass

class RateLimitError(Exception):
    """Exceção para limites de requisição atingidos."""
    pass

class AccountBlockedError(Exception):
    """Exceção para conta bloqueada por checkpoint, 2FA, ou verificação obrigatória."""
    def __init__(self, message, *, reason, url=None, screenshot_path=None):
        super().__init__(message)
        self.reason = reason
        self.url = url
        self.screenshot_path = screenshot_path

class BlockedError(Exception):
    """Instagram bloqueou esta engine/sessão."""
    pass

class AllEnginesBlockedError(Exception):
    """Todas as engines foram bloqueadas."""
    pass
