# SPDX-License-Identifier: Apache-2.0
"""Reference checks only when a host supplies scope; this module never executes a command."""
from __future__ import annotations

import ntpath
import re
import shlex
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from g6.coding_agent_gate.evidence import Evidence

_TEST_FILE = re.compile(r"\.(?:py|pyw|js|jsx|ts|tsx|mjs|cjs|go|rs|rb|java|cs)$", re.IGNORECASE)
_LINE = re.compile(r":\d+(?:-\d+)?$")


def _test_refs(command: str) -> list[str]:
    paths, _ = _classified(command)
    return [word for word in paths if _TEST_FILE.search(word.split("::", 1)[0])]


_PUNCTUATION = frozenset(";&|<>()")
#: The test runner whose positional arguments are all paths or node ids.
_RUNNERS = frozenset({"pytest", "py.test"})


def _shell_words(command: str):
    """POSIX shlex tokens with their original spans, retaining quoting and adjacency.

    shlex reads one character ahead at a punctuation boundary. Its character
    pushback belongs to the next token, not the current span. A consumed whitespace
    delimiter likewise is not part of a word. No second shell grammar decodes words.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    while True:
        start = lexer.instream.tell() - len(lexer._pushback_chars)
        word = lexer.get_token()
        if word is None:
            return
        while start < len(command) and command[start] in lexer.whitespace:
            start += 1
        end = lexer.instream.tell() - len(lexer._pushback_chars)
        if lexer.state == " " and end > start and command[end - 1] in lexer.whitespace:
            end -= 1
        yield word, command[start:end], start, end


def _segments(command: str) -> list[list[str]]:
    """Simple argv shapes, with redirection descriptors and operands removed.

    A pipe starts another program; a redirect changes the current program's IO,
    never starts a program or names a test. Unsupported IO syntax fails closed.
    """
    segments: list[list[str]] = [[]]
    operand = False
    previous = None
    for word, raw, start, end in _shell_words(command):
        operator = raw == word and word and set(word) <= _PUNCTUATION
        if operand:
            if operator or not word:
                raise ValueError("redirection needs an operand")
            operand = False
            continue
        if operator:
            if word in {"<", ">", ">>", "<&", ">&", "&>"}:
                if (word != "&>" and previous is not None and previous[1] == start
                        and previous[0].isascii() and previous[0].isdigit()):
                    segments[-1].pop()
                operand = True
            elif word in {";", "&&", "||", "|"}:
                segments.append([])
            else:
                raise ValueError("unsupported shell operator")
            previous = None
        else:
            segments[-1].append(word)
            previous = (raw, end)
    if operand:
        raise ValueError("redirection needs an operand")
    return [words for words in segments if words]


def _pathlike(word: str) -> bool:
    return bool(_TEST_FILE.search(word.split("::", 1)[0])) or "/" in word or "\\" in word or word.startswith(".")


def _pytest_selectors(words: list[str]) -> list[str]:
    """Use pytest's built-in option registrations, without initialization or collection.

    get_config registers only pytest's built-ins. Calling the parser directly skips
    preparse: no conftest, entry-point plugins, PYTEST_ADDOPTS or PYTEST_PLUGINS.
    In particular, -p is parsed as data, never used to import a named plugin.
    Unknown plugin options and incompatible/missing pytest parsers fail closed.
    """
    from _pytest.config import get_config

    before, after = (words[:words.index("--")], words[words.index("--") + 1:]) if "--" in words else (words, [])
    config = get_config(args=[])
    try:
        return list(config._parser.parse(before).file_or_dir) + after
    finally:
        config._ensure_unconfigure()


def _classified(command: str) -> tuple[list[str], list[str]]:
    """Contain every command-reading failure and parser diagnostic at one boundary.

    An unreadable command contributes no partial selectors, only its unchecked
    reference. The gate's existing unchecked-reference finding requires revision;
    independent policy and shortcut findings still keep their own severity.
    """
    with StringIO() as output, redirect_stdout(output), redirect_stderr(output):
        try:
            return _classify(command)
        except (Exception, SystemExit):
            return [], [command]


def _classify(command: str) -> tuple[list[str], list[str]]:
    """A test command's path references, and the words G6 cannot classify as program, option or path.

    After a recognised pytest program/module every positional word is a path or node id. In any other command, a word
    with a test-file suffix or a path separator is a path; the first word is the program; any other positional
    word cannot be classified."""
    paths: list[str] = []
    unknown: list[str] = []
    segments = _segments(command)
    # A bare node id is evidence naming one test, never a program in a pipeline.
    if len(segments) == 1 and len(segments[0]) == 1:
        node = segments[0][0]
        if command.strip().strip("\"'") == node and "::" in node and _pathlike(node.split("::", 1)[0]):
            return [node], []
    for words in segments:
        program = words[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        runner = 0 if program in _RUNNERS else None
        if (re.fullmatch(r"python(?:\d+(?:\.\d+)?)?|py", program)
                and len(words) >= 3 and words[1] == "-m" and words[2] in _RUNNERS):
            runner = 2
        if runner is not None:
            paths.extend(_pytest_selectors(words[runner + 1:]))
            continue
        unknown.append(words[0])  # an unknown runner cannot establish that all references were checked
        for word in words[0 if _pathlike(words[0]) else 1:]:
            if not word.startswith("-"):
                (paths if _pathlike(word) else unknown).append(word)
    return paths, unknown


def named_tests(command: str) -> list[str]:
    """File/node arguments, not an interpretation or execution of the shell command."""
    return _test_refs(command)


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
        refs, unknown = ([item.ref], []) if item.kind == "file" else _classified(item.ref)
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
