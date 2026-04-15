"""
Interface abstrata para engines de extração do Instagram.
Cada engine (Selenium, Playwright, httpx) implementa estes métodos.
O EngineManager itera sobre engines e faz fallback em BlockedError.
"""
from abc import ABC, abstractmethod
from typing import Callable, Optional, Set


class BaseEngine(ABC):

    @abstractmethod
    def login(self, username: str, password: str, **kwargs) -> bool:
        """Autentica no Instagram. Retorna True se sucesso."""
        ...

    @abstractmethod
    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None) -> Set[str]:
        """
        Extrai perfis (followers ou following).
        existing_profiles: perfis já coletados (para checkpoint)
        max_duration: timeout em segundos
        on_batch: callback chamado com set parcial a cada batch
        Retorna set de usernames.
        Levanta BlockedError se bloqueado.
        """
        ...

    @abstractmethod
    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        """Retorna a contagem total de followers/following sem abrir modal."""
        ...

    @abstractmethod
    def quit(self) -> None:
        """Libera recursos (fecha browser, sessão, etc.)."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome legível da engine (ex: 'selenium', 'playwright', 'httpx')."""
        ...

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """True se as dependências desta engine estão instaladas."""
        ...
