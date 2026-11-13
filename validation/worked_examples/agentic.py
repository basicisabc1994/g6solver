# SPDX-License-Identifier: Apache-2.0
"""A model decision where the loop had fixed logic, on G6's ``SolverPlanner`` pattern.

The kernel the owner asked for (``g6.agentic``, design 800a2e8) was never built. This is the small part the loop needs. The model
picks from a fixed menu and gives its reason. With no model, or a reply that fails or is off the menu, the fallback decides, and
the ``Decision`` says who decided. Nothing here touches evidence, the sets, the budget or an approval.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from g6.core.llm import LLMCaller
from g6.solver.agentic_runtime import Decision, SolverDecisionError, SolverPlanner, _rationale

FORM = ' Return only JSON: {"choice": "one of the menu", "rationale": "..."}.'


def choice(llm: LLMCaller | None, name: str, system: str, payload: Mapping[str, Any], menu: Sequence[str],
           fallback: str) -> tuple[str, Decision]:
    """One of ``menu``, and who chose it."""
    planner = SolverPlanner(llm)

    def by_model() -> tuple[str, str, str]:
        reply = planner._ask(system + FORM, {**payload, "menu": list(menu)}, 600)
        value = str(reply.get("choice") or "").strip()
        if value not in menu:
            raise SolverDecisionError(f"choice must be one of {list(menu)}, got {value!r}")
        return value, value, _rationale(reply)

    return planner._decide(name, by_model, lambda: (fallback, f"fallback:{fallback}"))
