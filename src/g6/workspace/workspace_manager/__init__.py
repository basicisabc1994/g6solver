# SPDX-License-Identifier: Apache-2.0
"""g6.workspace.workspace_manager — gives each agent task its own workspace folder and checks every path as part of each read and write, so nothing lands outside it.

An extension in the workspace family. Each task's folder is ``workspaces/<task_id>`` under the G6 home
(``g6.core.storage.state_path``), never a shared temporary folder or one a caller names. Every path is resolved against
that folder by ``g6.core.storage.contained`` (``..``, symbolic links and junctions followed; network and device paths
refused before anything is resolved) as part of the act itself, and a write checks its file's folder again once the
file is open.

    from g6.workspace.workspace_manager import Workspace, ops
    ws = Workspace.for_task("report-7")
    ws.write("notes/plan.md", "# Plan\n", dry_run=False)["sha256"]
    ws.write("../elsewhere.txt", "x", dry_run=False)        # WorkspaceRefused: G6_E_WORKSPACE_OUTSIDE_ROOT
    ops.read("report-7", "notes/plan.md").text               # '# Plan\n', qualified-draft

Ops: ``contained``, ``read``, ``files`` and ``status`` read; ``write``, ``backup``, ``restore`` and ``cleanup`` change
files, so they are sensitive and dry runs unless ``dry_run`` is false. A backup is a gzip tar archive of the plain files
(links are skipped and named); a restore takes only a backup kept in the workspace, checks every member with
``tarfile``'s ``data`` filter before anything changes, and backs up what it replaces; clean-up removes only a folder G6
made a workspace and never follows a link out of it. Results are ``qualified-draft`` at best.

Not ported from the archive's workspace_manager, and why: the Obsidian vault server (its ``eval_js`` ran caller
JavaScript) and the always-on memory agents with their aiohttp API (WORKSPACE_MANAGER-05); the agentic risk gate and
its caller-supplied ``confirmed`` and ``has_git`` (WORKSPACE_MANAGER-03: a person's confirmation never arrives as a
field of the same call, and ``.git`` is looked at); hosted tenancy, the system temporary folder as the default root
(WORKSPACE_MANAGER-01), ``git init`` through GitPython, the templates, patterns and scaffold; every result it labelled
verified (WORKSPACE_MANAGER-02).
"""
from g6.workspace._envelope import WorkspaceRefused, WorkspaceResult
from g6.workspace._files import Workspace
from g6.workspace.workspace_manager import ops
from g6.workspace.workspace_manager.block import SENSITIVE_OPS, WorkspaceManagerBlock, WorkspaceManagerInput

__g6_component__ = True
__g6_block__ = "WorkspaceManagerBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they change files
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a file's text can
# carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("write_outside_workspace",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "Workspace", "WorkspaceManagerBlock", "WorkspaceManagerInput", "WorkspaceRefused",
           "WorkspaceResult", "ops"]
