"""
Backoff exponencial com jitter para retry inteligente.
Usado pelo InstaExtractor quando não encontra novos perfis,
para escalar o tempo de espera antes de cada refresh.
"""
import random

try:
    from instat.constants import human_delay
except ImportError:
    from constants import human_delay


class SmartBackoff:
    """
    Delays crescentes: base^0, base^1, base^2... até max_delay.
    Jitter multiplica por uniform(0.5, 1.5) para evitar padrão detectável.
    Reset após sucesso (novos perfis encontrados).
    """

    def __init__(self, base: float = 2.0, max_delay: float = 300.0, jitter: bool = True):
        self.base = base
        self.max_delay = max_delay
        self.jitter = jitter
        self.attempt = 0

    def wait(self) -> float:
        """Calcula delay, aplica jitter, executa human_delay, incrementa attempt."""
        delay = min(self.base ** self.attempt, self.max_delay)
        if self.jitter:
            delay *= random.uniform(0.5, 1.5)
        self.attempt += 1
        human_delay(delay, variance=delay * 0.2)
        return delay

    def reset(self):
        """Reseta contador após sucesso."""
        self.attempt = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.reset()
