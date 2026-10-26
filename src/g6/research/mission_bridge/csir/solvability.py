# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A solvability belief: stated, never a gate (founder, 2026-10-14).

The engine highlights whether a problem is believed solvable, on three separate axes that never stand in for one another:

- **existence**: whether a solution is believed to exist (``believed_solvable``, ``believed_unsolvable``, ``open``,
  ``unknown``), always with the basis for the belief;
- **findability**: whether G6 can find one with what it has (CSIR's ``CanSolve``, tex:940-965), separate from existence:
  believing a solution exists says nothing about whether G6 can find it;
- **optimality**: ``not_claimed`` unless a proof is named; finding a solution says nothing about whether it is the best.

A belief never stops an attempt: an open problem, or one believed unsolvable, may still be worked on. It never changes a
mission's completion state either (the certificate carries it as a statement and decides nothing from it).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EXISTENCE = ("believed_solvable", "believed_unsolvable", "open", "unknown")
FINDABILITY = ("yes", "no", "unknown")
OPTIMALITY = ("not_claimed", "proved_optimal")


@dataclass(frozen=True)
class Solvability:
    existence: str = "unknown"
    basis: str = ""
    findability: str = "unknown"
    findability_basis: str = ""
    optimality: str = "not_claimed"
    optimality_proof: str = ""

    def __post_init__(self) -> None:
        if self.existence not in EXISTENCE:
            raise ValueError(f"existence must be one of {EXISTENCE}, got {self.existence!r}")
        if self.findability not in FINDABILITY:
            raise ValueError(f"findability must be one of {FINDABILITY}, got {self.findability!r}")
        if self.optimality not in OPTIMALITY:
            raise ValueError(f"optimality must be one of {OPTIMALITY}, got {self.optimality!r}")
        if self.existence != "unknown" and not self.basis.strip():
            raise ValueError("a belief about existence states its basis")
        if self.findability != "unknown" and not self.findability_basis.strip():
            raise ValueError("a statement about findability states its basis")
        if self.optimality == "proved_optimal" and not self.optimality_proof.strip():
            raise ValueError("optimality is claimed only with a named proof")
        if self.findability == "yes" and self.existence == "believed_unsolvable":
            raise ValueError("G6 finding a solution contradicts believing that none exists")

    def permits_attempt(self) -> bool:
        """Always: a belief never stops anyone working on a problem, open or not."""
        return True

    def describe(self) -> str:
        # COPY: solvability sentence shown with a mission's result (founder 2026-10-14).
        words = {"believed_solvable": "A solution is believed to exist", "believed_unsolvable": "No solution is believed to exist",
                 "open": "Whether a solution exists is an open question", "unknown": "Whether a solution exists is not known"}
        text = words[self.existence] + (f" ({self.basis})" if self.basis else "") + "."
        if self.existence == "believed_solvable":
            text += " That belief does not mean G6 can find a solution, or that a solution it finds is the best one."
        if self.findability == "yes":
            text += f" G6 can find one ({self.findability_basis})."
        elif self.findability == "no":
            text += f" G6 cannot find one with what it has ({self.findability_basis})."
        if self.optimality == "proved_optimal":
            text += f" The solution is reported optimal ({self.optimality_proof})."
        return text

    def as_dict(self) -> dict[str, Any]:
        return {"existence": self.existence, "basis": self.basis, "findability": self.findability,
                "findability_basis": self.findability_basis, "optimality": self.optimality,
                "optimality_proof": self.optimality_proof, "permits_attempt": True}
