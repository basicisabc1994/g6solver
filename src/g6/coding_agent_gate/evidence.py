# SPDX-License-Identifier: Apache-2.0
"""Typed evidence with provenance, and a structured verifier result (audit 004 CAG-001/002/007).

An item counts only when it has the structure its kind needs; anything else is rejected with the reason, so a
placeholder (``none``, ``TODO``, ``not run``, "tests pass" with no exit code, an empty log) is never evidence.
``observed_by`` says who produced the item: ``"g6"`` only when a G6 component ran the test or read the file itself,
``"caller"`` for everything an agent reports. The gate cannot tell a fabricated item from a real one; provenance is
how it says which items it took on trust.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["file", "test", "log", "diff", "review"]
KINDS: tuple[str, ...] = ("file", "test", "log", "diff", "review")
_SOURCES = ("g6", "caller")

#: Values that say nothing. A ref, log or reviewer that normalises to one of these is a placeholder.
_PLACEHOLDERS = frozenset({
    "", "-", "--", "?", "none", "n/a", "na", "nil", "null", "todo", "tbd", "fixme", "xxx", "placeholder",
    "not run", "notrun", "not applicable", "skip", "skipped", "pending", "unknown", "pass", "passed", "ok", "done",
    "yes", "works", "it works", "tests pass", "tests passed", "all tests pass", "lgtm", "trust me", "see above",
})
_PATH = re.compile(r"^(?:[A-Za-z]:)?[\w.\-/\\~@+]+(?::\d+(?:-\d+)?)?$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.MULTILINE)


def _norm(text: object) -> str:
    return " ".join(str(text or "").lower().replace("_", " ").split()).strip(" .!:;,'\"`")


def is_placeholder(text: object) -> bool:
    """True when ``text`` says nothing (empty, a sentinel such as ``none`` / ``not run``, or a bare "tests pass")."""
    return _norm(text) in _PLACEHOLDERS


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class Evidence:
    """One evidence item. ``ref`` is the path, the test command or id, the log name, or what was reviewed."""

    kind: Kind
    ref: str = ""
    output: str = ""              # log text or unified-diff text (never echoed back, only its length)
    exit_code: int | None = None  # a test run's exit code
    reviewer: str = ""            # who reviewed, for a review
    observed_by: Literal["g6", "caller"] = "caller"

    def problem(self, *, author: str = "") -> str:
        """Why this item does not count, or ``""`` when it does."""
        if self.kind not in KINDS:
            return f"unknown kind {self.kind!r}; use one of {', '.join(KINDS)}"
        if self.observed_by not in _SOURCES:
            return f"observed_by must be 'g6' or 'caller', not {self.observed_by!r}"
        ref = str(self.ref or "").strip()
        if self.kind == "file":
            return "" if _PATH.match(ref) and not is_placeholder(ref) else "a file item needs the path that was inspected"
        if self.kind == "test":
            if is_placeholder(ref):
                return "a test item needs the command or test id that ran"
            return "" if _is_int(self.exit_code) else "a test item needs its exit code; 'tests pass' is a claim, not a run"
        if self.kind == "log":
            return "" if not is_placeholder(self.output) else "a log item needs its non-empty text"
        if self.kind == "diff":
            return "" if _HUNK.search(self.output or "") else "a diff item needs unified-diff text with an @@ hunk"
        if is_placeholder(self.reviewer) or is_placeholder(ref):
            return "a review item needs who reviewed (reviewer) and what was reviewed (ref)"
        who, by = set(_norm(self.reviewer).split()), set(_norm(author).split())
        if by and (who <= by or by <= who):   # "Claude" reviewing work by "Claude Opus" is a self-review
            return "the reviewer is the author; a self-review does not count"
        return ""

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"kind": self.kind, "ref": self.ref, "observed_by": self.observed_by}
        if self.exit_code is not None:
            out["exit_code"] = self.exit_code
        if self.reviewer:
            out["reviewer"] = self.reviewer
        if self.output:
            out["output_chars"] = len(self.output)
        return out


_PASS_WORDS = frozenset({"pass", "passed"})
_FAIL_WORDS = frozenset({"fail", "failed", "failure", "error", "errored"})
_NOT_RUN_WORDS = frozenset({"", "not run", "notrun", "skipped", "none", "n/a"})
_EXIT = re.compile(r"^exit(?: code| status)?\s*[:=]?\s*(-?\d+)$")


@dataclass(frozen=True)
class VerifierResult:
    """A verifier's outcome. A non-zero exit code is a failure whatever ``status`` says."""

    status: Literal["pass", "fail", "not_run"] = "not_run"
    exit_code: int | None = None
    source: str = ""
    observed_by: Literal["g6", "caller"] = "caller"
    unrecognised: str = ""        # the raw text, when parsed from a string the gate could not read

    def __post_init__(self) -> None:
        if self.status not in ("pass", "fail", "not_run"):
            raise ValueError(f"verifier status must be pass, fail or not_run, not {self.status!r}")
        if self.exit_code is not None and not _is_int(self.exit_code):
            raise ValueError("verifier exit_code must be an int or None")
        if self.observed_by not in _SOURCES:
            raise ValueError(f"observed_by must be 'g6' or 'caller', not {self.observed_by!r}")

    @property
    def effective_status(self) -> str:
        if self.status == "fail" or (self.exit_code is not None and self.exit_code != 0):
            return "fail"
        return "pass" if self.status == "pass" else "not_run"

    @classmethod
    def parse(cls, value: VerifierResult | str | None) -> VerifierResult:
        """Read a verifier result. Only exact words (``pass`` / ``fail``) and an exit code (``exit code 1``) are
        read; any other string is ``not_run`` with the text kept in ``unrecognised``: unknown is unknown."""
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise TypeError("verifier must be a VerifierResult, a string or None")
        text = _norm(value)
        if text in _PASS_WORDS:
            return cls("pass")
        if text in _FAIL_WORDS:
            return cls("fail")
        if text in _NOT_RUN_WORDS:
            return cls()
        found = _EXIT.match(text)
        if found:
            code = int(found.group(1))
            return cls("pass" if code == 0 else "fail", exit_code=code)
        return cls(unrecognised=value.strip()[:200])

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "effective_status": self.effective_status, "exit_code": self.exit_code,
                "source": self.source, "observed_by": self.observed_by, "unrecognised": self.unrecognised}


__all__ = ["KINDS", "Evidence", "Kind", "VerifierResult", "is_placeholder"]
