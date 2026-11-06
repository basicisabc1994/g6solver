# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The patch review's evidence in the issuer's terms (G6 0.3 design, slice 2): ``assessment.requirement_evidence``.

The issuer, ``g6.evidence.certificate``, alone decides a requirement's status. This adapter only builds its inputs: each
assessed requirement is a predicate of the review's standard (the requirements as stated, hashed), the artefact is the
working tree the review digested, and each check a requirement lists becomes one ``SourceRecord`` under the mapping
table's row for its provenance (``g6.evidence.mapping``). A ``stated`` check is the repository's test when its file
exists unchanged at the base commit, and the patch author's when the patch adds or changes it. Its negative control is
G6's own run of it at the base commit, where it failed, for a requirement that asks for new behaviour. Every position is
the order G6 observed things in: the check frozen in the base commit, the patch, the stated checks' run, the run at the
base commit, then the run of the checks it killed again on the patch (``patch_review._run_again``), the receipt the
issuer can count because its control came first. A structural, derived or proposed check, a template instance and a
model's result are leads under the table and never promoted.

A requirement to keep behaviour has no negative control in a review: the base commit's code is its known-good reference
(a positive control), and no run of the review seeds a defect for its checks to catch. Nothing stands in for one, so it
is not counted, and ``not_counted`` says so, as it says for every requirement the issuer does not count, in words.

The review's own ``status`` and ``outcome`` keep their meaning; ``assessment.requirement_evidence`` adds the issuer's
statement for each requirement beside them, with what the mapping refused and why. Nothing here decides anything.

Why the issuer does not count a requirement is given as its own codes (``codes``), read from its structured fields:
a caveat's receipts' own results, its grounding rule's reasons for each check (``grounding``) and the records its
ledger did not hold; never by reading the text of its gaps. A review with an integrity problem gives the issuer
nothing to count (``integrity``): the runs the review does not believe are no evidence for it either.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from g6.core.ledger import digest
from g6.evidence.certificate import (
    Artifact,
    Certificate,
    CertificateRefused,
    Control,
    MissionFacts,
    PredicateStatement,
    grounding,
)
from g6.evidence.mapping import SourceRecord, about, environment_sha256, issue_records
from g6.harness.patch_review import AT_BASE   # the review's run of the stated checks against the base commit's code

#: Who the review names as the artefact's producer, and as the author of a check the patch did not write.
PATCH_AUTHOR, REPOSITORY = "the patch's author", "the repository"
#: Ledger positions, in the order G6 observes them in a review.
FROZEN, ARTEFACT, RECEIPT, BASE_RUN, AGAIN = 0, 1, 2, 3, 4
_RESULT = {"passed": "pass", "failed": "fail"}
#: Why a requirement to keep behaviour is never counted in a review (the mapping table's ``stated`` row).
KEEPS = ("it keeps behaviour: the base commit's code is its known-good reference, not a control, and no run seeded a "
         "defect for its checks to catch, so none was shown able to fail")
#: Why nothing is counted when the review found an integrity problem (its codes follow).
INTEGRITY = "the review found an integrity problem, so none of its runs is read as evidence"
#: The codes ``codes`` gives besides the grounding rule's: a caveat check's failure or result that is none, and a record
#: the ledger did not hold.
ADVISORY, UNKNOWN, NOT_IN_LEDGER = "advisory_check_failed", "check_result_unknown", "not_in_ledger"


def standard_sha256(evaluated: Sequence[Mapping[str, Any]]) -> str:
    """The digest of the review's standard: each assessed requirement as stated (id, text, kind, provenance, checks)."""
    return digest([{"id": req["id"], "text": req["text"], "kind": req.get("kind", "requirement"),
                    "provenance": req.get("provenance", "stated"), "checks": [c["id"] for c in req["checks"]]}
                   for req in evaluated])


def _at_base(runs: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return next((dict(run.get("outcomes") or {}) for run in runs if run.get("check") == AT_BASE), {})


def records_for(evaluated: Sequence[Mapping[str, Any]], runs: Sequence[Mapping[str, Any]], *, artifact: str,
                base_commit: str, check_sha: Mapping[str, str], written: Callable[[str], bool],
                environment: Mapping[str, Any], again: Mapping[str, Any] | None = None,
                ) -> tuple[MissionFacts, Artifact, list[SourceRecord]]:
    """The issuer's mission facts and artefact for the review, and one record for each check a requirement lists, and
    one more for each check that ran again on the patch after the run at the base commit (``again``).

    ``check_sha``: each stated check's SHA-256 (its node id and its file's bytes; absent when G6 could not read it);
    ``written``: whether the patch adds or changes a check's file; ``environment``: the stated checks' run's record;
    ``again``: ``patch_review._run_again``'s result."""
    predicates = tuple(req["id"] for req in evaluated)
    standard = standard_sha256(evaluated)
    facts = MissionFacts(mission_sha=digest({"artifact": artifact, "base_commit": base_commit, "standard": standard}),
                         mission_standard_sha=standard, standard_sha=standard, predicates=predicates,
                         standard_predicates=predicates)
    at_base = _at_base(runs)
    again_verdicts = dict((again or {}).get("verdicts") or {})
    environment_digest = environment_sha256(environment)
    kinds = {req["id"]: req.get("kind", "requirement") for req in evaluated}
    named: dict[str, dict[str, Any]] = {}   # check id -> its record's parts, the requirements naming it gathered
    for req in evaluated:
        for found in req["checks"]:
            one = named.setdefault(found["id"], {"provenance": found.get("provenance", "stated"),
                                                 "verdict": found.get("verdict"), "requirements": []})
            one["requirements"].append(req["id"])
    records = []
    for check, one in named.items():
        mine = one["provenance"] == "stated" and written(check)
        sha = check_sha.get(check, "")
        controls = tuple(Control(f"base-commit:{check}#{rid}", sha, rid, "g6", BASE_RUN, True)
                         for rid in one["requirements"]
                         if kinds[rid] != "preserve" and at_base.get(check) == "failed" and sha)
        keeps = all(kinds[rid] == "preserve" for rid in one["requirements"]) and at_base.get(check) == "passed"
        common = dict(kind=one["provenance"], predicates=tuple(one["requirements"]), artefact_sha256=artifact,
                      producer=PATCH_AUTHOR, artefact_seq=ARTEFACT, standard_sha256=standard, check_id=check,
                      check_sha256=sha, author=PATCH_AUTHOR if mine else REPOSITORY,
                      frozen_seq=ARTEFACT if mine else FROZEN, environment_sha256=environment_digest,
                      scope=f"the tests {check} names, on the working tree under review", controls=controls,
                      positive_seq=BASE_RUN if keeps else None)
        records.append(SourceRecord(record_id=f"stated-run:{check}", result=_RESULT.get(str(one["verdict"]), "unknown"),
                                    seq=RECEIPT, **common))
        if one["provenance"] == "stated" and check in again_verdicts:
            records.append(SourceRecord(record_id=f"again:{check}", seq=AGAIN,
                                        result=_RESULT.get(str(again_verdicts[check]), "unknown"), **common))
    return facts, Artifact(artifact, PATCH_AUTHOR, ARTEFACT), records


def _not_counted(req: Mapping[str, Any], at_base: Mapping[str, str], again: Mapping[str, Any],
                 check_sha: Mapping[str, str]) -> str:
    """Why the issuer does not count ``req``, in words, from what the review ran ("" when the issuer's own gap is all
    there is to say): the first reason among its stated checks."""
    if req.get("kind") == "preserve":
        return KEEPS
    stated = [c for c in req["checks"] if c.get("provenance", "stated") == "stated"]
    if not req["checks"]:
        return "no check was stated for it"
    if not stated:
        return "its checks are of kinds that never count (a contract read from the files, a proposed check)"
    for found in stated:
        check, verdict, base = found["id"], found.get("verdict"), at_base.get(found["id"])
        if verdict == "failed":
            return ("its check failed on the patch, and the review runs nothing at the base commit after a problem, so "
                    "no run showed the check can fail")
        if verdict != "passed":
            return f"its check did not run to a pass on the patch ({verdict})"
        if not check_sha.get(check):
            return "G6 could not read its check's file to name the check's code"
        if base == "passed":
            return "its check passes at the base commit too, so no run showed it can fail on this requirement"
        if base is None:
            return ("its check was not run at the base commit (G6 runs it there only when nothing before found a "
                    "problem), so no run showed it can fail")
        if base != "failed":
            return f"its run at the base commit gave no failure G6 can read ({base})"
        said = (again.get("verdicts") or {}).get(check)
        if said is None:
            return "it could not run again on the patch after its control: " + str(again.get("why") or "not run")
        if said == "failed":
            return "run again on the patch after its control, its check failed"
        if said != "passed":
            return f"run again on the patch after its control, its check did not pass ({said})"
    return ""


def codes(certificate: Certificate, statement: PredicateStatement, artifact: Artifact) -> list[str]:
    """Why the issuer does not count ``statement``'s predicate, as the issuer's own codes read from its structured
    fields, never from the text of its gap: for each check it keeps as a caveat, its receipts' own results (a failure,
    ``ADVISORY``; a result that is none, ``UNKNOWN``); then each of the predicate's checks' reasons under the issuer's
    grounding rule (``grounding``); then ``NOT_IN_LEDGER`` when a record its support would rest on is not in the
    ledger. Empty when the predicate is counted, or refuted (the refutation says why)."""
    if statement.verified or statement.refute:
        return []
    mine = [check for check in certificate.checks if statement.predicate in check.predicates]
    results: dict[str, set[str]] = {}
    for receipt in certificate.receipts:
        results.setdefault(receipt.check_id, set()).add(receipt.result)
    found: list[str] = []
    for check_id in statement.caveats:
        found += [code for result, code in (("fail", ADVISORY), ("unknown", UNKNOWN))
                  if result in results.get(check_id, set())]
    for check in mine:
        found += grounding(check, artifact, statement.predicate)
    held = {("receipt", r.receipt_id) for r in certificate.receipts if any(r.check_id == c.check_id for c in mine)}
    held |= {("control", k.control_id) for c in mine for k in c.controls if k.predicate == statement.predicate}
    if held & set(certificate.unresolved):
        found.append(NOT_IN_LEDGER)
    return list(dict.fromkeys(found))


def assess(evaluated: Sequence[Mapping[str, Any]], runs: Sequence[Mapping[str, Any]], *, artifact: str,
           base_commit: str, check_sha: Mapping[str, str], written: Callable[[str], bool],
           environment: Mapping[str, Any], again: Mapping[str, Any] | None = None,
           integrity: Sequence[str] = ()) -> dict:
    """``assessment.requirement_evidence``: the issuer's certificate for the review, each requirement's statement from
    it with the issuer kinds of its checks and, when it is not counted, why (``not_counted`` in G6's words from what the
    review ran, and ``codes``, the issuer's own), each record the mapping refused with its codes, and the run again
    (``again``). Inputs the issuer refuses as a whole are said so, with its problems, and nothing is counted; the
    review's own fields stand. ``integrity``: the codes of the review's integrity problems; with any, no run is read as
    evidence, so no record is made, and each requirement says why."""
    again = dict(again or {})
    try:
        facts, artefact, records = records_for(evaluated, runs, artifact=artifact, base_commit=base_commit,
                                               check_sha=check_sha, written=written, environment=environment,
                                               again=again)
        if integrity:   # the review's own rule: a run it does not believe is no evidence
            records = []
        issued = issue_records(facts, artefact, records)
    except (CertificateRefused, ValueError, TypeError) as exc:   # CertificateRefused is a ValueError: named for readers
        return {"authority": "g6.evidence.certificate", "refused_by_issuer": str(exc), "requirements": [],
                "refused": []}
    certificate = issued.certificate
    kinds = {check.check_id: check.kind for check in certificate.checks}
    check_of = {rec.record_id: about(rec)[0].check_id for rec in records}
    refused = [{"check": check_of.get(rid, ""), "record": rid,
                "codes": [{"code": code, "missing": why} for code, why in found]} for rid, found in issued.refused]
    at_base = _at_base(runs)
    requirements = []
    for statement, req in zip(certificate.predicates, evaluated, strict=True):
        checks = [c["id"] for c in req["checks"]]
        found = {"requirement": statement.predicate, **statement.as_dict(), "checked": statement.verified,
                 "kinds": {check: kinds.get(check, "") for check in checks},
                 "gap": dict(certificate.gaps).get(f"predicate:{statement.predicate}", ""),
                 "codes": codes(certificate, statement, artefact)}
        if not statement.verified and not statement.refute:
            found["not_counted"] = (f"{INTEGRITY} ({', '.join(integrity)})" if integrity else
                                    _not_counted(req, at_base, again, check_sha))
        requirements.append(found)
    return {"authority": "g6.evidence.certificate", "state": certificate.state,
            "certificate": certificate.as_dict(), "requirements": requirements, "refused": refused,
            **({"again": again} if again else {}), **({"integrity": list(integrity)} if integrity else {})}


__all__ = ["ADVISORY", "AGAIN", "ARTEFACT", "AT_BASE", "BASE_RUN", "FROZEN", "INTEGRITY", "KEEPS", "NOT_IN_LEDGER",
           "PATCH_AUTHOR", "RECEIPT", "REPOSITORY", "UNKNOWN", "assess", "codes", "records_for", "standard_sha256"]
