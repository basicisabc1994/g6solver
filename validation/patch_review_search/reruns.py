# SPDX-License-Identifier: Apache-2.0
"""V10's reruns of every finding's reproduction (docs/validation/patch_review_search/PROTOCOL.md, "Reruns").

A reproduction is ``{file, text, node}``: the test file's path in the repository, its text, and the node id to run
(the file itself, or a test in it). Each rerun writes the text at the path in a fresh copy of the tree and runs the
node with V9's arm 1 command for the repository (``executor``: V9's ``Executor``; at a study level arm 1's container
command, confined, with no network), within ``timeout_s`` (60 seconds unless the freeze entry sets otherwise). This
script imports nothing of G6.

A rerun **fails** on a failed assertion or an exception raised in the reproduction's body, **passes** when it passed,
and **errors** otherwise: it could not be collected, set up or finished in time, or recorded no outcome (read as arm
E reads a run, ``arm_e.reading``). Errors are counted, and are neither failures nor passes. A reproduction that cannot
be run that way (no relative path, no text, or a node outside its file) is not reproducible, and is not run.

- ``before_labels``: three runs on the patched tree and three at the base commit; a finding is **reproducible** when
  it fails in at least two of the three runs on the patched tree. The base results are recorded.
- ``on_reference_fix``: after the labels are read, three runs on the benchmark's reference fix; a finding is
  **compatible with the reference fix** when it passes in at least two, and **not comparable** when it errors in at
  least two.
"""
from __future__ import annotations

from pathlib import Path

from patch_review_search import arm_e

RUNS = 3
TIMEOUT_S = 60
PASS, FAIL, ERROR = "pass", "fail", "error"
_READ = {arm_e.PASSED: PASS, arm_e.FAILED: FAIL}


def problem(reproduction: dict) -> str:
    """Why ``reproduction`` cannot be rerun with the repository's own command ("" when it can)."""
    file, text, node = (reproduction.get(key) for key in ("file", "text", "node"))
    if not isinstance(file, str) or not isinstance(text, str) or not isinstance(node, str):
        return "it does not give a file, its text and a node id"
    parts = file.split("/")
    if not file or file.startswith("/") or "\\" in file or ":" in parts[0] or any(p in ("", ".", "..") for p in parts):
        return "its file is not a relative path inside the repository"
    if node != file and not node.startswith(file + "::"):
        return "its node id is not in its file"
    return ""


def rerun(executor, tree: Path, reproduction: dict, *, runs: int = RUNS, timeout_s: float = TIMEOUT_S) -> list[str]:
    """``runs`` reruns of ``reproduction`` on ``tree``, each in a fresh copy: ``pass``, ``fail`` or ``error`` each."""
    found = []
    for _ in range(runs):
        ran = executor.run(tree, [reproduction["node"]], timeout_s=timeout_s,
                           overlay={"files": {reproduction["file"]: reproduction["text"]}})
        found.append(_READ.get(arm_e.reading(ran, reproduction["node"]), ERROR))
    return found


def before_labels(executor, patched_tree: Path, base_tree: Path, reproduction: dict, *,
                  timeout_s: float = TIMEOUT_S) -> dict:
    """The reruns before any label is read: whether the finding is reproducible, with every result."""
    why = problem(reproduction)
    if why:
        return {"runnable": False, "why": why, "reproducible": False, "patched": [], "base": []}
    patched = rerun(executor, patched_tree, reproduction, timeout_s=timeout_s)
    base = rerun(executor, base_tree, reproduction, timeout_s=timeout_s)
    return {"runnable": True, "why": "", "reproducible": patched.count(FAIL) >= 2, "patched": patched, "base": base}


def on_reference_fix(executor, reference_tree: Path, reproduction: dict, *, timeout_s: float = TIMEOUT_S) -> dict:
    """The reruns on the reference fix, after the labels are read: compatible, not comparable, or neither."""
    why = problem(reproduction)
    if why:
        return {"runnable": False, "why": why, "compatible": False, "not_comparable": False, "reference": []}
    found = rerun(executor, reference_tree, reproduction, timeout_s=timeout_s)
    return {"runnable": True, "why": "", "compatible": found.count(PASS) >= 2,
            "not_comparable": found.count(ERROR) >= 2, "reference": found}
