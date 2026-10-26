# SPDX-License-Identifier: Apache-2.0
"""Bounded hierarchical extraction; offsets always refer to the input item's value."""
import re
from collections import Counter

from g6.context.packet import ContextItem, estimate_tokens


def _select(text: str, spans: list[tuple[int, int]], budget: int) -> list[tuple[int, int]]:
    words = {span: re.findall(r"\w+", text[span[0]:span[1]].casefold()) for span in spans}
    frequency = Counter(word for tokens in words.values() for word in tokens)
    ranked = sorted(spans, key=lambda span: -sum(frequency[w] for w in words[span]) / max(1, len(words[span])))
    selected = []
    chars = 0
    for start, end in ranked:
        cost = end - start + bool(selected)
        if (chars + cost + 3) // 4 <= budget:   # estimate_tokens of that many characters, without building them
            selected.append((start, end))
            chars += cost
    return sorted(selected)


def compress(item: ContextItem, budget_tokens: int) -> ContextItem:
    """Keep whole sentences, recursively reducing groups of 32, then selecting to budget.

    This measures word frequency, not importance or truth. No model is used. The
    returned item is untrusted data, even when the input was an instruction.
    """
    if type(budget_tokens) is not int or budget_tokens < 1 or len(item.value) > 1_000_000:
        raise ValueError("[G6_E_CONTEXT_COMPRESSION_INPUT] positive token budget and at most 1000000 characters")
    text = item.value
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S.*?(?:[.!?](?=\s|$)|$)", text, re.DOTALL)]
    if not spans:
        raise ValueError("[G6_E_CONTEXT_COMPRESSION_INPUT] no sentence to keep")
    depth = 0
    while len(spans) > 32 and estimate_tokens(" ".join(text[a:b] for a, b in spans)) > budget_tokens:
        reduced = []
        for at in range(0, len(spans), 32):
            group = spans[at:at + 32]
            size = estimate_tokens(" ".join(text[a:b] for a, b in group))
            # Keep at least the shortest whole sentence in each group.
            floor = min(estimate_tokens(text[a:b]) for a, b in group)
            reduced.extend(_select(text, group, max(floor, size // 2)))
        if len(reduced) >= len(spans):
            break
        spans = reduced
        depth += 1
    spans = _select(text, spans, budget_tokens)
    if not spans:
        raise ValueError("[G6_E_CONTEXT_COMPRESSION_BUDGET] no whole sentence fits; increase the budget")
    value = " ".join(text[a:b] for a, b in spans)
    metadata = {**item.metadata, "method": "term_frequency_extractive", "source_offsets": spans,
                "offset_basis": "input_item_value", "depth": depth, "completion_state": "qualified-draft",
                "lossy": value != text, "budget_tokens": budget_tokens}
    return ContextItem.model_validate({**item.model_dump(), "value": value, "token_estimate": estimate_tokens(value),
                                       "kind": "memory_note", "trust": "untrusted", "authority": "operational",
                                       "metadata": metadata})
