# SPDX-License-Identifier: Apache-2.0
"""The review contract (PROTOCOL.md, "Two contracts"): the task's own statement split into numbered requirements.

Arms 2, 3 and 4 receive it identically. The split is mechanical, so no person or model rewrites it:

1. The problem statement is requirement R1, whole.
2. The stated requirements follow: each top-level list item (a line starting ``-``, ``*``, ``+``, a bullet, or a
   number followed by ``.`` or ``)``) is one requirement, with every line up to the next top-level item (indented
   detail, nested items, wrapped lines) kept with it; text before the first item is split into paragraphs. Text with
   no list item is split into paragraphs (blank lines between them).
3. The interface follows, split the same way; a block that says no new interface is introduced is left out.

Line endings are normalised, each line's surrounding blanks are removed, empty text is dropped, and a requirement
repeated word for word is kept once, where it first appears. A requirement longer than G6's limit for one requirement
(``MAX_TEXT``) is split at paragraph breaks, then at line breaks, then at the last space, and never shortened.

The contract carries no checks. ``cite`` attaches the checks an arm may cite (the protocol's open choice, recorded in
the report): the test files the patch adds or changes, the same files for every requirement.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

#: G6's limit on one requirement's text (``g6.harness.patch_review.Requirement``).
MAX_TEXT = 20_000
_ITEM = re.compile(r"^(?P<indent>[ \t]*)(?:[-*+•]|\d{1,3}[.)])[ \t]+(?P<text>\S.*)$")
_NO_INTERFACE = re.compile(r"^\s*no new (?:public )?interfaces?\b", re.IGNORECASE)


def _lines(text: str) -> list[str]:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _clean(lines: list[str]) -> str:
    return "\n".join(line.strip() for line in lines).strip()


def _paragraphs(lines: list[str]) -> list[str]:
    found, current = [], []
    for line in [*lines, ""]:
        if line.strip():
            current.append(line)
        elif current:
            found.append(_clean(current))
            current = []
    return found


def _blocks(text: str) -> list[str]:
    lines = _lines(text)
    matches = [_ITEM.match(line) for line in lines]
    indents = [len(m["indent"].expandtabs(4)) for m in matches if m]
    if not indents:
        return _paragraphs(lines)
    top = min(indents)
    preamble: list[str] = []
    items: list[list[str]] = []
    for line, match in zip(lines, matches):
        if match and len(match["indent"].expandtabs(4)) == top:
            items.append([match["text"]])
        elif items:
            items[-1].append(line)
        else:
            preamble.append(line)
    return _paragraphs(preamble) + [_clean(item) for item in items]


def _fit(text: str) -> list[str]:
    """``text`` in pieces of at most ``MAX_TEXT`` characters, split where the text breaks, never shortened."""
    if len(text) <= MAX_TEXT:
        return [text]
    for separator in ("\n\n", "\n", " "):
        cut = text.rfind(separator, 0, MAX_TEXT + 1)
        if cut > 0:
            return [text[:cut].strip(), *_fit(text[cut + len(separator):].strip())]
    return [text[:MAX_TEXT], *_fit(text[MAX_TEXT:])]


def split(statement: str, requirements: str = "", interface: str = "") -> list[dict]:
    """The review contract: ``[{id, text, source}]``, numbered R1, R2, ... in the order the module docstring gives."""
    whole = "\n".join(line.rstrip() for line in _lines(statement)).strip()
    pieces = [("statement", whole)] if whole else []
    pieces += [("requirements", text) for text in _blocks(requirements)]
    pieces += [("interface", text) for text in _blocks(interface) if not _NO_INTERFACE.match(text)]
    found: list[dict] = []
    seen: set[str] = set()
    for source, text in pieces:
        if text in seen:
            continue
        seen.add(text)
        found += [{"id": f"R{len(found) + index + 1}", "text": part, "source": source}
                  for index, part in enumerate(_fit(text))]
    return found


def render(requirements: list[dict]) -> str:
    """The contract as the text an arm reads: each requirement numbered by its id, separated by a blank line."""
    return "\n\n".join(f"{r['id']}. {r['text']}" for r in requirements)


def patch_tests(paths: list[str]) -> list[str]:
    """The test files among ``paths`` (pytest's default names, ``test_*.py`` and ``*_test.py``), sorted."""
    return sorted(p for p in set(paths) if PurePosixPath(p).suffix == ".py"
                  and (PurePosixPath(p).name.startswith("test_") or PurePosixPath(p).name.endswith("_test.py")))


def cite(requirements: list[dict], checks: list[str]) -> list[dict]:
    """The contract in the form G6's review takes (``{id, text, checks}``), each requirement citing ``checks``."""
    return [{"id": r["id"], "text": r["text"], "checks": list(checks)} for r in requirements]
