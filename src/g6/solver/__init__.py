# SPDX-License-Identifier: Apache-2.0
"""g6.solver — run a goal through a fixed 15-step table of step functions you supply.

g6.solver — run a goal through a fixed 15-step table of step callables you supply (audit 028 §11).

Every step returning a value is not a check, and the best state a run gets is ``qualified-draft``.

Capability label: ``implemented``.

    from g6.solver import ConstraintSpec, SolverInput, solve
    out = solve(SolverInput(goal="What is 2 + 2?",
                            constraints=[ConstraintSpec(raw="says 4", kind="contains", value="4")]),
                {"goal_setting": lambda ctx: "add two numbers", "code_construction": lambda ctx: {"answer": "4"},
                 "solution_execution": lambda ctx: {"log": "ran"}, "solution_evaluation": lambda ctx: {"ok": True}})
    out.status, out.outcome, out.success, out.completion_state   # ("completed", "passed", True, "qualified-draft")

``plan_run`` decides complexity (``classify_task`` or a model) and the steps to run (``profile_for`` or a model);
``solve`` runs each planned step through ``runners[step.name]`` with retries and ``CategoryRegistry`` fallbacks
(``execute_step``), picks the final answer, checks the caller's ``ConstraintSpec`` s against it and, if asked,
runs ``LLMQualityChecker``. ``SolverPlanner(llm)`` takes any ``g6.core.llm.LLMCaller``; without one the
deterministic paths decide, and ``agentic_evidence`` records which decided. ``SolverOutput.to_envelope()`` gives
the cross-surface fields.

What the label means: ``status`` says whether every planned step returned a usable value; ``outcome`` / ``success``
say whether a deterministic named check on the answer passed (``unchecked`` otherwise). Neither is a claim that the
answer is right, so nothing here returns ``verified``. Whether a 15-step run beats one direct model call is not
established (audit 028, SV-008).

Not ported from the archive's ``solver`` (audit 028): the learning stack (``learning_loop``, ``learning_memory``,
``meta_learner``, ``failure_collector``, ``exemplar_verifier``, ``trace_abstractor``, ``cross_domain_transfer``,
``analogy_engine``), which learned from unchecked runs and wrote under the home directory; ``solver_ops_mcp`` and
``run_owner`` (hosted job queue and customer scoping); tier, billing and account code; the project store,
workspace, backup, git and healer modules; the block class and its production gate (production runs are refused
instead); ``prompt_engine``, ``step_adapter``, ``strategies*``, ``skill``, ``ood_*``, ``context_*``,
``difficulty_scaffolder``, ``critique_solver``, ``visual_classifier`` and ``deep_agent_protocol``; per-step
strategy routing, voting and self-consistency (they multiply calls with no measured benefit), the simple-task
passthrough (a simple run is the simple step profile), breakpoints, checkpoints, guardrails, presets, token and
cost figures (unmeasured), and every call into another archive component.
"""
from g6.solver.agentic_evidence import summarize_agentic_evidence
from g6.solver.agentic_runtime import Decision, SolverDecisionError, SolverPlanner
from g6.solver.category_registry import CategoryRegistry, ComponentCategory
from g6.solver.complexity_classifier import TaskComplexity, classify_task
from g6.solver.constraints import evaluate_constraints
from g6.solver.engine import RunPlan, plan_run, solve
from g6.solver.history import HistoryReport, SolverHistory
from g6.solver.llm_quality_checker import LLMQualityChecker, QualityVerdict
from g6.solver.red_flag_detector import RedFlag, red_flags
from g6.solver.schema import (
    ConstraintSpec,
    NamedCheck,
    SolverFailure,
    SolverInput,
    SolverOutput,
    StepResult,
    derive_completion_state,
    derive_solver_failures,
)
from g6.solver.step_executor import StepContext, execute_step
from g6.solver.step_profiles import ExecutionProfile, profile_for
from g6.solver.step_registry import STEP_REGISTRY, StepMeta, StepRegistry

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unchecked_answer", "unnoticed_drift")
__g6_introduces__: tuple[str, ...] = ()

__all__ = [
    "HistoryReport", "SolverHistory", "STEP_REGISTRY", "CategoryRegistry", "ComponentCategory", "ConstraintSpec", "Decision", "ExecutionProfile",
    "LLMQualityChecker", "NamedCheck", "QualityVerdict", "RedFlag", "RunPlan", "SolverDecisionError",
    "SolverFailure", "SolverInput", "SolverOutput", "SolverPlanner", "StepContext", "StepMeta", "StepRegistry",
    "StepResult", "TaskComplexity", "classify_task", "derive_completion_state", "derive_solver_failures",
    "evaluate_constraints", "execute_step", "plan_run", "profile_for", "red_flags", "solve",
    "summarize_agentic_evidence",
]
