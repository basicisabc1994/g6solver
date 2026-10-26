# SPDX-License-Identifier: Apache-2.0
"""WorkspaceManagerBlock: the registry and MCP door (``g6_invoke``) to g6.workspace.workspace_manager.

Each op calls one function of :mod:`g6.workspace.workspace_manager.ops` and returns its labelled result with
``component`` and ``op``. A refusal is a ``blocked-escalated`` result, never a raised error. The typed input refuses
unknown ops and unknown fields (``G6_E_INPUT_VALIDATION``): there is no field for a folder, an approval, a confirmation
or whether git is there, so a caller cannot supply one; the folder is always the task's own under the G6 home.

``write``, ``backup``, ``restore`` and ``cleanup`` change files, so the package lists them in ``__g6_sensitive_ops__``:
the registry refuses them over MCP unless the person who runs the server lists them in ``G6_MCP_SENSITIVE``, and even
then each is a dry run unless ``dry_run`` is false.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.workspace import _envelope as E
from g6.workspace.workspace_manager import ops

#: They change files: from Python, and over MCP only when the server's G6_MCP_SENSITIVE lists them.
SENSITIVE_OPS: tuple[str, ...] = ("write", "backup", "restore", "cleanup")
_CHANGES = " (sensitive: it changes files; a dry run unless dry_run is false)"
_OPS: dict[str, str] = {
    "contained": "contained(task_id, path): whether path stays inside the task's workspace once resolved ('..' and "
                 "links followed), and where; nothing is read or written",
    "read": "read(task_id, path, max_chars): a text file's content, cut at max_chars and said so, with its sha256",
    "files": "files(task_id): each plain file's path, bytes and sha256, and the links skipped, never followed",
    "status": "status(task_id): whether the workspace exists, its files and bytes, whether it holds .git (looked at), "
              "and its backups",
    "write": "write(task_id, path, content, dry_run): write a text file inside the workspace; the path is checked as "
             "part of the write" + _CHANGES,
    "backup": "backup(task_id, dry_run): a gzip tar archive of the plain files in the workspace's backups folder"
              + _CHANGES,
    "restore": "restore(task_id, backup, dry_run): put a backup's files back, every member checked with tarfile's data "
               "filter first and what it replaces backed up" + _CHANGES,
    "cleanup": "cleanup(task_id, dry_run): remove the task's workspace folder and its backups; links are never "
               "followed" + _CHANGES,
}
_NEEDS = {"contained": "path", "read": "path", "write": "path", "restore": "backup"}


class WorkspaceManagerInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["contained", "read", "files", "status", "write", "backup", "restore", "cleanup", "ops", "help"] = "help"
    task_id: str = Field("", max_length=100)
    path: str = Field("", max_length=1000)
    content: str = Field("", max_length=2_000_000)
    backup: str = Field("", max_length=200)
    dry_run: bool = True
    max_chars: int = Field(100_000, ge=1, le=2_000_000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: WorkspaceManagerInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[WorkspaceManagerInput], Any]] = {
    "contained": lambda d: ops.contained(d.task_id, d.path, **_ids(d)),
    "read": lambda d: ops.read(d.task_id, d.path, max_chars=d.max_chars, **_ids(d)),
    "files": lambda d: ops.files(d.task_id, **_ids(d)),
    "status": lambda d: ops.status(d.task_id, **_ids(d)),
    "write": lambda d: ops.write(d.task_id, d.path, d.content, dry_run=d.dry_run, **_ids(d)),
    "backup": lambda d: ops.backup(d.task_id, dry_run=d.dry_run, **_ids(d)),
    "restore": lambda d: ops.restore(d.task_id, d.backup, dry_run=d.dry_run, **_ids(d)),
    "cleanup": lambda d: ops.cleanup(d.task_id, dry_run=d.dry_run, **_ids(d)),
}


@block_contract(
    purpose="give each task its own workspace folder under the G6 home and check every path in it as part of the read "
            "or write, so nothing is read or written outside it",
    mitigates_failure_modes=("write_outside_workspace",),
    verification_method="each path resolved against the folder ('..', symbolic links and junctions followed) before "
                        "the act and the written file's folder resolved again once it is open; a write's sha256 read "
                        "back; every archive member checked with tarfile's data filter before a restore",
)
@component_maturity(
    "beta",
    reason="containment through g6.core.storage.contained, tested on '..', symbolic links, junctions, hard links, "
           "device and network paths and a folder swapped after the check; qualified-draft at best",
)
class WorkspaceManagerBlock(AIBlock[WorkspaceManagerInput, dict, dict]):
    """A task's workspace folder, with every path checked as part of the act. Never ``verified``."""

    def __init__(self, name: str = ops.COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: WorkspaceManagerInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        needed = _NEEDS.get(data.op)
        if not data.task_id.strip() or (needed and not getattr(data, needed).strip()):
            missing = "task_id" if not data.task_id.strip() else needed
            result = E.refuse(data.op, E.INPUT_INVALID, f"{data.op} needs {missing}", **_ids(data))
        else:
            result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "WorkspaceManagerBlock", "WorkspaceManagerInput"]
