# SPDX-License-Identifier: Apache-2.0
"""Test receipts: G6's own record that it ran a repository's tests, confined, on the files it names (plan Step 2,
amendment A6).

``repo_tests.run(..., receipt=True)`` writes one receipt after a confined run that started, and only then: never for
a run without an operating-system boundary (whoever allowed it), one that could not start, or a mutant's. A receipt
says what ran (the interpreter, with the SHA-256 of its file; the tests asked for, collected and left out), on what
(the digest of git's view of the repository before and after the run, the digest the patch review calls
``artifact_digest``, and the commit checked out), how (the run's isolation record) and what pytest found. Each goes
into an append-only, hash-chained store, ``$G6_HOME/evidence/test_receipts.jsonl``; its id is the SHA-256 of its
body. ``lookup`` gives a receipt back only when the store's chain checks out, the body still hashes to its id, and
the run began on the artifact named. The coding-agent gate counts an evidence item as G6-observed only through it.

What a receipt shows: G6 ran these tests on these files in that environment, and what pytest reported. It does not
show that the code is correct or that the tests are good ones, and the tests share their process with pytest's
report, so code under test could still bend what pytest reports.

The limit: the store is a file on this computer, owned by the same user as G6. It defends against JSON a caller sends
(an evidence item cannot make itself G6-observed by naming an id, a digest or a receipt's body), not against code
running as that user, which can write the file, or call ``_record``, itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

from g6.core.ledger import ChainLedger, digest
from g6.core.storage import state_path

SCHEMA = "g6.test_receipt/1"
KIND = "test-receipt"
#: Why an evidence item that cites a receipt is, or is not, G6-observed (``check``).
VERIFIED, UNKNOWN, ARTIFACT_MISMATCH = "verified", "unknown", "artifact_mismatch"


def store_path() -> Path:
    """The store: ``$G6_HOME/evidence/test_receipts.jsonl``."""
    return state_path("evidence", "test_receipts.jsonl")


def receipt_id(body: dict) -> str:
    """A receipt's id: the SHA-256 hex of its body's canonical JSON (sorted keys, no spaces, strict JSON)."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """One writer at a time across processes (``action_gating.operation_budget``'s sidecar lock)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as handle:
        deadline = monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if monotonic() >= deadline:
                    raise
                sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _record(body: dict) -> str:
    """Append ``body`` to the store and return its id. Private by convention: only ``repo_tests.run`` calls it, after
    a confined run it watched. Raises ``OSError`` or ``ValueError`` (``g6.core.ledger.LedgerError``) when the receipt
    cannot be written; a store whose chain does not check out takes nothing further."""
    ident = receipt_id(body)
    path = store_path()
    with _locked(path):
        ChainLedger(path).append(KIND, ident, body)
    return ident


def check(receipt: str, *, artifact_digest: str) -> tuple[str, dict | None]:
    """``("verified", body)`` when the store holds a receipt with this id, in a chain that checks out, whose body still
    hashes to the id and whose run began on ``artifact_digest`` (its ``source_before``); ``("artifact_mismatch",
    None)`` when it holds such a receipt but ``artifact_digest`` is empty or another; ``("unknown", None)`` for anything
    else, a missing, unreadable or edited store included. Reads only: never raises, never writes."""
    try:
        path = store_path()
        if not (isinstance(receipt, str) and receipt and path.is_file()):
            return UNKNOWN, None
        body = ChainLedger(path).get(KIND, receipt)   # opening the ledger checks its whole chain
        if receipt_id(body) != receipt:
            return UNKNOWN, None
    except Exception:  # a store that cannot be read vouches for nothing
        return UNKNOWN, None
    if not (isinstance(artifact_digest, str) and artifact_digest) or body.get("source_before") != artifact_digest:
        return ARTIFACT_MISMATCH, None
    return VERIFIED, body


def lookup(receipt_id: str, *, artifact_digest: str) -> dict | None:
    """The stored receipt with this id when it binds ``artifact_digest`` (``check``'s ``verified``), else None."""
    return check(receipt_id, artifact_digest=artifact_digest)[1]


def _executable(python: str) -> dict:
    """The interpreter that ran the tests: its path as run; its version, G6's own when it is the interpreter running
    G6, else as its virtual environment's ``pyvenv.cfg`` records it (it is never started to ask: "" when neither
    says); and the SHA-256 of its file ("" when that cannot be read)."""
    exe = python if os.path.isabs(python) else (shutil.which(python) or python)   # "python" in a frozen build
    real = os.path.realpath(exe)
    version = ""
    if os.path.normcase(real) == os.path.normcase(os.path.realpath(sys.executable)):
        version = platform.python_version()
    else:
        for cfg in (Path(exe).parent / "pyvenv.cfg", Path(exe).parent.parent / "pyvenv.cfg"):
            try:
                lines = cfg.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            settings = {key.strip(): value.strip() for key, sep, value in (line.partition("=") for line in lines) if sep}
            version = settings.get("version") or settings.get("version_info", "")
            break
    sha = hashlib.sha256()
    try:
        with open(real, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                sha.update(block)
    except OSError:
        return {"path": python, "version": version, "sha256": ""}
    return {"path": python, "version": version, "sha256": sha.hexdigest()}


def _body(*, started_at: str, finished_at: str, before: dict, after: dict, base_commit: str, python: str,
          requested: list[str], found: dict) -> dict:
    """A receipt's body (schema ``g6.test_receipt/1``) for one confined run: ``before`` and ``after`` are the run's
    ``repo_tests.snapshot`` before and after it, digested as the patch review digests its ``artifact_digest``;
    ``found`` is the run's result."""
    from g6 import __version__

    return {"schema": SCHEMA, "execution_id": uuid.uuid4().hex, "started_at": started_at, "finished_at": finished_at,
            "g6_version": __version__, "source_before": digest(before), "source_after": digest(after),
            "base_commit": base_commit, "executable": _executable(python),
            "tests": {"requested": list(requested), "collected": list(found.get("collected_ids") or []),
                      "deselected": int(found.get("deselected") or 0), "omitted": list(found.get("omitted_ids") or [])},
            "command": found.get("command", ""), "isolation": dict(found.get("isolation") or {}),
            "result": {key: found.get(key) for key in ("verdict", "passed", "failed", "not_passed", "total",
                                                       "failed_ids", "completed")}}


__all__ = ["ARTIFACT_MISMATCH", "KIND", "SCHEMA", "UNKNOWN", "VERIFIED", "check", "lookup", "receipt_id",
           "store_path"]
