# SPDX-License-Identifier: Apache-2.0
"""Grounding: find the source passages behind a claim, and say honestly how well they support it.

Use it to check whether the claims in a draft (an AI answer, a report, an email) are backed by your documents: each
sentence is matched against the sources, and wrong figures and flipped negations come back as conflicts.

Rebuilt small for the library (audit 014 §11). Bring your own corpus through the ``Retriever``
protocol; ``TextRetriever`` is a dependency-free BM25 over text you hand it.

    from g6.grounding import TextRetriever, check_claim
    r = TextRetriever.from_texts({"policy.md": open("policy.md").read()})
    check_claim("Refunds are issued within 14 days", r)

What ``check_claim`` establishes is lexical: the claim's content words appear together in one
source sentence, with no negation flip and no conflicting number. That is useful (it catches
invented facts, negation flips and wrong figures) but it is not entailment, so it never returns
``verified``: support reads ``qualified-draft``, anything else ``blocked-escalated``. An optional
``judge`` (an NLI model or LLM) is heuristic review in ECHA terms and can only make a result stricter.

Measured (study V1, ``docs/validation/results/V1``): on 174 negated or wrong-figure VitaminC pairs it called none
supported, where a relevance baseline called 32% supported; it found support for only 19% of supported paraphrases.

Two more checks sit beside ``check_claim`` and leave it unchanged:

- ``corroboration(claim, sources)`` groups the passages that support or contradict a claim by source lineage (a web
  host, else the corpus the text came in with, so two files of one repository are one lineage). ``SUPPORTED`` needs
  two lineages and no contradiction; any contradicting lineage makes it ``CONTESTED``. It is a proxy over lexical
  retrieval and says so; it is not evidence that the claim is true, and its best state is ``qualified-draft``.
- ``check_staleness(passages)`` ages each passage from the publication date its source declares
  (``Passage.published_at``). An undated source is ``unknown``, never assumed fresh; there is no year cap.
- ``coverage(text, sources)`` (merged from the archive's ``knowledge_quality``) runs ``corroboration`` on each claim of
  a draft and counts what is covered: two or more lineages and no contradiction. A draft resting on one lineage, or
  with a claim nothing supports, is ``blocked-escalated``; source weights a caller gives are recorded as its claim and
  change nothing. No table of host prestige: independence is counted, not reputation.

Not ported from the archive (audit 014): the remote grounding block and its ``source_capabilities``, the skill MCP
server, and the multimedia legs (the GRD-017 remote, skill and multimedia-label items have no code to fix here); the
staleness detector's confidence adjustment and expiry filter; reading a year out of a citation label; the archive's
host-or-filename independence grouping and its DOI-means-primary rule.
"""
from g6.grounding.claims import ClaimCheck, check_claim, check_claims, split_claims
from g6.grounding.coverage import CoverageReport, coverage
from g6.grounding.grounding_block import GroundingBlock, GroundingInput
from g6.grounding.lineage import CorroborationReport, corroboration
from g6.grounding.retrieval import Passage, Retriever, TextRetriever
from g6.grounding.staleness import SourceAge, StalenessReport, check_staleness
from g6.grounding.tripwire import instruction_signals

__g6_component__ = True
__g6_block__ = "GroundingBlock"
__g6_requires__: tuple[str, ...] = ()
__g6_extras__ = {"nli": ("sentence_transformers",)}
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("fabricated_fact", "weak_sources", "unsupported_citation", "ungrounded_answer",
                                     "malformed_model_output")
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["ClaimCheck", "CorroborationReport", "CoverageReport", "GroundingBlock", "GroundingInput", "Passage",
           "Retriever", "SourceAge", "StalenessReport", "TextRetriever", "check_claim", "check_claims",
           "check_staleness", "corroboration", "coverage", "instruction_signals", "split_claims"]
