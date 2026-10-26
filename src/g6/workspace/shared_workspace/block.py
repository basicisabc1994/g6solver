# SPDX-License-Identifier: Apache-2.0
"""SharedWorkspaceBlock: the registry and MCP door (``g6_invoke``) to g6.workspace.shared_workspace.

Each op calls one function of :mod:`g6.workspace.shared_workspace.ops` and returns its labelled result with
``component`` and ``op``. A refusal is a ``blocked-escalated`` result, never a raised error. The typed input refuses
unknown ops and unknown fields (``G6_E_INPUT_VALIDATION``): there is no field for a folder, a winner, an approval, a
force or an override, so a caller cannot settle a conflict by saying so.

``write`` changes a file, so the package lists it in ``__g6_sensitive_ops__``: the registry refuses it over MCP unless
the person who runs the server lists ``shared_workspace.write`` in ``G6_MCP_SENSITIVE``, and even then it is a dry run
unless ``dry_run`` is false. ``lock`` and ``unlock`` change only G6's own bookkeeping, which no caller's path can name.
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
from g6.workspace._coordination import MAX_TTL
from g6.workspace.shared_workspace import ops

#: It changes a file: from Python, and over MCP only when the server's G6_MCP_SENSITIVE lists it.
SENSITIVE_OPS: tuple[str, ...] = ("write",)
_OPS: dict[str, str] = {
    "read": "read(task_id, path, max_chars): a text file's content and its sha256, the version to cite when writing "
            "it back",
    "write": "write(task_id, path, content, expect_sha256, token, holder, dry_run): write only if the file is still "
             "the version expect_sha256 names (null: no file there yet, the default) and no one else holds a live "
             "lock; a stale write is refused with both hashes and logged (sensitive: it changes a file; a dry run "
             "unless dry_run is false)",
    "lock": "lock(task_id, path, holder, ttl_seconds, token): lock a path for up to an hour, or renew with the token; "
            "the holder is your word, the token in the reply is what opens the lock; an expired lock is taken over "
            "and that is logged",
    "unlock": "unlock(task_id, path, token): release a lock with its token",
    "locks": "locks(task_id): every lock on file, live or expired, with its holder (their word) and expiry",
    "conflicts": "conflicts(task_id): every refused write and every takeover, from G6's append-only log",
}
_NEEDS = {"read": ("path",), "write": ("path",), "lock": ("path", "holder"), "unlock": ("path", "token")}


class SharedWorkspaceInput(BaseModel):
    """One op's parameters. Fields an op does not use are ignored by it; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["read", "write", "lock", "unlock", "locks", "conflicts", "ops", "help"] = "help"
    task_id: str = Field("", max_length=100)
    path: str = Field("", max_length=1000)
    content: str = Field("", max_length=2_000_000)
    expect_sha256: str | None = Field(None, max_length=64)
    token: str = Field("", max_length=100)
    holder: str = Field("", max_length=100)
    ttl_seconds: float = Field(60.0, gt=0, le=MAX_TTL)
    dry_run: bool = True
    max_chars: int = Field(100_000, ge=1, le=2_000_000)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: SharedWorkspaceInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


_RUN: dict[str, Callable[[SharedWorkspaceInput], Any]] = {
    "read": lambda d: ops.read(d.task_id, d.path, max_chars=d.max_chars, **_ids(d)),
    "write": lambda d: ops.write(d.task_id, d.path, d.content, expect_sha256=d.expect_sha256, token=d.token,
                                 holder=d.holder, dry_run=d.dry_run, **_ids(d)),
    "lock": lambda d: ops.lock(d.task_id, d.path, d.holder, ttl_seconds=d.ttl_seconds, token=d.token, **_ids(d)),
    "unlock": lambda d: ops.unlock(d.task_id, d.path, d.token, **_ids(d)),
    "locks": lambda d: ops.locks(d.task_id, **_ids(d)),
    "conflicts": lambda d: ops.conflicts(d.task_id, **_ids(d)),
}


@block_contract(
    purpose="let several agents work in one workspace folder without overwriting each other: every write says which "
            "version it read, the first write wins, a stale one is refused with both hashes and logged",
    mitigates_failure_modes=("hidden_state_or_workspace_conflict",),
    verification_method="the file's sha256 compared with the version the writer read, under the workspace's short "
                        "exclusive lock, before the file is replaced; the written file's sha256 read back",
)
@component_maturity(
    "beta",
    reason="compare-and-swap writes and token locks on the family's shared workspace folder, tested with concurrent "
           "writers; qualified-draft at best",
)
class SharedWorkspaceBlock(AIBlock[SharedWorkspaceInput, dict, dict]):
    """Writes that say which version they replace, locks with a token, and a log of conflicts. Never ``verified``."""

    def __init__(self, name: str = ops.COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: SharedWorkspaceInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        missing = [f for f in ("task_id", *_NEEDS.get(data.op, ())) if not getattr(data, f).strip()]
        if missing:
            result = E.refuse(data.op, E.INPUT_INVALID, f"{data.op} needs {', '.join(missing)}", **_ids(data))
        else:
            result = _RUN[data.op](data)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})


__all__ = ["SENSITIVE_OPS", "SharedWorkspaceBlock", "SharedWorkspaceInput"]
