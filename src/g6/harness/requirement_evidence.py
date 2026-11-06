# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The patch review's evidence in the issuer's terms (G6 0.3 design, slice 2): ``assessment.requirement_evidence``.

The issuer, ``g6.evidence.certificate``, alone decides a requirement's status. This adapter only builds its inputs: each
assessed requirement is a predicate of the review's standard (the requirements as stated, hashed), the artefact is the
working tree the review digested, and each check a requirement lists becomes one ``SourceRecord`` under the mapping
table's row for its provenance (``g6.evidence.mapping``). A ``stated`` check is the repository's test when its file
exists unchanged at the base commit, and the patch author's when the patch adds or changes it. Its negative control is
G6's own run of it at the base commit, where it failed, for a requirement that asks for new behaviour; for one that keeps
behaviour, a pass at the base commit is its known-good reference. Every position is the order G6 observed things in: the
check frozen in the base commit, the patch, the stated checks' run, then the run at the base commit. A structural,
derived or proposed check, a template instance and a model's result are leads under the table and never promoted.

The review's own ``status`` and ``outcome`` keep their meaning; ``assessment.requirement_evidence`` adds the issuer's
statement for each requirement beside them, with what the mapping refused and why. Nothing here decides anything.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from g6.core.ledger import digest
from g6.evidence.certificate import Artifact, CertificateRefused, Control, MissionFacts
from g6.evidence.mapping import SourceRecord, environment_sha256, issue_records
from g6.harness.patch_review import AT_BASE   # the review's run of the stated checks against the base commit's code

#: Who the review names as the artefact's producer, and as the author of a check the patch did not write.
PATCH_AUTHOR, REPOSITORY = "the patch's author", "the repository"
#: Ledger positions, in the order G6 observes them in a review.
FROZEN, ARTEFACT, RECEIPT, BASE_RUN = 0, 1, 2, 3
_RESULT = {"passed": "pass", "failed": "fail"}


def standard_sha256(evaluated: Sequence[Mapping[str, Any]]) -> str:
    """The digest of the review's standard: each assessed requirement as stated (id, text, kind, provenance, checks)."""
    return digest([{"id": req["id"], "text": req["text"], "kind": req.get("kind", "requirement"),
                    "provenance": req.get("provenance", "stated"), "checks": [c["id"] for c in req["checks"]]}
                   for req in evaluated])


def records_for(evaluated: Sequence[Mapping[str, Any]], runs: Sequence[Mapping[str, Any]], *, artifact: str,
                base_commit: str, check_sha: Mapping[str, str], written: Callable[[str], bool],
                environment: Mapping[str, Any]) -> tuple[MissionFacts, Artifact, list[SourceRecord]]:
    """The issuer's mission facts and artefact for the review, and one record for each check a requirement lists.

    ``check_sha``: each stated check's SHA-256 (its node id and its file's bytes; absent when G6 could not read it);
    ``written``: whether the patch adds or changes a check's file; ``environment``: the stated checks' run's record."""
    predicates = tuple(req["id"] for req in evaluated)
    standard = standard_sha256(evaluated)
    facts = MissionFacts(mission_sha=digest({"artifact": artifact, "base_commit": base_commit, "standard": standard}),
                         mission_standard_sha=standard, standard_sha=standard, predicates=predicates,
                         standard_predicates=predicates)
    at_base = next((dict(run.get("outcomes") or {}) for run in runs if run.get("check") == AT_BASE), {})
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
        records.append(SourceRecord(
            kind=one["provenance"], record_id=f"stated-run:{check}", predicates=tuple(one["requirements"]),
            result=_RESULT.get(str(one["verdict"]), "unknown"), artefact_sha256=artifact, producer=PATCH_AUTHOR,
            artefact_seq=ARTEFACT, standard_sha256=standard, check_id=check, check_sha256=sha,
            author=PATCH_AUTHOR if mine else REPOSITORY, frozen_seq=ARTEFACT if mine else FROZEN,
            environment_sha256=environment_digest, seq=RECEIPT,
            scope=f"the tests {check} names, on the working tree under review", controls=controls,
            positive_seq=BASE_RUN if keeps else None))
    return facts, Artifact(artifact, PATCH_AUTHOR, ARTEFACT), records


def assess(evaluated: Sequence[Mapping[str, Any]], runs: Sequence[Mapping[str, Any]], *, artifact: str,
           base_commit: str, check_sha: Mapping[str, str], written: Callable[[str], bool],
           environment: Mapping[str, Any]) -> dict:
    """``assessment.requirement_evidence``: the issuer's certificate for the review, each requirement's statement from
    it with the issuer kinds of its checks, and each record the mapping refused with its codes. Inputs the issuer
    refuses as a whole are said so, with its problems, and nothing is counted; the review's own fields stand."""
    try:
        facts, artefact, records = records_for(evaluated, runs, artifact=artifact, base_commit=base_commit,
                                               check_sha=check_sha, written=written, environment=environment)
        issued = issue_records(facts, artefact, records)
    except (CertificateRefused, ValueError, TypeError) as exc:   # CertificateRefused is a ValueError: named for readers
        return {"authority": "g6.evidence.certificate", "refused_by_issuer": str(exc), "requirements": [],
                "refused": []}
    kinds = {rid.removeprefix("stated-run:"): kind for rid, _source, kind in issued.promoted}
    refused = [{"check": rid.removeprefix("stated-run:"), "codes": [{"code": code, "missing": why}
                                                                  for code, why in codes]}
               for rid, codes in issued.refused]
    requirements = []
    for statement, req in zip(issued.certificate.predicates, evaluated, strict=True):
        checks = [c["id"] for c in req["checks"]]
        requirements.append({
            "requirement": statement.predicate, **statement.as_dict(), "checked": statement.verified,
            "kinds": {check: kinds.get(check, "") for check in checks},
            "gap": dict(issued.certificate.gaps).get(f"predicate:{statement.predicate}", "")})
    return {"authority": "g6.evidence.certificate", "state": issued.certificate.state,
            "certificate": issued.certificate.as_dict(), "requirements": requirements, "refused": refused}


__all__ = ["ARTEFACT", "AT_BASE", "BASE_RUN", "FROZEN", "PATCH_AUTHOR", "RECEIPT", "REPOSITORY", "assess",
           "records_for", "standard_sha256"]
