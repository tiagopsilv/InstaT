"""Transcrição LITERAL do SQL e das regras da §6.3 do roadmap v13.5.

Não corrige nada: serve para demonstrar os defeitos do desenho.
Onde a v13.5 não especifica um comportamento, o adaptador escolhe a leitura
mais favorável ao desenho e registra isso no próprio método.
"""
import sqlite3

SCHEMA = """
CREATE TABLE accounts(account TEXT PRIMARY KEY, auth_state TEXT NOT NULL,
  restricted_until REAL, lease_owner TEXT, lease_until REAL,
  lease_gen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE endpoint_cooldowns(account TEXT, endpoint TEXT, cooldown_until REAL,
  reason TEXT, PRIMARY KEY(account, endpoint));
CREATE TABLE jobs(job_id TEXT PRIMARY KEY, target TEXT, list_type TEXT, backend TEXT,
  status TEXT NOT NULL, attempts INTEGER DEFAULT 0, end_reason TEXT, account TEXT,
  lease_gen INTEGER, cursor TEXT, cursor_context TEXT,
  pages_committed INTEGER NOT NULL DEFAULT 0,
  suspect_pages INTEGER NOT NULL DEFAULT 0, updated_at REAL);
CREATE TABLE pages(job_id TEXT, lease_gen INTEGER, account TEXT, cursor_in TEXT,
  cursor_out TEXT, n_items INTEGER, quality TEXT, reason TEXT, committed_at REAL,
  UNIQUE(job_id, lease_gen, cursor_in));
CREATE TABLE relations(target TEXT, list_type TEXT, member_pk TEXT, username TEXT,
  first_seen_at REAL, last_seen_at REAL, first_trusted_at REAL, source_job TEXT);
CREATE UNIQUE INDEX ux_rel_pk ON relations(target, list_type, member_pk)
  WHERE member_pk IS NOT NULL;
CREATE UNIQUE INDEX ux_rel_user ON relations(target, list_type, username)
  WHERE member_pk IS NULL;
"""


class V135:
    name = "v13.5 (SQL literal)"

    def __init__(self, path, ttl=30.0):
        self.path = path
        self.c = sqlite3.connect(path, isolation_level=None, timeout=0)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(SCHEMA)
        self.ttl = ttl

    # ---------------- contas ----------------
    def add_account(self, acct, auth="ok"):
        self.c.execute("INSERT INTO accounts(account, auth_state) VALUES(?,?)", (acct, auth))

    def set_auth(self, acct, state):
        self.c.execute("UPDATE accounts SET auth_state=? WHERE account=?", (state, acct))

    def expire(self, acct, now):
        self.c.execute("UPDATE accounts SET lease_until=? WHERE account=?", (now - 1, acct))

    def acquire(self, acct, owner, now, endpoint="followers"):
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            """UPDATE accounts SET lease_owner=?, lease_until=?, lease_gen=lease_gen+1
               WHERE account=? AND auth_state='ok'
                 AND (restricted_until IS NULL OR restricted_until<=?)
                 AND (lease_until IS NULL OR lease_until<=?)
                 AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns
                                  WHERE account=? AND endpoint=? AND cooldown_until>?)""",
            (owner, now + self.ttl, acct, now, now, acct, endpoint, now))
        if cur.rowcount != 1:
            c.execute("ROLLBACK")
            return None
        gen = c.execute("SELECT lease_gen FROM accounts WHERE account=?", (acct,)).fetchone()[0]
        c.execute("COMMIT")
        return (acct, owner, gen)

    def heartbeat(self, tok, now):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            """UPDATE accounts SET lease_until=?
               WHERE account=? AND lease_owner=? AND lease_gen=?
                 AND lease_until>? AND auth_state='ok'""",
            (now + self.ttl, acct, owner, gen, now))
        ok = cur.rowcount == 1
        c.execute("COMMIT")
        # v13.5: 0 linhas só "aciona should_stop"; o motivo não é distinguível.
        return "renewed" if ok else "unknown"

    # ---------------- jobs ----------------
    def create_job(self, job, target="alvo", list_type="followers", kind="cursor"):
        self.c.execute(
            "INSERT INTO jobs(job_id,target,list_type,backend,status) VALUES(?,?,?,?,'pending')",
            (job, target, list_type, kind))

    def claim(self, job, tok, now):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            """UPDATE jobs SET account=?, lease_gen=?, status='running', updated_at=?
               WHERE job_id=? AND status IN ('pending','running')
                 AND (account IS NULL
                      OR NOT EXISTS (SELECT 1 FROM accounts a
                                      WHERE a.account=jobs.account
                                        AND a.lease_gen=jobs.lease_gen
                                        AND a.lease_until>?))""",
            (acct, gen, now, job, now))
        ok = cur.rowcount == 1
        c.execute("COMMIT" if ok else "ROLLBACK")
        return ("ok" if ok else "rejected", None)

    def can_send(self, tok, job, run, now):
        # v13.5 só diz "o worker relê auth_state antes de cada operação remota".
        row = self.c.execute("SELECT auth_state FROM accounts WHERE account=?", (tok[0],)).fetchone()
        return (row is not None and row[0] == "ok"), "auth_state"

    def stop_run(self, tok, job, run, reason, now):
        # v13.5 não define encerramento de execução; leitura: libera a concessão.
        self.c.execute("UPDATE accounts SET lease_until=? WHERE account=? AND lease_owner=?",
                       (now, tok[0], tok[1]))

    def requeue(self, job, now):
        pass  # inexistente na v13.5

    def resume_point(self, job, run):
        cur = self.c.execute("SELECT cursor FROM jobs WHERE job_id=?", (job,)).fetchone()[0]
        return cur, None

    # ---------------- commit de página (solução C, v13.5) ----------------
    def commit(self, tok, job, run, *, attempt_id, pos, cursor_in, cursor_out, members,
               page_ok=True, now, **_ignored):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            """SELECT auth_state FROM accounts
               WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?""",
            (acct, owner, gen, now)).fetchone()
        if row is None:
            c.execute("ROLLBACK")
            return "rejected:posse"
        if row[0] != "ok":
            quality, reason = "suspect", "account_revoked"
        elif not members:
            quality, reason = "suspect", "empty_without_end"
        elif not page_ok:
            quality, reason = "suspect", "page_not_ok"
        else:
            quality, reason = "trusted", None
        try:
            c.execute(
                """INSERT INTO pages(job_id,lease_gen,account,cursor_in,cursor_out,n_items,
                                     quality,reason,committed_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (job, gen, acct, cursor_in, cursor_out, len(members), quality, reason, now))
        except sqlite3.IntegrityError:
            c.execute("ROLLBACK")
            return "idempotente(violacao UNIQUE)"
        if quality == "trusted":
            cur = c.execute(
                """UPDATE jobs SET cursor=?, pages_committed=pages_committed+1, updated_at=?
                   WHERE job_id=? AND account=? AND lease_gen=? AND status='running'
                     AND cursor IS ?""", (cursor_out, now, job, acct, gen, cursor_in))
        else:
            cur = c.execute(
                """UPDATE jobs SET suspect_pages=suspect_pages+1, updated_at=?
                   WHERE job_id=? AND account=? AND lease_gen=? AND status='running'
                     AND cursor IS ?""", (now, job, acct, gen, cursor_in))
        if cur.rowcount != 1:
            c.execute("ROLLBACK")
            return "rejected:job"
        target, lt = c.execute("SELECT target, list_type FROM jobs WHERE job_id=?", (job,)).fetchone()
        for pk, user in members:
            self._upsert_relation(target, lt, pk, user, job, now, quality == "trusted")
        c.execute("COMMIT")
        return f"committed:{quality}"

    def _upsert_relation(self, target, lt, pk, user, job, now, trusted):
        c = self.c
        ft = now if trusted else None
        if pk is not None:
            row = c.execute("SELECT rowid FROM relations WHERE target=? AND list_type=? AND member_pk=?",
                            (target, lt, pk)).fetchone()
            if row is None:
                row = c.execute("""SELECT rowid FROM relations WHERE target=? AND list_type=?
                                   AND username=? AND member_pk IS NULL""", (target, lt, user)).fetchone()
                if row is not None:  # sentido A: promove a linha sem pk
                    c.execute("""UPDATE relations SET member_pk=?, last_seen_at=?, source_job=?,
                                 first_trusted_at=COALESCE(first_trusted_at, ?) WHERE rowid=?""",
                              (pk, now, job, ft, row[0]))
                    return
                c.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)",
                          (target, lt, pk, user, now, now, ft, job))
                return
            c.execute("""UPDATE relations SET username=?, last_seen_at=?, source_job=?,
                         first_trusted_at=COALESCE(first_trusted_at, ?) WHERE rowid=?""",
                      (user, now, job, ft, row[0]))
            return
        row = c.execute("SELECT rowid FROM relations WHERE target=? AND list_type=? AND username=?",
                        (target, lt, user)).fetchone()
        if row is not None:  # sentido B / mesmo username
            c.execute("""UPDATE relations SET last_seen_at=?, source_job=?,
                         first_trusted_at=COALESCE(first_trusted_at, ?) WHERE rowid=?""",
                      (now, job, ft, row[0]))
            return
        c.execute("INSERT INTO relations VALUES(?,?,?,?,?,?,?,?)",
                  (target, lt, None, user, now, now, ft, job))

    # ---------------- leitura de estado ----------------
    def job_state(self, job, run=None):
        cursor, pc, sp = self.c.execute(
            "SELECT cursor, pages_committed, suspect_pages FROM jobs WHERE job_id=?", (job,)).fetchone()
        return {
            "progress_state": "indeterminado (v13.5 só tem cursor)",
            "cursor": cursor, "next_pos": None,
            "trusted_pages": pc, "suspect_total": sp, "suspect_open": None,
            # v13.5: "job com página suspect não termina completo"
            "can_complete": sp == 0,
        }

    def result_members(self, job, interpretation="global"):
        target, lt = self.c.execute("SELECT target, list_type FROM jobs WHERE job_id=?", (job,)).fetchone()
        if interpretation == "source_job":
            rows = self.c.execute("""SELECT username FROM relations
                                     WHERE source_job=? AND first_trusted_at IS NOT NULL""", (job,))
        else:
            rows = self.c.execute("""SELECT username FROM relations WHERE target=? AND list_type=?
                                     AND first_trusted_at IS NOT NULL""", (target, lt))
        return {r[0] for r in rows}

    def count_pages(self, job):
        return self.c.execute("SELECT COUNT(*) FROM pages WHERE job_id=?", (job,)).fetchone()[0]

    def count_members(self):
        return self.c.execute("SELECT COUNT(*) FROM relations").fetchone()[0]

    def merge_audit_count(self):
        return 0  # v13.5 não registra fusões

    # ---------------- backup (I9 v13.5) ----------------
    def backup_verify(self, src_path, dst_path, writer):
        # v13.5: "backup() a partir de uma conexão ativa ... contagens batem com a origem",
        # sem ponto de consistência definido para a comparação.
        dst = sqlite3.connect(dst_path)
        self.c.backup(dst, pages=-1)
        wrote = writer()
        origin = self.c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        copied = dst.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        return {"writer_committed_during_check": wrote, "origin": origin, "backup": copied,
                "integrity": integrity, "counts_match": origin == copied}
