# SPDX-License-Identifier: Apache-2.0
"""Holes in a sketch (ported from the archive's ``cegis.hole``, unchanged in behaviour).

``??`` is an integer in ``[lo, hi]``; ``??b`` a boolean; ``??{"a","b"}`` one of a finite set of strings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

HOLE_VAR_TPL = "_CEGIS_H{i}_"
# ??{"a","b"} first (most specific), then ??b (not followed by a word character), then ??
_HOLE_PATTERN = re.compile(r'\?\?\{[^}]*\}|\?\?b(?!\w)|\?\?')


class HoleKind(StrEnum):
    INT = "int"
    BOOL = "bool"
    STR_ENUM = "str_enum"


@dataclass(frozen=True)
class Hole:
    id: int
    kind: HoleKind = HoleKind.INT
    lo: int = -32
    hi: int = 32
    choices: tuple[Any, ...] = ()

    @property
    def all_values(self) -> list[Any]:
        if self.kind == HoleKind.INT:
            return list(range(self.lo, self.hi + 1))
        return list(self.choices)

    def accepts(self, value: Any) -> bool:
        """Whether ``value`` is one this hole may take (a control from anywhere is checked before it is used)."""
        if self.kind == HoleKind.INT:
            return type(value) is int and self.lo <= value <= self.hi
        if self.kind == HoleKind.BOOL:
            return type(value) is bool
        return type(value) is str and value in self.choices

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value, "lo": self.lo, "hi": self.hi, "choices": list(self.choices)}


def extract_holes(src: str, lo: int = -32, hi: int = 32) -> tuple[str, tuple[Hole, ...]]:
    """Replace each hole token, left to right, with ``_CEGIS_H0_``, ``_CEGIS_H1_``, ...; return the source and holes."""
    if lo > hi:
        raise ValueError(f"an integer hole needs lo <= hi, got [{lo}, {hi}]")
    holes: list[Hole] = []
    modified = src
    i = 0
    while (m := _HOLE_PATTERN.search(modified)) is not None:
        token = m.group()
        if token == "??b":
            hole = Hole(id=i, kind=HoleKind.BOOL, choices=(True, False))
        elif token.startswith("??{"):
            hole = Hole(id=i, kind=HoleKind.STR_ENUM, choices=tuple(re.findall(r'"([^"]*)"', token[3:-1])))
            if not hole.choices:
                raise ValueError(f"hole {i} ({token}) lists no choice")
        else:
            hole = Hole(id=i, kind=HoleKind.INT, lo=lo, hi=hi)
        holes.append(hole)
        modified = modified[:m.start()] + HOLE_VAR_TPL.format(i=i) + modified[m.end():]
        i += 1
    return modified, tuple(holes)


__all__ = ["HOLE_VAR_TPL", "Hole", "HoleKind", "extract_holes"]
