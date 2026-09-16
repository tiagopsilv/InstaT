"""Modelo de referência do desenho PROPOSTO na §6.3 v13.7.

Não é implementação do InstaT. Valida SQL e regras antes da F5.
Parâmetros de POLICY são limites operacionais iniciais (sanity-v1), não documentados pelo Instagram.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import unicodedata

POLICY = {
    "version": "sanity-v1",
    "K": 3,                         # telas (scan) consecutivas sem membro novo, após releitura
    "S": 3,                         # telas consecutivas com screen_hash idêntico
    "replay_ratio": 0.95,           # fração dos membros confiáveis anteriores ao segmento
    "frontier_confirm_screens": 2,  # telas consecutivas com membro novo para encerrar a releitura
    "counter_tol_abs_min": 2,       # membros
    "counter_tol_rel": 0.01,        # fração do contador
    "counter_stale_s": 1800,        # segundos
    "max_rereads_per_pos": 2,       # releituras ADICIONAIS por posição
    "max_runs": 3,                  # execuções totais por job
}
AUTO_RESUME_REASONS = ("lease_lost", "technical_error")
USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
HISTORY_LABEL = "histórico observado: não é lista atual nem prova de completude"


class BackupMisuse(Exception):
    pass


class BackupTimeout(Exception):
    pass


def normalize_username(u):
    return unicodedata.normalize("NFC", u).strip().lower()


def canonical_page(run_id, pos, cursor_in, cursor_out, members, page_ok, empty_state, loading,
                   end_marker, screen_hash, counter):
    obj = {
        "schema": "page-v1", "run_id": run_id, "pos": pos,
        "cursor_in": cursor_in, "cursor_out": cursor_out,
        "items": [{"pk": None if pk is None else str(pk), "u": normalize_username(u)} for pk, u in members],
        "page_ok": bool(page_ok), "empty_state": bool(empty_state), "loading": bool(loading),
        "end_marker": bool(end_marker), "screen_hash": screen_hash,
        "counter": None if counter is None else list(counter),
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


SCHEMA = """
CREATE TABLE accounts(
  account TEXT PRIMARY KEY,
  auth_state TEXT NOT NULL CHECK(auth_state IN ('ok','needs_attention','restricted')),
  restricted_until REAL, restricted_at REAL, auth_validated_at REAL, released_by TEXT,
  lease_owner TEXT, lease_until REAL, lease_gen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE endpoint_cooldowns(
  account TEXT NOT NULL, endpoint TEXT NOT NULL, cooldown_until REAL NOT NULL, reason TEXT,
  PRIMARY KEY(account, endpoint));
CREATE TABLE jobs(
  job_id TEXT PRIMARY KEY, target TEXT NOT NULL, list_type TEXT NOT NULL, endpoint TEXT NOT NULL,
  progress_kind TEXT NOT NULL CHECK(progress_kind IN ('cursor','scan')),
  status TEXT NOT NULL CHECK(status IN ('pending','running','partial','complete','failed')),
  end_reason TEXT, current_run INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
  requeued_at REAL, requeued_by TEXT, cancel_requested_at REAL, updated_at REAL);
CREATE TABLE runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL REFERENCES jobs(job_id),
  account TEXT NOT NULL, lease_owner TEXT NOT NULL, lease_gen INTEGER NOT NULL,
  cursor_context TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL, stop_reason TEXT,
  progress_state TEXT NOT NULL DEFAULT 'not_started'
    CHECK(progress_state IN ('not_started','in_progress','end_confirmed','end_unknown')),
  end_evidence TEXT,
  next_pos INTEGER NOT NULL DEFAULT 0, trusted_cursor TEXT,
  segment INTEGER NOT NULL DEFAULT 0, segment_started_at REAL NOT NULL,
  replay_target INTEGER NOT NULL DEFAULT 0, replay_done INTEGER NOT NULL DEFAULT 0,
  frontier_streak INTEGER NOT NULL DEFAULT 0,
  rounds_without_new INTEGER NOT NULL DEFAULT 0, stuck_rounds INTEGER NOT NULL DEFAULT 0,
  continuity_gaps INTEGER NOT NULL DEFAULT 0,
  last_screen_hash TEXT, last_screen_members TEXT,
  counter_kind TEXT, counter_lo INTEGER, counter_hi INTEGER, counter_read_at REAL,
  last_commit_at REAL);
CREATE TABLE pages(
  page_id INTEGER PRIMARY KEY AUTOINCREMENT,
  attempt_id TEXT NOT NULL UNIQUE,
  run_id INTEGER NOT NULL REFERENCES runs(run_id),
  segment INTEGER NOT NULL, pos INTEGER NOT NULL, cursor_in TEXT, cursor_out TEXT,
  canonical_schema TEXT NOT NULL, content_hash TEXT NOT NULL, n_items INTEGER NOT NULL,
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

ELIGIBLE_SQL = """
SELECT 1 FROM accounts
 WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?
   AND auth_state='ok'
   AND (restricted_until IS NULL OR restricted_until<=?)
   AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at>=restricted_at))
   AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns
                    WHERE account=? AND endpoint=? AND cooldown_until>?)
"""

JOB_TRUSTED_MEMBERS = """
SELECT DISTINCT o.member_id FROM observations o
  JOIN pages p ON p.page_id=o.page_id JOIN runs r ON r.run_id=p.run_id
 WHERE r.job_id=? AND p.quality='trusted'
"""


class V137:
    name = "v13.7 (desenho proposto)"

    def __init__(self, path, ttl=30.0, create=True, busy_timeout=0.0):
        self.path, self.ttl, self.busy_timeout = path, ttl, busy_timeout
        self.c = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout)
        self.c.execute("PRAGMA foreign_keys=ON")
        if create:
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.executescript(SCHEMA)

    def _rb(self, result, run=None):
        self.c.execute("ROLLBACK")
        return (result, run)

    # ------------------------------------------------------------ contas
    def add_account(self, acct):
        self.c.execute("INSERT INTO accounts(account, auth_state) VALUES(?, 'ok')", (acct,))

    def set_auth(self, acct, state, now=None, restricted_until=None):
        if state == "ok":
            raise ValueError("liberação só por release_account (manual e com sessão validada)")
        self.c.execute("""UPDATE accounts SET auth_state=?, restricted_at=?, restricted_until=?
                          WHERE account=?""", (state, now if now is not None else time.time(),
                                               restricted_until, acct))

    def release_account(self, acct, by, session_validated, now):
        if not session_validated:
            return "rejected:sessao_nao_validada"
        cur = self.c.execute("""UPDATE accounts SET auth_state='ok', auth_validated_at=?, released_by=?
                                WHERE account=?""", (now, by, acct))
        return "ok" if cur.rowcount == 1 else "rejected:conta_inexistente"

    def expire(self, acct, now):
        self.c.execute("UPDATE accounts SET lease_until=? WHERE account=?", (now - 1, acct))

    def acquire(self, acct, owner, now, endpoint="followers"):
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute(
            """UPDATE accounts SET lease_owner=?, lease_until=?, lease_gen=lease_gen+1
               WHERE account=? AND auth_state='ok'
                 AND (restricted_until IS NULL OR restricted_until<=?)
                 AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at>=restricted_at))
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
        cur = c.execute("""UPDATE accounts SET lease_until=?
                           WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?
                             AND auth_state='ok'""", (now + self.ttl, acct, owner, gen, now))
        if cur.rowcount == 1:
            c.execute("COMMIT")
            return "renewed"
        held = c.execute("""SELECT 1 FROM accounts WHERE account=? AND lease_owner=? AND lease_gen=?
                            AND lease_until>?""", (acct, owner, gen, now)).fetchone()
        c.execute("COMMIT")
        return "account_restricted" if held else "lease_lost"

    # ------------------------------------------------------------ jobs e execuções
    def create_job(self, job, target="alvo", list_type="followers", kind="cursor"):
        self.c.execute("""INSERT INTO jobs(job_id, target, list_type, endpoint, progress_kind, status)
                          VALUES(?,?,?,?,?,'pending')""", (job, target, list_type, list_type, kind))

    def _known_trusted(self, job):
        return len(self.c.execute(JOB_TRUSTED_MEMBERS, (job,)).fetchall())

    def claim(self, job, tok, now):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        try:
            j = c.execute("SELECT status, current_run, attempts, endpoint, requeued_at FROM jobs WHERE job_id=?",
                          (job,)).fetchone()
            if j is None:
                return self._rb("rejected:job_inexistente")
            status, cur_run, attempts, endpoint, requeued_at = j
            if c.execute(ELIGIBLE_SQL, (acct, owner, gen, now, now, acct, endpoint, now)).fetchone() is None:
                return self._rb("rejected:solicitante_sem_concessao")
            if status == "failed":
                return self._rb("rejected:job_encerrado")
            if attempts >= POLICY["max_runs"]:
                return self._rb("rejected:limite_de_execucoes")
            if cur_run is not None:
                ended_at, stop_reason, live = c.execute(
                    """SELECT r.ended_at, r.stop_reason,
                              EXISTS(SELECT 1 FROM accounts a WHERE a.account=r.account
                                     AND a.lease_owner=r.lease_owner AND a.lease_gen=r.lease_gen
                                     AND a.lease_until>?)
                         FROM runs r WHERE r.run_id=?""", (now, cur_run)).fetchone()
                if ended_at is None and live:
                    return self._rb("rejected:job_com_execucao_vigente")
                if ended_at is None:
                    c.execute("UPDATE runs SET ended_at=?, stop_reason='lease_lost' WHERE run_id=?", (now, cur_run))
                    ended_at, stop_reason = now, "lease_lost"
                if stop_reason not in AUTO_RESUME_REASONS and not (requeued_at is not None and requeued_at > ended_at):
                    return self._rb("rejected:aguardando_requeue")
            known = self._known_trusted(job)
            c.execute("""INSERT INTO runs(job_id, account, lease_owner, lease_gen, cursor_context, started_at,
                                          segment_started_at, replay_target, replay_done)
                         VALUES(?,?,?,?,?,?,?,?,?)""",
                      (job, acct, owner, gen, f"{acct}|gen{gen}|model", now, now, known, 1 if known == 0 else 0))
            run_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            cur = c.execute("""UPDATE jobs SET current_run=?, status='running', attempts=attempts+1,
                                  cancel_requested_at=NULL, updated_at=?
                               WHERE job_id=? AND current_run IS ?""", (run_id, now, job, cur_run))
            if cur.rowcount != 1:
                return self._rb("rejected:concorrencia")
            c.execute("COMMIT")
            return ("ok", run_id)
        except Exception:
            c.execute("ROLLBACK")
            raise

    def request_cancel(self, job, now):
        self.c.execute("UPDATE jobs SET cancel_requested_at=?, updated_at=? WHERE job_id=?", (now, now, job))

    def requeue(self, job, by, now):
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("""SELECT j.status, j.attempts, r.ended_at FROM jobs j
                           LEFT JOIN runs r ON r.run_id=j.current_run WHERE j.job_id=?""", (job,)).fetchone()
        if row is None:
            return self._rb("rejected:job_inexistente")[0]
        status, attempts, ended_at = row
        if attempts >= POLICY["max_runs"]:
            return self._rb("rejected:limite_de_execucoes")[0]
        if status == "failed":
            return self._rb("rejected:job_encerrado")[0]
        if status == "running" and ended_at is None:
            return self._rb("rejected:execucao_ativa")[0]
        c.execute("""UPDATE jobs SET requeued_at=?, requeued_by=?, status='pending', updated_at=?
                     WHERE job_id=?""", (now, by, now, job))
        c.execute("COMMIT")
        return "ok"

    def _posse(self, tok, job, run, now):
        acct, owner, gen = tok
        return self.c.execute(
            """SELECT a.auth_state, r.ended_at, j.current_run, j.cancel_requested_at
                 FROM accounts a
                 JOIN runs r ON r.account=a.account AND r.lease_owner=a.lease_owner AND r.lease_gen=a.lease_gen
                 JOIN jobs j ON j.job_id=r.job_id
                WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?
                  AND r.run_id=? AND j.job_id=?""", (acct, owner, gen, now, run, job)).fetchone()

    def can_send(self, tok, job, run, now):
        row = self._posse(tok, job, run, now)
        if row is None:
            return False, "lease_lost"
        auth, ended, cur_run, cancel_at = row
        if cur_run != run or ended is not None:
            return False, "run_not_current"
        if auth != "ok":
            return False, "account_restricted"
        if cancel_at is not None:
            return False, "cancel_requested"
        return True, "ok"

    def stop_run(self, tok, job, run, reason, now):
        acct, owner, gen = tok
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        cur = c.execute("""UPDATE runs SET ended_at=?, stop_reason=?
                           WHERE run_id=? AND account=? AND lease_owner=? AND lease_gen=? AND ended_at IS NULL""",
                        (now, reason, run, acct, owner, gen))
        if cur.rowcount == 1:
            c.execute("""UPDATE accounts SET lease_until=? WHERE account=? AND lease_owner=? AND lease_gen=?
                         AND lease_until>?""", (now, acct, owner, gen, now))
            c.execute("UPDATE jobs SET status='partial', end_reason=?, updated_at=? WHERE job_id=?", (reason, now, job))
        c.execute("COMMIT")

    def restart_segment(self, tok, job, run, now):
        c = self.c
        c.execute("BEGIN IMMEDIATE")
        row = self._posse(tok, job, run, now)
        if row is None:
            return self._rb("rejected:posse")[0]
        if row[2] != run or row[1] is not None:
            return self._rb("rejected:execucao_nao_corrente")[0]
        known = self._known_trusted(job)
        c.execute("""UPDATE runs SET segment=segment+1, segment_started_at=?, replay_target=?, replay_done=?,
                            frontier_streak=0, rounds_without_new=0, stuck_rounds=0,
                            last_screen_hash=NULL, last_screen_members=NULL
                     WHERE run_id=?""", (now, known, 1 if known == 0 else 0, run))
        c.execute("COMMIT")
        return "ok"

    def resume_point(self, job, run):
        return self.c.execute("SELECT trusted_cursor, next_pos FROM runs WHERE run_id=?", (run,)).fetchone()

    # ------------------------------------------------------------ identidade de membros
    def _merge(self, loser, survivor, now, reason):
        c = self.c
        c.execute("""INSERT OR IGNORE INTO observations(page_id, member_id, username_seen)
                     SELECT page_id, ?, username_seen FROM observations WHERE member_id=?""", (survivor, loser))
        c.execute("DELETE FROM observations WHERE member_id=?", (loser,))
        c.execute("""UPDATE members SET
                       first_seen_at = MIN(first_seen_at, (SELECT first_seen_at FROM members WHERE member_id=?)),
                       last_seen_at  = MAX(last_seen_at,  (SELECT last_seen_at  FROM members WHERE member_id=?))
                     WHERE member_id=?""", (loser, loser, survivor))
        c.execute("DELETE FROM members WHERE member_id=?", (loser,))
        c.execute("INSERT INTO member_merges(loser_member_id, survivor_member_id, merged_at, reason) VALUES(?,?,?,?)",
                  (loser, survivor, now, reason))

    def _resolve_member(self, target, lt, pk, user, now):
        c = self.c
        loose = c.execute("""SELECT member_id FROM members WHERE target=? AND list_type=? AND username=?
                             AND member_pk IS NULL""", (target, lt, user)).fetchone()
        loose = loose[0] if loose else None
        if pk is not None:
            row = c.execute("SELECT member_id FROM members WHERE target=? AND list_type=? AND member_pk=?",
                            (target, lt, pk)).fetchone()
            if row is None:
                if loose is not None:
                    c.execute("UPDATE members SET member_pk=?, last_seen_at=MAX(last_seen_at,?) WHERE member_id=?",
                              (pk, now, loose))
                    return loose
                c.execute("""INSERT INTO members(target, list_type, member_pk, username, first_seen_at, last_seen_at)
                             VALUES(?,?,?,?,?,?)""", (target, lt, pk, user, now, now))
                return c.execute("SELECT last_insert_rowid()").fetchone()[0]
            mid = row[0]
            c.execute("UPDATE members SET username=?, last_seen_at=MAX(last_seen_at,?) WHERE member_id=?",
                      (user, now, mid))
            if loose is not None and loose != mid:
                self._merge(loose, mid, now, "pk_confirms_username")
            return mid
        with_pk = [r[0] for r in c.execute("""SELECT member_id FROM members WHERE target=? AND list_type=?
                                              AND username=? AND member_pk IS NOT NULL""", (target, lt, user))]
        if len(with_pk) == 1:
            c.execute("UPDATE members SET last_seen_at=MAX(last_seen_at,?) WHERE member_id=?", (now, with_pk[0]))
            return with_pk[0]
        state = "ambiguous" if len(with_pk) > 1 else "ok"
        if loose is not None:
            c.execute("UPDATE members SET last_seen_at=MAX(last_seen_at,?), identity_state=? WHERE member_id=?",
                      (now, state, loose))
            return loose
        c.execute("""INSERT INTO members(target, list_type, member_pk, username, first_seen_at, last_seen_at,
                                         identity_state) VALUES(?,?,?,?,?,?,?)""",
                  (target, lt, None, user, now, now, state))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]

    def _trusted_in_job(self, member_id, job):
        return self.c.execute(
            """SELECT 1 FROM observations o JOIN pages p ON p.page_id=o.page_id JOIN runs r ON r.run_id=p.run_id
               WHERE o.member_id=? AND r.job_id=? AND p.quality='trusted' LIMIT 1""", (member_id, job)).fetchone() is not None

    # ------------------------------------------------------------ commit de página
    def commit(self, tok, job, run, *, attempt_id, pos, cursor_in, cursor_out, members, page_ok=True, now,
               screen_hash=None, loading=False, empty_state=False, end_marker=False, counter=None,
               received_at=None, **_ignored):
        acct, owner, gen = tok
        c = self.c
        content_hash = hashlib.sha256(canonical_page(run, pos, cursor_in, cursor_out, members, page_ok, empty_state,
                                                     loading, end_marker, screen_hash, counter)).hexdigest()
        c.execute("BEGIN IMMEDIATE")
        try:
            prev = c.execute("SELECT run_id, content_hash, quality FROM pages WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if prev is not None:
                c.execute("ROLLBACK")
                if prev[0] != run:
                    return "error:attempt_id_de_outra_execucao"
                if prev[1] == content_hash:
                    return f"duplicate:{prev[2]}"
                return "error:attempt_id_reutilizado_com_conteudo_diferente"
            row = c.execute(
                """SELECT a.auth_state, r.ended_at, r.progress_state, r.next_pos, r.trusted_cursor, j.current_run,
                          j.progress_kind, j.target, j.list_type, r.segment, r.segment_started_at,
                          r.replay_target, r.replay_done, r.frontier_streak, r.rounds_without_new, r.stuck_rounds,
                          r.continuity_gaps, r.last_screen_hash, r.last_screen_members
                     FROM accounts a
                     JOIN runs r ON r.account=a.account AND r.lease_owner=a.lease_owner AND r.lease_gen=a.lease_gen
                     JOIN jobs j ON j.job_id=r.job_id
                    WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?
                      AND r.run_id=? AND j.job_id=?""", (acct, owner, gen, now, run, job)).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return "rejected:posse"
            (auth, ended, pstate, next_pos, tcur, cur_run, kind, target, lt, segment, seg_started,
             rtarget, rdone, frontier, rwn, stuck, gaps, last_hash, last_members) = row
            if cur_run != run or ended is not None:
                c.execute("ROLLBACK")
                return "rejected:execucao_nao_corrente"
            if pstate in ("end_confirmed", "end_unknown"):
                c.execute("ROLLBACK")
                return "rejected:execucao_finalizada"
            if pos != next_pos:
                c.execute("ROLLBACK")
                return f"rejected:posicao_obsoleta(esperada={next_pos})"
            if kind == "cursor" and tcur != cursor_in:
                c.execute("ROLLBACK")
                return "rejected:cursor_divergente"
            prior = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND pos=?", (run, pos)).fetchone()[0]
            if prior > POLICY["max_rereads_per_pos"]:
                c.execute("ROLLBACK")
                return "rejected:limite_de_releituras"
            names = [normalize_username(u) for _, u in members]
            exact_zero = counter is not None and counter[0] == "exact" and counter[1] == 0
            if auth != "ok":
                quality, reason = "suspect", "account_revoked"
            elif not page_ok:
                quality, reason = "suspect", "classifier_not_positive"
            elif len(set(names)) != len(names) or any(not USERNAME_RE.match(n) for n in names):
                quality, reason = "suspect", "malformed_items"
            elif not members and not (pos == 0 and empty_state and exact_zero):
                quality, reason = "suspect", "empty_without_end"
            else:
                quality, reason = "trusted", None
            c.execute("""INSERT INTO pages(attempt_id, run_id, segment, pos, cursor_in, cursor_out, canonical_schema,
                                           content_hash, n_items, quality, reason, policy_version, received_at,
                                           committed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (attempt_id, run, segment, pos, cursor_in, cursor_out, "page-v1", content_hash, len(members),
                       quality, reason, POLICY["version"], received_at or now, now))
            page_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            new_for_job, seen_ids = 0, set()
            for (pk, raw), name in zip(members, names):
                mid = self._resolve_member(target, lt, pk, name, now)
                if quality == "trusted" and mid not in seen_ids and not self._trusted_in_job(mid, job):
                    new_for_job += 1
                seen_ids.add(mid)
                c.execute("INSERT OR IGNORE INTO observations(page_id, member_id, username_seen) VALUES(?,?,?)",
                          (page_id, mid, raw))
            if counter is not None:
                hi = counter[2] if len(counter) > 2 else counter[1]
                c.execute("""UPDATE runs SET counter_kind=?, counter_lo=?, counter_hi=?, counter_read_at=?
                             WHERE run_id=?""", (counter[0], counter[1], hi, now, run))
            if quality == "trusted":
                state, evidence = self._progress(kind, run, job, cursor_out, end_marker, screen_hash, loading, names,
                                                 new_for_job, segment, seg_started, rtarget, rdone, frontier, rwn,
                                                 stuck, gaps, last_hash, last_members, now)
                if state in ("end_confirmed", "end_unknown"):
                    status = self._run_status(run, assume_ended=True)["status"]
                    c.execute("UPDATE runs SET ended_at=?, stop_reason=? WHERE run_id=?",
                              (now, "end_confirmed" if state == "end_confirmed" else evidence, run))
                    c.execute("UPDATE jobs SET status=?, end_reason=?, updated_at=? WHERE job_id=?",
                              ("complete" if status == "complete" else "partial", evidence, now, job))
            elif pstate == "not_started":
                c.execute("UPDATE runs SET progress_state='in_progress', last_commit_at=? WHERE run_id=?", (now, run))
            c.execute("COMMIT")
            return f"committed:{quality}"
        except Exception:
            c.execute("ROLLBACK")
            raise

    def _progress(self, kind, run, job, cursor_out, end_marker, screen_hash, loading, names, new_for_job,
                  segment, seg_started, rtarget, rdone, frontier, rwn, stuck, gaps, last_hash, last_members, now):
        c = self.c
        if kind == "cursor":
            if end_marker:
                state, evidence = "end_confirmed", "end_marker"
            elif cursor_out is None:
                state, evidence = "end_unknown", "cursor_absent_no_end_signal"
            else:
                state, evidence = "in_progress", None
            c.execute("""UPDATE runs SET next_pos=next_pos+1, trusted_cursor=?, progress_state=?, end_evidence=?,
                                last_commit_at=? WHERE run_id=?""", (cursor_out, state, evidence, now, run))
            return state, evidence
        cur_set = set(names)
        prev = set(json.loads(last_members)) if last_members else None
        same_screen = screen_hash is not None and screen_hash == last_hash
        if prev is not None and not same_screen and cur_set and not (cur_set & prev):
            gaps += 1
        stuck = stuck + 1 if same_screen else 0
        frontier = frontier + 1 if new_for_job > 0 else 0
        prev_done = rdone
        if not rdone:
            if rtarget == 0:
                rdone = 1
            else:
                coverage = c.execute(
                    """SELECT COUNT(DISTINCT o.member_id) FROM observations o JOIN pages p ON p.page_id=o.page_id
                        WHERE p.run_id=? AND p.segment=? AND p.quality='trusted'
                          AND EXISTS (SELECT 1 FROM observations o2 JOIN pages p2 ON p2.page_id=o2.page_id
                                        JOIN runs r2 ON r2.run_id=p2.run_id
                                       WHERE o2.member_id=o.member_id AND r2.job_id=? AND p2.quality='trusted'
                                         AND p2.committed_at < ?)""", (run, segment, job, seg_started)).fetchone()[0]
                if coverage >= math.ceil(POLICY["replay_ratio"] * rtarget) or \
                        frontier >= POLICY["frontier_confirm_screens"]:
                    rdone = 1
        if new_for_job > 0:
            rwn = 0
        elif prev_done and not same_screen and not loading:
            rwn += 1
        collected = self._run_collected(run)
        verification, counter_ok = self._counter(run, collected)
        counter_exact_match = verification == "counter_consistent" and self._counter_kind(run) == "exact"
        if end_marker:
            state, evidence = "end_confirmed", "end_marker"
        elif stuck >= POLICY["S"]:
            state, evidence = "end_unknown", "screen_stuck"
        elif rdone and rwn >= POLICY["K"] and counter_exact_match:
            state, evidence = "end_confirmed", "k_rounds_and_exact_counter"
        elif rdone and rwn >= POLICY["K"]:
            state, evidence = "end_unknown", "no_end_evidence"
        else:
            state, evidence = "in_progress", None
        c.execute("""UPDATE runs SET next_pos=next_pos+1, progress_state=?, end_evidence=?, replay_done=?,
                            frontier_streak=?, rounds_without_new=?, stuck_rounds=?, continuity_gaps=?,
                            last_screen_hash=?, last_screen_members=?, last_commit_at=?
                     WHERE run_id=?""",
                  (state, evidence, rdone, frontier, rwn, stuck, gaps, screen_hash,
                   json.dumps(sorted(cur_set)), now, run))
        return state, evidence

    # ------------------------------------------------------------ resultados
    def _run_collected(self, run):
        return self.c.execute("""SELECT COUNT(DISTINCT o.member_id) FROM observations o
                                 JOIN pages p ON p.page_id=o.page_id WHERE p.run_id=? AND p.quality='trusted'""",
                              (run,)).fetchone()[0]

    def _counter_kind(self, run):
        return self.c.execute("SELECT counter_kind FROM runs WHERE run_id=?", (run,)).fetchone()[0]

    def _counter(self, run, collected):
        kind, lo, hi, read_at, last_commit = self.c.execute(
            "SELECT counter_kind, counter_lo, counter_hi, counter_read_at, last_commit_at FROM runs WHERE run_id=?",
            (run,)).fetchone()
        if kind is None:
            return "counter_unavailable", True
        if last_commit is not None and read_at is not None and last_commit - read_at > POLICY["counter_stale_s"]:
            return "counter_stale", True
        tol = max(POLICY["counter_tol_abs_min"], math.ceil(POLICY["counter_tol_rel"] * hi))
        ok = (lo - tol) <= collected <= (hi + tol)
        return ("counter_consistent" if ok else "counter_inconsistent"), ok

    def _run_status(self, run, assume_ended=False):
        c = self.c
        (job, pstate, evidence, ended_at, stop_reason, gaps, next_pos, tcur, started, last_commit, replay_done,
         segment) = c.execute(
            """SELECT job_id, progress_state, end_evidence, ended_at, stop_reason, continuity_gaps, next_pos,
                      trusted_cursor, started_at, last_commit_at, replay_done, segment
               FROM runs WHERE run_id=?""", (run,)).fetchone()
        suspect_open = c.execute(
            """SELECT COUNT(*) FROM pages s WHERE s.run_id=? AND s.quality='suspect'
               AND NOT EXISTS (SELECT 1 FROM pages t WHERE t.run_id=s.run_id AND t.pos=s.pos AND t.quality='trusted')""",
            (run,)).fetchone()[0]
        collected = self._run_collected(run)
        verification, counter_ok = self._counter(run, collected)
        members = sorted(r[0] for r in c.execute(
            """SELECT DISTINCT m.username FROM observations o JOIN pages p ON p.page_id=o.page_id
               JOIN members m ON m.member_id=o.member_id WHERE p.run_id=? AND p.quality='trusted'""", (run,)))
        reasons = []
        if pstate != "end_confirmed":
            reasons.append(evidence or pstate)
        if suspect_open:
            reasons.append("suspect_open")
        if gaps:
            reasons.append("continuity_gap")
        if not counter_ok:
            reasons.append("count_mismatch")
        if ended_at is None and not assume_ended:
            status = "running"
        else:
            status = "complete" if not reasons else "partial"
        return {"run_id": run, "job_id": job, "status": status, "reasons": reasons, "progress_state": pstate,
                "end_evidence": evidence, "next_pos": next_pos, "cursor": tcur, "members": members,
                "collected": collected, "suspect_open": suspect_open, "continuity_gaps": gaps,
                "verification": verification, "started_at": started, "ended_at": ended_at,
                "stop_reason": stop_reason, "last_commit_at": last_commit, "replay_done": replay_done,
                "segment": segment}

    def run_result(self, run):
        return self._run_status(run)

    def job_view(self, job):
        c = self.c
        runs = [r[0] for r in c.execute("SELECT run_id FROM runs WHERE job_id=? ORDER BY run_id DESC", (job,))]
        results = [self._run_status(r) for r in runs]
        if not results:
            return {"job_id": job, "selected_run_id": None, "selected_members": [], "history_observed": {},
                    "history_label": HISTORY_LABEL}
        selected = next((r for r in results if r["status"] == "complete"), results[0])
        last = results[0]

        def when(r):
            return r["ended_at"] if r["ended_at"] is not None else (r["last_commit_at"] or r["started_at"])

        history = {u: {"first_trusted_at": f, "last_trusted_at": l} for u, f, l in c.execute(
            """SELECT m.username, MIN(p.committed_at), MAX(p.committed_at) FROM observations o
               JOIN pages p ON p.page_id=o.page_id JOIN runs r ON r.run_id=p.run_id
               JOIN members m ON m.member_id=o.member_id
               WHERE r.job_id=? AND p.quality='trusted' GROUP BY m.member_id""", (job,))}
        suspect_total = c.execute("""SELECT COUNT(*) FROM pages p JOIN runs r ON r.run_id=p.run_id
                                     WHERE r.job_id=? AND p.quality='suspect'""", (job,)).fetchone()[0]
        return {
            "job_id": job,
            "selected_run_id": selected["run_id"],
            "selected_run_status": "complete" if selected["status"] == "complete" else "partial",
            "selected_run_reasons": selected["reasons"],
            "selected_run_at": when(selected),
            "selected_members": selected["members"],
            "selected_suspect_open": selected["suspect_open"],
            "last_attempt_run_id": last["run_id"],
            "last_attempt_status": last["status"],
            "last_attempt_state": last["progress_state"],
            "last_attempt_stop_reason": last["stop_reason"],
            "last_attempt_at": when(last),
            "history_observed": history,
            "history_label": HISTORY_LABEL,
            "suspect_total": suspect_total,
        }

    def result_members(self, job, interpretation=None):
        return set(self.job_view(job)["selected_members"])

    # ------------------------------------------------------------ backup
    def backup_from_connection(self, src_conn, dst, pages=-1, progress=None):
        if src_conn.in_transaction:
            raise BackupMisuse("backup a partir de conexão com transação aberta é proibido (travamento reproduzido)")
        own = isinstance(dst, str)
        dst_conn = sqlite3.connect(dst) if own else dst
        try:
            src_conn.backup(dst_conn, pages=pages, progress=progress)
        finally:
            if own:
                dst_conn.close()

    def verify_backup(self, path):
        v = sqlite3.connect(path)
        try:
            integrity = v.execute("PRAGMA integrity_check").fetchone()[0]
            fk = len(v.execute("PRAGMA foreign_key_check").fetchall())
            dup = len(v.execute("""SELECT run_id, pos FROM pages WHERE quality='trusted'
                                   GROUP BY run_id, pos HAVING COUNT(*) > 1""").fetchall())
            mismatch = v.execute("""SELECT COUNT(*) FROM runs r WHERE r.next_pos <>
                                    (SELECT COUNT(*) FROM pages p WHERE p.run_id=r.run_id AND p.quality='trusted')""").fetchone()[0]
            marker = dict(zip(("max_run_id", "max_page_id", "max_committed_at", "pages"),
                              v.execute("""SELECT (SELECT MAX(run_id) FROM runs), MAX(page_id), MAX(committed_at),
                                                  COUNT(*) FROM pages""").fetchone()))
        finally:
            v.close()
        ok = integrity == "ok" and fk == 0 and dup == 0 and mismatch == 0
        return {"ok": ok, "integrity": integrity, "fk_violations": fk, "dup_trusted": dup,
                "next_pos_mismatch": mismatch, "marker": marker, "compared_with_live_origin": False}

    def backup_online(self, dst_path, pages=-1, deadline_s=60.0, step_sleep_s=0.0):
        partial = dst_path + ".partial"
        for p in (partial,):
            if os.path.exists(p):
                os.remove(p)
        t0, steps = time.monotonic(), [0]

        def progress(status, remaining, total):
            steps[0] += 1
            if step_sleep_s:
                time.sleep(step_sleep_s)
            if time.monotonic() - t0 > deadline_s:
                raise BackupTimeout(f"prazo de {deadline_s}s excedido com {remaining}/{total} páginas restantes")

        src = sqlite3.connect(self.path, isolation_level=None, timeout=self.busy_timeout or 5.0)
        status, error = "ok", None
        try:
            self.backup_from_connection(src, partial, pages=pages, progress=progress)
        except BackupTimeout as e:
            status, error = "timeout", str(e)
        finally:
            src.close()
        elapsed = time.monotonic() - t0
        if status != "ok":
            if os.path.exists(partial):
                os.remove(partial)
            return {"status": status, "error": error, "elapsed": elapsed, "steps": steps[0],
                    "verification": {"ok": False, "compared_with_live_origin": False}}
        ver = self.verify_backup(partial)
        if ver["ok"]:
            os.replace(partial, dst_path)
        else:
            os.remove(partial)
            status = "invalid"
        return {"status": status, "elapsed": elapsed, "steps": steps[0], "verification": ver}

    @staticmethod
    def _digest(path):
        conn = sqlite3.connect(path)
        try:
            h = hashlib.sha256()
            for (table,) in conn.execute("""SELECT name FROM sqlite_master WHERE type='table'
                                            AND name NOT LIKE 'sqlite_%' ORDER BY name"""):
                h.update(table.encode())
                for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'):
                    h.update(repr(row).encode())
            return h.hexdigest()
        finally:
            conn.close()

    def backup_maintenance(self, dst_path, now, deadline_s=60.0):
        live_leases = self.c.execute("SELECT COUNT(*) FROM accounts WHERE lease_until > ?", (now,)).fetchone()[0]
        open_runs = self.c.execute(
            """SELECT COUNT(*) FROM runs r JOIN accounts a ON a.account=r.account AND a.lease_owner=r.lease_owner
               AND a.lease_gen=r.lease_gen WHERE r.ended_at IS NULL AND a.lease_until > ?""", (now,)).fetchone()[0]
        if live_leases or open_runs:
            return {"status": "rejected:nao_drenado", "live_leases": live_leases, "open_runs": open_runs}
        info = self.backup_online(dst_path, pages=-1, deadline_s=deadline_s)
        if info["status"] != "ok":
            return info
        info["digest_equal"] = self._digest(self.path) == self._digest(dst_path)
        info["compared_with_origin_in_maintenance_window"] = True
        return info
