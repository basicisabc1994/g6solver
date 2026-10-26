# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The cost-aware scheduler: E8e's first cut, extended by E2d (Phase E Part 3; Ledger §2, §11 to §17, §25, §28 Phases
3-4).

One planner: the mission engine. The chat drafts the plan, and the scheduler decides nothing the plan did not offer,
except one thing at run time (Ledger §13 step 8): a code step whose result an executed check failed may be handed to
the owner's chat. Everything it decides is a SchedulerDecision (Ledger §12): the candidate ExecutionOptions (§11), the
§12 reason codes in the §13 step order, the budget before and after, the risk tier, the acceptance contract it served
and its policy version.

- ``tiers.json`` holds the resource tiers T0-T7 (Ledger §14) and the resource modes (§25), as data with no vendor or
  model name: a tier is a class of resource G6 can observe, never a model.
- ``choose`` picks a route: never one below the assurance floor (TS §39); above the mode's ceiling, only a person;
  among checks, an objective one first (§13 step 7); then the least expected cost per accepted outcome (§30), when
  every open route's cost and chance are known in one currency; else by policy class, the cheapest tier first (§2: no
  fabricated precision).
- A mode only adds verification and only lowers optional limits: it never removes a check the tier requires (TS §42,
  STD-004).

Pure: no I/O but reading ``tiers.json``, no model.
"""
from __future__ import annotations

import functools
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from g6.workforce import acceptance, classes
from g6.workforce.economics import BUDGET_CURRENCY, BudgetPolicy, Money
from g6.workforce.plans import ARTIFACT_PREFIX, MAX_ATTEMPTS, Plan, UnitSpec

POLICY_VERSION = "e2d-1"
#: The policy the first cut (E8e) recorded; its decisions still read (``service._schedule``, the trace).
FIRST_CUT_POLICY = "e8e-1"
#: Ledger §12's reason codes, each with the §13 step whose rule produces it.
REASON_STEPS: dict[str, int] = {
    "HUMAN_APPROVAL_REQUIRED": 1, "DEADLINE_PRESSURE": 1,
    "CACHE_HIT_VERIFIED": 2,
    "DETERMINISTIC_METHOD_AVAILABLE": 3,
    "LOWEST_COST_SATISFIES_ACCEPTANCE": 4, "PREFERRED_CAPABILITY_UNAVAILABLE": 4,
    "HIGHER_RELIABILITY_REQUIRED": 7, "OBJECTIVE_VERIFIER_AVAILABLE": 7, "RISK_ESCALATION": 7,
    "RETRY_AFTER_TRANSIENT_FAILURE": 8, "STRATEGY_FAILURE": 8,
    "BUDGET_EXHAUSTED": 9,
}
_ORDER = {code: index for index, code in enumerate(REASON_STEPS)}
#: Ledger §11's strategy types.
STRATEGIES = ("cache", "deterministic", "retrieval", "capability", "worker", "worker_plus_verifier", "ensemble", "human")
_CONSEQUENTIAL = ("R2", "R3")
#: The side effects that act outside the machine: only the step's own op may take that action (P3.7, P3.8).
ACTING = frozenset({"external", "irreversible"})
#: Ledger §14's resource tiers, cheapest first (``tiers.json`` says what each covers).
TIER_NAMES = ("T0_REUSE", "T1_DETERMINISTIC", "T2_LOW_COST", "T3_STANDARD", "T4_HIGH_REASONING", "T5_MULTI_WORKER",
              "T6_SPECIALIST", "T7_HUMAN")
#: Ledger §25's resource modes. Balanced is the default (TS §42).
MODE_NAMES = ("fast", "balanced", "thorough", "critical")
DEFAULT_MODE = "balanced"
#: What is known of an option's chance of meeting the contract when none was measured (Ledger §2's policy classes).
RELIABILITY_CLASSES = ("verified_before", "objective", "unmeasured", "draft_until_checked", "person")
TIERS_FILE = Path(__file__).with_name("tiers.json")
_FILE_FIELDS = frozenset({"version", "tiers", "low_cost_ceiling", "modes"})
_TIER_FIELDS = frozenset({"name", "strategy_types", "reliability_class", "binds_budget", "chosen_automatically",
                          "covers"})
_MODE_FIELDS = frozenset({"escalate_up_to", "max_attempts_per_work_unit", "adds_verification", "soft_limit_percent"})


def ordered(codes: Iterable[str]) -> tuple[str, ...]:
    """The codes once each, in the Ledger §13 step order (ties keep §12's order)."""
    codes = set(codes)
    unknown = sorted(codes - set(REASON_STEPS))
    if unknown:
        raise ValueError(f"not Ledger §12 reason codes: {unknown}")
    return tuple(sorted(codes, key=lambda code: (REASON_STEPS[code], _ORDER[code])))


@dataclass(frozen=True)
class Tier:
    """One Ledger §14 resource tier, as ``tiers.json`` declares it."""

    name: str
    strategy_types: tuple[str, ...]
    reliability_class: str
    binds_budget: bool
    chosen_automatically: bool
    covers: str


@dataclass(frozen=True)
class Mode:
    """One Ledger §25 resource mode, as real limits, never a prompt: the highest tier the scheduler may move a step to
    without a person, a cap on each step's attempts, the verification it adds, and where the soft budget limit sits."""

    name: str
    escalate_up_to: str
    max_attempts_per_work_unit: int | None
    adds_verification: tuple[str, ...]
    soft_limit_percent: int | None


@dataclass(frozen=True)
class Tiers:
    version: str
    tiers: tuple[Tier, ...]
    low_cost_ceiling: Money
    modes: tuple[Mode, ...]

    def mode(self, name: str) -> Mode:
        for found in self.modes:
            if found.name == name:
                return found
        raise ValueError(f"a resource mode is one of {list(MODE_NAMES)}, got {name!r}")


def _whole(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def load_tiers(path: str | Path | None = None) -> Tiers:
    """``tiers.json``, judged: the eight tiers in order, the four modes in order, each field of the shape it must have.
    Every problem is named at once (``ValueError``)."""
    raw = json.loads(Path(path or TIERS_FILE).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != _FILE_FIELDS:
        raise ValueError(f"tiers.json: holds exactly {sorted(_FILE_FIELDS)}")
    problems: list[str] = []
    tiers = []
    for index, item in enumerate(raw["tiers"] if isinstance(raw["tiers"], list) else []):
        if not isinstance(item, dict) or set(item) != _TIER_FIELDS:
            problems.append(f"tier #{index}: holds exactly {sorted(_TIER_FIELDS)}")
            continue
        strategies = item["strategy_types"]
        if not (isinstance(strategies, list) and strategies and all(s in STRATEGIES for s in strategies)):
            problems.append(f"tier {item['name']!r}: strategy_types are Ledger §11's {list(STRATEGIES)}")
            strategies = []
        if item["reliability_class"] not in RELIABILITY_CLASSES:
            problems.append(f"tier {item['name']!r}: reliability_class is one of {list(RELIABILITY_CLASSES)}")
        if not all(isinstance(item[k], bool) for k in ("binds_budget", "chosen_automatically")):
            problems.append(f"tier {item['name']!r}: binds_budget and chosen_automatically are true or false")
        tiers.append(Tier(str(item["name"]), tuple(strategies), str(item["reliability_class"]),
                          item["binds_budget"] is True, item["chosen_automatically"] is True, str(item["covers"])))
    if tuple(t.name for t in tiers) != TIER_NAMES:
        problems.append(f"tiers are exactly {list(TIER_NAMES)}, in that order")
    try:
        low = Money.from_dict(raw["low_cost_ceiling"])
    except ValueError as exc:
        problems.append(f"low_cost_ceiling: {exc}")
        low = Money(0, BUDGET_CURRENCY)
    found = raw["modes"] if isinstance(raw["modes"], dict) else {}
    if tuple(found) != MODE_NAMES:
        problems.append(f"modes are exactly {list(MODE_NAMES)}, in that order")
    modes = []
    for name, item in found.items():
        if not isinstance(item, dict) or set(item) != _MODE_FIELDS:
            problems.append(f"mode {name!r}: holds exactly {sorted(_MODE_FIELDS)}")
            continue
        if item["escalate_up_to"] not in TIER_NAMES:
            problems.append(f"mode {name!r}: escalate_up_to is a tier")
        cap = item["max_attempts_per_work_unit"]
        if cap is not None and not _whole(cap, 1, MAX_ATTEMPTS):
            problems.append(f"mode {name!r}: max_attempts_per_work_unit is null or 1..{MAX_ATTEMPTS}")
        adds = item["adds_verification"]
        if not (isinstance(adds, list) and all(a in acceptance.VERIFICATION_REQUIREMENTS for a in adds)):
            problems.append(f"mode {name!r}: adds_verification names verification requirements "
                            f"{sorted(acceptance.VERIFICATION_REQUIREMENTS)}")
            adds = []
        percent = item["soft_limit_percent"]
        if percent is not None and not _whole(percent, 1, 100):
            problems.append(f"mode {name!r}: soft_limit_percent is null or 1..100")
        modes.append(Mode(str(name), str(item["escalate_up_to"]), cap, tuple(adds), percent))
    if problems:
        raise ValueError("tiers.json: " + "; ".join(problems))
    return Tiers(str(raw["version"]), tuple(tiers), low, tuple(modes))


@functools.lru_cache(maxsize=1)
def default_tiers() -> Tiers:
    """The shipped ``tiers.json``, read once per process."""
    return load_tiers()


@dataclass(frozen=True)
class ExecutionOption:
    """Ledger §11: one candidate route for one step. Its cost is Money or unknown (None, never 0); its chance of meeting
    the contract is measured, with its n, or unknown, never guessed (Ledger §2)."""

    id: str
    work_unit_id: str
    strategy_type: str
    resource_tier: str
    required_capabilities: tuple[tuple[str, str], ...] = ()
    candidate_worker_spec: str = ""
    estimated_cost: Money | None = None
    estimated_latency: str = "not estimated"
    reliability_class: str = "unmeasured"
    acceptance_probability: Fraction | None = None
    probability_n: int = 0
    risk_coverage: tuple[str, ...] = ()
    permission_requirements: tuple[str, ...] = ()
    expected_human_attention: str = "none"
    verification_plan: tuple[str, ...] = ()
    mechanism: str = ""
    degradation_implications: tuple[str, ...] = ()
    meets_floor: bool = True
    binds_budget: bool = True
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.strategy_type not in STRATEGIES:
            raise ValueError(f"strategy_type is one of {list(STRATEGIES)}, got {self.strategy_type!r}")
        if self.resource_tier not in TIER_NAMES:
            raise ValueError(f"resource_tier is one of {list(TIER_NAMES)}, got {self.resource_tier!r}")
        if self.reliability_class not in RELIABILITY_CLASSES:
            raise ValueError(f"reliability_class is one of {list(RELIABILITY_CLASSES)}, got {self.reliability_class!r}")
        chance = self.acceptance_probability
        if chance is not None and not (isinstance(chance, Fraction) and 0 <= chance <= 1 and self.probability_n > 0):
            raise ValueError("a chance of meeting the contract is a Fraction from 0 to 1, measured on n > 0 cases")

    def per_accepted(self) -> Fraction | None:
        """Ledger §30 for this option: its expected cost per accepted outcome; None when either part is unknown."""
        if self.estimated_cost is None or not self.acceptance_probability:
            return None
        return Fraction(self.estimated_cost.amount_minor_units) / self.acceptance_probability

    def as_dict(self) -> dict:
        out = asdict(self)
        out["required_capabilities"] = [list(c) for c in self.required_capabilities]
        out["estimated_cost"] = self.estimated_cost.as_dict() if self.estimated_cost is not None else None
        chance = self.acceptance_probability
        out["acceptance_probability"] = None if chance is None else f"{chance.numerator}/{chance.denominator}"
        for name in ("risk_coverage", "permission_requirements", "verification_plan", "degradation_implications"):
            out[name] = list(getattr(self, name))
        return out


# COPY: Coverage ledger R2: why an option is rejected when its contract does not claim the failure mode a step requires.
NOT_MITIGATED = "it does not mitigate {mode}, which this step requires"


class NoRoute(ValueError):
    """No option is open: each is below the floor, above the ceiling, or measured never to meet the contract."""

    def __init__(self, rejected: tuple[tuple[str, str], ...]) -> None:
        self.rejected = rejected
        super().__init__("; ".join(f"{oid}: {why}" for oid, why in rejected) or "no option was offered")


@dataclass(frozen=True)
class Choice:
    selected: ExecutionOption
    rejected: tuple[tuple[str, str], ...]
    #: ``expected_cost_per_accepted_outcome`` or ``policy_class``.
    by: str
    #: Whether a costlier open option was passed over for it (``LOWEST_COST_SATISFIES_ACCEPTANCE``).
    lowest_cost: bool


def _rank(option: ExecutionOption) -> int:
    return TIER_NAMES.index(option.resource_tier)


def _minor(value: Fraction | None) -> str:
    """An expected cost as exact text: whole minor units, or a fraction of one (never a float)."""
    if value is None:
        return "unknown"
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def choose(options: Iterable[ExecutionOption], *, ceiling: str | None = None, requires: Iterable[str] = ()) -> Choice:
    """The option to run (module docstring; ruling P3.6). ``ceiling``: the highest tier open without a person (the
    mode's). A person (T7) is never above it: a person is the approval. ``requires`` (coverage ledger R2): the failure
    modes this step must mitigate; an option whose ``risk_coverage`` lacks one is rejected (it can only remove an
    option, never add one)."""
    requires = tuple(requires)
    rejected: list[tuple[str, str]] = []
    open_: list[ExecutionOption] = []
    for option in options:
        missing = [mode for mode in requires if mode not in option.risk_coverage]
        if not option.meets_floor:
            rejected.append((option.id, option.rationale or "it is below the assurance floor this step needs"))
        elif missing:
            rejected.append((option.id, NOT_MITIGATED.format(mode=", ".join(missing))))
        elif ceiling is not None and option.strategy_type != "human" and _rank(option) > TIER_NAMES.index(ceiling):
            rejected.append((option.id, (f"{option.resource_tier} is above {ceiling}, the most this mission's mode "
                                         "uses without a person's approval")))
        elif option.acceptance_probability == 0:
            rejected.append((option.id, f"measured on {option.probability_n} cases, it never met the contract"))
        else:
            open_.append(option)
    if not open_:
        raise NoRoute(tuple(rejected))
    objective = [o for o in open_ if o.mechanism in acceptance.OBJECTIVE]
    if objective:  # Ledger §13 step 7, §17: where an objective check exists, it is preferred
        rejected += [(o.id, "an objective check of the same output is open, and is preferred")
                     for o in open_ if o not in objective]
        open_ = objective
    currencies = {o.estimated_cost.currency for o in open_ if o.estimated_cost is not None}
    priced = len(currencies) == 1 and all(o.per_accepted() is not None for o in open_)
    if priced:
        ranked = sorted(open_, key=lambda o: (o.per_accepted(), _rank(o)))
    else:
        ranked = sorted(open_, key=_rank)  # stable: the order offered breaks a tie
    selected = ranked[0]
    for other in ranked[1:]:
        if priced:
            why = (f"its expected cost per accepted outcome, {_minor(other.per_accepted())} minor units, is not below "
                   f"{_minor(selected.per_accepted())}")
        else:
            why = (f"{other.resource_tier} costs more than {selected.resource_tier} by policy class, with no measured "
                   "cost and chance to say otherwise")
        rejected.append((other.id, why))
    return Choice(selected, tuple(rejected), "expected_cost_per_accepted_outcome" if priced else "policy_class",
                  lowest_cost=len(ranked) > 1)


@dataclass(frozen=True)
class SchedulerDecision:
    """Ledger §12, for one step of a stored plan."""

    mission_id: str
    work_unit_id: str
    candidate_options: tuple[dict, ...]
    selected_option: dict
    reason_codes: tuple[str, ...]
    budget_before: dict
    risk_tier: str
    acceptance_contract_reference: str
    timestamp: str
    #: Ledger §12 ``budget_after``: None at plan time (nothing was spent); on an escalation, the budget that is left.
    budget_after: dict | None = None
    policy_version: str = POLICY_VERSION
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _cost_tier(found: Any, tiers: Tiers) -> str:
    """T2 or T3 for an op that may reach a model (P3.2): T2 only when its reviewed contract declares a per-call cost at
    or under ``tiers.json``'s low-cost ceiling, in the same currency; one that declares none is T3."""
    ceiling = getattr(found, "cost_ceiling", None)
    low = tiers.low_cost_ceiling
    if ceiling is not None and ceiling.currency == low.currency \
            and ceiling.amount_minor_units <= low.amount_minor_units:
        return "T2_LOW_COST"
    return "T3_STANDARD"


def chat_option(unit: UnitSpec, why: str) -> ExecutionOption:
    """The owner's own chat writes the step (the pull rung): T4, a draft until its check judges it, at a cost G6 is not
    told and the mission's budget does not bind (economics ruling R6)."""
    return ExecutionOption(id=f"{unit.key}:chat", work_unit_id=unit.key, strategy_type="worker",
                           resource_tier="T4_HIGH_REASONING", candidate_worker_spec="puller",
                           reliability_class="draft_until_checked",
                           degradation_implications=("a draft until its check judges it",), binds_budget=False,
                           rationale=why)


def person_option(unit: UnitSpec, why: str) -> ExecutionOption:
    """A person decides (T7): the end of Ledger §15's hierarchy, never above a mode's ceiling."""
    return ExecutionOption(id=f"{unit.key}:person", work_unit_id=unit.key, strategy_type="human",
                           resource_tier="T7_HUMAN", candidate_worker_spec="gatekeeper", reliability_class="person",
                           expected_human_attention="a person decides this step", binds_budget=False, rationale=why)


def planned_option(unit: UnitSpec, contract: Callable[[str, str], Any], effects: Callable[[str, str], str | None],
                   tiers: Tiers | None = None) -> tuple[ExecutionOption, set[str]]:
    """What the plan names for one step, as an option, and the reason codes the first cut's rules establish from the
    plan and the shipped ParamContracts (E8e, unchanged): a gate and an acting op need a person; a model-free call
    needs no model; a model-free oracle is an objective check."""
    tiers = tiers or default_tiers()
    if unit.kind == "gate":
        return person_option(unit, "a gate is a person's decision"), {"HUMAN_APPROVAL_REQUIRED"}
    codes: set[str] = set()
    acts = tuple(sorted({effects(c, o) for c, o in unit.capabilities} & ACTING))
    if acts:
        codes.add("HUMAN_APPROVAL_REQUIRED")
    step = unit.call if unit.kind == "do" else unit.oracle
    if step is None:
        return replace(chat_option(unit, "no code step is named: the owner's chat writes it"),
                       required_capabilities=unit.capabilities, permission_requirements=acts), codes
    found = contract(*step["capability"])
    model_free = bool(found is not None and found.model_free)
    if model_free:
        codes.add("DETERMINISTIC_METHOD_AVAILABLE" if unit.kind == "do" else "OBJECTIVE_VERIFIER_AVAILABLE")
    mechanism = (acceptance.mechanism_of(str(step.get("kind") or ""), model_free, step["capability"])
                 if unit.kind == "verify" else "")
    return ExecutionOption(
        id=f"{unit.key}:planned", work_unit_id=unit.key,
        strategy_type="deterministic" if model_free else "capability",
        resource_tier="T1_DETERMINISTIC" if model_free else _cost_tier(found, tiers),
        required_capabilities=unit.capabilities, candidate_worker_spec="verifier" if unit.kind == "verify" else "doer",
        estimated_cost=getattr(found, "cost_ceiling", None),
        reliability_class="objective" if model_free else "unmeasured", permission_requirements=acts,
        mechanism=mechanism, rationale=f"the plan names {'.'.join(step['capability'])}"), codes


def decide(mission_id: str, plan: Plan, contract: Callable[[str, str], Any],
           effects: Callable[[str, str], str | None], *, now: str,
           acceptance_contract: acceptance.AcceptanceContract | None = None, mode: str = DEFAULT_MODE,
           tiers: Tiers | None = None) -> list[SchedulerDecision]:
    """One decision per step of ``plan``, in the plan's order (Ledger §13 steps 1-7). The route the plan names is what
    runs; the decision records the candidates weighed, why the cheaper is enough, and the codes the rules establish.
    ``acceptance_contract``: the contract the plan serves (its digest is the reference; a passed deadline is
    DEADLINE_PRESSURE). ``mode``: the mission's resource mode (one that adds verification is RISK_ESCALATION)."""
    tiers = tiers or default_tiers()
    adds = tiers.mode(mode).adds_verification
    reference = (f"acceptance:{acceptance_contract.sha256}" if acceptance_contract is not None
                 else f"plan:{plan.sha256}")
    late = bool(acceptance_contract is not None and acceptance_contract.deadline
                and acceptance_contract.deadline < now)
    checked_by: dict[str, list[str]] = {}
    for unit in plan.units:
        if unit.kind == "verify":
            checked_by.setdefault(unit.verifies, []).append(unit.key)
    out = []
    for unit in plan.units:
        planned, codes = planned_option(unit, contract, effects, tiers)
        planned = replace(planned, verification_plan=tuple(checked_by.get(unit.key, ())))
        options = [planned]
        if unit.kind == "do" and unit.call is not None and not planned.permission_requirements:
            options.append(replace(chat_option(unit, "the owner's model can write this step too: it is the route "
                                                     "taken if the planned call's result fails its check"),
                                   verification_plan=planned.verification_plan))
        choice = choose(options)
        if choice.selected.id == planned.id and choice.lowest_cost:
            codes.add("LOWEST_COST_SATISFIES_ACCEPTANCE")
        if any(name.startswith(ARTIFACT_PREFIX) for name in unit.inputs):
            codes.add("CACHE_HIT_VERIFIED")
        if unit.risk in _CONSEQUENTIAL:
            codes.add("HIGHER_RELIABILITY_REQUIRED")
        if adds and unit.kind != "gate":
            codes.add("RISK_ESCALATION")
        if late:
            codes.add("DEADLINE_PRESSURE")
        metadata = {"mode": mode, "tiers": tiers.version, "plan": f"plan:{plan.sha256}", "chosen_by": choice.by,
                    "rejected": [list(r) for r in choice.rejected]}
        if choice.selected.id != planned.id:  # never in Part 3: the cheapest open route is the plan's own
            metadata["preferred"] = choice.selected.id
        out.append(SchedulerDecision(
            mission_id=str(mission_id), work_unit_id=unit.key,
            candidate_options=tuple(o.as_dict() for o in options), selected_option=planned.as_dict(),
            reason_codes=ordered(codes), budget_before=Money(unit.budget_cents, BUDGET_CURRENCY).as_dict(),
            risk_tier=unit.risk, acceptance_contract_reference=reference, timestamp=now, metadata=metadata))
    return out


def mode_of(created: Mapping[str, Any]) -> str:
    """A mission's resource mode from its ``mission.created`` record: Balanced unless another was chosen (TS §42). A
    value that is not a mode reads as Balanced, which removes nothing the tier requires."""
    found = str(created.get("resource_mode") or DEFAULT_MODE)
    return found if found in MODE_NAMES else DEFAULT_MODE


def settings(mode: str) -> Mode:
    return default_tiers().mode(mode)


def with_mode(contract: acceptance.AcceptanceContract, mode: str) -> acceptance.AcceptanceContract:
    """The contract with what the mode adds (Ledger §25: verifier requirements): only ever added, never removed, so no
    mode drops a check its tier requires (TS §42, STD-004). A mode that adds nothing new returns the contract itself,
    with its digest."""
    needs = tuple(dict.fromkeys((*contract.verification_requirements, *settings(mode).adds_verification)))
    if needs == contract.verification_requirements:
        return contract
    return replace(contract, verification_requirements=needs, metadata={**contract.metadata, "resource_mode": mode})


def budget_for(policy: BudgetPolicy, mode: str) -> BudgetPolicy:
    """The mission's Ledger §20 policy under its mode (§25: budgets, resource tiers, retry limits): the soft limit where
    the mode sets one, a cap on each step's attempts, and the highest tier open without a person."""
    found = settings(mode)
    soft = (policy.soft_machine_cost_limit if found.soft_limit_percent is None
            else policy.hard_machine_cost_limit * found.soft_limit_percent // 100)
    return replace(policy, soft_machine_cost_limit=soft, max_attempts_per_work_unit=found.max_attempts_per_work_unit,
                   max_resource_tier_without_approval=found.escalate_up_to)


def limited(plan: Plan, cap: int | None) -> Plan:
    """The plan as a mode runs it: no step tries more often than the mode allows, and never more than the plan asked. The
    stored plan and its digest are unchanged; a gate keeps its one attempt."""
    if cap is None:
        return plan
    return replace(plan, units=tuple(replace(u, max_attempts=min(u.max_attempts, cap)) for u in plan.units))


def escalation(mission_id: str, unit: UnitSpec, *, planned: ExecutionOption, verdict: Mapping[str, Any], mode: str,
               pressure: str, budget_before: Money, acceptance_reference: str, now: str,
               deadline_passed: bool = False) -> SchedulerDecision:
    """Ledger §13 step 8 for a code step whose latest result an executed check failed, with attempts left (P3.7). The
    candidates: the same call again (never: the same inputs give the same answer, which failed); the owner's chat
    writing it with the check's reasons (Ledger §15: an alternate strategy on a stronger resource); a person. The chat
    is chosen unless the step acts outside (only its own op may take that action) or the mode's ceiling is below it;
    then a person decides, and the step stops as it did before E2d. Past the soft limit, nothing whose cost the budget
    binds is added (P3.12)."""
    same = replace(planned, id=f"{unit.key}:same", meets_floor=False,
                   rationale="the same call with the same inputs gives the same answer, and that answer failed its "
                             "check")
    chat = chat_option(unit, "the owner's chat writes this step with the check's reasons; its result is judged again")
    if planned.permission_requirements:
        chat = replace(chat, meets_floor=False, rationale=(f"this step acts outside "
                                                           f"({', '.join(planned.permission_requirements)}): only its "
                                                           "own op may take that action"))
    options = [same, chat, person_option(unit, "the step stops, and a person decides what to do with it")]
    if pressure:
        options = [replace(o, meets_floor=False, rationale="past the mission's soft budget limit, nothing whose cost "
                                                           "the budget binds is added")
                   if o.binds_budget and o.meets_floor else o for o in options]
    choice = choose(options, ceiling=settings(mode).escalate_up_to)
    codes = {"STRATEGY_FAILURE"}
    if choice.selected.strategy_type == "human":
        codes.add("HUMAN_APPROVAL_REQUIRED")
    if choice.lowest_cost:
        codes.add("LOWEST_COST_SATISFIES_ACCEPTANCE")
    if unit.risk in _CONSEQUENTIAL:
        codes.add("HIGHER_RELIABILITY_REQUIRED")
    if deadline_passed:
        codes.add("DEADLINE_PRESSURE")
    if pressure == "BUDGET_EXHAUSTED":
        codes.add("BUDGET_EXHAUSTED")
    return SchedulerDecision(
        mission_id=str(mission_id), work_unit_id=unit.key, candidate_options=tuple(o.as_dict() for o in options),
        selected_option=choice.selected.as_dict(), reason_codes=ordered(codes), budget_before=budget_before.as_dict(),
        risk_tier=unit.risk, acceptance_contract_reference=acceptance_reference, timestamp=now,
        budget_after=budget_before.as_dict(),
        metadata={"mode": mode, "chosen_by": choice.by, "pressure": pressure,
                  "rejected": [list(r) for r in choice.rejected],
                  "evidence": {"verdict_id": str(verdict.get("verdict_id", "")),
                               "artifact_id": str(verdict.get("artifact_id", "")),
                               "check": str(verdict.get("unit_key", ""))},
                  "budget_after": "unchanged: neither the owner's own session nor a person's time is charged to this "
                                  "mission's machine budget"})


def retry_codes(failure_code: str, *, sent_back: bool) -> tuple[str, ...]:
    """Ledger §12's code for another attempt at a step, kept on E2a's retry record (P3.11): a failure that may not recur
    tried again, or a result an independent check failed written again with the check's reasons (V4b, or an
    escalation)."""
    if failure_code and classes.recovery(failure_code) == "transient_retry":
        return ("RETRY_AFTER_TRANSIENT_FAILURE",)
    if not failure_code and sent_back:
        return ("STRATEGY_FAILURE",)
    return ()


__all__ = ["ACTING", "DEFAULT_MODE", "FIRST_CUT_POLICY", "MODE_NAMES", "POLICY_VERSION", "REASON_STEPS",
           "RELIABILITY_CLASSES", "STRATEGIES", "TIERS_FILE", "TIER_NAMES", "Choice", "ExecutionOption", "Mode",
           "NoRoute", "SchedulerDecision", "Tier", "Tiers", "budget_for", "chat_option", "choose", "decide",
           "default_tiers", "escalation", "limited", "load_tiers", "mode_of", "ordered", "person_option",
           "planned_option", "retry_codes", "settings", "with_mode"]
