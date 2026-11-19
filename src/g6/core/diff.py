# SPDX-License-Identifier: Apache-2.0
"""Git unified diff structure, shared by evidence and patch readers.

Only LF separates records. Hunk counters keep header-shaped source out of
the header grammar. Invalid/truncated records are explicit, never evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from g6.core.git_name import _UNESCAPED

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    old_name: str = ""
    new_name: str = ""
    text: str = ""
    hunks: list[Hunk] = field(default_factory=list)
    valid: bool = True

    @property
    def names(self) -> set[str]:
        return {self.old_name, self.new_name} - {""}

    def sides(self) -> tuple[str, str]:
        """Hunk fragments, for conservative AST scanning when full files are unavailable."""
        return tuple("".join(line[1:] for h in self.hunks for line in h.lines if line[:1] in marks)
                     for marks in ((" ", "-"), (" ", "+")))


def hunk_header(line: str) -> Hunk | None:
    match = _HUNK.fullmatch(line.rstrip("\r\n"))
    if match is None:
        return None
    a, n, b, m = match.groups()
    return Hunk(int(a), int(n) if n is not None else 1, int(b), int(m) if m is not None else 1)


def file_name(raw: str, *, prefix: bool = True) -> str:
    """Read git's C escapes as bytes (including octal UTF-8), or a bare path."""
    raw = raw.rstrip("\r\n").removesuffix("\t")
    if raw.startswith('"'):
        if len(raw) < 2 or not raw.endswith('"'):
            raise ValueError("unclosed git path")
        body, data, at = raw[1:-1], bytearray(), 0
        while at < len(body):
            char = body[at]
            if char == '"':
                raise ValueError("bare quote in git path")
            if char != "\\":
                data.extend(char.encode("utf-8", "surrogateescape"))
                at += 1
                continue
            tail = body[at + 1:at + 4]
            if body[at + 1:at + 2] in _UNESCAPED:
                data.extend(_UNESCAPED[body[at + 1]].encode())
                at += 2
            elif len(tail) == 3 and tail[0] in "0123" and all(c in "01234567" for c in tail):
                data.append(int(tail, 8))
                at += 4
            else:
                raise ValueError("invalid git escape")
        raw = data.decode("utf-8", "surrogateescape")
    elif '"' in raw or any(ord(c) < 32 for c in raw):
        raise ValueError("unquoted control in git path")
    return "" if raw == "/dev/null" else raw[2:] if prefix and raw.startswith(("a/", "b/")) else raw


def _git_names(raw: str) -> tuple[str, str] | None:
    """Read a diff --git pair; ambiguous bare space paths await ---/+++ headers."""
    raw = raw.rstrip("\r\n")
    if raw.startswith('"'):
        at = 1
        while at < len(raw):
            if raw[at] == "\\":
                at += 2
            elif raw[at] == '"':
                break
            else:
                at += 1
        if at >= len(raw) or raw[at + 1:at + 2] != " ":
            return None
        left, right = raw[:at + 1], raw[at + 2:]
    elif ' "b/' in raw:
        left, right = raw.split(' "b/', 1)
        right = '"b/' + right
    elif raw.count(" b/") == 1:
        left, right = raw.split(" b/", 1)
        right = "b/" + right
    else:
        return None
    return file_name(left), file_name(right)


def parse_diff(text: str) -> list[FileDiff]:
    out: list[FileDiff] = []
    old = new = 0
    rows = text.split("\n")
    for line in [row + "\n" for row in rows[:-1]] + ([rows[-1]] if rows[-1] else []):
        current = out[-1] if out else None
        if old or new:
            if line[:1] in (" ", "-", "+"):
                old -= line[:1] in (" ", "-")
                new -= line[:1] in (" ", "+")
                current.hunks[-1].lines.append(line)
                current.text += line
                if old < 0 or new < 0:
                    current.valid = False
                    old = new = 0
                continue
            if line.startswith("\\ No newline at end of file"):
                current.text += line
                continue
            current.valid = False
            old = new = 0
        if (current is None or line.startswith("diff --git ")
                or (line.startswith("--- ") and (current.hunks or "--- " in current.text))):
            current = FileDiff()
            out.append(current)
        current.text += line
        try:
            if line.startswith("diff --git "):
                names = _git_names(line[len("diff --git "):])
                if names is not None:
                    current.old_name, current.new_name = names
            elif line.startswith("--- "):
                current.old_name = file_name(line[4:])
            elif line.startswith("+++ "):
                current.new_name = file_name(line[4:])
            elif line.startswith(("rename from ", "copy from ")):
                current.old_name = file_name(line.split(" ", 2)[2], prefix=False)
            elif line.startswith(("rename to ", "copy to ")):
                current.new_name = file_name(line.split(" ", 2)[2], prefix=False)
            elif line.startswith("@@"):
                hunk = hunk_header(line)
                if hunk is None:
                    current.valid = False
                else:
                    current.hunks.append(hunk)
                    old, new = hunk.old_count, hunk.new_count
            elif current.hunks and line[:1] in ("+", "-", " "):
                current.valid = False
        except ValueError:
            current.valid = False
    if (old or new) and out:
        out[-1].valid = False
    for part in out:
        if part.hunks and not part.names:
            part.valid = False
    return out
