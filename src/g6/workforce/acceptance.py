# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What counts as done, and how strongly it was checked (Phase E Part 2: E2c, E7d; Ledger §7, §10, §16).

A mission's acceptance contract (Ledger §7) is compiled from the intent engine's own ``IntentContract``
(``compile_contract``):
* its checkpoints become requirements a closed set of model-free predicates measures (``PREDICATES``);
* a success criterion no checkpoint names is a requirement G6 cannot check, which holds the result at
  qualified-draft and is recorded as unmet demand;
* its risk tier sets what every output's checks need and the state the mission must reach (Doctrine §7 tiers 0 to 3;
  Ledger §16: by risk class, objective first);
* whether the owner's spec is complete is the intent engine's own ``IntentElicitor``'s decision (E7d), recorded in
  ``metadata``.

``evaluate`` measures a contract against facts ``gather`` takes from the ledger's rows and applies it to the
roll-up:
* an unmet or unchecked requirement holds the result at qualified-draft;
* a result below its required state is blocked-escalated, unless the only shortfall is an allowed degradation;
* nothing is ever promoted.

Each check's Ledger §10 mechanism is recorded on its attempt (``mechanism_record``). A coarse class (none to
objective) summarises them per output and per mission while keeping every record. Pure: no I/O. The runtime and the
auditor each pass rows they read themselves.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from g6.core import risk_signals
from g6.intent_engine.elicitor import IntentElicitor
from g6.intent_engine.schema import IntentContract
from g6.workforce.contracts import (
    AUTHORITY_COPY,
    BLOCKED,
    QUALIFIED,
    VERDICT_RESULTS,
    VERIFIED,
    VERIFIER_GRADE,
    Outcome,
    lookup,
    rank,
)
from g6.workforce.economics import BUDGET_CURRENCY, Money, hard_limit
from g6.workforce.plans import (
    EFFECT_TIERS,
    RISK_TIERS,
    Plan,
    effect_of,
    higher,
)

#: Doctrine §7's four tiers, by the plan's risk tier (plan ruling P2.12).
TIERS = {"R0": "exploratory", "R1": "ordinary", "R2": "consequential", "R3": "high_consequence"}
#: What each tier asks of every output's checks (Doctrine §7; Ledger §16).
VERIFICATION_BY_TIER = {"R0": ("output_checked",), "R1": ("output_checked",), "R2": ("independent_check",),
                        "R3": ("objective_check", "person_approved")}
VERIFICATION_REQUIREMENTS = {  # each is measured as passed, so that is what it says (fix pass M3)
    "output_checked": "every result passed a check",
    "independent_check": "every result passed an independent check",
    "objective_check": "every result passed an objective check (a test, a proof or an exact recomputation)",
    "person_approved": "a person approved the work before it was used",
}
#: Ledger §10's mechanisms, the objective ones, and the coarse classes that summarise them (plan ruling P2.14).
MECHANISMS = ("schema_validation", "deterministic_computation", "test_execution", "external_state_check",
              "authoritative_source", "cross_source_confirmation", "independent_model_review", "human_review",
              "formal_proof_check", "receipt_validation", "manual_operator_check", "other")
OBJECTIVE = frozenset({"schema_validation", "deterministic_computation", "test_execution", "external_state_check",
                       "formal_proof_check", "receipt_validation"})
STRENGTHS = ("none", "weak", "moderate", "strong", "objective")
#: The check kinds that are objective verifiers (Ledger §7 ``objective_verifiers``).
OBJECTIVE_KINDS = ("formal", "test", "deterministic")
#: Ops whose check is a receipt check (the outbox's).
RECEIPT_OPS = frozenset({("workforce", "check_receipts")})
#: The closed set of model-free predicates, with the parameters each takes (plan ruling P2.11).
PREDICATES: dict[str, dict[str, type]] = {
    "output_present": {"unit": str},
    "fields_present": {"unit": str, "fields": list},
    "field_equals": {"unit": str, "field": str, "equals": object},
    "each_has": {"unit": str, "list": str, "field": str},
    "check_passed": {"unit": str},
    "tests_passed": {"unit": str},
    "approved": {"unit": str},
    "receipt": {"unit": str},
    "state_confirmed": {"unit": str},  # E10b: what an irreversible step changed was confirmed (ruling E10.10)
}
#: Degradations a contract may accept below its required state: nothing was sent (plan ruling P2.13).
DEGRADATIONS = ("shadow",)
REQUIRED_STATES = (VERIFIED, QUALIFIED)
#: Tiers whose contract the owner confirms before a plan is stored (E7d; plan ruling P2.9).
CONFIRM_TIERS = ("R2", "R3")
#: What a contract's own ``acceptance`` object may set.
OVERRIDES = ("required_completion_state", "allowed_degradation", "max_latency_seconds", "formal_standard")
#: The formal analysis a coding mission may ask for (PGA P1.4): advisory evidence, or required (a cap when missing).
FORMAL_MODES = ("advisory", "required")
MET, UNMET, UNMEASURED = "met", "unmet", "unmeasured"
CANNOT_CHECK = "no check G6 can run measures it as written"
_NAME_CHARS = 200
#: The rules a standard was derived under, by version (TS §36): a contract keeps the version it was derived under, so a
#: later rule never changes a standard already confirmed. "" is a contract kept before standards were derived (Part 2),
#: or one whose template derived its own standard (A3's objective entry; ``compile_contract(derive=False)``).
STANDARD_POLICY = "e10b-1"
#: Where a standard came from (TS §6): the tier the mission was created with, the task's own words, its template, a
#: step of its plan, or something found while it ran (TS §34).
SOURCES = ("declared", "objective", "template", "step", "runtime")
#: The sources that say what the task is, whatever its plan rates its steps (ruling E10.9).
CODE_DERIVED = ("objective", "template")
#: Each Doctrine tier back to its code.
CODES = {name: code for code, name in TIERS.items()}
#: Each tier as a person reads it (TS §28's "Standard" line; Design §4.1).
# COPY: the stakes, in the words the Standard line uses.
TIER_WORDS = {"R0": "exploratory", "R1": "ordinary", "R2": "consequential", "R3": "high consequence"}
#: Each side effect in a person's words, for what raised a standard (Design §4.1), and its TS §9 reversibility.
# COPY: what a step does, in the "Why these stakes" line.
EFFECT_WORDS = {"read": "only reads", "local_write": "changes files here", "external": "reaches someone outside",
                "irreversible": "cannot be undone"}
REVERSIBILITY = {"read": "reversible", "local_write": "mostly_reversible", "external": "partially_reversible",
                 "irreversible": "irreversible"}
#: The checks that can confirm what a step changed in the outside world (TS §10 Tier 3; ruling E10.10).
STATE_MECHANISMS = ("external_state_check", "receipt_validation")
#: The failure surfaces G6 challenges, by the kind of work a step does (FE §12; TS §43; ruling E10.14). Kept small and
#: extended in place: a new kind or surface is added to this one table, never beside it. General work names none yet.
SURFACES = {
    "code": ("original_bug_still_present", "regression_introduced", "test_weakened", "edge_case_missed"),
    "external": ("approval_missing", "action_unconfirmed"),
}
SURFACE_IDS = frozenset(name for names in SURFACES.values() for name in names)
SURFACE_STATES = ("passed", "failed", "not_tested")
#: Each surface in a person's words (Design §4.1): the claim when it held, what failed when it did not, and the
#: question when it was not tested.
# COPY: the failure surfaces, in the words the result and the Trust Report use.
SURFACE_WORDS = {
    "original_bug_still_present": ("its tests pass", "its tests fail", "whether its tests pass"),
    "regression_introduced": ("nothing else broke", "something else broke", "whether anything else broke"),
    "test_weakened": ("its tests were not weakened", "its tests were weakened", "whether its tests were weakened"),
    "edge_case_missed": ("its tests catch small deliberate mistakes", "its tests missed small deliberate mistakes",
                         "whether its tests catch small deliberate mistakes"),
    "approval_missing": ("a person approved it first", "it went ahead without a person's approval",
                         "whether a person approved it first"),
    "action_unconfirmed": ("what it did was confirmed", "what it did could not be confirmed",
                           "whether what it did took effect"),
}
#: The surfaces only a G6-authored, model-free probe challenges (E10d: the mandatory one for consequential code).
PROBE_SURFACES = ("test_weakened", "edge_case_missed")
#: The bench refuses a patch that touches a test file and says so (``bench.run``'s ``integrity``). That is a STRUCTURAL
#: PROTECTION, not a check G6 ran on the tests' adequacy: it cannot fail on a delivered patch, so it has no negative
#: control and is never read as the ``test_weakened`` surface passing (founder ruling R-1, 2026-10-09; E10.22). Only a
#: check that can reject an introduced defect fills that surface (``code_surfaces(tests_weakened=)``, from verify_task's
#: own weakened-test comparison).
#: Who adjudicates a mission's result against its standard (TS §24, §27): G6's own code, never the worker.
ADJUDICATOR = "g6.acceptance.evaluate"


# --- requirements ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One thing that must hold for the mission to count as done: a predicate of the closed set, or ("") a statement
    no predicate measures. ``problem`` says why it cannot be measured as written (never met)."""

    name: str
    predicate: str = ""
    params: dict = field(default_factory=dict)
    problem: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "predicate": self.predicate, "params": dict(self.params), "problem": self.problem}


def requirement(name: Any, predicate: Any, params: Any) -> Requirement:
    """A requirement as written: anything written wrong keeps its ``problem`` (not checked, never met, no crash)."""
    name = " ".join(str(name or "").split())[:_NAME_CHARS] or "an unnamed requirement"
    predicate = str(predicate or "").strip()
    if predicate not in PREDICATES:
        return Requirement(name, "", {}, f"G6 has no check called {predicate!r}" if predicate else CANNOT_CHECK)
    given = dict(params) if isinstance(params, Mapping) else {}
    wanted = PREDICATES[predicate]
    problems = [f"{key!r} is missing" for key in wanted if key not in given]
    problems += [f"{key!r} is not a parameter of {predicate}" for key in given if key not in wanted]
    problems += [f"{key!r} must be a {kind.__name__}" for key, kind in wanted.items()
                 if key in given and kind is not object and not isinstance(given[key], kind)]
    if isinstance(given.get("fields"), list) and not all(isinstance(f, str) for f in given["fields"]):
        problems.append("'fields' must be a list of field paths")
    if given.get("fields") == []:  # never met by default (fix pass M2)
        problems.append("'fields' names no field, so it would check nothing")
    return Requirement(name, predicate, given, "; ".join(problems))


def unit_requirements(plan: Plan) -> tuple[Requirement, ...]:
    """A plan's own unit checks, each measured on its unit (a verify unit's on the unit it judges)."""
    out = []
    for spec in plan.units:
        for check in spec.checks:
            params = dict(check.params)
            if check.predicate in PREDICATES and "unit" in PREDICATES[check.predicate]:
                params.setdefault("unit", spec.verifies if spec.kind == "verify" else spec.key)
            out.append(requirement(f"{spec.key}: {check.name}", check.predicate, params))
    return tuple(out)


# --- the contract (Ledger §7) ------------------------------------------------------------------------------------------


def _strings(values: Any, allowed: Iterable[str], what: str) -> tuple[str, ...]:
    found = tuple(values or ())
    if not all(isinstance(v, str) and v in allowed for v in found):
        raise ValueError(f"{what} is a list of {sorted(allowed)}")
    return found


def _seconds(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class AcceptanceContract:
    """Ledger §7 for one mission (the mission id is the row it is stored on)."""

    requirements: tuple[Requirement, ...]
    verification_requirements: tuple[str, ...]
    risk_tier: str
    required_completion_state: str
    max_cost: Money | None = None
    max_latency_seconds: int | None = None
    deadline: str = ""
    human_approval_requirements: tuple[str, ...] = ()
    objective_verifiers: tuple[str, ...] = ()
    allowed_degradation: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    #: What the standard was derived from, each {source, tier, why, ...} (TS §4, §6; E10b).
    derived_from: tuple[dict, ...] = ()
    #: The rules it was derived under (TS §36); "" for a contract kept before standards were derived.
    policy_version: str = ""
    #: Its revision: a raised standard is the next one (TS §34; S15).
    revision: int = 1

    def as_dict(self) -> dict:
        out = {"requirements": [r.as_dict() for r in self.requirements],
               "verification_requirements": list(self.verification_requirements), "risk_tier": self.risk_tier,
               "required_completion_state": self.required_completion_state,
               "max_cost": self.max_cost.as_dict() if self.max_cost is not None else None,
               "max_latency_seconds": self.max_latency_seconds, "deadline": self.deadline,
               "human_approval_requirements": list(self.human_approval_requirements),
               "objective_verifiers": list(self.objective_verifiers),
               "allowed_degradation": list(self.allowed_degradation), "metadata": dict(self.metadata)}
        # Only when set, so a contract kept before E10b keeps its digest (Review Focus 1).
        if self.derived_from:
            out["derived_from"] = [dict(d) for d in self.derived_from]
        if self.policy_version:
            out["policy_version"] = self.policy_version
        if self.revision != 1:
            out["revision"] = self.revision
        return out

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), default=str)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Any) -> AcceptanceContract:
        """A stored contract, re-read through the same rules (``ValueError`` when it cannot be read)."""
        if not isinstance(data, Mapping):
            raise ValueError("an acceptance contract is a JSON object")  # noqa: TRY004 - one refusal type
        reqs = []
        for item in data.get("requirements") or ():
            if not isinstance(item, Mapping):
                raise ValueError("a requirement is a JSON object")  # noqa: TRY004 - one refusal type
            if item.get("predicate"):
                reqs.append(requirement(item.get("name"), item.get("predicate"), item.get("params")))
            else:
                reqs.append(Requirement(str(item.get("name") or "an unnamed requirement"), "", {},
                                        str(item.get("problem") or CANNOT_CHECK)))
        if data.get("risk_tier") not in TIERS.values():
            raise ValueError(f"risk_tier is one of {list(TIERS.values())}")
        if data.get("required_completion_state") not in REQUIRED_STATES:
            raise ValueError(f"required_completion_state is one of {list(REQUIRED_STATES)}")
        latency = data.get("max_latency_seconds")
        if latency is not None and not _seconds(latency):
            raise ValueError("max_latency_seconds is a whole number of seconds > 0")
        cost = data.get("max_cost")
        derived = data.get("derived_from") or ()
        if not isinstance(derived, list | tuple) or not all(
                isinstance(d, Mapping) and d.get("source") in SOURCES and d.get("tier") in RISK_TIERS for d in derived):
            raise ValueError(f"derived_from is a list of {{source, tier, why}}, a source one of {list(SOURCES)}")
        revision = data.get("revision", 1)
        if not (isinstance(revision, int) and not isinstance(revision, bool) and revision >= 1):
            raise ValueError("revision is a whole number >= 1")
        return cls(requirements=tuple(reqs),
                   verification_requirements=_strings(data.get("verification_requirements"),
                                                      VERIFICATION_REQUIREMENTS, "verification_requirements"),
                   risk_tier=data["risk_tier"], required_completion_state=data["required_completion_state"],
                   max_cost=Money.from_dict(cost) if cost is not None else None, max_latency_seconds=latency,
                   deadline=str(data.get("deadline") or ""),
                   human_approval_requirements=tuple(str(x) for x in data.get("human_approval_requirements") or ()),
                   objective_verifiers=_strings(data.get("objective_verifiers"), OBJECTIVE_KINDS,
                                                "objective_verifiers"),
                   allowed_degradation=_strings(data.get("allowed_degradation"), DEGRADATIONS, "allowed_degradation"),
                   metadata=dict(data.get("metadata") or {}), derived_from=tuple(dict(d) for d in derived),
                   policy_version=str(data.get("policy_version") or ""), revision=revision)


def spec_complete(objective: str, contract: IntentContract) -> tuple[bool, list[tuple[str, str]]]:
    """Whether the owner's spec is complete, as the intent engine's own elicitor decides it (E7d: by code, never by
    the chat), and the questions still open: none left, or the owner declined clarification. A checkpoint counts as
    a success criterion. A decline skips the other questions but never the success criteria: with nothing named to
    check, the spec is not complete (audit 005 IE-001). A placeholder ("x", "tbd") answers nothing, so an all-"x"
    spec is not complete either (IE-002)."""
    named = [*contract.success_criteria, *(c.name for c in contract.checkpoints)]
    session = IntentElicitor().elicit(str(objective), answers={"success_criteria": named} if named else {},
                                      prior=contract, user_declined=contract.declined_clarification)
    asked = [(q.field, q.question) for q in session.questions]
    criteria_asked = any(field == "success_criteria" for field, _ in asked)
    return (not asked or (contract.declined_clarification and bool(named) and not criteria_asked)), asked


def risk_code(contract: AcceptanceContract) -> str:
    """The contract's tier as its code (R0 to R3); one that is not a Doctrine tier counts as R3 (fail closed)."""
    return CODES.get(contract.risk_tier, "R3")


def _source(source: str, tier: str, why: str, **more: Any) -> dict:
    return {"source": source, "tier": tier, "why": why, **more}


def derive(objective: str, declared: str, floors: Iterable[Mapping] = ()) -> tuple[str, tuple[dict, ...]]:
    """The tier a task demands before its plan is known (TS §38 stages 1 and 3; E10b), and what it was derived from:
    the tier declared, the strongest signals in the task's own words (``mvp.core.risk_signals``; none when nothing
    matches, since the classifier's moderate default is not a signal), and any further floor (a template's). The
    highest wins; nothing lowers what was declared."""
    if declared not in TIERS:
        raise ValueError(f"risk tier must be one of {list(TIERS)}, got {declared!r}")
    # COPY: why each source set the stakes (the "Why these stakes" line and the Standard line read these).
    derived = [_source("declared", declared, "the stakes the mission was created with")]
    found = risk_signals.signals(objective)
    if found:
        top = found[0][0]
        derived += [_source("objective", tier, f"the task mentions '{term}'") for tier, term in found if tier == top]
    for extra in floors:
        if extra.get("source") not in SOURCES or extra.get("tier") not in RISK_TIERS:
            raise ValueError(f"a floor is {{source, tier, why}}, a source one of {list(SOURCES)}")
        derived.append(dict(extra))
    return higher(*(d["tier"] for d in derived)), tuple(derived)


def base_tier(contract: AcceptanceContract) -> str:
    """The stakes a mission was accepted at, before its plan (ruling E10.9): what a step the plan did not rate takes.
    The contract's own tier for one kept before standards were derived."""
    found = [d["tier"] for d in contract.derived_from if d.get("source") in ("declared", *CODE_DERIVED)]
    return higher(*found) if found else risk_code(contract)


def code_floor(contract: AcceptanceContract) -> str:
    """What the task itself demands of every step, whatever the plan rates it (ruling E10.9): its words and its
    template; R0 when nothing does."""
    return higher(*(d["tier"] for d in contract.derived_from if d.get("source") in CODE_DERIVED))


def describe_standard(tier: str, derived: Iterable[Mapping]) -> str:
    """The standard in a person's words (TS §28's "Standard" line): its stakes, and what raised them."""
    why = [str(d.get("why")) for d in derived if d.get("source") != "declared" and d.get("why")]
    # COPY: the Standard line when nothing raised the declared stakes.
    return f"{TIER_WORDS.get(tier, TIER_WORDS['R3'])} ({'; '.join(dict.fromkeys(why)) or 'as set for this mission'})"


def standard_words(contract: AcceptanceContract) -> str:
    """``describe_standard`` for a contract; "" for one kept before standards were derived (nothing is invented)."""
    return describe_standard(risk_code(contract), contract.derived_from) if contract.policy_version else ""


def weaker(given: AcceptanceContract, current: AcceptanceContract) -> list[str]:
    """How ``given`` would ask less than ``current``, in words ([] when it asks at least as much): a standard is never
    lowered automatically (TS §19, §35; STD-005)."""
    out = []
    # COPY: each way a contract would ask less, said in the refusal.
    if RISK_TIERS.index(risk_code(given)) < RISK_TIERS.index(risk_code(current)):
        out.append("lower the stakes")
    if rank(given.required_completion_state) < rank(current.required_completion_state):
        out.append("accept a weaker result")
    if set(current.verification_requirements) - set(given.verification_requirements):
        out.append("drop a check")
    if any(r not in given.requirements for r in current.requirements):
        out.append("drop a requirement")
    if set(current.human_approval_requirements) - set(given.human_approval_requirements):
        out.append("drop an approval")
    if set(current.objective_verifiers) - set(given.objective_verifiers):
        out.append("drop an objective check")
    if set(given.allowed_degradation) - set(current.allowed_degradation):
        out.append("allow more to degrade")
    return out


def strengthen(contract: AcceptanceContract, *, tier: str, derived_from: Iterable[Mapping] = (),
               requirements: Iterable[Requirement] = ()) -> AcceptanceContract:
    """The contract raised (TS §34; S15): at ``tier`` or above, with the ``requirements`` it lacks added, as the next
    revision, with what raised it appended to ``derived_from``. The same contract (the same object) when nothing
    rises, so detail that changes no requirement never asks the owner again (ruling E10.7). Never lowers anything."""
    now = risk_code(contract)
    new = higher(now, tier)
    added: list[Requirement] = []
    for found in requirements:  # a Requirement holds a dict, so it is compared, never hashed
        if found not in contract.requirements and found not in added:
            added.append(found)
    if new == now and not added:
        return contract
    seen = [dict(d) for d in contract.derived_from]
    sources = tuple(dict(d) for d in derived_from if dict(d) not in seen)
    raised = replace(contract, requirements=(*contract.requirements, *added),
                     derived_from=(*contract.derived_from, *sources), revision=contract.revision + 1)
    return floor(raised, new)


def for_plan(contract: AcceptanceContract, plan: Plan, effects: Callable[[str, str], str | None]) -> AcceptanceContract:
    """The standard once the plan's steps are known (TS §38 stages 2-4; E10b): the mission at least as high as its
    highest step (production-path S9), what raised it said step by step with its reversibility, and each step that
    cannot be undone owing a confirmation that what it changed took effect (TS §10 Tier 3; TS §46; ruling E10.10). The
    same contract when nothing rises."""
    base = base_tier(contract)
    derived, needs = [], []
    for unit in plan.units:
        if unit.kind != "do":
            continue
        effect = effect_of(unit.capabilities, effects)
        if RISK_TIERS.index(unit.risk) > RISK_TIERS.index(base):
            # COPY: why a step raised the stakes.
            why = (f"step '{unit.key}' {EFFECT_WORDS[effect]}" if EFFECT_TIERS[effect] == unit.risk
                   else f"step '{unit.key}' was planned at these stakes")
            derived.append(_source("step", unit.risk, why, unit=unit.key, reversibility=REVERSIBILITY[effect]))
        if effect == "irreversible":
            # COPY: the requirement an irreversible step owes.
            needs.append(requirement(f"{unit.key}: what it changed was confirmed", "state_confirmed",
                                     {"unit": unit.key}))
    return strengthen(contract, tier=higher(*(u.risk for u in plan.units)), derived_from=derived, requirements=needs)


def _deadline(text: Any) -> tuple[str, str]:
    """(the deadline as a UTC time, or ""; why it cannot be read, or "")."""
    text = str(text or "").strip()
    if not text:
        return "", ""
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        return "", f"the deadline {text[:80]!r} is not a date and time with a zone that G6 can read"
    return when.astimezone(UTC).isoformat(timespec="seconds"), ""


def floor(contract: AcceptanceContract, risk_tier: str) -> AcceptanceContract:
    """The contract never asks less than its mission's tier or its own (Doctrine §7; E10b: the higher of the two, so a
    standard raised above the tier a mission was created with keeps its rise). The tier's verification requirements
    are always in it, and a high-consequence one always needs a verified result, a person's approval and an objective
    check."""
    tier = higher(risk_tier, risk_code(contract))
    needs = tuple(dict.fromkeys((*contract.verification_requirements, *VERIFICATION_BY_TIER[tier])))
    if tier != "R3":
        return replace(contract, risk_tier=TIERS[tier], verification_requirements=needs)
    return replace(contract, risk_tier=TIERS[tier], verification_requirements=needs, required_completion_state=VERIFIED,
                   human_approval_requirements=tuple(dict.fromkeys((*contract.human_approval_requirements,
                                                                     "a_person_approves"))),
                   objective_verifiers=tuple(dict.fromkeys((*contract.objective_verifiers, *OBJECTIVE_KINDS))))


def compile_contract(*, objective: str, contract: IntentContract, risk_tier: str, budget_cents: Any,
                     overrides: Mapping[str, Any] | None = None, compiled_on_read: bool = False,
                     floors: Iterable[Mapping] = (), template_derived: bool = False) -> AcceptanceContract:
    """The mission's acceptance contract from the owner's clarified contract (E7d: the spec compiles into it), at the
    tier the task demands (E10b: the highest of the tier declared, the task's own words and ``floors``). A mission
    accepted before contracts were kept keeps the tier it was given, and so does one whose template derived its own
    standard by code (``template_derived``: A3's objective entry, ``coding._freeze`` and ``formatting.start``; ruling
    E10b-R1). ``overrides`` is the contract's own ``acceptance`` object, validated here: it may raise what the mission
    needs, never lower it (TS §35)."""
    if compiled_on_read or template_derived:
        if risk_tier not in TIERS:
            raise ValueError(f"risk tier must be one of {list(TIERS)}, got {risk_tier!r}")
        tier, derived = risk_tier, ()
    else:
        tier, derived = derive(objective, risk_tier, floors)
    over = dict(overrides or {})
    unknown = sorted(set(over) - set(OVERRIDES))
    if unknown:
        raise ValueError(f"acceptance: unknown field(s) {unknown}; it may set {list(OVERRIDES)}")
    needed = VERIFIED if tier == "R3" or contract.risk_tolerance == "low" else QUALIFIED
    asked = over.get("required_completion_state") or None
    if asked is not None and asked not in REQUIRED_STATES:
        raise ValueError(f"acceptance: required_completion_state is one of {list(REQUIRED_STATES)}")
    if asked is not None and rank(asked) < rank(needed):
        # COPY: the refusal when the chat asks for a weaker result than the mission needs.
        raise ValueError("acceptance: required_completion_state may raise what this mission needs, never lower it: it "
                         f"needs a {needed} result. Nothing was recorded (code G6_E_POLICY_BLOCKED)")
    required = asked or needed
    allowed = _strings(over.get("allowed_degradation"), DEGRADATIONS, "acceptance: allowed_degradation")
    latency = over.get("max_latency_seconds")
    if latency is not None and not _seconds(latency):
        raise ValueError("acceptance: max_latency_seconds is a whole number of seconds > 0")
    formal = over.get("formal_standard")
    if "formal_standard" in over:
        if not template_derived:
            # COPY: Refusal when the chat sets formal analysis instead of the coding mission's own creation input.
            raise ValueError('acceptance: formal_standard is set only by a coding mission created with "formal" in its '
                             "repository JSON. Nothing was recorded.")
        if not (isinstance(formal, dict) and set(formal) == {"mode"} and formal["mode"] in FORMAL_MODES):
            # COPY: Refusal of a malformed formal_standard in the acceptance object.
            raise ValueError(f'acceptance: formal_standard is exactly {{"mode": one of {list(FORMAL_MODES)}}}')
    named = {" ".join(str(c.name).split()) for c in contract.checkpoints}
    reqs = [requirement(c.name, c.predicate, c.params) for c in contract.checkpoints]
    reqs += [Requirement(" ".join(str(text).split())[:_NAME_CHARS], "", {}, CANNOT_CHECK)
             for text in contract.success_criteria if str(text).strip() and " ".join(str(text).split()) not in named]
    complete, questions = spec_complete(objective, contract)
    deadline, deadline_problem = _deadline(contract.deadline)
    hard, readable = hard_limit(budget_cents)
    metadata = {"plan_risk": risk_tier, "spec_complete": complete, "missing": [list(q) for q in questions],
                "completeness_method": "intent_engine.IntentElicitor (field completeness)",
                "assumptions": [a.statement for a in contract.assumptions], "deadline_problem": deadline_problem}
    if compiled_on_read:
        metadata["compiled"] = "on read: the mission was accepted before acceptance contracts were kept"
    if formal is not None:  # PGA P1.4: in the digest; it adds no requirement (requirements stay the required tests)
        metadata["formal_standard"] = {"mode": formal["mode"]}
    return floor(AcceptanceContract(
        requirements=tuple(reqs), verification_requirements=VERIFICATION_BY_TIER[tier], risk_tier=TIERS[tier],
        required_completion_state=required, max_cost=Money(hard, BUDGET_CURRENCY) if readable else None,
        max_latency_seconds=latency, deadline=deadline,
        human_approval_requirements=("external_ops_behind_a_gate", *(("a_person_approves",) if tier == "R3" else ())),
        objective_verifiers=OBJECTIVE_KINDS if tier == "R3" else (), allowed_degradation=allowed, metadata=metadata,
        derived_from=derived, policy_version=STANDARD_POLICY if derived else ""), tier)


def unreadable(risk_tier: str, why: str) -> AcceptanceContract:
    """A stored contract that cannot be read: it holds the result at qualified-draft (an R3 one blocks), fail closed."""
    lost = Requirement("the mission's acceptance contract", "", {}, f"it could not be read ({str(why)[:200]})")
    return floor(AcceptanceContract(requirements=(lost,), verification_requirements=(), risk_tier="exploratory",
                                    required_completion_state=QUALIFIED, metadata={"unreadable": True}), risk_tier)


def cannot_check(contract: AcceptanceContract) -> tuple[Requirement, ...]:
    """The requirements no predicate measures (unmet demand ``cannot_check``, E7d)."""
    return tuple(r for r in contract.requirements if not r.predicate)


# COPY: Tells the owner, before they confirm what counts as done, which functions the request declared as part of the change
# (founder, 2026-10-08): a difference in how these behave is reported to the person, not sent back as a defect.
DECLARED = "G6 will report, not reject, a change in how these behave, because the request declared them part of this change: {names}."


def describe(contract: AcceptanceContract) -> list[str]:
    """What counts as done, in plain lines (the owner reads these before confirming it)."""
    needed = "verified" if contract.required_completion_state == VERIFIED else "at least usable with caution"
    lines = [f"Stakes: {contract.risk_tier.replace('_', ' ')}. To count as done the result must be {needed}."]
    why = [str(d["why"]) for d in contract.derived_from if d.get("source") != "declared"]
    if why:
        # COPY: what raised the stakes, in the owner's confirmation text (E10b).
        lines.append(f"Why these stakes: {'; '.join(dict.fromkeys(why))}.")
    lines += [f"- {r.name}: " + ("G6 checks this" if not r.problem else f"G6 cannot check this ({r.problem})")
              for r in contract.requirements]
    lines += [f"- {VERIFICATION_REQUIREMENTS[v]}" for v in contract.verification_requirements]
    executor = (contract.metadata.get("coding") or {}).get("executor")
    if executor:
        params = executor["params"]
        lines.append(f"Selected executor: {params['adapter']} with {params['model']['provider']}/{params['model']['model']}. "
                     "G6 controls source-only scratch drafting and executed-check feedback within the frozen proposal limit. "
                     "No provider or host fallback; local costs may be unknown. Separate checks decide assurance.")
    constructed = (contract.metadata.get("coding") or {}).get("harness_intelligence") or {}
    if constructed:
        # COPY: Explain the added obligation and its assumptions before confirmation.
        lines += [f"Predicted failure: {h['prediction']}" for h in constructed.get("hypotheses", [])]
        lines += [f"Added check: an identical event retry after a different event must leave the same state "
                  f"({c['predicate_id']}); evidence is nonindependent." for c in constructed.get("checks", [])]
        lines += [f"Check limit: {limit}" for limit in constructed.get("limits", [])]
    declared = (contract.metadata.get("coding") or {}).get("declared") or []
    if declared:
        lines.append(DECLARED.format(names=", ".join(str(d.get("function")) for d in declared)))
    missing = contract.metadata.get("missing") or []
    if missing:
        lines.append("Not yet answered: " + " ".join(str(q[1]) for q in missing))
    return lines


# --- mechanisms (Ledger §10) -------------------------------------------------------------------------------------------


def mechanism_of(kind: str, model_free: bool, op: Iterable[str]) -> str:
    """The Ledger §10 mechanism a check of this kind is (plan ruling P2.14)."""
    if tuple(op) in RECEIPT_OPS:
        return "receipt_validation"
    found = {"formal": "formal_proof_check", "test": "test_execution", "deterministic": "deterministic_computation",
             "model_critic": "independent_model_review", "human": "human_review"}.get(kind)
    if found:
        return found
    return "deterministic_computation" if kind == "oracle" and model_free else "other"


def tally(answer: Any) -> dict:
    """What a test check counted, when its answer carries a map of test to pass or fail (the bench's ``tests``)."""
    tests = answer.get("tests") if isinstance(answer, Mapping) else None
    if isinstance(tests, Mapping) and tests and all(isinstance(v, bool) for v in tests.values()):
        return {"passed": sum(1 for v in tests.values() if v), "total": len(tests)}
    return {}


def mechanism_record(*, check: str, judges: str, artifact_id: str, kind: str, op: Iterable[str], model_free: bool,
                     executed: bool, independent: bool, result: str, measured: Mapping | None = None,
                     authority: str = "", surfaces: Mapping | None = None) -> dict:
    """One check's attempt as Ledger §10 records it, kept on its ``run.closed`` event. ``authority``: the class its
    oracle earned at this use (Gate W), kept only when set. ``surfaces``: the failure surfaces the check itself reports
    (E10c), kept only when it reports one."""
    op = tuple(op)
    out = {"mechanism": mechanism_of(kind, model_free, op), "check": check, "judges": judges,
           "artifact_id": artifact_id, "op": ".".join(op), "kind": kind, "executed": bool(executed),
           "independent": bool(independent), "model_free": bool(model_free), "result": result,
           "measured": dict(measured or {})}
    if authority:
        out["authority"] = authority
    if surfaces:  # E10c: the failure surfaces the check itself reports; only when it reports one (older shape kept)
        out["surfaces"] = {str(name): str(status) for name, status in surfaces.items()}
    return out


def unit_strength(records: Iterable[Mapping]) -> str:
    """One output's coarse class from the checks on it (plan ruling P2.14); a fail a person set aside is not a pass."""
    found = list(records)
    standing = [r for r in found if not r.get("set_aside")]
    passed = [r for r in standing if r.get("executed") and r.get("result") == "pass"]
    if any(r.get("mechanism") in OBJECTIVE and r.get("independent") and r.get("model_free") for r in passed):
        return "objective"
    independent = [r for r in passed if r.get("independent")]
    if len(independent) >= 2:
        return "strong"
    if independent:
        return "moderate"
    if passed or any(r.get("mechanism") == "human_review" for r in found):
        return "weak"
    return "none"


def weakest(strengths: Iterable[str]) -> str:
    found = list(strengths)
    return min(found, key=STRENGTHS.index) if found else "none"


def code_surfaces(*, tests_ran: bool = False, tests_good: bool = False, mutations_tried: int = 0,
                  mutations_caught: int = 0, tests_weakened: bool | None = None) -> dict[str, str]:
    """A code change as verify_task checked it (E10c): the caller's tests against the problem, small deliberate changes
    against their blind spots, and (E10a) whether a test was weakened. What did not run is absent, so it reads not
    tested."""
    found = {}
    if tests_ran:
        found["original_bug_still_present"] = "passed" if tests_good else "failed"
    if mutations_tried > 0:
        found["edge_case_missed"] = "passed" if mutations_caught >= mutations_tried else "failed"
    if tests_weakened is not None:
        found["test_weakened"] = "failed" if tests_weakened else "passed"
    return found


#: What verify_task counts when it reruns the ORIGINAL versions of the changed tests on the new code: that check
#: reports on whether a test was weakened, never on the problem the tests were written for.
OLD_TEST_KEYS = ("old_passed", "old_failed", "old_not_run")


def _count(measured: Mapping, key: str) -> int | None:
    value = measured.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _reports_on_the_problem(measured: Mapping) -> bool:
    """Whether a test run challenged the problem: not a rerun of the original tests, and not a run in which nothing passed
    and nothing failed (every test skipped), which exercised nothing."""
    if any(key in measured for key in OLD_TEST_KEYS):
        return False
    return not (_count(measured, "passed") == 0 and _count(measured, "failed") == 0)


def exercised(record: Mapping) -> dict[str, str]:
    """The failure surfaces one check's attempt challenged, and how each came out (FE §12; E10c): what the check
    recorded under ``surfaces`` (only ids of the vocabulary), a test run's own result for the problem it was run
    against, and a receipt check's failure for whether the action took effect. A check that did not execute, whose
    fail a person set aside, or that could not decide, challenged nothing."""
    if not record.get("executed") or record.get("set_aside") or record.get("result") not in ("pass", "fail"):
        return {}
    found = {str(name): str(status) for name, status in (record.get("surfaces") or {}).items()
             if name in SURFACE_IDS and status in ("passed", "failed")}
    if record.get("mechanism") == "test_execution" and _reports_on_the_problem(record.get("measured") or {}):
        found.setdefault("original_bug_still_present", "passed" if record["result"] == "pass" else "failed")
    if record.get("mechanism") == "receipt_validation" and record["result"] == "fail":
        found.setdefault("action_unconfirmed", "failed")
    return found


def unit_classes(plan: Plan, spec: Any, effects: Callable[[str, str], str | None] | None = None) -> tuple[str, ...]:
    """The kinds of work a step does, for the surfaces G6 challenges (ruling E10.14): ``code`` when a check of kind
    ``test`` judges it; ``external`` when one of its ops reaches outside or cannot be undone (known only with the
    broker's side effects); () for general work."""
    out = []
    if any(u.kind == "verify" and u.verifies == spec.key and (u.oracle or {}).get("kind") == "test" for u in plan.units):
        out.append("code")
    if effects is not None and effect_of(spec.capabilities, effects) in ("external", "irreversible"):
        out.append("external")
    return tuple(out)


def _worse(found: dict[str, str], name: str, status: str) -> None:
    """A surface's status across checks: one that failed stays failed."""
    found[name] = "failed" if "failed" in (status, found.get(name)) else status


def _acted(unit: str, facts: Facts) -> dict[str, str]:
    """A step that acts outside, challenged on its approval (its gates' decisions) and its outcome (its receipts): a
    send refused in shadow did nothing, so its outcome reads not tested. A rejection that nothing went ahead after
    challenged nothing (a person said no and it did not happen), so it reads not tested; it fails only if the step went
    ahead without every gate before it approved."""
    out: dict[str, str] = {}
    decided = [facts.approvals.get(gate) for gate in facts.guards.get(unit, ())]
    states = facts.sends.get(unit, ())
    if decided and all(d == "approve" for d in decided):
        out["approval_missing"] = "passed"
    elif decided and any(s in ("sent", "unknown") for s in states):
        out["approval_missing"] = "failed"
    if "unknown" in states:
        out["action_unconfirmed"] = "failed"
    elif states and all(s == "sent" for s in states):
        out["action_unconfirmed"] = "passed"
    return out


def surfaces_of(facts: Facts) -> tuple[tuple[str, str, str], ...]:
    """Each step's failure surfaces as (step, surface, passed | failed | not_tested), in vocabulary order (FE §12;
    E10c). A surface no check exercised reads not_tested, never passed."""
    out = []
    for unit in facts.do_units:
        found: dict[str, str] = {}
        for record in facts.checks.get(unit, ()):
            for name, status in exercised(record).items():
                _worse(found, name, status)
        kinds = facts.classes.get(unit, ())
        if "external" in kinds:
            for name, status in _acted(unit, facts).items():
                _worse(found, name, status)
        out += [(unit, name, found.get(name, "not_tested")) for kind in kinds for name in SURFACES.get(kind, ())]
    return tuple(out)


def surface_text(name: str, status: str) -> str:
    """A surface in a person's words: the claim when it held, what failed when it did not, the question when it was
    not tested."""
    claim, failed, question = SURFACE_WORDS[name]
    return {"passed": claim, "failed": failed}.get(status, question)


#: The FE §5 strategy that challenges each surface: what kind of attempt to break the work it was.
STRATEGY_OF = {"original_bug_still_present": "test_execution", "regression_introduced": "test_execution",
               "test_weakened": "test_integrity", "edge_case_missed": "mutation",
               "approval_missing": "approval_check", "action_unconfirmed": "state_reconciliation"}
#: FE §28's fields, as one verify cycle's record holds them (the ledger adds the cost and the latency).
ELICITATION_FIELDS = ("work_unit_id", "artifact_id", "risk_tier", "known_failure_signatures_considered",
                      "elicitation_strategies", "probes_executed", "counterexamples_found", "evidence_gaps",
                      "assumptions_challenged", "novel_failures", "failures_confirmed", "false_alarms",
                      "remaining_unchecked_surfaces", "cost", "latency_seconds", "verdict", "policy_version")


def _counterexamples(measured: Mapping) -> int:
    """Failures a check found, by its own count: tests that failed, and small deliberate changes its tests missed."""
    def whole(key: str) -> int | None:
        value = measured.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    failed, passed, total = whole("failed"), whole("passed"), whole("total")
    if failed is None and passed is not None and total is not None:
        failed = max(0, total - passed)
    tried, caught = whole("mutations_tried"), whole("mutations_caught")
    return (failed or 0) + (max(0, tried - caught) if tried is not None and caught is not None else 0)


def elicitation_record(*, unit: str, artifact_id: str, risk_tier: str, classes: Iterable[str],
                       records: Iterable[Mapping], verdict: str) -> dict:
    """One verify cycle's FailureElicitationRecord (FE §28; E10c), kept on the check's ``run.closed`` detail: the
    surfaces it was to challenge, the strategies and checks that ran, what they found and what stayed unchecked. Ids,
    counts and codes only: never a probe's input, a counterexample or any content (S6; ruling E10.16), on any host.
    The ledger that closes the attempt adds its cost and latency."""
    found_records = [dict(r) for r in records]
    wanted = [name for kind in classes for name in SURFACES.get(kind, ())]
    found: dict[str, str] = {}
    for record in found_records:
        for name, status in exercised(record).items():
            _worse(found, name, status)
    ran = [r for r in found_records if r.get("executed") and r.get("mechanism") in MECHANISMS]
    gaps = [r for r in found_records if not r.get("executed") and r.get("mechanism") in MECHANISMS]
    return {"work_unit_id": str(unit), "artifact_id": str(artifact_id),
            "risk_tier": risk_tier if risk_tier in RISK_TIERS else "",
            "known_failure_signatures_considered": wanted,
            "elicitation_strategies": sorted({STRATEGY_OF[name] for name in found}),
            "probes_executed": sorted({str(r["mechanism"]) for r in ran}),
            "counterexamples_found": sum(_counterexamples(r.get("measured") or {}) for r in ran),
            "evidence_gaps": sorted({str(r["mechanism"]) for r in gaps}),
            "assumptions_challenged": [], "novel_failures": [],
            "failures_confirmed": sorted(name for name, status in found.items() if status == "failed"),
            "false_alarms": [],
            "remaining_unchecked_surfaces": sorted(name for name in wanted if name not in found),
            "verdict": verdict if verdict in VERDICT_RESULTS else "unmeasured", "policy_version": STANDARD_POLICY}


# --- facts and measurement ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """What a contract is measured on, taken from the ledger's rows (``gather``)."""

    do_units: tuple[str, ...]
    outputs: Mapping[str, Any]
    checks: Mapping[str, tuple[dict, ...]]
    approvals: Mapping[str, str]
    sends: Mapping[str, tuple[str, ...]]
    degradations: Mapping[str, tuple[str, ...]]
    finished_at: str = ""
    elapsed_seconds: int | None = None
    #: The kinds of work each step does (``unit_classes``), and the gates upstream of each (E10c).
    classes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    guards: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def _elapsed(start: str, end: str) -> int | None:
    try:
        return max(0, int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()))
    except (TypeError, ValueError):
        return None


def _from_verdict(plan: Plan, verdict: Mapping) -> dict:
    """A check closed before Ledger §10 records were kept, read from its verdict row: executed, independent,
    model-free and its result as the row holds them, its mechanism from the plan's oracle kind (fix pass I1)."""
    try:
        oracle = plan.unit(verdict["unit_key"]).oracle or {}
    except KeyError:
        oracle = {}
    return mechanism_record(check=verdict["unit_key"], judges=verdict["verifies_key"],
                            artifact_id=verdict["artifact_id"], kind=str(oracle.get("kind") or ""),
                            op=tuple(oracle.get("capability") or ()), model_free=bool(verdict["model_free"]),
                            executed=bool(verdict["executed"]), independent=bool(verdict["independent"]),
                            result=str(verdict["result"]), authority=str(verdict.get("authority") or ""))


def gather(*, plan: Plan, runs: Iterable[Mapping], artifacts: Mapping[str, Mapping], closed: Iterable[Mapping],
           decisions: Iterable[Mapping], receipts: Iterable[Mapping], verdicts: Iterable[Mapping] = (),
           created_at: str = "", finished_at: str = "", questions: Iterable[Mapping] = (),
           effects: Callable[[str, str], str | None] | None = None) -> Facts:
    """The facts, from rows the caller read: each do unit's latest output; every check's newest record on that output
    (a person's set-aside of a fail marks it and adds their judgement); each gate's decision; the receipt states of
    each unit's sends; what degraded each output. A check whose ``run.closed`` carries no records (it closed before
    they were kept) is read from its verdict row; a pass the ledger refused (blocked) counts for nothing. ``questions``
    (oldest first): a gate stands as its latest asked question does, undecided while that one waits (SR3-2, review
    M1)."""
    runs = list(runs)
    closed = list(closed)
    latest: dict[str, Mapping] = {}
    for run in runs:
        if run["state"] == "closed" and run["artifact_id"]:
            latest[run["unit_key"]] = run
    outputs = {key: (artifacts.get(run["artifact_id"]) or {}).get("content") for key, run in latest.items()}
    found: dict[str, dict[str, dict]] = {}
    recorded = {detail.get("run_id") for detail in closed if "verification" in detail}
    for verdict in verdicts:  # oldest first: the newest record of each check on the latest output stands
        judged = latest.get(verdict["verifies_key"])
        if (verdict["run_id"] in recorded or judged is None or verdict["artifact_id"] != judged["artifact_id"]
                or (verdict["result"] == "pass" and verdict["state"] == BLOCKED)):
            continue
        found.setdefault(verdict["verifies_key"], {})[verdict["unit_key"]] = _from_verdict(plan, verdict)
    for detail in closed:
        for record in detail.get("verification") or ():
            judged = latest.get(record.get("judges", ""))
            if judged is not None and record.get("artifact_id") == judged["artifact_id"]:
                found.setdefault(record["judges"], {})[record["check"]] = dict(record)
    approvals: dict[str, str] = {}
    answered: dict[str, str] = {}
    for decision in decisions:
        if decision["kind"] == "gate":  # a gate asked again ("<gate>#<n>") is its gate's latest answer (SR3-2)
            answered[str(decision["unit_key"])] = decision["decision"]
            approvals[str(decision["unit_key"]).partition("#")[0]] = decision["decision"]
            continue
        verifier = next((u for u in plan.units if u.key == decision["unit_key"]), None)
        judged = found.get(verifier.verifies, {}) if verifier is not None else {}
        if decision["decision"] == "set_aside" and decision["unit_key"] in judged:
            judged[decision["unit_key"]]["set_aside"] = True
            judged[f"{decision['unit_key']} (a person)"] = {
                "mechanism": "human_review", "check": decision["unit_key"], "judges": verifier.verifies,
                "artifact_id": judged[decision["unit_key"]].get("artifact_id", ""), "op": "", "kind": "human",
                "executed": True, "independent": True, "model_free": True, "result": "set_aside", "measured": {}}
    latest_asked: dict[str, str] = {}
    for asked in questions:
        if asked.get("kind") == "gate":
            latest_asked[str((asked.get("detail") or {}).get("reapproves") or asked.get("unit"))] = str(asked.get("unit"))
    for gate, key in latest_asked.items():  # review M1: a gate whose latest question waits for a person is undecided
        if key in answered:
            approvals[gate] = answered[key]
        else:
            approvals.pop(gate, None)
    unit_of = {run["run_id"]: run["unit_key"] for run in runs}
    sends: dict[str, list[str]] = {}
    for receipt in receipts:
        sends.setdefault(unit_of.get(receipt.get("run_id", ""), ""), []).append(str(receipt.get("state", "")))
    degradations: dict[str, tuple[str, ...]] = {}
    for key, run in latest.items():
        outcome = Outcome.from_record(json.loads(run.get("outcome_json") or "{}"))
        if outcome.degraded:
            texts = tuple(r.removeprefix("degraded: ") for r in outcome.reasons if r.startswith("degraded: "))
            degradations[key] = texts or ("no reason was given",)
    for key, records in found.items():
        outcomes = [Outcome.from_record(record.get("outcome")) for record in records.values()]
        texts = tuple(r.removeprefix("degraded: ") for outcome in outcomes if outcome.degraded
                      for r in outcome.reasons if r.startswith("degraded: "))
        if texts:
            degradations[key] = tuple(dict.fromkeys((*degradations.get(key, ()), *texts)))
    return Facts(do_units=tuple(u.key for u in plan.units if u.kind == "do"), outputs=outputs,
                 checks={key: tuple(value.values()) for key, value in found.items()}, approvals=approvals,
                 sends={key: tuple(value) for key, value in sends.items()}, degradations=degradations,
                 finished_at=finished_at, elapsed_seconds=_elapsed(created_at, finished_at),
                 classes={u.key: unit_classes(plan, u, effects) for u in plan.units if u.kind == "do"},
                 guards={u.key: tuple(sorted(k for k in plan.upstream(u.key) if plan.unit(k).kind == "gate"))
                         for u in plan.units if u.kind == "do"})


@dataclass(frozen=True)
class Measured:
    name: str
    status: str
    why: str
    predicate: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "why": self.why, "predicate": self.predicate}


def _passed_why(records: list[Mapping]) -> str:
    counted = [r["measured"] for r in records if r.get("measured")]
    if counted:
        return "; ".join(f"{m['passed']} of {m['total']} tests passed" for m in counted)
    return f"{len(records)} independent check{'' if len(records) == 1 else 's'} passed it"


def _same(value: Any, expected: Any) -> bool:
    """Equal, with a true/false value never equal to a number (Python's ``True == 1``; fix pass M2)."""
    if isinstance(value, bool) or isinstance(expected, bool):
        return isinstance(value, bool) and isinstance(expected, bool) and value is expected
    return value == expected


def measure(req: Requirement, facts: Facts) -> Measured:
    """One requirement against the facts: met, unmet, or not measured with why (never met by default)."""
    def say(status: str, why: str) -> Measured:
        return Measured(req.name, status, why, req.predicate)

    if req.problem:
        return say(UNMEASURED, req.problem)
    params, unit = req.params, str(req.params.get("unit", ""))
    if req.predicate == "approved":
        decided = facts.approvals.get(unit)
        if decided is None:
            return say(UNMEASURED, f"no person has decided {unit!r}")
        return say(MET, f"a person approved {unit!r}") if decided == "approve" else say(
            UNMET, f"a person did not approve {unit!r}")
    if req.predicate == "receipt":
        states = facts.sends.get(unit, ())
        if not states:
            return say(UNMEASURED, f"{unit!r} sent nothing that has a receipt")
        sent = sum(1 for s in states if s == "sent")
        if sent == len(states):
            return say(MET, f"all {sent} messages have a sent receipt")
        return say(UNMET, f"{len(states) - sent} of {len(states)} messages were not sent")
    if unit not in facts.do_units:
        return say(UNMEASURED, f"the plan has no step {unit!r} that produces a result")
    if req.predicate in ("check_passed", "tests_passed", "state_confirmed"):
        if req.predicate == "state_confirmed":
            found = [r for r in facts.checks.get(unit, ()) if r.get("executed") and not r.get("set_aside")
                     and r.get("mechanism") in STATE_MECHANISMS]
            # COPY: what a person reads about a confirmation of an irreversible step's outside state.
            if any(r.get("result") == "fail" for r in found):
                return say(UNMET, f"a check found that what {unit!r} changed did not take effect")
            if any(r.get("result") == "pass" and r.get("independent") for r in found):
                return say(MET, f"a check confirmed what {unit!r} changed")
            return say(UNMEASURED, f"no check confirmed what {unit!r} changed in the outside world (G6 has no such "
                                   "check for this step yet)")
        observed = [r for r in facts.checks.get(unit, ()) if r.get("executed")]
        if req.predicate == "tests_passed":
            observed = [r for r in observed if r.get("mechanism") == "test_execution"]
        records = [r for r in observed if not r.get("set_aside")]
        if any(r.get("result") == "fail" for r in records):
            return say(UNMET, f"a check found a problem in {unit!r}")
        passed = [r for r in records if r.get("result") == "pass" and r.get("independent")]
        if passed:
            return say(MET, _passed_why(passed))
        if records:
            subject = "tests" if req.predicate == "tests_passed" else "checks"
            applicable = [r for r in records if (r.get("probe") or {}).get("status") != "not_applicable"]
            if not applicable:
                return say(UNMEASURED, f"the requested {subject} were not applicable to its latest result")
            if any(r.get("result") == "pass" for r in applicable):
                return say(UNMEASURED, f"{subject} passed on its latest result, but are not independent")
            return say(UNMEASURED, f"{subject} ran on its latest result, but were inconclusive")
        if observed:
            return say(UNMEASURED, "checks ran on its latest result, but their findings were set aside")
        return say(UNMEASURED, "no test ran on its latest result" if req.predicate == "tests_passed"
                   else "no independent check ran on its latest result")
    if unit not in facts.outputs:
        return say(UNMET, f"{unit!r} produced no result")
    content = facts.outputs[unit]
    if req.predicate == "output_present":
        return say(MET, f"{unit!r} produced its result")
    if req.predicate == "fields_present":
        absent = [path for path in params["fields"] if not lookup(content, path)[0]]
        return say(UNMET, f"its result lacks {absent}") if absent else say(MET, "its result has every field named")
    if req.predicate == "field_equals":
        found, value = lookup(content, params["field"])
        if not found:
            return say(UNMET, f"its result has no {params['field']!r}")
        if _same(value, params["equals"]):
            return say(MET, f"{params['field']} is {value!r}")
        return say(UNMET, f"{params['field']} is {value!r}, not {params['equals']!r}")
    found, items = lookup(content, params["list"])  # each_has
    if not found or not isinstance(items, list):
        return say(UNMET, f"its result has no list at {params['list']!r}")
    if not items:
        return say(UNMEASURED, f"{params['list']!r} is empty, so there was nothing to check")
    lacking = sum(1 for item in items if not (isinstance(item, Mapping) and item.get(params["field"])))
    if lacking:
        return say(UNMET, f"{lacking} of {len(items)} items have no {params['field']!r}")
    return say(MET, f"each of {len(items)} items has a {params['field']!r}")


def _verification(name: str, facts: Facts) -> Measured:
    """One of the tier's verification requirements, over every do unit's latest output."""
    text = VERIFICATION_REQUIREMENTS[name]
    if name == "person_approved":
        if "approve" in facts.approvals.values():
            return Measured(text, MET, "a person approved a step of this mission")
        return Measured(text, UNMET, "no person approved any step of this mission")
    if not facts.do_units:
        return Measured(text, UNMET, "nothing the mission produced was checked")
    short = []
    for unit in facts.do_units:
        passed = [r for r in facts.checks.get(unit, ()) if r.get("executed") and r.get("result") == "pass"
                  and not r.get("set_aside")]
        if name == "independent_check":
            passed = [r for r in passed if r.get("independent")]
        elif name == "objective_check":
            passed = [r for r in passed if r.get("independent") and r.get("model_free")
                      and r.get("mechanism") in OBJECTIVE]
        if not passed:
            short.append(unit)
    if short:
        return Measured(text, UNMET, f"not so for {short}")
    return Measured(text, MET, f"so for each of {len(facts.do_units)} results")


def _limits(contract: AcceptanceContract, facts: Facts) -> tuple[Measured, ...]:
    out = []
    problem = contract.metadata.get("deadline_problem")
    if problem:
        out.append(Measured("finished by the deadline", UNMEASURED, str(problem)))
    elif contract.deadline:
        if not facts.finished_at:
            out.append(Measured("finished by the deadline", UNMEASURED, "the mission has not finished"))
        elif facts.finished_at <= contract.deadline:
            out.append(Measured("finished by the deadline", MET, f"finished at {facts.finished_at}"))
        else:
            out.append(Measured("finished by the deadline", UNMET,
                                f"finished at {facts.finished_at}, after {contract.deadline}"))
    if contract.max_latency_seconds is not None:
        name = f"finished within {contract.max_latency_seconds} seconds"
        if facts.elapsed_seconds is None:
            out.append(Measured(name, UNMEASURED, "its time taken is not known"))
        else:
            ok = facts.elapsed_seconds <= contract.max_latency_seconds
            out.append(Measured(name, MET if ok else UNMET, f"it took {facts.elapsed_seconds} seconds"))
    return tuple(out)


# --- evaluating a roll-up ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Evaluation:
    """How the mission met its contract: each requirement, verification requirement and limit, every check record
    (Ledger §10, inspectable), each output's class and the mission's (the weakest), and whether it was accepted."""

    requirements: tuple[Measured, ...]
    verification: tuple[Measured, ...]
    limits: tuple[Measured, ...]
    records: tuple[dict, ...]
    unit_strengths: tuple[tuple[str, str], ...]
    strength: str
    required_completion_state: str
    accepted: bool
    allowed: tuple[str, ...] = ()
    finished_at: str = ""
    #: Each step's failure surfaces (step, surface, passed | failed | not_tested) and who adjudicated (TS §27; E10c).
    surfaces: tuple[tuple[str, str, str], ...] = ()
    adjudicated_by: str = ""

    @property
    def mechanisms(self) -> tuple[str, ...]:
        return tuple(sorted({str(r.get("mechanism", "other")) for r in self.records}))

    def as_dict(self) -> dict:
        return {"requirements": [m.as_dict() for m in self.requirements],
                "verification": [m.as_dict() for m in self.verification],
                "limits": [m.as_dict() for m in self.limits], "records": [dict(r) for r in self.records],
                "unit_strengths": [list(p) for p in self.unit_strengths], "strength": self.strength,
                "mechanisms": list(self.mechanisms), "required_completion_state": self.required_completion_state,
                "accepted": self.accepted, "allowed": list(self.allowed), "finished_at": self.finished_at,
                "surfaces": [list(s) for s in self.surfaces], "adjudicated_by": self.adjudicated_by}

    @classmethod
    def from_dict(cls, data: Mapping) -> Evaluation:
        def measured(items: Any) -> tuple[Measured, ...]:
            return tuple(Measured(str(m["name"]), str(m["status"]), str(m["why"]), str(m.get("predicate", "")))
                         for m in items or ())

        return cls(measured(data.get("requirements")), measured(data.get("verification")),
                   measured(data.get("limits")), tuple(dict(r) for r in data.get("records") or ()),
                   tuple((str(a), str(b)) for a, b in data.get("unit_strengths") or ()),
                   str(data.get("strength", "none")), str(data.get("required_completion_state", QUALIFIED)),
                   bool(data.get("accepted")), tuple(data.get("allowed") or ()), str(data.get("finished_at", "")),
                   tuple(tuple(str(x) for x in s) for s in data.get("surfaces") or ()),
                   str(data.get("adjudicated_by", "")))


def _kind(degradation: str) -> str:
    return "shadow" if str(degradation).startswith("shadow:") else "other"


def evaluate(contract: AcceptanceContract, facts: Facts, outcome: Outcome, *,
             extra: Iterable[Requirement] = ()) -> tuple[Outcome, Evaluation]:
    """The roll-up with the contract applied: an unmet or unchecked requirement holds it at qualified-draft; below
    its required state it is blocked-escalated unless the only shortfall is an allowed degradation; never promoted.
    ``extra``: the plan's own unit checks."""
    reqs = tuple(measure(r, facts) for r in (*contract.requirements, *extra))
    checks = tuple(_verification(name, facts) for name in contract.verification_requirements)
    limits = _limits(contract, facts)
    out = outcome
    for found in (*reqs, *checks, *limits):
        if found.status == UNMET:
            out = out.demote(QUALIFIED, f"what counts as done is not met: {found.name}: {found.why}")
        elif found.status == UNMEASURED:
            out = out.demote(QUALIFIED, f"what counts as done was not checked: {found.name}: {found.why}")
    strengths = tuple((unit, unit_strength(facts.checks.get(unit, ()))) for unit in facts.do_units)
    strength = weakest(s for _, s in strengths)
    required, allowed = contract.required_completion_state, ()
    if rank(out.state) < rank(required) and out.state != BLOCKED:
        kinds = {_kind(text) for texts in facts.degradations.values() for text in texts}
        shortfall = any(m.status != MET for m in (*reqs, *checks, *limits))
        # Any other reason below verified is a shortfall too: a check that did not pass, or one a person set aside
        # (fix pass M8); only a degradation may be allowed.
        shortfall = shortfall or any(r.get("set_aside") or r.get("result") != "pass" or not r.get("executed")
                                     for unit in facts.do_units for r in facts.checks.get(unit, ()))
        # Gate W review I1: a check below verifier-grade (A4; no class reads as A0), or the roll-up's own cap, holds
        # the result below verified whatever degraded it, so no allowed degradation excuses it.
        shortfall = shortfall or any(r.get("authority") != VERIFIER_GRADE
                                     for unit in facts.do_units for r in facts.checks.get(unit, ()))
        shortfall = shortfall or any(reason.startswith(AUTHORITY_COPY["rollup_capped"].format(unit=unit, reason=""))
                                     for unit in facts.do_units for reason in out.reasons)
        if (out.degraded and not shortfall and strength == "objective" and kinds
                and kinds <= set(contract.allowed_degradation)):
            allowed = tuple(sorted(kinds))
        else:
            out = out.demote(BLOCKED, f"what counts as done needs a {required} result and this one is {out.state}: "
                                      "a person decides whether to use it")
    records = tuple(r for unit in facts.do_units for r in facts.checks.get(unit, ()))
    accepted = rank(out.state) >= rank(required) or bool(allowed)
    return out, Evaluation(reqs, checks, limits, records, strengths, strength, required, accepted, allowed,
                           facts.finished_at, surfaces_of(facts), ADJUDICATOR)


__all__ = [
    "ADJUDICATOR",
    "CANNOT_CHECK",
    "CODES",
    "CODE_DERIVED",
    "CONFIRM_TIERS",
    "DEGRADATIONS",
    "EFFECT_WORDS",
    "ELICITATION_FIELDS",
    "MECHANISMS",
    "MET",
    "OBJECTIVE",
    "OBJECTIVE_KINDS",
    "OVERRIDES",
    "PREDICATES",
    "PROBE_SURFACES",
    "RECEIPT_OPS",
    "REQUIRED_STATES",
    "REVERSIBILITY",
    "SOURCES",
    "STANDARD_POLICY",
    "STATE_MECHANISMS",
    "STRATEGY_OF",
    "STRENGTHS",
    "SURFACES",
    "SURFACE_IDS",
    "SURFACE_STATES",
    "SURFACE_WORDS",
    "TIERS",
    "TIER_WORDS",
    "UNMEASURED",
    "UNMET",
    "VERIFICATION_BY_TIER",
    "VERIFICATION_REQUIREMENTS",
    "AcceptanceContract",
    "Evaluation",
    "Facts",
    "Measured",
    "Requirement",
    "base_tier",
    "cannot_check",
    "code_floor",
    "code_surfaces",
    "compile_contract",
    "derive",
    "describe",
    "describe_standard",
    "elicitation_record",
    "evaluate",
    "exercised",
    "floor",
    "for_plan",
    "gather",
    "measure",
    "mechanism_of",
    "mechanism_record",
    "requirement",
    "risk_code",
    "spec_complete",
    "standard_words",
    "strengthen",
    "surface_text",
    "surfaces_of",
    "tally",
    "unit_classes",
    "unit_requirements",
    "unit_strength",
    "unreadable",
    "weaker",
    "weakest",
]
