"""Spool local durável de tentativas (roadmap §6.3.3).

Um arquivo por tentativa, escrito atomicamente (temporário no mesmo diretório,
fsync, os.replace). A intenção é gravada ANTES do envio; a resposta bruta,
ANTES do commit. Após crash:
  - entrada com resposta → repetir o commit com o mesmo attempt_id;
  - entrada sem resposta → a resposta se perdeu → nova leitura (reprocessamento).
"""
import json
import os
import tempfile
from typing import Any, Dict, List, Optional


class AttemptSpool:
    SUFFIX = ".attempt.json"

    def __init__(self, directory: str) -> None:
        self.dir = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, attempt_id: str) -> str:
        return os.path.join(self.dir, f"{attempt_id}{self.SUFFIX}")

    def _write(self, attempt_id: str, doc: Dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json.tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path(attempt_id))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def begin(self, attempt_id: str, *, run_id: int, pos: int, cursor_in: Optional[str]) -> None:
        self._write(attempt_id, {"attempt_id": attempt_id, "run_id": run_id, "pos": pos,
                                 "cursor_in": cursor_in, "response": None, "received_at": None})

    def record_response(self, attempt_id: str, response: Dict[str, Any], received_at: float) -> None:
        with open(self._path(attempt_id), encoding="utf-8") as f:
            doc = json.load(f)
        doc["response"], doc["received_at"] = response, received_at
        self._write(attempt_id, doc)

    def pending(self, run_id: int) -> List[Dict[str, Any]]:
        out = []
        for name in sorted(os.listdir(self.dir)):
            if not name.endswith(self.SUFFIX):
                continue
            try:
                with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                continue
            if doc.get("run_id") == run_id:
                out.append(doc)
        return sorted(out, key=lambda d: (d["pos"], d["response"] is None))

    def ack(self, attempt_id: str) -> None:
        try:
            os.unlink(self._path(attempt_id))
        except FileNotFoundError:
            pass


__all__ = ["AttemptSpool"]
