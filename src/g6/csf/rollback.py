# SPDX-License-Identifier: Apache-2.0
"""Rollback: ``rollback_feasibility`` (a static check of a plan's operations) and ``GitCheckpoint`` (snapshot and an
opt-in hard reset of one git repository).

``OPERATIONS`` is the one operation vocabulary (audit 012, CSF-016). The archive had three that disagreed: its
irreversible set said ``external_api_call`` while both hazard tables said ``external_api``, so an external API call
was classed reversible, and ``process_spawn`` was known to one table and unknown to another. An operation that is not
in the vocabulary is ``unknown`` and makes a plan infeasible: it is never assumed reversible. The archive's estimated
rollback delay (a constant 0.1 s per step, never measured) is gone.

``GitCheckpoint`` is the archive's ``GitRollbackManager`` with the gate inside it (CSF-011). The archive staged every
untracked file (``git add -A``, an unignored ``.env`` included) and ran ``git reset --hard`` for any caller; its
break-glass flag was checked only in one MCP wrapper. Here a snapshot commits tracked files only, a reset needs
``allow_reset=True`` on the object, and every command runs on ``repo_root`` only when it is the repository's top level.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from g6.core.git_env import UnsafeGitConfig, safe_git, safe_git_args
from g6.core.outside import find_program
from g6.csf.schema import INPUT_INVALID, MAX_HORIZON, CSFError, CSFInputError, check_id, labelled

ROLLBACK_INFEASIBLE = "G6_E_CSF_ROLLBACK_INFEASIBLE"
STATIC_CLASSIFICATION = "G6_E_CSF_STATIC_CLASSIFICATION"
GIT_RESET_DISABLED = "G6_E_CSF_GIT_RESET_DISABLED"
GIT_OUTSIDE_ROOT = "G6_E_CSF_GIT_OUTSIDE_ROOT"
GIT_UNAVAILABLE = "G6_E_CSF_GIT_UNAVAILABLE"
GIT_FAILED = "G6_E_CSF_GIT_FAILED"
GIT_UNSAFE = "G6_E_CSF_GIT_UNSAFE"

#: Each known operation: ``reversible`` when restoring a workspace checkpoint undoes it, ``irreversible`` when it acts
#: outside the workspace, where no checkpoint reaches.
OPERATIONS: Mapping[str, str] = MappingProxyType({
    "file_read": "reversible",
    "file_write": "reversible",
    "compute": "reversible",
    "rollback": "reversible",
    "llm_call": "reversible",  # changes nothing a checkpoint holds; the prompt itself has left the machine
    "code_execute": "irreversible",  # arbitrary code can act outside the workspace
    "process_spawn": "irreversible",
    "process_kill": "irreversible",
    "database_write": "irreversible",
    "database_drop": "irreversible",
    "external_api": "irreversible",
    "external_api_call": "irreversible",
    "network_request": "irreversible",
    "network_broadcast": "irreversible",
    "email_send": "irreversible",
    "payment_process": "irreversible",
})


def classify_operation(op: str) -> str:
    """``reversible``, ``irreversible`` or ``unknown`` (not in ``OPERATIONS``; never treated as reversible)."""
    return OPERATIONS.get(op, "unknown") if isinstance(op, str) else "unknown"


def _snapshot_steps(horizon: int) -> tuple[int, ...]:
    """Every ceil(N/10) steps from 0, and the last step (the archive's Definition 5 schedule)."""
    interval = max(1, math.ceil(horizon / 10))
    steps = list(range(0, horizon, interval))
    if steps[-1] != horizon - 1:
        steps.append(horizon - 1)
    return tuple(steps)


@dataclass(frozen=True)
class RollbackFeasibility:
    """Whether a workspace checkpoint can undo every operation of a plan, by the shared vocabulary.

    ``reversible_steps`` are the steps whose operation is reversible (or that have none); ``snapshot_steps`` is when to
    take a checkpoint. This is a static classification of operation names, not an observation of what they do.
    """

    feasible: bool
    horizon: int
    operations: tuple[str, ...]
    irreversible_ops: tuple[str, ...]
    unknown_ops: tuple[str, ...]
    reversible_steps: tuple[int, ...]
    snapshot_interval: int
    snapshot_steps: tuple[int, ...]
    request_id: str = ""
    run_id: str = ""

    @property
    def completion_state(self) -> str:
        return "qualified-draft" if self.feasible else "blocked-escalated"

    def to_envelope(self) -> dict[str, Any]:
        """The result as a library envelope with a ``G6_E_*`` warning card."""
        if self.feasible:
            code, action = STATIC_CLASSIFICATION, "Confirm that each operation does only what its name says."
            message = ("Every operation is reversible by name in g6.csf's vocabulary; this classifies names, it does "
                       "not observe what the operations do.")
        else:
            code, action = ROLLBACK_INFEASIBLE, "Remove or approve the listed operations before relying on rollback."
            parts = [f"irreversible: {', '.join(self.irreversible_ops)}"] if self.irreversible_ops else []
            parts += [f"not in the vocabulary: {', '.join(self.unknown_ops)}"] if self.unknown_ops else []
            message = "A workspace checkpoint cannot undo this plan (" + "; ".join(parts) + ")."
        evidence = {"kind": "csf_rollback_feasibility", "operations": list(self.operations),
                    "classification": {op: classify_operation(op) for op in self.operations},
                    "irreversible_ops": list(self.irreversible_ops), "unknown_ops": list(self.unknown_ops),
                    "measured": False}
        return labelled(self, state=self.completion_state, code=code, message=message, action=action,
                        evidence=[evidence])


def rollback_feasibility(operations: Iterable[str], horizon: int, *, request_id: str = "",
                         run_id: str = "") -> RollbackFeasibility:
    """Classify the plan ``operations`` (operation ``i`` runs at step ``i``, at most ``horizon`` of them). Feasible only
    when every operation is ``reversible``."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= MAX_HORIZON:
        raise CSFInputError(INPUT_INVALID, f"horizon must be an integer between 1 and {MAX_HORIZON}, got {horizon!r}")
    if isinstance(operations, str | bytes | Mapping) or not isinstance(operations, Iterable):
        raise CSFInputError(INPUT_INVALID, "operations must be a list of operation names")
    ops = tuple(operations)
    if any(not isinstance(op, str) or not op for op in ops):
        raise CSFInputError(INPUT_INVALID, "every operation is a non-empty string")
    if len(ops) > horizon:
        raise CSFInputError(INPUT_INVALID, f"{len(ops)} operations do not fit a horizon of {horizon} steps")
    kinds = [classify_operation(op) for op in ops]
    irreversible = tuple(sorted({op for op, kind in zip(ops, kinds, strict=True) if kind == "irreversible"}))
    unknown = tuple(sorted({op for op, kind in zip(ops, kinds, strict=True) if kind == "unknown"}))
    reversible = tuple(t for t in range(horizon) if t >= len(ops) or kinds[t] == "reversible")
    schedule = _snapshot_steps(horizon)
    return RollbackFeasibility(
        feasible=not irreversible and not unknown, horizon=horizon, operations=ops, irreversible_ops=irreversible,
        unknown_ops=unknown, reversible_steps=reversible, snapshot_interval=max(1, math.ceil(horizon / 10)),
        snapshot_steps=schedule, request_id=check_id(request_id, "request_id"), run_id=check_id(run_id, "run_id"))


_COMMIT_ID = re.compile(r"[0-9a-fA-F]{7,64}\Z")
_GIT_TIMEOUT_S = 120


class GitCheckpoint:
    """Snapshot, and only when ``allow_reset=True`` hard-reset, the git repository whose top level is ``repo_root``.

    * ``snapshot(message)`` stages changes to tracked files only (``git add --update``) and commits them, returning
      the commit id (HEAD when nothing changed). Untracked files are never added; add them yourself if you mean to.
    * ``reset(commit)`` runs ``git reset --hard`` to a commit of this repository. It restores tracked files; untracked
      files are neither saved by a snapshot nor removed by a reset. Refused with ``G6_E_CSF_GIT_RESET_DISABLED`` unless
      the object was built with ``allow_reset=True`` (exactly ``True``).
    * Every command runs with ``repo_root`` as the boundary: git is not allowed to search above it, variables that
      point git elsewhere are dropped, and a root that is not the repository's top level is refused with
      ``G6_E_CSF_GIT_OUTSIDE_ROOT`` (a subdirectory would act on the whole enclosing repository).
    * Every command runs as ``g6.core.git_env.safe_git`` sets git up (review B2-M5): the tree may be an agent's, so no
      hook, filter, fsmonitor, signing program or transport its config names runs, and a checkpoint commit is
      unsigned. A repository holding a submodule, or a git that ignores those settings, is refused with
      ``G6_E_CSF_GIT_UNSAFE``.

    Failures raise :class:`~g6.csf.schema.CSFError` with a ``G6_E_CSF_GIT_*`` code; bad arguments raise
    :class:`~g6.csf.schema.CSFInputError`. Nothing is read from or written to any other location.
    """

    def __init__(self, repo_root: str | os.PathLike[str], allow_reset: bool = False) -> None:
        if not isinstance(allow_reset, bool):
            raise CSFInputError(INPUT_INVALID, "allow_reset must be True or False")
        root = Path(repo_root).resolve()
        if not root.is_dir():
            raise CSFInputError(INPUT_INVALID, f"repo_root {str(root)!r} is not a directory")
        self.root = root
        self.allow_reset = allow_reset

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        exe = find_program("git")
        if exe is None:
            raise CSFError(GIT_UNAVAILABLE, "git is not on PATH")
        try:
            with safe_git(self.root, ceiling=self.root.parent, timeout=_GIT_TIMEOUT_S) as env:
                done = subprocess.run([exe, "-C", str(self.root), *safe_git_args(*args)], capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", env=env, timeout=_GIT_TIMEOUT_S,
                                      check=False)
        except UnsafeGitConfig as exc:
            raise CSFError(GIT_UNSAFE, f"git {args[0]} not run: {exc}") from None
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CSFError(GIT_FAILED, f"git {args[0]} did not complete: {type(exc).__name__}") from None
        if check and done.returncode != 0:
            raise CSFError(GIT_FAILED, f"git {args[0]} failed: {' '.join(done.stderr.split())[:300]}")
        return done

    def _require_root_is_toplevel(self) -> None:
        done = self._git("rev-parse", "--show-toplevel", check=False)
        top = done.stdout.strip()
        if done.returncode != 0 or not top or Path(top).resolve() != self.root:
            # audit 012, CSF-011: commands act on repo_root's own repository, never on one that encloses it.
            raise CSFError(GIT_OUTSIDE_ROOT, f"{str(self.root)!r} is not the top level of a git repository")

    def _require_reset_allowed(self) -> None:
        if self.allow_reset is not True:
            # audit 012, CSF-011: the gate is on the object; the archive checked its flag only in one MCP wrapper.
            raise CSFError(GIT_RESET_DISABLED, "git reset --hard is disabled; build GitCheckpoint(repo_root, "
                                               "allow_reset=True) to allow it")

    def _stage(self) -> None:
        # audit 012, CSF-011: tracked files only; the archive's ``git add -A`` committed every untracked file.
        self._git("add", "--update")  # the whole tree: the root is the top level

    def head(self) -> str:
        """The commit id of HEAD."""
        self._require_root_is_toplevel()
        return self._git("rev-parse", "--verify", "HEAD").stdout.strip()

    def snapshot(self, message: str = "g6 checkpoint") -> str:
        """Commit the changes to tracked files and return the commit id (HEAD when there is nothing to commit)."""
        if not isinstance(message, str) or not message.strip() or len(message) > 2000:
            raise CSFInputError(INPUT_INVALID, "message must be a non-empty string of at most 2000 characters")
        self._require_root_is_toplevel()
        self._stage()
        staged = self._git("diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            head = self._git("rev-parse", "--verify", "--quiet", "HEAD", check=False)
            if head.returncode == 0:
                return head.stdout.strip()
            raise CSFError(GIT_FAILED, "nothing to checkpoint: the repository has no commit and no tracked change")
        if staged.returncode != 1:
            raise CSFError(GIT_FAILED, f"git diff failed: {' '.join(staged.stderr.split())[:300]}")
        self._git("commit", "--quiet", "-m", message)
        return self._git("rev-parse", "--verify", "HEAD").stdout.strip()

    def reset(self, commit: str) -> str:
        """``git reset --hard`` to ``commit`` (a commit id of this repository); returns the full id."""
        self._require_reset_allowed()
        if not isinstance(commit, str) or not _COMMIT_ID.match(commit):
            raise CSFInputError(INPUT_INVALID, "commit must be a hexadecimal commit id, as snapshot() returns")
        self._require_root_is_toplevel()
        found = self._git("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", check=False)
        if found.returncode != 0 or not found.stdout.strip():
            raise CSFInputError(INPUT_INVALID, f"{commit!r} is not a commit of this repository")
        sha = found.stdout.strip()
        self._git("reset", "--hard", "--quiet", sha)
        return sha


__all__ = ["GIT_FAILED", "GIT_OUTSIDE_ROOT", "GIT_RESET_DISABLED", "GIT_UNAVAILABLE", "GIT_UNSAFE", "OPERATIONS",
           "ROLLBACK_INFEASIBLE", "STATIC_CLASSIFICATION", "GitCheckpoint", "RollbackFeasibility",
           "classify_operation", "rollback_feasibility"]
