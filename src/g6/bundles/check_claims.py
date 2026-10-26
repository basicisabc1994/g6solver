# SPDX-License-Identifier: Apache-2.0
"""Bundle: Check claims against your sources — which statements in a draft do your documents back up?

Works offline (no model). Paste a draft (an AI answer, a report, a summary) and the source text it
should rest on. Every sentence of the draft is checked with ``g6.grounding.check_claim``: found,
contradicted (negation flipped, numbers differ) or not found. Lexical support is not proof, so the
best a claim can earn is ``qualified-draft``.
"""
from __future__ import annotations

import re
from typing import Any

from g6.bundles import Bundle, BundleResult, Field, weakest

_ADVICE = {
    "quoted": "Stated in the source.",
    "supported": "Backed by the source (reworded) — skim to confirm.",
    "partial": "Only partly backed — some words or figures are not in the source.",
    "conflict": "The source says something different — fix or remove.",
    "not_found": "Nothing in the sources backs this — find a source or remove it.",
}


def parse_sources(text: str) -> dict[str, str]:
    """Sources are separated by a line of ``---``; an optional first line ``source: <name>`` names one."""
    out: dict[str, str] = {}
    for i, block in enumerate(b for b in re.split(r"^\s*-{3,}\s*$", text, flags=re.M) if b.strip()):
        lines = block.strip().splitlines()
        name = f"source {i + 1}"
        if lines and lines[0].lower().startswith("source:"):
            name = lines[0].split(":", 1)[1].strip() or name
            lines = lines[1:]
        out[name] = "\n".join(lines)
    return out


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    from g6.grounding import TextRetriever, check_claims

    sources = parse_sources(inputs.get("sources", ""))
    if not sources:
        return BundleResult("blocked-escalated", "Paste at least one source to check against.")
    checks = check_claims(inputs.get("draft", ""), TextRetriever.from_texts(sources))
    if not checks:
        return BundleResult("blocked-escalated", "Paste a draft with at least one full sentence.")
    rows = [{
        "claim": c.claim,
        "result": c.status.replace("_", " "),
        "what to do": _ADVICE[c.status] + (f" ({'; '.join(c.conflicts)})" if c.conflicts else "")
        + (f" Missing: {', '.join(c.missing_terms)}." if c.status == "partial" and c.missing_terms else ""),
        "source": f"{c.source}: “{c.sentence}”" if c.sentence and c.status != "not_found" else "—",
        "label": c.completion_state,
    } for c in checks]
    backed = sum(c.status in ("quoted", "supported") for c in checks)
    bad = sum(c.status == "conflict" for c in checks)
    return BundleResult(
        weakest([c.completion_state for c in checks]),
        f"{len(checks)} claim(s): {backed} backed by your sources, {bad} contradicted, "
        f"{len(checks) - backed - bad} not (fully) found.",
        rows,
        ["This checks wording and figures against the text you pasted. It does not check that the sources "
         "themselves are right, and a reworded claim can still change the meaning — read the flagged rows.",
         "Nothing is labelled verified: word overlap is evidence, not proof."],
    )


BUNDLE = Bundle(
    id="check-claims",
    title="Check claims against your sources",
    summary="Find the statements in a draft that your documents don't back up.",
    fields=(
        Field("draft", "Draft to check", placeholder="Refunds are issued within 30 days. Delivery is free.",
              help="An AI answer, report or summary. Each sentence is checked on its own."),
        Field("sources", "Your sources",
              placeholder="source: refund policy\nRefunds are issued within 14 days of a return.\n---\n"
                          "source: shipping\nOrders over $50 ship free.",
              help="Paste the text of each source. Separate sources with a line of ---; "
                   "start one with 'source: <name>' to name it."),
    ),
    run=run,
)
