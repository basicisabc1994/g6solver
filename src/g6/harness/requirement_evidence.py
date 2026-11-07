# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The patch review's evidence in the issuer's terms (G6 0.3 design, slice 2): ``assessment.requirement_evidence``.

The issuer, ``g6.evidence.certificate``, alone decides a requirement's status. This adapter only builds its inputs: each
assessed requirement is a predicate of the review's standard (the requirements as stated, hashed), the artefact is the
working tree the review digested, and each check a requirement lists becomes one ``SourceRecord`` under the mapping
table's row for its provenance (``g6.evidence.mapping``). A ``stated`` check is the repository's test when its file
exists unchanged at the base commit, and the patch author's when the patch adds or changes it. Its negative control is
G6's own run of it at the base commit, where it failed, for a requirement that asks for new behaviour. Every position is
the order G6 observed things in: the check frozen in the base commit, the patch, the stated checks' run, each other run
of the review that collected a stated check on the patch before the run at the base commit (``observed``: the tests
that touch the changed modules, and their replays on the patch in a copy), the run at the base commit, then the run of
the checks it killed again on the patch (``patch_review._run_again``), the receipt the issuer can count because its
control came first. Every run of a stated check on the patch adds its receipt, in the order it ran, so a failure seen
between two passes reaches the issuer (review 2's finding 5). A structural, derived or proposed check, a template
instance and a model's result are leads under the table and never promoted.

Every id made from parts here (a record's, a control's) is ``g6.evidence.mapping.id_of`` of the parts: a structure,
never a joined string, so two pytest node ids can never give two checks or two requirements one id (finding 6).

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
from g6.evidence.mapping import SourceRecord, about, environment_sha256, id_of, issue_records
from g6.harness.patch_review import AT_BASE   # the review's run of the stated checks against the base commit's code

#: Who the review names as the artefact's producer, and as the author of a check the patch did not write.
PATCH_AUTHOR, REPOSITORY = "the patch's author", "the repository"
#: Ledger positions, in the order G6 observes them in a review: the freeze, the patch and the stated checks' run; the
#: runs after it come next, in the order they ran (``positions``).
FROZEN, ARTEFACT, RECEIPT = 0, 1, 2
_RESULT = {"passed": "pass", "failed": "fail"}
#: What each other run on the patch is called when a requirement says why it is not counted.
_RUNS = {"derived": "the run of the tests that touch the changed modules",
         "replay": "the replay of its test file on the patch, in a copy"}


def observed_runs(observed: Sequence[Mapping[str, Any]] | None) -> list[dict]:
    """The other runs of the stated checks on the patch, in the order they ran, each that observed one: its name
    (``run``, a list of parts) and each stated check's verdict in it (``verdicts``)."""
    found = []
    for one in observed or ():
        name = one.get("run")
        parts = list(name) if isinstance(name, (list, tuple)) else [str(name)]
        verdicts = {str(check): str(said) for check, said in dict(one.get("verdicts") or {}).items()}
        if verdicts:
            found.append({"run": parts, "verdicts": verdicts})
    return found


def positions(observed: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """The ledger positions of the run at the base commit and of the run again: after the stated checks' run and each
    of the ``observed`` runs (``observed_runs``), which came before the run at the base commit."""
    base_run = RECEIPT + 1 + len(observed)
    return base_run, base_run + 1


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
                observed: Sequence[Mapping[str, Any]] = (),
                ) -> tuple[MissionFacts, Artifact, list[SourceRecord]]:
    """The issuer's mission facts and artefact for the review, and one record for each check a requirement lists, one
    more for each other run on the patch that observed a stated check before the run at the base commit
    (``observed``), and one more for each check that ran again on the patch after that run (``again``).

    ``check_sha``: each stated check's SHA-256 (its node id and its file's bytes; absent when G6 could not read it);
    ``written``: whether the patch adds or changes a check's file; ``environment``: the stated checks' run's record;
    ``again``: ``patch_review._run_again``'s result; ``observed``: the review's other runs of its stated checks on the
    patch, in the order they ran (``observed_runs``)."""
    predicates = tuple(req["id"] for req in evaluated)
    standard = standard_sha256(evaluated)
    facts = MissionFacts(mission_sha=digest({"artifact": artifact, "base_commit": base_commit, "standard": standard}),
                         mission_standard_sha=standard, standard_sha=standard, predicates=predicates,
                         standard_predicates=predicates)
    at_base = _at_base(runs)
    again_verdicts = dict((again or {}).get("verdicts") or {})
    seen = observed_runs(observed)
    base_run, again_at = positions(seen)
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
        controls = tuple(Control(id_of("base-commit", check, rid), sha, rid, "g6", base_run, True)
                         for rid in one["requirements"]
                         if kinds[rid] != "preserve" and at_base.get(check) == "failed" and sha)
        keeps = all(kinds[rid] == "preserve" for rid in one["requirements"]) and at_base.get(check) == "passed"
        common = dict(kind=one["provenance"], predicates=tuple(one["requirements"]), artefact_sha256=artifact,
                      producer=PATCH_AUTHOR, artefact_seq=ARTEFACT, standard_sha256=standard, check_id=check,
                      check_sha256=sha, author=PATCH_AUTHOR if mine else REPOSITORY,
                      frozen_seq=ARTEFACT if mine else FROZEN, environment_sha256=environment_digest,
                      scope=f"the tests {check} names, on the working tree under review", controls=controls,
                      positive_seq=base_run if keeps else None)
        records.append(SourceRecord(record_id=id_of("stated-run", check),
                                    result=_RESULT.get(str(one["verdict"]), "unknown"), seq=RECEIPT, **common))
        if one["provenance"] != "stated":
            continue
        for at, run in enumerate(seen, start=RECEIPT + 1):   # each other run that observed it, in the order it ran
            if check in run["verdicts"]:
                records.append(SourceRecord(record_id=id_of(*run["run"], check), seq=at,
                                            result=_RESULT.get(run["verdicts"][check], "unknown"), **common))
        if check in again_verdicts:
            records.append(SourceRecord(record_id=id_of("again", check), seq=again_at,
                                        result=_RESULT.get(str(again_verdicts[check]), "unknown"), **common))
    return facts, Artifact(artifact, PATCH_AUTHOR, ARTEFACT), records


def _not_counted(req: Mapping[str, Any], at_base: Mapping[str, str], again: Mapping[str, Any],
                 check_sha: Mapping[str, str], observed: Sequence[Mapping[str, Any]] = ()) -> str:
    """Why the issuer does not count ``req``, in words, from what the review ran ("" when the issuer's own gap is all
    there is to say): the first reason among its stated checks. ``observed``: the other runs of its checks on the
    patch (``observed_runs``)."""
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
        other = next(((run, run["verdicts"][check]) for run in observed
                      if run["verdicts"].get(check, "passed") != "passed"), None)
        if other is not None:
            run, said = other
            return (f"its check passed on the patch, and in {_RUNS.get(run['run'][0], 'another run on the patch')} it "
                    f"did not ({said}), so a failure G6 saw stands")
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
           integrity: Sequence[str] = (), observed: Sequence[Mapping[str, Any]] = ()) -> dict:
    """``assessment.requirement_evidence``: the issuer's certificate for the review, each requirement's statement from
    it with the issuer kinds of its checks and, when it is not counted, why (``not_counted`` in G6's words from what the
    review ran, and ``codes``, the issuer's own), each record the mapping refused with its codes, the other runs of the
    stated checks on the patch (``observed``) and the run again (``again``). Inputs the issuer refuses as a whole are
    said so, with its problems, and nothing is counted; the review's own fields stand. ``integrity``: the codes of the
    review's integrity problems; with any, no run is read as evidence, so no record is made, and each requirement says
    why."""
    again = dict(again or {})
    seen = observed_runs(observed)
    try:
        facts, artefact, records = records_for(evaluated, runs, artifact=artifact, base_commit=base_commit,
                                               check_sha=check_sha, written=written, environment=environment,
                                               again=again, observed=seen)
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
                                    _not_counted(req, at_base, again, check_sha, seen))
        requirements.append(found)
    return {"authority": "g6.evidence.certificate", "state": certificate.state,
            "certificate": certificate.as_dict(), "requirements": requirements, "refused": refused,
            **({"observed": seen} if seen else {}), **({"again": again} if again else {}),
            **({"integrity": list(integrity)} if integrity else {})}


#: The ledger positions of a confirmed expectation's runs in its own certificate: frozen when the person confirmed it
#: (G6 held it before the review read the tree), the artefact, the run on the patch, the run at the base commit (its
#: control), and the run again on the patch.
CONFIRMED_BASE, CONFIRMED_AGAIN = RECEIPT + 1, RECEIPT + 2


def _confirmed_not_counted(found: Mapping[str, Any]) -> str:
    """Why the issuer does not count a confirmed expectation, in words, from what the review ran."""
    runs = found.get("runs") or {}
    if found.get("bound") is not True:
        return ("its runs were not bound to the artefact: G6 ran them without the review's snapshot of the tree, so "
                "nothing shows they ran on the bytes the artifact digest names")
    if found.get("result") != "passed":
        return f"its check did not pass on the patch ({found.get('result')}: {found.get('detail', '')})"[:300]
    base = (runs.get("base") or {}).get("result")
    if base is None:
        return "its check was not run at the base commit, so no run showed it can fail"
    if base == "passed":
        return ("its check passes at the base commit too, so no run showed it can fail: a confirmed case the base "
                "commit already has keeps behaviour, and has no control in a review")
    if not found.get("killed"):
        return f"its run at the base commit gave no failure G6 can read ({base})"
    again = (runs.get("again") or {}).get("result")
    if again is None:
        return "it could not run again on the patch after its control"
    return "" if again == "passed" else f"run again on the patch after its control, it did not pass ({again})"


def confirmed_evidence(standard: Any, results: Sequence[Mapping[str, Any]], *, artifact: str, base_commit: str,
                       environment: Mapping[str, Any], integrity: Sequence[str] = ()) -> dict:
    """The issuer's statement for each confirmed expectation the review enforced (slice 3, item 5), in its own
    certificate under the expectation standard (``ExpectationStandard``): each is a predicate of that standard, its
    check the example.v1 instance compiled from the person's answer (row ``person_confirmed``: its author is the person,
    never the patch's producer). Each run on the patch is a receipt, in the order it ran; the run at the base commit,
    where the check failed, is its control. ``results``: ``confirmed_checks.run``'s, in the standard's order. The
    artefact digest reaches the issuer only for a result whose runs on the patch were bound to that artefact's bytes
    (``bound``: the executor refused any copy that was not the tree the review snapshot); for any other, nothing is
    counted. With an integrity problem, nothing is counted, as for the review's own requirements. The statement names
    the standard it used: its digest and its revision."""
    from g6.harness.confirmed_expectation import PERSON_CONFIRMED

    facts = standard.facts(digest({"artifact": artifact, "base_commit": base_commit, "standard": standard.sha256}))
    environment_digest = environment_sha256(environment)
    records: list[SourceRecord] = []
    for one, found in zip(standard.expectations, results, strict=True):
        ident = one.expectation_id
        runs = (found.get("runs") or {}) if found.get("bound") is True else {}   # only runs bound to the artefact
        controls = (Control(id_of("base-commit", ident), one.check_sha256, ident, "g6", CONFIRMED_BASE, True),) if (
            found.get("killed") is True) else ()   # the runner's one rule for a control (confirmed_checks)
        common = dict(kind=PERSON_CONFIRMED, predicates=(ident,), artefact_sha256=artifact, producer=PATCH_AUTHOR,
                      artefact_seq=ARTEFACT, standard_sha256=standard.sha256, check_id=ident,
                      check_sha256=one.check_sha256, author=one.answered_by, frozen_seq=FROZEN,
                      environment_sha256=environment_digest, controls=controls,
                      scope=f"the case the person confirmed ({one.scope}), on the working tree under review")
        for name, seq in (("patch", RECEIPT), ("again", CONFIRMED_AGAIN)):
            if name in runs:
                records.append(SourceRecord(record_id=id_of("confirmed", name, ident), seq=seq,
                                            result=_RESULT.get(str(runs[name].get("result")), "unknown"), **common))
    if integrity:
        records = []
    try:
        issued = issue_records(facts, Artifact(artifact, PATCH_AUTHOR, ARTEFACT), records)
    except (CertificateRefused, ValueError, TypeError) as exc:
        return {"authority": "g6.evidence.certificate", "standard_sha256": standard.sha256,
                "standard_revision": standard.revision, "refused_by_issuer": str(exc), "expectations": [],
                "refused": []}
    certificate = issued.certificate
    artefact = Artifact(artifact, PATCH_AUTHOR, ARTEFACT)
    expectations = []
    for statement, found in zip(certificate.predicates, results, strict=True):
        item = {"expectation": statement.predicate, **statement.as_dict(), "checked": statement.verified,
                "gap": dict(certificate.gaps).get(f"predicate:{statement.predicate}", ""),
                "codes": codes(certificate, statement, artefact)}
        if not statement.verified and not statement.refute:
            item["not_counted"] = (f"{INTEGRITY} ({', '.join(integrity)})" if integrity else
                                   _confirmed_not_counted(found))
        expectations.append(item)
    return {"authority": "g6.evidence.certificate", "standard_sha256": standard.sha256,
            "standard_revision": standard.revision, "state": certificate.state,
            "certificate": certificate.as_dict(), "expectations": expectations,
            "refused": [{"record": rid, "codes": [{"code": code, "missing": why} for code, why in found]}
                        for rid, found in issued.refused]}


__all__ = ["ADVISORY", "ARTEFACT", "AT_BASE", "CONFIRMED_AGAIN", "CONFIRMED_BASE", "FROZEN", "INTEGRITY", "KEEPS",
           "NOT_IN_LEDGER", "PATCH_AUTHOR", "RECEIPT", "REPOSITORY", "UNKNOWN", "assess", "codes", "confirmed_evidence",
           "observed_runs", "positions", "records_for", "standard_sha256"]
