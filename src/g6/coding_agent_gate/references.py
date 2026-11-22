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


_PUNCTUATION = frozenset(";&|<>()")
#: The test runner whose positional arguments are all paths or node ids.
_RUNNERS = frozenset({"pytest", "py.test"})
#: pytest options whose value is the next word, never a test path.
_VALUE_OPTIONS = frozenset({"-k", "-m", "-p", "-c", "-o", "-n", "-W", "-r", "--tb", "--maxfail", "--rootdir",
                            "--basetemp", "--junitxml", "--junit-xml", "--deselect", "--ignore", "--ignore-glob",
                            "--confcutdir", "--log-level", "--durations", "--timeout", "--cov", "--cov-report",
                            "--import-mode", "--capture", "--color", "--override-ini"})


def _segments(command: str) -> list[list[str]]:
    """The simple commands of a command line, split at shell punctuation; a redirection's file-descriptor number
    (the ``2`` of ``2>&1``) is not an argument."""
    lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [[]]
    for word in lexer:
        if word and set(word) <= _PUNCTUATION:
            if set(word) & {"<", ">"} and segments[-1] and segments[-1][-1].isdigit():
                segments[-1].pop()
            segments.append([])
        else:
            segments[-1].append(word.strip("\"'"))
    return [words for words in segments if words]


def _pathlike(word: str) -> bool:
    return bool(_TEST_FILE.search(word.split("::", 1)[0])) or "/" in word or "\\" in word or word.startswith(".")


def _classified(command: str) -> tuple[list[str], list[str]]:
    """A test command's path references, and the words G6 cannot classify as program, option or path.

    After ``pytest`` (however launched) every positional word is a path or node id. In any other command, a word
    with a test-file suffix or a path separator is a path; the first word is the program; any other positional
    word cannot be classified."""
    paths: list[str] = []
    unknown: list[str] = []
    for words in _segments(command):
        runner = next((i for i, word in enumerate(words)
                       if word.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe") in _RUNNERS), None)
        if runner is not None:
            value = False
            for word in words[runner + 1:]:
                if value:
                    value = False
                elif word.startswith("-"):
                    value = word in _VALUE_OPTIONS
                else:
                    paths.append(word)
            continue
        for word in words[0 if _pathlike(words[0]) else 1:]:
            if not word.startswith("-"):
                (paths if _pathlike(word) else unknown).append(word)
    return paths, unknown


def named_tests(command: str) -> list[str]:
    """File/node arguments, not an interpretation or execution of the shell command."""
    try:
        return _test_refs(command)
    except ValueError:
        return []


def usable_path(text: str) -> bool:
    """Whether the text can name a path on every OS G6 runs on: no NUL and no unpaired surrogate (UTF-8)."""
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _relative(ref: str) -> str:
    return _LINE.sub("", ref).replace("\\", "/").removeprefix("./")


def check_references(evidence, *, repo_root: str | None, paths) -> tuple[list[str], list[str]]:
    """``(missing, unchecked)``: declared file refs and every path a test command names, checked within the supplied
    root/list, and the references G6 could not check (unparseable, unusable as a path, or unclassifiable). No
    reference is skipped.

    Existence is not observation. A path list checks membership only; a directory selector under a root must be a
    directory inside it. An OS error is allowed to reach the caller: it must not be hidden as a missing file or
    worked around.
    """
    if repo_root is None and paths is None:
        return [], []
    if repo_root is not None and not usable_path(repo_root):
        raise ValueError("repo_root is not a usable path: it holds a NUL or an unpaired surrogate")
    allowed = {_relative(str(p)) for p in paths} if paths is not None else None
    root = Path(repo_root).resolve() if repo_root is not None and repo_root.strip() else None
    missing, unchecked = [], []
    for item in evidence:
        if item.evidence_status == "observed_by_g6":
            continue  # the ref is the receipt id, whose requested tests came from the store
        if item.kind not in ("file", "test"):
            continue
        if not usable_path(item.ref):
            unchecked.append(item.ref)  # no path holds a NUL or an unpaired surrogate
            continue
        try:
            refs, unknown = ([item.ref], []) if item.kind == "file" else _classified(item.ref)
        except ValueError:
            unchecked.append(item.ref)  # an unparseable command cannot establish that its references resolve
            continue
        unchecked += unknown
        for ref in refs:
            rel = _relative(ref.split("::", 1)[0])
            if repo_root is not None:
                target = (root / rel).resolve() if root is not None else None
                valid = bool(target is not None and target.is_relative_to(root)
                             and (target.is_file() or (item.kind == "test" and target.is_dir())))
                if valid and allowed is not None:
                    inside = target.relative_to(root).as_posix()
                    valid = inside in allowed or (target.is_dir() and any(
                        path.startswith(inside.rstrip("/") + "/") for path in allowed))
            else:
                valid = (not ntpath.isabs(rel) and ".." not in rel.split("/") and rel in allowed)
            if not valid:
                missing.append(ref)
    return list(dict.fromkeys(missing)), list(dict.fromkeys(unchecked))


def review_call(task: str, contract: str, evidence: tuple[Evidence, ...], repo_root: str | None) -> dict:
    tests = list(dict.fromkeys(test for item in evidence if item.kind == "test"
                               and item.evidence_status == "asserted" for test in named_tests(item.ref)))
    return {"tool": "g6_review_patch", "arguments": {
        "repo": repo_root if repo_root is not None else "<repository root required>", "task": task,
        "requirements": [{"id": "R1", "text": contract or task, "checks": tests}], "tests": tests}}
