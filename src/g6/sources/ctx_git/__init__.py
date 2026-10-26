# SPDX-License-Identifier: Apache-2.0
"""g6.sources.ctx_git — read and change git repositories inside folders you declare, with every call logged and every change since a point reported, whoever made it.

An extension in the sources family. git runs over its command line (an outside program you install, ``__g6_outside__``)
in ``g6.core.git_env``'s hardened environment: a repository's own config, hooks and attributes cannot make git start a
program (fsmonitor, hooks, pagers, credential helpers, filters, diff and merge drivers, signing, transports), a
submodule is refused, and the git folder and working tree are pinned. It works only in repositories at or under the
roots the person who runs G6 declares (``G6_GIT_CONFIG`` or ``ctx_git/config.json`` under the G6 home); a path outside
them, or a link out of them, is refused, and with no root nothing runs.

    from g6.core.registry import get_registry
    out = get_registry().invoke("ctx_git", "changes_since", {"repo": "C:/work/project", "since": "HEAD~3"}).unwrap()
    [c["how"] for c in out["commits"]]   # "made through ctx_git", "brought in by ctx_git ..." or "not through ctx_git"

Ops: reads ``status``, ``diff``, ``log``, ``show``, ``blame``, ``branch_list``, ``tag_list``, ``remote_list`` and
``changes_since``; local changes ``add``, ``commit``, ``branch_create``, ``branch_switch``, ``merge``, ``stash_save``,
``stash_pop``, ``tag_create`` and ``reset_staged``, which are sensitive ops the person allows and do not ask the action
gate (it is for acts that leave the machine); outward ``fetch``, ``pull``, ``push`` and ``clone``, each checked by
``g6.action_gating`` first: a dry run unless asked otherwise, and then sent only on the gate's allow, to an https or
ssh remote on a host the person declared. Every op that runs git is sensitive. Each call is written to ``ctx_git/audit.jsonl`` under the G6
home, which is how ``changes_since`` tells a commit made through ctx_git from one made any other way.

Not ported from the archive's ctx_git, and why: GitPython (git runs over its command line instead); the MCP
sub-package, its in-memory store and its server; the caller's ``safety_confirm`` and ``allow_dirty_worktree`` flags,
which let an agent approve itself (a dirty working copy is refused with no override, and a force push or force delete
is not offered); init, branch_delete, stash_list and repo_info; the model rerank of the log; the pattern and skill
catalogues, the production gate and the metadata files (one held a personal address); every result it labelled
verified.
"""
from g6.sources.ctx_git import config, ops, runner
from g6.sources.ctx_git.block import SENSITIVE_OPS, CtxGitBlock, CtxGitInput
from g6.sources.ctx_git.config import GitConfig, GitRefused
from g6.sources.ctx_git.runner import GIT

__g6_component__ = True
__g6_block__ = "CtxGitBlock"
__g6_family__ = "sources"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # each runs git on your computer; some change it or the network
__g6_requires__: tuple[str, ...] = ("action_gating", "grounding", "knowledge")  # the gate; the shared reply and notes
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (repository text
# can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("untracked_repo_change",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (GIT,)
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["GIT", "SENSITIVE_OPS", "CtxGitBlock", "CtxGitInput", "GitConfig", "GitRefused", "config", "ops", "runner"]
