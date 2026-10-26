# SPDX-License-Identifier: Apache-2.0
"""The labelling choke point, the refusals, and the shape of the package (audit 017 FM-002, FM-008, FM-009, FM-013,
FM-014, FM-015, FM-017, FM-018, FM-019, FM-020). None of these needs Z3."""
from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.formal
from g6.formal import (
    FormalResult,
    ValidityResult,
    apply_envelope,
    check_propositional,
    solve,
    solver_support,
)
from g6.formal.envelope import (
    BACKEND_REFUSED,
    CONCLUSION_ASSUMED,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    NO_DECISION,
    SOLVER_DECISION,
    VACUOUS_PREMISES,
)
from g6.invariants import check_envelope

SRC = Path(g6.formal.__file__).parent
TESTS = Path(__file__).parent


def _artifact():
    return check_propositional("A | B").artifact


# --- FM-002 (and FM-010, FM-011, FM-012, closed by not porting): no request reaches a code-executing engine ----------

@pytest.mark.parametrize("request_", [
    {"solver": "prolog", "formula": "shell('touch fm002')"},
    {"solver": "lean", "formula": '#eval IO.Process.run {cmd := "touch", args := #["fm002"]}'},
    {"solver": "tla", "formula": "x"},
    {"solver": "z3", "formula": "x", "backend": "prolog_mcp"},
    {"formula": "x", "backend": "lean_mcp"},
    {"formula": "x", "operation": "run_code"},
    {"formula": "x", "op": "solve", "solver": "prolog"},
    {"formula": "x", "engine": "sagemath"},
])
def test_fm002_a_request_for_another_engine_is_refused(request_, tmp_path, monkeypatch) -> None:
    """Archive: op="solve", solver="prolog" reached janus.query_once(<caller goal>), and backend="prolog_mcp" reached
    the Prolog handler, round the tool-name deny-list. Here the request is refused before anything runs."""
    monkeypatch.chdir(tmp_path)
    result = solve({**request_, "request_id": "q", "run_id": "r"})
    assert result.code == BACKEND_REFUSED and result.completion_state == "blocked-escalated", result.reason
    assert result.satisfiable is None and result.artifact is None
    assert result.evidence[0]["external_solver_ran"] is False and result.evidence[0]["kind"] == "formal_refusal"
    assert check_envelope(result.model_dump()) == [] and (result.request_id, result.run_id) == ("q", "r")
    assert list(tmp_path.iterdir()) == []


def test_fm002_apply_envelope_refuses_a_foreign_backend_and_drops_its_claim() -> None:
    claimed = FormalResult(operation="check", backend="lean", satisfiable=True, model={"p": True},
                           artifact=_artifact())
    result = apply_envelope(claimed, backend="lean", operation="check")
    assert result.code == BACKEND_REFUSED and result.completion_state == "blocked-escalated"
    assert (result.satisfiable, result.model, result.artifact) == (None, {}, None)
    validity = apply_envelope(ValidityResult(operation="prove", backend="triangulation", valid=True,
                                             counterexample={"x": 1}), backend="triangulation", operation="prove")
    assert validity.code == BACKEND_REFUSED and validity.valid is False and validity.counterexample is None


_FORBIDDEN_MODULES = {"subprocess", "socket", "urllib", "http", "requests", "httpx", "ctypes", "multiprocessing",
                      "os", "shutil", "importlib", "janus_swi", "asyncio", "anthropic", "openai", "litellm",
                      "g6.core.llm", "g6.core.model_calls", "g6.core.sandbox"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint"}


def test_fm002_no_code_path_runs_code_writes_files_or_reaches_the_network() -> None:
    """Archive: Prolog via janus (FM-002, FM-010), exec() in z3_run_code (FM-011), an unpinned jar download (FM-012),
    unmetered LLM calls (FM-019). This package imports none of the means and calls none of the builtins."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            found += [f"{path.name}: import {n}" for n in names
                      if any(n == m or n.startswith(m + ".") for m in _FORBIDDEN_MODULES)]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                found.append(f"{path.name}:{node.lineno}: {node.func.id}()")
    assert found == []


def test_fm009_a_caller_declared_confidence_is_not_an_input() -> None:
    """Archive: the router admitted a safety-critical sub-package call on the caller's own grounding_confidence and
    human_reviewed. There is no gate here, and no field to declare one with."""
    result = solve({"formula": "TRUE", "solver": "propositional",
                    "source_payload": {"grounding_confidence": 1.0, "human_reviewed": True}})
    assert result.code == INPUT_INVALID and result.completion_state == "blocked-escalated"


# --- FM-013: the pure-Python path says what it is --------------------------------------------------------------------

def test_fm013_the_dpll_is_labelled_pure_python_never_an_engine() -> None:
    """Archive: the router stamped backend_executed=True when a pure-Python stand-in had answered."""
    result = solve({"solver": "propositional", "formula": "A & ~B"})
    assert result.satisfiable is True and result.completion_state == "qualified-draft"
    evidence = result.evidence[0]
    assert evidence["pure_python"] is True and evidence["external_solver_ran"] is False
    assert "pure-Python" in evidence["engine"] and "no external solver ran" in result.warning_card["message"]
    rows = {row["backend"]: row for row in solver_support()}
    assert rows["propositional"]["pure_python"] is True and rows["propositional"]["available"] is True
    assert rows["z3"]["pure_python"] is False and set(rows["z3"]) >= {"available", "version", "reason", "probe"}


def test_fm013_a_dpll_decision_cannot_be_relabelled_as_z3() -> None:
    relabelled = apply_envelope(check_propositional("A"), backend="z3", operation="check_sat")
    assert relabelled.code == NO_DECISION and relabelled.completion_state == "blocked-escalated"
    assert "'propositional', not 'z3'" in relabelled.reason
    assert relabelled.evidence[0]["pure_python"] is True and relabelled.evidence[0]["external_solver_ran"] is False


# --- the envelope: never verified, always the five fields, invariants enforced here ----------------------------------

def test_no_result_can_be_verified() -> None:
    """FM-001, FM-003, FM-008: three archive paths reached completion_state="verified" (an elaborating Lean file, one
    LLM-translated Z3 leg, parse success). Here ``verified`` is not a value a result can hold."""
    for model in (FormalResult, ValidityResult):
        with pytest.raises(ValidationError):
            model(operation="check", backend="z3", completion_state="verified")
    best = apply_envelope(FormalResult(operation="check_sat", backend="propositional", satisfiable=True,
                                       artifact=_artifact()), backend="propositional", operation="check_sat")
    assert best.completion_state == "qualified-draft" and best.warning_card["verified"] is False


def test_fm014_the_envelope_enforces_vacuity_whoever_built_the_result() -> None:
    vacuous = apply_envelope(ValidityResult(operation="v", backend="z3", valid=True, vacuous=True,
                                            artifact=_artifact()), backend="z3", operation="v")
    assert vacuous.valid is False and vacuous.code == VACUOUS_PREMISES
    assert vacuous.completion_state == "blocked-escalated"
    circular = apply_envelope(ValidityResult(operation="v", backend="z3", valid=True, conclusion_assumed=True,
                                             artifact=_artifact()), backend="z3", operation="v")
    assert circular.code == CONCLUSION_ASSUMED and circular.completion_state == "blocked-escalated"


def test_a_result_with_no_recorded_decision_is_not_a_decision() -> None:
    result = apply_envelope(FormalResult(operation="check_sat", backend="z3", satisfiable=True),
                            backend="z3", operation="check_sat")
    assert result.code == NO_DECISION and result.completion_state == "blocked-escalated"


def test_apply_envelope_is_idempotent_and_keeps_ids() -> None:
    once = check_propositional("A", request_id="q", run_id="r")
    twice = apply_envelope(once, backend="propositional", operation="check_sat")
    assert twice == once and once.warning_card["code"] == SOLVER_DECISION


@pytest.mark.parametrize("call", [
    lambda **ids: check_propositional("A & ~A", **ids),
    lambda **ids: check_propositional(" | ".join(f"(a{i} & b{i})" for i in range(20)), **ids),
    lambda **ids: solve({"solver": "prolog", "formula": "x", **ids}),
    lambda **ids: solve({"solver": "propositional", "formula": "A", **ids}),
    lambda **ids: solve({"formula": "", **ids}),
])
def test_fm015_every_result_carries_the_envelope(call) -> None:
    """Archive: the skill MCP tools returned JSON without completion_state, warning_card, evidence or ids."""
    dumped = call(request_id="req-1", run_id="run-1").model_dump()
    for key in ("completion_state", "warning_card", "evidence", "request_id", "run_id"):
        assert key in dumped
    assert (dumped["request_id"], dumped["run_id"]) == ("req-1", "run-1") and dumped["evidence"]
    assert check_envelope(dumped) == [] and dumped["completion_state"] != "verified"


def test_a_too_large_formula_is_its_own_code() -> None:
    result = check_propositional(" | ".join(f"(a{i} & b{i})" for i in range(20)))
    assert result.code == INPUT_TOO_LARGE


# --- FM-017, FM-018, FM-020: the package itself ---------------------------------------------------------------------

def test_fm017_the_package_is_the_core_only() -> None:
    """Archive: 216 files, 18 sub-packages, about 400 tools. Here: six modules and no sub-package."""
    modules = list(pkgutil.iter_modules(g6.formal.__path__))
    assert not [m.name for m in modules if m.ispkg]
    # owner, 2026-10-20: formal methods ported into the library (design 2026-10-20-formal-methods-port-design.md);
    # core run 6 merged formal_verification's check_contract as one module.
    assert sorted(m.name for m in modules) == ["contract", "envelope", "facade", "formal_block", "four_valued",
                                               "proof_artifacts", "propositional", "schema", "z3_bridge", "z3_depth"]


def test_fm018_every_file_is_apache_2_with_no_proprietary_notice() -> None:
    """Archive: "Proprietary and confidential" headers beside an MIT plugin.json."""
    for path in sorted([*SRC.rglob("*.py"), *TESTS.glob("*.py")]):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
        if path.is_relative_to(SRC):
            for word in ("Proprietary", "All rights reserved", "Copyright"):
                assert word not in text, f"{path.name}: {word}"


def test_fm020_the_docstring_states_the_label_and_the_ceiling() -> None:
    """Archive docs claimed "only Lean proofs reach verified", "network: none" and a "preferred" native ProB that
    never ran."""
    doc = g6.formal.__doc__ or ""
    assert "Capability label: ``implemented``" in doc and "(audit 017 §11)" in doc
    assert "The best state is ``qualified-draft``" in doc and "nothing here is ``verified``" in doc
    for claim in ("only lean proofs reach", "preferred engine", "hash-reverifiable", "solver-grounded",
                  "machine-checked", "network: none"):
        assert claim not in doc.lower()
