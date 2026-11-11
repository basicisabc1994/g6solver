# SPDX-License-Identifier: Apache-2.0
"""Heuristics as a method kind (0.3 design, 14.2): a strategy artefact of ``g6.learning`` as a ``MethodVariant`` of
kind ``heuristic``, beside the evidence plan and never inside it.

A heuristic variant names the bytes of its strategy (``content_digest``), so its definition digest, and with it every
trial that can credit it, is bound to what is applied. The selector drops heuristic variants before it ranks anything:
no obligation is met or removed by one.
"""
from __future__ import annotations

from typing import Any

from .metacognition import HEURISTIC, STRATEGY_ENGINE, STRATEGY_METHOD, STRATEGY_OP, MethodVariant
from .snapshot import number, publish, text


def heuristic_variant(artefact: Any, *, id: str, estimated_cost: float) -> MethodVariant:
    """The variant for one strategy artefact (``g6.learning.strategy.StrategyArtefact``): kind ``heuristic``, method
    ``strategy``, applied by ``g6.learning``'s ``apply``, its representation the artefact's mapping and scope, and its
    content digest the SHA-256 of the artefact's own bytes, computed here from those bytes."""
    import hashlib

    data = artefact.to_bytes()
    if type(data) is not bytes:
        raise ValueError("a strategy artefact gives its bytes")
    representation = f"{text(artefact.mapping, where='artefact.mapping')}:{text(artefact.scope, where='artefact.scope')}"
    return publish(MethodVariant(id=text(id, where="id"), method=STRATEGY_METHOD, representation=representation,
                                 engine=STRATEGY_ENGINE, op=STRATEGY_OP,
                                 estimated_cost=number(estimated_cost, where="estimated_cost"), kind=HEURISTIC,
                                 content_digest=hashlib.sha256(data).hexdigest()),
                   where="heuristic_variant")


__all__ = ["heuristic_variant"]
