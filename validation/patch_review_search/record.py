# SPDX-License-Identifier: Apache-2.0
"""V10's append-only record (docs/validation/patch_review_search/PROTOCOL.md, "Safeguards": every arm's output and
every rerun result is hashed into an append-only record, whose final hash is committed to G6's public repository
before any label is read). Imports nothing of G6.

Each entry is ``{seq, kind, sha256, previous, chain, data}``: ``sha256`` is the SHA-256 of its data (``canonical``:
JSON with sorted keys and no spaces), and ``chain`` the SHA-256 of the previous entry's chain (64 zeros for the first)
followed by that digest. ``Record`` writes the entries as JSON lines to a file that must be new; ``verify`` reads them
back and refuses (``RecordRefused``) a record whose chain does not hold, or does not end at the committed hash. Nothing
is analysed from a record ``verify`` has not accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

START = "0" * 64
_HASH = re.compile(r"[0-9a-f]{64}")


class RecordRefused(ValueError):
    """A record that cannot be analysed: its chain does not hold, or it does not end at the committed hash."""


def canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def _chain(previous: str, digest: str) -> str:
    return hashlib.sha256((previous + digest).encode("ascii")).hexdigest()


class Record:
    """An append-only record, in memory and, given ``path``, written as JSON lines to that new file."""

    def __init__(self, path: str | Path | None = None):
        self.path, self.entries, self.final = (Path(path) if path else None), [], START
        if self.path is not None:
            with open(self.path, "x", encoding="utf-8"):
                pass

    def add(self, kind: str, data: dict) -> str:
        digest = hashlib.sha256(canonical(data)).hexdigest()
        chain = _chain(self.final, digest)
        entry = {"seq": len(self.entries), "kind": kind, "sha256": digest, "previous": self.final, "chain": chain,
                 "data": data}
        self.entries.append(entry)
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical(entry).decode("utf-8") + "\n")
        self.final = chain
        return chain


def verify(path: str | Path, committed: str) -> list[dict]:
    """The entries of the record at ``path``, once its chain holds from the first entry to the last and its final
    chain hash is ``committed`` (64 lower-case hexadecimal digits); otherwise ``RecordRefused``."""
    if not isinstance(committed, str) or not _HASH.fullmatch(committed):
        raise RecordRefused("the committed hash is not 64 lower-case hexadecimal digits")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordRefused(f"the record cannot be read: {type(exc).__name__}") from exc
    entries, previous = [], START
    for number, line in enumerate(text.splitlines(), 1):
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise RecordRefused(f"line {number} of the record is not JSON") from exc
        if not isinstance(entry, dict) or set(entry) != {"seq", "kind", "sha256", "previous", "chain", "data"}:
            raise RecordRefused(f"line {number} of the record is not an entry")
        digest = hashlib.sha256(canonical(entry["data"])).hexdigest()
        if (entry["seq"] != number - 1 or entry["previous"] != previous or entry["sha256"] != digest
                or entry["chain"] != _chain(previous, digest)):
            raise RecordRefused(f"the record's chain does not hold at line {number}")
        entries.append(entry)
        previous = entry["chain"]
    if previous != committed:
        raise RecordRefused("the record does not end at the committed hash")
    return entries
