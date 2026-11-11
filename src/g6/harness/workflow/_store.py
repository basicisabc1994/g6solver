# SPDX-License-Identifier: Apache-2.0
"""One hash-chained file of the workflow record, opened under the rules ``g6.harness.question_store`` set for its own:
one writer at a time (a sidecar lock), regular files only (never a link or a second name for a file kept elsewhere),
and a chain that does not check out is unreadable, never read as empty.

A chain opened with ``head=True`` also keeps its last entry's hash beside it after every write and checks the chain
against it on every open, so a file cut back to an earlier prefix is unreadable too. Decisions and feedback use that,
because a truncated one would undo a dismissal or a cooldown; a run's chain does not, because a shortened run only
loses observations, which reads as missing coverage.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from g6.core.ledger import ChainLedger, LedgerError
from g6.core.storage import PathRefused, plain_file
from g6.harness.workflow import policy
from g6.workforce.receipt_store import _locked

CODE = "G6_E_WORKFLOW_RECORD_UNREADABLE"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class RecordUnreadable(Exception):
    """A part of the workflow record that cannot be read. The message names no path and repeats no stored content."""

    code = CODE

    def __init__(self, why: str) -> None:
        self.why = why
        super().__init__(f"[{CODE}] a part of the workflow record G6 holds for this project cannot be read ({why}); "
                         "the review is not affected. Deleting that part of the record removes it.")


def plain(path: Path) -> bool:
    """Whether ``path`` holds a plain file; False when nothing is there; ``RecordUnreadable`` for anything else."""
    if not os.path.lexists(path):
        return False
    try:
        plain_file(path)
    except (PathRefused, OSError):
        raise RecordUnreadable("its place holds something that is not a plain file") from None
    return True


def write_whole(path: Path, text: str) -> None:
    """``text`` written whole beside ``path``, readable by its owner only, then moved into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + policy.PART)
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


class _Open:
    """A chain held open under its lock."""

    def __init__(self, chain: Chain, ledger: ChainLedger) -> None:
        self._chain, self._ledger = chain, ledger

    def entries(self) -> list[dict]:
        return self._ledger.entries()

    def has(self, kind: str, ident: str) -> bool:
        return any(entry["kind"] == kind and entry["id"] == ident for entry in self._ledger.entries())

    def append(self, kind: str, ident: str, body: dict) -> bool:
        """One entry, and its head kept; False when this kind and id are already there (a retry). ``LedgerError``
        when the chain's own rules refuse it."""
        if self.has(kind, ident):
            return False
        self._ledger.append(kind, ident, body)
        if self._chain.keeps_head:
            write_whole(self._chain.head, self._ledger.head() + "\n")
        return True


class Chain:
    def __init__(self, path: Path, validate: Callable | None = None, *, head: bool = False) -> None:
        self.path, self.keeps_head, self._validate = path, head, validate
        self.head = path.with_name(path.name.removesuffix(policy.RUN_SUFFIX) + policy.HEAD)

    def exists(self) -> bool:
        return os.path.lexists(self.path) or self.keeps_head and os.path.lexists(self.head)

    def _kept(self) -> str | None:
        if not self.keeps_head or not plain(self.head):
            return None
        try:
            text = self.head.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            raise RecordUnreadable("its head cannot be read") from None
        if not _HEX64.match(text):
            raise RecordUnreadable("its head is not the hash of an entry")
        return text

    def _ledger(self) -> ChainLedger:
        kept = self._kept()
        plain(self.path)
        try:
            ledger = ChainLedger(self.path, validate=self._validate)
        except (LedgerError, OSError, UnicodeError) as exc:
            # the ledger's own words name entries and rules, never a stored value
            raise RecordUnreadable(str(exc)[:300] if isinstance(exc, LedgerError) else "it cannot be opened") from None
        if self.keeps_head and ledger.head() != (kept or ""):
            entries = ledger.entries()
            before = entries[-2]["entry_sha256"] if len(entries) >= 2 else ""
            if not entries or before != (kept or ""):
                raise RecordUnreadable("its chain does not end at the head G6 kept after its last write")
            write_whole(self.head, ledger.head() + "\n")       # a stop between a write and its head
        return ledger

    @contextmanager
    def open(self) -> Iterator[_Open]:
        """The chain under its lock, for a read and a write that must not be separated."""
        try:
            with _locked(self.path):
                yield _Open(self, self._ledger())
        except OSError:
            raise RecordUnreadable("it cannot be opened or written") from None

    def entries(self) -> list[dict]:
        """Every entry, in order; ``[]`` when nothing is stored (and then nothing is created)."""
        if not self.exists():
            return []
        with self.open() as held:
            return held.entries()

    def append(self, kind: str, ident: str, body: dict) -> bool:
        with self.open() as held:
            return held.append(kind, ident, body)

    def files(self) -> tuple[Path, ...]:
        """The chain's file and its sidecars, for removal."""
        return tuple(self.path.with_name(self.path.name + suffix) for suffix in ("", policy.LOCK, policy.PART)) + (
            self.head, self.head.with_name(self.head.name + policy.PART))
