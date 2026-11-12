# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Scope and correspondence are bound (G6 0.3 design, section 15.3 check 5).

A ``ScopeBinding`` records what the statement covers, the exact observation it constrains and what it does not
cover, and binds the bytes all of that depends on: the statement, its definitions, its domain, assumptions and
bounds, the adapter, the rendering shown, and the standard (its digest and revision) whose confirmed examples it was
checked against. One changed byte in any of them, or another standard, breaks the binding, and the check names the
part. An unsupported case is always listed, never dropped.

Correspondence to code is a separate obligation: nothing here says the code does what the statement says.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from g6.core.ledger import canonical
from g6.harness.formalisation.adapters import ADAPTERS, ExampleObservation
from g6.harness.formalisation.checks import CheckOutcome
from g6.harness.formalisation.render import RENDERERS, Rendering, Unrenderable
from g6.harness.formalisation.statement import FormalStatement


@dataclass(frozen=True)
class ScopeBinding:
    statement_sha256: str
    definitions_sha256: str
    assumptions_sha256: str
    adapter: str
    rendering_sha256: str
    standard_sha256: str
    standard_revision: int
    covers: str
    not_covered: tuple[str, ...]

    def to_dict(self) -> dict:
        return {**asdict(self), "not_covered": list(self.not_covered)}

    def record(self) -> bytes:
        return canonical(self.to_dict()).encode("ascii")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.record()).hexdigest()


def _exclusions(statement: FormalStatement) -> tuple[str, ...]:
    from g6.harness.formalisation import render_smt

    return render_smt.exclusions(statement) if statement.language == "smt" else ()


def bind_scope(statement: FormalStatement, rendering: Rendering, standard, cases: Sequence[ExampleObservation]
               ) -> ScopeBinding:
    """The binding of this statement, as rendered, to this standard. Every example must be one of the standard's
    confirmed expectations; each one the adapter cannot bind is listed in ``not_covered`` with the reason."""
    held = {one.answer_sha256 for one in standard.expectations}
    not_covered = list(_exclusions(statement))
    adapter = ADAPTERS.get(statement.adapter)
    for case in cases:
        if case.answer_sha256 not in held:
            raise ValueError(f"the example {case.expectation_id} is not one of the standard's confirmed expectations")
        for label, outcome in (("confirmed", case.accepted), *(("rejected", one) for one in case.rejected)):
            why = (f"no adapter {statement.adapter!r}" if adapter is None else
                   adapter.bind(statement, case, outcome).unsupported)
            if why:
                not_covered.append(f"example {case.expectation_id}, {label} outcome {sorted(outcome)[0]}: {why}")
    seen = statement.observation
    what = {"returns": "the value it returns", "raises": "the error it raises",
            "effect": "its effect on an argument"}[seen.kind]
    covers = (f"{seen.entry_point}: {what}, for inputs "
              + ", ".join(f"{name} ({sort})" for name, sort in seen.inputs)
              + (f", under {len(statement.domain)} domain term(s) and {len(statement.assumptions)} assumption(s)"))
    return ScopeBinding(
        statement_sha256=statement.sha256, definitions_sha256=statement.definitions_sha256,
        assumptions_sha256=statement.assumptions_sha256, adapter=statement.adapter,
        rendering_sha256=rendering.sha256, standard_sha256=standard.sha256, standard_revision=standard.revision,
        covers=covers, not_covered=tuple(not_covered))


def check_scope(binding: ScopeBinding, statement: FormalStatement, rendering: Rendering, standard) -> CheckOutcome:
    """Check 5: every bound part still is what was bound; a failure names the part."""
    reasons = []
    for part, bound, now in (
            ("statement", binding.statement_sha256, statement.sha256),
            ("definitions", binding.definitions_sha256, statement.definitions_sha256),
            ("assumptions", binding.assumptions_sha256, statement.assumptions_sha256),
            ("adapter", binding.adapter, statement.adapter),
            ("rendering", binding.rendering_sha256, rendering.sha256),
            ("standard", binding.standard_sha256, standard.sha256),
            ("standard", binding.standard_revision, standard.revision)):
        if bound != now:
            reasons.append(f"{part}: bound {str(bound)[:16]}, now {str(now)[:16]}; the binding is for other bytes")
    if rendering.statement_sha256 != statement.sha256 or rendering.formal_text != statement.text:
        reasons.append("rendering: it is the rendering of another statement")
    else:
        try:
            made = RENDERERS[statement.fragment](statement) if statement.fragment in RENDERERS else None
        except Unrenderable:
            made = None
        if made != rendering:
            reasons.append("rendering: it is not what the renderer gives for this statement")
    reasons = list(dict.fromkeys(reasons))
    return CheckOutcome("scope", "failed" if reasons else "passed", tuple(reasons),
                        detail={"covers": binding.covers, "not_covered": list(binding.not_covered),
                                "binding_sha256": binding.sha256})


__all__ = ["ScopeBinding", "bind_scope", "check_scope"]