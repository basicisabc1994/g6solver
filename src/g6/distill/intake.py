# SPDX-License-Identifier: Apache-2.0
"""Read supplied failure events into the records ledger, once per stable source event.

No polling, tool calls, hooks or implicit independence. Callers provide a stable event id,
loop and round from the producer's record, not a poll timestamp. One serial writer is
required, as for Stores. A conflicting replay refuses rather than silently relabelling.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Self

from pydantic import Field, model_validator

from g6.core.ledger import LedgerError, digest
from .findings import met_before
from .ledger import Stores
from .records import Evidence, FailureRecord, FindingClass, Model

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]


class EventContext(Model):
    event_id: Text
    family: Text
    loop: Text
    round: Annotated[int, Field(ge=1, strict=True)]
    finding_class: FindingClass
    other: str = ""
    g6_commit: Text
    observed_at: Text

    @model_validator(mode="after")
    def named_other(self) -> Self:
        if (self.finding_class == "other") != bool(self.other.strip()):
            raise ValueError("other requires its own name, and only other has a name")
        if self.other != self.other.strip():
            raise ValueError("other name must be canonical (no surrounding whitespace)")
        return self


def class_key(record: FailureRecord) -> str:
    """The RCA treats each named other mechanism as a separate class."""
    if record.finding_class != "other":
        return record.finding_class
    name = record.provenance.get("other", "")
    if not isinstance(name, str) or not name.strip():
        raise LedgerError("other failure class needs its own name")
    return f"other: {name}"


def unit_cohort(record: FailureRecord) -> tuple[str | None, str | None]:
    """Read unit custody from original case headers or a derived training record."""
    case = record.provenance.get('case')
    if isinstance(case, dict) and isinstance(case.get('provenance'), dict):
        unit, cohort = case.get('unit_id'), case['provenance'].get('cohort')
    else:
        unit, cohort = record.provenance.get('unit_id'), record.provenance.get('cohort')
    return (unit if isinstance(unit, str) else None, cohort if isinstance(cohort, str) else None)


def earlier(records: list[FailureRecord], candidate: FailureRecord) -> list[str]:
    """Adapt to the original ledger rather than duplicating its loop/round rule.

    Legacy records without loop metadata are conservatively separate events/loops.
    """
    rows, labels = [], {}
    for record in [*records, candidate]:
        row = dict(folder=record.record_id, finding=1,
                   branch=record.provenance.get("loop", record.record_id),
                   round=record.provenance.get("round", 1))
        rows.append(row)
        labels[row["folder"], 1] = dict(mechanism=record.finding_class,
                                       other=record.provenance.get("other", ""))
    return sorted(ident for ident, _ in met_before(rows, labels, rows[-1]))


class FailureIntake:
    def __init__(self, stores: Stores | None = None):
        self.stores = stores or Stores()
        self.failures = self.stores.for_model(FailureRecord)

    def _put(self, channel: str, payload: dict, context: EventContext, *, kind: str, source: str,
             case_ref: str | None = None, evidence: list[Evidence] | None = None,
             observed_by: str = "self", split: str = "monitor", native_id=None) -> FailureRecord:
        context = EventContext.model_validate_json(context.model_dump_json())
        from .rectify_gate import Rectifier
        # Validate all logs before appending a source record.
        rectifier = Rectifier(self.stores)
        rectifier._logs()
        key = digest(dict(channel=channel, event_id=context.event_id if native_id is None else native_id))
        fingerprint = digest(dict(payload=payload, context=context.model_dump(mode="json")))
        records = self.failures.list()
        for old in records:
            if old.provenance.get("event_key") == key:
                if old.provenance.get("event_fingerprint") != fingerprint:
                    raise LedgerError("conflicting replay of a recorded source event")
                rectifier.reconcile()
                return old
        record = FailureRecord(kind=kind, source=source, family=context.family, observed_by=observed_by,
            case_ref=case_ref or digest(payload), split=split,
            evidence=evidence if evidence is not None else [Evidence(kind=channel, digest=digest(payload),
                                                                    receipt=f"{channel}:{context.event_id}")],
            finding_class=context.finding_class, met_before=[], g6_commit=context.g6_commit,
            observed_at=context.observed_at,
            provenance=dict(event_key=key, event_fingerprint=fingerprint, channel=channel,
                event_id=context.event_id, loop=context.loop, round=context.round, other=context.other))
        body = record.model_dump(mode="json", exclude={"record_id"})
        record = FailureRecord(**(body | {"met_before": earlier(records, record)}))
        self.failures.put(record)
        rectifier.reconcile()
        return record

    def refusal(self, reply: Mapping, context: EventContext, *, well_formed: bool) -> FailureRecord | None:
        if type(well_formed) is not bool:
            raise ValueError("well_formed must be a measured boolean")
        if not well_formed or reply.get("ok") is True:
            return None
        # Jobs surface exceptions through a refusal-shaped door reply. The same
        # native event read from the job log must not become a second failure.
        if reply.get("status") == "failed" and reply.get("job_id"):
            return self.crash(reply, context)
        code = re.match(r"^\[(G6_E_[A-Z0-9_]+)\]", str(reply.get("error", "")))
        card = reply.get("warning_card", {})
        card_code = card.get("code", "") if isinstance(card, Mapping) else ""
        if reply.get("ok") is not False or not (code or re.fullmatch(r"G6_E_[A-Z0-9_]+", card_code)):
            raise ValueError("a refusal needs a failed reply and stable G6_E code")
        return self._put("refusal", dict(reply), context, kind="g6_refusal", source="runner",
                         native_id=reply.get("request_id") or context.event_id)

    def crash(self, row: Mapping, context: EventContext) -> FailureRecord | None:
        if row.get("status") in {"pending", "running", "completed"}:
            return None
        if row.get("status") != "failed" or not row.get("error") or not row.get("job_id"):
            raise ValueError("a crash needs a failed job record, job id and error")
        return self._put("crash", dict(row), context, kind="g6_crash", source="runner", native_id=row["job_id"])

    def k_finding(self, row: Mapping, context: EventContext) -> FailureRecord:
        if not row.get("test") or not row.get("rule") or type(row.get("finding")) is not int:
            raise ValueError("a K finding needs its rule, reproducing test and finding number")
        if row.get("branch") != context.loop or row.get("round") != context.round:
            raise ValueError("finding loop/round differs from its context")
        return self._put("k_loop", dict(row), context, kind="missed_defect", source="k_loop",
                         native_id=[row.get("branch"), row.get("folder"), row["finding"]])

    def ci(self, check: Mapping, context: EventContext, *, branch: str) -> FailureRecord | None:
        if branch != "main" or check.get("status") != "completed" or check.get("conclusion") in {
                "success", "neutral", "skipped"}:
            return None
        if check.get("conclusion") not in {"failure", "timed_out", "action_required", "startup_failure"}:
            raise ValueError("CI result is not a measured regression")
        if not check.get("id") or check.get("head_sha") != context.g6_commit:
            raise ValueError("CI needs its check id and exact commit")
        return self._put("ci", dict(check), context, kind="regression", source="ci", native_id=check["id"])

    def discrepancy(self, row, context: EventContext) -> FailureRecord:
        from g6.self_model.monitor import FailureRecord as MonitorFailure
        if not isinstance(row, MonitorFailure):
            raise ValueError("a monitor failure is required, never an explained model update")
        row = MonitorFailure.model_validate_json(row.model_dump_json(exclude={"record_id"}))
        evidence = [Evidence(kind="measured_field", digest=digest(field.model_dump(mode="json")),
                             receipt=field.source) for field in row.evidence]
        return self._put("discrepancy", row.model_dump(mode="json"), context, kind=row.kind,
                         source=row.source, case_ref=row.case_ref, evidence=evidence, native_id=row.case_id)

    def record(self, row: FailureRecord, context: EventContext) -> FailureRecord:
        """Intake an existing self-repair, prediction, selector or person failure record."""
        row = FailureRecord.model_validate_json(row.model_dump_json())
        if (row.family, row.finding_class, row.g6_commit) != (
                context.family, context.finding_class, context.g6_commit):
            raise ValueError("existing failure and context disagree")
        return self._put(f"record:{row.source}", row.model_dump(mode="json"), context, kind=row.kind,
                         source=row.source, case_ref=row.case_ref, evidence=row.evidence,
                         observed_by=row.observed_by, split=row.split, native_id=row.record_id)
