# SPDX-License-Identifier: Apache-2.0
"""The trees every arm and the label runner work on: the repository at the task's base commit, with the patch applied
to the working tree and never committed, so ``HEAD`` is the base commit (PROTOCOL.md, "Patches").

Each arm gets its own fresh tree, so nothing one arm does reaches another. An empty patch, or one that does not apply
to the base commit, leaves no review decision: ``apply`` says which, and nothing is reviewed.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

#: Settings for every git call here: line endings untouched, no prompt. A fresh local clone has no active hooks.
_GIT = ("git", "-c", "core.autocrlf=false", "-c", "advice.detachedHead=false")
GIT_TIMEOUT_S = 600
_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")


def _env() -> dict[str, str]:
    return {"GIT_TERMINAL_PROMPT": "0", **{k: v for k, v in os.environ.items() if k.upper() in _KEEP}}


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([*_GIT, "-C", str(cwd), *args], check=check, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S, env=_env())


def checkout(source: str | Path, base_commit: str, dest: str | Path) -> Path:
    """A fresh clone of ``source`` at ``base_commit``, detached, in ``dest`` (which must not exist yet)."""
    dest = Path(dest)
    subprocess.run([*_GIT, "clone", "--quiet", "--no-hardlinks", "--no-checkout", "--config", "core.autocrlf=false",
                    str(source), str(dest)], check=True, capture_output=True, text=True, timeout=GIT_TIMEOUT_S,
                   env=_env())
    _git(dest, "checkout", "--quiet", "--detach", base_commit)
    return dest


def apply(tree: Path, patch: str) -> str:
    """Apply ``patch`` to ``tree``'s working tree: ``"non_empty"`` when it applied, ``"empty"`` when there is no
    change in it, ``"not_applying"`` when git cannot apply it to the base commit (the tree is left unchanged)."""
    if not patch.strip():
        return "empty"
    with tempfile.TemporaryDirectory(prefix="v9-patch-") as folder:
        file = Path(folder) / "change.diff"
        file.write_text(patch if patch.endswith("\n") else patch + "\n", encoding="utf-8", newline="\n")
        if _git(tree, "apply", "--check", "--whitespace=nowarn", str(file), check=False).returncode != 0:
            return "not_applying"
        _git(tree, "apply", "--whitespace=nowarn", str(file))
    return "non_empty" if changed_files(tree) else "empty"


def changed_files(tree: Path) -> list[str]:
    """The files the working tree changes against ``HEAD`` (added, modified, deleted) and its untracked files."""
    tracked = _git(tree, "diff", "--name-only", "--no-renames", "HEAD").stdout.splitlines()
    untracked = _git(tree, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return sorted({path for path in [*tracked, *untracked] if path})


_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_NEW = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def patch_paths(patch: str) -> list[str]:
    """The paths a unified diff names (its ``diff --git`` headers, or its ``+++ b/`` lines), sorted."""
    found = {after for _, after in _HEADER.findall(patch)} or set(_NEW.findall(patch))
    return sorted(path.strip() for path in found)
