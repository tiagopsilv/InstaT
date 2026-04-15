"""
Checkpoint incremental para extração de perfis.
Persiste progresso em disco a cada N perfis coletados.
Crash ou bloqueio = retoma de onde parou.
Checkpoints expiram após 24h.
"""
import json
import time
from pathlib import Path
from typing import Optional, Set


class ExtractionCheckpoint:
    """
    Arquivo: .instat_checkpoints/{profile_id}_{list_type}.json
    Formato: {'profiles': [...], 'count': N, 'timestamp': float}
    """
    EXPIRY_SECONDS = 86400  # 24h

    def __init__(self, profile_id: str, list_type: str,
                 checkpoint_dir: str = '.instat_checkpoints'):
        self._dir = Path(checkpoint_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / f'{profile_id}_{list_type}.json'

    def save(self, profiles: Set[str]) -> None:
        """Salva set de perfis em JSON com timestamp."""
        data = {'profiles': sorted(profiles),
                'count': len(profiles),
                'timestamp': time.time()}
        self._file.write_text(json.dumps(data), encoding='utf-8')

    def load(self) -> Optional[Set[str]]:
        """Carrega checkpoint se existir e não estiver expirado."""
        if not self._file.exists():
            return None
        data = json.loads(self._file.read_text(encoding='utf-8'))
        if time.time() - data['timestamp'] > self.EXPIRY_SECONDS:
            self.clear()
            return None
        return set(data['profiles'])

    def clear(self) -> None:
        """Remove arquivo de checkpoint."""
        self._file.unlink(missing_ok=True)
