# SPDX-License-Identifier: Apache-2.0
"""An append-only, hash-chained ledger of JSON entries (graduated from ``research.echa.ledger``; audit 037).

Every entry holds a body, the hash of the body, the hash of the entry before it and its own hash. ``verify`` and
``ChainLedger.audit`` recompute all of it, so an edited, removed, added or reordered entry is found. A chain cut at its
tail is still a consistent chain: only a head recorded elsewhere shows it, so the ledger takes ``expect_head``, the head
a reviewer kept.

The chain knows kinds, ids and bodies only. A ``validate`` hook adds a domain's own rules (what an entry of each kind
must reference), given the entries before it; ``research.echa.ledger`` is this chain with ECHA's rules.

Fixed on graduation (audit 037):

- LED-001: before each append the file's last entry is read back, and a ledger whose file another writer extended (or
  removed) since it was opened refuses the write instead of forking the chain. Two writers racing inside that window
  can still interleave; one writer per file is the supported use.
- LED-002: bodies are strict JSON. NaN and infinities are refused on write and on read.
- LED-003: ``digest`` refuses values JSON cannot hold, where the research digest stringified them (``default=str``), so
  a set or an object never digests differently from one process to the next.

In memory when no path is given; one JSON line per entry in a file when one is. Each write is flushed to disk.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

ENTRY_KEYS = frozenset({"seq", "kind", "id", "body", "body_sha256", "prev", "entry_sha256"})
HEAD_DIFFERS = "the head differs from the head kept (entries were removed or added since)"

#: ``validate(kind, ident, body, seen)`` returns what is wrong with one entry; ``seen`` maps (kind, id) to the bodies
#: of the entries before it, in order.
Validator = Callable[[str, str, dict, Mapping[tuple[str, str], dict]], list[str]]


class LedgerError(ValueError):
    """A record the ledger refuses to write."""


class LedgerCorrupt(LedgerError):
    """A ledger whose chain or references do not check out; it accepts nothing further."""


def canonical(obj: Any) -> str:
    """Sorted, compact, strict JSON. A value JSON cannot hold, NaN or an infinity raises ``ValueError``."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except TypeError as exc:
        raise ValueError(f"not JSON: {exc}") from None


def digest(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def _roundtrip(body: Any) -> Any:
    return json.loads(canonical(body))


def _entry_hash(entry: dict) -> str:
    return digest({key: entry.get(key) for key in ("seq", "kind", "id", "body_sha256", "prev")})


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _no_constants(name: str) -> None:
    raise ValueError(f"non-standard JSON constant {name}")


def _parse(line: str) -> Any:
    return json.loads(line, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants)


def _verify(entries: list[dict], validate: Validator | None) -> list[str]:
    problems: list[str] = []
    prev = ""
    seen: dict[tuple[str, str], dict] = {}
    for i, entry in enumerate(entries):
        tag = f"entry {i}"
        if set(entry) != ENTRY_KEYS:
            problems.append(f"{tag}: unexpected or missing fields {sorted(set(entry) ^ ENTRY_KEYS)}")
        if entry.get("seq") != i:
            problems.append(f"{tag}: seq is {entry.get('seq')!r}, expected {i} (an entry was removed, added or reordered)")
        if entry.get("prev") != prev:
            problems.append(f"{tag}: prev does not link to the entry before it")
        body = entry.get("body")
        try:
            body_ok = digest(body) == entry.get("body_sha256")
        except ValueError:
            body_ok = False
        if not body_ok:
            problems.append(f"{tag}: body does not match its body_sha256")
        if _entry_hash(entry) != entry.get("entry_sha256"):
            problems.append(f"{tag}: entry_sha256 does not match its fields")
        prev = entry.get("entry_sha256")
        kind, ident = entry.get("kind"), entry.get("id")
        if isinstance(body, dict) and isinstance(kind, str) and isinstance(ident, str):
            problems += [f"{tag}: {p}" for p in _check(kind, ident, body, seen, validate)]
            seen[(kind, ident)] = body
        else:
            problems.append(f"{tag}: kind, id and body are required")
    return problems


def _check(kind: str, ident: str, body: dict, seen: Mapping[tuple[str, str], dict],
           validate: Validator | None) -> list[str]:
    if (kind, ident) in seen:
        return [f"{kind} {ident!r} is already in the ledger"]
    return list(validate(kind, ident, body, seen)) if validate is not None else []


def _read(path: Path) -> tuple[list[dict], list[str]]:
    entries, problems = [], []
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return [], ["the ledger file is not valid UTF-8"]
    for number, line in enumerate(text.split("\n"), 1):  # "\n" only: U+2028 and U+0085 may sit inside an entry
        if not line.strip():
            continue
        try:
            row = _parse(line)
        except ValueError as exc:
            reason = "duplicate key" if "duplicate" in str(exc) else ("NaN or infinity" if "constant" in str(exc)
                                                                      else "not valid JSON")
            problems.append(f"line {number}: {reason}")
            continue
        if isinstance(row, dict):
            entries.append(row)
        else:
            problems.append(f"line {number}: not an entry")
    return entries, problems


def _file_head(path: Path) -> str | None:
    """The ``entry_sha256`` of the file's last entry, "" for an empty file, None when the file is missing."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            end = handle.tell()
            chunk, pos = b"", end
            while pos > 0 and chunk.strip(b"\n").count(b"\n") < 1:
                step = min(4096, pos)
                pos -= step
                handle.seek(pos)
                chunk = handle.read(step) + chunk
    except FileNotFoundError:
        return None
    lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
    if not lines:
        return ""
    try:
        row = _parse(lines[-1].decode("utf-8"))
    except ValueError:
        return "<unreadable>"
    return row.get("entry_sha256", "") if isinstance(row, dict) else "<unreadable>"


class ChainLedger:
    def __init__(self, path: str | Path | None = None, *, expect_head: str | None = None,
                 validate: Validator | None = None) -> None:
        """``expect_head``: the head a reviewer kept elsewhere; a ledger whose head differs (entries cut from its tail or
        added to it) does not open. Without one the ledger is unanchored: a clean chain says nothing about its tail."""
        self._path = Path(path) if path is not None else None
        self.expect_head = expect_head
        self._validate = validate
        self._entries: list[dict] = []
        self._seen: dict[tuple[str, str], dict] = {}
        if self._path is not None and self._path.exists() and self._path.stat().st_size:
            entries, problems = _read(self._path)
            problems += _verify(entries, validate)
            head = entries[-1].get("entry_sha256", "") if entries else ""
            if expect_head is not None and head != expect_head:
                problems.append(HEAD_DIFFERS)
            if problems:
                raise LedgerCorrupt("; ".join(problems[:5]))
            for entry in entries:
                self._remember(entry)
        elif expect_head:
            raise LedgerCorrupt(HEAD_DIFFERS)

    @property
    def path(self) -> Path | None:
        """The backing path, or None for an in-memory ledger."""
        return self._path

    def reload(self) -> None:
        """Read a fresh validated snapshot without writing; callers serialise reload/count/append.

        Previously observed entries must remain an unchanged prefix. A missing or shortened file
        cannot silently reset this instance's history. An in-memory ledger has nothing to reload.
        """
        if self._path is None:
            return
        fresh = ChainLedger(self._path, expect_head=self.expect_head, validate=self._validate)
        if (len(fresh._entries) < len(self._entries)
                or any(old["entry_sha256"] != new["entry_sha256"]
                       for old, new in zip(self._entries, fresh._entries))):
            raise LedgerCorrupt("previously observed ledger entries were removed or changed")
        self._entries, self._seen = fresh._entries, fresh._seen

    @classmethod
    def audit(cls, path: str | Path, *, expect_head: str | None = None,
              validate: Validator | None = None) -> tuple[str, ...]:
        """What is wrong with a ledger file, without opening it for writing; ``expect_head`` is the head a reviewer kept."""
        target = Path(path)
        if not target.exists():
            return ("the ledger file does not exist",)
        entries, problems = _read(target)
        problems += _verify(entries, validate)
        head = entries[-1].get("entry_sha256", "") if entries else ""
        if expect_head is not None and head != expect_head:
            problems.append(HEAD_DIFFERS)
        return tuple(problems)

    def _remember(self, entry: dict) -> None:
        self._entries.append(entry)
        self._seen[(entry["kind"], entry["id"])] = entry["body"]

    def append(self, kind: str, ident: str, body: dict) -> str:
        """Write one entry and return its hash; a refused entry raises ``LedgerError`` and writes nothing."""
        if not (isinstance(kind, str) and kind and isinstance(ident, str) and ident and isinstance(body, dict)):
            raise LedgerError("kind and id must be non-empty text and body a mapping")
        try:
            body = _roundtrip(body)
        except ValueError as exc:
            raise LedgerError(f"{kind} {ident}: {exc}") from None
        problems = _check(kind, ident, body, self._seen, self._validate)
        if problems:
            raise LedgerError(problems[0])
        entry = {"seq": len(self._entries), "kind": kind, "id": ident, "body": body, "body_sha256": digest(body),
                 "prev": self.head()}
        entry["entry_sha256"] = _entry_hash(entry)
        if self._path is not None:
            on_disk = _file_head(self._path)
            if on_disk != self.head() and not (on_disk is None and not self._entries):
                raise LedgerError("the ledger file changed since it was opened (another writer?); reopen it")
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        self._remember(entry)
        return entry["entry_sha256"]

    def get(self, kind: str, ident: str) -> dict:
        try:
            return _roundtrip(self._seen[(kind, ident)])
        except KeyError:
            raise KeyError(f"no {kind} {ident!r} in the ledger") from None

    def entry(self, kind: str, ident: str) -> dict:
        for entry in self._entries:
            if entry["kind"] == kind and entry["id"] == ident:
                return _roundtrip(entry)
        raise KeyError(f"no {kind} {ident!r} in the ledger")

    def entries(self) -> list[dict]:
        return _roundtrip(self._entries)

    def head(self) -> str:
        return self._entries[-1]["entry_sha256"] if self._entries else ""

    def verify(self) -> tuple[str, ...]:
        problems = _verify(self._entries, self._validate)
        if self.expect_head is not None and self.head() != self.expect_head:
            problems.append(HEAD_DIFFERS)
        return tuple(problems)
