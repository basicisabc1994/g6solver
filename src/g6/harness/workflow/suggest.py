# SPDX-License-Identifier: Apache-2.0
"""The suggestion policy (design 17.3 "Suggestions"; 17.4 step 4): zero to three optional actions, in a published
fixed order (docs/product_global_specs/suggestion_order.md), each with an identity that wording cannot change.

- Class 1: an action backed by a receipt that addresses the stated outcome.
- Class 2: a cheap, reversible check that tells a hypothesis's explanations apart.
- Class 3: do nothing, or say what observation is missing.

Within a class a measured, lower burden comes first, then a measured one, then an unknown one (said as unknown);
what remains is ordered by intervention id. No model produces any number here, nothing estimates a saving, and G6's
own actions get no preference over what the person does without G6.

A mandatory finding is not a suggestion and has no intervention id, so no decision can hide it. A hypothesis yields
checks and proposals only: never a settings change, an external action, new data access or a weaker safeguard. When
G6 itself is in the way, the only step offered on G6 is a switch for one of its optional features, which the person
sets; G6 changes nothing.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from g6.core.ledger import digest
from g6.harness.workflow.diagnose import Diagnosis
from g6.harness.workflow.obstacle import ACCEPTED, Obstacle
from g6.harness.workflow.record import Event, ProjectRecord, Ref, event_id, workflow_source

ORDER_VERSION = "suggestion-order.v1"
MAX_SHOWN = 3
KINDS = ("run_reproduction", "answer_pending_question", "state_a_check_for_requirement", "rerun_at_base",
         "grant_source", "fix_setup", "reduce_g6_steps", "observe_more", "do_nothing")
#: The only things ``reduce_g6_steps`` may name: person-set switches for optional features. Never consent, the
#: confinement boundary, the review, a stop or an approval.
G6_SWITCHES = ("G6_WORKFLOW_VIEW=off", "G6_WORKFLOW_RECORD=off", "G6_QUESTIONS_PER_TASK=0", "G6_FEEDBACK_QUESTION=off")
BURDENS = ("lower", "known", "unknown")
CLASS_WORDS = {1: "backed by a receipt and addresses the stated outcome",
               2: "a cheap, reversible check that tells the hypothesis's explanations apart",
               3: "nothing above is supported, so this only says what is missing or to leave things as they are"}
WORDS = {
    "run_reproduction": "run the failing check yourself",
    "answer_pending_question": "answer the pending question in your client's dialog",
    "state_a_check_for_requirement": "state a check for this requirement",
    "rerun_at_base": "run the failing check at the base commit",
    "grant_source": "decide whether to share the source the reviewer needs",
    "fix_setup": "change the one thing the refusal names, then run the review once",
    "reduce_g6_steps": "switch off one of G6's optional steps where the server is registered",
    "observe_more": "let G6 observe what is missing before anything is changed",
    "do_nothing": "do nothing: no action here is supported by what G6 observed",
}
#: Which obstacle gives which class 1 action.
_FOR_OBSTACLE = {"failing_accepted_check": "run_reproduction", "pending_question": "answer_pending_question",
                 "review_unsent": "grant_source", "refused_run": "fix_setup"}


@dataclass(frozen=True)
class Intervention:
    kind: str
    target: str                # a requirement id, a question digest, a source name, a code or a switch
    scope: str                 # "task" or "project"

    def __post_init__(self) -> None:
        if self.kind not in KINDS or self.scope not in ("task", "project") or not isinstance(self.target, str) \
                or len(self.target) > 200:
            raise ValueError("an intervention has a published kind, a short target and a scope of task or project")
        if self.kind == "reduce_g6_steps" and self.target not in G6_SWITCHES:
            raise ValueError("G6 may only suggest switching off one of its own optional features")

    @property
    def id(self) -> str:
        """The identity decisions are kept under: kind, target and scope, and nothing else. Wording, job ids,
        receipts, evidence and the ordering rule's version are not in it."""
        return digest({"kind": self.kind, "target": self.target, "scope": self.scope})


@dataclass(frozen=True)
class Suggestion:
    intervention: Intervention
    cls: int
    reason_code: str
    burden: str
    receipt: Ref | None
    hypothesis_id: str = ""

    def to_dict(self) -> dict:
        one = self.intervention
        return {"intervention_id": one.id, "kind": one.kind, "target": one.target, "scope": one.scope,
                "said": WORDS[one.kind], "class": self.cls, "why_this_class": CLASS_WORDS[self.cls],
                "reason_code": self.reason_code, "burden": self.burden,
                "burden_said": "burden unknown" if self.burden == "unknown" else f"burden {self.burden}, measured here",
                "receipt": None if self.receipt is None else {"kind": self.receipt.kind, "id": self.receipt.id},
                "hypothesis_id": self.hypothesis_id, "order_version": ORDER_VERSION}


def admit(suggestion: Suggestion) -> tuple[bool, str]:
    """Whether a candidate may be offered, and why not: class 1 needs a receipt that ties it to the stated outcome,
    class 2 the hypothesis whose explanations it tells apart. A step that only makes something faster has neither."""
    if suggestion.cls == 1 and suggestion.receipt is None:
        return False, "no receipt ties it to the stated outcome"
    if suggestion.cls == 2 and not suggestion.hypothesis_id:
        return False, "it distinguishes no recorded hypothesis"
    if suggestion.cls not in CLASS_WORDS or suggestion.burden not in BURDENS:
        return False, "it has no place in the published order"
    if suggestion.cls != 3 and suggestion.intervention.kind in ("do_nothing", "observe_more", "reduce_g6_steps"):
        return False, "doing nothing, observing more and switching off a G6 step are class 3 only"
    return True, ""


def _burden(kind: str, measured: Mapping[str, float]) -> str:
    if kind not in measured:
        return "unknown"
    return "lower" if measured[kind] <= min(measured.values()) else "known"


def candidates(diagnosis: Diagnosis, assessment: Mapping | None, *, allowed: Collection[str] = KINDS,
               measured: Mapping[str, float] | None = None, g6_over: Collection[str] = ()) -> tuple[Suggestion, ...]:
    """Every applicable action the diagnosis and the review support, each once, unranked. ``allowed``: the kinds the
    current policy permits (anything else is never a candidate). ``measured``: seconds this project's record holds for
    a kind, where it holds any. ``g6_over``: G6's optional steps whose cost is over the frozen caps on this task
    (names from ``G6_SWITCHES``)."""
    measured = dict(measured or {})
    found: dict[str, Suggestion] = {}

    def add(kind: str, target: str, cls: int, reason: str, *, scope: str = "task", receipt: Ref | None = None,
            hypothesis: str = "") -> None:
        if kind not in allowed:
            return
        one = Suggestion(Intervention(kind, target, scope), cls, reason, _burden(kind, measured), receipt, hypothesis)
        kept = found.get(one.intervention.id)
        if admit(one)[0] and (kept is None or one.cls < kept.cls):
            found[one.intervention.id] = one

    stated = "stated_outcome" not in diagnosis.missing
    for obstacle in diagnosis.obstacles:
        kind = _FOR_OBSTACLE.get(obstacle.kind)
        if kind and stated:
            add(kind, _target(obstacle), 1, "receipt_addresses_outcome", receipt=obstacle.receipt,
                scope="project" if kind == "grant_source" else "task")
    if stated and isinstance(assessment, Mapping):
        for req in assessment.get("requirements") or []:
            if isinstance(req, Mapping) and req.get("status") == "not_established" and not req.get("checks") \
                    and req.get("provenance", "stated") in ACCEPTED:
                add("state_a_check_for_requirement", str(req.get("id", ""))[:200], 1, "receipt_addresses_outcome",
                    receipt=Ref("review", "review", digest(str(assessment.get("report", "")))))
    for hypothesis in diagnosis.hypotheses:
        kind = hypothesis.check.get("kind")
        if kind in KINDS:
            add(kind, _target(hypothesis.obstacle), 2, "check_distinguishes_explanations", hypothesis=hypothesis.id)
        if hypothesis.why_code == "g6_setup_blocks":
            add("reduce_g6_steps", G6_SWITCHES[0], 3, "g6_is_the_obstacle")
    for switch in g6_over:
        if switch in G6_SWITCHES:
            add("reduce_g6_steps", switch, 3, "g6_cost_over_the_frozen_caps")
    if not found:
        if diagnosis.missing:
            add("observe_more", diagnosis.missing[0], 3, "observation_missing")
        else:
            add("do_nothing", "", 3, "nothing_supported")
    return tuple(found.values())


def _target(obstacle: Obstacle) -> str:
    return (obstacle.requirement or obstacle.receipt.id)[:200]


def order(found: Sequence[Suggestion]) -> tuple[Suggestion, ...]:
    """The published order: class, then burden (lower, known, unknown), then intervention id."""
    return tuple(sorted((one for one in found if admit(one)[0]),
                        key=lambda one: (one.cls, BURDENS.index(one.burden), one.intervention.id)))


def ranked(found: Sequence[Suggestion], decisions, *, task: str, now: datetime,
           outcome_sha256: str) -> tuple[tuple[Suggestion, ...], tuple[dict, ...]]:
    """At most three suggestions to show, in the published order, without any the person dismissed; and, for each
    one shown again after a dismissal, the reason. ``decisions``: the project's ``Decisions``. A store that cannot be
    read raises ``RecordUnreadable``, and the caller then shows no suggestion."""
    shown, again = [], []
    for one in order(found):
        state, why = decisions.standing(one.intervention.id, task=task, now=now, outcome_sha256=outcome_sha256)
        if state == "suppressed":
            continue
        if len(shown) < MAX_SHOWN:
            shown.append(one)
            if state == "reopened":
                again.append({"intervention_id": one.intervention.id, "reason": why})
    return tuple(shown), tuple(again)


def lines(suggestions: Sequence[Mapping], reopened: Sequence[Mapping], room: int) -> list[str]:
    """The suggestions as lines for the view, as many as fit in ``room`` (at least one when there is any); the rest
    are in the reply's ``workflow`` field. Each line gives its class reason and its burden, known or not."""
    from g6.harness import patch_report

    if not suggestions:
        return ["- **Suggestions:** none."]
    why = {row["intervention_id"]: row["reason"] for row in reopened}
    fit, out = max(1, min(room, len(suggestions))), []
    for number, one in enumerate(suggestions[:fit], 1):
        target = " " + patch_report.code(one["target"]) if one["target"] else ""
        back = f"; shown again because {why[one['intervention_id']]}" if one["intervention_id"] in why else ""
        more = f" ({len(suggestions) - fit} more in this reply's workflow field)" if number == fit and len(suggestions) > fit else ""
        out.append(f"- **You may ({one['why_this_class']}; {one['burden_said']}{back}):** {one['said']}{target}{more}")
    return out


def _latest_run(record: ProjectRecord, task: str) -> str | None:
    runs = [e for e in record.events(task=task) if e.kind == "run_started" and e.data.get("origin_kind") != "tool_calls"]
    return max(runs, key=lambda e: e.at).run if runs else None


def record_event(record: ProjectRecord, task: str, kind: str, intervention: Intervention | Mapping, *, at: str,
                 native: str = "", provenance: str = "host_observed", extra: Mapping | None = None) -> bool:
    """One of the suggestion life events, each its own kind: ``suggestion_eligible``, ``suggestion_displayed``,
    ``suggestion_adopted``, ``suggestion_dismissed``, ``suggestion_implemented``, ``suggestion_assessed``."""
    if kind not in EVENT_KINDS:
        raise ValueError("not a suggestion event")
    run = _latest_run(record, task)
    if run is None:
        return False
    one = intervention if isinstance(intervention, Mapping) else {
        "intervention_id": intervention.id, "kind": intervention.kind, "target": intervention.target,
        "scope": intervention.scope}
    data = {"intervention": one["intervention_id"], "kind": one["kind"], "target": one["target"][:200],
            "scope": one["scope"], "order_version": ORDER_VERSION, **(extra or {})}
    return record.append(Event(project=record.project, task=task, run=run, kind=kind, at=at,
                               event_id=event_id(run, kind, f"{one['intervention_id']}:{native}"),
                               provenance=provenance, sources=(workflow_source(record.project),), baseline_sha256="",
                               artifact_sha256="", data=data))


EVENT_KINDS = ("suggestion_eligible", "suggestion_displayed", "suggestion_adopted", "suggestion_dismissed",
               "suggestion_implemented", "suggestion_assessed")


def displayed(events: Sequence[Event], intervention_id: str) -> Event | None:
    """The latest time this intervention was displayed for the task, or None: a decision is taken only on a
    suggestion G6 showed."""
    shown = [e for e in events if e.kind == "suggestion_displayed" and e.data.get("intervention") == intervention_id]
    return shown[-1] if shown else None


__all__ = ["BURDENS", "CLASS_WORDS", "EVENT_KINDS", "G6_SWITCHES", "Intervention", "KINDS", "MAX_SHOWN",
           "ORDER_VERSION", "Suggestion", "WORDS", "admit", "candidates", "displayed", "lines", "order", "ranked",
           "record_event"]
