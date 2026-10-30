# SPDX-License-Identifier: Apache-2.0
"""A static check of a plan of op calls, and a run that follows it (audit 016 §11: the archive's ``composer.py``).

A plan is a list of steps ``{"component": str, "op": str, "args": {...}}``. ``check_plan`` judges every step before
anything runs and reports every problem, each with its step and a stable code:

* the component is registered (``G6_E_UNKNOWN_COMPONENT``) and loads (``G6_E_NAV_COMPONENT_UNLOADABLE``);
* the op is declared by a ParamContract or listed by the component's block (``G6_E_NAV_UNKNOWN_OP``), and is not one
  the component marks sensitive (``G6_E_SENSITIVE_COMPONENT_DENIED``);
* the args fit the op's contract: every required key, no unknown key, the declared types (``G6_E_INPUT_VALIDATION``);
* the authority rule (``g6.navigator.authority``: ``G6_E_OP_NOT_CLEARED``, ``G6_E_OP_ACTS_BELOW_A2``);
* when the caller sets ``max_effect``, each op's declared side effect is within it; an op with no contract declares
  none, so no budget admits it (``G6_E_NAV_EFFECT_OVER_BUDGET``).

A plan that passes is ``qualified-draft``: statically consistent, and nothing ran. Steps take static args, so nothing
checks that one step's output can feed the next. ``run_plan`` runs a plan only when ``check_plan`` passes it and the
caller injects an executor; it stops at the first failed step. Running a step is not checking its result, so a run's
best state is ``qualified-draft`` too. Neither ever says ``verified`` (audit 016, NAV-001).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from g6.core.registry import get_registry
from g6.core.reliability_envelope import WarningCard
from g6.navigator import authority as _authority
from g6.navigator.authority import Authority
from g6.navigator.catalog import Contracts, listed_ops
from g6.workforce.capabilities import SIDE_EFFECTS, ParamContract, load_contracts
from g6.workforce.contracts import AUTHORITY_COPY, BLOCKED, QUALIFIED, outcome_of
from g6.workforce.plans import effect_of

EMPTY_PLAN = "G6_E_NAV_EMPTY_PLAN"
STEP_MALFORMED = "G6_E_NAV_STEP_MALFORMED"
UNKNOWN_COMPONENT = "G6_E_UNKNOWN_COMPONENT"  # the registry's own code
COMPONENT_UNLOADABLE = "G6_E_NAV_COMPONENT_UNLOADABLE"
UNKNOWN_OP = "G6_E_NAV_UNKNOWN_OP"
SENSITIVE = "G6_E_SENSITIVE_COMPONENT_DENIED"  # the registry's own code
INPUT_VALIDATION = "G6_E_INPUT_VALIDATION"  # the registry's own code
EFFECT_OVER_BUDGET = "G6_E_NAV_EFFECT_OVER_BUDGET"
STATIC_CHECK = "G6_E_NAV_STATIC_CHECK"
NO_EXECUTOR = "G6_E_NAV_NO_EXECUTOR"
STEP_FAILED = "G6_E_NAV_STEP_FAILED"
RAN_UNCHECKED = "G6_E_NAV_RAN_UNCHECKED"

#: What a step is read for. Any other key is refused, so a misspelt key cannot vanish and a step cannot declare its own
#: agency level (audit 016, NAV-012: the archive read ``agency_level`` from the very step it judged).
STEP_FIELDS = frozenset({"component", "op", "args"})
#: ``executor(component, op, args) -> answer``; ``g6.core.registry.get_registry().invoke`` is one.
Executor = Callable[[str, str, dict[str, Any]], Any]
_STATES = (QUALIFIED, BLOCKED)  # never "verified" (docs/transition/DECISIONS.md #22)
_ERROR_CHARS = 300
_CHECKED = ("the component is registered and loads", "the op is declared by a contract or listed by its block",
            "the op is not sensitive", "the args fit the op's contract",
            "the authority rule (A0 refused; an op that can act needs A2)")
_NOT_CHECKED = ("no step ran", "whether one step's output can feed the next: steps take static args",
                "the args of an op with no contract: its component's typed input checks them when it runs")


def _plain(value: Any) -> Any:
    """A JSON-safe copy of an answer, for a result that crosses a surface."""
    if hasattr(value, "model_dump"):
        return _plain(value.model_dump())
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _card(code: str, message: str, action: str, state: str) -> dict[str, Any]:
    return WarningCard(code=code, message=message, suggested_action=action,
                       severity="blocked" if state == BLOCKED else "amber").model_dump()


def _state(state: str) -> None:
    if state not in _STATES:
        raise ValueError(f"a plan's state is one of {_STATES}, never {state!r}: a static check or a run of unchecked "
                         "steps cannot verify anything (audit 016, NAV-001)")


@dataclass(frozen=True)
class Issue:
    """One problem: the step it belongs to (None: the plan as a whole), a stable code and the reason in words."""

    step: int | None
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"step": self.step, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class StepCheck:
    """How one step was judged."""

    index: int
    component: str
    op: str
    args: dict[str, Any]
    exists: bool
    #: The contract's declared side effect; None when no contract declares the op.
    effect: str | None
    #: The class the authority rule judged ("A0" when nothing measured the op).
    authority: str
    contract_sha256: str = ""
    issues: tuple[Issue, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "component": self.component, "op": self.op, "args": _plain(self.args),
                "exists": self.exists, "effect": self.effect, "authority": self.authority,
                "contract_sha256": self.contract_sha256, "issues": [i.as_dict() for i in self.issues]}


@dataclass(frozen=True)
class PlanReport:
    """``check_plan``'s answer. ``valid`` is True only when no step has a problem; the state is then
    ``qualified-draft``, else ``blocked-escalated``."""

    steps: tuple[StepCheck, ...]
    issues: tuple[Issue, ...]
    #: The worst declared side effect, an op with no contract counting as external (the workforce planner's reading).
    effect: str
    completion_state: str
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""

    def __post_init__(self) -> None:
        _state(self.completion_state)

    @property
    def valid(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "effect": self.effect, "steps": [s.as_dict() for s in self.steps],
                "issues": [i.as_dict() for i in self.issues], "completion_state": self.completion_state,
                "warning_card": self.warning_card, "evidence": self.evidence, "request_id": self.request_id,
                "run_id": self.run_id}


def _declared_effect(contract: ParamContract | None) -> str | None:
    """An op's side effect is what its contract declares, never a guess (audit 016, NAV-003)."""
    return contract.side_effect if contract is not None else None


def _component_view(component: str) -> tuple[frozenset[str], frozenset[str], str]:
    """(the ops its block lists, the ops it marks sensitive, why it does not load or "")."""
    try:
        sensitive = frozenset(getattr(get_registry().get_module(component), "__g6_sensitive_ops__", ()))
        return listed_ops(component), sensitive, ""
    except Exception as exc:  # noqa: BLE001 - a component that raises while loading does not load; the step says so
        return frozenset(), frozenset(), f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS]


def _authority_issue(index: int, name: str, found: _authority.Finding) -> Issue:
    if found.code == _authority.NOT_CLEARED:
        copy = AUTHORITY_COPY["plan_a0"].format(op=name, unit=index)
        detail = "class A0" if found.measured else "class A0: no authority reading measured it"
    else:
        copy = AUTHORITY_COPY["plan_effect"].format(op=name, unit=index)
        detail = f"class {found.cls}; an op with effect {found.effect!r} needs {found.need}"
    return Issue(index, found.code, f"{copy} ({detail})")


def _check_step(index: int, raw: Any, table: Contracts, names: set[str], views: dict, read: Authority,
                max_effect: str | None) -> StepCheck:
    say = f"step {index}"
    if not isinstance(raw, Mapping):
        return StepCheck(index, "", "", {}, False, None, "A0",
                         issues=(Issue(index, STEP_MALFORMED, f"{say}: a step is an object {{component, op, args}}"),))
    issues: list[Issue] = []
    unknown = sorted(map(str, set(raw) - STEP_FIELDS))
    if unknown:
        issues.append(Issue(index, STEP_MALFORMED,
                            f"{say}: unknown field(s) {unknown}; a step is read only for component, op and args"))
    component, op, args = raw.get("component"), raw.get("op"), raw.get("args", {})
    if not isinstance(args, Mapping):
        issues.append(Issue(index, STEP_MALFORMED, f"{say}: args is an object"))
        args = {}
    args = dict(args)
    if not (isinstance(component, str) and component and isinstance(op, str) and op):
        issues.append(Issue(index, STEP_MALFORMED, f"{say}: a step names a component and an op"))
        return StepCheck(index, str(component or ""), str(op or ""), args, False, None, "A0", issues=tuple(issues))
    name = f"{component}.{op}"
    # audit 016, NAV-001: the op is resolved against the contracts and the registry; the archive checked only that
    # both names were non-empty.
    contract = table.get((component, op))
    exists = contract is not None
    if component in names:
        if component not in views:
            views[component] = _component_view(component)
        listed, sensitive, broken = views[component]
        if broken:
            issues.append(Issue(index, COMPONENT_UNLOADABLE, f"{say}: {component} does not load here ({broken})"))
        exists = exists or op in listed
        if not exists and not broken:
            issues.append(Issue(index, UNKNOWN_OP, f"{say}: {component} has no op {op!r}: no contract declares it "
                                                   f"and its block lists {sorted(listed)}"))
        if op in sensitive:
            issues.append(Issue(index, SENSITIVE, f"{say}: {name} is sensitive and cannot run through a plan"))
    elif contract is None:
        issues.append(Issue(index, UNKNOWN_COMPONENT,
                            f"{say}: {component!r} is not a registered component and no contract declares {name}"))
    effect = _declared_effect(contract)
    if contract is not None:
        issues += [Issue(index, INPUT_VALIDATION, f"{say}: {name}: {p}") for p in contract.violations(args)]
    cls = _authority.measured(component, op, read) or "A0"
    if exists:
        found = _authority.judge(component, op, authority=read, effect=effect)
        if found is not None:
            issues.append(_authority_issue(index, name, found))
        if max_effect is not None and (effect is None or SIDE_EFFECTS.index(effect) > SIDE_EFFECTS.index(max_effect)):
            # audit 016, NAV-003: an op with no contract declares no effect, so no budget admits it.
            said = f"declared side effect {effect!r}" if effect else "no declared side effect"
            issues.append(Issue(index, EFFECT_OVER_BUDGET, f"{say}: {name} has {said}, beyond max_effect {max_effect!r}"))
    return StepCheck(index, component, op, args, exists, effect, cls,
                     contract.sha256 if contract is not None else "", tuple(issues))


def check_plan(steps: Any, *, contracts: Contracts | None = None, authority: Authority | None = None,
               max_effect: str | None = None, request_id: str = "", run_id: str = "") -> PlanReport:
    """Every problem in ``steps``, judged before anything runs (module docstring). ``contracts``: the ParamContracts
    to judge by (default: the shipped ones). ``authority``: the reading the rule uses (default: the library's own
    table, bound to each contract's digest). ``max_effect``: the worst side effect the caller allows, one of
    ``("read", "local_write", "external", "irreversible")``, or None for no budget."""
    if max_effect is not None and max_effect not in SIDE_EFFECTS:
        raise ValueError(f"max_effect must be one of {SIDE_EFFECTS} or None, got {max_effect!r}")
    table = load_contracts() if contracts is None else contracts
    read = authority or _authority.shipped_authority(table)
    if not isinstance(steps, (list, tuple)) or not steps:
        checks: tuple[StepCheck, ...] = ()
        issues: tuple[Issue, ...] = (Issue(None, EMPTY_PLAN, "a plan is a non-empty list of steps "
                                                             "{component, op, args}"),)
    else:
        names, views = set(get_registry().names()), {}
        checks = tuple(_check_step(i, raw, table, names, views, read, max_effect) for i, raw in enumerate(steps))
        issues = tuple(issue for check in checks for issue in check.issues)
    effect = effect_of([(c.component, c.op) for c in checks if c.component and c.op],
                       lambda component, op: _declared_effect(table.get((component, op))))
    if issues:
        state = BLOCKED
        more = f" ({len(issues) - 1} more in issues)" if len(issues) > 1 else ""
        card = _card(issues[0].code, issues[0].message + more, "Fix every problem in issues; nothing ran.", state)
    else:
        state = QUALIFIED
        card = _card(STATIC_CHECK, "Every step names an op that exists, its args fit the op's contract and the "
                                   "authority rule clears it. This is a static check and nothing ran, so the plan is "
                                   "a draft, not verified.",
                     "Run it with run_plan and an executor, and check each step's result before relying on it.", state)
    evidence = [{"kind": "static_plan_check", "steps": len(checks), "problems": len(issues),
                 "checked": [*_CHECKED, *([f"each declared side effect is within {max_effect!r}"] if max_effect else [])],
                 "not_checked": list(_NOT_CHECKED),
                 "authority_reading": "caller" if authority else "g6.workforce.service.CENSUS, bound to contract digests",
                 "contracts": {f"{c.component}.{c.op}": c.contract_sha256 for c in checks if c.contract_sha256}}]
    return PlanReport(checks, issues, effect, state, card, evidence, request_id, run_id)


@dataclass(frozen=True)
class StepRun:
    """One step the executor was called for: ``ok`` or ``failed``, with its answer or why it failed."""

    index: int
    component: str
    op: str
    status: str
    result: Any = None
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "component": self.component, "op": self.op, "status": self.status,
                "result": _plain(self.result), "error": self.error}


@dataclass(frozen=True)
class PlanResult:
    """``run_plan``'s answer: the check it ran first, every step it ran, and the state of the whole."""

    report: PlanReport
    runs: tuple[StepRun, ...]
    completion_state: str
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""

    def __post_init__(self) -> None:
        _state(self.completion_state)

    @property
    def executed(self) -> int:
        return sum(1 for r in self.runs if r.status == "ok")

    def to_dict(self) -> dict[str, Any]:
        return {"report": self.report.to_dict(), "runs": [r.as_dict() for r in self.runs],
                "executed": self.executed, "completion_state": self.completion_state,
                "warning_card": self.warning_card, "evidence": self.evidence, "request_id": self.request_id,
                "run_id": self.run_id}


def _run_step(check: StepCheck, executor: Executor) -> StepRun:
    try:
        reply = executor(check.component, check.op, dict(check.args))
    except Exception as exc:  # noqa: BLE001 - a step that raises is a failed step
        return StepRun(check.index, check.component, check.op, "failed",
                       error=f"raised {type(exc).__name__}: {exc}"[:_ERROR_CHARS])
    kind = type(reply).__name__
    if kind == "Failure":  # returns.result.Failure: how g6.core.registry.invoke answers a refused call
        return StepRun(check.index, check.component, check.op, "failed", error=str(reply.failure())[:_ERROR_CHARS])
    if kind == "Success":
        reply = reply.unwrap()
    # A step hands a component's answer to whoever reads the plan, so it goes through the door (plan A1): what is kept
    # is the door's copy, which claims no more than the door decided.
    from returns.result import Success

    from g6.core.door import door_reply

    reply = door_reply(check.component, check.op, Success(reply), caller=dict(check.args))["result"]
    declared = outcome_of(reply)
    if declared in (BLOCKED, "error"):
        return StepRun(check.index, check.component, check.op, "failed", reply, f"its answer declares {declared}")
    return StepRun(check.index, check.component, check.op, "ok", reply)


def run_plan(steps: Any, *, executor: Executor | None = None, contracts: Contracts | None = None,
             authority: Authority | None = None, max_effect: str | None = None, request_id: str = "",
             run_id: str = "") -> PlanResult:
    """Run ``steps`` in order through ``executor``, only after ``check_plan`` (same arguments) passes the whole plan.
    No executor: nothing runs. The first failed step (it raised, its answer is a ``Failure``, or it declares a block or
    an error) stops the run."""
    report = check_plan(steps, contracts=contracts, authority=authority, max_effect=max_effect,
                        request_id=request_id, run_id=run_id)

    def result(runs: list[StepRun], state: str, code: str, message: str, action: str) -> PlanResult:
        ran = {"kind": "plan_run", "executed": sum(1 for r in runs if r.status == "ok"),
               "failed": [r.index for r in runs if r.status == "failed"], "not_run": len(report.steps) - len(runs),
               "not_checked": ["each step's result: running a step is not checking it, and a 'verified' a step's own "
                               "answer declares is not carried"]}
        return PlanResult(report, tuple(runs), state, _card(code, message, action, state), [*report.evidence, ran],
                          request_id, run_id)

    if not report.valid:
        return result([], BLOCKED, report.warning_card["code"], f"Nothing ran: {report.warning_card['message']}",
                      "Fix every problem in report.issues and run the plan again.")
    if executor is None:
        return result([], BLOCKED, NO_EXECUTOR, "The plan passed the static check, but no executor was given, so "
                                                "nothing ran.",
                      "Pass executor=..., for example g6.core.registry.get_registry().invoke.")
    runs: list[StepRun] = []
    for check in report.steps:
        runs.append(_run_step(check, executor))
        if runs[-1].status == "failed":
            failed = runs[-1]
            return result(runs, BLOCKED, STEP_FAILED,
                          f"Step {failed.index} ({failed.component}.{failed.op}) failed: {failed.error}. "
                          f"{len(report.steps) - len(runs)} later step(s) did not run.",
                          "Inspect the failed step; the steps before it ran and may have had their effects.")
    return result(runs, QUALIFIED, RAN_UNCHECKED, "Every step ran. Running a step is not checking its result, so "
                                                  "this is a draft, not verified.",
                  "Check each step's result before relying on it.")


__all__ = ["COMPONENT_UNLOADABLE", "EFFECT_OVER_BUDGET", "EMPTY_PLAN", "INPUT_VALIDATION", "NO_EXECUTOR",
           "RAN_UNCHECKED", "SENSITIVE", "STATIC_CHECK", "STEP_FAILED", "STEP_FIELDS", "STEP_MALFORMED",
           "UNKNOWN_COMPONENT", "UNKNOWN_OP", "Executor", "Issue", "PlanReport", "PlanResult", "StepCheck", "StepRun",
           "check_plan", "run_plan"]
