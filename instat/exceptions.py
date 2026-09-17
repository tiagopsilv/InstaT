# instat/exceptions.py

class LoginError(Exception):
    """Exceção para falhas de login no Instagram."""
    pass

class ProfileNotFoundError(Exception):
    """Exceção para perfis não encontrados ou privados."""
    pass

class RateLimitError(Exception):
    """Exceção para limites de requisição atingidos (429).

    retry_after: segundos pedidos pelo servidor via Retry-After, se houver.
    """
    def __init__(self, message="", *, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after

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

class ChallengeError(BlockedError):
    """Challenge/checkpoint exigido — precisa de atenção manual."""
    pass

class RestrictedError(BlockedError):
    """Restrição ou feedback do Instagram — congela a operação até revisão."""
    pass

class ProxyError(BlockedError):
    """Erro do proxy (ex.: 407 TRAFFIC_EXHAUSTED). Nunca é bloqueio do Instagram."""
    def __init__(self, message="", *, cause="unknown"):
        super().__init__(message)
        self.cause = cause

class TransientError(BlockedError):
    """Falha transitória (timeout, conexão, 5xx do serviço)."""
    def __init__(self, message="", *, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

class AllEnginesBlockedError(Exception):
    """Todas as engines foram bloqueadas."""
    pass

class ExtractionStoppedError(AllEnginesBlockedError):
    """O governador parou a extração sem nenhum perfil coletado.

    reason: motivo terminal (ex.: 'challenge', 'proxy:NO_RAY', 'budget:bytes').
    stop: o GovernorStop que originou a parada.
    """
    def __init__(self, message="", *, stop=None):
        super().__init__(message)
        self.stop = stop
        self.reason = getattr(stop, 'reason', None)
