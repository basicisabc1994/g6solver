# SPDX-License-Identifier: Apache-2.0
"""``corroboration``: how many independent source lineages back a claim, and does any of them contradict it?

It is a proxy and it says so (audit 014, GRD-010). Each passage's stance comes from the same lexical sentence check
as ``check_claim``, run on that passage alone: ``quoted``/``supported`` supports the claim, ``conflict`` opposes it,
anything else does not count. A passage's lineage is read off its source label. So the report counts agreement
between labelled sources found by lexical retrieval; it does not establish that the claim is true, or that sources
the labels call independent really are.

Statuses use the epistemic vocabulary of ``g6.core.evidence``: ``SUPPORTED`` needs two or more supporting lineages
and no opposing one, ``ASSERTED`` is one supporting lineage, ``CONTESTED`` is any opposing lineage, ``UNVERIFIABLE``
is none either way. Only ``SUPPORTED`` reads ``qualified-draft``; nothing here returns ``verified``.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from g6.core import BLOCKED_ESCALATED, QUALIFIED_DRAFT
from g6.grounding.claims import Judge, check_claim
from g6.grounding.retrieval import Passage, Retriever

STATUSES = ("SUPPORTED", "ASSERTED", "CONTESTED", "UNVERIFIABLE")

# lineage(passage) -> a key; passages with the same key are one lineage
Lineage = Callable[[Passage], str]


@dataclass(frozen=True)
class CorroborationReport:
    claim: str
    status: str                                  # one of STATUSES
    completion_state: str                        # qualified-draft only when SUPPORTED, else blocked-escalated
    supporting_lineages: tuple[str, ...] = ()
    opposing_lineages: tuple[str, ...] = ()
    passages_considered: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}


def _lineage(passage: Passage) -> str:
    """The default lineage: a web source's host, else the corpus the text came in with (its ``provenance``).

    audit 014, GRD-010: the archive gave every source without a host its own lineage, so two files of one repository
    corpus counted as two independent sources. Here every file that came in together is one lineage, and a DOI is just
    a link through doi.org: it adds no weight and DOIs share that one host."""
    source = passage.source.strip()
    if source.lower().startswith("doi:"):
        source = "https://doi.org/" + source[4:].strip()
    url = urlparse(source)
    host = (url.hostname or "") if url.scheme in ("http", "https") else ""
    if host:
        return "host:" + host.removeprefix("www.")
    return "corpus:" + (passage.provenance or "unspecified")


def _stance(claim: str, passage: Passage, judge: Judge | None) -> str:
    """``supports``, ``opposes`` or ``neither``, from ``check_claim`` on this passage alone.

    audit 014, GRD-010: the archive stamped every passage ``supports``, so CONTESTED could never be reached."""
    status = check_claim(claim, [passage], judge=judge).status
    if status in ("quoted", "supported"):
        return "supports"
    return "opposes" if status == "conflict" else "neither"


def _status(supporting: dict[str, list[Passage]], opposing: dict[str, list[Passage]]) -> str:
    """audit 014, GRD-010: support is counted in lineages only; no source (a DOI included) counts as primary alone."""
    if opposing:
        return "CONTESTED"
    if len(supporting) >= 2:
        return "SUPPORTED"
    return "ASSERTED" if supporting else "UNVERIFIABLE"


def corroboration(claim: str, sources: Retriever | list[Passage], *, top_k: int = 5, judge: Judge | None = None,
                  lineage: Lineage | None = None) -> CorroborationReport:
    """Group the passages that support or contradict ``claim`` by source lineage.

    ``lineage`` replaces the default grouping when you know more than the labels say (for example a manifest that
    names each document's publisher). A ``judge`` is passed to ``check_claim`` and can only add opposition."""
    passages = sources.search(claim, top_k) if isinstance(sources, Retriever) else list(sources)
    lineage_of = lineage or _lineage
    supporting: dict[str, list[Passage]] = {}
    opposing: dict[str, list[Passage]] = {}
    for p in passages:
        stance = _stance(claim, p, judge)
        if stance == "neither":
            continue
        key = str(lineage_of(p) or "").strip() or "unspecified"
        (supporting if stance == "supports" else opposing).setdefault(key, []).append(p)
    status = _status(supporting, opposing)
    by_lineage: dict[str, set[str]] = {}
    for group in (supporting, opposing):
        for key, members in group.items():
            by_lineage.setdefault(key, set()).update(p.source for p in members)
    evidence = {
        "method": "lexical_retrieval_proxy",
        "oracle_category": "HEURISTIC_REVIEW",
        "stance": "check_claim on each passage alone: quoted/supported supports, conflict opposes",
        "lineage": "caller_supplied" if lineage else "source label: web host, else the corpus the text came in with",
        "passages_supporting": sum(len(v) for v in supporting.values()),
        "passages_opposing": sum(len(v) for v in opposing.values()),
        "sources_by_lineage": {key: sorted(members) for key, members in sorted(by_lineage.items())},
        "judge": judge is not None,
        "not_established": "truth; that sources the labels call independent are independent; entailment; "
                           "text outside the supplied passages",
    }
    return CorroborationReport(claim, status, QUALIFIED_DRAFT if status == "SUPPORTED" else BLOCKED_ESCALATED,
                               tuple(sorted(supporting)), tuple(sorted(opposing)), len(passages), evidence)
