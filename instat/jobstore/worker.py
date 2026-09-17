"""Worker de backend com cursor: leitura remota → spool → commit (roadmap §6.3.3, §6.3.7, §6.3.8).

Ordem por tentativa:
  can_send → attempt_id (UUIDv4) → intenção no spool → envio → resposta no spool
  → commit (mesmo attempt_id) → ack do spool.
Na partida, entradas pendentes do spool desta execução são resolvidas primeiro:
com resposta → repete o commit (idempotente); sem resposta → reprocessamento.

`fault(point, pos)` é gancho de teste para injeção de falhas.
"""
import time
import uuid
from typing import Any, Callable, Dict, Optional

try:
    from instat.jobstore.spool import AttemptSpool
    from instat.jobstore.store import JobStore, Token
except ImportError:  # pragma: no cover
    from jobstore.spool import AttemptSpool  # type: ignore
    from jobstore.store import JobStore, Token  # type: ignore

FAULT_POINTS = ("before_send", "after_response", "after_spool", "in_commit_tx", "after_commit")

Fetch = Callable[[Optional[str], float], Dict[str, Any]]


class InjectedCrash(Exception):
    """Falha injetada em teste (simula queda do processo naquele ponto)."""


class CursorWorker:
    def __init__(self, store: JobStore, spool: AttemptSpool, fetch: Fetch, tok: Token, job: str, run: int, *,
                 request_timeout_s: float = 10.0, fault: Optional[Callable[[str, int], None]] = None,
                 max_pages: int = 1_000_000) -> None:
        if not 0 < request_timeout_s < store.ttl / 2:
            raise ValueError(f"request timeout ({request_timeout_s}) must be < ttl/2 ({store.ttl / 2})")
        self.store, self.spool, self.fetch = store, spool, fetch
        self.tok, self.job, self.run_id = tok, job, run
        self.request_timeout_s = request_timeout_s
        self._fault = fault
        self.max_pages = max_pages
        self.stats = {"committed": 0, "duplicates": 0, "reprocessed": 0, "recovered_from_spool": 0,
                      "discarded_obsolete": 0, "suspect": 0}

    def _hit(self, point: str, pos: int) -> None:
        if self._fault is not None:
            self._fault(point, pos)

    def _commit_entry(self, entry: Dict[str, Any]) -> str:
        pos, resp = entry["pos"], entry["response"]
        self.store._fault_hook = (lambda: self._hit("in_commit_tx", pos)) if self._fault else None
        try:
            result = self.store.commit(
                self.tok, self.job, self.run_id, attempt_id=entry["attempt_id"], pos=pos,
                cursor_in=entry["cursor_in"], cursor_out=resp.get("cursor_out"),
                members=[tuple(m) for m in resp.get("members", [])], page_ok=resp.get("page_ok", True),
                end_marker=resp.get("end_marker", False), counter=resp.get("counter"),
                received_at=entry.get("received_at"), now=None)
        finally:
            self.store._fault_hook = None
        self._hit("after_commit", pos)
        return result

    def _account(self, result: str) -> None:
        if result.startswith("committed:"):
            self.stats["committed"] += 1
            if result == "committed:suspect":
                self.stats["suspect"] += 1
        elif result.startswith("duplicate:"):
            self.stats["duplicates"] += 1

    def _recover(self) -> Optional[str]:
        for entry in self.spool.pending(self.run_id):
            if entry["response"] is None:
                self.stats["reprocessed"] += 1          # resposta perdida: nova leitura
                self.spool.ack(entry["attempt_id"])
                continue
            result = self._commit_entry(entry)
            self._account(result)
            if result.startswith(("committed:", "duplicate:")):
                self.stats["recovered_from_spool"] += 1
                self.spool.ack(entry["attempt_id"])
            elif result.startswith("rejected:posicao_obsoleta"):
                self.stats["discarded_obsolete"] += 1
                self.spool.ack(entry["attempt_id"])
            else:
                return result
        return None

    def _ended(self) -> Optional[str]:
        row = self.store.c.execute("SELECT ended_at, stop_reason FROM runs WHERE run_id=?",
                                   (self.run_id,)).fetchone()
        return row[1] if row and row[0] is not None else None

    def run(self) -> Dict[str, Any]:
        stop = self._recover()
        if stop is not None:
            return {"stop": stop, **self.stats}
        for _ in range(self.max_pages):
            ended = self._ended()
            if ended is not None:
                return {"stop": ended, **self.stats}
            ok, why = self.store.can_send(self.tok, self.job, self.run_id, now=None)
            if not ok:
                return {"stop": why, **self.stats}
            cursor_in, pos = self.store.resume_point(self.job, self.run_id)
            attempt_id = str(uuid.uuid4())
            self.spool.begin(attempt_id, run_id=self.run_id, pos=pos, cursor_in=cursor_in)
            self._hit("before_send", pos)
            response = self.fetch(cursor_in, self.request_timeout_s)
            received_at = time.time()
            self._hit("after_response", pos)
            self.spool.record_response(attempt_id, response, received_at)
            self._hit("after_spool", pos)
            entry = {"attempt_id": attempt_id, "pos": pos, "cursor_in": cursor_in, "response": response,
                     "received_at": received_at}
            result = self._commit_entry(entry)
            self._account(result)
            if result.startswith(("committed:", "duplicate:")):
                self.spool.ack(attempt_id)
                continue
            self.spool.ack(attempt_id)
            return {"stop": result, **self.stats}
        return {"stop": "max_pages", **self.stats}


__all__ = ["FAULT_POINTS", "CursorWorker", "InjectedCrash"]
