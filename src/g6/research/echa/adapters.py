# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Adapters from the structures G6 already writes (plain dicts: no product import) to the kernel's records.

``acceptance.mechanism_record`` is one check's attempt as the workforce keeps it on a run's close; ``acceptance.
elicitation_record`` is one verify cycle's failure elicitation record. The mapping is deliberately conservative and is a
starting classification for a person to confirm at preregistration, not a ruling on independence: a check that did not
run is unknown, an author-controlled test is a partial falsifier, anything a model or a person judged is a heuristic
review, and only a model-free check recorded as independent is an independent executable.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .records import EvidenceRecord

_DECIDED = ("pass", "fail")
_REVIEWS = ("independent_model_review", "human_review")
ADAPTED_NOTE = ("adapted from a workforce mechanism record; the category is a conservative mapping and not a ruling "
                "on independence")


def _category(rec: Mapping[str, Any]) -> str:
    if not rec.get("executed"):
        return "NO_DISCRIMINATING_EVIDENCE"
    mechanism = rec.get("mechanism")
    if mechanism == "formal_proof_check":
        return "FORMAL_CHECK"
    if mechanism in _REVIEWS or not rec.get("model_free"):
        return "HEURISTIC_REVIEW"
    return "INDEPENDENT_EXECUTABLE" if rec.get("independent") else "PARTIAL_FALSIFIER"


def evidence_from_mechanism_record(rec: Mapping[str, Any], *, evidence_id: str, predicate_id: str, standard_sha256: str,
                                   oracle_version: str, fixture_version: str, source_ref: str, scope: str,
                                   independence_group: str = "", negative_control_ref: str = "",
                                   known_limitations: tuple[str, ...] = ()) -> EvidenceRecord:
    executed = bool(rec.get("executed"))
    independent = executed and bool(rec.get("independent"))
    result = rec.get("result") if executed and rec.get("result") in _DECIDED else "unknown"
    return EvidenceRecord(
        evidence_id=evidence_id, predicate_id=predicate_id, oracle_category=_category(rec), oracle_version=oracle_version,
        fixture_version=fixture_version, independent=independent,
        independence_group=independence_group if independent else "",
        independence_rationale="the workforce recorded this check as independent of the step it judges" if independent else "",
        result=result, scope=scope, known_limitations=(*known_limitations, ADAPTED_NOTE), source_ref=source_ref,
        standard_sha256=standard_sha256, negative_control_ref=negative_control_ref, purpose="check",
        oracle_error_bound=None)


def known_unknowns(elicitation: Mapping[str, Any]) -> tuple[str, ...]:
    """What a failure elicitation record says stayed unchecked, as the episode's ``known_unknowns``."""
    return (*(f"surface not tested: {name}" for name in elicitation.get("remaining_unchecked_surfaces") or ()),
            *(f"no evidence from: {name}" for name in elicitation.get("evidence_gaps") or ()))
