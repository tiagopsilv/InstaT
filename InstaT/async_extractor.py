"""
AsyncInstaExtractor: wrapper async sobre InstaExtractor.

Delega a API síncrona para threads via asyncio.to_thread (Python 3.9+).
Permite uso natural em aplicações async (FastAPI, aiohttp, etc.) e
extração paralela de múltiplos perfis via asyncio.gather.

Uso:
    import asyncio
    from instat import AsyncInstaExtractor

    async def main():
        async with AsyncInstaExtractor('user', 'pass', headless=True) as ext:
            followers = await ext.get_followers('target', max_duration=60)

            # Paralelo (cria extractor por profile para evitar serialização):
            async def _fetch(pid):
                async with AsyncInstaExtractor('u', 'p') as e:
                    return await e.get_followers(pid)

            r1, r2 = await asyncio.gather(_fetch('p1'), _fetch('p2'))

    asyncio.run(main())

LIMITAÇÃO: com 1 instância compartilhada, chamadas paralelas são serializadas
pelo Selenium (single browser). Para paralelismo real, use 1 instância por
profile conforme exemplo acima.
"""
import asyncio
from typing import Dict, List, Optional

try:
    from instat.exporters import BaseExporter
    from instat.extractor import InstaExtractor
except ImportError:  # pragma: no cover
    from exporters import BaseExporter  # type: ignore
    from extractor import InstaExtractor  # type: ignore


class AsyncInstaExtractor:
    """Wrapper async sobre InstaExtractor. API idêntica à síncrona."""

    def __init__(self, username: str, password: str,
                 headless: bool = True, timeout: int = 10,
                 proxies: Optional[List[str]] = None,
                 accounts: Optional[List[Dict[str, str]]] = None,
                 engines: Optional[List[str]] = None,
                 exporter: Optional[BaseExporter] = None):
        """
        Guarda config para lazy init. O login real acontece em
        _ensure_initialized na primeira chamada (construtor não pode awaitar).
        """
        self._config = {
            'username': username,
            'password': password,
            'headless': headless,
            'timeout': timeout,
            'proxies': proxies,
            'accounts': accounts,
            'engines': engines,
            'exporter': exporter,
        }
        self._sync: Optional[InstaExtractor] = None
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Inicializa o InstaExtractor síncrono uma única vez, thread-safe."""
        if self._sync is not None:
            return
        async with self._init_lock:
            if self._sync is not None:
                return
            self._sync = await asyncio.to_thread(
                InstaExtractor, **self._config
            )

    async def get_followers(self, profile_id: str,
                            max_duration: Optional[float] = None) -> List[str]:
        await self._ensure_initialized()
        assert self._sync is not None
        return await asyncio.to_thread(
            self._sync.get_followers, profile_id, max_duration
        )

    async def get_following(self, profile_id: str,
                            max_duration: Optional[float] = None) -> List[str]:
        await self._ensure_initialized()
        assert self._sync is not None
        return await asyncio.to_thread(
            self._sync.get_following, profile_id, max_duration
        )

    async def get_total_count(self, profile_id: str,
                              list_type: str) -> Optional[int]:
        await self._ensure_initialized()
        assert self._sync is not None
        return await asyncio.to_thread(
            self._sync.get_total_count, profile_id, list_type
        )

    async def to_csv(self, profile_id: str, list_type: str, path: str,
                     max_duration: Optional[float] = None) -> List[str]:
        await self._ensure_initialized()
        assert self._sync is not None
        return await asyncio.to_thread(
            self._sync.to_csv, profile_id, list_type, path, max_duration
        )

    async def to_json(self, profile_id: str, list_type: str, path: str,
                      max_duration: Optional[float] = None,
                      indent: int = 2) -> List[str]:
        await self._ensure_initialized()
        assert self._sync is not None
        return await asyncio.to_thread(
            self._sync.to_json, profile_id, list_type, path, max_duration, indent
        )

    async def to_sqlite(self, profile_id: str, list_type: str, db_path: str,
                        table: str = 'profiles',
                        max_duration: Optional[float] = None) -> List[str]:
        await self._ensure_initialized()
        assert self._sync is not None
        return await asyncio.to_thread(
            self._sync.to_sqlite, profile_id, list_type, db_path,
            table, max_duration
        )

    @staticmethod
    def parse_count_text(text: str) -> int:
        """Utilidade pura — não precisa ser async."""
        return InstaExtractor.parse_count_text(text)

    async def close(self) -> None:
        """Fecha o WebDriver/browser/client subjacente. Idempotente."""
        if self._sync is None:
            return
        sync_ref = self._sync
        self._sync = None
        await asyncio.to_thread(sync_ref.quit)

    async def __aenter__(self) -> "AsyncInstaExtractor":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


__all__ = ["AsyncInstaExtractor"]
