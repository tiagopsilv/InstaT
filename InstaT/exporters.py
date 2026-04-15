"""
Exporters para salvar resultados de extração em CSV, JSON, SQLite ou callback custom.
Todos usam stdlib — sem dependências externas.

BaseExporter define o contrato. Subclasses implementam export(profiles, metadata).

metadata é um dict com:
  profile_id: str         — perfil alvo
  list_type: str          — 'followers' | 'following'
  count: int              — número de perfis
  timestamp: float        — time.time() do início
  duration_seconds: float — duração da extração
"""
import csv
import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable


class BaseExporter(ABC):
    """Interface abstrata para exporters de perfis."""

    @abstractmethod
    def export(self, profiles: list, metadata: dict) -> None:
        """
        Exporta lista de usernames + metadata para o destino.

        profiles: List[str] de usernames
        metadata: dict com chaves obrigatórias profile_id, list_type, count,
                  timestamp, duration_seconds
        """
        ...

    @staticmethod
    def _iso_from_timestamp(ts: float) -> str:
        """Converte timestamp Unix → ISO 8601 UTC."""
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    @staticmethod
    def _validate_metadata(metadata: dict) -> None:
        """Valida que metadata tem as chaves obrigatórias."""
        required = {'profile_id', 'list_type', 'count', 'timestamp', 'duration_seconds'}
        missing = required - set(metadata.keys())
        if missing:
            raise ValueError(f"metadata missing keys: {sorted(missing)}")


class CSVExporter(BaseExporter):
    """
    Salva perfis em CSV. Colunas: username, profile_id, list_type, extracted_at.
    Usa utf-8-sig (BOM) para compatibilidade com Excel.
    Reexportar sobrescreve o arquivo.
    """

    COLUMNS = ['username', 'profile_id', 'list_type', 'extracted_at']

    def __init__(self, path: str):
        self.path = path

    def export(self, profiles: list, metadata: dict) -> None:
        self._validate_metadata(metadata)
        extracted_at = self._iso_from_timestamp(metadata['timestamp'])
        profile_id = metadata['profile_id']
        list_type = metadata['list_type']

        with open(self.path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(self.COLUMNS)
            for username in profiles:
                writer.writerow([username, profile_id, list_type, extracted_at])


class JSONExporter(BaseExporter):
    """Salva {'metadata': {...}, 'profiles': [...]} em arquivo JSON."""

    def __init__(self, path: str, indent: int = 2):
        self.path = path
        self.indent = indent

    def export(self, profiles: list, metadata: dict) -> None:
        self._validate_metadata(metadata)
        payload = {
            'metadata': dict(metadata),
            'profiles': list(profiles),
        }
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=self.indent, ensure_ascii=False)


class SQLiteExporter(BaseExporter):
    """
    Salva perfis em tabela SQLite com PRIMARY KEY (username, profile_id, list_type).
    Reexportar é idempotente via INSERT OR IGNORE.
    """

    def __init__(self, db_path: str, table: str = 'profiles'):
        self.db_path = db_path
        if not table.replace('_', '').isalnum():
            raise ValueError(f"Invalid table name: {table!r}")
        self.table = table

    def export(self, profiles: list, metadata: dict) -> None:
        self._validate_metadata(metadata)
        extracted_at = self._iso_from_timestamp(metadata['timestamp'])
        profile_id = metadata['profile_id']
        list_type = metadata['list_type']

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    username TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    list_type TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    PRIMARY KEY (username, profile_id, list_type)
                )
            """)
            rows = [(u, profile_id, list_type, extracted_at) for u in profiles]
            conn.executemany(
                f"INSERT OR IGNORE INTO {self.table} "
                f"(username, profile_id, list_type, extracted_at) VALUES (?,?,?,?)",
                rows
            )
            conn.commit()
        finally:
            conn.close()


class CallbackExporter(BaseExporter):
    """Exporter que chama uma função arbitrária. Útil para S3/BigQuery/Kafka."""

    def __init__(self, callback: Callable[[list, dict], None]):
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback

    def export(self, profiles: list, metadata: dict) -> None:
        self._validate_metadata(metadata)
        self._callback(list(profiles), dict(metadata))


__all__ = [
    'BaseExporter', 'CSVExporter', 'JSONExporter',
    'SQLiteExporter', 'CallbackExporter'
]
