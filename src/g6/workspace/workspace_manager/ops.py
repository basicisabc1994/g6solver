# SPDX-License-Identifier: Apache-2.0
"""The ops of g6.workspace.workspace_manager, each on one task's workspace folder under the G6 home.

Every op names the workspace by ``task_id`` (a plain name); the folder is ``workspaces/<task_id>`` under the G6 home,
never a folder a caller chooses. Every path is relative to that folder and checked as part of the act. Each op returns a
:class:`~g6.workspace._envelope.WorkspaceResult`: ``qualified-draft`` with what G6 observed, or ``blocked-escalated``
with a stable code. ``write``, ``backup``, ``restore`` and ``cleanup`` change files, so they are dry runs unless
``dry_run`` is false (and the package lists them as sensitive). No reply names a folder on the machine: paths are given
relative to the workspace, and the workspace as ``workspaces/<task_id> under the G6 home``.
"""
from __future__ import annotations

from g6.workspace import _envelope as E
from g6.workspace import _ops
from g6.workspace._files import Workspace

COMPONENT = "workspace_manager"
#: How many files one ``files`` reply lists.
MAX_LISTED = 1000
_run, _seen = _ops.run, _ops.seen


def contained(task_id: str, path: str, *, request_id: str = "", run_id: str = "") -> E.WorkspaceResult:
    """Whether ``path`` stays inside the task's workspace once resolved (``..`` and links followed), and where."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        try:
            rel, inside, why = ws.relative(ws.contained(path)), True, ""
        except E.WorkspaceRefused as exc:
            if exc.code == E.INPUT_INVALID and not str(path).strip():
                raise
            rel, inside, why = "", False, exc.reason
        said = f"{path!r} resolves to {rel!r} inside {ws.label}" if inside else f"{path!r} would not stay inside: {why}"
        return E.answer("contained", E.RESOLVED, said + "; nothing was read or written.",
                        "Use the resolved path; a path that would not stay inside is refused by every op.",
                        evidence=_seen(ws, asked=str(path), inside=inside), inside=inside, path=rel, reason=why, **ids)

    return _run("contained", task_id, act, ids)


def read(task_id: str, path: str, *, max_chars: int = 100_000, request_id: str = "", run_id: str = ""
         ) -> E.WorkspaceResult:
    """A text file's content (UTF-8), cut at ``max_chars`` and said so, with the SHA-256 of the whole file."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        return _ops.read(ws, path, max_chars, ids)

    return _run("read", task_id, act, ids)


def files(task_id: str, *, request_id: str = "", run_id: str = "") -> E.WorkspaceResult:
    """Each plain file's path, size and SHA-256 (at most ``MAX_LISTED``), and the links skipped, never followed."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        listed, skipped = ws.files()
        cut = len(listed) > MAX_LISTED
        said = f"{len(listed)} file(s) in {ws.label}" + (f", the first {MAX_LISTED} listed" if cut else "")
        return E.answer("files", E.LISTED, said + (f"; {len(skipped)} link(s) skipped" if skipped else "") + ".",
                        "Read or write a listed file by its path.",
                        evidence=_seen(ws, files=len(listed), skipped=len(skipped)), files=listed[:MAX_LISTED],
                        skipped=skipped, truncated=cut, **ids)

    return _run("files", task_id, act, ids)


def status(task_id: str, *, request_id: str = "", run_id: str = "") -> E.WorkspaceResult:
    """Whether the workspace exists, its files and bytes, whether it holds a .git folder (looked at), its backups."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        found = ws.status()
        said = (f"{ws.label}: {found['files']} file(s), {found['bytes']} bytes, {len(found['backups'])} backup(s)"
                if found["exists"] else f"there is no workspace folder at {ws.label} yet; a write makes it")
        return E.answer("status", E.LISTED, said + ".", "Write to make the workspace; back it up before a change.",
                        evidence=_seen(ws, **{k: v for k, v in found.items() if k != "backups"}), **found, **ids)

    return _run("status", task_id, act, ids)


def write(task_id: str, path: str, content: str, *, dry_run: bool = True, request_id: str = "", run_id: str = ""
          ) -> E.WorkspaceResult:
    """Write ``content`` to ``path`` in the workspace: a dry run unless ``dry_run`` is false."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.write(path, content, dry_run=dry_run)
        new = "a new file" if done["replaces"] is None else "replacing the file there"
        if dry_run:
            return E.answer("write", E.DRY_RUN, f"dry run: would write {done['path']} ({done['bytes']} bytes, {new}); "
                                                "nothing was written.",
                            "Ask again with dry_run false to write it.", evidence=_seen(ws, **done), **done, **ids)
        back = ("its SHA-256 read back is the one written" if done["read_back"] == done["sha256"]
                else "but the SHA-256 read back differs from the one written")
        return E.answer("write", E.WRITTEN, f"wrote {done['path']} ({done['bytes']} bytes, {new}); {back}.",
                        "Cite the sha256 as the version you wrote.", evidence=_seen(ws, **done), **done, **ids)

    return _run("write", task_id, act, ids)


def backup(task_id: str, *, dry_run: bool = True, request_id: str = "", run_id: str = "") -> E.WorkspaceResult:
    """A gzip tar archive of the workspace's plain files in its backups folder: a dry run unless ``dry_run`` is false."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.backup(dry_run=dry_run)
        skipped = f"; {len(done['skipped'])} link(s) skipped" if done["skipped"] else ""
        if dry_run:
            return E.answer("backup", E.DRY_RUN, f"dry run: would archive {done['members']} file(s), {done['bytes']} "
                                                 f"bytes{skipped}; nothing was written.",
                            "Ask again with dry_run false to make the backup.", evidence=_seen(ws, **done), **done,
                            **ids)
        return E.answer("backup", E.BACKED_UP, f"archived {done['members']} file(s) as {done['backup']}{skipped}.",
                        "Restore it by its name; status lists the backups.", evidence=_seen(ws, **done), **done, **ids)

    return _run("backup", task_id, act, ids)


def restore(task_id: str, backup: str, *, dry_run: bool = True, request_id: str = "", run_id: str = ""
            ) -> E.WorkspaceResult:
    """Put a backup's files back in place of the workspace's: a dry run unless ``dry_run`` is false."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.restore(backup, dry_run=dry_run)
        if dry_run:
            return E.answer("restore", E.DRY_RUN, f"dry run: {backup} passed the check of every member and would "
                                                  f"replace {done['replaces']} file(s) with {done['members']}; nothing "
                                                  "was changed.",
                            "Ask again with dry_run false to restore it; what it replaces is backed up first.",
                            evidence=_seen(ws, **done), **done, **ids)
        kept = f"; what it replaced is kept as {done['previous_backup']}" if done["previous_backup"] else ""
        return E.answer("restore", E.RESTORED, f"restored {done['members']} member(s) from {backup}{kept}.",
                        "Restore the previous backup to undo it.", evidence=_seen(ws, **done), **done, **ids)

    return _run("restore", task_id, act, ids)


def cleanup(task_id: str, *, dry_run: bool = True, request_id: str = "", run_id: str = "") -> E.WorkspaceResult:
    """Remove the task's workspace folder and its backups: a dry run unless ``dry_run`` is false."""
    ids = {"request_id": request_id, "run_id": run_id}

    def act(ws: Workspace) -> E.WorkspaceResult:
        done = ws.cleanup(dry_run=dry_run)
        if dry_run:
            return E.answer("cleanup", E.DRY_RUN, f"dry run: would remove {ws.label} with {done['files']} file(s) and "
                                                  f"{done['backups']} backup(s); nothing was removed.",
                            "Ask again with dry_run false to remove it; it cannot be undone.",
                            evidence=_seen(ws, **done), **done, **ids)
        return E.answer("cleanup", E.REMOVED, f"removed {ws.label} ({done['files']} file(s), {done['backups']} "
                                              "backup(s)); links in it were removed, never followed.",
                        "A new write makes the workspace again, empty.", evidence=_seen(ws, **done), **done, **ids)

    return _run("cleanup", task_id, act, ids)


__all__ = ["COMPONENT", "MAX_LISTED", "backup", "cleanup", "contained", "files", "read", "restore", "status", "write"]
