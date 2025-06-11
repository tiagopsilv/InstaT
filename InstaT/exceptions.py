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