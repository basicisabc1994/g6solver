# SPDX-License-Identifier: Apache-2.0
"""source_scan.py -- flag source lines that quietly defeat a safety chain. An advisory review aid, not a gate.

Each rule is a regular expression matched line by line against the code, with the text of string literals and comments
blanked first, so a smell quoted in a docstring or a pattern table is not a finding. When the tokenizer cannot read
the text (a fragment, a syntax error) the raw lines are scanned and ``strings_blanked`` is False: a finding inside a
string is then possible. A finding asks for review; no finding means only that no rule matched.
"""
from __future__ import annotations

import io
import re
import tokenize
from dataclasses import dataclass, field

#: (rule, pattern, why it is a smell). Matched against each code line with its leading space removed.
RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("unused_guard_verdict", re.compile(r"^[\w.]+\.scan_\w+\([^=]*\)\s*$"),
     "a scan's verdict is computed and thrown away"),
    ("planner_calls_executor", re.compile(r"executor\.run\(\s*(?:plan|planner|\w*output)\b"),
     "planner output is handed straight to an executor"),
    ("ceiling_from_planner_output", re.compile(r"(?:max_\w+|ceiling\w*|policy\.\w+)\s*=\s*(?:plan|planner)\b"),
     "a ceiling is set from planner output"),
    # The library's KillSwitch keeps its flag in ``_tripped``; writing either spelling bypasses ``reset``.
    ("kill_switch_reset", re.compile(r"\._?tripped\s*=(?!=)|kill_switch\.reset\("),
     "a kill switch is reset, or its flag written directly"),
    ("floor_from_observed_value", re.compile(r"(?:floor\w*|required\w*)\s*=\s*(?:score|observed|measured)\w*"),
     "a floor is derived from the value it is meant to bound"),
)

_BLANKED = {tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}


@dataclass(frozen=True)
class StaticFinding:
    rule: str
    reason: str
    line_no: int
    line: str
    path: str = ""


@dataclass(frozen=True)
class SourceScan:
    """The findings in one source text, and how it was read. ``lines_scanned`` is 0 for an empty text."""

    findings: tuple[StaticFinding, ...]
    lines_scanned: int
    strings_blanked: bool
    path: str = ""
    advisory: bool = field(default=True, init=False)

    @property
    def flagged(self) -> bool:
        return bool(self.findings)


def _code_lines(lines: list[str], text: str) -> tuple[list[str], bool]:
    """``lines`` with string and comment text replaced by spaces, and whether that worked."""
    grid = [list(line) for line in lines]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in _BLANKED:
                continue
            (first, start), (last, end) = tok.start, tok.end
            for row in range(first, min(last, len(grid)) + 1):
                chars = grid[row - 1]
                for col in range(start if row == first else 0, min(end if row == last else len(chars), len(chars))):
                    chars[col] = " "
    except (tokenize.TokenError, SyntaxError):
        return list(lines), False
    return ["".join(chars) for chars in grid], True


def scan_source(text: str, path: str = "") -> SourceScan:
    """Every rule match in ``text``; each finding reports the original line. ``path`` only labels the findings."""
    if not isinstance(text, str):
        raise TypeError(f"text must be a string, got {type(text).__name__}")
    lines = [line.rstrip("\r\n") for line in io.StringIO(text).readlines()]
    code, blanked = _code_lines(lines, text)
    findings = []
    for number, line in enumerate(code, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        findings += [StaticFinding(rule, reason, number, lines[number - 1], path)
                     for rule, pattern, reason in RULES if pattern.search(stripped)]
    return SourceScan(tuple(findings), len(lines), blanked, path)


__all__ = ["RULES", "SourceScan", "StaticFinding", "scan_source"]
