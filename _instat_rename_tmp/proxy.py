"""
Pool de proxies HTTP/SOCKS5 com rotação round-robin e cooldown automático.

Uso:
    pool = ProxyPool(['http://user:pass@p1:8080', 'socks5://p2:1080'])
    proxy = pool.get_next()  # 'http://user:pass@p1:8080'
    # ... requisição falha ...
    pool.mark_failed(proxy)
    # ... próxima chamada pula proxy em cooldown ...
    proxy = pool.get_next()  # 'socks5://p2:1080'
"""
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class ProxyState:
    """Estado de um proxy individual no pool."""
    url: str
    fail_count: int = 0
    cooldown_until: float = 0.0

    @property
    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until


class ProxyPool:
    """
    Gerencia uma lista de proxies com rotação round-robin.
    Proxies que falham entram em cooldown (default 30 min) e são pulados.
    Após o cooldown expirar, voltam ao rotation automaticamente.
    """
    COOLDOWN = 1800  # 30 min em segundos

    def __init__(self, proxies: List[str]):
        # Filtra strings vazias/None mas preserva ordem
        self._proxies: List[ProxyState] = [
            ProxyState(url=p.strip()) for p in proxies if p and p.strip()
        ]
        self._index = 0

    def get_next(self) -> Optional[str]:
        """
        Retorna o próximo proxy disponível em round-robin.
        Retorna None se TODOS os proxies estão em cooldown ou pool vazio.
        """
        available = [p for p in self._proxies if p.is_available]
        if not available:
            return None
        proxy = available[self._index % len(available)]
        self._index += 1
        return proxy.url

    def mark_failed(self, url: str, cooldown: Optional[float] = None) -> None:
        """Marca proxy como falho e inicia cooldown."""
        cd = cooldown if cooldown is not None else self.COOLDOWN
        for p in self._proxies:
            if p.url == url:
                p.fail_count += 1
                p.cooldown_until = time.time() + cd
                return

    def mark_success(self, url: str) -> None:
        """Reseta contador de falhas e cooldown do proxy."""
        for p in self._proxies:
            if p.url == url:
                p.fail_count = 0
                p.cooldown_until = 0.0
                return

    @property
    def available_count(self) -> int:
        """Número de proxies atualmente disponíveis."""
        return sum(1 for p in self._proxies if p.is_available)

    @property
    def total_count(self) -> int:
        """Número total de proxies no pool."""
        return len(self._proxies)

    @classmethod
    def from_file(cls, path: str) -> 'ProxyPool':
        """
        Carrega proxies de arquivo de texto (1 proxy por linha).
        Linhas em branco e whitespace são ignoradas.
        """
        lines = Path(path).read_text(encoding='utf-8').strip().splitlines()
        return cls([p.strip() for p in lines if p.strip()])


__all__ = ['ProxyState', 'ProxyPool']
