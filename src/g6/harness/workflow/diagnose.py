# SPDX-License-Identifier: Apache-2.0
"""The coding-workflow diagnosis adapter: hypotheses only (design 17.2 "A constraint hypothesis"; 17.4 step 3).

A hypothesis is named only from an observed blocking dependency or a repeated pattern, for a stated outcome, and
always with at least one other explanation, what would count against it, and a cheap check whose result each
explanation predicts differently. The predictions are recorded before anyone acts (``record_predictions``); the later
observation is read against them (``reassess``). Abstaining is the default: a slow stage, a failing check nobody
accepted, missing events or an unknown outcome name no hypothesis, two rules firing stay two with no winner, and
nothing here carries a probability, a score or a saving. A hypothesis grants no action.

The rules are fixed plain code, each with its stated line and its counterevidence (``RULES``); no model is called.
They read this task's own events and never the tuning loop's rows.

``capacity_view`` offers the serial-capacity calculation (``self_maintenance.constraints.identify_constraint``) only
when independently measured stage capacities exist for serial, non-overlapping work; otherwise it says why it did
not calculate. Its result is arithmetic over declared stages, never the identification of a cause.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from g6.core.ledger import digest
from g6.harness.workflow.adapters import NO_OBSERVATION
from g6.harness.workflow.obstacle import Obstacle, Outcome, obstacles
from g6.harness.workflow.record import Event, ProjectRecord, Ref, event_id, workflow_source

STATUS = ("obstacle_only", "hypothesis", "several", "insufficient_observation", "unknown_outcome")
BASES = ("blocking_dependency", "repeated_pattern")
RESULTS = ("retained", "revised", "retired", "unresolved")
CALCULATION = "a calculation over declared serial stages with independent measurements; it identifies no cause"


@dataclass(frozen=True)
class Explanation:
    id: str
    code: str
    predicts: str              # the qualitative result this explanation expects of the hypothesis's check


@dataclass(frozen=True)
class Hypothesis:
    id: str
    outcome: Outcome
    scope: str
    basis: str
    obstacle: Obstacle
    why_code: str
    explanations: tuple[Explanation, ...]
    counterevidence: tuple[Ref, ...]
    check: dict
    coverage_missing: tuple[str, ...]
    overlapping: bool
    waiting_observed: bool | None

    def to_dict(self) -> dict:
        return {"id": self.id, "rule": self.why_code, "basis": self.basis, "scope": self.scope,
                "obstacle": self.obstacle.to_dict(), "why": WORDS[self.why_code],
                "explanations": [{"id": e.id, "code": e.code, "said": WORDS[e.code], "predicts": e.predicts}
                                 for e in self.explanations],
                "would_count_against_it": AGAINST[self.why_code], "check": dict(self.check),
                "coverage_missing": list(self.coverage_missing), "overlapping": self.overlapping,
                "waiting_observed": self.waiting_observed}


@dataclass(frozen=True)
class Diagnosis:
    status: str
    hypotheses: tuple[Hypothesis, ...]
    obstacles: tuple[Obstacle, ...]
    missing: tuple[str, ...]
    capacity: Any | None                # a ConstraintAssessment, when the calculation's assumptions held
    capacity_abstained: str

    def to_dict(self) -> dict:
        return {"status": self.status, "hypotheses": [h.to_dict() for h in self.hypotheses],
                "obstacles": [o.to_dict() for o in self.obstacles], "missing": list(self.missing),
                "capacity": None if self.capacity is None else {
                    "said": CALCULATION, "least_effective_capacity_stage": self.capacity.bottleneck,
                    "observations": [list(one) for one in self.capacity.observations]},
                "capacity_not_calculated": self.capacity_abstained}


@dataclass(frozen=True)
class Rule:
    name: str
    basis: str
    reads: str                 # the input semantics: what the rule takes as observed
    line: str                  # the stated line: when it fires
    against: str               # the counterevidence that withdraws it
    explanations: tuple[tuple[str, str], ...]     # (code, the check result it predicts); the first is the rule's own
    check: dict
    fire: Callable


#: Fixed words for every code a hypothesis can carry. A hypothesis is always said as one, never as a finding.
WORDS = {
    "accepted_check_blocks": "an accepted check for the stated outcome fails on this patch, so the outcome may wait on it",
    "repair_repeats": "the same reviewer's test has stayed failing across runs or rounds, so repairs may not be converging",
    "question_waits": "a behaviour question has waited through two reviews that ended with insufficient evidence",
    "g6_setup_blocks": "G6 itself has refused or failed to send the review twice for the same reason",
    "patch_wrong_for_case": "the patch is wrong for this case",
    "check_stale_or_wrong": "the check is stale or wrong",
    "intent_under_stated": "what is intended is under-stated (a question is pending or was not asked)",
    "reviewer_test_contested": "the reviewer's test is contested",
    "repair_regresses_another_case": "each repair breaks another case",
    "answer_is_missing": "your answer is what is missing",
    "stated_checks_are_missing": "stated checks are what is missing",
    "g6_setup_is_obstacle": "G6's own setup is in the way (its boundary, a sharing grant or the repository's state)",
    "task_needs_no_review": "the task needs no review of this kind",
}
AGAINST = {
    "accepted_check_blocks": "the check fails at the base commit too (if it cannot run there, this stays unresolved)",
    "repair_repeats": "the test passed in the latest run",
    "question_waits": "a later run ended otherwise with the question still pending",
    "g6_setup_blocks": "a run completed between the two",
}


def _review_runs(events: Sequence[Event]) -> dict[str, list[Event]]:
    """Each review or loop run's events in order (a task's call list is not a run of work)."""
    kinds = {e.run: e.data.get("origin_kind") for e in events if e.kind == "run_started"}
    runs: dict[str, list[Event]] = {}
    for event in events:
        if kinds.get(event.run) in ("loop_session", "review_job"):
            runs.setdefault(event.run, []).append(event)
    return dict(sorted(runs.items(), key=lambda item: (min(e.at for e in item[1]), item[0])))


def overlapping(events: Sequence[Event]) -> bool:
    """Whether two runs of this task were under way at once: one began before another had its last event."""
    spans = sorted((min(e.at for e in run), max(e.at for e in run)) for run in _review_runs(events).values())
    return any(later[0] < earlier[1] for earlier, later in zip(spans, spans[1:]))


def _at_base(assessment: Mapping | None) -> dict[str, str]:
    """Each check's verdict in a run at the base commit, where the assessment holds one."""
    scope = (assessment or {}).get("scope") if isinstance((assessment or {}).get("scope"), Mapping) else {}
    found: dict[str, str] = {}
    for run in scope.get("checks_run") or []:
        if isinstance(run, Mapping) and "base commit" in str(run.get("check", "")) and isinstance(run.get("outcomes"), Mapping):
            found.update({str(check): str(verdict) for check, verdict in run["outcomes"].items()})
    return found


def _accepted_check_blocks(events, assessment, loop, found) -> list[tuple[Obstacle, tuple[Ref, ...], bool | None]]:
    base = _at_base(assessment)
    fired = []
    for one in found:
        if one.kind != "failing_accepted_check":
            continue
        checks = [str(c.get("id")) for req in (assessment or {}).get("requirements") or [] if isinstance(req, Mapping)
                  and str(req.get("id", ""))[:200] == one.requirement for c in req.get("checks") or []
                  if isinstance(c, Mapping) and c.get("verdict") == "failed"]
        if checks and all(base.get(check) == "failed" for check in checks):
            continue                    # it fails at the base commit too: withdrawn
        fired.append((one, (), None))
    return fired


def _open_tests(run: list[Event]) -> list[set[str]]:
    rounds = sorted((e for e in run if e.kind == "loop_round"), key=lambda e: e.data.get("round") or 0)
    return [set(e.data.get("open_sha256") or ()) for e in rounds]


def _repair_repeats(events, assessment, loop, found) -> list[tuple[Obstacle, tuple[Ref, ...], bool | None]]:
    runs = {run: _open_tests(held) for run, held in _review_runs(events).items()}
    runs = {run: rounds for run, rounds in runs.items() if rounds}
    if not runs:
        return []
    latest = list(runs)[-1]
    at_end = Counter(sha for rounds in runs.values() for sha in rounds[-1])
    in_rounds = {sha: max(sum(sha in one for one in rounds) for rounds in runs.values())
                 for rounds in runs.values() for one in rounds for sha in one}
    fired = []
    for sha in sorted(in_rounds):
        if (at_end[sha] >= 2 or in_rounds[sha] >= 3) and sha in runs[latest][-1]:     # still open in the latest run
            pending = any(e.kind == "question_pending" for e in events)
            fired.append((Obstacle("reviewer_test_stays_open", "one reviewer's test on this task",
                                   Ref("loop_state", latest, sha)), (), pending))
    return fired


def _question_waits(events, assessment, loop, found) -> list[tuple[Obstacle, tuple[Ref, ...], bool | None]]:
    fired = []
    for pending in (e for e in events if e.kind == "question_pending" and e.refs):
        later = [e for e in events if e.kind == "review_finished" and e.at >= pending.at and e.data.get("outcome")]
        if len(later) >= 2 and all(e.data.get("outcome") == "insufficient_evidence" for e in later):
            fired.append((Obstacle("pending_question", "one behaviour question on this task", pending.refs[0]), (), True))
    return fired


def _g6_setup_blocks(events, assessment, loop, found) -> list[tuple[Obstacle, tuple[Ref, ...], bool | None]]:
    ends = []                           # each run's end, in order: the code that refused it, or "" when it completed
    for run, held in _review_runs(events).items():
        for event in held:
            if event.kind == "loop_ended":
                ends.append((run, event.data.get("code") or "" if event.data.get("ending") == "untested" else ""))
            elif event.kind == "review_finished":
                ends.append((run, event.data.get("refusal_code") or ""))
    fired = []
    for code in sorted({code for _run, code in ends if code}):
        last_two = [n for n, (_run, one) in enumerate(ends) if one == code][-2:]
        if len(last_two) == 2 and not any(one == "" for _run, one in ends[last_two[0]:last_two[1]]):
            kind = "review_unsent" if code == "G6_E_CLOUD_CONSENT_REQUIRED" else "refused_run"
            fired.append((Obstacle(kind, f"this task's runs ({code})", Ref("receipt", ends[last_two[1]][0], digest(code))),
                          (), None))
    return fired


RULES: tuple[Rule, ...] = (
    Rule("accepted_check_blocks", "blocking_dependency",
         "a patch review's requirements and their checks, each with who stated it and G6's own verdict",
         "an accepted check tied to a requirement of the stated outcome is reproduced failing on the current artefact",
         AGAINST["accepted_check_blocks"], (("patch_wrong_for_case", "passes"), ("check_stale_or_wrong", "fails")),
         {"kind": "rerun_at_base", "observable": "accepted_check_at_base_commit",
          "command": "run the failing check at the base commit"}, _accepted_check_blocks),
    Rule("g6_setup_blocks", "repeated_pattern",
         "each run's end as G6 recorded it: completed, or the stable code it was refused or unsent with",
         "the same refusal or unsent code ends 2 or more runs of the task with nothing tested",
         AGAINST["g6_setup_blocks"], (("g6_setup_is_obstacle", "completes"), ("task_needs_no_review", "refused_again")),
         {"kind": "fix_setup", "observable": "next_run_after_the_setup_is_changed",
          "command": "change the one thing the refusal names, then run the review once"}, _g6_setup_blocks),
    Rule("question_waits", "repeated_pattern",
         "the question store's pending question and the outcome each later review ended with",
         "one behaviour question is pending across 2 or more runs whose reviews ended insufficient_evidence",
         AGAINST["question_waits"], (("answer_is_missing", "ends_otherwise"),
                                     ("stated_checks_are_missing", "insufficient_evidence_again")),
         {"kind": "answer_pending_question", "observable": "next_review_after_the_answer",
          "command": "answer the pending question in your client's dialog, then run the review once"}, _question_waits),
    Rule("repair_repeats", "repeated_pattern",
         "the reviewers' tests still failing at the end of each loop round, by digest",
         "the same reviewer test file is open at the end of 2 or more runs, or in 3 or more rounds of one run",
         AGAINST["repair_repeats"], (("intent_under_stated", "open_test_passes"),
                                     ("reviewer_test_contested", "same_test_rejected_again"),
                                     ("repair_regresses_another_case", "another_test_fails")),
         {"kind": "observe_more", "observable": "next_round_after_the_intent_is_stated",
          "command": "state or confirm the intended behaviour for that case, then run one more round"}, _repair_repeats),
)


def diagnose(events: Sequence[Event], *, assessment: Mapping | None = None, loop: Mapping | None = None,
             outcome: Outcome | None, job_id: str = "") -> Diagnosis:
    """Hypotheses for one task, or the reason there is none. Ordered by rule name and hypothesis id; no winner."""
    found = obstacles(assessment, loop, events, job_id=job_id)
    missing = tuple(name for name, absent in ((NO_OBSERVATION, not events), ("stated_outcome", outcome is None))
                    if absent)
    capacity, why_not = capacity_view(events)
    if not events and not isinstance(assessment, Mapping) and not isinstance(loop, Mapping):
        return Diagnosis("insufficient_observation", (), found, missing, capacity, why_not)
    if outcome is None:        # no stated outcome, no hypothesis about what it waits on; the obstacle is kept
        return Diagnosis("unknown_outcome" if found else "insufficient_observation", (), found, missing, capacity, why_not)
    overlap = overlapping(events)
    hypotheses = []
    for rule in sorted(RULES, key=lambda r: r.name):
        for obstacle, against, waiting in rule.fire(events, assessment, loop, found):
            ident = digest(["hypothesis", rule.name, obstacle.kind, obstacle.scope, obstacle.receipt.id])[:32]
            hypotheses.append(Hypothesis(
                id=ident, outcome=outcome, scope=obstacle.scope, basis=rule.basis, obstacle=obstacle, why_code=rule.name,
                explanations=tuple(Explanation(digest([ident, code])[:16], code, predicts)
                                   for code, predicts in rule.explanations),
                counterevidence=against, check=dict(rule.check), coverage_missing=missing, overlapping=overlap,
                waiting_observed=waiting))
    hypotheses.sort(key=lambda h: (h.why_code, h.id))
    status = "several" if len(hypotheses) > 1 else "hypothesis" if hypotheses else (
        "obstacle_only" if found else "insufficient_observation")
    return Diagnosis(status, tuple(hypotheses), found, missing, capacity, why_not)


def capacity_view(events: Sequence[Event]) -> tuple[Any | None, str]:
    """The serial-capacity calculation, or None and why not. It needs a host-observed measurement for every stage
    (``stage_measured`` events: attempted, succeeded, capacity per day), at least two stages, attempted work in each
    and no overlapping runs. Nothing in 0.3.0 writes such measurements for a person's task, so it normally abstains."""
    stages = [e for e in events if e.kind == "stage_measured"]
    if len({e.data.get("stage") for e in stages}) < 2:
        return None, "no independently measured serial stages for this task"
    if overlapping(events):
        return None, "runs of this task overlapped, so its stages are not serial"
    if any(e.provenance != "host_observed" for e in stages):
        return None, "a stage's measurement was not observed by the host"
    from g6.self_maintenance.constraints import StageMeasurement, identify_constraint

    try:
        measured = [StageMeasurement(str(e.data["stage"]), e.data["attempted"], e.data["succeeded"],
                                     float(e.data["capacity_per_day"]), independently_observed=True) for e in stages]
        result = identify_constraint(measured)
    except (KeyError, TypeError, ValueError):
        return None, "a stage has no attempted work or no measured capacity"
    return (result, "") if result.throughput_bound is not None else (None, "the stages were not independently measured")


def _latest_run(record: ProjectRecord, task: str) -> str | None:
    runs = [e for e in record.events(task=task) if e.kind == "run_started" and e.data.get("origin_kind") != "tool_calls"]
    return max(runs, key=lambda e: e.at).run if runs else None


def _write(record: ProjectRecord, task: str, kind: str, native: str, data: dict, at: str) -> bool:
    run = _latest_run(record, task)
    if run is None:
        return False
    return record.append(Event(project=record.project, task=task, run=run, kind=kind, at=at,
                               event_id=event_id(run, kind, native), provenance="rule_derived",
                               sources=(workflow_source(record.project),), baseline_sha256="", artifact_sha256="",
                               data=data))


def record_predictions(record: ProjectRecord, task: str, hypothesis: Hypothesis, *, at: str) -> bool:
    """One ``prediction`` event per explanation, before the check runs: what each expects the check to show.
    True when any was written now; a hypothesis already recorded writes nothing again."""
    wrote = [_write(record, task, "prediction", f"{hypothesis.id}:{one.id}",
                    {"hypothesis": hypothesis.id, "explanation": one.code, "rule": hypothesis.why_code,
                     "observable": hypothesis.check["observable"], "expected": one.predicts}, at)
             for one in hypothesis.explanations]
    return any(wrote)


def reassess(record: ProjectRecord, task: str, hypothesis_id: str, later: Sequence[Event], *, at: str) -> str:
    """Read the later observation against the recorded predictions and record the result: ``retained`` when it is
    what the rule's own explanation predicted and not what the others did; ``revised`` when it contradicts that one
    and fits another; ``retired`` when it fits none; ``unresolved`` when there is no such observation, no recorded
    prediction, or the result does not tell the explanations apart."""
    predictions = [e for e in record.events(task=task) if e.kind == "prediction" and e.data.get("hypothesis") == hypothesis_id]
    recorded_at = min((e.at for e in predictions), default="")
    seen = [e for e in later if e.kind == "check_observed" and predictions
            and e.data.get("observable") == predictions[0].data.get("observable") and e.at >= recorded_at]
    result, standing = "unresolved", ""
    if predictions and seen:
        observed = seen[-1].data.get("result")
        fits = [e.data["explanation"] for e in predictions if e.data.get("expected") == observed]
        own = predictions[0].data["explanation"]
        if len(fits) == len(predictions) and len(predictions) > 1:
            result = "unresolved"
        elif own in fits:
            result, standing = "retained", own
        elif fits:
            result, standing = "revised", fits[0]
        else:
            result = "retired"
    _write(record, task, "hypothesis_reassessed", f"{hypothesis_id}:{at}:{result}",
           {"hypothesis": hypothesis_id, "result": result, **({"standing": standing} if standing else {})}, at)
    return result


def record_change(record: ProjectRecord, task: str, before: Diagnosis, after: Diagnosis, *, at: str) -> bool:
    """Record that the apparent constraint changed between two diagnoses of one task (their hypotheses differ)."""
    old, new = sorted(h.why_code for h in before.hypotheses), sorted(h.why_code for h in after.hypotheses)
    if old == new and before.status == after.status:
        return False
    return _write(record, task, "apparent_constraint_changed", digest([old, new, before.status, after.status, at]),
                  {"before": old, "after": new, "before_status": before.status, "after_status": after.status}, at)


def lines(diagnosis: Mapping) -> list[str]:
    """At most two lines for the view at the diagnosis level: the hypothesis said as one, with what else it could
    be; and what would count against it, with the check that tells them apart. None when there is no hypothesis."""
    found = diagnosis.get("hypotheses") or []
    if not found:
        return []
    if len(found) > 1:         # several stay several: none is singled out, and the order is by name only
        return [f"- **{len(found)} hypotheses, not findings, and none preferred:** " + "; ".join(one["why"] for one in found) + ".",
                "- **What would count against each, and the check that tells its explanations apart:** in this reply's "
                "workflow field. Nothing is ranked."]
    first = found[0]
    others = "; or ".join(one["said"] for one in first["explanations"][1:])
    return [f"- **A hypothesis, not a finding:** {first['why']}. It could instead be that {others}.",
            f"- **What would count against it:** {first['would_count_against_it']}. A check that tells them apart: "
            f"{first['check']['command']}."]


__all__ = ["AGAINST", "BASES", "CALCULATION", "Diagnosis", "Explanation", "Hypothesis", "RESULTS", "RULES", "Rule",
           "STATUS", "WORDS", "capacity_view", "diagnose", "lines", "overlapping", "reassess", "record_change",
           "record_predictions"]
