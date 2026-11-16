# SPDX-License-Identifier: Apache-2.0
"""Holes in a sketch (ported from the archive's ``cegis.hole``, unchanged in behaviour for the first three kinds).

``??`` is an integer in ``[lo, hi]``; ``??b`` a boolean; ``??{"a","b"}`` one of a finite set of strings; ``??f`` a
floating-point number in ``[lo, hi]``, which has no candidate grid of its own: :func:`float_grid` refuses with the limit
cegis declares, ``G6_E_CEGIS_HOLE_UNSUPPORTED``. ``float_grid`` is the part the agentic override kernel may substitute
(``g6.cegis.__g6_agentic_parts__``): in its confined child a model proposes the grid, which :func:`valid_float_grid`
checks before it is used. cegis's own check and verifier still decide whether a candidate fits.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

HOLE_VAR_TPL = "_CEGIS_H{i}_"
#: The code of the one limit a hole kind gives (``g6.cegis.__g6_limits__``).
HOLE_UNSUPPORTED = "G6_E_CEGIS_HOLE_UNSUPPORTED"
#: The most candidates one float hole's grid may hold.
MAX_FLOAT_GRID = 64
# ??{"a","b"} first (most specific), then ??b and ??f (not followed by a word character), then ??
_HOLE_PATTERN = re.compile(r'\?\?\{[^}]*\}|\?\?b(?!\w)|\?\?f(?!\w)|\?\?')


class HoleKind(StrEnum):
    INT = "int"
    BOOL = "bool"
    STR_ENUM = "str_enum"
    FLOAT = "float"


class HoleUnsupported(ValueError):
    """A hole cegis has no candidates for (a ``??f`` with no grid); its message starts with ``[G6_E_CEGIS_HOLE_UNSUPPORTED]``."""


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
        if self.kind == HoleKind.FLOAT:
            return type(value) is float and math.isfinite(value) and value in self.choices
        return type(value) is str and value in self.choices

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value, "lo": self.lo, "hi": self.hi, "choices": list(self.choices)}


def float_grid(hole: int, lo: int, hi: int, sketch: str) -> tuple[float, ...]:
    """The candidate values of float hole number ``hole`` of ``sketch``, each a finite float in ``[lo, hi]``.

    cegis has no grid for a floating-point hole, so it refuses here. This is the part the agentic override kernel may
    substitute for one call, in its confined child, where a model proposes the grid and :func:`valid_float_grid`
    checks it before cegis uses it."""
    raise HoleUnsupported(f"[{HOLE_UNSUPPORTED}] hole {hole} is a floating-point hole (??f) and cegis has no candidate "
                          f"grid for it in [{lo}, {hi}]; use an integer, boolean or string hole, or run a confined "
                          "model proposal under the provider's consent (g6_agentic_run), keeping the original answer beside it")


def valid_float_grid(value: Any, hole: int, lo: int, hi: int, sketch: str) -> str:
    """"" when ``value`` is a grid :func:`float_grid` may return for these arguments, else why not: 1 to
    :data:`MAX_FLOAT_GRID` distinct finite floats, each in ``[lo, hi]``."""
    if not isinstance(value, tuple | list) or not value:
        return "the grid must hold at least one value"
    if len(value) > MAX_FLOAT_GRID:
        return f"the grid may hold at most {MAX_FLOAT_GRID} values"
    for item in value:
        if type(item) is not float:
            return f"{item!r} is not a float"
        if not math.isfinite(item):
            return f"{item!r} is not a finite float"
        if not lo <= item <= hi:
            return f"{item!r} is outside the hole's bounds [{lo}, {hi}]"
    if len(set(value)) != len(value):
        return "the grid names a value twice"
    return ""


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
        elif token == "??f":
            grid = tuple(float_grid(i, lo, hi, src))   # the module's own name, looked up at each call
            why = valid_float_grid(grid, i, lo, hi, src)
            if why:
                raise HoleUnsupported(f"[{HOLE_UNSUPPORTED}] hole {i} (??f): {why}")
            hole = Hole(id=i, kind=HoleKind.FLOAT, lo=lo, hi=hi, choices=grid)
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


__all__ = ["HOLE_UNSUPPORTED", "HOLE_VAR_TPL", "MAX_FLOAT_GRID", "Hole", "HoleKind", "HoleUnsupported",
           "extract_holes", "float_grid", "valid_float_grid"]
