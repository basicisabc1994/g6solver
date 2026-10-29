# SPDX-License-Identifier: Apache-2.0
"""Advisory simplifications, each with explicit preservation and performance obligations."""
from __future__ import annotations

from dataclasses import dataclass

from g6.complexity.ir import CIR


@dataclass(frozen=True)
class Simplification:
    target: str
    proposal: str
    required_checks: tuple[str, ...]
    status: str = "proposal_only"


def propose_simplifications(cir: CIR) -> tuple[Simplification, ...]:
    normative = tuple(sorted(o.id for o in cir.intent.obligations if o.normative))
    return tuple(
        Simplification(s.id, "Consider a derived read-only view or a narrower single owner",
                       (*normative, "differential-preservation", "performance-regression"))
        for s in cir.state if s.mutable and s.derivation and s.essentiality == "accidental_avoidable"
    )
