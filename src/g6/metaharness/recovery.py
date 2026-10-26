# SPDX-License-Identifier: Apache-2.0
"""Recovery and root cause (owner ruling, DECISIONS #27): when tuning fails or finds no gain, say why, and what can be
done next.

Two sources, and never a held-out case:

* ``for_decision``: the gate's own aggregates for a decision that is not ``recommend`` (wins, losses, disagreements,
  the threshold), the same numbers the decision already shows;
* ``diagnose``: the active version's failures on the dev split, the cases a proposer already sees.

Each cause carries its evidence. Each remedy says who acts: ``candidate`` (the meta-harness can propose and test it;
the gate and a person still decide) or ``person`` (only a person can: accept a risky task, add held-out cases, fix a
check). A remedy names the G6 component it uses. Recovery changes nothing itself.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from statistics import NormalDist
from typing import Any

from g6.metaharness.schema import (
    BUDGET_EXCEEDED,
    HOLDOUT_REUSED,
    INSUFFICIENT_EVIDENCE,
    NO_GAIN,
    NOT_COMPUTE_MATCHED,
    OBJECTIVE_REGRESSED,
    SUITE_CANNOT_DISCRIMINATE,
    Workflow,
)

# causes
INTAKE_BLOCKED = "intake_blocked"
CHECK_REJECTED = "check_rejected"
HARNESS_HELD = "harness_held"
WRONG_ANSWER = "wrong_answer"
OVERUSED_LABEL = "overused_label"
ORACLE_SILENT = "oracle_silent"
UNDERPOWERED = "underpowered"
NO_SIGN_OF_GAIN = "no_sign_of_gain"
MORE_COMPUTE = "more_compute"
COST = "cost_or_bound"
SUITE = "suite_cannot_discriminate"
SPLIT_SPENT = "held_out_spent"
EVALUATION_BUDGET = "evaluation_budget"
PROPOSER_FAILED = "proposer_failed"
SEARCH_EXHAUSTED = "search_exhausted"
NEEDS_A_PERSON = "needs_a_person"

RISKY = "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED"
POWER_TARGET = 0.8


@dataclass(frozen=True)
class Cause:
    code: str
    what: str
    cases: int = 0                      # dev cases with this cause (0 for a cause read from the gate)
    examples: tuple[str, ...] = ()      # dev case ids, at most five


@dataclass(frozen=True)
class Remedy:
    action: str
    who: str                            # "candidate": the meta-harness can propose it; "person": only a person can
    addresses: str                      # a cause code
    component: str = ""                 # the G6 component it uses
    how: str = ""


@dataclass(frozen=True)
class Recovery:
    summary: str
    causes: tuple[Cause, ...]
    remedies: tuple[Remedy, ...]

    @property
    def next_action(self) -> str:
        return self.remedies[0].action if self.remedies else ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"next_action": self.next_action}


# --------------------------------------------------------------------------------------------------------------------
# What the gate said
# --------------------------------------------------------------------------------------------------------------------
def cases_needed(n: int, wins: int, losses: int, threshold: float) -> int | None:
    """About how many held-out cases a gain of the observed size needs to pass the sign test at ``threshold`` with
    probability 0.8 (normal approximation over the disagreements); None when nothing was gained."""
    if n <= 0 or wins <= losses:
        return None
    d = (wins + losses) / n
    gain = (wins - losses) / n
    z = NormalDist().inv_cdf(1 - threshold) + NormalDist().inv_cdf(POWER_TARGET)
    discordant = (z * d / gain) ** 2
    return max(n + 1, math.ceil(discordant / d))


def _diagnose_first() -> Remedy:
    return Remedy("Run mh.diagnose(): it reads the active version's dev failures and names the G6 components that "
                  "address them.", "candidate", NO_SIGN_OF_GAIN, "metaharness", "mh.diagnose()")


def for_decision(decision: Any, report: Any, workflow: Workflow) -> Recovery:
    """Why a decision is not ``recommend``, from the gate's aggregates only, and what can be done next."""
    code, reasons = decision.code, list(decision.reasons)
    s = getattr(report, "success", None) or {}
    n, wins, losses = getattr(report, "n", 0), int(s.get("wins", 0)), int(s.get("losses", 0))
    policy = workflow.policy
    causes: list[Cause] = []
    remedies: list[Remedy] = []
    if code in (INSUFFICIENT_EVIDENCE, NO_GAIN) and wins > losses:
        need = cases_needed(n, wins, losses, policy.threshold)
        causes.append(Cause(UNDERPOWERED, f"the candidate won {wins} and lost {losses} of {n} held-out cases: a gain "
                            f"this test cannot confirm at this size (p must be at most {policy.threshold:g}, with at "
                            f"least {policy.min_discordant} disagreements)"))
        remedies.append(Remedy(f"Add fresh held-out cases with known answers: about {need} in all would confirm a gain "
                               f"of this size {POWER_TARGET:.0%} of the time. Do not read this result as 'no effect'.",
                               "person", UNDERPOWERED, "", "a larger held-out split, frozen and confirmed by a person"))
        remedies.append(_diagnose_first())
    elif code in (INSUFFICIENT_EVIDENCE, NO_GAIN):
        causes.append(Cause(NO_SIGN_OF_GAIN, f"no sign of a gain: {wins} wins and {losses} losses on {n} held-out "
                            "cases"))
        remedies.append(_diagnose_first())
    elif code == NOT_COMPUTE_MATCHED:
        causes.append(Cause(MORE_COMPUTE, "the candidate used more model calls than the base, and the base given the "
                            "same calls did as well: the gain is the extra compute"))
        remedies.append(Remedy("Try a cheaper variant of the change (fewer samples, retries or steps).", "candidate",
                               MORE_COMPUTE, "reasoning", "Profile(retries=..., pipeline=[...]) with fewer calls"))
        remedies.append(_diagnose_first())
    elif code == OBJECTIVE_REGRESSED:
        causes.append(Cause(COST, "; ".join(reasons)))
        remedies.append(Remedy("Try a cheaper variant of the change.", "candidate", COST))
        remedies.append(Remedy("If the bound itself is wrong, a person changes the objective; that is a new workflow "
                               "to confirm, and earlier comparisons are void.", "person", COST))
    elif code == SUITE_CANNOT_DISCRIMINATE:
        causes.append(Cause(SUITE, "; ".join(reasons)))
        remedies.append(Remedy("Fix the checks or the oracle so an empty answer fails them, then confirm the workflow "
                               "again. The meta-harness never changes them.", "person", SUITE))
    elif code == HOLDOUT_REUSED:
        causes.append(Cause(SPLIT_SPENT, "; ".join(reasons)))
        remedies.append(Remedy("Freeze a fresh held-out split and confirm the new workflow.", "person", SPLIT_SPENT))
    elif code == BUDGET_EXCEEDED:
        causes.append(Cause(EVALUATION_BUDGET, "; ".join(reasons)))
        remedies.append(Remedy("Raise the evaluation budget (EvidencePolicy.max_eval_model_calls) or try a cheaper "
                               "candidate. A partial evaluation proves nothing.", "person", EVALUATION_BUDGET))
    else:
        causes.append(Cause(NEEDS_A_PERSON, "; ".join(reasons) or code))
        remedies.append(Remedy("A person resolves this before anything else can be measured: "
                               + ("; ".join(reasons) or code), "person", NEEDS_A_PERSON))
    summary = f"not recommended ({code}): " + "; ".join(c.what for c in causes)
    return Recovery(summary, tuple(causes), tuple(remedies))


def for_proposal(error: str | None, refused: Sequence[str]) -> Recovery:
    """Why a proposer offered nothing new."""
    if error:
        cause = Cause(PROPOSER_FAILED, f"the strategy failed: {error}")
    else:
        cause = Cause(SEARCH_EXHAUSTED, "the strategy proposed nothing valid and new" +
                      (f" ({len(refused)} refused: {refused[0]})" if refused else ""))
    remedies = (Remedy("Run mh.diagnose() to see which kind of failure is left, and which G6 components address it.",
                       "candidate", cause.code, "metaharness", "mh.diagnose()"),
                Remedy("Declare more options for the search (prompts, pipeline steps, extra checks, models), or write "
                       "a candidate by hand with mh.candidate(...).", "person", cause.code))
    return Recovery(cause.what, (cause,), remedies)


# --------------------------------------------------------------------------------------------------------------------
# What the dev failures say
# --------------------------------------------------------------------------------------------------------------------
_LABEL = re.compile(r"[A-Za-z][\w.\-]{0,47}")


def answer_kind(workflow: Workflow) -> str:
    """``code`` (expected ``{"tests": ...}``), ``number``, ``label`` (short identifiers) or ``text``, from dev only."""
    expected = [c.expected for c in workflow.suite.dev]
    if workflow.code_root is not None or any(isinstance(e, Mapping) and "tests" in e for e in expected):
        return "code"
    values = [str(e).strip() for e in expected]
    if values and all(_number(v) for v in values):
        return "number"
    if values and all(_LABEL.fullmatch(v) for v in values):
        return "label"
    return "text"


def _number(value: str) -> bool:
    try:
        float(value.replace(",", ""))
    except ValueError:
        return False
    return True


#: Remedies for wrong answers, by answer kind: the G6 component each one uses, and how to try it.
WRONG_ANSWER_REMEDIES: dict[str, tuple[Remedy, ...]] = {
    "label": (
        Remedy("Show the model labelled examples of similar inputs from your own labelled data (never the held-out "
               "cases): confusable labels are told apart by examples, not by their names.", "candidate", WRONG_ANSWER,
               "context", "Profile(exemplars=8), with Workflow(examples=<your labelled cases>) or the dev split; "
               "searchable as {'exemplars': [0, 8]}"),
        Remedy("Vote over several answers (self-consistency); the gate compares it with the base given the same "
               "calls.", "candidate", WRONG_ANSWER, "reasoning", "g6.reasoning.self_consistency / weighted_vote"),
        Remedy("If the label is a judgement about a source text (supports, contradicts), add a step that checks the "
               "claim against the text and corrects a label the text contradicts.", "candidate", WRONG_ANSWER,
               "grounding", "a PipelineStep calling g6.grounding.check_claim (status 'conflict')"),
    ),
    "number": (
        Remedy("Ask for the working, then the final answer on its own line ('Answer: <number>').", "candidate",
               WRONG_ANSWER, "", "Profile(prompt=...)"),
        Remedy("Vote over several worked answers (self-consistency), compared at equal compute.", "candidate",
               WRONG_ANSWER, "reasoning", "g6.reasoning.self_consistency / weighted_vote"),
    ),
    "code": (
        Remedy("Run the example tests the task shows, out of process, and give the model the failure to repair, a few "
               "rounds at most (never a hidden test).", "candidate", WRONG_ANSWER, "reasoning",
               "Workflow(pipeline_steps={'repair_with_tests': steps.repair_with_tests(rounds=3)}), then "
               "Profile(pipeline=['repair_with_tests']); g6.metaharness.coding for repository tasks"),
        Remedy("For code with a fixed shape and a few unknown parts, search the parts against tests "
               "(counterexample-guided synthesis).", "candidate", WRONG_ANSWER, "cegis", "g6.cegis.cegis"),
    ),
    "text": (
        Remedy("Check each claim in the answer against the sources the task gives, and revise what they do not "
               "support.", "candidate", WRONG_ANSWER, "grounding", "a PipelineStep calling g6.grounding.check_claims"),
        Remedy("Adjudicate the answer's claims by type and evidence grade before it is released.", "candidate",
               WRONG_ANSWER, "epistemic_integrity", "g6.epistemic_integrity.adjudicate"),
    ),
}


def _ids(items: Iterable[Any]) -> tuple[str, ...]:
    return tuple(f.case_id for f in items)[:5]


def _bare(output: str) -> str:
    return (output or "").strip().strip("`'\"").strip().rstrip(".").strip()


def overused_label(wrong: Sequence[Any], workflow: Workflow) -> tuple[str, int] | None:
    """A label (one of the dev answers) given as the whole wrong answer at least 3 times and in at least 40% of the
    wrong answers, with how often; None when no label stands out."""
    labels = {str(c.expected).strip() for c in workflow.suite.dev}
    said = Counter(_bare(f.output) for f in wrong if _bare(f.output) in labels)
    if not said:
        return None
    label, times = said.most_common(1)[0]
    return (label, times) if times >= 3 and times >= 0.4 * len(wrong) else None


def diagnose(view: Any, workflow: Workflow, decisions: Sequence[Mapping[str, Any]] = ()) -> Recovery:
    """Root causes of the active version's dev failures (``view`` is the proposer view: dev cases only), and the
    remedies for each, in the order to try them: what only a person can unblock first."""
    failures = list(view.failures)
    n = len(view.dev)
    blocked = [f for f in failures if f.code.startswith("G6_E_PIPELINE_")]
    rejected = [f for f in failures if f not in blocked and f.failed_checks]
    held = [f for f in failures if f.code and f not in blocked and f not in rejected]
    wrong = [f for f in failures if not f.code and f.oracle == "fail"]
    silent = [f for f in failures if not f.code and f.oracle == "unknown"]
    causes: list[Cause] = []
    remedies: list[Remedy] = []
    if blocked:
        codes = Counter(f.code for f in blocked)
        causes.append(Cause(INTAKE_BLOCKED, f"{len(blocked)} of {n} dev cases were held at intake before any model "
                            f"call ({', '.join(f'{c} x{k}' for c, k in codes.most_common())})", len(blocked),
                            _ids(blocked)))
        if RISKY in codes:
            remedies.append(Remedy("The task's text names something high-stakes, so the intake holds each run for a "
                                   "person. If you accept that for this workflow, name yourself in "
                                   "Workflow(accept_risky='<your name>') and confirm it again; no profile can get "
                                   "past it.", "person", INTAKE_BLOCKED, "reliability_pipeline",
                                   "Workflow(..., accept_risky='<your name>')"))
        else:
            remedies.append(Remedy("The intake holds these tasks; read its reason, then reword the task or give it "
                                   "what it asks for. No profile can get past it.", "person", INTAKE_BLOCKED,
                                   "reliability_pipeline"))
    if rejected:
        said = {c.key: c.predicate for c in (*workflow.checks, *workflow.optional_checks.values())}
        names = Counter(said.get(c, c) for f in rejected for c in f.failed_checks)
        causes.append(Cause(CHECK_REJECTED, f"{len(rejected)} of {n} dev answers failed a check "
                            f"({', '.join(f'{c!r} x{k}' for c, k in names.most_common(3))})", len(rejected),
                            _ids(rejected)))
        remedies.append(Remedy("Ask for the format the check wants in the prompt, or retry an empty answer.",
                               "candidate", CHECK_REJECTED, "", "Profile(prompt=..., retries=1)"))
        remedies.append(Remedy("If the check rejects good answers, a person fixes it and confirms the workflow again; "
                               "the meta-harness never changes a check.", "person", CHECK_REJECTED))
    if held:
        codes = Counter(f.code for f in held)
        causes.append(Cause(HARNESS_HELD, f"the harness held {len(held)} of {n} dev runs "
                            f"({', '.join(f'{c} x{k}' for c, k in codes.most_common(3))})", len(held), _ids(held)))
        remedies.append(Remedy("Read each code: a spent call budget is raised in Profile.budgets (and its cost shows "
                               "in the comparison); a model error is fixed where the model runs.", "candidate",
                               HARNESS_HELD, "", "Profile(budgets=...)"))
    if wrong:
        kind = answer_kind(workflow)
        causes.append(Cause(WRONG_ANSWER, f"{len(wrong)} of {n} dev answers passed the checks and were wrong "
                            f"(answer kind: {kind})", len(wrong), _ids(wrong)))
        over = overused_label(wrong, workflow) if kind == "label" else None
        if over is not None:
            label, times = over
            causes.append(Cause(OVERUSED_LABEL, f"{times} of the {len(wrong)} wrong answers were {label!r}: the model "
                                f"over-uses it", times, _ids(f for f in wrong if _bare(f.output) == label)))
            remedies.append(Remedy(f"Ask the model once more whenever it answers {label!r}: confirm it against the "
                                   "input, or choose another label.", "candidate", OVERUSED_LABEL, "reasoning",
                                   f"Workflow(pipeline_steps={{'second_look': steps.second_look({label!r}, labels)}}), "
                                   "then Profile(pipeline=['second_look'])"))
        remedies.extend(WRONG_ANSWER_REMEDIES[kind])
    if silent:
        causes.append(Cause(ORACLE_SILENT, f"the oracle gave no answer on {len(silent)} of {n} dev cases", len(silent),
                            _ids(silent)))
        remedies.append(Remedy("Make the oracle return True or False on every case; an unknown is never a pass.",
                               "person", ORACLE_SILENT))
    tried = Counter(d.get("code", "") for d in decisions if d.get("verdict") != "recommend")
    if sum(tried.values()) >= 2:
        causes.append(Cause(SEARCH_EXHAUSTED, f"{sum(tried.values())} candidates were refused so far "
                            f"({', '.join(f'{c} x{k}' for c, k in tried.most_common())})"))
        remedies.append(Remedy("Widen what is tried: declare pipeline steps built on the components named above, "
                               "not only prompt wording.", "person", SEARCH_EXHAUSTED))
        if tried.get(INSUFFICIENT_EVIDENCE, 0) + tried.get(NO_GAIN, 0) >= 2:
            remedies.append(Remedy("Several candidates fell short on the held-out split: a larger split may be what is "
                                   "missing. Each decision's recovery estimates the size.", "person",
                                   SEARCH_EXHAUSTED))
    remedies.sort(key=lambda r: r.who != "person" or r.addresses != INTAKE_BLOCKED)
    if not failures:
        summary = f"the active version passed all {n} dev cases: dev shows nothing to fix"
    else:
        summary = f"{len(failures)} of {n} dev cases fail: " + "; ".join(c.what for c in causes if c.cases)
    return Recovery(summary, tuple(causes), tuple(remedies))


__all__ = ["Cause", "Recovery", "Remedy", "WRONG_ANSWER_REMEDIES", "answer_kind", "cases_needed", "diagnose",
           "for_decision", "for_proposal", "overused_label"]
