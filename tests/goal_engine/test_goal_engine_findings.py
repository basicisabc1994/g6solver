# SPDX-License-Identifier: Apache-2.0
"""Audit 013 regression tests, one or more per finding, named after it.

The archive cannot run here, so each check says what the archive did. Each check is a function run twice: once
against this package (it must pass), and once with the archive's behaviour put back in memory by monkeypatching (it
must fail), so that none of them is a null instrument.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

import g6.goal_engine as goal_engine
import g6.goal_engine.controls as controls
import g6.goal_engine.plan as plan_module
import g6.goal_engine.predicates as predicates
from g6.core.approval_authority import ApprovalAuthority
from g6.goal_engine import (
    DEFAULT_MAX_DEPTH,
    BreakpointSpec,
    CheckpointSpec,
    GoalSpec,
    GuardrailSpec,
    Observation,
    PlanNode,
    ResourceBoundsSpec,
    build_plan_tree,
    evaluate_controls,
    release_pause,
)
from g6.goal_engine.codes import (
    APPROVAL_REQUIRED,
    CONTROL_INVALID,
    GOAL_EMPTY,
    NOT_DECOMPOSED,
    PLAN_DECLARED,
    PLAN_REJECTED,
    PLAN_TRUNCATED,
    PREDICATE_UNKNOWN,
    CompletionState,
)

PACKAGE = Path(goal_engine.__file__).parent
TESTS = Path(__file__).parent
SPDX = "# SPDX-License-Identifier: Apache-2.0"
# The archive's header words, assembled so that no file here carries them as a notice.
NOTICES = (" ".join(("Proprietary", "and", "confidential")), " ".join(("All", "rights", "reserved")))


def _sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(PACKAGE.glob("*.py"))}


def _sample(max_prob: float) -> GoalSpec:
    """The audit's GE-006 setup: the shape of examples/sample_goal.json with the top-level csf_hazard tightened."""
    return GoalSpec.model_validate({
        "goal": "Build and check an intrusion-detection pipeline",
        "resource_bounds": {"max_execution_seconds": 600, "max_tokens_per_hour": 500000},
        "guardrails": [{"name": "csf_code_execute", "predicate": "csf_hazard",
                        "params": {"action": "code_execute", "max_prob": max_prob}}],
        "subtasks": [{"goal": "Ingest"}, {"goal": "Train"}, {"goal": "Ground"}, {"goal": "Check"},
                     {"goal": "Deploy", "breakpoints": [{"name": "pre_deploy_artefact_review"}]}],
    })


# --- GE-001, GE-002, GE-011, GE-014 (dropped with their files): nothing executable ships ----------------------------

_CALLS = {"exec", "eval", "compile", "__import__", "breakpoint"}
_MODULES = {"subprocess", "socket", "urllib", "http", "ctypes", "multiprocessing", "threading", "asyncio", "os", "sys",
            "importlib", "pickle", "marshal", "shutil", "requests", "httpx", "anthropic", "openai", "fastmcp", "mcp"}
_CORE_MODULES = {"g6.core.llm", "g6.core.model_calls", "g6.core.sandbox", "g6.core.process_tree",
                 "g6.core.component_process"}


def _unsafe(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _CALLS:
            found.append(f"call {node.func.id}")
        names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        found += [f"import {n}" for n in names if n.split(".")[0] in _MODULES or n in _CORE_MODULES]
    return found


def check_nothing_executable(sources: dict[str, str]) -> None:
    found = {name: _unsafe(text) for name, text in sources.items()}
    assert not any(found.values()), found


def test_ge001_ge002_ge011_ge014_no_exec_eval_subprocess_thread_network_or_model_call_in_the_package() -> None:
    check_nothing_executable(_sources())


def test_ge001_check_catches_the_archive_exec_paths() -> None:
    # neurosymbolic_mcp/cegis.py exec'd caller strings with full builtins; solver_mcp/executor.py and
    # verifier_mcp/sandbox.py exec'd in threads; eml_mcp called a model subprocess.
    for archive in ("exec(candidate, cand_ns)\n", "import threading\n", "from subprocess import run\n",
                    "from g6.core.llm import LLMCaller\n"):
        with pytest.raises(AssertionError):
            check_nothing_executable({"archive.py": archive})


# --- GE-002, GE-003: no code path can state ``verified`` -----------------------------------------------------------

def check_never_verified(sources: dict[str, str]) -> None:
    stated = {name: [n.lineno for n in ast.walk(ast.parse(text)) if isinstance(n, ast.Constant) and n.value == "verified"]
              for name, text in sources.items()}
    assert not any(stated.values()), stated
    assert "verified" not in get_args(CompletionState)


def test_ge002_ge003_no_code_path_states_verified() -> None:
    check_never_verified(_sources())


def test_ge002_check_catches_the_archive_stamp() -> None:
    # decomposer.py:338 and verifier_mcp/verifier_block.py:138 both assigned the bare string.
    with pytest.raises(AssertionError):
        check_never_verified({"decomposer.py": 'tree.completion_state = "verified"\n'})


# --- GE-003: a goal without subtasks is not decomposed, and says so ------------------------------------------------

def check_ge003() -> None:
    alone = build_plan_tree(GoalSpec(goal="Build a web app"))
    assert alone.completion_state == "qualified-draft"
    assert alone.warning_card["code"] == NOT_DECOMPOSED
    assert "generates no subtasks" in alone.warning_card["message"]
    assert [n.goal for n in alone.nodes] == ["Build a web app"]  # no "[unsolved]" placeholder child
    declared = build_plan_tree(GoalSpec(goal="g", subtasks=[GoalSpec(goal="s")]))
    assert (declared.completion_state, declared.warning_card["code"]) == ("qualified-draft", PLAN_DECLARED)
    assert build_plan_tree(GoalSpec(goal="   ")).completion_state == "blocked-escalated"


def test_ge003_goal_without_subtasks_is_a_qualified_draft_that_needs_decomposition() -> None:
    check_ge003()


def test_ge003_check_catches_the_archive_verified_stamp(monkeypatch) -> None:
    # The archive stamped every tree "verified"; the result type cannot even hold it.
    monkeypatch.setattr(plan_module, "_state", lambda blocked: "verified")
    with pytest.raises(ValidationError, match="completion_state"):
        check_ge003()


def test_ge003_check_catches_the_archive_placeholder(monkeypatch) -> None:
    # decomposer.py:355-358: with no subtasks, the archive added GoalInput(goal=f"[unsolved] {goal}").
    walk = plan_module._walk

    def archive_walk(spec, max_depth):
        nodes, rejected, truncated = walk(spec, max_depth)
        if len(nodes) == 1:
            nodes.append(PlanNode(node_id="root.0", parent_id="root", depth=1, goal=f"[unsolved] {spec.goal}"))
        return nodes, rejected, truncated

    monkeypatch.setattr(plan_module, "_walk", archive_walk)
    with pytest.raises(AssertionError):
        check_ge003()


# --- GE-004: an active breakpoint yields a PauseRequest that only a recorded approval releases ---------------------

def check_ge004() -> None:
    spec = GoalSpec(goal="Deploy", breakpoints=[BreakpointSpec(name="pre_deploy_review")],
                    subtasks=[GoalSpec(goal="Push the artefact")])
    report = evaluate_controls(spec, Observation(), run_id="run-1")
    assert [p.breakpoint for p in report.pauses] == ["pre_deploy_review"]
    assert not report.may_proceed and report.completion_state == "blocked-escalated"
    assert report.warning_card["code"] == APPROVAL_REQUIRED
    pause = report.pauses[0]
    authority = ApprovalAuthority()
    assert release_pause(pause, "approved-by-the-caller", authority=authority) is None
    record = authority.issue(reviewer_id="reviewer-1", scope=pause.scope, run_id="run-1")
    released = release_pause(pause, record.approval_id, authority=authority)
    assert released is not None and released.reviewer_id == "reviewer-1"
    below = evaluate_controls(spec, Observation(node="root.0"), run_id="run-1")
    assert [(p.node_id, p.declared_by) for p in below.pauses] == [("root.0", "root")]


def test_ge004_active_root_breakpoint_yields_a_pause_only_a_recorded_approval_releases() -> None:
    check_ge004()


def test_ge004_check_catches_the_archive_noop_breakpoint(monkeypatch) -> None:
    # predicates.py:210: callback=lambda _: None, so nothing ever paused.
    monkeypatch.setattr(controls, "_pause_requests", lambda *args, **kwargs: ())
    with pytest.raises(AssertionError):
        check_ge004()


# --- GE-005: built-in predicates read the observation; unknown is never a pass; the root is evaluated --------------

_ARCHIVE_HAZARDS = {"llm_call": 0.05, "file_write": 0.01, "code_execute": 0.10, "external_api": 0.08, "rollback": 0.001}


def _archive_csf(params):  # predicates.py:77-95: a constant per spec; an unknown action had hazard 0.0
    return lambda node: _ARCHIVE_HAZARDS.get(params.get("action", ""), 0.0) <= params.get("max_prob", 1.0)


def _archive_metric_above(params):  # predicates.py:49-60: read the metric off the node; missing passed
    def predicate(node):
        value = getattr(node, params.get("metric", ""), None)
        return True if value is None else float(value) >= params.get("threshold", 0.0)
    return predicate


def _archive_resource_limit(params):  # predicates.py:25-46: compared the node's DECLARED bounds, never usage
    def predicate(node):
        declared = getattr(node, "resource_bounds", None)
        if declared is None:
            return True
        limit, cap = getattr(declared, "max_tokens_per_hour", None), params.get("max_tokens_per_hour")
        return not (cap is not None and limit is not None and limit > cap)
    return predicate


def check_ge005_csf() -> None:
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="g", predicate="csf_hazard", params={"max_prob": 1.0})])
    assert [r.verdict for r in evaluate_controls(spec, Observation(action="unknown_op")).results] == ["fail"]
    unreported = evaluate_controls(spec, Observation())
    assert [r.verdict for r in unreported.results] == ["unknown"] and not unreported.may_proceed
    # The audit's falsification, csf_hazard with params {"action": "rm_rf_root"}: a spec error, never a pass.
    falsifier = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="g", predicate="csf_hazard",
                                                             params={"action": "rm_rf_root"})])
    result = evaluate_controls(falsifier, Observation(action="rm_rf_root")).results[0]
    assert (result.verdict, result.code) == ("unknown", CONTROL_INVALID)


def check_ge005_metric() -> None:
    spec = GoalSpec(goal="train",
                    guardrails=[GuardrailSpec(name="f1_floor", predicate="metric_above",
                                              params={"metric": "f1", "threshold": 0.5})],
                    checkpoints=[CheckpointSpec(name="f1_gate", predicate="metric_above",
                                                params={"metric": "f1", "threshold": 0.92})])
    missing = evaluate_controls(spec, Observation())
    assert {r.name: r.verdict for r in missing.results} == {"f1_floor": "unknown", "f1_gate": "unknown"}
    assert not missing.may_proceed
    low = evaluate_controls(spec, Observation(metrics={"f1": 0.4}))
    assert {r.name: r.verdict for r in low.results} == {"f1_floor": "fail", "f1_gate": "fail"}


def check_ge005_resource() -> None:
    spec = GoalSpec(goal="g", guardrails=[GuardrailSpec(name="tokens", predicate="resource_limit",
                                                        params={"max_tokens_per_hour": 500000})])
    over = evaluate_controls(spec, Observation(usage={"tokens_per_hour": 600000}))
    assert over.results[0].verdict == "fail" and over.blocked
    assert evaluate_controls(spec, Observation()).results[0].verdict == "unknown"


def test_ge005_csf_hazard_fails_an_unknown_action_and_is_unknown_without_one() -> None:
    check_ge005_csf()


def test_ge005_metric_predicates_are_unknown_when_the_metric_is_not_reported() -> None:
    check_ge005_metric()


def test_ge005_resource_limit_reads_reported_usage_not_declared_limits() -> None:
    check_ge005_resource()


@pytest.mark.parametrize(("name", "archive", "check"), [
    ("csf_hazard", _archive_csf, check_ge005_csf),
    ("metric_above", _archive_metric_above, check_ge005_metric),
    ("resource_limit", _archive_resource_limit, check_ge005_resource),
])
def test_ge005_checks_catch_the_archive_predicates(monkeypatch, name, archive, check) -> None:
    monkeypatch.setitem(predicates._PREDICATES, name, archive)
    with pytest.raises(AssertionError):
        check()


# --- GE-006: rejected and truncated subtasks are reported, never silently dropped ----------------------------------

def check_ge006() -> None:
    spec = _sample(max_prob=0.05)  # the archive dropped all five subtasks here and kept the root, "verified"
    plan = build_plan_tree(spec)
    assert len(plan.nodes) == 6 and plan.completion_state == "qualified-draft"
    report = evaluate_controls(spec, Observation(node="root.0", action="code_execute",
                                                 usage={"execution_seconds": 1.0, "tokens_per_hour": 1.0}))
    assert [(r.name, r.verdict) for r in report.results if r.name == "csf_code_execute"] == [("csf_code_execute",
                                                                                            "fail")]
    assert report.completion_state == "blocked-escalated"

    nested = GoalSpec(goal="top", subtasks=[
        GoalSpec(goal="   ", subtasks=[GoalSpec(goal="orphan")]),
        GoalSpec(goal="a", subtasks=[GoalSpec(goal="b", subtasks=[GoalSpec(goal="c")])]),
        GoalSpec(goal="typo", checkpoints=[CheckpointSpec(name="x", predicate="metric_abov")]),
    ])
    plan = build_plan_tree(nested, max_depth=2)
    assert {(d.node_id, d.code, d.subtree_size) for d in plan.rejected} == {("root.0", GOAL_EMPTY, 2),
                                                                            ("root.2", PREDICATE_UNKNOWN, 1)}
    assert [(d.node_id, d.code, d.depth) for d in plan.truncated] == [("root.1.0.0", PLAN_TRUNCATED, 3)]
    assert plan.warning_card["code"] == PLAN_REJECTED and plan.completion_state == "qualified-draft"
    assert {e.get("node_id") for e in plan.evidence} >= {"root.0", "root.2", "root.1.0.0"}
    assert [n.node_id for n in plan.nodes] == ["root", "root.1", "root.1.0"]
    only_deep = build_plan_tree(GoalSpec(goal="a", subtasks=[GoalSpec(goal="b")]), max_depth=0)
    assert only_deep.warning_card["code"] == PLAN_TRUNCATED and only_deep.truncated[0].node_id == "root.0"


def test_ge006_rejected_and_truncated_subtasks_are_reported_and_guardrails_do_not_drop_nodes() -> None:
    check_ge006()


def test_ge006_check_catches_the_archive_silent_drop(monkeypatch) -> None:
    # search_tree.py:232-239 removed a rejected child and returned ok; decomposer.py:113-118 skipped it.
    walk = plan_module._walk
    monkeypatch.setattr(plan_module, "_walk", lambda spec, max_depth: (walk(spec, max_depth)[0], [], []))
    with pytest.raises(AssertionError):
        check_ge006()


# --- GE-007: per-subtask controls apply to that subtask and everything below it, and nowhere else -------------------

def check_ge007() -> None:
    spec = GoalSpec(goal="top", subtasks=[
        GoalSpec(goal="risky", resource_bounds=ResourceBoundsSpec(max_execution_seconds=60),
                 guardrails=[GuardrailSpec(name="tight", predicate="resource_limit",
                                           params={"max_tokens_per_hour": 10})],
                 subtasks=[GoalSpec(goal="below")]),
        GoalSpec(goal="sibling"),
    ])
    usage = {"tokens_per_hour": 100.0, "execution_seconds": 120.0}
    risky = evaluate_controls(spec, Observation(node="root.0", usage=usage))
    assert [(r.name, r.declared_by, r.verdict) for r in risky.results] == [("resource_bounds", "root.0", "fail"),
                                                                           ("tight", "root.0", "fail")]
    assert evaluate_controls(spec, Observation(node="root.0.0", usage=usage)).blocked  # inherited below
    for node in ("root", "root.1"):  # survival: outside that subtree the same usage is fine
        assert evaluate_controls(spec, Observation(node=node, usage=usage)).may_proceed
    nested_gate = GoalSpec(goal="g", subtasks=[GoalSpec(goal="s", breakpoints=[BreakpointSpec(name="review")])])
    assert [p.declared_by for p in evaluate_controls(nested_gate, Observation(node="root.0")).pauses] == ["root.0"]
    assert evaluate_controls(nested_gate, Observation()).pauses == ()


def test_ge007_a_subtask_guardrail_blocks_that_subtask_and_its_subtree_only() -> None:
    check_ge007()


def test_ge007_check_catches_the_archive_top_level_only_controls(monkeypatch) -> None:
    # decomposer.py:314-317 resolved only data.guardrails/checkpoints/breakpoints; nested specs were discarded.
    chain = controls._scope_chain
    monkeypatch.setattr(controls, "_scope_chain",
                        lambda spec, node_id: chain(spec, node_id) and [("root", spec)])
    with pytest.raises(AssertionError):
        check_ge007()


# --- GE-008 (gate dropped with decomposer.py) and GE-009: depth is measured, no gate or self-check is claimed --------

def check_ge008(spec: GoalSpec) -> None:
    text = json.dumps(build_plan_tree(spec).model_dump(), default=str).lower()
    assert "solvab" not in text, "the plan claims a solvability check that does not exist"
    assert not [name for name in dir(goal_engine) if "solvab" in name.lower()]


def check_ge009() -> None:
    chain = GoalSpec(goal="a", subtasks=[GoalSpec(goal="b", subtasks=[GoalSpec(goal="c", subtasks=[GoalSpec(goal="d")])])])
    plan = build_plan_tree(chain)
    assert (plan.depth, plan.max_depth) == (3, DEFAULT_MAX_DEPTH)  # measured, not the cap
    alone = build_plan_tree(GoalSpec(goal="a"))
    assert alone.depth == 0 and alone.warning_card["code"] == NOT_DECOMPOSED  # the shallow tree is flagged


def test_ge008_the_plan_claims_no_solvability_gate() -> None:
    check_ge008(_sample(max_prob=0.10))


def test_ge008_check_catches_the_archive_solvability_claim(monkeypatch) -> None:
    # decomposer.py:340-345 recorded evidence solvability_checked=True from a gate that could not fail.
    stamp = plan_module.evidence
    monkeypatch.setattr(plan_module, "evidence", lambda *a, **k: {**stamp(*a, **k), "solvability_checked": True})
    with pytest.raises(AssertionError):
        check_ge008(_sample(max_prob=0.10))


def test_ge009_plan_depth_is_the_deepest_node_kept() -> None:
    check_ge009()


def test_ge009_check_catches_the_archive_cap_as_depth(monkeypatch) -> None:
    # decomposer.py:337: tree.actual_depth = max_depth, then validate_tree compared it with itself.
    monkeypatch.setattr(plan_module, "_depth", lambda nodes: DEFAULT_MAX_DEPTH)
    with pytest.raises(AssertionError):
        check_ge009()


# --- GE-010: unknown fields and loose numbers are refused -----------------------------------------------------------

_GOALS = ({"goal": "g", "max_depth": 3}, {"task": "t"}, {"goal": "g", "subtasks": [{"goal": "s", "task": "t"}]},
          {"goal": "g", "guardrails": [{"name": "n", "predicate": "p", "param": {}}]})
_BOUNDS = ({"max_tokens": 5}, {"max_execution_seconds": float("nan")}, {"max_tokens_per_hour": "5"},
           {"max_tokens_per_hour": True}, {"max_disk_bytes": -1})


def _refuses(model, data) -> bool:
    try:
        model.model_validate(data)
    except ValidationError:
        return True
    except Exception:  # a raw TypeError is a crash, not a refusal
        return False
    return False


def check_ge010(goal_model, bounds_model) -> None:
    accepted = [d for d in _GOALS if not _refuses(goal_model, d)] + [d for d in _BOUNDS if not _refuses(bounds_model, d)]
    assert not accepted, f"accepted without error: {accepted}"


def test_ge010_unknown_fields_and_loose_numbers_are_validation_errors() -> None:
    check_ge010(GoalSpec, ResourceBoundsSpec)


def test_ge010_check_catches_the_archive_schema() -> None:
    from g6.intent_engine.schema import GoalInput, ResourceBoundsSchema  # the archive's schema, ported unchanged

    with pytest.raises(AssertionError):
        check_ge010(GoalInput, ResourceBoundsSchema)


# --- GE-012: the package says what it does, and claims no enforcement ----------------------------------------------

_CLAIMS = ("halt execution", "halts execution", "enforces", "enforced", "stop and wait", "decomposes goals")


def check_ge012(doc: str) -> None:
    offered = doc.split("Not ported from the archive", 1)[0]
    assert "Nothing in this package executes a goal" in offered
    assert not [claim for claim in _CLAIMS if claim in offered.lower()]


def test_ge012_package_docstring_states_nothing_executes_and_claims_no_enforcement() -> None:
    check_ge012(goal_engine.__doc__ or "")


def test_ge012_check_catches_the_archive_use_case_text() -> None:
    # docs/mkdocs/docs/use-cases/index.md:3, :46-48.
    archive = ("The GoalDecomposer decomposes goals into a SearchTree. Guardrails halt execution when violated; "
               "breakpoints stop and wait for human review.")
    with pytest.raises(AssertionError):
        check_ge012(archive)


# --- GE-013: every file is Apache-2.0 and carries no proprietary notice ---------------------------------------------

def check_ge013(sources: dict[str, str], tests: dict[str, str]) -> None:
    wrong = [n for n, t in {**sources, **tests}.items() if (t.splitlines() or [""])[0] != SPDX]
    noticed = [n for n, t in sources.items() if any(word in t for word in NOTICES)]
    assert not wrong and not noticed, (wrong, noticed)


def test_ge013_every_file_is_apache_2_0_without_a_proprietary_notice() -> None:
    check_ge013(_sources(), {p.name: p.read_text(encoding="utf-8") for p in TESTS.glob("*.py")})


def test_ge013_check_catches_the_archive_header() -> None:
    archive = f"# {NOTICES[0]}. See LICENSE for terms.\n"
    with pytest.raises(AssertionError):
        check_ge013({"decomposer.py": archive}, {})
    with pytest.raises(AssertionError):
        check_ge013({"decomposer.py": f"{SPDX}\n# {NOTICES[1]}.\n"}, {})


# --- GE-015, GE-016, GE-017: only the small core ships, with no hard-coded home paths --------------------------------

_SHIPPED = {"__init__.py", "block.py", "codes.py", "controls.py", "decomposition.py", "plan.py", "predicates.py", "schema.py"}
_HOME = ("C:" + "\\Users\\", "C:" + "/Users/", "/home/", "/Users/")  # built at runtime (owner-approved, R-08)


def check_small_core(files: set[str], dirs: set[str], sources: dict[str, str]) -> None:
    assert files == _SHIPPED and not dirs - {"__pycache__", "skill"}, (files, dirs)  # skill/: its SKILL.md (FW-01)
    assert not [n for n, t in sources.items() if any(h in t for h in _HOME)]


def test_ge015_ge016_ge017_only_the_core_ships_and_no_home_path_is_hard_coded() -> None:
    entries = list(PACKAGE.iterdir())
    check_small_core({p.name for p in entries if p.is_file() and p.suffix == ".py"},
                     {p.name for p in entries if p.is_dir()}, _sources())


def test_ge015_check_catches_the_archive_layout() -> None:
    # 13 *_mcp sub-packages (templated reasoning, GE-015; solver_mcp's default-allow policy, GE-016) and
    # skill/hooks/hooks.json:31 with a user's home directory in it (GE-017).
    with pytest.raises(AssertionError):
        check_small_core(_SHIPPED, {"tumix_mcp", "solver_mcp"}, {})
    with pytest.raises(AssertionError):
        check_small_core(_SHIPPED, set(), {"hooks.py": 'CWD = "C:' + '\\Users\\someone\\Desktop\\repo"'})
