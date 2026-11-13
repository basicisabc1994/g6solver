# SPDX-License-Identifier: Apache-2.0
"""The workspace family's stable codes and the one labelled result each member's ops return.

``_``-prefixed, so it never registers as a component. An answer is ``qualified-draft``: its warning card's code says
what it rests on (what G6 read, listed, wrote or would write), and its evidence says what G6 observed. A refusal is
``blocked-escalated`` with its own code and the reason in words. Nothing is ``verified``: :class:`WorkspaceResult` has
no such state, because a file written, a folder listed or a version stored is an observation of G6's own act, not a
check that the content is right.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from g6.core.reliability_envelope import WarningCard
from g6.core.resources import RESOURCE_LIMIT

# What an answer rests on (its warning card's code).
RESOLVED = "G6_E_WORKSPACE_RESOLVED"
READ = "G6_E_WORKSPACE_READ"
LISTED = "G6_E_WORKSPACE_LISTED"
DRY_RUN = "G6_E_WORKSPACE_DRY_RUN"
WRITTEN = "G6_E_WORKSPACE_WRITTEN"
BACKED_UP = "G6_E_WORKSPACE_BACKED_UP"
RESTORED = "G6_E_WORKSPACE_RESTORED"
REMOVED = "G6_E_WORKSPACE_REMOVED"

# Refusals.
INPUT_INVALID = "G6_E_WORKSPACE_INPUT_INVALID"
OUTSIDE_ROOT = "G6_E_WORKSPACE_OUTSIDE_ROOT"
RESERVED = "G6_E_WORKSPACE_RESERVED"
NOT_FOUND = "G6_E_WORKSPACE_NOT_FOUND"
NOT_A_FILE = "G6_E_WORKSPACE_NOT_A_FILE"
NOT_TEXT = "G6_E_WORKSPACE_NOT_TEXT"
NOT_A_WORKSPACE = "G6_E_WORKSPACE_NOT_A_WORKSPACE"
ARCHIVE_REFUSED = "G6_E_WORKSPACE_ARCHIVE_REFUSED"
OP_FAILED = "G6_E_WORKSPACE_OP_FAILED"

# Several agents in one workspace (shared_workspace).
LOCK_TAKEN = "G6_E_WORKSPACE_LOCK_TAKEN"
LOCK_RELEASED = "G6_E_WORKSPACE_LOCK_RELEASED"
STALE_WRITE = "G6_E_WORKSPACE_STALE_WRITE"
LOCKED = "G6_E_WORKSPACE_LOCKED"
BUSY = "G6_E_WORKSPACE_BUSY"

# The versioned library (align_prompt_library).
LIBRARY_STORED = "G6_E_LIBRARY_STORED"
LIBRARY_READ = "G6_E_LIBRARY_READ"
LIBRARY_LISTED = "G6_E_LIBRARY_LISTED"
LIBRARY_RENDERED = "G6_E_LIBRARY_RENDERED"
LIBRARY_INPUT_INVALID = "G6_E_LIBRARY_INPUT_INVALID"
LIBRARY_NOT_FOUND = "G6_E_LIBRARY_NOT_FOUND"
LIBRARY_UNKNOWN_VERSION = "G6_E_LIBRARY_UNKNOWN_VERSION"
LIBRARY_RETIRED = "G6_E_LIBRARY_RETIRED"
LIBRARY_KIND_MISMATCH = "G6_E_LIBRARY_KIND_MISMATCH"
LIBRARY_MISSING_VALUE = "G6_E_LIBRARY_MISSING_VALUE"
LIBRARY_EXTRA_VALUE = "G6_E_LIBRARY_EXTRA_VALUE"
LIBRARY_CORRUPT = "G6_E_LIBRARY_CORRUPT"
LIBRARY_CATALOGUE = "G6_E_LIBRARY_CATALOGUE"
LIBRARY_NAME_TAKEN = "G6_E_LIBRARY_NAME_TAKEN"
LIBRARY_INSTALL_PARTIAL = "G6_E_LIBRARY_INSTALL_PARTIAL"

_ACTIONS: dict[str, str] = {
    INPUT_INVALID: "Correct the input as the reason says and ask again.",
    OUTSIDE_ROOT: "Name a path inside the workspace, relative to its folder; a path that leaves it, through '..' or "
                  "a link, is never read or written.",
    RESERVED: "Leave G6's own bookkeeping folder (.g6-workspace) alone; name another path.",
    NOT_FOUND: "Check the name with files, status or backups, and ask again.",
    NOT_A_FILE: "Read or write a plain file kept in the workspace itself; copy a linked file in instead.",
    NOT_TEXT: "The file is not UTF-8 text; it can be backed up and restored, but not read as text.",
    NOT_A_WORKSPACE: "Only a folder G6 made a workspace (it holds .g6-workspace) is removed or restored.",
    ARCHIVE_REFUSED: "Nothing was changed. Restore a backup G6 made, or ask a person to check the archive.",
    OP_FAILED: "Nothing more was done. Read the reason; ask again once the machine's problem is fixed.",
    RESOURCE_LIMIT: "Remove files the work no longer needs, or write a smaller file.",
    STALE_WRITE: "Someone changed the file since you read it. Read it again, merge your change into what is there now, "
                 "and write with the new sha256.",
    LOCKED: "Wait until the lock expires or its holder releases it, or ask them; a lock is released only with its "
            "token.",
    BUSY: "Ask again in a moment; another write was in progress.",
    LIBRARY_INPUT_INVALID: "Correct the input as the reason says and ask again.",
    LIBRARY_NOT_FOUND: "Check the name with search; put stores a new one.",
    LIBRARY_UNKNOWN_VERSION: "Use a version or digest that history lists for the name; nothing else was answered.",
    LIBRARY_RETIRED: "The name is retired; read an earlier version by its number or digest, or put a new version.",
    LIBRARY_KIND_MISMATCH: "A name keeps its kind; store the other kind under another name.",
    LIBRARY_MISSING_VALUE: "Give a value for every placeholder the reason names.",
    LIBRARY_EXTRA_VALUE: "Leave out the values the template has no placeholder for.",
    LIBRARY_CORRUPT: "Nothing was read or stored. Ask a person to check the library file against a copy they kept.",
    LIBRARY_NAME_TAKEN: "The name holds a prompt of your own; install the catalogue under a prefix, or keep yours.",
}

State = Literal["qualified-draft", "blocked-escalated"]


class WorkspaceRefused(Exception):
    """An op a workspace member will not do, with its stable code and the reason in words."""

    def __init__(self, code: str, reason: str, **details: Any) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code, self.reason, self.details = code, reason, details


class WorkspaceResult(BaseModel):
    """One op's labelled outcome. Op-specific fields sit beside the envelope's."""

    model_config = ConfigDict(extra="allow", frozen=True)

    operation: str
    code: str = ""
    reason: str = ""
    completion_state: State
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""


def answer(operation: str, card: str, message: str, next_step: str, /, *, evidence: dict[str, Any],
           request_id: str = "", run_id: str = "", **fields: Any) -> WorkspaceResult:
    """A ``qualified-draft`` answer whose card (``card``) says what it rests on and what to do next."""
    warning = WarningCard(code=card, message=message, severity="amber", suggested_action=next_step).model_dump()
    return WorkspaceResult.model_validate({**fields, "operation": operation, "completion_state": "qualified-draft",
                                           "warning_card": warning, "evidence": [evidence], "request_id": request_id,
                                           "run_id": run_id})


def refuse(operation: str, code: str, reason: str, *, evidence: dict[str, Any] | None = None, request_id: str = "",
           run_id: str = "", **fields: Any) -> WorkspaceResult:
    """A ``blocked-escalated`` refusal with its stable code and the reason in words."""
    action = _ACTIONS.get(code, "Read the reason and correct the request.")
    warning = WarningCard(code=code, message=reason, severity="blocked", suggested_action=action).model_dump()
    return WorkspaceResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                           "completion_state": "blocked-escalated", "warning_card": warning,
                                           "evidence": [{**(evidence or {}), "code": code, "reason": reason}],
                                           "request_id": request_id, "run_id": run_id})


__all__ = ["ARCHIVE_REFUSED", "BACKED_UP", "BUSY", "DRY_RUN", "INPUT_INVALID", "LIBRARY_CORRUPT", "LIBRARY_EXTRA_VALUE",
           "LIBRARY_CATALOGUE", "LIBRARY_INPUT_INVALID", "LIBRARY_INSTALL_PARTIAL", "LIBRARY_KIND_MISMATCH", "LIBRARY_LISTED",
           "LIBRARY_MISSING_VALUE", "LIBRARY_NAME_TAKEN",
           "LIBRARY_NOT_FOUND", "LIBRARY_READ", "LIBRARY_RENDERED", "LIBRARY_RETIRED", "LIBRARY_STORED",
           "LIBRARY_UNKNOWN_VERSION", "LISTED", "LOCKED", "LOCK_RELEASED", "LOCK_TAKEN", "NOT_A_FILE", "NOT_A_WORKSPACE", "NOT_FOUND", "NOT_TEXT", "OP_FAILED", "OUTSIDE_ROOT", "READ",
           "REMOVED", "RESERVED", "RESOLVED", "RESOURCE_LIMIT", "RESTORED", "STALE_WRITE", "WRITTEN",
           "WorkspaceRefused", "WorkspaceResult", "answer", "refuse"]
