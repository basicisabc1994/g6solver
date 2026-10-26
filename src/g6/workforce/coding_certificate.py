# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""A code change's certificate (Runtime Validator Constructor R6.3-R6.5), only with ``G6_WORKFORCE_CERTIFICATES`` on.

Controls (R6.3): once the standard is frozen and before any patch exists, G6 runs every frozen required test against the
unpatched baseline snapshot and records one ``control`` event per test: killed when the test failed there (the baseline
is the seeded defect: a code change starts from a failing test). A test that passed on the baseline is recorded unkilled,
and then (R6.7) G6 seeds defects of its own into the repository's code, the module the change may edit first, one at a
time (``mutation_check.mutants``, with a call on one argument dropped as well), runs the whole suite on a scratch copy of
the snapshot with each, and records a killed control for every such test the defect made fail. The claim is scoped (plan
gap 4): the test can fail on a defect in this code, not that it catches every defect. A test no seeded defect made fail,
within ``MAX_CONTROL_MUTANTS`` runs, stays a named gap; and nothing seeded is kept if the snapshot changed while they ran.
A code change has no known-good reference, so no check has a positive control: a failing test is a caveat, never a
refutation (the issuer's rule).

Receipts (R6.4): each test check's run records, per named test, its result on the patch and the digest of the frozen test
file that ran (the bench runs only test files byte-identical to the frozen snapshot, whose digest is checked at every
step; ``rungs.run_verify``). Only receipts on the patch's latest bytes are given to the issuer.

The close (R6.5): one predicate and one check per frozen required test (the check's code is its frozen file; its author
is the repository, or G6's constructor for a check the premortem constructed), frozen at the mission's ``standard`` event,
judged by ``certificates.issue``. ``decided`` applies founder ruling R6-P as the lead reads it: a verified certificate
lifts the qualified-draft caps; anything else closes at the stricter state. A blocked mission is never lifted, and a cap
the certificate does not cover (a degraded output, a check on the patch that did not pass, what a person must read about
an unchecked part of the change) is kept.

Evidence kinds (audit 010 WF-001/WF-002, DECISIONS #22): the repository's tests are the caller's own, and a constructed
check is task-derived, so under the library's default policy (``honest``) both enter the issuer as
``partial_falsifier``: they can refute a change and never support ``verified``. ``G6_WORKFORCE_CERTIFICATE_POLICY=
founder-d2`` is the owner's override of #22 (the archive's ruling D2): repository tests are then recorded
``independent_executable``; a constructed check never is. Their pass is read from a record the process that ran the
patch wrote, so a verified certificate on them names that gap beside it on the close and among the outcome's reasons
(``recorder_gap``, WORKFORCE-01): the override stands, and the gap is never silent.

Positions are the mission's events in record order (``MissionStore.events``: rowid order, never the racy ``seq``).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import Any

from g6.core import confine
from g6.core.ledger import ChainLedger, LedgerError
from g6.evidence import certificate as certificates
from g6.evidence.certificate import (
    Artifact,
    Certificate,
    Check,
    Control,
    MissionFacts,
    Receipt,
)
from g6.workforce import bench, mutation_check, truth
from g6.workforce.contracts import QUALIFIED, VERIFIED, Outcome, answer, rank

CONTROL = "control"
REPOSITORY = "repository"
CONSTRUCTOR = "g6:constructor"
#: The evidence kind of a check the caller or the task supplied: it can refute, never independently support.
PARTIAL = "partial_falsifier"
#: The kind the issuer's A4 rule accepts; given to repository tests only under the owner's ``founder-d2`` override.
INDEPENDENT = "independent_executable"
#: The one setting that chooses the policy, and its two values; anything else is ``honest`` (fails closed).
ENV_POLICY = "G6_WORKFORCE_CERTIFICATE_POLICY"
HONEST = "honest"
FOUNDER_D2 = "founder-d2"


def policy(env: dict | None = None) -> str:
    """The certificate policy in force: ``founder-d2`` only when the owner set exactly that, else ``honest``."""
    raw = str((os.environ if env is None else env).get(ENV_POLICY) or "").strip().lower()
    return FOUNDER_D2 if raw == FOUNDER_D2 else HONEST


def _kind(author: str) -> str:
    return INDEPENDENT if author == REPOSITORY and policy() == FOUNDER_D2 else PARTIAL


#: The most whole-suite runs with a seeded defect a mission's controls make (R6.7).
MAX_CONTROL_MUTANTS = 12
#: How long one such run may take; a run that does not report says nothing about any test.
CONTROL_TIMEOUT_S = 60
# COPY: The reason a code change closes verified on its certificate.
CERTIFIED = ("Verified: every frozen test passed on these exact bytes, and before any change was made G6 had seen each of "
             "them fail, on the unchanged code or on a defect G6 put into it.")
# COPY: The reason a code change's certificate holds its result below what the checks alone gave.
NOT_CERTIFIED = "G6's certificate for this change is {state}: {gaps}"


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def record_controls(wf, mission_id: str, standard: dict) -> None:
    """The negative controls (R6.3): each frozen required test not yet recorded, run on the unpatched baseline snapshot;
    one ``control`` event per test, killed when the test ran and failed. Idempotent per test; the caller runs it after
    the freeze and before any plan, so before any patch exists. A run stopped after the baseline seeds when resumed."""
    done = {e["detail"].get("check_id") for e in wf.missions.events(mission_id, kind=CONTROL)}
    todo = [node for node in standard["required_tests"] if node not in done]
    sha = wf.missions.acceptance(mission_id).sha256
    if todo:
        reply = bench.run("run_tests", {"repo": standard["snapshot"], "tests": todo}, venture=wf.venture_id)
        ran = answer(reply).get("tests")  # a refusal carries no answer: every control is then unexecuted, never killed
        ran = ran if isinstance(ran, dict) else {}
        confined = _confined(answer(reply))
        for node in todo:
            executed = isinstance(ran.get(node), bool)
            wf.missions.record_event(mission_id, CONTROL, actor="code", detail={
                "control_id": f"baseline:{node}", "check_id": node, "predicate": node,
                "check_sha": standard["test_digests"][node.split("::")[0]], "generated_by": certificates.G6,
                "target": "baseline", "baseline_digest": standard["baseline_digest"], "acceptance_sha256": sha,
                "executed": executed, "killed": executed and ran[node] is False, "confined": confined},
                idem_key=f"coding:control:{node}")
    if policy() == FOUNDER_D2:
        # audit 010 WF-006: under the honest policy a killed control can neither lead to support (the frozen tests are
        # partial falsifiers) nor to a refutation (no known-good reference), so the seeded runs would buy nothing
        _seeded_controls(wf, mission_id, standard, sha)


def caught(nodes: Collection[str], found: dict) -> set[str]:
    """The tests in ``nodes`` whose body ran and failed in a whole-suite run (``truth.run_suite``): a case of the test (a
    parametrized one) reported ``failed``. Review #1 (2026-10-15): a skip, an expected failure, a fixture error or a file
    that no longer imports ran none of the test's assertions, so it never counts as having seen the defect. A run that
    did not report says nothing, and a test it does not mention is never counted as caught."""
    if not found.get("ran"):
        return set()
    outcomes = found.get("outcomes") or {}
    return {node for node in nodes
            if any(outcome == "failed" for key, outcome in outcomes.items() if key.split("[", 1)[0] == node)}


def _run_mutant(snapshot: Path, relative: str, text: str) -> dict:
    """The whole suite on a scratch copy of ``snapshot`` with ``relative`` replaced by ``text``, in a folder G6 made (so a
    confined run may use it)."""
    with confine.run_folder(prefix="g6_control_") as scratch:
        copy = Path(scratch) / "repo"
        shutil.copytree(snapshot, copy, ignore=bench._IGNORED)
        (copy / relative).write_text(text, encoding="utf-8")
        return truth.run_suite(copy, CONTROL_TIMEOUT_S)


def _confined(found: Any) -> bool:
    """Whether a run's answer says its tests ran confined by the operating system (``g6.core.confine``)."""
    isolation = found.get("isolation") if isinstance(found, dict) else None
    return isinstance(isolation, dict) and bool(isolation.get("backend"))


def _sources(snapshot: Path, first: Collection[str]) -> list[str]:
    """The repository's code G6 may seed a defect into: the files the change may edit, then every other Python file that
    is not a test, in name order."""
    found = sorted(p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*.py")
                   if not bench._IGNORED_PART.intersection(p.parts)
                   and not bench._TEST_FILE.search(p.relative_to(snapshot).as_posix()))
    return [*(f for f in first if f in found), *(f for f in found if f not in first)]


def _seeded_controls(wf, mission_id: str, standard: dict, sha: str) -> None:
    """R6.7: the tests that passed on the baseline and have no killed control yet, against defects G6 seeds into the
    repository's code, one whole-suite run each, until each is caught or ``MAX_CONTROL_MUTANTS`` runs are spent."""
    events = wf.missions.events(mission_id, kind=CONTROL)
    passed = {e["detail"]["check_id"] for e in events if e["detail"].get("target") == "baseline"
              and e["detail"].get("executed") and not e["detail"].get("killed")}
    remaining = passed - {e["detail"]["check_id"] for e in events if e["detail"].get("killed")}
    if not remaining:
        return
    snapshot = bench._repo(standard["snapshot"], wf.venture_id)
    before = bench._digest(snapshot)
    kept, runs = [], 0
    for relative in _sources(snapshot, standard["permissions"]["files"]):
        if not remaining or runs >= MAX_CONTROL_MUTANTS:
            break
        for mutant in mutation_check.mutants((snapshot / relative).read_text(encoding="utf-8"), MAX_CONTROL_MUTANTS,
                                             calls=True):
            if not remaining or runs >= MAX_CONTROL_MUTANTS:
                break
            runs += 1
            found = _run_mutant(snapshot, relative, mutant.source)
            for node in sorted(caught(remaining, found)):
                kept.append((runs, node, relative, mutant, _confined(found)))
                remaining.discard(node)
    if bench._digest(snapshot) != before:  # a seeded run wrote outside its copy: nothing it reported is kept
        return
    for run, node, relative, mutant, confined in kept:
        wf.missions.record_event(mission_id, CONTROL, actor="code", detail={
            "confined": confined,
            "control_id": f"mutant:{run}:{node}", "check_id": node, "predicate": node,
            "check_sha": standard["test_digests"][node.split("::")[0]], "generated_by": certificates.G6,
            "target": "mutant", "mutant": {"file": relative, "line": mutant.line, "change": mutant.describe(),
                                           "sha256": hashlib.sha256(mutant.source.encode("utf-8")).hexdigest()},
            "baseline_digest": standard["baseline_digest"], "acceptance_sha256": sha,
            "executed": True, "killed": True}, idem_key=f"coding:control:mutant:{node}")


def certify(missions, ledger, mission_id: str) -> Certificate:
    """The certificate for the patch's latest bytes under the mission's frozen standard (``CertificateRefused`` when the
    records cannot support one: no frozen standard event or no patch)."""
    accepted = missions.acceptance(mission_id)
    standard = accepted.metadata["coding"]
    mission = missions.get(mission_id)
    events = missions.events(mission_id)
    at = {e["evidence_id"]: i for i, e in enumerate(events)}
    frozen = max((i for i, e in enumerate(events)
                  if e["kind"] == "standard" and e["detail"].get("acceptance_sha256") == accepted.sha256), default=None)
    latest = ledger.last_artifact(mission_id, "patch")
    if frozen is None or latest is None:
        raise certificates.CertificateRefused(["no frozen standard event" if frozen is None else "no patch to certify"])
    nodes = tuple(standard["required_tests"])
    constructed = {c["nodeid"] for c in (standard.get("harness_intelligence") or {}).get("checks") or []}
    controls: dict[str, list[Control]] = {}
    unconfined = set()   # tests a run with the owner's permissions (no operating-system isolation) touched: never support
    for i, e in enumerate(events):
        found = e["detail"]
        if e["kind"] == CONTROL and found.get("acceptance_sha256") == accepted.sha256:
            controls.setdefault(found["check_id"], []).append(Control(
                found["control_id"], found["check_sha"], found["predicate"], found["generated_by"], i,
                bool(found["killed"])))
            if found.get("confined") is not True:
                unconfined.add(found["check_id"])
    receipts = []
    for i, e in enumerate(events):
        if e["kind"] != "run.closed":
            continue
        for record in e["detail"].get("verification") or []:
            if record.get("judges") != "patch" or record.get("artifact_id") != latest["artifact_id"]:
                continue
            ran = [r for r in record.get("receipts") or [] if r.get("test") in nodes]
            receipts += [Receipt(f"{e['detail']['run_id']}:{r['test']}", r["test"], r["check_sha"],
                                 str(record.get("artifact_sha", "")), r["result"], i) for r in ran]
            # a test that ran with the owner's permissions (no operating-system isolation) can refute, never support
            unconfined.update(r["test"] for r in ran if r.get("confined") is not True)
    authors = {node: CONSTRUCTOR if node in constructed else REPOSITORY for node in nodes}
    checks = [Check(node, (node,), authors[node], PARTIAL if node in unconfined else _kind(authors[node]),
                    standard["test_digests"][node.split("::")[0]], frozen, tuple(controls.get(node, ())))
              for node in nodes]
    facts = MissionFacts(
        mission_sha=_sha([mission_id, accepted.sha256]), mission_standard_sha=accepted.sha256,
        standard_sha=accepted.sha256, predicates=tuple(r.name for r in accepted.requirements),
        standard_predicates=nodes, gates=_gates(missions, mission_id),
        cost_cents=mission["budget_cents"] if isinstance(mission["budget_cents"], int) else None)
    artifact = Artifact(latest["sha256"], f"worker:{latest['producer']}", at[latest["artifact_id"]])
    # EVIDENCE-01: the controls and receipts G6's runs recorded for this mission go to a ledger the issuer resolves
    # them in; a repeated id is refused, never resolved twice.
    observed = ChainLedger()
    try:
        certificates.record(observed, *(c for found in controls.values() for c in found), *receipts)
    except LedgerError as exc:
        raise certificates.CertificateRefused([str(exc)]) from None
    return certificates.issue(facts, artifact, checks, receipts, decisions=_approved(missions, mission_id, facts.gates),
                              ledger=observed)


def _gates(missions, mission_id: str) -> tuple[str, ...]:
    """The plan's gates: each a person's decision the certificate must carry or name as a gap (audit 010 WF-005)."""
    return tuple(u.key for u in missions.plan(mission_id)[1].units if u.kind == "gate")


def _approved(missions, mission_id: str, gates: tuple[str, ...]) -> tuple[str, ...]:
    """The gates whose latest question a person answered ``approve`` (a re-asked gate counts only its latest answer)."""
    from g6.workforce.decisions import Decisions  # decisions -> missions -> coding imports this module

    book = Decisions(missions)
    return tuple(g for g in gates if ((book.effective(mission_id, g)[1] or {}).get("decision") == "approve"))


def decided(missions, ledger, mission_id: str, outcome: Outcome, *, unchecked: tuple[str, ...]) -> tuple[Outcome, dict]:
    """The mission's outcome under its certificate (founder ruling R6-P, as the lead reads it) and what the close keeps
    (``{"certificate": ...}``, or ``{"certificate_refused": [...]}`` with the outcome unchanged)."""
    try:
        cert = certify(missions, ledger, mission_id)
    except certificates.CertificateRefused as refused:
        return outcome, {"certificate_refused": list(refused.problems)}
    all_passed = False
    if cert.state == VERIFIED:
        latest = ledger.last_artifact(mission_id, "patch")
        checks = {u.key for u in missions.plan(mission_id)[1].units if u.kind == "verify"}
        passed = {v["unit_key"] for v in ledger.verdicts(mission_id, "patch")
                  if v["artifact_id"] == latest["artifact_id"] and v["executed"] and v["result"] == "pass"}
        all_passed = checks <= passed
    gaps = "; ".join(subject for subject, _ in cert.gaps)[:300]
    found: dict[str, Any] = {"certificate": cert.as_dict()}
    recorded = recorder_gap(cert)
    if recorded:  # beside the issuer's certificate, whose fields its digest and verify() bind
        found["certificate_gaps"] = [["receipts", recorded]]
    return ruled(cert.state, outcome, unchecked=unchecked, all_passed=all_passed, gaps=gaps, named=recorded), found


def recorder_gap(cert: Certificate) -> str:
    """WORKFORCE-01: the gap a verified certificate carries when its support rests on the bench's per-test receipts
    (``independent_executable``, under ``founder-d2`` only), whose pass record the process that ran the patch wrote
    (``truth.RECORDER_GAP``); ``""`` for any other certificate."""
    return truth.RECORDER_GAP if cert.state == VERIFIED and any(kind == INDEPENDENT for *_, kind in cert.rerun) else ""


def ruled(cert_state: str, outcome: Outcome, *, unchecked: tuple[str, ...], all_passed: bool, gaps: str = "",
          named: str = "") -> Outcome:
    """The state rule (founder ruling R6-P, as the lead reads it). A verified certificate lifts a qualified-draft outcome
    to verified only when nothing outside the certificate is known to be wrong: the outcome is not degraded, no part of
    the change is disclosed as unchecked (founder 10-08), and every check on the latest bytes passed. A blocked outcome is
    never lifted. Any other certificate state only ever lowers the outcome, to the certificate's state. ``named``: a gap
    the certificate carries (``recorder_gap``), said among the lifted outcome's reasons."""
    if cert_state == VERIFIED:
        if outcome.state == QUALIFIED and not outcome.degraded and not unchecked and all_passed:
            return Outcome(VERIFIED, reasons=(CERTIFIED, *((named,) if named else ())), evidence=outcome.evidence,
                           source="certificate")
        return outcome
    if rank(cert_state) < rank(outcome.state):
        return outcome.demote(cert_state, NOT_CERTIFIED.format(state=cert_state, gaps=gaps))
    return outcome
