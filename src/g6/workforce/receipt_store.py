# SPDX-License-Identifier: Apache-2.0
"""Test receipts: G6's own record that it ran a repository's tests, confined, on the files it names (plan Step 2,
amendment A6).

``repo_tests.run(..., receipt=True)`` writes one receipt after a confined run that started, and only then: never for
a run without an operating-system boundary (whoever allowed it), one that could not start, a mutant's, or one beside
a git-ignored file that could change the run (``repo_tests.ignored_inputs``: an ignored ``conftest.py``,
``pytest.toml``, ``.pytest.toml``, ``pytest.ini``, ``.pytest.ini``, ``setup.cfg``, ``tox.ini``, ``pyproject.toml``,
``sitecustomize.py``,
``usercustomize.py`` or ``.pth`` file, a test-named file, a Python file named like a tracked module, which could
shadow it, bytecode outside ``__pycache__``, a compiled extension module, a link, or package metadata declaring a
pytest plugin), or when that scan stopped at its limit: git's view, which the digest covers, leaves ignored files out.
Every other ignored Python file (a generated ``_version.py``, say) is bound into the receipt instead, path to SHA-256,
as ``ignored_inputs``. A receipt says what ran (the interpreter, with the SHA-256 of its file; the tests asked for,
collected and left out), on what (the digest of git's view of the repository before and after the run, the digest the
patch review calls ``artifact_digest``, and the commit checked out), how (the run's isolation record) and what pytest
found. Each goes into an append-only, hash-chained store, ``$G6_HOME/evidence/test_receipts.jsonl``; its id is the
SHA-256 of its body. ``check`` says ``matched`` only when the store's chain checks out, the body still hashes to its
id, and the run began on the artifact named. The coding-agent gate counts an evidence item as G6-observed only through
it, and only for the artifact digest its host bound it to, never one a caller sends.

What a receipt shows: G6 ran these tests on these files in that environment, and what pytest reported. It does not
show that the code is correct or that the tests are good ones, and the tests share their process with pytest's
report, so code under test could still bend what pytest reports. Nothing binds what the run loaded from outside git's
view and the scan above: the packages and pytest plugins installed in the interpreter's environment (the
repository's own ``.venv`` included, when its interpreter runs the tests; a plugin installed there can still change
results), stale bytecode (``__pycache__``) left beside the sources, and ignored files that are not Python (data the
tests read, say), which the scan neither judges nor binds.

Known limits, deferred: every ``check`` reads and rehashes the whole store; a receipt records the run's command as a
short summary and does not record the environment variables or installed packages.

The limit: the store is a file on this computer, owned by the same user as G6. It defends against JSON a caller sends
(an evidence item cannot make itself G6-observed by naming an id, a digest or a receipt's body), not against code
running as that user, which can write the file, or call ``_record``, itself.

A store whose chain does not check out (a torn last line after a crash, an edit) is ``store_unreadable`` to every
check, and takes no more receipts. To recover, move the file aside: G6 starts a new store, and the receipts in the
old one no longer count.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep

from g6.core.ledger import ChainLedger, digest
from g6.core.outside import find_program
from g6.core.storage import state_path

SCHEMA = "g6.test_receipt/1"
KIND = "test-receipt"
#: Why an evidence item that cites a receipt is, or is not, G6-observed (``check``). Not "verified": that word is a
#: reliability state, and the door rewrites it wherever it appears in a result.
MATCHED, UNKNOWN, ARTIFACT_MISMATCH, STORE_UNREADABLE = "matched", "unknown", "artifact_mismatch", "store_unreadable"
#: How to recover from ``STORE_UNREADABLE``.
RECOVERY = ("move $G6_HOME/evidence/test_receipts.jsonl aside: G6 starts a new store, and the receipts in the old one "
            "no longer count")


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
    """``("matched", body)`` when the store holds a receipt with this id, in a chain that checks out, whose body still
    hashes to the id and whose run began on ``artifact_digest`` (its ``source_before``); ``("artifact_mismatch",
    None)`` when it holds such a receipt but ``artifact_digest`` is empty or another; ``("store_unreadable", None)``
    when the store is there but its chain does not check out (``RECOVERY``); ``("unknown", None)`` for anything else, a
    missing store or an edited body included. Reads only: never raises, never writes."""
    try:
        path = store_path()
        if not (isinstance(receipt, str) and receipt and path.is_file()):
            return UNKNOWN, None
        ledger = ChainLedger(path)   # opening the ledger checks its whole chain
    except Exception:  # a store that cannot be read vouches for nothing
        return STORE_UNREADABLE, None
    try:
        body = ledger.get(KIND, receipt)
    except Exception:
        return UNKNOWN, None
    if receipt_id(body) != receipt:
        return UNKNOWN, None
    if not (isinstance(artifact_digest, str) and artifact_digest) or body.get("source_before") != artifact_digest:
        return ARTIFACT_MISMATCH, None
    return MATCHED, body


def lookup(receipt_id: str, *, artifact_digest: str) -> dict | None:
    """The stored receipt with this id when it binds ``artifact_digest`` (``check``'s ``matched``), else None."""
    return check(receipt_id, artifact_digest=artifact_digest)[1]


def _executable(python: str) -> dict:
    """The interpreter that ran the tests: its path as run; its version, G6's own when it is the interpreter running
    G6, else as its virtual environment's ``pyvenv.cfg`` records it (it is never started to ask: "" when neither
    says); and the SHA-256 of its file ("" when that cannot be read)."""
    exe = python if os.path.isabs(python) else (find_program(python) or python)   # "python" in a frozen build
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
          requested: list[str], found: dict, ignored: dict[str, str] | None = None) -> dict:
    """A receipt's body (schema ``g6.test_receipt/1``) for one confined run: ``before`` and ``after`` are the run's
    ``repo_tests.snapshot`` before and after it, digested as the patch review digests its ``artifact_digest``;
    ``found`` is the run's result; ``ignored`` the git-ignored Python files G6 did not judge, path to SHA-256
    (``repo_tests.Ignored.other``), bound here because the digest leaves them out. ``repo_env``: each variable
    ``G6_REPO_ENV`` gave the tests, with the SHA-256 of its value, never the value; ``env_set_by_g6``: the variables G6
    set itself (the folder Hypothesis keeps its database in)."""
    from g6 import __version__

    return {"schema": SCHEMA, "execution_id": uuid.uuid4().hex, "started_at": started_at, "finished_at": finished_at,
            "g6_version": __version__, "source_before": digest(before), "source_after": digest(after),
            "base_commit": base_commit, "executable": _executable(python),
            "tests": {"requested": list(requested), "collected": list(found.get("collected_ids") or []),
                      "deselected": int(found.get("deselected") or 0), "omitted": list(found.get("omitted_ids") or [])},
            "command": found.get("command", ""), "isolation": dict(found.get("isolation") or {}),
            "ignored_inputs": dict(sorted((ignored or {}).items())),
            "repo_env": dict(found.get("repo_env") or {}), "env_set_by_g6": list(found.get("env_set_by_g6") or []),
            "result": {key: found.get(key) for key in ("verdict", "passed", "failed", "not_passed", "total",
                                                       "failed_ids", "completed", "degraded")}}


def counts_as_pass(body: dict) -> bool:
    """Whether a receipt's run is a pass: pytest's verdict ``passed`` with at least one test, nothing skipped or left
    unrun, the run complete and not degraded (the patch review's rule). "1 passed, 50 skipped" is not a pass."""
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    return (result.get("verdict") == "passed" and isinstance(result.get("total"), int) and result["total"] > 0
            and result.get("not_passed") == 0 and result.get("completed") is True and not result.get("degraded"))


__all__ = ["ARTIFACT_MISMATCH", "KIND", "MATCHED", "RECOVERY", "SCHEMA", "STORE_UNREADABLE", "UNKNOWN", "check",
           "counts_as_pass", "lookup", "receipt_id", "store_path"]
