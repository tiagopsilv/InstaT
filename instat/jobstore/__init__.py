"""Persistência de jobs, execuções e páginas com posse e idempotência (roadmap §6.3, Fase 5).

Não altera o contrato público legado (`get_followers`, `PersistentStore`).
"""
from instat.jobstore.policy import HISTORY_LABEL, SANITY_V1
from instat.jobstore.store import BackupMisuse, BackupTimeout, JobStore

__all__ = ["BackupMisuse", "BackupTimeout", "HISTORY_LABEL", "JobStore", "SANITY_V1"]
