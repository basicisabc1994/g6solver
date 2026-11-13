# SPDX-License-Identifier: Apache-2.0
"""g6.goal_engine — holds the subtasks you declare as a tree and checks them against what your executor reports.

g6.goal_engine — declare a goal as a tree of subtasks with guardrails, checkpoints, resource bounds and breakpoints,
then read those controls against what an executor reports, without executing anything (audit 013 §11).

Capability label: ``implemented``. Nothing in this package executes a goal, or halts or pauses work. The planner
itself still generates no subtask: subtasks come from you, or, through ``propose_goal_tree``, from a proposer you
inject (over MCP, the model you configured, at most 100 calls), and that tree is a proposal until a person or a mission
accepts it. It describes a declared plan and evaluates its controls; an executor you write must call
``evaluate_controls`` before each node's work and obey the report.

    from g6.core.approval_authority import ApprovalAuthority
    from g6.goal_engine import GoalSpec, Observation, build_plan_tree, evaluate_controls, release_pause
    spec = GoalSpec.model_validate({
        "goal": "Publish the quarterly report",
        "resource_bounds": {"max_tokens_per_hour": 200000},
        "guardrails": [{"name": "safe_actions", "predicate": "csf_hazard", "params": {"max_prob": 0.05}}],
        "subtasks": [{"goal": "Draft the report"},
                     {"goal": "Send it out", "breakpoints": [{"name": "review_before_send"}]}],
    })
    plan = build_plan_tree(spec)        # nodes root, root.0, root.1 as declared; qualified-draft at best
    report = evaluate_controls(spec, Observation(node="root.1", action="file_write",
                                                 usage={"tokens_per_hour": 1200}))
    [(r.name, r.verdict) for r in report.results]   # [("resource_bounds", "pass"), ("safe_actions", "pass")]
    report.may_proceed                  # False: one PauseRequest is pending
    release_pause(report.pauses[0], approval_id, authority=authority)   # the ApprovalRecord, or None

Every control declared on a node applies to that node and every node below it. A guardrail that fails or cannot be
decided (a usage figure, metric or action the observation does not report) blocks; a checkpoint is reported and never
blocks; each active breakpoint asks for an approval that an ``ApprovalAuthority`` recorded for that pause. The
observation is the executor's word and nothing here checks it, so no result is ``verified``: plans and reports are
``qualified-draft`` at best and ``blocked-escalated`` when refused, blocked or paused. Register domain checks with
``register_predicate(name, factory)``; built-ins cannot be replaced unless ``override=True``.

Not ported from the archive's ``goal_engine`` (audit 013):

* ``GoalDecomposer``, which echoed the caller's subtasks or added an ``[unsolved]`` placeholder and stamped the tree
  ``verified`` (GE-003); its silent drop of guardrail-rejected and over-deep subtasks (GE-006); the solvability gate,
  fed the depth as its tool count so it could not fail (GE-008); the self-check, adaptive-depth classifier, bias
  depth override and ``depth_floor_check`` detector, which compared the cap with itself (GE-009, GE-017);
* the archive's ``SearchTree`` construction-time controls and no-op breakpoint callback (GE-004, GE-005), and the
  ``always_true`` / ``always_pause`` predicates, which read nothing (GE-005);
* ``core.py`` (a timeout checked before any work, GE-017), ``patterns.py`` and ``skills.py`` (catalogues with three
  false "executable" claims), ``tool_ops.py`` (the 325-op router, GE-010) and ``envelope.py`` (replaced by
  ``g6.core.reliability_envelope``);
* all 13 ``*_mcp`` sub-packages: in-process ``exec`` of caller code with full builtins or an escapable sandbox
  (GE-001, GE-014), ``vf_verify_code`` marking a claim verified because unrelated code ran (GE-002), LLM text and
  errors labelled verified (GE-011), templated and keyword "reasoning" (GE-015), a default-allow tool policy
  (GE-016), and per-process in-memory stores;
* the ``skill/`` plugin (agents, hooks with a hard-coded home path, commands, the aggregated MCP server) and the
  documentation that promised enforced halts and human gates (GE-001, GE-012, GE-017).
"""
from g6.goal_engine.block import GoalEngineBlock, GoalEngineInput
from g6.goal_engine.controls import (
    ApprovalRecord,
    ControlReport,
    ControlResult,
    PauseRequest,
    evaluate_controls,
    release_pause,
)
from g6.goal_engine.plan import DEFAULT_MAX_DEPTH, DroppedNode, PlanNode, PlanTree, build_plan_tree
from g6.goal_engine.predicates import HAZARDS, Verdict, list_predicates, register_predicate
from g6.goal_engine.schema import (
    BreakpointSpec,
    CheckpointSpec,
    GoalSpec,
    GuardrailSpec,
    Observation,
    ResourceBoundsSpec,
)

__g6_component__ = True
__g6_block__ = "GoalEngineBlock"
__g6_model__ = "llm"      # g6_invoke gives the block the model the person configured, by this keyword
__g6_maturity__ = "beta"  # the package's label; the block carries its own (component_maturity), which is the one read
__g6_sensitive_ops__: tuple[str, ...] = ()
__g6_requires__: tuple[str, ...] = ("align_prompt_library",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a proposed tree
# followed without checking each step).
__g6_mitigates__: tuple[str, ...] = ("goal_drift", "budget_overrun")
__g6_introduces__: tuple[str, ...] = ("unchecked_plan",)
__g6_outside__ = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["DEFAULT_MAX_DEPTH", "HAZARDS", "ApprovalRecord", "BreakpointSpec", "CheckpointSpec", "ControlReport",
           "ControlResult", "DroppedNode", "GoalEngineBlock", "GoalEngineInput", "GoalSpec", "GuardrailSpec",
           "Observation", "PauseRequest", "PlanNode", "PlanTree", "ResourceBoundsSpec", "Verdict", "build_plan_tree",
           "evaluate_controls", "list_predicates", "register_predicate", "release_pause"]
