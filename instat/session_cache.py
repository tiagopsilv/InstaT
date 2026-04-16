"""
Cache de sessão/cookies do Instagram.
Persiste cookies em disco para reutilizar sessão sem re-login.
Re-login frequente é o maior gatilho de bloqueio do Instagram.
Cookies válidos por max_age (default 1h).
"""
import json
import os
import time
from pathlib import Path
from typing import List, Optional


def _restrict_permissions(path: Path) -> None:
    """chmod 0600 no POSIX; no-op silencioso em Windows (sem chmod real).

    Cookies aqui contêm sessionid — se outro usuário no sistema ler este
    arquivo, impersona a conta IG. chmod 0600 faz ele ser lido apenas pelo
    dono no POSIX. Em Windows, ACLs são herdadas do diretório pai.
    """
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


class SessionCache:
    """
    Arquivo: .instat_sessions/{username}.json
    Formato: {'cookies': [...], 'saved_at': float}
    """

    def __init__(self, cache_dir: str = '.instat_sessions'):
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, username: str, cookies: list) -> None:
        """Salva lista de cookies com timestamp. Arquivo recebe chmod 0600."""
        path = self._dir / f'{username}.json'
        data = {'cookies': cookies, 'saved_at': time.time()}
        path.write_text(json.dumps(data), encoding='utf-8')
        _restrict_permissions(path)

    def load(self, username: str, max_age: int = 3600) -> Optional[List[dict]]:
        """Carrega cookies se existirem e não estiverem expirados."""
        path = self._dir / f'{username}.json'
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding='utf-8'))
        if time.time() - data['saved_at'] > max_age:
            return None
        return data['cookies']

    def clear(self, username: str) -> None:
        """Remove arquivo de cookies."""
        (self._dir / f'{username}.json').unlink(missing_ok=True)
