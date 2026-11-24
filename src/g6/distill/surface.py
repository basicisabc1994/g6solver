# SPDX-License-Identifier: Apache-2.0
"""Local distillation record and library views; host execution lives in MCP's backend."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import TypeAdapter

from g6.core.ledger import ChainLedger, digest
from g6.core.storage import state_path
from .ledger import Stores, failure_writer
from .library import Library, public_entry
from .records import FailureRecord, LibraryEntry, Sha256


@failure_writer(reading=True)
def library(op: str = "list", entry_id: str = "") -> dict:
    if op not in {"list", "describe", "history"}:
        raise ValueError("library op must be list, describe or history")
    if op == "list":
        if entry_id:
            raise ValueError("list takes no entry_id")
        return {"entries": [public_entry(e) for e in Library().list()]}
    TypeAdapter(Sha256).validate_python(entry_id)
    historical = Stores().for_model(LibraryEntry).get(entry_id)
    if op == "history":
        return {"entries": [public_entry(e) for e in Stores().for_model(LibraryEntry).list()
                            if e.candidate_id == historical.candidate_id],
                "unlabelled_fires": [row["body"] for row in ChainLedger(
                    state_path("distill", "field_fires_ledger.jsonl")).entries()
                    if row["kind"] == "field_fire" and row["body"].get("candidate_id") == historical.candidate_id]}
    current = next(e for e in Library().list() if e.candidate_id == historical.candidate_id)
    return {"entry": public_entry(current), "requested_entry_id": entry_id}


def unclassified(kind: str, family: str) -> str:
    """The named "other" class of a report no one has classified yet (intake.class_key needs a name)."""
    return f"unclassified {kind} report in {family}"


def failure_record(kind: str, family: str, case_json: str, note: str) -> dict:
    if not family.strip() or len(family) > 200 or len(note) > 8000 or len(case_json) > 100_000:
        raise ValueError("failure requires a family and bounded case and note")
    case = json.loads(case_json)
    if not isinstance(case, dict):
        raise ValueError("case_json must be an object")
    # Client assertions cannot establish independent observation or held-out custody.
    row = FailureRecord(kind=kind, family=family, source="person", observed_by="self",
                        case_ref=digest(case), split=case.get("split", "field"), evidence=[],
                        finding_class="other", met_before=[], g6_commit="unknown",
                        observed_at=datetime.now(timezone.utc).isoformat(),
                        provenance={"case": case, "note": note, "intake": "client_request",
                                    "other": unclassified(kind, family)})
    from .intake import unit_cohort
    with failure_writer():
        store = Stores().for_model(FailureRecord)
        unit, cohort = unit_cohort(row)
        if unit and cohort and any(unit_cohort(old)[0] == unit and unit_cohort(old)[1] not in {None, cohort}
                                   for old in store.list()):
            from g6.core.door import blocked_reply
            return blocked_reply('[G6_E_SYNTH_COHORT] One unit cannot have conflicting cohorts; '
                                 'the earlier custody is retained.')
        from .rectify_gate import Rectifier
        rectifier = Rectifier()
        rectifier._logs()
        ident = store.put(row)
        rectifier.reconcile()
        return {"record_id": ident, "observed_by": "self"}


def record_reproduction(event: dict) -> None:
    """Retain an explicitly reported reproduction from the hook as self-observed field data.

    Tool responses passed by a client are not independent evaluator receipts. A raw concern,
    library list or silent detector is never labelled as a confirmed failure here.
    """
    value = event.get("tool_response")
    if not isinstance(value, dict):
        return
    value = value.get("structuredContent", value)
    if not isinstance(value, dict):
        return
    assessment = value.get("assessment")
    if not isinstance(assessment, dict) or assessment.get("outcome") != "defect_detected":
        return
    completion = assessment.get("completion")
    defects = completion.get("defects_reproduced", {}) if isinstance(completion, dict) else {}
    rows = [*assessment.get("reasons", [])]
    if isinstance(defects, dict) and isinstance(defects.get("stated_and_repository_checks"), list):
        rows.extend(defects["stated_and_repository_checks"])
    reproductions = [r for r in rows if isinstance(r, dict) and r.get("reproduction")]
    if not reproductions:
        return
    body = {"reproductions": reproductions, "tool": str(event.get("tool_name", ""))}
    if len(json.dumps(body)) > 100_000:
        return
    case_ref = digest(body)
    store = Stores().for_model(FailureRecord)
    row = FailureRecord(kind="missed_defect", family="test_weakening", source="k_loop", observed_by="self",
                        case_ref=case_ref, split="field", evidence=[], finding_class="other", met_before=[],
                        g6_commit="unknown", observed_at=datetime.now(timezone.utc).isoformat(),
                        provenance={"case": body, "intake": "untrusted_tool_response",
                                    "other": unclassified("missed_defect", "test_weakening")})
    with failure_writer():
        from .rectify_gate import Rectifier
        rectifier = Rectifier()
        rectifier._logs()
        if not any(old.case_ref == case_ref and old.source == 'k_loop' for old in store.list()):
            store.put(row)
        rectifier.reconcile()
