"""Conteúdo canônico `page-v1` e hash de tentativa (roadmap §6.3.3)."""
import hashlib
import json
import re
import unicodedata
from typing import Any, Iterable, Optional, Sequence

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")


def normalize_username(u: Any) -> str:
    """NFC, sem espaços nas pontas, minúsculas."""
    return unicodedata.normalize("NFC", str(u)).strip().lower()


def canonical_page(run_id: int, pos: int, cursor_in: Optional[str], cursor_out: Optional[str],
                   members: Iterable[Sequence[Any]], page_ok: bool, empty_state: bool, loading: bool,
                   end_marker: bool, screen_hash: Optional[str], counter: Optional[Sequence[Any]]) -> bytes:
    """JSON UTF-8, chaves ordenadas, separadores compactos, sem horários nem qualidade."""
    doc = {
        "schema": "page-v1",
        "run_id": run_id, "pos": pos, "cursor_in": cursor_in, "cursor_out": cursor_out,
        "items": [{"pk": None if pk is None else str(pk), "u": normalize_username(u)} for pk, u in members],
        "page_ok": bool(page_ok), "empty_state": bool(empty_state), "loading": bool(loading),
        "end_marker": bool(end_marker), "screen_hash": screen_hash,
        "counter": list(counter) if counter is not None else None,
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(*args: Any, **kwargs: Any) -> str:
    return hashlib.sha256(canonical_page(*args, **kwargs)).hexdigest()


__all__ = ["USERNAME_RE", "canonical_page", "content_hash", "normalize_username"]
