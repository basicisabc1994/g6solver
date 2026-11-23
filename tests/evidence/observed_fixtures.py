# SPDX-License-Identifier: Apache-2.0
"""Explicit host observations for pre-custody mapping unit fixtures.

These fixtures isolate structural mapping tests from execution. Custody acceptance
tests call the production boundary directly and never use this fixture adapter.
"""
from dataclasses import replace

from g6.evidence import mapping
from g6.evidence.custody import RunReference, _record_case
from g6.workforce import receipt_store


# Reissuing the same batch is another reading of these observations, not another
# execution. Keep this test-only identity stable within the actual host store.
_OBSERVED_BATCHES = []


def observe_records(records):
    records = list(records)
    events = []
    references = {}
    unique = []
    indices = []
    for rec in records:
        index = next((i for i, known in enumerate(unique) if known == rec), len(unique))
        indices.append(index)
        if index < len(unique):
            continue
        unique.append(rec)
        parts, _ = mapping.about(rec)
        controls = rec.controls if isinstance(rec.controls, (tuple, list)) else ()
        for control in controls:
            if hasattr(control, "control_id"):
                events.append((control.seq, index, ("control", control.control_id), "fail" if control.killed else "pass"))
        if type(rec.positive_seq) is int:
            events.append((rec.positive_seq, index, ("positive",), "pass"))
        events.append((rec.seq if type(rec.seq) is int else 0, index, ("execution",), rec.result))
        if not all(type(value) is str for value in (parts.artefact, parts.check_id, parts.check_sha, parts.environment)):
            continue
    for _, index, role, result in sorted(events, key=lambda event: event[0]):
        rec = unique[index]
        parts, _ = mapping.about(rec)
        subject = parts.artefact if role == ("execution",) else "0" * 64
        if not isinstance(parts.check_id, str):
            continue
        receipt = _record_case(artifact=subject, environment_sha256=parts.environment,
                              checks={parts.check_id: {"sha256": parts.check_sha, "result": result}})
        references[index, role] = RunReference(receipt, subject)
    converted = []
    for index, rec in enumerate(unique):
        controls = rec.controls if isinstance(rec.controls, (tuple, list)) else ()
        converted.append(replace(rec, execution=references.get((index, ("execution",))),
            control_runs=tuple((c.control_id, references.get((index, ("control", c.control_id))))
                               for c in controls if hasattr(c, "control_id")),
            positive=references.get((index, ("positive",)))))
    return [converted[index] for index in indices]


def issue_observed_records(facts, artifact, records, **kwargs):
    records = list(records)
    store = receipt_store.store_path()
    for known_store, known, observed in _OBSERVED_BATCHES:
        if known_store == store and known == records:
            break
    else:
        observed = observe_records(records)
        _OBSERVED_BATCHES.append((store, records, observed))
    return mapping.issue_records(facts, artifact, observed, **kwargs)


def observed_report(root, ids, raw, *, subject=None):
    """An explicit fake host executor: retain the real store and the production phase reading."""
    from g6.core.ledger import digest
    from g6.evidence.custody import run_environment
    from g6.harness import patch_review
    from g6.workforce import repo_tests

    raw = dict(raw)
    subject = subject or raw.get("source_before") or digest(repo_tests.snapshot(root))
    checks = {*ids, *repo_tests.read_report(raw).nodes}
    shas = patch_review._check_shas(root, checks)
    verdicts = patch_review._verdicts(list(checks), raw)
    raw.update(host_run({check: {"sha256": shas.get(check, ""), "result": {
        "passed": "pass", "failed": "fail"}.get(patch_review.observation(verdicts[check]), "unknown")}
        for check in checks}, artifact=subject, environment_sha256=digest(run_environment(raw))))
    return raw


def host_run(checks, *, artifact, environment_sha256):
    receipt = _record_case(artifact=artifact, environment_sha256=environment_sha256, checks=checks)
    return {"custody_receipt": receipt, "source_before": artifact}


def assess_observed(evaluated, runs, **kwargs):
    """Declare legacy outcome fixtures as host observations, without replacing any ingestion checks."""
    from g6.harness import requirement_evidence as evidence

    shas = kwargs["check_sha"]
    artifact = kwargs["artifact"]
    environment = mapping.environment_sha256(kwargs["environment"])

    def stored(outcomes, subject):
        return host_run({check: {"sha256": shas.get(check, ""), "result": {
            "passed": "pass", "failed": "fail"}.get(result, "unknown")} for check, result in outcomes.items()},
            artifact=subject, environment_sha256=environment)

    first = {check["id"]: evidence.observation(check) for req in evaluated for check in req["checks"]}
    runs = [dict(run) for run in runs]
    primary = next((run for run in runs if run.get("tier") == 1), None)
    if primary is None:
        primary = {"tier": 1}
        runs.insert(0, primary)
    primary.update(stored(first, primary.get("source_before") or artifact))
    seen = [dict(run) for run in kwargs.get("observed", ())]
    for run in seen:
        run.update(stored(run.get("verdicts") or {}, artifact))
    for run in runs:
        if run.get("check") == evidence.AT_BASE:
            run.update(stored(evidence.at_base_observations([run]), "0" * 64))
    again = dict(kwargs.get("again") or {})
    if again.get("verdicts"):
        again.update(stored(again["verdicts"], artifact))
    return evidence.assess(evaluated, runs, **{**kwargs, "again": again, "observed": seen})
