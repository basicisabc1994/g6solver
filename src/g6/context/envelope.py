# SPDX-License-Identifier: Apache-2.0
"""``score_packet``: the packet's envelope, recomputed from its content (audit 032, CTX-001/004/006/012).

A packet's own ``completion_state``, ``warning_card``, ``evidence`` and ``used_tokens`` are never read: tokens are
recounted from the item text, conflicts come from the merge losers recorded at assembly plus any duplicate keys,
staleness from each item's ``freshness_class``. The best state is ``qualified-draft``: an assembled packet is a set
of keyword matches whose relevance and truth nothing here checks, so it is never ``verified``. It is
``blocked-escalated`` when it has no task, or when the task and output contract alone do not fit the budget.
"""
from __future__ import annotations

from typing import Any

from g6.core.reliability_envelope import warning_card

from g6.context.budget import PRUNE_ORDER
from g6.context.packet import PACKET_SECTIONS, ContextPacket, ContextScorecard, packet_tokens

CHECKS_RUN: tuple[str, ...] = (
    "tokens recounted from item text against budget_tokens",
    "authority conflicts: values discarded by the merge, and duplicate keys",
    "stale items (freshness_class)",
    "empty repository retrieval",
    "a task is present and fits the budget",
)
NOT_CHECKED: tuple[str, ...] = (
    "whether the excerpts are relevant to the task",
    "whether retrieved text is true or current",
    "instructions inside untrusted excerpts and notes (they are labelled, not filtered)",
    "secrets whose shape the masking patterns do not recognise",
)


def _ev(code: str, message: str, source: str) -> dict[str, str]:
    return {"code": code, "message": message, "source": source}


def _skipped(retrieval: Any) -> str:
    """What the retriever did not read for its suffix, for an empty retrieval's message ('' when it was not told)."""
    if not isinstance(retrieval, dict) or "skipped_suffix" not in retrieval:
        return ""
    skipped, reads = retrieval["skipped_suffix"], ", ".join(map(str, retrieval.get("suffixes") or ()))
    if not skipped:
        return f"; no file was skipped for its suffix (it reads {reads})"
    return (f"; {skipped} file(s) were skipped for their suffix, since the retriever reads only {reads}; pass "
            "suffixes to Retriever to read others")


def _conflict_keys(packet: ContextPacket) -> list[str]:
    keys = {str(c.get("key")) for c in packet.conflicts if isinstance(c, dict)}
    by_key: dict[str, set[str]] = {}
    for item in packet.all_items:
        by_key.setdefault(item.key, set()).add(item.value)
    keys |= {key for key, values in by_key.items() if len(values) > 1}
    return sorted(keys)


def _metadata_evidence(packet: ContextPacket) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    retrieval = packet.metadata.get("retrieval")
    if isinstance(retrieval, dict):
        if retrieval.get("truncated"):
            found.append(_ev("G6_E_CONTEXT_RETRIEVAL_TRUNCATED", "the repository walk stopped at max_files, so some "
                             "files were never looked at", "retriever"))
        if retrieval.get("unreadable"):
            found.append(_ev("G6_E_CONTEXT_UNREADABLE_FILES", f"{retrieval['unreadable']} file(s) could not be read "
                             "and were matched by name only", "retriever"))
        if retrieval.get("missing_roots"):
            found.append(_ev("G6_E_CONTEXT_ROOT_MISSING", "configured root(s) not found: "
                             + ", ".join(map(str, retrieval["missing_roots"])), "retriever"))
    rerank = packet.metadata.get("rerank")
    if isinstance(rerank, dict) and rerank.get("called"):
        if rerank.get("fallback"):
            found.append(_ev("G6_E_CONTEXT_RERANKER_FALLBACK", "the reranker failed or answered with no usable "
                             f"order, so keyword order was kept: {rerank.get('reason', '')}", "reranker"))
        else:
            found.append(_ev("G6_E_CONTEXT_RERANKER_USED", f"{rerank.get('candidates', 0)} masked candidate(s) were "
                             "sent to the injected reranker; its order is advice and the budget floor was applied "
                             f"after it (ignored {rerank.get('ignored', 0)}, appended {rerank.get('appended', 0)})"
                             + ("; the candidate set was capped, so later matches were never offered to it"
                                if isinstance(retrieval, dict) and retrieval.get("capped") else ""),
                             "reranker"))
    return found


def score_packet(packet: ContextPacket) -> ContextScorecard:
    """Recompute the packet's envelope from its items, handles and recorded merge losers."""
    items = packet.all_items
    used = packet_tokens(packet)
    blocked: list[dict[str, str]] = []
    warned: list[dict[str, str]] = []
    if not any(item.value.strip() for item in packet.task):
        blocked.append(_ev("G6_E_CONTEXT_TASK_MISSING", "the packet carries no task", "packet"))
    if used > packet.budget_tokens:
        if any(packet.section_items(name) for name in PRUNE_ORDER):
            warned.append(_ev("G6_E_CONTEXT_BUDGET_EXCEEDED", f"{used} tokens exceed the budget of "
                              f"{packet.budget_tokens}", "packet"))
        else:  # audit 032, CTX-012: block rather than drop the task
            blocked.append(_ev("G6_E_CONTEXT_BUDGET_TOO_SMALL", f"the task and output contract alone take {used} "
                               f"tokens, over the budget of {packet.budget_tokens}", "packet"))
    if packet.metadata.get("context_truncated"):
        warned.append(_ev("G6_E_CONTEXT_BUDGET_EXCEEDED", "the packet was pruned to fit the token budget: "
                          f"{packet.metadata.get('pruned', {})}", "budget"))
    stale = packet.stale_items
    if stale:
        warned.append(_ev("G6_E_CONTEXT_STALE", f"{len(stale)} stale item(s): "
                          + ", ".join(item.key for item in stale[:5]), "packet"))
    conflicts = _conflict_keys(packet)  # audit 032, CTX-001: losers were recorded before the merge
    if conflicts:
        warned.append(_ev("G6_E_CONTEXT_AUTHORITY_CONFLICT", f"{len(conflicts)} key(s) carried different values: "
                          + ", ".join(conflicts[:5]), "packet"))
    empty = not packet.repository and not packet.repository_handles
    if empty:  # audit 032, CTX-006
        retrieval = packet.metadata.get("retrieval")
        found = retrieval.get("candidates", 0) if isinstance(retrieval, dict) else 0
        warned.append(_ev("G6_E_CONTEXT_EMPTY_RETRIEVAL", "no repository excerpt is in the packet"
                          + (f" ({found} candidate(s) matched but none fit the budget)" if found else
                             f" (nothing matched the query in the configured roots{_skipped(retrieval)})"), "packet"))
    warned.extend(_metadata_evidence(packet))
    evidence = blocked + warned
    state = "blocked-escalated" if blocked else "qualified-draft"
    return ContextScorecard(
        run_id=packet.run_id, item_count=len(items), used_tokens=used, budget_tokens=packet.budget_tokens,
        budget_ratio=used / max(1, packet.budget_tokens), stale_count=len(stale), conflict_count=len(conflicts),
        empty_retrieval=empty, missing_sections=[s for s in PACKET_SECTIONS if not packet.section_items(s)],
        completion_state=state, warning_card=_card(state, evidence), evidence=evidence,
        request_id=packet.request_id, task_id=packet.task_id)


def _card(state: str, evidence: list[dict[str, str]]) -> dict[str, Any]:
    if evidence:
        code, message = evidence[0]["code"], evidence[0]["message"]
    else:
        code, message = "G6_E_CONTEXT_UNVERIFIED", ("an assembled context packet is a qualified draft: its excerpts "
                                                     "are keyword matches whose relevance and truth are not checked")
    action = ("Supply a task and a budget that fits it, then rebuild the packet." if state == "blocked-escalated"
              else "Treat excerpts and notes as untrusted data and check what you rely on.")
    card = warning_card(code, message, action)
    card.update(completion_state=state, reasons=[e["message"] for e in evidence], checks_run=list(CHECKS_RUN),
                not_checked=list(NOT_CHECKED))
    return card


def rescore_packet(packet: ContextPacket) -> ContextPacket:
    """``packet`` with ``used_tokens`` and its envelope fields replaced by :func:`score_packet`'s."""
    card = score_packet(packet)
    return packet.model_copy(update={"used_tokens": card.used_tokens, "completion_state": card.completion_state,
                                     "warning_card": card.warning_card, "evidence": card.evidence})


__all__ = ["CHECKS_RUN", "NOT_CHECKED", "rescore_packet", "score_packet"]
