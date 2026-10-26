# SPDX-License-Identifier: Apache-2.0
"""Cheap deterministic signs of a bad output: empty, a refusal, or a phrase looping.

A flag is evidence the output is unusable; the absence of flags is not evidence that it is good, so no caller
counts "no flags" as a passing check.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_STEPS = frozenset({8, 9, 10})
_REFUSALS = (
    re.compile(r"I('m| am) sorry.{0,20}(cannot|can't|unable)", re.IGNORECASE),
    re.compile(r"I('m| am) not able to", re.IGNORECASE),
    re.compile(r"as an AI.{0,30}(cannot|can't)", re.IGNORECASE),
)


@dataclass(frozen=True)
class RedFlag:
    name: str
    detail: str


def is_refusal(text: str) -> bool:
    return any(p.search(text) for p in _REFUSALS)


def _is_python(text: str) -> bool:
    try:
        compile(text, "<answer>", "exec")   # parses only; nothing runs
    except Exception:  # noqa: BLE001 - SyntaxError, ValueError (NUL bytes), RecursionError on deep nesting
        return False
    return True


def _repeated_phrase(words: list[str], threshold: int) -> str | None:
    for size in (3, 4, 5):
        seen: dict[str, int] = {}
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            seen[phrase] = seen.get(phrase, 0) + 1
            if seen[phrase] >= threshold:
                return phrase
    return None


def red_flags(text: str | None, *, step_number: int = 0) -> list[RedFlag]:
    """``EMPTY_OUTPUT``, ``REFUSAL`` and ``REPETITION`` flags for ``text``.

    A phrase of 3-5 words seen 3 times is a loop; on a code step (8-10) the bar is 6, and text that parses as
    Python is not checked for repetition at all. The archive's ``WRONG_LANGUAGE`` flag (ASCII ratio < 0.5) is not
    ported: it rejected answers written in non-Latin scripts. Its length-baseline flag needed state kept across
    runs and is not ported either.
    """
    if text is None or not text.strip():
        return [RedFlag("EMPTY_OUTPUT", "output is empty")]
    flags: list[RedFlag] = []
    code_step = step_number in _CODE_STEPS
    words = text.split()
    if len(words) >= 6 and not (code_step and _is_python(text)):
        phrase = _repeated_phrase(words, 6 if code_step else 3)
        if phrase is not None:
            flags.append(RedFlag("REPETITION", f"phrase repeated: {phrase[:50]!r}"))
    if is_refusal(text):
        flags.append(RedFlag("REFUSAL", "matches a refusal pattern"))
    return flags


__all__ = ["RedFlag", "is_refusal", "red_flags"]
