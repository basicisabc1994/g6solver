# SPDX-License-Identifier: Apache-2.0
"""``coverage``: how much of a draft rests on more than one independent source.

Merged from the archive's ``knowledge_quality`` (B09). It flagged a coverage gap from a fixed threshold on retrieval
scores whose scale depends on the backend (KNOWLEDGE_QUALITY-01; its own evaluation got the gap decision right 9 times
in 22), and called a source weak from a hand-written table of host scores (-02). Here coverage is counted per claim:
each sentence of the draft (``split_claims``) goes through :func:`~g6.grounding.corroboration` and its lineage rule (a
web source's host, else the corpus the text came in with), and a claim is covered only when two or more lineages
support it and none contradicts it. Independence is counted; no host is worth more than another.

Optional ``weights`` (``{source label or lineage: number}``) are the caller's own view of its sources: recorded per
claim beside the sources they name, as the caller's claim, and no status or label depends on them.

The best state is ``qualified-draft``, every claim covered; otherwise ``blocked-escalated`` with the worst code among
the claims: ``G6_E_GROUNDING_CONTESTED`` (a source contradicts a claim), ``G6_E_GROUNDING_UNSUPPORTED`` (no source
supports one), ``G6_E_GROUNDING_THIN_SOURCES`` (one rests on a single lineage). Support is lexical, as in
``check_claim``, so coverage is a proxy and never ``verified``.

Each claim searches the sources again, so the work is bounded before any search: a draft of more than
:data:`MAX_TEXT_BYTES` bytes or :data:`MAX_CLAIMS` claims is refused with ``G6_E_GROUNDING_TEXT_TOO_LARGE``, and
sources holding more than :data:`MAX_PASSAGES` passages (a list, or a retriever that says its size) with
``G6_E_GROUNDING_SOURCES_TOO_LARGE``, each naming its cap.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from g6.core import BLOCKED_ESCALATED, QUALIFIED_DRAFT
from g6.core.reliability_envelope import warning_card
from g6.grounding.claims import Judge, split_claims
from g6.grounding.lineage import Lineage, corroboration
from g6.grounding.retrieval import Passage, Retriever

CONTESTED = "G6_E_GROUNDING_CONTESTED"
UNSUPPORTED = "G6_E_GROUNDING_UNSUPPORTED"
THIN_SOURCES = "G6_E_GROUNDING_THIN_SOURCES"
NO_CLAIMS = "G6_E_GROUNDING_NO_CHECKABLE_SENTENCE"
TEXT_TOO_LARGE = "G6_E_GROUNDING_TEXT_TOO_LARGE"
SOURCES_TOO_LARGE = "G6_E_GROUNDING_SOURCES_TOO_LARGE"
#: The most one coverage check takes on, each refused before any search.
MAX_TEXT_BYTES = 100_000
MAX_CLAIMS = 200
MAX_PASSAGES = 5_000
#: Worst first: the corroboration status, the code it gives a draft, and what to do about it.
_WORST = (
    ("CONTESTED", CONTESTED, "contradicted by a source", "Resolve the contradiction from the sources before the draft "
                                                          "goes out, or say the point is disputed."),
    ("UNVERIFIABLE", UNSUPPORTED, "supported by no source", "Find a source for each unsupported claim, or take it out."),
    ("ASSERTED", THIN_SOURCES, "resting on a single source lineage", "Find a second, independent source for each "
                                                                      "thin claim, or say it rests on one."),
)


@dataclass(frozen=True)
class CoverageReport:
    """Per-claim support counted in independent lineages; see the module docstring."""

    claims: tuple[dict[str, Any], ...]
    covered: int
    single_lineage: int
    contested: int
    unsupported: int
    covered_share: float
    lineages: list[str]
    completion_state: str
    warning_card: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def _weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    found: dict[str, float] = {}
    for name, value in (weights or {}).items():
        if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
            raise ValueError(f"the weight for {name!r} must be a finite number, not {value!r}")
        found[str(name)] = float(value)
    return found


def coverage(text: str, sources: Retriever | list[Passage], *, top_k: int = 5, judge: Judge | None = None,
             lineage: Lineage | None = None, weights: Mapping[str, float] | None = None) -> CoverageReport:
    """Each claim of ``text`` and the independent source lineages that support or contradict it.

    ``judge`` and ``lineage`` are passed to :func:`~g6.grounding.corroboration` (a judge can only add opposition; a
    lineage function of yours replaces the label rule, and the evidence says so). ``weights`` are recorded as yours."""
    given = _weights(weights)
    found_claims, refused = _bounded(text, sources)
    if refused is not None:
        return refused
    claims: list[dict[str, Any]] = []
    for claim in found_claims:
        found = corroboration(claim, sources, top_k=top_k, judge=judge, lineage=lineage)
        by_lineage = found.evidence.get("sources_by_lineage", {})
        backing = sorted({source for key in found.supporting_lineages for source in by_lineage.get(key, ())})
        named = {name: given[name] for name in [*backing, *found.supporting_lineages] if name in given}
        claims.append({"claim": claim, "status": found.status, "lineages": list(found.supporting_lineages),
                       "opposing_lineages": list(found.opposing_lineages), "sources": backing,
                       "caller_weights": named})
    statuses = [c["status"] for c in claims]
    covered = statuses.count("SUPPORTED")
    card = None
    if not claims:
        card = warning_card(NO_CLAIMS, "the text has no checkable sentence (three words or more)",
                            "Pass the draft itself, one claim per sentence.")
    else:
        for status, code, what, action in _WORST:
            if status in statuses:
                card = warning_card(code, f"{statuses.count(status)} of {len(claims)} claim(s) {what}", action)
                break
    evidence = {
        "method": "per-claim corroboration over lexical support",
        "oracle_category": "HEURISTIC_REVIEW",
        "covered_means": "two or more independent lineages support the claim and none contradicts it",
        "lineage": "caller_supplied" if lineage else "source label: web host, else the corpus the text came in with",
        "prestige": "not used: no host or venue counts for more than another; independence is counted",
        "caller_weights": ("the caller's claim, recorded beside the sources it names; no status or label depends on "
                           "it" if given else "none given"),
        "not_established": "truth; that sources the labels call independent are independent; entailment; text "
                           "outside the supplied passages",
    }
    return CoverageReport(
        claims=tuple(claims), covered=covered, single_lineage=statuses.count("ASSERTED"),
        contested=statuses.count("CONTESTED"), unsupported=statuses.count("UNVERIFIABLE"),
        covered_share=round(covered / len(claims), 3) if claims else 0.0,
        lineages=sorted({key for c in claims for key in c["lineages"]}),
        completion_state=QUALIFIED_DRAFT if card is None else BLOCKED_ESCALATED, warning_card=card, evidence=evidence)


def _bounded(text: str, sources: Retriever | list[Passage]) -> tuple[list[str], CoverageReport | None]:
    """The draft's claims, or the refusal of a check past one of the caps, decided before any search."""
    size = len(text.encode("utf-8", "replace"))
    found = split_claims(text) if size <= MAX_TEXT_BYTES else []
    held = len(sources) if hasattr(sources, "__len__") else None   # type: ignore[arg-type]
    if size > MAX_TEXT_BYTES:
        code, reason = TEXT_TOO_LARGE, f"the draft is {size} bytes; coverage takes at most {MAX_TEXT_BYTES}"
    elif len(found) > MAX_CLAIMS:
        code, reason = TEXT_TOO_LARGE, f"the draft has {len(found)} claims; coverage takes at most {MAX_CLAIMS}"
    elif held is not None and held > MAX_PASSAGES:
        code, reason = SOURCES_TOO_LARGE, f"the sources hold {held} passages; coverage takes at most {MAX_PASSAGES}"
    else:
        return found, None
    card = warning_card(code, reason, "Split the draft or the sources and check each part.")
    return [], CoverageReport(claims=(), covered=0, single_lineage=0, contested=0, unsupported=0, covered_share=0.0,
                              lineages=[], completion_state=BLOCKED_ESCALATED, warning_card=card,
                              evidence={"method": "refused before any search", "reason": reason,
                                        "caps": {"text_bytes": MAX_TEXT_BYTES, "claims": MAX_CLAIMS,
                                                 "passages": MAX_PASSAGES}})


__all__ = ["CONTESTED", "MAX_CLAIMS", "MAX_PASSAGES", "MAX_TEXT_BYTES", "SOURCES_TOO_LARGE", "TEXT_TOO_LARGE",
           "THIN_SOURCES", "UNSUPPORTED", "CoverageReport", "coverage"]
