# SPDX-License-Identifier: Apache-2.0
"""Reference application: assess an AI-written Python patch against stated requirements, with the evidence for each.

``review_patch`` gives exactly one technical outcome, decided in this order:

1. ``test_integrity_compromised``: a changed test weakened (a moved test file is compared with its old text), a
   changed test-runner file (``.pytest.ini`` included), a run that changed tracked files or whose result cannot be
   believed, the repository changing during the review, or the coding gate finding a shortcut;
2. ``defect_detected``: a stated check or extra test failed, failed to collect or timed out; or a test that touches a
   changed module fails on the patch and passed at the base commit. One that fails at both is pre-existing: an unknown;
3. ``insufficient_evidence``: no requirement stated, a requirement with no stated check, a stated check that did not
   run (missing, not collected, skipped, deselected), no test ran, or no change against the base;
4. ``supported_within_scope``: otherwise. Every requirement passed its stated checks and integrity is intact. That is
   support on the checks named, never a claim that the patch does everything the task needs.

Each requirement reads ``passed_on_stated_checks``, ``failed`` or ``not_established`` (with a reason and a suggested
test in words); G6 never invents a passing test. Checks adapt to cost: tier 1 (always) is the digest, integrity, and
the stated checks with the extra tests in one confined run; tier 2, the tests that touch the changed modules, runs
only when tier 1 found no integrity problem and no defect; tier 3 (mutation, old-against-new probes) is not run here.

Every outcome is ``qualified-draft``. A refusal (invalid input or repository, a git error, a stale artifact digest, a
run no operating-system boundary confined) is ``blocked-escalated`` with no outcome, and is never a detection.
Merging is a separate decision: action gating is not consulted, and nothing here merges code. Supplied sources stay
caller assertions; their lexical support is reported and decides nothing.
"""
from __future__ import annotations

import platform
import time
import uuid
from dataclasses import replace
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.core import component_process
from g6.core.ai_block import AIBlock
from g6.core.block_contract import BlockContract
from g6.core.compose import Stage, compose
from g6.core.door import blocked_reply
from g6.core.errors import ISOLATION_UNAVAILABLE
from g6.core.ledger import digest
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.core.result import Result
from g6.workforce import repo_tests, verify

SUPPORTED, DEFECT = "supported_within_scope", "defect_detected"
INTEGRITY, INSUFFICIENT = "test_integrity_compromised", "insufficient_evidence"
PASSED_ON_CHECKS, FAILED, NOT_ESTABLISHED = "passed_on_stated_checks", "failed", "not_established"
CODE = "G6_E_PATCH_REVIEW"
#: Files that configure, or could stand in for, the runner: ``repo_tests``'s list and pytest's hidden config file.
RUNNER_FILES = repo_tests.RUNNER_FILES | {".pytest.ini"}
#: Run verdicts whose results are not believed: an integrity problem, never evidence.
UNTRUSTED = frozenset({repo_tests.CHANGED, "inconsistent_report", "incomplete"})
#: A check's verdicts that are a defect when it is stated or extra; every other verdict but "passed" did not run.
DEFECTS = frozenset({repo_tests.FAILED, "collection_error", "timed_out"})
#: Coding gate findings about the checks themselves: they change the outcome. Every other finding is a merge reason.
INTEGRITY_FINDINGS = frozenset({"shortcut_in_answer", "shortcut_in_diff"})
SPECIFICATION_NOTE = ("The task's specification may be incomplete: G6 checks the requirements stated here, and it "
                      "cannot rule out in general that the task needs something no requirement states.")
MERGE_REASON = ("G6 does not authorise merging. This assessment reports evidence from the stated checks; merging is a "
                "separate decision that needs its own approval.")
NOT_CHECKED = ("behaviour no stated check exercises", "requirements the task needs but does not state",
               "whether the tests are independent of the patch's author", "source freshness",
               "benefit relative to a baseline", "merge authorisation")
TIER_3 = "tier 3 not run: mutation and old-against-new probes are not part of this version"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)
    checks: list[str] = Field(default_factory=list, max_length=200)


class GateAdapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: str
    task: str
    claim: str
    test_run: dict


class GateAdapter(AIBlock):
    """Reviewed mapping from G6's own test run to the coding gate's declared evidence."""
    __g6_block_contract__ = BlockContract(
        name="patch_gate_adapter", inputs_required=("task", "claim", "test_run"),
        outputs=("task", "answer", "task_contract", "evidence", "verifier"),
        verification_method="unit_tests_only",
        guarantees=("does not convert source support into test evidence", "a run that did not happen is not evidence"))

    def __init__(self):
        super().__init__("patch_gate_adapter")

    def infer(self, data: GateAdapterInput) -> Result[dict]:
        ran = data.test_run
        mapped = {"task": data.task, "answer": data.claim, "task_contract": data.task,
                  "completion_state": "qualified-draft"}
        if not ran.get("verdict"):
            return Result.ok({**mapped, "evidence": [], "verifier": {"status": "not_run"}})
        passed = ran.get("verdict") == "passed" and ran.get("passed", 0) > 0 and not ran.get("degraded")
        # The generic gate door accepts only declared evidence; the reply keeps the host-observed runs itself.
        return Result.ok({**mapped, "evidence": [{"kind": "test", "ref": ran.get("command", "pytest"),
                                                  "exit_code": 0 if passed else 1}],
                          "verifier": {"status": "pass" if passed else "fail", "exit_code": 0 if passed else 1}})


class _Refused(repo_tests.RepoRefused):
    pass


def _execute(root: Path, ids: list[str], *, timeout_s: int, change: repo_tests.Change | None = None) -> dict:
    """Every test run of a review. In place on the patched tree (``repo_tests.run``); or, given ``change``, each test
    file of ``ids`` replayed at the base commit and then on the patch, in a copy (``repo_tests.run_before``), as
    ``{file: result}``: a file that did not pass at the base commit reads ``not_run``."""
    if change is not None:
        texts = {rel: (root / rel).read_text(encoding="utf-8", errors="replace") for rel in ids}
        return repo_tests.run_before(replace(change, before=texts), timeout_s=timeout_s)
    # Receipts plug in here: pass receipt=True, and _record keeps ran["receipt"] beside each check run.
    return repo_tests.run(root, ids, timeout_s=timeout_s)


def _confined(ran: dict) -> dict:
    """``ran``, or a refusal when no operating-system boundary confined it: its results are never assessed."""
    isolation = ran.get("isolation") or {}
    if "isolation" in ran and not isolation.get("backend"):
        raise _Refused(ISOLATION_UNAVAILABLE, "G6 assesses a patch only from test runs inside an operating-system "
                       f"boundary, and none confined this one ({isolation.get('why') or 'no backend'}), so its results "
                       "are not used. Run g6 doctor: it says what this computer needs for a boundary.")
    return ran


def _inputs(task, claim, tests, sources, timeout_s, requirements) -> list[Requirement]:
    def bad(message: str) -> _Refused:
        return _Refused("G6_E_INPUT_VALIDATION", message)

    if not isinstance(task, str) or not task.strip():
        raise bad("task must say what the patch is for")
    if not isinstance(claim, str):
        raise bad("claim must be text")
    if not isinstance(tests, (list, tuple)) or not all(isinstance(t, str) for t in tests):
        raise bad("tests must be a list of pytest node ids")
    if sources is not None and not (isinstance(sources, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in sources.items())):
        raise bad("sources must map source names to text")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) or not 1 <= timeout_s <= repo_tests.MAX_TIMEOUT_S:
        raise bad(f"timeout_s must be a whole number of seconds from 1 to {repo_tests.MAX_TIMEOUT_S}")
    if not isinstance(requirements, (list, tuple)):
        raise bad("requirements must be a list of {id, text, checks}")
    try:
        stated = [Requirement.model_validate(item) for item in requirements]
    except ValidationError as exc:
        raise bad(f"each requirement is {{id, text, checks}}: {exc.errors()[0]['msg']}") from exc
    if len({r.id for r in stated}) != len(stated):
        raise bad("requirement ids must be unique")
    return stated


def _covers(check: str, node: str) -> bool:
    check = check.rstrip("/")
    return node == check or node.startswith((check + "::", check + "[", check + "/"))


def _node(phases: dict, failed: bool) -> str:
    states = set(phases.values())
    if failed or "failed" in states:
        return "failed"
    if "skipped" in states:
        return "skipped"
    return "passed" if phases.get("call") == "passed" else "not_run"


def _not_found(check: str, output: str) -> bool:
    """Whether pytest said it found no test named ``check`` (it prints the path it was given, or the full path)."""
    for line in output.replace("\\", "/").splitlines():
        _, sep, rest = line.partition("not found: ")
        if sep and (rest.strip() == check or rest.strip().endswith("/" + check)):
            return True
    return False


def _verdicts(ids: list[str], ran: dict) -> dict[str, dict]:
    """Each check's own verdict in one run, read from pytest's per-test outcomes."""
    run, said, output = ran["verdict"], ran.get("said") or "", ran.get("output") or ""
    outcomes = ran.get("test_outcomes") or {}
    failed = set(ran.get("failed_ids") or []) | set(ran.get("setup_errors") or {})
    omitted = set(ran.get("omitted_ids") or [])
    missing = {check for check in ids if _not_found(check, output)}
    found: dict[str, dict] = {}
    for check in ids:
        nodes = {node: _node(phases, node in failed) for node, phases in outcomes.items() if _covers(check, node)}
        dropped = [node for node in omitted if _covers(check, node)]
        states = set(nodes.values())
        if run in UNTRUSTED:
            verdict, detail = run, said or "the run's results are not believed"
        elif "failed" in states:
            verdict, detail = FAILED, "failed: " + ", ".join(sorted(n for n, s in nodes.items() if s == "failed")[:5])
        elif "skipped" in states:
            verdict, detail = "skipped", f"{sum(s == 'skipped' for s in nodes.values())} of {len(nodes)} skipped"
        elif dropped:
            verdict, detail = "deselected", f"{len(dropped)} test(s) deselected by the repository's configuration"
        elif states == {"passed"}:
            verdict, detail = "passed", f"{len(nodes)} test(s) passed"
        elif nodes:
            verdict, detail = ("timed_out", said) if run == "timed_out" else ("not_run", "started, never finished")
        elif check in missing:
            verdict, detail = "not_collected", "pytest found no test with this id"
        elif missing:
            verdict, detail = "not_run", "the run stopped first: another stated check names a test pytest cannot find"
        elif run in ("collection_error", repo_tests.FAILED, "timed_out"):
            verdict = "timed_out" if run == "timed_out" else "collection_error"
            detail = said or "the tests could not be collected"
        elif run in repo_tests.RAN or run == "no_tests_collected":
            verdict, detail = "not_collected", "no test with this id ran"
        else:
            verdict, detail = run, said
        found[check] = {"verdict": verdict, "detail": detail}
    return found


def _record(tier: int, kind: str, ids: list[str], ran: dict, command: str = "") -> dict:
    return {"tier": tier, "check": kind, "command": command or "pytest " + " ".join(ids), "tests": list(ids),
            "verdict": ran.get("verdict"), "seconds": ran.get("seconds", 0.0), "passed": ran.get("passed", 0),
            "failed": ran.get("failed", 0), "skipped": ran.get("not_passed", 0), "said": ran.get("said", "")}


def _requirement(req: Requirement, verdicts: dict[str, dict], norm: dict[str, str]) -> dict:
    checks = [{"id": check, **verdicts[check]} for check in dict.fromkeys(norm[raw] for raw in req.checks)]
    out = {"id": req.id, "text": req.text, "checks": checks}
    failing = [c["id"] for c in checks if c["verdict"] in DEFECTS]
    unrun = [f"{c['id']} ({c['verdict']})" for c in checks if c["verdict"] != "passed" and c["id"] not in failing]
    if not checks:
        return {**out, "status": NOT_ESTABLISHED, "reason": "no check was stated for this requirement",
                "suggested_test": "a test that runs the changed code the way this requirement describes and asserts "
                                  f"its result: {req.text}"}
    if failing:
        return {**out, "status": FAILED, "reason": "failed on its stated checks: " + ", ".join(failing)}
    if unrun:
        return {**out, "status": NOT_ESTABLISHED, "reason": "stated checks did not run to a pass: " + ", ".join(unrun),
                "suggested_test": "make those checks run here, or add a test that runs the changed code the way this "
                                  f"requirement describes and asserts its result: {req.text}"}
    return {**out, "status": PASSED_ON_CHECKS, "reason": "passed on its stated checks: " + ", ".join(
        c["id"] for c in checks)}


def _gate(task: str, claim: str, ran: dict) -> dict:
    flow = compose(
        Stage(GateAdapter(), "adapt", inputs={"task": "caller.task", "claim": "caller.claim",
                                              "test_run": "caller.test_run"}),
        Stage("coding_agent_gate", "verify_answer", inputs={key: f"0.{key}" for key in
              ("task", "answer", "task_contract", "evidence", "verifier")}),
        caller_inputs=("task", "claim", "test_run"))
    composition = flow.infer({"task": task, "claim": claim, "test_run": ran}).value
    found = composition.get("result") or {}
    return {"verdict": found.get("verdict"), "completion_state": composition["completion_state"],
            "findings": list(found.get("findings") or []), "warning_card": composition.get("warning_card"),
            "assurance": composition["assurance"]}


def _grounding(claim: str, sources: dict[str, str] | None) -> dict:
    if not sources:
        return {"status": "not run", "reason": "no sources were given", "decides_outcome": False}
    named = {name: {"digest": digest(text), "authority": "caller-supplied", "freshness": "not established"}
             for name, text in sources.items()}
    if not claim.strip():
        return {"status": "not run", "reason": "no claim was given", "sources": named, "decides_outcome": False}
    flow = compose(Stage("grounding", "check_claims"), caller_inputs=("text", "sources"))
    got = flow.infer({"text": claim, "sources": sources}).value
    return {"status": "run" if got.get("result") else "not run", "completion_state": got["completion_state"],
            "checks": (got.get("result") or {}).get("checks", []), "warning_card": got.get("warning_card"),
            "sources": named, "decides_outcome": False,
            "note": "lexical support against caller-supplied text, not entailment; it does not decide the outcome"}


def review_patch(repo: str | Path, *, task: str, requirements: list[dict], tests: list[str] | tuple[str, ...] = (),
                 claim: str = "", sources: dict[str, str] | None = None, base: str = "HEAD",
                 expected_artifact_digest: str = "", timeout_s: int = 300) -> dict:
    """Assess the working tree's patch against ``base`` on the stated requirements (``[{id, text, checks}]``, checks
    being pytest node ids) and the extra ``tests``. Never accepts claimed test results; never authorises a merge.

    Optional ``expected_artifact_digest`` pins a prior snapshot; a changed one is refused (``G6_E_PATCH_STALE``).
    """
    started = time.monotonic()
    try:
        stated = _inputs(task, claim, tests, sources, timeout_s, requirements)
        root = repo_tests.checked_repo(repo)
        before = repo_tests.snapshot(root)
        artifact = digest(before)
        if expected_artifact_digest and artifact != expected_artifact_digest:
            return blocked_reply("[G6_E_PATCH_STALE] working tree differs from the supplied artifact digest")
        change = repo_tests.change(root, base)
        norm = {raw: repo_tests.inside(root, raw, "test") for req in stated for raw in req.checks}
        norm.update({raw: repo_tests.inside(root, raw, "test") for raw in tests})
        stated_ids = list(dict.fromkeys(norm[raw] for req in stated for raw in req.checks))
        ids = list(dict.fromkeys([*stated_ids, *(norm[raw] for raw in tests)]))
        return _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources,
                       deadline=started + timeout_s, started=started)
    except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
        raise
    except repo_tests.RepoRefused as exc:
        return blocked_reply(f"[{exc.code}] {exc}")
    except (OSError, ValueError, TypeError) as exc:
        return blocked_reply(f"[{CODE}] {exc}")


def _left(deadline: float) -> int:
    return int(deadline - time.monotonic())


def _untrusted(tier: int, ran: dict) -> str:
    said = ran.get("said") or ""
    changed = ", ".join(ran.get("changed") or [])
    return f"tier {tier} run: {ran['verdict']}" + (f": {said}" if said else "") + (f" ({changed})" if changed else "")


def _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources, *, deadline,
            started) -> dict:
    reasons: list[dict] = []
    unknowns: list[str] = []
    runs: list[dict] = []
    skipped: list[dict] = []

    def reason(stage: str, code: str, detail: str) -> None:
        reasons.append({"stage": stage, "code": code, "detail": detail})

    # Integrity, before anything runs: a moved test file is compared with its old text, never read as deleted.
    for finding in verify.weakened_tests({change.renamed.get(rel, rel): text for rel, text in change.before.items()},
                                         change.after):
        reason("integrity", "test_weakened", finding)
    runner = sorted(set(change.runner_files) | {rel for rel in change.files
                                                if PurePosixPath(rel).name.lower() in RUNNER_FILES})
    if runner:
        reason("integrity", "runner_configuration_changed", ", ".join(runner))
    if not change.files:
        reason("evidence", "no_change", f"the working tree shows no change against {change.base}")

    # Tier 1: the stated checks and the extra tests, in one confined run.
    present = [i for i in ids if (root / i.partition("::")[0]).exists()]
    verdicts = {i: {"verdict": "missing", "detail": "no such file on the patched tree"} for i in ids
                if i not in present}
    first: dict = {}
    if present:
        first = _confined(_execute(root, present, timeout_s=max(1, _left(deadline))))
        runs.append(_record(1, "stated checks and extra tests", present, first))
        verdicts.update(_verdicts(present, first))
        if first["verdict"] in UNTRUSTED:
            reason("integrity", "run_not_trusted", _untrusted(1, first))
    else:
        skipped.append({"tier": 1, "check": "stated checks and extra tests",
                        "reason": "no check was stated" if not ids else "no stated check names a file on the tree"})
    for check in ids:
        if verdicts[check]["verdict"] in DEFECTS:
            reason("tier_1", "check_failed", f"{check}: {verdicts[check]['verdict']} ({verdicts[check]['detail']})")
        elif verdicts[check]["verdict"] != "passed" and check not in stated_ids:
            unknowns.append(f"extra test {check} did not run to a pass: {verdicts[check]['verdict']}")

    # Tier 2: the tests that touch the changed modules, only when tier 1 found no integrity problem and no defect.
    second: dict = {}
    if any(r["stage"] in ("integrity", "tier_1") for r in reasons):
        found = "an integrity problem" if any(r["stage"] == "integrity" for r in reasons) else "a defect"
        skipped.append({"tier": 2, "check": "tests that touch the changed modules", "reason": f"tier 1 found {found}"})
    elif _left(deadline) < 1:
        skipped.append({"tier": 2, "check": "tests that touch the changed modules", "reason": "the time budget is spent"})
    else:
        derived, more = repo_tests.derive_tests(change)
        derived = [rel for rel in derived if rel not in present]
        if more:
            unknowns.append(f"{more} more test file(s) touch the changed modules and were not run (the limit is "
                            f"{repo_tests.MAX_DERIVED})")
        if not derived:
            skipped.append({"tier": 2, "check": "tests that touch the changed modules",
                            "reason": "no other test file names a changed module"})
        else:
            second = _confined(_execute(root, derived, timeout_s=max(1, _left(deadline))))
            runs.append(_record(2, "tests that touch the changed modules", derived, second))
            if second["verdict"] in UNTRUSTED:
                reason("integrity", "run_not_trusted", _untrusted(2, second))
            elif second["verdict"] == repo_tests.FAILED:
                _on_base(root, change, derived, second, deadline, runs, unknowns, reason)
            elif second["verdict"] not in repo_tests.RAN:
                unknowns.append(f"the tests that touch the changed modules did not run to a result "
                                f"({second['verdict']}): {second.get('said', '')}")
    skipped.append({"tier": 3, "check": "mutation and old-against-new probes", "reason": TIER_3})

    if repo_tests.snapshot(root) != before:
        reason("integrity", "repository_changed_during_review", "the working tree changed while G6 reviewed it")
    gate = _gate(task, claim, first)
    merge_reasons = []
    for finding in gate["findings"]:
        if finding.get("code") in INTEGRITY_FINDINGS:
            reason("integrity", "gate_shortcut", finding.get("message", ""))
        else:
            merge_reasons.append(finding.get("message", ""))

    evaluated = [_requirement(req, verdicts, norm) for req in stated]
    if not stated:
        reason("evidence", "no_requirement_stated", "no requirement was stated, so none can be supported")
    for req in evaluated:
        if not req["checks"]:
            reason("evidence", "requirement_without_check", f"{req['id']}: no check was stated")
    for check in stated_ids:
        if verdicts[check]["verdict"] not in DEFECTS and verdicts[check]["verdict"] != "passed":
            reason("evidence", "check_did_not_run", f"{check}: {verdicts[check]['verdict']} "
                                                    f"({verdicts[check]['detail']})")
    if not any(run.get("passed", 0) + run.get("failed", 0) for run in runs):
        reason("evidence", "no_test_ran", "no test ran to a result")
    return _reply(change, evaluated, reasons, unknowns, runs, skipped, first or second, gate, merge_reasons, artifact,
                  claim, sources, started)


def _on_base(root, change, derived, second, deadline, runs, unknowns, reason) -> None:
    """Replay each failing derived test file at the base commit: a defect only when it passed there."""
    failing = sorted({node.partition("::")[0] for node in [*second.get("failed_ids", []),
                                                           *second.get("setup_errors", {})]} & set(derived))
    if not failing:
        unknowns.append(f"the tests that touch the changed modules failed without a test G6 can name: "
                        f"{second.get('said') or 'read their output'}")
        return
    if _left(deadline) < 1:
        unknowns.extend(f"{rel} fails on the patch; the time budget was spent before it ran at the base commit"
                        for rel in failing)
        return
    replays = _execute(root, failing, timeout_s=max(1, _left(deadline)), change=change)
    for rel in failing:
        out = _confined(replays.get(rel) or {"verdict": "not_run", "said": "not replayed"})
        runs.append(_record(2, "base-commit comparison", [rel], out, command=f"pytest {rel} at the base commit "
                                                                              "and on the patch, in a copy"))
        if out.get("verdict") == repo_tests.FAILED:
            reason("tier_2", "derived_test_regressed", f"{rel} passed at the base commit and fails on the patch: "
                   + ", ".join(out.get("failed_ids", [])[:5]))
        elif out.get("verdict") == "passed":
            unknowns.append(f"{rel} failed in place on the patch but passed when replayed in a copy")
        else:
            unknowns.append(f"{rel} fails on the patch and did not pass at the base commit either, so G6 does not "
                            f"count it as a defect of this patch ({out.get('said') or out.get('verdict')})")


_SUMMARY = {
    SUPPORTED: "Supported within scope: every stated requirement passed its stated checks, and G6 found no sign that "
               "the tests or the test runner were weakened. This covers only the checks named here; it does not show "
               "that the patch does everything the task needs, and it is not permission to merge.",
    DEFECT: "Defect detected: {}.",
    INTEGRITY: "Test integrity compromised: the tests may not be trustworthy for this patch ({}).",
    INSUFFICIENT: "Insufficient evidence: G6 can neither support nor reject the patch on the checks it has ({}).",
}
_NEXT = {
    SUPPORTED: "Read the diff for behaviour no stated check exercises; merging is a separate decision with its own "
               "approval.",
    DEFECT: "Fix the patch so the named checks pass, then review it again.",
    INTEGRITY: "Read the changed tests and runner files yourself: restore what the patch weakened, or confirm the "
               "change is intended, then review it again.",
    INSUFFICIENT: "State each requirement with the tests that check it, make those tests run here, and review again.",
}


def _reply(change, evaluated, reasons, unknowns, runs, skipped, ran, gate, merge_reasons, artifact, claim, sources,
           started) -> dict:
    stages = {r["stage"] for r in reasons}
    codes = {r["code"] for r in reasons}
    if "integrity" in stages:
        outcome, why = INTEGRITY, [r["detail"] for r in reasons if r["stage"] == "integrity"]
    elif codes & {"check_failed", "derived_test_regressed"}:
        outcome, why = DEFECT, [r["detail"] for r in reasons if r["stage"] in ("tier_1", "tier_2")]
    elif "evidence" in stages:
        outcome, why = INSUFFICIENT, [r["detail"] for r in reasons if r["stage"] == "evidence"]
    else:
        outcome, why = SUPPORTED, []
    summary = _SUMMARY[outcome].format("; ".join(why[:5]))
    next_actions = [_NEXT[outcome]]
    next_actions += [f"{req['id']}: {req['reason']}; suggested: {req['suggested_test']}" for req in evaluated
                     if "suggested_test" in req]
    if unknowns:
        next_actions.append("Look into the unknowns yourself: G6 does not count them for or against the patch.")
    environment = {"isolation": ran.get("isolation"), "python": ran.get("python"),
                   "interpreter_source": ran.get("interpreter_source"),
                   "host_python_version": platform.python_version(), "platform": platform.platform()}
    evidence = [{"source": "g6.workforce.repo_tests", "summary": f"tier {run['tier']}: {run['command']}: "
                 f"{run['verdict']}"[:200], "authority": "host-observed", "artifact_digest": artifact,
                 "base_commit": change.base_commit,
                 "scope": "these test outcomes on this working tree; not complete correctness", **run} for run in runs]
    assessment = {
        "outcome": outcome, "summary": summary, "reasons": reasons,
        "requirements": evaluated,
        "requirements_without_adequate_check": [r["id"] for r in evaluated if r["status"] == NOT_ESTABLISHED],
        "specification_note": SPECIFICATION_NOTE,
        "scope": {"artifact_digest": artifact, "base_commit": change.base_commit, "changed_files": list(change.files),
                  "environment": environment, "checks_run": runs, "checks_skipped": skipped,
                  "seconds": round(time.monotonic() - started, 3)},
        "unknowns": unknowns, "next_actions": next_actions, "not_checked": list(NOT_CHECKED)}
    card = warning_card(CODE, summary, next_actions[0])
    return {**envelope_fields(reliability_label="qualified-draft", warning_card=card, evidence=evidence,
                              request_id=uuid.uuid4().hex[:12], degradation_reason=summary[:300]),
            "ok": True, "assessment": assessment,
            "merge": {"status": "requires_separate_approval", "reason": MERGE_REASON, "reasons": merge_reasons},
            "gate": gate, "grounding": _grounding(claim, sources), "artifact_digest": artifact}
