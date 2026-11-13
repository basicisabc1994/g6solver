# SPDX-License-Identifier: Apache-2.0
"""The stated outcome, and obstacles G6 can show a receipt for (design 17.2 "A constraint hypothesis", first step).

An obstacle is an observation with a receipt: a check the request stated or the person confirmed that G6 reproduced
failing on this artefact; an integrity finding that stops the review; a behaviour question still pending across runs;
a run G6 refused; a review that was not sent for want of a grant. Each is scoped to one expectation on one artefact.
It is never called the constraint on the person's work.

What names nothing: a check a model wrote, a reviewer's own test, a slow stage, a single failed run. With too little
observed the result is empty, and the view says that observation is insufficient.

The outcome is taken, in order, from an expectation the person confirmed, then from the goal the request supplied;
otherwise it is not stated, and nothing is asked about it here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from g6.core.cloud_consent import sha256
from g6.harness.workflow.record import Event, Ref

OBSTACLE_KINDS = ("failing_accepted_check", "integrity_block", "pending_question", "refused_run", "review_unsent")
#: The fixed words for each kind: what was observed, never why.
WORDS = {
    "failing_accepted_check": "a check the request stated or you confirmed fails on this patch, reproduced by G6",
    "integrity_block": "the review found an integrity problem in the tests or their runner",
    "pending_question": "one behaviour question about this task is still waiting for your answer",
    "refused_run": "G6 refused to run, so nothing was tested",
    "review_unsent": "the review was not sent: a source is not shared with the reviewer's provider (G6's own setup)",
}
#: A repeated pattern the diagnosis adapter may name beside a hypothesis. It is not an obstacle with a receipt for an
#: accepted expectation, and the narrow view never shows one.
PATTERN_WORDS = {"reviewer_test_stays_open": "a reviewer's test has stayed failing across runs or rounds (a pattern, not "
                                             "an accepted check)"}
#: Whose checks count: the request's and the person's. A model's hypothesis is not an accepted check.
ACCEPTED = ("stated", "person_confirmed")
PROVENANCE_WORDS = {"person_confirmed": "confirmed by you", "attributed_label": "as the request stated it"}
CONSENT_REQUIRED = "G6_E_CLOUD_CONSENT_REQUIRED"
MAX_SHOWN = 140


@dataclass(frozen=True)
class Obstacle:
    kind: str
    scope: str                 # one expectation on one artefact, in fixed words and ids
    receipt: Ref
    requirement: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "observed": WORDS.get(self.kind) or PATTERN_WORDS[self.kind], "scope": self.scope, "requirement": self.requirement,
                "receipt": {"kind": self.receipt.kind, "id": self.receipt.id, "sha256": self.receipt.sha256}}


@dataclass(frozen=True)
class Outcome:
    text_sha256: str
    shown: str                 # at most MAX_SHOWN characters; rendered as a code span, never as prose
    provenance: str

    def to_dict(self) -> dict:
        return {"shown": self.shown, "text_sha256": self.text_sha256, "provenance": self.provenance,
                "said": PROVENANCE_WORDS[self.provenance]}


def cut(text: str) -> str:
    one_line = " ".join(str(text).split())
    return one_line if len(one_line) <= MAX_SHOWN else one_line[:MAX_SHOWN - 3] + "..."


def stated_outcome(intent: Mapping | None, confirmed: Sequence, task: str) -> Outcome | None:
    """The outcome the person stated for ``task``: the first expectation they confirmed (A's standard), else the
    goal the request supplied, else None. Nothing is inferred from the task's own text, and nothing is asked."""
    for one in confirmed or ():
        question, answer = getattr(one, "question", None), getattr(one, "answer", None)
        if question is not None and answer is not None:
            shown = f"{question.function}: outcome {answer.option}, for requirement {question.requirement_id}"
            return Outcome(question.sha256, cut(shown), "person_confirmed")
    goal = intent.get("goal") if isinstance(intent, Mapping) else None
    if isinstance(goal, str) and goal.strip():
        return Outcome(sha256(goal), cut(goal), "attributed_label")
    return None


def _review_ref(assessment: Mapping, job_id: str) -> Ref:
    return Ref("review", job_id or "review", sha256(str(assessment.get("report", ""))))


def obstacles(assessment: Mapping | None, loop: Mapping | None, events: Sequence[Event], *,
              job_id: str = "") -> tuple[Obstacle, ...]:
    """Every obstacle the inputs show with a receipt, in a stable order (an integrity finding first). ``assessment``: a patch
    review's; ``loop``: a loop step's reply (``ending``, ``error``, ``run_id``, ``report``); ``events``: the task's."""
    found: list[Obstacle] = []
    if isinstance(assessment, Mapping):
        ref = _review_ref(assessment, job_id)
        if any(isinstance(r, Mapping) and r.get("stage") == "integrity" for r in assessment.get("reasons") or []):
            found.append(Obstacle("integrity_block", "this patch, as reviewed", ref))
        for req in assessment.get("requirements") or []:
            if not isinstance(req, Mapping) or req.get("status") != "failed" \
                    or req.get("provenance", "stated") not in ACCEPTED:
                continue
            failing = [c for c in req.get("checks") or [] if isinstance(c, Mapping) and c.get("verdict") == "failed"
                       and c.get("provenance", "stated") in ACCEPTED]
            if failing:        # reproduced on this artefact by G6's own run of an accepted check
                found.append(Obstacle("failing_accepted_check", f"requirement {cut(str(req.get('id', '')))[:60]} on "
                                      "this patch", ref, requirement=str(req.get("id", ""))[:200]))
    if isinstance(loop, Mapping) and loop.get("ending") == "untested":
        error = str(loop.get("error") or "")
        code = error[1:error.index("]")] if error.startswith("[G6_E_") and "]" in error else ""
        ref = Ref("report", str(loop.get("run_id") or "loop")[:64], sha256(str(loop.get("report", "")) + error))
        if code == CONSENT_REQUIRED:
            found.append(Obstacle("review_unsent", "this loop's review of this patch", ref))
        elif code:
            found.append(Obstacle("refused_run", f"this loop's run ({code})", ref))
    pending = [e for e in events if e.kind == "question_pending" and e.refs]
    runs = [e for e in events if e.kind == "run_started" and e.data.get("origin_kind") != "tool_calls"]
    for event in pending:
        if sum(run.at >= event.at for run in runs) >= 2:      # still waiting after two runs began
            found.append(Obstacle("pending_question", "one behaviour question on this task", event.refs[0]))
    # an integrity finding leads, as it does everywhere in G6's reports; then by kind and scope
    return tuple(sorted(set(found), key=lambda o: (o.kind != "integrity_block", OBSTACLE_KINDS.index(o.kind), o.scope,
                                                   o.receipt.id)))


def next_check(found: Sequence[Obstacle], assessment: Mapping | None) -> dict | None:
    """One bounded check the person may run for the first obstacle, or None. It is an offer: nothing here runs it,
    and it grants nothing."""
    if not found:
        return None
    first = found[0]
    if first.kind == "failing_accepted_check" and isinstance(assessment, Mapping):
        for req in assessment.get("requirements") or []:
            if isinstance(req, Mapping) and str(req.get("id", ""))[:200] == first.requirement:
                failing = [str(c.get("id")) for c in req.get("checks") or [] if isinstance(c, Mapping)
                           and c.get("verdict") == "failed" and c.get("provenance", "stated") in ACCEPTED]
                if failing:
                    return {"kind": "run_reproduction", "target": first.requirement,
                            "command": cut("pytest " + failing[0])}
        return None
    if first.kind == "pending_question":
        return {"kind": "answer_pending_question", "target": first.receipt.id[:12],
                "command": "g6_confirm_behaviour shows it again; only you answer, in your client's dialog"}
    if first.kind == "review_unsent":
        return {"kind": "grant_source", "target": "reviewer", "command": "g6_cloud_grant asks you in your client's "
                "dialog; sharing is your decision"}
    return None


__all__ = ["ACCEPTED", "OBSTACLE_KINDS", "Obstacle", "Outcome", "WORDS", "cut", "next_check", "obstacles",
           "stated_outcome"]
