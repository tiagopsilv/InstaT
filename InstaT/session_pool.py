"""
Pool de contas Instagram com rotação e cooldown automático.
Cada conta (Session) pode ter seu próprio proxy atribuído.
Quando uma conta é marcada como bloqueada, entra em cooldown e é pulada
até o cooldown expirar.
"""
import time
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Session:
    """Conta Instagram com estado de cooldown."""
    username: str
    password: str
    proxy: Optional[str] = None
    cooldown_until: float = 0.0
    fail_count: int = 0

    @property
    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until


class SessionPool:
    """
    Pool de Sessions com rotação round-robin.
    accounts: List[Dict] no formato [{'username': '...', 'password': '...'}, ...]
    proxy_pool: opcional, atribui um proxy de cada vez para cada conta.
    """
    DEFAULT_COOLDOWN = 3600          # 1h (rate limit típico)
    META_INTERSTITIAL_COOLDOWN = 21600  # 6h (precisa verificação manual)

    def __init__(self, accounts: List[Dict[str, str]], proxy_pool=None):
        if not accounts:
            raise ValueError("SessionPool requires at least one account")
        self.sessions: List[Session] = []
        for acc in accounts:
            username = acc.get('username') if isinstance(acc, dict) else None
            password = acc.get('password') if isinstance(acc, dict) else None
            if not username or not password:
                raise ValueError(
                    f"Account missing username or password: {acc!r}"
                )
            proxy = proxy_pool.get_next() if proxy_pool else None
            self.sessions.append(Session(
                username=username,
                password=password,
                proxy=proxy
            ))
        self._index = 0

    def get_available(self) -> Optional[Session]:
        """Retorna a próxima Session disponível em round-robin. None se nenhuma."""
        available = [s for s in self.sessions if s.is_available]
        if not available:
            return None
        session = available[self._index % len(available)]
        self._index += 1
        return session

    def available_sessions(self) -> List[Session]:
        """Retorna lista de Sessions atualmente disponíveis."""
        return [s for s in self.sessions if s.is_available]

    def mark_blocked(self, session: Session,
                     cooldown: Optional[float] = None) -> None:
        """
        Marca session como bloqueada e inicia cooldown.
        cooldown=None usa DEFAULT_COOLDOWN (1h).
        Para Meta interstitial/2FA, passar META_INTERSTITIAL_COOLDOWN (6h).
        """
        cd = cooldown if cooldown is not None else self.DEFAULT_COOLDOWN
        session.cooldown_until = time.time() + cd
        session.fail_count += 1

    def mark_success(self, session: Session) -> None:
        """Reseta cooldown e fail_count após uso bem-sucedido."""
        session.cooldown_until = 0.0
        session.fail_count = 0

    def all_blocked(self) -> bool:
        """True se TODAS as sessions estão em cooldown."""
        return not any(s.is_available for s in self.sessions)

    @property
    def total_count(self) -> int:
        return len(self.sessions)

    @property
    def available_count(self) -> int:
        return sum(1 for s in self.sessions if s.is_available)


__all__ = ['Session', 'SessionPool']
