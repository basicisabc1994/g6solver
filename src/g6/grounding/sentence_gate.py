# SPDX-License-Identifier: Apache-2.0
"""The original grounding sentence boundaries, retaining source offsets."""
from __future__ import annotations

import re


def sentence_at(text: str, start: int | None, end: int | None) -> tuple[int, int, str] | None:
    """Find the single source sentence enclosing an interval, without expanding its authority.

    Like the original reader, split only after .!? followed by whitespace.
    Line wrapping and semicolons do not split a sentence; a final fragment remains
    a sentence. Strip only surrounding whitespace while keeping source coordinates.
    """
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
        return None
    left = 0
    for right in [m.start() for m in re.finditer(r"(?<=[.!?])\s+", text)] + [len(text)]:
        part = text[left:right]
        sentence_start = left + len(part) - len(part.lstrip())
        sentence_end = right - (len(part) - len(part.rstrip()))
        if sentence_start <= start < end <= sentence_end:
            return sentence_start, sentence_end, text[sentence_start:sentence_end]
        left = right
    return None
