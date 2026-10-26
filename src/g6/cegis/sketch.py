# SPDX-License-Identifier: Apache-2.0
"""A sketch: a Python function with holes. Pure: it builds source text and never executes it (audit 027 CG-002: the
archive executed it in process behind an AST denylist; here only a ``Runner`` executes, out of process)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from g6.cegis._child import validate_source
from g6.cegis.hole import HOLE_VAR_TPL, Hole, HoleKind, extract_holes

#: A control: one value per hole id.
Control = dict[int, Any]


def _literal(value: Any) -> str:
    return repr(value)  # bool, int and str only (checked by Hole.accepts): their repr is their source


def _default(hole: Hole) -> Any:
    if hole.kind == HoleKind.INT:
        return max(hole.lo, min(hole.hi, 0))
    if hole.kind == HoleKind.BOOL:
        return False
    return hole.choices[0]


@dataclass(frozen=True)
class Sketch:
    original_src: str
    modified_src: str
    holes: tuple[Hole, ...]
    fn_name: str = "f"

    @classmethod
    def from_src(cls, src: str, lo: int = -32, hi: int = 32, *, fn_name: str = "f") -> Sketch:
        if not fn_name.isidentifier():
            raise ValueError(f"fn_name {fn_name!r} is not a Python name")
        modified, holes = extract_holes(src, lo=lo, hi=hi)
        why = validate_source(modified)  # the child checks every filled-in candidate again before it runs
        if why:
            raise ValueError(f"the sketch is refused: {why}")
        return cls(original_src=src, modified_src=modified, holes=holes, fn_name=fn_name)

    def check(self, control: Mapping[int, Any]) -> Control:
        """``control`` as a full, valid control: every value one its hole may take (refused otherwise)."""
        known = {h.id: h for h in self.holes}
        extra = sorted(set(control) - set(known))
        if extra:
            raise ValueError(f"the control names holes the sketch does not have: {extra}")
        out: Control = {}
        for hole in self.holes:
            value = control.get(hole.id, _default(hole))
            if not hole.accepts(value):
                raise ValueError(f"hole {hole.id} ({hole.kind.value}) cannot take {value!r}")
            out[hole.id] = value
        return out

    def instantiate(self, control: Mapping[int, Any]) -> str:
        """The concrete source with every hole filled (the control is checked first)."""
        src = self.modified_src
        for hole_id, value in self.check(control).items():
            src = src.replace(HOLE_VAR_TPL.format(i=hole_id), _literal(value))
        return src

    def as_wire(self) -> dict[str, Any]:
        """What a runner's child needs: the source with hole names, the holes and the function's name."""
        return {"source": self.modified_src, "holes": [h.as_dict() for h in self.holes], "fn_name": self.fn_name}


__all__ = ["Control", "Sketch"]
