# SPDX-License-Identifier: Apache-2.0
"""The two documents and the records between them (META_HARNESS_PLAN.md 4.1, 4.3).

A ``Workflow`` judges: the task, its standard checks, the oracle that reads each held-out answer, the objectives, the
suite and the evidence policy. A person confirms it and it is frozen by digest. A ``Profile`` acts: which declared model
runs each step, budgets, prompt text, hints, extra checks and optional steps. A ``Proposal`` can carry only a profile, so
there is no field through which a proposal could express a change to what judges it (MH-1).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from g6.core.ledger import digest
from g6.harness import Check
from g6.metaharness.promotion import PromotionPolicy

# Stable codes (META_HARNESS_PLAN.md 4.2).
STANDARD_CHANGED = "G6_E_META_STANDARD_CHANGED"
PROTECTED = "G6_E_META_PROTECTED"
SCHEMA = "G6_E_META_SCHEMA"
NOT_HELD_OUT = "G6_E_META_NOT_HELD_OUT"
HOLDOUT_REUSED = "G6_E_META_HOLDOUT_REUSED"
ANSWER_IN_PREDICATE = "G6_E_META_ANSWER_IN_PREDICATE"
SUITE_CANNOT_DISCRIMINATE = "G6_E_META_SUITE_CANNOT_DISCRIMINATE"
INSUFFICIENT_EVIDENCE = "G6_E_META_INSUFFICIENT_EVIDENCE"
PATCH_EVIDENCE_REQUIRED = "G6_E_META_PATCH_EVIDENCE_REQUIRED"
NO_GAIN = "G6_E_META_NO_GAIN"
NOT_COMPUTE_MATCHED = "G6_E_META_NOT_COMPUTE_MATCHED"
OBJECTIVE_REGRESSED = "G6_E_META_OBJECTIVE_REGRESSED"
BUDGET_EXCEEDED = "G6_E_META_BUDGET_EXCEEDED"
APPROVAL_REQUIRED = "G6_E_META_APPROVAL_REQUIRED"
APPROVER_IS_PROPOSER = "G6_E_META_APPROVER_IS_PROPOSER"
DRIFT_DEMOTED = "G6_E_META_DRIFT_DEMOTED"
RECOMMENDED = "G6_E_META_RECOMMENDED"
NOT_CONFIRMED = "G6_E_META_NOT_CONFIRMED"
WORKFLOW_INVALID = "G6_E_META_WORKFLOW_INVALID"
NO_NEW_CANDIDATE = "G6_E_META_NO_NEW_CANDIDATE"
ONLINE_OFF = "G6_E_META_ONLINE_OFF"
AUDIT_FLOOR = "G6_E_META_AUDIT_FLOOR"
CAPABILITY_UNCONFIRMED = "G6_E_META_CAPABILITY_UNCONFIRMED"
HOLDOUT_CHANGED = "G6_E_META_HOLDOUT_CHANGED"
NO_SELECTION_SPLIT = "G6_E_META_NO_SELECTION_SPLIT"
SELECTED = "G6_E_META_SELECTED"

PROVENANCES = ("independent", "caller", "model-drafted")
EFFECTS = ("idempotent", "reversible", "irreversible", "unknown")
# What a run record measures; an objective names one of these.
MEASURES = ("success", "model_calls", "wall_clock_s", "cost_usd")
DIRECTIONS = ("max", "min", "bound")


def _source_sha(fn: Callable[..., Any]) -> str:
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):
        source = repr(fn)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Objective:
    """One objective (AD A2.1). ``min``: the candidate's one-sided 95% upper bound on the paired increase must stay within
    ``tolerance`` of the base's mean. ``bound``: the candidate's upper bound must stay at or below ``bound``."""

    name: str
    direction: str
    tolerance: float = 0.05
    bound: float | None = None
    unit: str = ""
    reference: float | None = None
    provenance: str = "default"     # default | specified | inferred
    confirmed: bool = True
    weight: float | None = None     # ranks proposals for a person only; the gate never reads it (AD A2.2)

    def __post_init__(self) -> None:
        if self.name not in MEASURES:
            raise ValueError(f"objective {self.name!r}: measure must be one of {', '.join(MEASURES)}")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"objective {self.name!r}: direction must be one of {', '.join(DIRECTIONS)}")
        if not (math.isfinite(self.tolerance) and self.tolerance >= 0):
            raise ValueError(f"objective {self.name!r}: tolerance must be a finite number >= 0")
        if self.direction == "bound" and (self.bound is None or not math.isfinite(self.bound)):
            raise ValueError(f"objective {self.name!r}: a bound objective needs a finite bound")


def default_objectives() -> tuple[Objective, ...]:
    """Success up; model calls and wall clock non-inferior within 5% (owner ruling Q9). Safety is never an objective."""
    return (Objective("success", "max", unit="share of held-out cases"),
            Objective("model_calls", "min", unit="calls per case"),
            Objective("wall_clock_s", "min", unit="seconds per case"))


@dataclass(frozen=True)
class EvidencePolicy:
    """The meta-gate's thresholds (META_HARNESS_PLAN.md 5.3; owner ruling Q9)."""

    min_cases: int = 30
    min_discordant: int = 5
    alpha: float = 0.05
    max_evaluations: int = 5        # K: evaluations allowed per held-out split before a fresh one is needed
    call_tolerance: float = 1.0
    max_eval_model_calls: int = 10_000
    demote_alpha: float = 0.05      # the monitor's false-demotion bound, over every consultation
    looks: int = 1                  # a sequential confirmation's looks; 1 = one test at the end (frontier design §4.2)
    # non-inferiority: the one-sided 95% upper bound on the rise in the false-accept rate may not exceed this (V6b-F5)
    false_accept_margin: float = 0.05
    staged_evaluation: bool = False

    def __post_init__(self) -> None:
        if self.min_cases < 1 or self.min_discordant < 1 or self.max_evaluations < 1 or self.max_eval_model_calls < 1:
            raise ValueError("min_cases, min_discordant, max_evaluations and max_eval_model_calls must be >= 1")
        if self.staged_evaluation and self.looks != 1:
            raise ValueError("staged rejection and sequential confirmation cannot be combined")
        if not 1 <= self.looks <= 10:
            raise ValueError("looks must be between 1 and 10")
        if not 0 <= self.false_accept_margin < 1:
            raise ValueError("false_accept_margin must be in [0, 1)")
        if not 0 < self.alpha < 1 or not (math.isfinite(self.call_tolerance) and self.call_tolerance >= 1):
            raise ValueError("alpha must be in (0, 1) and call_tolerance a finite number >= 1")
        if not 0 < self.demote_alpha < 1:
            raise ValueError("demote_alpha must be in (0, 1)")

    @property
    def threshold(self) -> float:
        return self.alpha / self.max_evaluations


@dataclass(frozen=True)
class SuiteCase:
    case_id: str
    input: str
    expected: Any                   # read only by the workflow's oracle, inside the evaluator
    meta: Mapping[str, Any] = field(default_factory=dict)

    def ref(self) -> dict[str, str]:
        return {"case_id": self.case_id, "input_sha256": hashlib.sha256(self.input.encode("utf-8")).hexdigest(),
                "expected_sha256": digest(self.expected)}


@dataclass(frozen=True)
class Suite:
    dev: tuple[SuiteCase, ...]
    held_out: tuple[SuiteCase, ...]
    provenance: Mapping[str, str] = field(default_factory=lambda: {"dev": "caller", "held_out": "caller"})
    # Ranks candidates (``select.race``), so the held-out split is spent once, on the winner (frontier design §4.2).
    selection: tuple[SuiteCase, ...] = ()

    def digest(self, split: str) -> str:
        return digest([c.ref() for c in getattr(self, split)])

    def problems(self) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for split in ("dev", "held_out", "selection"):
            ids = [c.case_id for c in getattr(self, split)]
            if len(set(ids)) != len(ids) or not all(isinstance(i, str) and i.strip() for i in ids):
                found.append((WORKFLOW_INVALID, f"{split}: every case needs its own non-empty id"))
            if split != "selection" and self.provenance.get(split) not in PROVENANCES:
                found.append((WORKFLOW_INVALID, f"{split}: provenance must be one of {', '.join(PROVENANCES)}"))
        dev_ids = {c.case_id for c in self.dev}
        dev_inputs = {c.ref()["input_sha256"] for c in self.dev}
        shared = sorted(c.case_id for c in self.held_out
                        if c.case_id in dev_ids or c.ref()["input_sha256"] in dev_inputs)
        if shared:
            found.append((NOT_HELD_OUT, f"held-out cases also in dev (by id or input): {shared[:5]}"))
        others = {key for c in (*self.dev, *self.held_out) for key in (c.case_id, c.ref()["input_sha256"])}
        reused = sorted(c.case_id for c in self.selection if c.case_id in others or c.ref()["input_sha256"] in others)
        if reused:
            found.append((NOT_HELD_OUT, f"selection cases also in dev or held-out (by id or input): {reused[:5]}"))
        if not self.held_out:
            found.append((WORKFLOW_INVALID, "the held-out split is empty"))
        return found


@dataclass(frozen=True)
class Capability:
    """One action the executor can take (AD A1.2). ``unknown`` is treated as irreversible. A person confirms every
    effect other than ``idempotent``, with a loss scale for an irreversible or unknown one (Proposition A.5: an
    undeclared irreversible effect cannot be made safe by any kernel). A profile can never add one (MH-10)."""

    name: str
    effect: str
    loss_scale: float | None = None
    confirmed_by: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(f"capability {self.name!r}: effect must be one of {', '.join(EFFECTS)}")

    def problem(self) -> str:
        from g6.core.adaptation_gate import is_automated

        if self.effect == "idempotent":
            return ""
        if not self.confirmed_by.strip() or is_automated(self.confirmed_by):
            return f"capability {self.name!r} ({self.effect}) needs a person's confirmation"
        if self.effect in ("irreversible", "unknown") and not (self.loss_scale and math.isfinite(self.loss_scale)
                                                              and self.loss_scale > 0):
            return f"capability {self.name!r} ({self.effect}) needs a positive loss scale from that person"
        return ""


@dataclass(frozen=True)
class Model:
    """A model the user declares, with a cap on calls per run. G6 never adds one (owner ruling Q14).
    ``adapter_loader(adapter_dir) -> LLMCaller``: how this local model runs with a LoRA adapter (owner ruling Q8); a
    model without one cannot take an adapter."""

    caller: Any                     # a g6.core.llm.LLMCaller
    max_calls_per_run: int = 8
    paid: bool = False
    adapter_loader: Any = None
    usage: Callable[[], Mapping[str, Any]] | None = None  # trusted adapter's last-call receipt; never proposal data

    def __post_init__(self) -> None:
        if self.max_calls_per_run < 1:
            raise ValueError("max_calls_per_run must be >= 1")


@dataclass(frozen=True)
class PipelineStep:
    """An optional step a profile may add after the answer (acting: it may revise the output; it never judges it).
    ``run(output, contract, ctx) -> output``. ``component`` and ``op`` name the library op it uses, checked by
    ``g6.navigator.check_plan`` with ``authority``, the class a person reads for it (the library measures only its
    workforce bench ops). ``mandatory`` steps are in every profile: a profile without one is refused."""

    name: str
    component: str
    op: str
    run: Callable[[str, Any, Any], str]
    authority: str = ""
    effect: str = "read"
    mandatory: bool = False
    args: Mapping[str, Any] = field(default_factory=dict)   # what check_plan validates against the op's contract


class PatchEvidence(BaseModel):
    """Observations from the workflow's trusted patch evaluator, never proposal claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    base_digest: str
    candidate_digest: str
    safety_violations: int | None = Field(None, ge=0, strict=True)
    injection_resilience_not_worse: bool | None = Field(None, strict=True)
    evaluator_confidence: float | None = Field(None, ge=0, le=1, strict=True, allow_inf_nan=False)
    checked_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class Workflow:
    """What judges, frozen by digest once a person confirms it. ``task`` turns a case's input into the task text
    (``{input}`` is replaced); ``oracle(output, expected)`` reads a held-out answer and is the only code that does."""

    name: str
    task: str
    checks: tuple[Check, ...]
    oracle: Callable[[str, Any], bool]
    suite: Suite
    models: Mapping[str, Model]
    objectives: tuple[Objective, ...] = field(default_factory=default_objectives)
    policy: EvidencePolicy = field(default_factory=EvidencePolicy)
    optional_checks: Mapping[str, Check] = field(default_factory=dict)   # a profile may add these, never remove one
    steps: tuple[str, ...] = ("answer",)
    audit_floor: float = 0.1
    version: int = 1
    capabilities: tuple[Capability, ...] = ()
    held_out_vault: Any = None      # a g6.core.vault.Vault over the held-out file, when the split came from one
    pipeline_steps: Mapping[str, PipelineStep] = field(default_factory=dict)
    code_root: Any = None           # a flat folder of the user's Python files with main.solve(task) (code-level changes)
    accept_risky: str | None = None  # the person who accepts a task the harness intake holds as risky (V6-F2)
    examples: tuple[SuiteCase, ...] = ()  # labelled data the person owns, shown when a profile asks (never held-out)
    # Why a dev answer failed, in words a proposer can act on (a failing test's message, the expected label). Called on
    # dev cases only, by ``propose.build_view``; never on a selection or held-out case (frontier design §4.1).
    feedback: Callable[[str, SuiteCase], str] | None = None
    promotion_policy: PromotionPolicy | None = None
    # Trusted judging code: independently measure this base/candidate using the artifact store; no proposal input.
    patch_evaluator: Callable[[Profile, Profile, Any], Mapping[str, Any]] | None = None

    def harness_decisions(self) -> tuple[str, ...]:
        """What ``g6.harness.run`` is told a person decided: only the risky-task acceptance, never a gate."""
        return ("accept_risky",) if self.accept_risky else ()

    def problems(self) -> list[tuple[str, str]]:
        found = list(self.suite.problems())
        held = {key for c in (*self.suite.held_out, *self.suite.selection) for key in (c.case_id, c.ref()["input_sha256"])}
        shown = sorted(c.case_id for c in self.examples if c.case_id in held or c.ref()["input_sha256"] in held)
        if shown:
            found.append((NOT_HELD_OUT, f"examples that are held-out or selection cases (by id or input): {shown[:5]}"))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", self.name or ""):
            found.append((WORKFLOW_INVALID, "name: letters, digits, '.', '_' or '-', at most 64"))
        if "{input}" not in self.task:
            found.append((WORKFLOW_INVALID, "task must contain {input}"))
        if not self.checks:
            found.append((WORKFLOW_INVALID, "the workflow needs at least one standard check"))
        if not self.models:
            found.append((WORKFLOW_INVALID, "declare at least one model"))
        if not self.steps or len(set(self.steps)) != len(self.steps):
            found.append((WORKFLOW_INVALID, "steps must be distinct and at least one"))
        names = [o.name for o in self.objectives]
        if names.count("success") != 1 or next(o for o in self.objectives if o.name == "success").direction != "max":
            found.append((WORKFLOW_INVALID, "exactly one 'success' objective, direction 'max'"))
        if any(not o.confirmed for o in self.objectives):
            found.append((WORKFLOW_INVALID, "an objective is not confirmed by a person"))
        if any(o.weight is not None and not o.confirmed for o in self.objectives):
            found.append((WORKFLOW_INVALID, "a weight on an objective nobody confirmed (AD gate P0)"))
        names_c = [c.name for c in self.capabilities]
        if len(set(names_c)) != len(names_c):
            found.append((WORKFLOW_INVALID, "two capabilities share a name"))
        found += [(CAPABILITY_UNCONFIRMED, p) for p in (c.problem() for c in self.capabilities) if p]
        if self.code_root is not None:
            import g6

            root = Path(self.code_root).resolve()
            if not (root / "main.py").is_file():
                found.append((WORKFLOW_INVALID, "code_root needs a main.py with solve(task) -> str"))
            if root.is_relative_to(Path(g6.__file__).resolve().parent):
                found.append((PROTECTED, "code_root is inside G6's own installed source: never changed in an install"))
        if self.accept_risky is not None:
            from g6.core.adaptation_gate import is_automated

            if not self.accept_risky.strip() or is_automated(self.accept_risky):
                found.append((WORKFLOW_INVALID, "accept_risky names the person who accepts the risky task "
                                                "(no empty or auto: name)"))
        if not 0 < self.audit_floor <= 1:
            found.append((WORKFLOW_INVALID, "audit_floor must be in (0, 1]"))
        keys = [c.key for c in (*self.checks, *self.optional_checks.values())]
        if len(set(keys)) != len(keys):
            found.append((WORKFLOW_INVALID, "two checks share an id"))
        found += [(ANSWER_IN_PREDICATE, p) for p in self.answer_leaks()]
        return found

    def answer_leaks(self) -> list[str]:
        """Held-out answers that appear in text the executor sees (the task template or a check's predicate)."""
        texts = [self.task, *(c.predicate for c in (*self.checks, *self.optional_checks.values()))]
        leaks = []
        for case in self.suite.held_out:
            value = case.expected if isinstance(case.expected, str) else json.dumps(case.expected, sort_keys=True)
            value = value.strip()
            if len(value) >= 2 and any(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", t) for t in texts):
                leaks.append(f"held-out case {case.case_id!r}: its expected value appears in the task or a predicate")
        return leaks

    def digest(self) -> str:
        return digest({
            "name": self.name, "version": self.version, "task": self.task,
            **({"promotion_policy": self.promotion_policy.model_dump()} if self.promotion_policy else {}),
            "checks": [c.code_sha() for c in self.checks],
            "optional_checks": {k: c.code_sha() for k, c in sorted(self.optional_checks.items())},
            "oracle": _source_sha(self.oracle), "steps": list(self.steps),
            **({"patch_evaluator": _source_sha(self.patch_evaluator)} if self.patch_evaluator is not None else {}),
            "objectives": [o.__dict__ for o in self.objectives], "policy": _policy_fields(self.policy),
            "suite": {"dev": self.suite.digest("dev"), "held_out": self.suite.digest("held_out"),
                      "provenance": dict(self.suite.provenance),
                      **({"selection": self.suite.digest("selection")} if self.suite.selection else {})},
            "models": {k: {"max_calls_per_run": m.max_calls_per_run, "paid": m.paid,
                           **({"usage": _source_sha(m.usage)} if m.usage is not None else {})} for k, m in sorted(self.models.items())},
            "audit_floor": self.audit_floor,
            "capabilities": [{"name": c.name, "effect": c.effect, "loss_scale": c.loss_scale,
                              "confirmed_by": c.confirmed_by} for c in self.capabilities],
            "held_out_vault": getattr(self.held_out_vault, "digest", None),
            "pipeline_steps": {k: {"component": s.component, "op": s.op, "authority": s.authority, "effect": s.effect,
                                   "mandatory": s.mandatory, "run": _source_sha(s.run)}
                               for k, s in sorted(self.pipeline_steps.items())},
            "code_root": _code_digest(self.code_root),
            **({"accept_risky": self.accept_risky} if self.accept_risky is not None else {}),
            **({"examples": digest([c.ref() for c in self.examples])} if self.examples else {}),
            **({"feedback": _source_sha(self.feedback)} if self.feedback is not None else {}),
        })


_LATER_POLICY_DEFAULTS = {"looks": 1, "false_accept_margin": 0.05, "staged_evaluation": False}


def _policy_fields(policy: EvidencePolicy) -> dict[str, Any]:
    """The policy as digested: a field added later is left out while it holds its default, so a workflow confirmed
    before it existed keeps its digest."""
    return {k: v for k, v in policy.__dict__.items() if k not in _LATER_POLICY_DEFAULTS or v != _LATER_POLICY_DEFAULTS[k]}


def _code_digest(root: Any) -> str | None:
    """What the workflow's base code is: every file's digest (so a changed base is a changed workflow)."""
    if root is None:
        return None
    base = Path(root)
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.iterdir()) if p.is_file()})


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int = Field(8, ge=1)
    max_wall_clock_s: float = Field(120.0, gt=0, allow_inf_nan=False)
    max_output_chars: int = Field(400_000, ge=1)


class Profile(BaseModel):
    """The acting settings only. Unknown fields are refused (``G6_E_META_SCHEMA``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    routing: dict[str, str] = Field(default_factory=dict)          # step -> declared model id
    budgets: Budgets = Field(default_factory=Budgets)
    prompt: str = Field("", max_length=8000)                         # extra instructions to the executor
    skill_text: str = Field("", max_length=8000)  # instructions only; never installed or executed
    hints: tuple[str, ...] = ()                                      # g6.learning theory ids
    context_budget_chars: int = Field(0, ge=0)
    extra_checks: tuple[str, ...] = ()                               # keys of the workflow's optional checks
    pipeline: tuple[str, ...] = ()                                   # names of the workflow's pipeline steps, in order
    retries: int = Field(0, ge=0, le=3)
    prompt_layout: Literal["default", "stable_prefix"] = "default"  # R1: hints in the system prompt, for prefix caches
    distillates: dict[str, str] = Field(default_factory=dict)        # step -> an admitted distillate's digest (R2)
    code: dict[str, str] = Field(default_factory=dict)               # file in code_root -> sha256 of its new content
    adapter: str | None = None                                       # an adapter's digest (LoRA, owner ruling Q8)
    exemplars: int = Field(0, ge=0, le=32)                           # nearest labelled examples shown (recovery)

    @field_validator("hints", "extra_checks", "pipeline")
    @classmethod
    def _distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or any(not v.strip() for v in value):
            raise ValueError("entries must be distinct and non-empty")
        return value

    def digest(self) -> str:
        data = self.model_dump(mode="json")
        if not data["exemplars"]:   # added after V6: a profile without it keeps the digest it had
            data.pop("exemplars")
        if not data["skill_text"]:
            data.pop("skill_text")
        return digest(data)

    def problems_for(self, workflow: Workflow) -> list[tuple[str, str]]:
        from g6.metaharness._code_safety import skill_text_problems

        found = [(SCHEMA, why) for why in skill_text_problems(self.skill_text)]
        if self.exemplars and not (workflow.examples or workflow.suite.dev):
            found.append((SCHEMA, "exemplars needs labelled examples: declare Workflow(examples=...) or dev cases"))
        for step, model in self.routing.items():
            if step not in workflow.steps:
                found.append((SCHEMA, f"routing names an unknown step {step!r}"))
            if model not in workflow.models:
                found.append((SCHEMA, f"routing names an undeclared model {model!r} (G6 never adds one)"))
        missing = [s for s in workflow.steps if s not in self.routing]
        if missing:
            found.append((SCHEMA, f"no model routed for steps {missing}"))
        unknown = [k for k in self.extra_checks if k not in workflow.optional_checks]
        if unknown:
            found.append((SCHEMA, f"extra_checks names checks the workflow does not offer: {unknown}"))
        found += self._pipeline_problems(workflow)
        found += [(SCHEMA, f"distillates names an unknown step {s!r}") for s in self.distillates if s not in workflow.steps]
        if self.code:
            if workflow.code_root is None:
                found.append((SCHEMA, "code changes need a workflow with a code_root"))
            bad = [n for n in self.code if n != Path(n).name or not n.endswith(".py") or n in (".", "..")]
            if bad:
                found.append((SCHEMA, f"code changes name only files directly in code_root (*.py): {bad}"))
        if self.adapter is not None:
            cannot = sorted({m for m in self.routing.values() if workflow.models[m].adapter_loader is None}
                            if all(m in workflow.models for m in self.routing.values()) else ())
            if cannot:
                found.append((SCHEMA, f"an adapter needs a local model that can load one; these cannot: {cannot}"))
        return found

    def _pipeline_problems(self, workflow: Workflow) -> list[tuple[str, str]]:
        found = [(SCHEMA, f"pipeline names a step the workflow does not offer: {n!r}")
                 for n in self.pipeline if n not in workflow.pipeline_steps]
        missing = [n for n, s in workflow.pipeline_steps.items() if s.mandatory and n not in self.pipeline]
        if missing:
            found.append((SCHEMA, f"the pipeline removes mandatory steps: {missing}"))
        steps = [workflow.pipeline_steps[n] for n in self.pipeline if n in workflow.pipeline_steps]
        if steps:
            from g6.navigator import check_plan

            declared = {(s.component, s.op): s.authority for s in steps}
            report = check_plan([{"component": s.component, "op": s.op, "args": dict(s.args)} for s in steps],
                                authority=lambda c, o: declared.get((c, o), ""))
            for issue in report.issues:
                if issue.code != "G6_E_NAV_UNKNOWN_OP":   # an op with no contract: run, and noted by pipeline_notes
                    found.append((SCHEMA, f"pipeline refused by navigator.check_plan: {issue.code}: {issue.message}"))
        return found

    def pipeline_notes(self, workflow: Workflow) -> list[str]:
        return [f"pipeline step {n!r} ({workflow.pipeline_steps[n].component}.{workflow.pipeline_steps[n].op}) has no "
                "parameter contract: navigator.check_plan could not check its arguments" for n in self.pipeline
                if n in workflow.pipeline_steps]


def parse_profile(data: Mapping[str, Any] | Profile) -> tuple[Profile | None, str]:
    """A profile from data, or None and why (unknown fields, wrong types)."""
    if isinstance(data, Profile):
        return data, ""
    try:
        return Profile.model_validate(dict(data)), ""
    except ValidationError as exc:
        return None, "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'profile'}: {e['msg']}" for e in exc.errors())
    except (TypeError, ValueError) as exc:
        return None, str(exc).splitlines()[0] if str(exc) else type(exc).__name__


def profile_diff(base: Profile, candidate: Profile) -> dict[str, dict[str, Any]]:
    a, b = base.model_dump(mode="json"), candidate.model_dump(mode="json")
    return {k: {"from": a[k], "to": b[k]} for k in a if a[k] != b[k]}


@dataclass(frozen=True)
class Proposal:
    """A candidate only. ``claim`` is the predicted direction per objective, kept as data (KS 4.1)."""

    base_digest: str
    candidate: Profile
    proposer: str
    strategy: str = "hand-written"
    claim: Mapping[str, Literal["up", "down", "same"]] = field(default_factory=dict)
    source_run_ids: tuple[str, ...] = ()
    diff: Mapping[str, Any] = field(default_factory=dict)
    parent_digests: tuple[str, ...] = ()

    @property
    def candidate_digest(self) -> str:
        return self.candidate.digest()

    def digest(self) -> str:
        return digest({"base": self.base_digest, "candidate": self.candidate_digest, "proposer": self.proposer,
                       "strategy": self.strategy, "claim": dict(self.claim), "sources": list(self.source_run_ids),
                       **({"parents": list(self.parent_digests)} if self.parent_digests else {})})
