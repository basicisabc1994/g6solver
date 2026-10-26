# SPDX-License-Identifier: Apache-2.0
"""g6.workspace.shared_workspace — lets several agents share one workspace folder without overwriting each other's work: the first write wins and a stale one is refused.

An extension in the workspace family, on the family's shared workspace folder (``workspaces/<task_id>`` under the G6
home, every path checked as part of the act). The rule is stated in code: every write says which version of the file
it read (``expect_sha256``, the sha256 ``read`` gave; ``None``, the default, for a file not there yet, so a write that
says nothing never replaces one), and the check and the replace happen under the workspace's short exclusive lock. Of
two agents that read the same version, the first write lands; the second is refused (``G6_E_WORKSPACE_STALE_WRITE``)
with the version it expected and the one there now, and it reads again and merges.

    from g6.workspace.shared_workspace import ops
    base = ops.read("report-7", "plan.md").sha256
    ops.write("report-7", "plan.md", "alice's", expect_sha256=base, holder="alice", dry_run=False).written   # True
    ops.write("report-7", "plan.md", "bob's", expect_sha256=base, holder="bob", dry_run=False).code
    # 'G6_E_WORKSPACE_STALE_WRITE', with expected_sha256 and current_sha256; ops.conflicts("report-7") lists it

Ops: ``read``, ``write`` (sensitive, a dry run unless asked), ``lock`` and ``unlock`` (a lock on a path for up to an
hour; the holder's name is the caller's word, and the token in the reply is what writes through the lock and releases
it; an expired lock may be taken over, and the takeover is logged), ``locks`` and ``conflicts`` (every refused write
and every takeover, from a log G6 only appends to). A live lock refuses every other write to its path, a plain
``workspace_manager`` write included, and any restore or clean-up of the workspace. Results are ``qualified-draft`` at
best.

Not ported from the archive's shared_workspace, and why: the model that picked a winner between lock holders, whose
deterministic floor never named one (SHARED_WORKSPACE-02: the first write wins instead); ``detect_conflicts``, which
read fields its own locks never wrote (SHARED_WORKSPACE-01: conflicts are the refused writes, logged as they happen);
the eight-folder layout and the note and merge-queue writers named by caller ids (SHARED_WORKSPACE-03); the patterns,
the strategy files and the scaffold.
"""
from g6.workspace._envelope import WorkspaceRefused, WorkspaceResult
from g6.workspace._files import Workspace
from g6.workspace.shared_workspace import ops
from g6.workspace.shared_workspace.block import SENSITIVE_OPS, SharedWorkspaceBlock, SharedWorkspaceInput

__g6_component__ = True
__g6_block__ = "SharedWorkspaceBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # it changes a file
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a file's text can
# carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("hidden_state_or_workspace_conflict",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "SharedWorkspaceBlock", "SharedWorkspaceInput", "Workspace", "WorkspaceRefused",
           "WorkspaceResult", "ops"]
