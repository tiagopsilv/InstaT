"""Migração de `profiles_seen` (PersistentStore) para o JobStore (roadmap §6.3.11).

Dado legado não tem proveniência nem qualidade: entra como página `suspect`
(`legacy_import`) numa execução sintética encerrada, que não consome o limite
de execuções nem ocupa `current_run`. Nunca conta como confiável.
Backup verificado do banco legado ANTES da importação; o banco legado não é alterado.
"""
import os
import sqlite3
import uuid
from typing import Any, Dict, Optional

try:
    from instat.jobstore.canonical import content_hash, normalize_username
    from instat.jobstore.policy import SANITY_V1
    from instat.jobstore.store import JobStore
except ImportError:  # pragma: no cover
    from jobstore.canonical import content_hash, normalize_username  # type: ignore
    from jobstore.policy import SANITY_V1  # type: ignore
    from jobstore.store import JobStore  # type: ignore

LEGACY_ACCOUNT = "legacy_import"


def _backup_legacy(legacy_path: str, backup_dir: str) -> Dict[str, Any]:
    os.makedirs(backup_dir, exist_ok=True)
    dst = os.path.join(backup_dir, os.path.basename(legacy_path) + ".pre-migration.bak")
    partial = dst + ".partial"
    src = sqlite3.connect(legacy_path)
    try:
        out = sqlite3.connect(partial)
        try:
            src.backup(out, pages=-1)
        finally:
            out.close()
    finally:
        src.close()
    v = sqlite3.connect(partial)
    try:
        integrity = v.execute("PRAGMA integrity_check").fetchone()[0]
        copy_rows = v.execute("SELECT COUNT(*) FROM profiles_seen").fetchone()[0]
    finally:
        v.close()
    orig = sqlite3.connect(legacy_path)
    try:
        orig_rows = orig.execute("SELECT COUNT(*) FROM profiles_seen").fetchone()[0]
    finally:
        orig.close()
    ok = integrity == "ok" and copy_rows == orig_rows
    if ok:
        os.replace(partial, dst)
    else:
        os.remove(partial)
    return {"path": dst, "verification": {"ok": ok, "integrity": integrity, "rows": copy_rows,
                                          "origin_rows": orig_rows}}


def import_legacy_profiles_seen(store: JobStore, legacy_path: str, *, backup_dir: str,
                                now: Optional[float] = None) -> Dict[str, Any]:
    backup = _backup_legacy(legacy_path, backup_dir)
    if not backup["verification"]["ok"]:
        return {"status": "rejected:backup_nao_verificado", "backup": backup, "imported": 0, "jobs": []}
    legacy = sqlite3.connect(legacy_path)
    try:
        groups: Dict[tuple, list] = {}
        for pid, lt, user in legacy.execute(
                "SELECT profile_id, list_type, username FROM profiles_seen ORDER BY profile_id, list_type, username"):
            groups.setdefault((pid, lt), []).append(user)
    finally:
        legacy.close()

    c = store.c
    t = store._begin(now)
    jobs, imported = [], 0
    try:
        if c.execute("SELECT 1 FROM accounts WHERE account=?", (LEGACY_ACCOUNT,)).fetchone() is None:
            c.execute("INSERT INTO accounts(account, auth_state) VALUES(?, 'ok')", (LEGACY_ACCOUNT,))
        for (pid, lt), users in groups.items():
            job = f"legacy:{pid}:{lt}"
            if c.execute("SELECT 1 FROM jobs WHERE job_id=?", (job,)).fetchone() is None:
                c.execute("""INSERT INTO jobs(job_id, target, list_type, endpoint, progress_kind, status, end_reason,
                                              attempts, updated_at) VALUES(?,?,?,?, 'cursor', 'pending',
                                              'legacy_import', 0, ?)""", (job, pid, lt, lt, t))
            c.execute("""INSERT INTO runs(job_id, account, lease_owner, lease_gen, cursor_context, started_at,
                                          ended_at, stop_reason, progress_state, end_evidence, segment_started_at)
                         VALUES(?,?, 'legacy', 0, 'legacy_import', ?, ?, 'legacy_import', 'end_unknown',
                                'legacy_import', ?)""", (job, LEGACY_ACCOUNT, t, t, t))
            run = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            members = [(None, u) for u in users]
            chash = content_hash(run, 0, None, None, members, False, False, False, False, None, None)
            c.execute("""INSERT INTO pages(attempt_id, run_id, segment, pos, cursor_in, cursor_out, canonical_schema,
                                           content_hash, n_items, quality, reason, policy_version, received_at,
                                           committed_at)
                         VALUES(?,?,0,0,NULL,NULL,'page-v1',?,?, 'suspect','legacy_import',?,?,?)""",
                      (f"legacy-{uuid.uuid4()}", run, chash, len(members), SANITY_V1["version"], t, t))
            page = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            for u in users:
                mid = store._resolve_member(pid, lt, None, normalize_username(u), t)
                c.execute("INSERT OR IGNORE INTO observations(page_id, member_id, username_seen) VALUES(?,?,?)",
                          (page, mid, u))
                imported += 1
            jobs.append(job)
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")
    return {"status": "ok", "imported": imported, "jobs": jobs, "backup": backup}


__all__ = ["LEGACY_ACCOUNT", "import_legacy_profiles_seen"]
