# SPDX-License-Identifier: Apache-2.0
"""The ops of g6.workspace.shared_workspace, each on one task's workspace folder under the G6 home.

The rule that settles two agents' edits is stated here, in code: the first write wins. Every write says which version
it read (``expect_sha256``, the sha256 ``read`` gave; ``None`` for a file not there yet, which is also the default, so a
write that says nothing never replaces a file). The check and the replace happen under the workspace's short exclusive
lock, so a write whose version was replaced meanwhile is refused (``G6_E_WORKSPACE_STALE_WRITE``) with both hashes, and
logged; the loser reads again, merges, and writes with the new sha256. A lock on a path (``lock``) refuses every other
write to it until it is released or expires; the holder's name is the caller's word, and the token in ``lock``'s reply
is what writes through the lock and releases it. ``conflicts`` lists every refused write and every takeover of an
expired lock, from a log G6 only appends to.
"""
from __future__ import annotations

from typing import Any

from g6.workspace import _envelope as E
from g6.workspace import _ops
from g6.workspace._files import Workspace

COMPONENT = "shared_workspace"
_run, _seen = _ops.run, _ops.seen


def read(task_id: str, path: str, *, max_chars: int = 100_000, request_id: str = "", run_id: str = "",
         **workspace: Any) -> E.WorkspaceResult:
    """A text file's content and its sha256: the version to cite as ``expect_sha256`` when writing it back."""
    ids = {"request_id": request_id, "run_id": run_id}
    return _run("read", task_id, lambda ws: _ops.read(ws, path, max_chars, ids), ids, **workspace)


def write(task_id: str, path: str, content: str, *, expect_sha256: str | None = None, token: str = "",
          holder: str = "", dry_run: bool = True, request_id: str = "", run_id: str = "", **workspace: Any
          ) -> E.WorkspaceResult:
    """Write ``content`` only if the file is still the version ``expect_sha256`` names (``None``: no file there yet),
    and no one else holds a live lock on it: a dry run unless ``dry_run`` is false."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.write(path, content, expect_sha256=expect_sha256, token=token, holder=holder, dry_run=dry_run)
        based = "a new file" if done["replaces"] is None else f"the version {str(done['replaces'])[:12]}... it read"
        if dry_run:
            return E.answer("write", E.DRY_RUN, f"dry run: {done['path']} is still {based}, so the write would land; "
                                                "nothing was written.",
                            "Ask again with dry_run false to write it.", evidence=_seen(ws, **done), **done, **ids)
        return E.answer("write", E.WRITTEN, f"wrote {done['path']} over {based}; its SHA-256 read back is "
                                            + ("the one written." if done["read_back"] == done["sha256"] else
                                               "not the one written."),
                        "Cite the new sha256 as the version you wrote; others must read it before they write.",
                        evidence=_seen(ws, **done), **done, **ids)

    return _run("write", task_id, act, ids, **workspace)


def lock(task_id: str, path: str, holder: str, *, ttl_seconds: float = 60.0, token: str = "", request_id: str = "",
         run_id: str = "", **workspace: Any) -> E.WorkspaceResult:
    """Lock ``path`` for ``ttl_seconds`` (or renew a lock with its ``token``); the reply's ``token`` opens it."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.lock(path, holder, ttl_seconds, token=token)
        how = ("renewed" if done["renewed"] else
               f"taken over from {done['took_over']['claimed_holder']!r}, whose lock had expired (logged)"
               if done["took_over"] else "taken")
        return E.answer("lock", E.LOCK_TAKEN, f"lock on {done['path']} {how} for {holder!r} (their word) until "
                                              f"{done['expires_at']}.",
                        "Keep the token: it writes through the lock and releases it. Release it when you are done.",
                        evidence=_seen(ws, **{k: v for k, v in done.items() if k != "token"}), **done, **ids)

    return _run("lock", task_id, act, ids, **workspace)


def unlock(task_id: str, path: str, token: str, *, request_id: str = "", run_id: str = "", **workspace: Any
           ) -> E.WorkspaceResult:
    """Release the lock on ``path`` with the token ``lock`` gave."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.unlock(path, token)
        said = f"released the lock on {done['path']}" if done["released"] else f"{done['path']} held no live lock"
        return E.answer("unlock", E.LOCK_RELEASED, said + ".", "Others may write the file now; they read it first.",
                        evidence=_seen(ws, **done), **done, **ids)

    return _run("unlock", task_id, act, ids, **workspace)


def locks(task_id: str, *, request_id: str = "", run_id: str = "", **workspace: Any) -> E.WorkspaceResult:
    """Every lock on file in the workspace, live or expired, with its holder (their word) and expiry; never a token."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        found = ws.locks()
        live = sum(1 for lock in found if lock["live"])
        return E.answer("locks", E.LISTED, f"{live} live lock(s), {len(found) - live} expired, in {ws.label}.",
                        "Wait for a live lock to be released or to expire before writing its path.",
                        evidence=_seen(ws, live=live, expired=len(found) - live), locks=found, **ids)

    return _run("locks", task_id, act, ids, **workspace)


def conflicts(task_id: str, *, request_id: str = "", run_id: str = "", **workspace: Any) -> E.WorkspaceResult:
    """Every refused write (a stale version, a lock held by another) and every takeover, from G6's append-only log."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        found = ws.conflicts()
        refused, takeovers = found["conflicts"], found["takeovers"]
        cut = f"; {found['unreadable_lines']} line(s) of the log could not be read" if found["unreadable_lines"] else ""
        return E.answer("conflicts", E.LISTED, f"{len(refused)} refused write(s) and {len(takeovers)} takeover(s) "
                                               f"logged in {ws.label}{cut}.",
                        "Each refused writer reads the file again and merges; a person settles what they cannot.",
                        evidence=_seen(ws, refused=len(refused), takeovers=len(takeovers),
                                       unreadable_lines=found["unreadable_lines"]),
                        conflicts=refused[-1000:], takeovers=takeovers[-1000:],
                        unreadable_lines=found["unreadable_lines"], **ids)

    return _run("conflicts", task_id, act, ids, **workspace)


__all__ = ["COMPONENT", "conflicts", "lock", "locks", "read", "unlock", "write"]
