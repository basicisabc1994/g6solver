"""Frozen db65ca1 reading; stdlib and sibling fixtures only.

Copied source; receipt version/executable/digest metadata use fixed test adapters.
"""
from __future__ import annotations

import uuid
__version__ = "fixture"

def digest(value):
    return "fixture-digest"

def _executable(value):
    return {"path": value}


SCHEMA = "g6.test_receipt/1"


def _body(*, started_at: str, finished_at: str, before: dict, after: dict, base_commit: str, python: str,
          requested: list[str], found: dict, ignored: dict[str, str] | None = None) -> dict:
    """A receipt's body (schema ``g6.test_receipt/1``) for one confined run: ``before`` and ``after`` are the run's
    ``repo_tests.snapshot`` before and after it, digested as the patch review digests its ``artifact_digest``;
    ``found`` is the run's result; ``ignored`` the git-ignored Python files G6 did not judge, path to SHA-256
    (``repo_tests.Ignored.other``), bound here because the digest leaves them out. ``repo_env``: each variable
    ``G6_REPO_ENV`` gave the tests, with the SHA-256 of its value, never the value; ``env_set_by_g6``: the variables G6
    set itself (the folder Hypothesis keeps its database in)."""

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
