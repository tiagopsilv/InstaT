"""JobStore — persistência de jobs, execuções e páginas (roadmap §6.3, Fase 5).

Critério de correção: invariantes I1–I12 da §6.3.1. Partiu do modelo v13.7
(docs/design-validation/v13.7), revisado contra a especificação:
  - `now=None` → horário lido DEPOIS de obter o BEGIN IMMEDIATE (I7);
  - toda transação faz ROLLBACK em exceção; violação de unicidade nunca vira sucesso;
  - validação de ttl e busy_timeout (§6.3.12);
  - uma conexão por instância; heartbeat e backup usam conexões próprias.

Resultados retornam strings estáveis (`ok`, `committed:<q>`, `duplicate:<q>`,
`rejected:<motivo>`, `error:<motivo>`), como na especificação.
"""
import hashlib
import json
import math
import os
import sqlite3
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from instat.jobstore.canonical import USERNAME_RE, canonical_page, normalize_username
    from instat.jobstore.policy import AUTO_RESUME_REASONS, HISTORY_LABEL, SANITY_V1
    from instat.jobstore.schema import SCHEMA, SCHEMA_VERSION
except ImportError:  # pragma: no cover
    from jobstore.canonical import USERNAME_RE, canonical_page, normalize_username  # type: ignore
    from jobstore.policy import AUTO_RESUME_REASONS, HISTORY_LABEL, SANITY_V1  # type: ignore
    from jobstore.schema import SCHEMA, SCHEMA_VERSION  # type: ignore

POLICY = SANITY_V1
Token = Tuple[str, str, int]

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


class BackupMisuse(Exception):
    """Backup a partir de conexão com transação aberta (travamento reproduzido em [E2])."""


class BackupTimeout(Exception):
    """Prazo do backup excedido."""


class _Rollback(Exception):
    def __init__(self, result: Any) -> None:
        super().__init__(result)
        self.result = result


def validate_timing(ttl: float, busy_timeout: float) -> None:
    if not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"lease ttl must be finite and positive (seconds), got {ttl!r}")
    if not isinstance(busy_timeout, (int, float)) or not math.isfinite(busy_timeout) or busy_timeout < 0:
        raise ValueError(f"busy_timeout must be finite and >= 0, got {busy_timeout!r}")
    if busy_timeout > ttl / 6 + 1e-9:
        raise ValueError(f"busy_timeout ({busy_timeout}) must be <= ttl/6 ({ttl / 6:.3f})")


class JobStore:
    name = "instat.jobstore (F5)"

    def __init__(self, path: str, ttl: float = 30.0, create: bool = True, busy_timeout: float = 0.0,
                 clock: Callable[[], float] = time.time) -> None:
        validate_timing(ttl, busy_timeout)
        self.path, self.ttl, self.busy_timeout, self.clock = path, float(ttl), float(busy_timeout), clock
        self.c = self._open_with_retry(path, busy_timeout)
        self._fault_hook: Optional[Callable[[], None]] = None
        # Intervalo exato da última concessão/liberação desta instância (horários
        # das próprias transações): evidência para auditoria de exclusividade (F4).
        self.last_lease: Optional[Dict[str, Any]] = None
        self.last_release: Optional[Dict[str, Any]] = None
        if create:
            self.c.execute("PRAGMA journal_mode=WAL")
            self.c.executescript(SCHEMA)
            self.c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    OPEN_RETRIES = 6          # 0,05 · (2^6 − 1) ≈ 3,2 s no pior caso
    OPEN_RETRY_BASE_S = 0.05

    @classmethod
    def _open_with_retry(cls, path: str, busy_timeout: float) -> sqlite3.Connection:
        """Abre a conexão repetindo 'disk I/O error' transitório com limite.

        Medido na F5 (Windows): logo após matar processos que usavam o banco em WAL,
        a primeira abertura falhou com 'disk I/O error' e abriu normalmente 0,2 s
        depois, com integrity_check = ok. Outros erros não são repetidos.
        """
        last: Optional[sqlite3.OperationalError] = None
        for attempt in range(cls.OPEN_RETRIES + 1):
            conn = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                # sonda que LÊ o arquivo (cabeçalho e índice WAL); PRAGMA foreign_keys não lê
                conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                return conn
            except sqlite3.OperationalError as e:
                conn.close()
                if "disk i/o error" not in str(e).lower():
                    raise
                last = e
                if attempt < cls.OPEN_RETRIES:
                    time.sleep(cls.OPEN_RETRY_BASE_S * (2 ** attempt))
        assert last is not None
        raise last

    def close(self) -> None:
        self.c.close()

    # ------------------------------------------------------------ transações
    def _begin(self, now: Optional[float]) -> float:
        """BEGIN IMMEDIATE e só então o horário (I7)."""
        self.c.execute("BEGIN IMMEDIATE")
        return self.clock() if now is None else now

    def _tx(self, now: Optional[float], body: Callable[[float], Any]) -> Any:
        t = self._begin(now)
        try:
            result = body(t)
        except _Rollback as rb:
            self.c.execute("ROLLBACK")
            return rb.result
        except BaseException:
            if self.c.in_transaction:
                self.c.execute("ROLLBACK")
            raise
        self.c.execute("COMMIT")
        return result

    # ------------------------------------------------------------ contas (§6.3.2, §6.3.5)
    def add_account(self, acct: str) -> None:
        self.c.execute("INSERT INTO accounts(account, auth_state) VALUES(?, 'ok')", (acct,))

    def set_auth(self, acct: str, state: str, now: Optional[float] = None,
                 restricted_until: Optional[float] = None) -> None:
        if state == "ok":
            raise ValueError("liberação só por release_account (manual e com sessão validada)")

        def body(t: float) -> None:
            self.c.execute("UPDATE accounts SET auth_state=?, restricted_at=?, restricted_until=? WHERE account=?",
                           (state, t, restricted_until, acct))
        self._tx(now, body)

    def release_account(self, acct: str, by: str, session_validated: bool, now: Optional[float] = None) -> str:
        if session_validated is not True:
            return "rejected:sessao_nao_validada"

        def body(t: float) -> str:
            cur = self.c.execute("UPDATE accounts SET auth_state='ok', auth_validated_at=?, released_by=? "
                                 "WHERE account=?", (t, by, acct))
            if cur.rowcount != 1:
                raise _Rollback("rejected:conta_inexistente")
            return "ok"
        return self._tx(now, body)

    def expire(self, acct: str, now: float) -> None:
        """Utilitário de teste/operação: encerra a concessão da conta."""
        self.c.execute("UPDATE accounts SET lease_until=? WHERE account=?", (now - 1, acct))

    def acquire(self, acct: str, owner: str, now: Optional[float] = None,
                endpoint: str = "followers") -> Optional[Token]:
        def body(t: float) -> Optional[Token]:
            cur = self.c.execute(
                """UPDATE accounts SET lease_owner=?, lease_until=?, lease_gen=lease_gen+1
                   WHERE account=? AND auth_state='ok'
                     AND (restricted_until IS NULL OR restricted_until<=?)
                     AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at>=restricted_at))
                     AND (lease_until IS NULL OR lease_until<=?)
                     AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns
                                      WHERE account=? AND endpoint=? AND cooldown_until>?)""",
                (owner, t + self.ttl, acct, t, t, acct, endpoint, t))
            if cur.rowcount != 1:
                raise _Rollback(None)
            gen = self.c.execute("SELECT lease_gen FROM accounts WHERE account=?", (acct,)).fetchone()[0]
            self.last_lease = {"account": acct, "owner": owner, "gen": gen, "start": t, "until": t + self.ttl}
            return (acct, owner, gen)
        return self._tx(now, body)

    def release_lease(self, tok: Token, now: Optional[float] = None) -> bool:
        """Encerra a concessão só se conta, dono e geração conferem e ela está vigente.

        Liberação tardia de um dono antigo (geração anterior) não tem efeito.
        """
        acct, owner, gen = tok

        def body(t: float) -> bool:
            cur = self.c.execute("""UPDATE accounts SET lease_until=?
                                    WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?""",
                                 (t, acct, owner, gen, t))
            released = cur.rowcount == 1
            self.last_release = {"account": acct, "owner": owner, "gen": gen, "at": t, "released": released}
            return released
        return self._tx(now, body)

    def lease_valid(self, tok: Token, now: Optional[float] = None) -> bool:
        acct, owner, gen = tok
        t = self.clock() if now is None else now
        return self.c.execute("""SELECT 1 FROM accounts WHERE account=? AND lease_owner=? AND lease_gen=?
                                 AND lease_until>?""", (acct, owner, gen, t)).fetchone() is not None

    def set_endpoint_cooldown(self, acct: str, endpoint: str, until: float, reason: str,
                              now: Optional[float] = None) -> None:
        """Cooldown por (conta, endpoint); nunca encurta um cooldown maior já gravado."""
        def body(t: float) -> None:
            self.c.execute("""INSERT INTO endpoint_cooldowns(account, endpoint, cooldown_until, reason)
                              VALUES(?,?,?,?)
                              ON CONFLICT(account, endpoint) DO UPDATE SET
                                cooldown_until = MAX(cooldown_until, excluded.cooldown_until),
                                reason = excluded.reason""", (acct, endpoint, until, reason))
        self._tx(now, body)

    def eligible_accounts(self, endpoint: str, now: Optional[float] = None) -> List[str]:
        """Contas que `acquire` concederia agora (mesmo predicado, só leitura)."""
        t = self.clock() if now is None else now
        return [r[0] for r in self.c.execute(
            """SELECT account FROM accounts a
               WHERE auth_state='ok'
                 AND (restricted_until IS NULL OR restricted_until<=?)
                 AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at>=restricted_at))
                 AND (lease_until IS NULL OR lease_until<=?)
                 AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns e
                                  WHERE e.account=a.account AND e.endpoint=? AND e.cooldown_until>?)
               ORDER BY account""", (t, t, endpoint, t))]

    def heartbeat(self, tok: Token, now: Optional[float] = None) -> str:
        acct, owner, gen = tok

        def body(t: float) -> str:
            cur = self.c.execute("""UPDATE accounts SET lease_until=?
                                    WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?
                                      AND auth_state='ok'""", (t + self.ttl, acct, owner, gen, t))
            if cur.rowcount == 1:
                return "renewed"
            held = self.c.execute("""SELECT 1 FROM accounts WHERE account=? AND lease_owner=? AND lease_gen=?
                                     AND lease_until>?""", (acct, owner, gen, t)).fetchone()
            return "account_restricted" if held else "lease_lost"
        return self._tx(now, body)

    # ------------------------------------------------------------ jobs e execuções (§6.3.6)
    def create_job(self, job: str, target: str = "alvo", list_type: str = "followers", kind: str = "cursor") -> None:
        self.c.execute("""INSERT INTO jobs(job_id, target, list_type, endpoint, progress_kind, status)
                          VALUES(?,?,?,?,?,'pending')""", (job, target, list_type, list_type, kind))

    def _known_trusted(self, job: str) -> int:
        return len(self.c.execute(JOB_TRUSTED_MEMBERS, (job,)).fetchall())

    def claim(self, job: str, tok: Token, now: Optional[float] = None) -> Tuple[str, Optional[int]]:
        acct, owner, gen = tok
        c = self.c

        def body(t: float) -> Tuple[str, Optional[int]]:
            j = c.execute("SELECT status, current_run, attempts, endpoint, requeued_at FROM jobs WHERE job_id=?",
                          (job,)).fetchone()
            if j is None:
                raise _Rollback(("rejected:job_inexistente", None))
            status, cur_run, attempts, endpoint, requeued_at = j
            if c.execute(ELIGIBLE_SQL, (acct, owner, gen, t, t, acct, endpoint, t)).fetchone() is None:
                raise _Rollback(("rejected:solicitante_sem_concessao", None))
            if status == "failed":
                raise _Rollback(("rejected:job_encerrado", None))
            if attempts >= POLICY["max_runs"]:
                raise _Rollback(("rejected:limite_de_execucoes", None))
            if cur_run is not None:
                ended_at, stop_reason, live = c.execute(
                    """SELECT r.ended_at, r.stop_reason,
                              EXISTS(SELECT 1 FROM accounts a WHERE a.account=r.account
                                     AND a.lease_owner=r.lease_owner AND a.lease_gen=r.lease_gen
                                     AND a.lease_until>?)
                         FROM runs r WHERE r.run_id=?""", (t, cur_run)).fetchone()
                if ended_at is None and live:
                    raise _Rollback(("rejected:job_com_execucao_vigente", None))
                if ended_at is None:
                    c.execute("UPDATE runs SET ended_at=?, stop_reason='lease_lost' WHERE run_id=?", (t, cur_run))
                    ended_at, stop_reason = t, "lease_lost"
                if stop_reason not in AUTO_RESUME_REASONS and not (requeued_at is not None and requeued_at > ended_at):
                    raise _Rollback(("rejected:aguardando_requeue", None))
            known = self._known_trusted(job)
            c.execute("""INSERT INTO runs(job_id, account, lease_owner, lease_gen, cursor_context, started_at,
                                          segment_started_at, replay_target, replay_done)
                         VALUES(?,?,?,?,?,?,?,?,?)""",
                      (job, acct, owner, gen, f"{acct}|gen{gen}", t, t, known, 1 if known == 0 else 0))
            run_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            cur = c.execute("""UPDATE jobs SET current_run=?, status='running', attempts=attempts+1,
                                  cancel_requested_at=NULL, updated_at=?
                               WHERE job_id=? AND current_run IS ?""", (run_id, t, job, cur_run))
            if cur.rowcount != 1:
                raise _Rollback(("rejected:concorrencia", None))
            return ("ok", run_id)
        return self._tx(now, body)

    def request_cancel(self, job: str, now: Optional[float] = None) -> None:
        self._tx(now, lambda t: self.c.execute(
            "UPDATE jobs SET cancel_requested_at=?, updated_at=? WHERE job_id=?", (t, t, job)))

    def requeue(self, job: str, by: str, now: Optional[float] = None) -> str:
        c = self.c

        def body(t: float) -> str:
            row = c.execute("""SELECT j.status, j.attempts, r.ended_at FROM jobs j
                               LEFT JOIN runs r ON r.run_id=j.current_run WHERE j.job_id=?""", (job,)).fetchone()
            if row is None:
                raise _Rollback("rejected:job_inexistente")
            status, attempts, ended_at = row
            if attempts >= POLICY["max_runs"]:
                raise _Rollback("rejected:limite_de_execucoes")
            if status == "failed":
                raise _Rollback("rejected:job_encerrado")
            if status == "running" and ended_at is None:
                raise _Rollback("rejected:execucao_ativa")
            c.execute("UPDATE jobs SET requeued_at=?, requeued_by=?, status='pending', updated_at=? WHERE job_id=?",
                      (t, by, t, job))
            return "ok"
        return self._tx(now, body)

    def _posse(self, tok: Token, job: str, run: int, now: float) -> Optional[tuple]:
        acct, owner, gen = tok
        return self.c.execute(
            """SELECT a.auth_state, r.ended_at, j.current_run, j.cancel_requested_at
                 FROM accounts a
                 JOIN runs r ON r.account=a.account AND r.lease_owner=a.lease_owner AND r.lease_gen=a.lease_gen
                 JOIN jobs j ON j.job_id=r.job_id
                WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?
                  AND r.run_id=? AND j.job_id=?""", (acct, owner, gen, now, run, job)).fetchone()

    def can_send(self, tok: Token, job: str, run: int, now: Optional[float] = None) -> Tuple[bool, str]:
        """Verificação antes de cada envio (§6.3.7). Não substitui a revalidação no commit."""
        t = self.clock() if now is None else now
        row = self._posse(tok, job, run, t)
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

    def stop_run(self, tok: Token, job: str, run: int, reason: str, now: Optional[float] = None) -> None:
        acct, owner, gen = tok

        def body(t: float) -> None:
            cur = self.c.execute("""UPDATE runs SET ended_at=?, stop_reason=?
                                    WHERE run_id=? AND account=? AND lease_owner=? AND lease_gen=?
                                      AND ended_at IS NULL""", (t, reason, run, acct, owner, gen))
            if cur.rowcount == 1:
                ended = self.c.execute("""UPDATE accounts SET lease_until=? WHERE account=? AND lease_owner=?
                                          AND lease_gen=? AND lease_until>?""", (t, acct, owner, gen, t))
                self.last_release = {"account": acct, "owner": owner, "gen": gen, "at": t,
                                     "released": ended.rowcount == 1, "via": "stop_run"}
                self.c.execute("UPDATE jobs SET status='partial', end_reason=?, updated_at=? WHERE job_id=?",
                               (reason, t, job))
        self._tx(now, body)

    def restart_segment(self, tok: Token, job: str, run: int, now: Optional[float] = None) -> str:
        def body(t: float) -> str:
            row = self._posse(tok, job, run, t)
            if row is None:
                raise _Rollback("rejected:posse")
            if row[2] != run or row[1] is not None:
                raise _Rollback("rejected:execucao_nao_corrente")
            known = self._known_trusted(job)
            self.c.execute("""UPDATE runs SET segment=segment+1, segment_started_at=?, replay_target=?, replay_done=?,
                                     frontier_streak=0, rounds_without_new=0, stuck_rounds=0,
                                     last_screen_hash=NULL, last_screen_members=NULL
                              WHERE run_id=?""", (t, known, 1 if known == 0 else 0, run))
            return "ok"
        return self._tx(now, body)

    def resume_point(self, job: str, run: int) -> Optional[tuple]:
        return self.c.execute("SELECT trusted_cursor, next_pos FROM runs WHERE run_id=?", (run,)).fetchone()

    # ------------------------------------------------------------ identidade de membros (§6.3.11)
    def _merge(self, loser: int, survivor: int, now: float, reason: str) -> None:
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

    def _resolve_member(self, target: str, lt: str, pk: Optional[str], user: str, now: float) -> int:
        c = self.c
        loose_row = c.execute("""SELECT member_id FROM members WHERE target=? AND list_type=? AND username=?
                                 AND member_pk IS NULL""", (target, lt, user)).fetchone()
        loose = loose_row[0] if loose_row else None
        if pk is not None:
            pk = str(pk)
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

    def _trusted_in_job(self, member_id: int, job: str) -> bool:
        return self.c.execute(
            """SELECT 1 FROM observations o JOIN pages p ON p.page_id=o.page_id JOIN runs r ON r.run_id=p.run_id
               WHERE o.member_id=? AND r.job_id=? AND p.quality='trusted' LIMIT 1""",
            (member_id, job)).fetchone() is not None

    # ------------------------------------------------------------ commit de página — solução C (§6.3.8)
    def commit(self, tok: Token, job: str, run: int, *, attempt_id: str, pos: int, cursor_in: Optional[str],
               cursor_out: Optional[str], members: Sequence[Sequence[Any]], page_ok: bool = True,
               now: Optional[float] = None, screen_hash: Optional[str] = None, loading: bool = False,
               empty_state: bool = False, end_marker: bool = False, counter: Optional[Sequence[Any]] = None,
               received_at: Optional[float] = None, **_ignored: Any) -> str:
        acct, owner, gen = tok
        c = self.c
        members = [(pk, u) for pk, u in members]
        chash = hashlib.sha256(canonical_page(run, pos, cursor_in, cursor_out, members, page_ok, empty_state,
                                              loading, end_marker, screen_hash, counter)).hexdigest()

        def body(t: float) -> str:
            prev = c.execute("SELECT run_id, content_hash, quality FROM pages WHERE attempt_id=?",
                             (attempt_id,)).fetchone()
            if prev is not None:
                if prev[0] != run:
                    raise _Rollback("error:attempt_id_de_outra_execucao")
                if prev[1] == chash:
                    raise _Rollback(f"duplicate:{prev[2]}")
                raise _Rollback("error:attempt_id_reutilizado_com_conteudo_diferente")
            row = c.execute(
                """SELECT a.auth_state, r.ended_at, r.progress_state, r.next_pos, r.trusted_cursor, j.current_run,
                          j.progress_kind, j.target, j.list_type, r.segment, r.segment_started_at,
                          r.replay_target, r.replay_done, r.frontier_streak, r.rounds_without_new, r.stuck_rounds,
                          r.continuity_gaps, r.last_screen_hash, r.last_screen_members
                     FROM accounts a
                     JOIN runs r ON r.account=a.account AND r.lease_owner=a.lease_owner AND r.lease_gen=a.lease_gen
                     JOIN jobs j ON j.job_id=r.job_id
                    WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?
                      AND r.run_id=? AND j.job_id=?""", (acct, owner, gen, t, run, job)).fetchone()
            if row is None:
                raise _Rollback("rejected:posse")
            (auth, ended, pstate, next_pos, tcur, cur_run, kind, target, lt, segment, seg_started,
             rtarget, rdone, frontier, rwn, stuck, gaps, last_hash, last_members) = row
            if cur_run != run or ended is not None:
                raise _Rollback("rejected:execucao_nao_corrente")
            if pstate in ("end_confirmed", "end_unknown"):
                raise _Rollback("rejected:execucao_finalizada")
            if pos != next_pos:
                raise _Rollback(f"rejected:posicao_obsoleta(esperada={next_pos})")
            if kind == "cursor" and tcur != cursor_in:
                raise _Rollback("rejected:cursor_divergente")
            prior = c.execute("SELECT COUNT(*) FROM pages WHERE run_id=? AND pos=?", (run, pos)).fetchone()[0]
            if prior > POLICY["max_rereads_per_pos"]:
                raise _Rollback("rejected:limite_de_releituras")
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
                      (attempt_id, run, segment, pos, cursor_in, cursor_out, "page-v1", chash, len(members),
                       quality, reason, POLICY["version"], received_at if received_at is not None else t, t))
            page_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            new_for_job, seen_ids = 0, set()
            for (pk, raw), name in zip(members, names):
                mid = self._resolve_member(target, lt, pk, name, t)
                if quality == "trusted" and mid not in seen_ids and not self._trusted_in_job(mid, job):
                    new_for_job += 1
                seen_ids.add(mid)
                c.execute("INSERT OR IGNORE INTO observations(page_id, member_id, username_seen) VALUES(?,?,?)",
                          (page_id, mid, str(raw)))
            if counter is not None:
                hi = counter[2] if len(counter) > 2 else counter[1]
                c.execute("""UPDATE runs SET counter_kind=?, counter_lo=?, counter_hi=?, counter_read_at=?
                             WHERE run_id=?""", (counter[0], counter[1], hi, t, run))
            if quality == "trusted":
                state, evidence = self._progress(kind, run, job, cursor_out, end_marker, screen_hash, loading, names,
                                                 new_for_job, segment, seg_started, rtarget, rdone, frontier, rwn,
                                                 stuck, gaps, last_hash, last_members, t)
                if state in ("end_confirmed", "end_unknown"):
                    status = self._run_status(run, assume_ended=True)["status"]
                    c.execute("UPDATE runs SET ended_at=?, stop_reason=? WHERE run_id=?",
                              (t, "end_confirmed" if state == "end_confirmed" else evidence, run))
                    c.execute("UPDATE jobs SET status=?, end_reason=?, updated_at=? WHERE job_id=?",
                              ("complete" if status == "complete" else "partial", evidence, t, job))
            elif pstate == "not_started":
                c.execute("UPDATE runs SET progress_state='in_progress', last_commit_at=? WHERE run_id=?", (t, run))
            if self._fault_hook is not None:
                self._fault_hook()   # injeção de falha dentro da transação (testes)
            return f"committed:{quality}"
        return self._tx(now, body)

    def _progress(self, kind: str, run: int, job: str, cursor_out: Optional[str], end_marker: bool,
                  screen_hash: Optional[str], loading: bool, names: List[str], new_for_job: int, segment: int,
                  seg_started: float, rtarget: int, rdone: int, frontier: int, rwn: int, stuck: int, gaps: int,
                  last_hash: Optional[str], last_members: Optional[str], now: float) -> Tuple[str, Optional[str]]:
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
        verification, _ = self._counter(run, collected)
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

    # ------------------------------------------------------------ resultados (§6.3.9)
    def _run_collected(self, run: int) -> int:
        return self.c.execute("""SELECT COUNT(DISTINCT o.member_id) FROM observations o
                                 JOIN pages p ON p.page_id=o.page_id WHERE p.run_id=? AND p.quality='trusted'""",
                              (run,)).fetchone()[0]

    def _counter_kind(self, run: int) -> Optional[str]:
        return self.c.execute("SELECT counter_kind FROM runs WHERE run_id=?", (run,)).fetchone()[0]

    def _counter(self, run: int, collected: int) -> Tuple[str, bool]:
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

    def _run_status(self, run: int, assume_ended: bool = False) -> Dict[str, Any]:
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

    def run_result(self, run: int) -> Dict[str, Any]:
        """Resultado de UMA execução, calculado só com ela (I10)."""
        return self._run_status(run)

    def job_view(self, job: str) -> Dict[str, Any]:
        """Seleção, última tentativa e histórico observado (§6.3.9, decisão 5)."""
        c = self.c
        runs = [r[0] for r in c.execute("SELECT run_id FROM runs WHERE job_id=? ORDER BY run_id DESC", (job,))]
        results = [self._run_status(r) for r in runs]
        if not results:
            return {"job_id": job, "selected_run_id": None, "selected_run_status": None, "selected_run_at": None,
                    "selected_members": [], "selected_suspect_open": 0, "last_attempt_run_id": None,
                    "last_attempt_status": None, "last_attempt_state": None, "last_attempt_stop_reason": None,
                    "last_attempt_at": None, "history_observed": {}, "history_label": HISTORY_LABEL,
                    "suspect_total": 0}
        selected = next((r for r in results if r["status"] == "complete"), results[0])
        last = results[0]

        def when(r: Dict[str, Any]) -> Optional[float]:
            return r["ended_at"] if r["ended_at"] is not None else (r["last_commit_at"] or r["started_at"])

        history = {u: {"first_trusted_at": f, "last_trusted_at": lt} for u, f, lt in c.execute(
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

    def result_members(self, job: str, interpretation: Any = None) -> set:
        return set(self.job_view(job)["selected_members"])

    # ------------------------------------------------------------ backup (§6.3.13)
    def backup_from_connection(self, src_conn: sqlite3.Connection, dst: Any, pages: int = -1,
                               progress: Optional[Callable[[int, int, int], object]] = None) -> None:
        if src_conn.in_transaction:
            raise BackupMisuse("backup a partir de conexão com transação aberta é proibido")
        own = isinstance(dst, str)
        dst_conn = sqlite3.connect(dst) if own else dst
        try:
            src_conn.backup(dst_conn, pages=pages, progress=progress)
        finally:
            if own:
                dst_conn.close()

    def verify_backup(self, path: str) -> Dict[str, Any]:
        v = sqlite3.connect(path)
        try:
            integrity = v.execute("PRAGMA integrity_check").fetchone()[0]
            fk = len(v.execute("PRAGMA foreign_key_check").fetchall())
            dup = len(v.execute("""SELECT run_id, pos FROM pages WHERE quality='trusted'
                                   GROUP BY run_id, pos HAVING COUNT(*) > 1""").fetchall())
            mismatch = v.execute("""SELECT COUNT(*) FROM runs r WHERE r.next_pos <>
                                    (SELECT COUNT(*) FROM pages p WHERE p.run_id=r.run_id AND p.quality='trusted')"""
                                 ).fetchone()[0]
            marker = dict(zip(("max_run_id", "max_page_id", "max_committed_at", "pages"),
                              v.execute("""SELECT (SELECT MAX(run_id) FROM runs), MAX(page_id), MAX(committed_at),
                                                  COUNT(*) FROM pages""").fetchone()))
        finally:
            v.close()
        ok = integrity == "ok" and fk == 0 and dup == 0 and mismatch == 0
        return {"ok": ok, "integrity": integrity, "fk_violations": fk, "dup_trusted": dup,
                "next_pos_mismatch": mismatch, "marker": marker, "compared_with_live_origin": False}

    def backup_online(self, dst_path: str, pages: int = -1, deadline_s: float = 60.0,
                      step_sleep_s: float = 0.0) -> Dict[str, Any]:
        """Passo único por conexão própria, com prazo; promoção só após verificação."""
        partial = dst_path + ".partial"
        if os.path.exists(partial):
            os.remove(partial)
        t0, steps = time.monotonic(), [0]

        def progress(status: int, remaining: int, total: int) -> None:
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
    def _digest(path: str) -> str:
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

    def backup_maintenance(self, dst_path: str, now: Optional[float] = None,
                           deadline_s: float = 60.0) -> Dict[str, Any]:
        """Comparação exata com a origem: só com tudo drenado."""
        t = self.clock() if now is None else now
        live_leases = self.c.execute("SELECT COUNT(*) FROM accounts WHERE lease_until > ?", (t,)).fetchone()[0]
        open_runs = self.c.execute(
            """SELECT COUNT(*) FROM runs r JOIN accounts a ON a.account=r.account AND a.lease_owner=r.lease_owner
               AND a.lease_gen=r.lease_gen WHERE r.ended_at IS NULL AND a.lease_until > ?""", (t,)).fetchone()[0]
        if live_leases or open_runs:
            return {"status": "rejected:nao_drenado", "live_leases": live_leases, "open_runs": open_runs}
        info = self.backup_online(dst_path, pages=-1, deadline_s=deadline_s)
        if info["status"] != "ok":
            return info
        info["digest_equal"] = self._digest(self.path) == self._digest(dst_path)
        info["compared_with_origin_in_maintenance_window"] = True
        return info


__all__ = ["BackupMisuse", "BackupTimeout", "JobStore", "POLICY", "validate_timing"]
