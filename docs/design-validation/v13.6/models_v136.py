"""Modelo de referência do desenho PROPOSTO na §6.3 v13.6.

Não é implementação do InstaT. Existe para validar que o SQL e as regras do
documento atendem às invariantes antes da F5.
"""
import hashlib
import json
import math
import re
import sqlite3

POLICY_VERSION = "sanity-v1"
K_STAGNANT = 3          # rodadas sem membro novo, após replay, para fim confirmado (scan)
S_STUCK = 3             # telas idênticas seguidas → fim desconhecido
REPLAY_RATIO = 0.95     # cobertura dos membros confiáveis já conhecidos para encerrar a releitura
MAX_REREADS_PER_POS = 2
MAX_RUNS = 3
STALE_COUNTER_S = 1800
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

SCHEMA = """
CREATE TABLE accounts(
  account TEXT PRIMARY KEY,
  auth_state TEXT NOT NULL CHECK(auth_state IN ('ok','needs_attention','restricted')),
  restricted_until REAL, lease_owner TEXT, lease_until REAL,
  lease_gen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE endpoint_cooldowns(
  account TEXT NOT NULL, endpoint TEXT NOT NULL, cooldown_until REAL NOT NULL, reason TEXT,
  PRIMARY KEY(account, endpoint));
CREATE TABLE jobs(
  job_id TEXT PRIMARY KEY, target TEXT NOT NULL, list_type TEXT NOT NULL, endpoint TEXT NOT NULL,
  progress_kind TEXT NOT NULL CHECK(progress_kind IN ('cursor','scan')),
  status TEXT NOT NULL CHECK(status IN ('pending','running','partial','complete','failed')),
  end_reason TEXT, current_run INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
  requeued_at REAL, updated_at REAL);
CREATE TABLE runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  account TEXT NOT NULL, lease_owner TEXT NOT NULL, lease_gen INTEGER NOT NULL,
  cursor_context TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, stop_reason TEXT,
  progress_state TEXT NOT NULL DEFAULT 'not_started'
    CHECK(progress_state IN ('not_started','in_progress','end_confirmed','end_unknown')),
  next_pos INTEGER NOT NULL DEFAULT 0, trusted_cursor TEXT,
  replay_target INTEGER NOT NULL DEFAULT 0, replay_done INTEGER NOT NULL DEFAULT 0,
  rounds_without_new INTEGER NOT NULL DEFAULT 0, stuck_rounds INTEGER NOT NULL DEFAULT 0,
  last_screen_hash TEXT,
  counter_kind TEXT, counter_lo INTEGER, counter_hi INTEGER, counter_read_at REAL,
  last_commit_at REAL);
CREATE TABLE pages(
  page_id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL UNIQUE,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  pos INTEGER NOT NULL, cursor_in TEXT, cursor_out TEXT,
  content_hash TEXT NOT NULL, n_items INTEGER NOT NULL,
  quality TEXT NOT NULL CHECK(quality IN ('trusted','suspect')), reason TEXT,
  policy_version TEXT NOT NULL, received_at REAL NOT NULL, committed_at REAL NOT NULL);
CREATE UNIQUE INDEX ux_pages_trusted_pos ON pages(run_id, pos) WHERE quality = 'trusted';
CREATE TABLE members(
  member_id INTEGER PRIMARY KEY AUTOINCREMENT,
  target TEXT NOT NULL, list_type TEXT NOT NULL, member_pk TEXT, username TEXT NOT NULL,
  first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL,
  identity_state TEXT NOT NULL DEFAULT 'ok' CHECK(identity_state IN ('ok','ambiguous')));
CREATE UNIQUE INDEX ux_mem_pk ON members(target, list_type, member_pk) WHERE member_pk IS NOT NULL;
CREATE UNIQUE INDEX ux_mem_user ON members(target, list_type, username) WHERE member_pk IS NULL;
CREATE TABLE observations(
  page_id INTEGER NOT NULL REFERENCES pages(page_id),
  member_id INTEGER NOT NULL REFERENCES members(member_id),
  username_seen TEXT NOT NULL,
  PRIMARY KEY(page_id, member_id));
CREATE TABLE member_merges(
  merge_id INTEGER PRIMARY KEY AUTOINCREMENT,
  loser_member_id INTEGER NOT NULL, survivor_member_id INTEGER NOT NULL,
  merged_at REAL NOT NULL, reason TEXT NOT NULL);
"""

JOB_TRUSTED_MEMBERS = """
SELECT DISTINCT o.member_id
  FROM observations o
  JOIN pages p ON p.page_id = o.page_id
  JOIN runs  r ON r.run_id  = p.run_id
 WHERE r.job_id = ? AND p.quality = 'trusted'
"""


class V136:
    name = "v13.6 (desenho proposto)"

    def __init__(self, path, ttl=30.0):
        self.path = path
        self.c = sqlite3.connect(path, isolation_level=None, timeout=0)
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.executescript(SCHEMA)
        self.ttl = ttl

    # ---------------- utilitários ----------------
    def _rollback(self, result, run=None):
        self.c.execute("ROLLBACK")
        return (result, run)

    @staticmethod
    def _hash(**fields):
        return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()

    # ---------------- contas e concessão ----------------
    def add_account(self, acct, auth="ok"):
        self.c.execute("INSERT INTO accounts(account, auth_state) VALUES(?,?)", (acct, auth))

    def set_auth(self, acct, state):
        self.c.execute("UPDATE accounts SET auth_state=? WHERE account=?", (state, acct))

    def expire(self, acct, now):
        self.c.execute("UPDATE accounts SET lease_until=? WHERE account=?", (now - 1, acct))

    def acquire(self, acct, owner, now, endpoint="followers"):
        c = self.c
        c.execute("BEGIN IMMEDIATE")          # :now é calculado pelo chamador após o bloqueio (I7)
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
        if cur.rowcount == 1:
            c.execute("COMMIT")
            return "renewed"
        # Classificação na MESMA transação: ainda tem posse? então o motivo é restrição.
        held = c.execute(
            """SELECT 1 FROM accounts WHERE account=? AND lease_owner=? AND lease_gen=?
               AND lease_until>?""", (acct, owner, gen, now)).fetchone()
        c.execute("COMMIT")
        return "account_restricted" if held else "lease_lost"

    # ---------------- jobs e execuções ----------------
    def create_job(self, job, target="alvo", list_type="followers", kind="cursor"):
        self.c.execute(
            """INSERT INTO jobs(job_id, target, list_type, endpoint, progress_kind, status)
               VALUES(?,?,?,?,?,'pending')""", (job, target, list_type, list_type, kind))

    def claim(self, job, tok, now):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        try:
            j = c.execute("""SELECT status, current_run, attempts, endpoint, requeued_at
                             FROM jobs WHERE job_id=?""", (job,)).fetchone()
            if j is None:
                return self._rollback("rejected:job_inexistente")
            status, cur_run, attempts, endpoint, requeued_at = j
            # 1. solicitante: conta, proprietário, geração, validade e elegibilidade
            eligible = c.execute(
                """SELECT 1 FROM accounts
                    WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?
                      AND auth_state='ok'
                      AND (restricted_until IS NULL OR restricted_until<=?)
                      AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns
                                       WHERE account=? AND endpoint=? AND cooldown_until>?)""",
                (acct, owner, gen, now, now, acct, endpoint, now)).fetchone()
            if eligible is None:
                return self._rollback("rejected:solicitante_sem_concessao")
            if status in ("complete", "failed"):
                return self._rollback("rejected:job_encerrado")
            if attempts >= MAX_RUNS:
                return self._rollback("rejected:limite_de_execucoes")
            # 2. execução anterior
            if cur_run is not None:
                ended_at, stop_reason, live = c.execute(
                    """SELECT r.ended_at, r.stop_reason,
                              EXISTS(SELECT 1 FROM accounts a
                                      WHERE a.account=r.account AND a.lease_owner=r.lease_owner
                                        AND a.lease_gen=r.lease_gen AND a.lease_until>?)
                         FROM runs r WHERE r.run_id=?""", (now, cur_run)).fetchone()
                if ended_at is None and live:
                    return self._rollback("rejected:job_com_execucao_vigente")
                if ended_at is None:
                    c.execute("UPDATE runs SET ended_at=?, stop_reason='lease_lost' WHERE run_id=?",
                              (now, cur_run))
                    ended_at, stop_reason = now, "lease_lost"
                if stop_reason in ("account_restricted", "challenge") and \
                        not (requeued_at is not None and requeued_at > ended_at):
                    return self._rollback("rejected:aguardando_requeue")
            # 3. nova execução
            known = len(c.execute(JOB_TRUSTED_MEMBERS, (job,)).fetchall())
            c.execute(
                """INSERT INTO runs(job_id, account, lease_owner, lease_gen, cursor_context,
                                    started_at, replay_target, replay_done)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (job, acct, owner, gen, f"{acct}|gen{gen}|model", now, known, 1 if known == 0 else 0))
            run_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            cur = c.execute(
                """UPDATE jobs SET current_run=?, status='running', attempts=attempts+1, updated_at=?
                   WHERE job_id=? AND current_run IS ?""", (run_id, now, job, cur_run))
            if cur.rowcount != 1:
                return self._rollback("rejected:concorrencia")
            c.execute("COMMIT")
            return ("ok", run_id)
        except Exception:
            c.execute("ROLLBACK")
            raise

    def can_send(self, tok, job, run, now):
        acct, owner, gen = tok
        row = self.c.execute(
            """SELECT a.auth_state, r.ended_at, j.current_run
                 FROM accounts a
                 JOIN runs r ON r.account=a.account AND r.lease_owner=a.lease_owner
                            AND r.lease_gen=a.lease_gen
                 JOIN jobs j ON j.job_id=r.job_id
                WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?
                  AND r.run_id=? AND j.job_id=?""", (acct, owner, gen, now, run, job)).fetchone()
        if row is None:
            return False, "lease_lost"
        auth, ended, cur_run = row
        if cur_run != run or ended is not None:
            return False, "run_not_current"
        if auth != "ok":
            return False, "account_restricted"
        return True, "ok"

    def stop_run(self, tok, job, run, reason, now):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            """UPDATE runs SET ended_at=?, stop_reason=?
               WHERE run_id=? AND account=? AND lease_owner=? AND lease_gen=? AND ended_at IS NULL""",
            (now, reason, run, acct, owner, gen))
        if cur.rowcount == 1:
            c.execute("""UPDATE accounts SET lease_until=?
                         WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?""",
                      (now, acct, owner, gen, now))
            if reason in ("account_restricted", "challenge", "cancel_requested", "budget_exhausted"):
                c.execute("UPDATE jobs SET status='partial', end_reason=?, updated_at=? WHERE job_id=?",
                          (reason, now, job))
        c.execute("COMMIT")

    def requeue(self, job, now):
        self.c.execute("""UPDATE jobs SET requeued_at=?, status='pending', updated_at=?
                          WHERE job_id=? AND status IN ('partial','pending')""", (now, now, job))

    def resume_point(self, job, run):
        cur, pos = self.c.execute("SELECT trusted_cursor, next_pos FROM runs WHERE run_id=?",
                                  (run,)).fetchone()
        return cur, pos

    # ---------------- identidade de membros ----------------
    def _merge(self, loser, survivor, now, reason):
        c = self.c
        c.execute("""INSERT OR IGNORE INTO observations(page_id, member_id, username_seen)
                     SELECT page_id, ?, username_seen FROM observations WHERE member_id=?""",
                  (survivor, loser))
        c.execute("DELETE FROM observations WHERE member_id=?", (loser,))
        c.execute("""UPDATE members SET
                       first_seen_at = MIN(first_seen_at, (SELECT first_seen_at FROM members WHERE member_id=?)),
                       last_seen_at  = MAX(last_seen_at,  (SELECT last_seen_at  FROM members WHERE member_id=?))
                     WHERE member_id=?""", (loser, loser, survivor))
        c.execute("DELETE FROM members WHERE member_id=?", (loser,))
        c.execute("""INSERT INTO member_merges(loser_member_id, survivor_member_id, merged_at, reason)
                     VALUES(?,?,?,?)""", (loser, survivor, now, reason))

    def _resolve_member(self, target, lt, pk, user, now):
        c = self.c
        loose = c.execute("""SELECT member_id FROM members WHERE target=? AND list_type=?
                             AND username=? AND member_pk IS NULL""", (target, lt, user)).fetchone()
        loose = loose[0] if loose else None
        if pk is not None:
            row = c.execute("""SELECT member_id FROM members WHERE target=? AND list_type=?
                               AND member_pk=?""", (target, lt, pk)).fetchone()
            if row is None:
                if loose is not None:                    # sentido A: promove
                    c.execute("UPDATE members SET member_pk=?, last_seen_at=MAX(last_seen_at,?) WHERE member_id=?",
                              (pk, now, loose))
                    return loose
                c.execute("""INSERT INTO members(target, list_type, member_pk, username, first_seen_at, last_seen_at)
                             VALUES(?,?,?,?,?,?)""", (target, lt, pk, user, now, now))
                return c.execute("SELECT last_insert_rowid()").fetchone()[0]
            mid = row[0]
            c.execute("UPDATE members SET username=?, last_seen_at=MAX(last_seen_at,?) WHERE member_id=?",
                      (user, now, mid))
            if loose is not None and loose != mid:        # pk atual confirma o username: funde
                self._merge(loose, mid, now, "pk_confirms_username")
            return mid
        with_pk = [r[0] for r in c.execute(
            """SELECT member_id FROM members WHERE target=? AND list_type=?
               AND username=? AND member_pk IS NOT NULL""", (target, lt, user))]
        if len(with_pk) == 1:                             # sentido B
            c.execute("UPDATE members SET last_seen_at=MAX(last_seen_at,?) WHERE member_id=?", (now, with_pk[0]))
            return with_pk[0]
        state = "ambiguous" if len(with_pk) > 1 else "ok"
        if loose is not None:
            c.execute("UPDATE members SET last_seen_at=MAX(last_seen_at,?), identity_state=? WHERE member_id=?",
                      (now, state, loose))
            return loose
        c.execute("""INSERT INTO members(target, list_type, member_pk, username, first_seen_at,
                                         last_seen_at, identity_state) VALUES(?,?,?,?,?,?,?)""",
                  (target, lt, None, user, now, now, state))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _trusted_in_job(self, member_id, job):
        return self.c.execute(
            """SELECT 1 FROM observations o JOIN pages p ON p.page_id=o.page_id
               JOIN runs r ON r.run_id=p.run_id
               WHERE o.member_id=? AND r.job_id=? AND p.quality='trusted' LIMIT 1""",
            (member_id, job)).fetchone() is not None

    # ---------------- commit de página ----------------
    def commit(self, tok, job, run, *, attempt_id, pos, cursor_in, cursor_out, members,
               page_ok=True, now, screen_hash=None, loading=False, empty_state=False,
               counter=None, received_at=None, **_ignored):
        acct, owner, gen = tok
        c = self.c
        content_hash = self._hash(pos=pos, cursor_in=cursor_in, cursor_out=cursor_out,
                                  members=members, page_ok=page_ok, screen_hash=screen_hash,
                                  loading=loading, empty_state=empty_state, counter=counter)
        c.execute("BEGIN IMMEDIATE")                      # :now após o bloqueio (I7)
        try:
            # 0. repetição do MESMO commit: não grava nada, devolve o resultado já confirmado
            prev = c.execute("SELECT content_hash, quality FROM pages WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if prev is not None:
                c.execute("ROLLBACK")
                if prev[0] == content_hash:
                    return f"duplicate:{prev[1]}"
                return "error:attempt_id_reutilizado_com_conteudo_diferente"
            # 1. posse + execução corrente
            row = c.execute(
                """SELECT a.auth_state, r.ended_at, r.progress_state, r.next_pos, r.trusted_cursor,
                          j.current_run, j.progress_kind, j.target, j.list_type,
                          r.replay_target, r.replay_done, r.rounds_without_new, r.stuck_rounds,
                          r.last_screen_hash
                     FROM accounts a
                     JOIN runs r ON r.account=a.account AND r.lease_owner=a.lease_owner
                                AND r.lease_gen=a.lease_gen
                     JOIN jobs j ON j.job_id=r.job_id
                    WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?
                      AND r.run_id=? AND j.job_id=?""",
                (acct, owner, gen, now, run, job)).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return "rejected:posse"
            (auth, ended, pstate, next_pos, tcur, cur_run, kind, target, lt,
             rtarget, rdone, rwn, stuck, last_hash) = row
            if cur_run != run or ended is not None:
                c.execute("ROLLBACK")
                return "rejected:execucao_nao_corrente"
            if pstate in ("end_confirmed", "end_unknown"):
                c.execute("ROLLBACK")
                return "rejected:execucao_finalizada"
            # 2. posição lógica
            if pos != next_pos:
                c.execute("ROLLBACK")
                return f"rejected:posicao_obsoleta(esperada={next_pos})"
            if kind == "cursor" and tcur != cursor_in:
                c.execute("ROLLBACK")
                return "rejected:cursor_divergente"
            prior = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND pos=?", (run, pos)).fetchone()[0]
            if prior > MAX_REREADS_PER_POS:
                c.execute("ROLLBACK")
                return "rejected:limite_de_releituras"
            # 3. qualidade — política sanity-v1
            names = [u for _, u in members]
            exact_zero = counter is not None and counter[0] == "exact" and counter[1] == 0
            if auth != "ok":
                quality, reason = "suspect", "account_revoked"
            elif not page_ok:
                quality, reason = "suspect", "classifier_not_positive"
            elif len(set(names)) != len(names) or any(not USERNAME_RE.match(u) for u in names):
                quality, reason = "suspect", "malformed_items"
            elif not members and not (pos == 0 and empty_state and exact_zero):
                quality, reason = "suspect", "empty_without_end"
            else:
                quality, reason = "trusted", None
            # 4. página (observação imutável)
            try:
                c.execute(
                    """INSERT INTO pages(attempt_id, run_id, pos, cursor_in, cursor_out, content_hash,
                                         n_items, quality, reason, policy_version, received_at, committed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (attempt_id, run, pos, cursor_in, cursor_out, content_hash, len(members),
                     quality, reason, POLICY_VERSION, received_at or now, now))
            except sqlite3.IntegrityError as e:
                c.execute("ROLLBACK")
                return f"error:violacao_de_unicidade({e})"
            page_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            # 5. membros e observações
            new_for_job = 0
            for pk, user in members:
                mid = self._resolve_member(target, lt, pk, user, now)
                if quality == "trusted" and not self._trusted_in_job(mid, job):
                    new_for_job += 1
                c.execute("INSERT OR IGNORE INTO observations(page_id, member_id, username_seen) VALUES(?,?,?)",
                          (page_id, mid, user))
            if counter is not None:
                kind_c, lo = counter[0], counter[1]
                hi = counter[2] if len(counter) > 2 else lo
                c.execute("""UPDATE runs SET counter_kind=?, counter_lo=?, counter_hi=?, counter_read_at=?
                             WHERE run_id=?""", (kind_c, lo, hi, now, run))
            # 6. progresso — só página confiável avança
            if quality == "trusted":
                if kind == "cursor":
                    state = "end_confirmed" if cursor_out is None else "in_progress"
                    c.execute("""UPDATE runs SET next_pos=next_pos+1, trusted_cursor=?, progress_state=?,
                                 last_commit_at=? WHERE run_id=?""", (cursor_out, state, now, run))
                else:
                    same_screen = screen_hash is not None and screen_hash == last_hash
                    stuck = stuck + 1 if same_screen else 0
                    seen_run = c.execute(
                        """SELECT COUNT(DISTINCT o.member_id) FROM observations o
                           JOIN pages p ON p.page_id=o.page_id
                           WHERE p.run_id=? AND p.quality='trusted'""", (run,)).fetchone()[0]
                    prev_done = rdone
                    if not rdone and (new_for_job > 0 or
                                      (rtarget > 0 and seen_run >= math.ceil(REPLAY_RATIO * rtarget))):
                        rdone = 1
                    if new_for_job > 0:
                        rwn = 0
                    elif prev_done and not same_screen and not loading:
                        rwn += 1
                    if stuck >= S_STUCK:
                        state = "end_unknown"
                    elif rdone and rwn >= K_STAGNANT:
                        state = "end_confirmed"
                    else:
                        state = "in_progress"
                    c.execute(
                        """UPDATE runs SET next_pos=next_pos+1, progress_state=?, replay_done=?,
                                  rounds_without_new=?, stuck_rounds=?, last_screen_hash=?,
                                  last_commit_at=? WHERE run_id=?""",
                        (state, rdone, rwn, stuck, screen_hash, now, run))
            elif pstate == "not_started":
                c.execute("UPDATE runs SET progress_state='in_progress', last_commit_at=? WHERE run_id=?",
                          (now, run))
            c.execute("COMMIT")
            return f"committed:{quality}"
        except Exception:
            c.execute("ROLLBACK")
            raise

    # ---------------- leitura de estado e resultados ----------------
    def _counter_verification(self, run, collected):
        kind, lo, hi, read_at, last_commit = self.c.execute(
            """SELECT counter_kind, counter_lo, counter_hi, counter_read_at, last_commit_at
               FROM runs WHERE run_id=?""", (run,)).fetchone()
        if kind is None:
            return "counter_unavailable", True
        if last_commit is not None and read_at is not None and last_commit - read_at > STALE_COUNTER_S:
            return "counter_stale", True
        tol = max(2, math.ceil(0.01 * hi))
        ok = (lo - tol) <= collected <= (hi + tol)
        return ("counter_consistent" if ok else "counter_inconsistent"), ok

    def job_state(self, job, run=None):
        c = self.c
        pstate, next_pos, tcur = c.execute(
            "SELECT progress_state, next_pos, trusted_cursor FROM runs WHERE run_id=?", (run,)).fetchone()
        trusted_pages = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND quality='trusted'",
                                  (run,)).fetchone()[0]
        suspect_total = c.execute(
            """SELECT COUNT(*) FROM pages p JOIN runs r ON r.run_id=p.run_id
               WHERE r.job_id=? AND p.quality='suspect'""", (job,)).fetchone()[0]
        suspect_open = c.execute(
            """SELECT COUNT(*) FROM pages s WHERE s.run_id=? AND s.quality='suspect'
               AND NOT EXISTS (SELECT 1 FROM pages t WHERE t.run_id=s.run_id AND t.pos=s.pos
                               AND t.quality='trusted')""", (run,)).fetchone()[0]
        collected = len(c.execute(JOB_TRUSTED_MEMBERS, (job,)).fetchall())
        verification, counter_ok = self._counter_verification(run, collected)
        return {
            "progress_state": pstate, "cursor": tcur, "next_pos": next_pos,
            "trusted_pages": trusted_pages, "suspect_total": suspect_total,
            "suspect_open": suspect_open, "collected": collected, "verification": verification,
            "can_complete": pstate == "end_confirmed" and suspect_open == 0 and counter_ok,
        }

    def result_members(self, job, interpretation=None):
        return {r[0] for r in self.c.execute(
            f"SELECT username FROM members WHERE member_id IN ({JOB_TRUSTED_MEMBERS})", (job,))}

    def count_pages(self, job):
        return self.c.execute("""SELECT COUNT(*) FROM pages p JOIN runs r ON r.run_id=p.run_id
                                 WHERE r.job_id=?""", (job,)).fetchone()[0]

    def count_members(self):
        return self.c.execute("SELECT COUNT(*) FROM members").fetchone()[0]

    def merge_audit_count(self):
        return self.c.execute("SELECT COUNT(*) FROM member_merges").fetchone()[0]

    # ---------------- backup (I9 com ponto de consistência) ----------------
    def backup_verify(self, src_path, dst_path, writer):
        # Coordenador segura o bloqueio de escrita; o backup é feito por OUTRA conexão.
        # Backup pela mesma conexão com BEGIN IMMEDIATE aberto trava (diag_backup.py).
        c = self.c
        dst = sqlite3.connect(dst_path)
        reader = sqlite3.connect(src_path, isolation_level=None)
        c.execute("BEGIN IMMEDIATE")                       # exclui escritores durante a janela
        try:
            origin = c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
            wrote = writer()                               # deve falhar: bloqueio de escrita ocupado
            reader.backup(dst, pages=-1)                   # snapshot do mesmo estado contado
        finally:
            c.execute("COMMIT")
            reader.close()
        copied = dst.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        dst.close()
        return {"writer_committed_during_check": wrote, "origin": origin, "backup": copied,
                "integrity": integrity, "counts_match": origin == copied}
