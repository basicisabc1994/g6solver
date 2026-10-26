# SPDX-License-Identifier: Apache-2.0
"""g6.harness — the end-to-end spine: standard → intake → plan → execute → adjudicate → remember.

Capability label: ``implemented``. This is the one place components are composed.

    from g6.harness import Check, Standard, run
    standard = Standard(goal="Summarise the refund policy",
                        checks=(Check("states the refund window", lambda out: "14 days" in out),))
    result = run(standard, executor=lambda contract: my_agent(contract.task))

1. **Intake** (``g6.reliability_pipeline.run_intake``): the standard's checks are its acceptance criteria. A block stops
   the run as ``blocked-escalated`` with the intake's code and the next human action.
2. **Plan**: the checks are frozen (each one's code is hashed) before anything runs, and each check is run on the
   caller's known-bad outputs (it must fail them) and known-good output (it must pass it). A check that accepts a
   known-bad output is broken, and is reported as broken. Failures remembered from earlier runs of the same task
   family that this standard does not check are named, and block until a check is added or a person waives them.
3. **Execute**: ``executor(task_contract)`` returns the output. G6 never runs it in-process for you; an executor is
   whatever you pass (an agent, a model call, a function).
4. **Adjudicate**: each check is run on the exact output bytes and ``g6.evidence.issue`` decides the label from those
   receipts. The harness is stricter than the issuer in one way only: a failed acceptance check blocks, even when the
   check's own controls are too weak for the issuer to call it a refutation.
5. **Remember**: each failed check is stored under ``$G6_HOME/harness.db`` against the task family, so a later run, in
   another process, asks for it (and says so when it is handed an output that already failed).

While the process-wide stop (``g6.core.stop``) is engaged, ``run`` does none of this and answers ``G6_E_STOPPED``.

What it does not establish: that the checks capture what the caller needs. Checks written by the caller are not
independent of the caller, so with the default evidence kind a result is ``qualified-draft`` at best (DECISIONS #22).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from g6.reliability_pipeline.task_contract import SuccessCriterion

from g6.harness.memory import Memory

_SLUG = re.compile(r"[^a-z0-9]+")


def _sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


@dataclass(frozen=True)
class Check:
    """One acceptance criterion and how G6 checks it on the output."""

    predicate: str
    run: Callable[[str], bool | str]   # True / "pass", False / "fail", or "unknown"
    check_id: str = ""
    author: str = "caller"
    kind: str = "partial_falsifier"    # an evidence kind of g6.evidence; a caller's check is not independent
    known_bad: tuple[str, ...] = ()    # outputs that break the criterion: the check must fail every one
    known_good: str | None = None      # an output that meets it: the check must pass it

    @property
    def key(self) -> str:
        return self.check_id or _SLUG.sub("-", self.predicate.lower()).strip("-")[:60] or "check"

    def code_sha(self) -> str:
        """What is frozen: the predicate, the id and the check's source (or its repr when there is no source)."""
        try:
            source = inspect.getsource(self.run)
        except (OSError, TypeError):
            try:
                source = inspect.getsource(type(self.run)) + repr(self.run)
            except (OSError, TypeError):
                source = repr(self.run)
        return _sha(json.dumps([self.predicate, self.key, self.kind, source]))


@dataclass(frozen=True)
class Standard:
    goal: str
    checks: tuple[Check, ...]
    context_sources: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()         # decisions a person must make before the result can be relied on
    family: str = ""                    # what lessons are filed under; defaults to the normalised goal
    plan: Any = None                    # a g6.reliability_pipeline.plan.Plan; critiqued before anything runs

    criteria: tuple[SuccessCriterion, ...] = ()  # exact predicate text; criticality remains declared context

    @property
    def family_key(self) -> str:
        return " ".join((self.family or self.goal).lower().split())


@dataclass
class _Ledger:
    seq: int = 0

    def next(self) -> int:
        self.seq += 1
        return self.seq


def _caller_author(check: Check) -> str:
    """Who a check the caller supplied is recorded as written by: the author it names, never G6 (EVIDENCE-01). G6 runs
    the check but did not write it, nor the known-bad outputs its controls come from, so a claim of "g6" is the
    caller's."""
    from g6.evidence.certificate import G6

    return "caller" if check.author.strip().lower() == G6 else check.author


def _result(value: Any) -> str:
    if value is True or value == "pass":
        return "pass"
    if value is False or value == "fail":
        return "fail"
    return "unknown"


def _try(check: Check, output: str) -> str:
    try:
        return _result(check.run(output))
    except Exception:  # noqa: BLE001 - a check that raises has not answered
        return "unknown"


def _envelope(state: str, code: str, message: str, *, request_id: str, run_id: str, evidence: list[dict] | None = None,
              **extra: Any) -> dict[str, Any]:
    from g6.core.reliability_envelope import envelope_fields, warning_card

    envelope = envelope_fields(reliability_label=state, request_id=request_id, run_id=run_id, evidence=evidence or [])
    if envelope["completion_state"] != "verified":
        envelope["warning_card"] = warning_card(code, message)
    return {**envelope, **extra}


def run(standard: Standard, executor: Callable[[Any], str] | None, *, decisions: Collection[str] = (),
        memory: Memory | None = None, producer: str = "executor") -> dict[str, Any]:
    """Run ``standard`` once; the reply is the library envelope with the run's gaps, next action and certificate.

    ``decisions`` are the gates a person has decided, plus ``accept_risky`` (the person confirms a risky task) and
    ``waive:<check key>`` (the person accepts a remembered failure this standard does not check)."""
    own = memory is None
    memory = memory if memory is not None else Memory()
    try:
        reply = _run(standard, executor, set(decisions), memory, producer)
        if reply.get("stage") == "stop":
            return reply
        reply["standard_sha"] = _standard_sha(standard)
        memory._remember_run(standard.family_key, reply)
        return reply
    finally:
        if own:
            memory.close()


def _run(standard: Standard, executor: Callable[[Any], str] | None, decisions: set[str], memory: Memory,
         producer: str) -> dict[str, Any]:
    from g6.core.ledger import ChainLedger
    from g6.evidence import Artifact, CertificateRefused, MissionFacts, issue, record
    from g6.evidence import Check as EvidenceCheck
    from g6.evidence import Control, Receipt
    from g6.reliability_pipeline.failure_modes import relevant
    from g6.reliability_pipeline.intake import run_intake
    from g6.reliability_pipeline.stages import StageState

    from g6.core import stop

    request_id, run_id = uuid.uuid4().hex[:12], uuid.uuid4().hex
    stopped = stop.engaged()   # audit 007 SE-010: nothing runs while G6 is stopped
    if stopped is not None:
        return _envelope("blocked-escalated", stop.STOPPED, f"G6 is stopped: {stopped}", request_id=request_id,
                         run_id=run_id, stage="stop",
                         next_action="Whoever engaged the stop releases it; then run again.")
    keys = [c.key for c in standard.checks]
    if not standard.checks or len(set(keys)) != len(keys):
        why = "the standard has no checks" if not standard.checks else "two checks share an id"
        return _envelope("blocked-escalated", "G6_E_HARNESS_STANDARD", why, request_id=request_id, run_id=run_id,
                         next_action="Give each acceptance criterion one check with its own id.")

    predicates: dict[str, list[str]] = {}
    for check in standard.checks:
        predicates.setdefault(check.predicate, []).append(check.key)
    duplicate_predicates = [ids for ids in predicates.values() if len(ids) > 1]
    if duplicate_predicates:
        return _envelope("blocked-escalated", "G6_E_HARNESS_CRITERIA",
                         f"ambiguous predicate text shared by check ids: {duplicate_predicates}",
                         request_id=request_id, run_id=run_id)

    if standard.criteria and (len(standard.criteria) != len(standard.checks) or
                              {c.text for c in standard.criteria} != {c.predicate for c in standard.checks} or
                              any(type(c.critical) is not bool for c in standard.criteria)):
        return _envelope("blocked-escalated", "G6_E_HARNESS_CRITERIA", "criteria must map once to every check predicate",
                         request_id=request_id, run_id=run_id)

    # 1. intake
    intake = run_intake(SimpleNamespace(goal=standard.goal, acceptance_criteria=[c.predicate for c in standard.checks],
                                        context_sources=list(standard.context_sources),
                                        success_criteria_structured=standard.criteria,
                                        accepted_risky_intent="accept_risky" in decisions), clarification=False)
    if intake.state == StageState.BLOCK:
        return _envelope("blocked-escalated", intake.reason, intake.safe_next_action or intake.reason,
                         request_id=request_id, run_id=run_id, stage="intake",
                         next_action=intake.safe_next_action or intake.reason,
                         feasibility=getattr(intake.metadata.get("feasibility_report"), "classification", None))
    contract = intake.metadata["task_contract"]

    # 2. plan: the caller's plan critiqued (audit 018 PC-003: the verdict is obeyed), then freeze, controls, memory
    if standard.plan is not None:
        from g6.reliability_pipeline.plan import critique

        report = critique(standard.plan)
        if report.verdict != "pass":
            critique_envelope = report.to_envelope(request_id=request_id, run_id=run_id)
            return {**critique_envelope, "stage": "plan",
                    "next_action": "; ".join(f.better_plan for f in report.findings if f.better_plan)}
    ledger = _Ledger()
    frozen = {c.key: (c, c.code_sha(), ledger.next()) for c in standard.checks}
    broken, controls, positive = [], {}, {}
    for key, (check, sha, at) in frozen.items():
        made, author = [], _caller_author(check)
        for i, bad in enumerate(check.known_bad):
            killed = _try(check, bad) == "fail"
            made.append(Control(f"{key}:bad{i}", sha, key, author, ledger.next(), killed))
            if not killed:
                broken.append(f"check {key!r} accepted a known-bad output")
        controls[key] = tuple(made)
        if check.known_good is not None:
            if _try(check, check.known_good) == "pass":
                positive[key] = ledger.next()
            else:
                broken.append(f"check {key!r} rejected its known-good output")
    remembered = memory.failures(standard.family_key)
    unchecked = sorted({f["check"] for f in remembered} - set(keys) - {d[len("waive:"):] for d in decisions
                                                                      if d.startswith("waive:")})
    skills = [s.failure_id for s in relevant(standard.goal)]
    plan = {"checks": {k: sha for k, (_, sha, _) in frozen.items()}, "broken_checks": broken,
            "criteria": {check.key: next(({"text": c.text, "critical": c.critical} for c in contract.criteria
                                          if c.text == check.predicate), None) for check in standard.checks},
            "known_bad_results": {k: [c.killed for c in cs] for k, cs in controls.items()},
            "remembered_failures": [f["check"] for f in remembered], "unchecked_failures": unchecked,
            "failure_modes_consulted": skills}
    if unchecked:
        return _envelope("blocked-escalated", "G6_E_HARNESS_REMEMBERED_FAILURE",
                         f"earlier runs of this task failed on {unchecked}, and this standard does not check them",
                         request_id=request_id, run_id=run_id, stage="plan", plan=plan,
                         next_action=("Add a check for " + ", ".join(repr(u) for u in unchecked)
                                      + ", or waive it (decision 'waive:<key>') if it no longer applies."))

    # 3. execute
    if executor is None:
        return _envelope("blocked-escalated", "G6_E_HARNESS_NO_EXECUTOR", "no executor was given", request_id=request_id,
                         run_id=run_id, stage="execute", plan=plan, next_action="Pass an executor that does the task.")
    try:
        output = executor(contract)
    except Exception as exc:  # noqa: BLE001 - the executor's failure is reported, never raised through
        return _envelope("blocked-escalated", "G6_E_HARNESS_EXECUTOR_FAILED", f"{type(exc).__name__}: {exc}",
                         request_id=request_id, run_id=run_id, stage="execute", plan=plan,
                         next_action="Fix the executor and run again.")
    output = output if isinstance(output, str) else str(output)
    artifact = Artifact(_sha(output), producer, ledger.next())

    plan["changed_checks"] = [k for k, (c, sha, _) in frozen.items() if c.code_sha() != sha]
    broken.extend(f"check {k!r} changed during execution" for k in plan["changed_checks"])

    # 4. adjudicate
    results = {key: _try(check, output) for key, (check, _, _) in frozen.items()}
    receipts = [Receipt(f"r-{key}", key, frozen[key][1], artifact.sha256, results[key], ledger.next()) for key in keys]
    checks = [EvidenceCheck(key, (key,), _caller_author(check), check.kind, sha, at, controls[key],
                            positive_seq=positive.get(key)) for key, (check, sha, at) in frozen.items()]
    standard_sha = _sha(json.dumps([standard.goal, sorted(plan["checks"].items()), sorted(standard.gates)]))
    facts = MissionFacts(mission_sha=_sha(standard_sha + run_id), mission_standard_sha=standard_sha,
                         standard_sha=standard_sha, predicates=tuple(keys), standard_predicates=tuple(keys),
                         gates=tuple(standard.gates))
    # EVIDENCE-01: the controls and receipts this run observed go to its own ledger, which the issuer resolves them in.
    observed = ChainLedger()
    record(observed, *(c for made in controls.values() for c in made), *receipts)
    try:
        cert = issue(facts, artifact, checks, receipts, decisions=decisions & set(standard.gates), ledger=observed)
    except CertificateRefused as exc:
        return _envelope("blocked-escalated", "G6_E_HARNESS_CERTIFICATE_REFUSED", str(exc), request_id=request_id,
                         run_id=run_id, stage="adjudicate", plan=plan, next_action="Fix the standard.")

    # 5. remember
    failed = [k for k in keys if results[k] == "fail"]
    repeated = memory.seen_failing(standard.family_key, artifact.sha256)
    for key in failed:
        memory.record(standard.family_key, key, artifact.sha256, run_id)

    state, code = cert.state, "G6_E_HARNESS_GAPS"
    if failed or broken:
        state = "blocked-escalated"
        code = "G6_E_HARNESS_CHECK_FAILED" if failed else "G6_E_HARNESS_BROKEN_CHECK"
    gaps = [f"{name}: {why}" for name, why in cert.gaps]
    message = ("; ".join(f"check {k!r} failed" for k in failed) or "; ".join(broken) or "; ".join(gaps) or "no gap")
    if repeated:
        message += " (this exact output also failed an earlier run of this task)"
    next_action = (f"Revise the output so it meets {failed}." if failed else
                   "Fix the broken checks." if broken else
                   "Supply independent evidence (a check written by someone other than the producer, frozen first, "
                   "with a control it fails) to go beyond a qualified draft." if state == "qualified-draft" else "None.")
    evidence = [{"kind": "receipt", "check": r.check_id, "result": r.result, "artifact_sha": r.artifact_sha}
                for r in receipts]
    return _envelope(state, code, message, request_id=request_id, run_id=run_id, evidence=evidence, stage="done",
                     plan=plan, results=results, gaps=gaps, next_action=next_action, output=output,
                     repeated_failing_output=repeated, certificate=cert.as_dict())


def run_steps(steps: Sequence[tuple[str, Standard, Callable[[Any], str] | None]], *,
              decisions: Collection[str] = (), memory: Memory | None = None) -> dict[str, Any]:
    """Run trusted application executors in order, with the same checks and stop rules as run.

    This composes Python callables; it is not an execution boundary for untrusted code.
    The summary never promotes the labels returned by individual steps.
    """
    request_id, run_id = uuid.uuid4().hex[:12], uuid.uuid4().hex
    if not steps:
        return _envelope("blocked-escalated", "G6_E_HARNESS_STEPS", "no steps were supplied",
                         request_id=request_id, run_id=run_id, steps=[])
    reports = []
    for name, standard, executor in steps:
        result = run(standard, executor, decisions=decisions, memory=memory)
        reports.append({"name": name, "result": result})
        if result["completion_state"] == "blocked-escalated":
            break
    blocked = reports[-1]["result"]["completion_state"] == "blocked-escalated"
    return _envelope("blocked-escalated" if blocked else "qualified-draft", "G6_E_HARNESS_STEPS",
                     "stopped at the first blocked step" if blocked else "steps ran; consult their receipts and gaps",
                     request_id=request_id, run_id=run_id, steps=reports,
                     evidence=[{"kind": "step", "name": r["name"], "run_id": r["result"]["run_id"]}
                               for r in reports])


def value_report(run_id: str, *, memory: Memory | None = None) -> dict[str, Any]:
    """The first two failed checks, critical first, stable standard order for ties.

    Criticality is declared context from the pipeline's SuccessCriterion, not an observed business value.
    Failure status comes exclusively from the saved run. Unknown checks stay explicit gaps.
    """
    own = memory is None
    memory = memory if memory is not None else Memory()
    try:
        record = memory.run_record(run_id)
        if record is None:
            raise ValueError("G6_E_HARNESS_RUN: no recorded run with that id")
        results = record.get("results", {})
        criteria = record.get("plan", {}).get("criteria", {})
        failed = [{"check": k, **criteria[k]} for k, v in results.items() if v == "fail" and criteria.get(k)]
        failed.sort(key=lambda item: not item["critical"])
        return _report("failed checks ordered by declared criterion criticality; no economic or completeness verdict",
                       highest=failed[0] if failed else None, next=failed[1] if len(failed) > 1 else None,
                       unmeasured=[k for k, v in results.items() if v == "unknown"],
                       missing_criticality=[k for k, v in results.items() if v == "fail" and not criteria.get(k)],
                       evidence=[{"kind": "run", "run_id": run_id}], adjudicated=bool(results))
    finally:
        if own:
            memory.close()


def _standard_sha(standard: Standard) -> str:
    return _sha(json.dumps([standard.goal, [(c.key, c.code_sha(), c.known_bad, c.known_good) for c in standard.checks],
                            standard.gates, [(c.text, c.critical, c.verifier) for c in standard.criteria]], sort_keys=True))


def _report(message: str, **values: Any) -> dict[str, Any]:
    return _envelope("qualified-draft", "G6_E_HARNESS_REPORT", message,
                     request_id=uuid.uuid4().hex[:12], run_id=uuid.uuid4().hex, **values)


def failure_counts(by: str = "check", min_count: int = 1, *, memory: Memory | None = None) -> dict[str, Any]:
    """Failed-check occurrences, including old failure rows; family counts are not run counts."""
    own = memory is None
    memory = memory if memory is not None else Memory()
    try:
        counts = memory.failure_counts(by, min_count)
        return _report("failure counts", counts=counts, by=by)
    finally:
        if own:
            memory.close()


def fail_rate(family: str, *, memory: Memory | None = None) -> dict[str, Any]:
    """Share of recorded attempts that blocked. Old failure-only history has no denominator."""
    own = memory is None
    memory = memory if memory is not None else Memory()
    try:
        rows = memory.runs(" ".join(family.lower().split()))
        failed = sum(r["completion_state"] == "blocked-escalated" for r in rows)
        return _report("blocked attempts / recorded attempts; excludes legacy failure-only history",
                       family=family, runs=len(rows), failed_runs=failed,
                       rate=failed / len(rows) if rows else None, status="measured" if rows else "no runs",
                       evidence=[{"kind": "run", "run_id": r["run_id"]} for r in rows])
    finally:
        if own:
            memory.close()

__g6_mitigates__: tuple[str, ...] = ("unchecked_step", "learning_gap", "untested_workflow", "weak_link", "silent_behaviour_change")
__g6_introduces__: tuple[str, ...] = ("runner_in_process",)
__g6_sensitive_ops__: tuple[str, ...] = ("run", "run_steps", "evaluate_cases")
from g6.harness.assertions import check_field, confirm_suite, resolve_field  # noqa: E402
from g6.harness.readiness import evaluate_cases, readiness_report  # noqa: E402

__all__ = ["Check", "Memory", "Standard", "run", "run_steps", "failure_counts", "fail_rate", "evaluate_cases", "readiness_report", "value_report",
           "check_field", "confirm_suite", "resolve_field"]
