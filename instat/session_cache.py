"""
Cache de sessão/cookies do Instagram.
Persiste cookies em disco para reutilizar sessão sem re-login.
Re-login frequente é o maior gatilho de bloqueio do Instagram.
Cookies válidos por max_age (default 1h); restauração validada renova.

Arquivo inválido de qualquer forma (JSON, schema, tipos, data futura,
identidade inconsistente) é tratado como cache miss — nunca exceção.
"""
import json
import os
import tempfile
import time
from pathlib import Path
from typing import List, Optional

SCHEMA_VERSION = 2
FUTURE_TOLERANCE_S = 300
LEGACY_DIR = '.instat_sessions'


def default_session_dir() -> Path:
    """INSTAT_SESSION_DIR, senão ~/.instat/sessions (independe do cwd)."""
    env = os.environ.get('INSTAT_SESSION_DIR')
    if env:
        return Path(env)
    return Path.home() / '.instat' / 'sessions'


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


def _cookie_value(cookies: list, name: str) -> Optional[str]:
    for c in cookies:
        if c.get('name') == name:
            return c.get('value')
    return None


class SessionCache:
    """
    Arquivo: {dir}/{username}.json
    Formato v2: {'version': 2, 'username', 'backend', 'ds_user_id',
                 'saved_at': float, 'cookies': [...]}
    v1 legado ({'cookies', 'saved_at'}) continua legível.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self._explicit = cache_dir is not None
        self._dir = Path(cache_dir) if self._explicit else default_session_dir()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, username: str) -> Path:
        return self._dir / f'{username}.json'

    def save(self, username: str, cookies: list, backend: Optional[str] = None) -> None:
        """Grava atomicamente (temporário 0600 + os.replace)."""
        data = {
            'version': SCHEMA_VERSION,
            'username': username,
            'backend': backend,
            'ds_user_id': _cookie_value(cookies, 'ds_user_id'),
            'saved_at': time.time(),
            'cookies': cookies,
        }
        self._write(self._path(username), data)

    def touch(self, username: str) -> None:
        """Renova saved_at após uma restauração validada."""
        data = self._read(username)
        if data is None:
            return
        data['saved_at'] = time.time()
        if data.get('version') != SCHEMA_VERSION:
            data.update(version=SCHEMA_VERSION, username=username, backend=None,
                        ds_user_id=_cookie_value(data['cookies'], 'ds_user_id'))
        self._write(self._path(username), data)

    def _write(self, path: Path, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(prefix=f'.{path.stem}.', suffix='.tmp', dir=self._dir)
        try:
            _restrict_permissions(Path(tmp))
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        _restrict_permissions(path)

    def _read(self, username: str) -> Optional[dict]:
        candidates = [self._path(username)]
        if not self._explicit:
            candidates.append(Path(LEGACY_DIR) / f'{username}.json')
        for path in candidates:
            if path.exists():
                return self._validate(username, path)
        return None

    @staticmethod
    def _validate(username: str, path: Path) -> Optional[dict]:
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        version = data.get('version', 1)
        if version not in (1, SCHEMA_VERSION):
            return None
        saved_at, cookies = data.get('saved_at'), data.get('cookies')
        if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)):
            return None
        if not isinstance(cookies, list) or not all(isinstance(c, dict) for c in cookies):
            return None
        if saved_at > time.time() + FUTURE_TOLERANCE_S:
            return None
        if version == SCHEMA_VERSION:
            if data.get('username') != username:
                return None
            expected = data.get('ds_user_id')
            if expected is not None and _cookie_value(cookies, 'ds_user_id') != expected:
                return None
        return data

    def load(self, username: str, max_age: int = 3600) -> Optional[List[dict]]:
        """Carrega cookies se válidos e com idade < max_age."""
        data = self._read(username)
        if data is None or time.time() - data['saved_at'] >= max_age:
            return None
        return data['cookies']

    def expected_ds_user_id(self, username: str) -> Optional[str]:
        data = self._read(username)
        return data.get('ds_user_id') if data else None

    def clear(self, username: str) -> None:
        """Remove arquivo de cookies."""
        self._path(username).unlink(missing_ok=True)
