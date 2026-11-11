# SPDX-License-Identifier: Apache-2.0
"""Heuristics as a method kind of ``g6.cognitive_bridge`` (0.3 design, 14.2), from the side that owns the strategy:
the adapter that reads a strategy artefact.

A heuristic is a ``MethodVariant`` of kind ``heuristic`` whose content digest is the SHA-256 of its strategy
artefact's bytes. It sits beside the evidence plan and never inside it: the bridge's selector drops heuristic variants
before it ranks anything. This module gives the bridge's records what they need as plain data, and reads the bridge's
records by their fields; it imports nothing from the bridge, so the bridge's own boundary rules stay the bridge's.

- ``variant_fields``: the fields of the variant for one artefact: ``MethodVariant(**variant_fields(...))``.
"""
from __future__ import annotations

import hashlib
from typing import Any

from g6.learning.strategy import StrategyArtefact

#: What the bridge's ``MethodVariant`` asks of a heuristic (``g6.cognitive_bridge.metacognition``; a test keeps the
#: two equal).
HEURISTIC = "heuristic"
STRATEGY_METHOD, STRATEGY_ENGINE, STRATEGY_OP = "strategy", "g6.learning", "apply"


def variant_fields(artefact: StrategyArtefact, *, id: str, estimated_cost: float) -> dict[str, Any]:
    """The fields of the heuristic variant for ``artefact``: kind ``heuristic``, method ``strategy``, applied by
    ``g6.learning``'s ``apply``, its representation the artefact's mapping and scope, and its content digest the
    SHA-256 of the artefact's own bytes."""
    return {"id": id, "method": STRATEGY_METHOD, "representation": f"{artefact.mapping}:{artefact.scope}",
            "engine": STRATEGY_ENGINE, "op": STRATEGY_OP, "estimated_cost": estimated_cost, "kind": HEURISTIC,
            "content_digest": hashlib.sha256(artefact.to_bytes()).hexdigest()}


__all__ = ["HEURISTIC", "STRATEGY_ENGINE", "STRATEGY_METHOD", "STRATEGY_OP", "variant_fields"]
