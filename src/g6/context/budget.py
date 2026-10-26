# SPDX-License-Identifier: Apache-2.0
"""The token-budget floor: ``select_within_budget`` for repository handles and ``prune_packet`` for a whole packet.

``select_within_budget`` is the single truncation point for repository handles and runs after any re-rank, so a
re-ranker can change which handles come first but never how many tokens they take. ``prune_packet`` drops items
from the end of each section in ``PRUNE_ORDER`` until the packet fits. It never drops the task or the output
contract (audit 032, CTX-012): a packet whose protected sections alone exceed the budget is left over budget, and
``score_packet`` blocks it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from g6.context.packet import ContextItem, ContextPacket, RepositoryHandle, item_tokens, packet_tokens

#: Sections pruned first to last. ``task`` and ``output_contract`` are never pruned.
PRUNE_ORDER: tuple[str, ...] = ("exemplar", "memory", "repository", "tool", "component", "verification", "authority")
PROTECTED_SECTIONS: tuple[str, ...] = ("task", "output_contract")
#: Share of the packet budget that repository handles may take before the whole packet is pruned.
REPOSITORY_SHARE = 0.24


_BudgetItem = TypeVar("_BudgetItem", RepositoryHandle, ContextItem)


def select_within_budget(handles: Iterable[_BudgetItem], budget: int) -> list[_BudgetItem]:
    """The handles, in the given order, while their running ``token_estimate`` total stays within ``budget``.

    Stops at the first handle that would exceed it (checked before adding), so the result never exceeds ``budget``;
    a handle larger than the whole budget is never included.
    """
    kept: list[_BudgetItem] = []
    used = 0
    for handle in handles:
        cost = item_tokens(handle) if isinstance(handle, ContextItem) else int(handle.token_estimate or 0)
        if used + cost > budget:
            break
        kept.append(handle)
        used += cost
    return kept


def prune_packet(packet: ContextPacket) -> ContextPacket:
    """``packet`` cut to ``budget_tokens`` by dropping the last items of each section in ``PRUNE_ORDER``.

    ``repository_handles`` loses exactly the handles whose repository items were dropped, so a pruned handle cannot
    be surfaced beside the packet. ``metadata["context_truncated"]`` and ``metadata["pruned"]`` record the cut.
    """
    used = packet_tokens(packet)
    if used <= packet.budget_tokens:
        return packet.model_copy(update={"used_tokens": used})
    sections = {name: packet.section_items(name) for name in PRUNE_ORDER}
    pruned: dict[str, int] = {}
    dropped_handles: set[tuple] = set()
    for name in PRUNE_ORDER:
        items = sections[name]
        while items and used > packet.budget_tokens:
            removed = items.pop()
            used -= item_tokens(removed)
            pruned[name] = pruned.get(name, 0) + 1
            handle = removed.metadata.get("handle") if name == "repository" else None
            if isinstance(handle, dict):
                dropped_handles.add(RepositoryHandle.model_validate(handle).identity)
    handles = [h for h in packet.repository_handles if h.identity not in dropped_handles]
    return packet.model_copy(update={**sections, "used_tokens": used, "repository_handles": handles,
                                     "metadata": {**packet.metadata, "context_truncated": True, "pruned": pruned}})


__all__ = ["PROTECTED_SECTIONS", "PRUNE_ORDER", "REPOSITORY_SHARE", "prune_packet", "select_within_budget"]
