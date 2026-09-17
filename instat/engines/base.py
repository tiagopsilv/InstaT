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

    def get_recent_posts(self, profile_id: str, limit: int = 5) -> list:
        """Retorna até `limit` posts recentes com métricas de engajamento.

        Default: raise NotImplementedError. Engines que não suportam
        (Selenium DOM scraping fica caro pra cada post) deixam o
        EngineManager cascatear pra próxima engine. Engines que
        suportam (HttpxEngine via API privada) sobrescrevem.

        Retorna lista de PostMetrics (instat.post_metrics)."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support get_recent_posts. "
            "Include httpx engine in your cascade for post metrics."
        )

    def get_profile_info(self, profile_id: str):
        """Metadados do cabeçalho do perfil (instat.profile_info.ProfileInfo).

        Default: raise NotImplementedError — mesmo padrão de get_recent_posts.
        Não é abstrato: subclasses legadas continuam instanciáveis, e o
        EngineManager/InstaExtractor pulam engines sem a capacidade.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support get_profile_info."
        )

    @property
    def capabilities(self) -> frozenset:
        """Capacidades explícitas da engine.

        Derivadas por padrão: {'extract', 'total_count'} mais 'profile_info' /
        'recent_posts' quando a subclasse sobrescreve o método. Uma engine pode
        declarar o conjunto explicitamente (atributo ou property) — opt-in.
        """
        caps = {"extract", "total_count"}
        cls = type(self)
        if cls.get_profile_info is not BaseEngine.get_profile_info:
            caps.add("profile_info")
        if cls.get_recent_posts is not BaseEngine.get_recent_posts:
            caps.add("recent_posts")
        return frozenset(caps)

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
