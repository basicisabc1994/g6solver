# SPDX-License-Identifier: Apache-2.0
"""Reference checks only when a host supplies scope; this module never executes a command."""
from __future__ import annotations

import ntpath
import re
import shlex
from pathlib import Path

from g6.coding_agent_gate.evidence import Evidence

_TEST_FILE = re.compile(r"\.(?:py|pyw|js|jsx|ts|tsx|mjs|cjs|go|rs|rb|java|cs)$", re.IGNORECASE)
_LINE = re.compile(r":\d+(?:-\d+)?$")


def _test_refs(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    words = list(lexer)
    return [word.strip("\"'") for word in words
            if not word.startswith("-") and _TEST_FILE.search(word.strip("\"'").split("::", 1)[0])]


def named_tests(command: str) -> list[str]:
    """File/node arguments, not an interpretation or execution of the shell command."""
    try:
        return _test_refs(command)
    except ValueError:
        return []


def _relative(ref: str) -> str:
    return _LINE.sub("", ref).replace("\\", "/").removeprefix("./")


def check_references(evidence, *, repo_root: str | None, paths) -> list[str]:
    """Check only declared file refs and test-file arguments, within the supplied root/list.

    Existence is not observation. A path list checks membership only. An OS error is allowed to
    reach the caller: it must not be hidden as a missing file or worked around.
    """
    if repo_root is None and paths is None:
        return []
    allowed = {_relative(str(p)) for p in paths} if paths is not None else None
    root = Path(repo_root).resolve() if repo_root is not None and repo_root.strip() else None
    missing = []
    for item in evidence:
        if item.evidence_status == "observed_by_g6":
            continue  # the ref is the receipt id, whose requested tests came from the store
        try:
            refs = [item.ref] if item.kind == "file" else _test_refs(item.ref) if item.kind == "test" else []
        except ValueError:
            missing.append(item.ref)  # an unparseable command cannot establish that its references resolve
            continue
        for ref in refs:
            rel = _relative(ref.split("::", 1)[0])
            if repo_root is not None:
                target = (root / rel).resolve() if root is not None else None
                valid = bool(target is not None and target.is_relative_to(root) and target.is_file())
                if valid and allowed is not None:
                    valid = target.relative_to(root).as_posix() in allowed
            else:
                valid = (not ntpath.isabs(rel) and ".." not in rel.split("/") and rel in allowed)
            if not valid:
                missing.append(ref)
    return list(dict.fromkeys(missing))


def review_call(task: str, contract: str, evidence: tuple[Evidence, ...], repo_root: str | None) -> dict:
    tests = list(dict.fromkeys(test for item in evidence if item.kind == "test"
                               and item.evidence_status == "asserted" for test in named_tests(item.ref)))
    return {"tool": "g6_review_patch", "arguments": {
        "repo": repo_root if repo_root is not None else "<repository root required>", "task": task,
        "requirements": [{"id": "R1", "text": contract or task, "checks": tests}], "tests": tests}}
