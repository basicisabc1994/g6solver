# SPDX-License-Identifier: Apache-2.0
"""g6.optimisation's block (owner, 2026-10-20): every op reachable through the registry and ``g6_invoke``, each answer
inside the library envelope and never ``verified``; unknown ops and unknown fields refused; nothing runs caller code;
and without OR-Tools the package still imports, every solving op is an honest refusal, and ``ops`` and ``help`` still
answer."""
from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.optimisation
from g6.core import discovery
from g6.core.maturity import maturity_reason
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.mcp import invoke_reply
from g6.optimisation.envelope import NOT_PROVEN_OPTIMAL, SOLVER_ANSWER, SOLVER_UNAVAILABLE
from g6.optimisation.optimisation_block import OptimisationBlock
from g6.optimisation.schema import OptimResult

HAS_ORTOOLS = importlib.util.find_spec("ortools") is not None
needs_ortools = pytest.mark.skipif(not HAS_ORTOOLS, reason="OR-Tools is not installed (pip install 'g6[optim] @ git+https://github.com/basicisabc1994/g6solver')")
SRC = Path(g6.optimisation.__file__).parent

DISTANCES = [[0, 2, 9, 10, 7], [2, 0, 6, 4, 3], [9, 6, 0, 8, 5], [10, 4, 8, 0, 6], [7, 3, 5, 6, 0]]
CALLS: dict[str, dict] = {
    "solve_lp": {"variables": {"doors": {}, "windows": {}},
                 "constraints": [{"name": "plant1", "terms": {"doors": 1}, "ub": 4},
                                 {"name": "plant2", "terms": {"windows": 2}, "ub": 12},
                                 {"name": "plant3", "terms": {"doors": 3, "windows": 2}, "ub": 18}],
                 "objective": {"doors": 3, "windows": 5}, "sense": "maximise"},
    "solve_mip": {"variables": {"a": {"ub": 10, "integer": True}, "b": {"ub": 10, "integer": True}},
                  "constraints": [{"terms": {"a": 2, "b": 3}, "ub": 7.5}], "objective": {"a": 3, "b": 4},
                  "sense": "maximize"},
    "solve_cp": {"variables": {"x": {"lb": 0, "ub": 3}, "y": {"lb": 0, "ub": 3}, "z": {"lb": 0, "ub": 3}},
                 "constraints": [{"terms": {"x": 1, "y": 1, "z": 1}, "lb": 5}], "all_different": [["x", "y", "z"]],
                 "objective": {"x": 1, "y": 2}, "sense": "maximise"},
    "solve_knapsack": {"values": [60, 100, 120], "weights": [10, 20, 30], "capacities": [50]},
    "solve_job_shop": {"jobs": [
        [{"machine": 0, "duration": 3}, {"machine": 1, "duration": 2}, {"machine": 2, "duration": 2}],
        [{"machine": 0, "duration": 2}, {"machine": 2, "duration": 1}, {"machine": 1, "duration": 4}],
        [{"machine": 1, "duration": 4}, {"machine": 2, "duration": 3}]]},
    "solve_assignment": {"costs": [[4, 1, 3], [2, 0, 5], [3, 2, 2]]},
    "solve_max_flow": {"source": "s", "sink": "t", "arcs": [
        {"from": "s", "to": "a", "capacity": 3}, {"from": "s", "to": "b", "capacity": 2},
        {"from": "a", "to": "b", "capacity": 5}, {"from": "a", "to": "t", "capacity": 2},
        {"from": "b", "to": "t", "capacity": 3}]},
    "solve_min_cost_flow": {"supplies": {"a": 2, "d": -2}, "arcs": [
        {"from": "a", "to": "b", "capacity": 2, "cost": 1}, {"from": "a", "to": "c", "capacity": 2, "cost": 3},
        {"from": "b", "to": "d", "capacity": 1, "cost": 1}, {"from": "b", "to": "c", "capacity": 2, "cost": 1},
        {"from": "c", "to": "d", "capacity": 2, "cost": 1}]},
    "solve_routing": {"distances": DISTANCES, "time_limit": 2},
}
#: The SciPy ops (merged from adapt_optimisation and adapt_pygad); tests/optimisation/test_nonlinear.py runs them.
SEARCHES = ("minimise", "global_search")
EXPECTED = {  # objective, and the warning card on the qualified draft
    "solve_lp": (36, SOLVER_ANSWER), "solve_mip": (10, SOLVER_ANSWER), "solve_cp": (8, SOLVER_ANSWER),
    "solve_knapsack": (220, SOLVER_ANSWER), "solve_job_shop": (11, SOLVER_ANSWER), "solve_assignment": (5, SOLVER_ANSWER),
    "solve_max_flow": (5, SOLVER_ANSWER), "solve_min_cost_flow": (5, SOLVER_ANSWER), "solve_routing": (None, NOT_PROVEN_OPTIMAL),
}


@pytest.fixture
def no_ortools(monkeypatch: pytest.MonkeyPatch) -> None:
    """``import ortools`` (and every ``ortools.*`` already loaded) now raises ImportError, as on a machine without it."""
    for name in [m for m in sys.modules if m == "ortools" or m.startswith("ortools.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "ortools", None)


def test_optimisation_is_registered_with_a_block_and_no_sensitive_op() -> None:
    assert get_registry().get_class("optimisation") is OptimisationBlock
    info = discovery.describe("optimisation")
    assert info["block"] == "OptimisationBlock" and info["maturity"] == "beta" and info["sensitive_ops"] == []
    assert info["requires"] == [] and sorted(info["ops"]) == sorted([*CALLS, *SEARCHES])
    assert info["summary"].endswith(".") and len(info["summary"]) <= 170
    words = ((g6.optimisation.__doc__ or "") + maturity_reason(OptimisationBlock)).lower()
    assert "experimental" not in words and "poetry" not in words


@needs_ortools
@pytest.mark.parametrize("op", list(CALLS))
def test_every_op_answers_through_g6_invoke_inside_the_envelope(op: str) -> None:
    reply = invoke_reply("optimisation", op, json.dumps({**CALLS[op], "request_id": "req-1", "run_id": "run-1"}))
    assert check_envelope(reply) == [] and reply["ok"] is True, reply.get("error")
    result = reply["result"]
    assert check_envelope(result) == [] and (result["component"], result["op"]) == ("optimisation", op)
    assert result["completion_state"] == reply["completion_state"] == "qualified-draft", (result["code"], result["reason"])
    objective, card = EXPECTED[op]
    assert reply["warning_card"]["code"] == result["warning_card"]["code"] == card
    if objective is not None:
        assert result["objective"] == pytest.approx(objective)
    assert result["check"]["passed"] is True and result["evidence"][0]["check"]["passed"] is True
    assert (result["request_id"], result["run_id"]) == ("req-1", "run-1")
    assert result["evidence"][0]["engine"].startswith("OR-Tools") and result["evidence"][0]["engine_version"]


@pytest.mark.parametrize("op", list(CALLS))
def test_without_ortools_every_solving_op_is_blocked_and_says_what_to_install(op: str, no_ortools) -> None:
    reply = invoke_reply("optimisation", op, json.dumps(CALLS[op]))
    assert check_envelope(reply) == [] and reply["completion_state"] == "blocked-escalated"
    card = reply["warning_card"]
    assert card["code"] == SOLVER_UNAVAILABLE and "pip install 'g6[optim] @ git+https://github.com/basicisabc1994/g6solver'" in card["suggested_action"]
    result = reply["result"]
    assert result["code"] == SOLVER_UNAVAILABLE and result["objective"] is None and result["solution"] == {}
    assert result["evidence"][0]["external_solver_ran"] is False and check_envelope(result) == []


@pytest.mark.parametrize("op", ["ops", "help"])
def test_ops_and_help_answer_with_or_without_ortools(op: str, monkeypatch: pytest.MonkeyPatch) -> None:
    for blocked in (False, True):
        if blocked:
            monkeypatch.setitem(sys.modules, "ortools", None)
        payload = get_registry().invoke("optimisation", op).unwrap()
        assert len(payload[op]) == len(CALLS) + len(SEARCHES)
        reply = invoke_reply("optimisation", op)
        assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"


@pytest.mark.parametrize(("op", "params"), [
    ("run_code", {"code": "import os"}),
    ("save_model", {"name": "m"}),
    ("solve_lp", {"code": "print(1)"}),
    ("solve_lp", {"variables": {"x": {}}, "objective": "3*x"}),
    ("solve_lp", {"variables": {"x": {"lb": "inf"}}}),
    ("solve_lp", {"variables": {"x": {"lb": 0, "kind": "real"}}}),
    ("solve_lp", {"variables": {"x": {}}, "time_limit": 0}),
    ("solve_routing", {"distances": DISTANCES, "local_search": "simulated_annealing"}),
    ("solve_assignment", {"costs": [[1.5, 2], [3, 4]]}),
    ("solve_max_flow", {"arcs": [{"from": "s", "to": "t", "capacity": -1}], "source": "s", "sink": "t"}),
])
def test_unknown_ops_unknown_fields_and_bad_parameters_are_refused(op: str, params: dict) -> None:
    result = get_registry().invoke("optimisation", op, params, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert str(result.failure()).startswith("[G6_E_INPUT_VALIDATION] [OPTIMISATION_ERROR]")
    reply = invoke_reply("optimisation", op, json.dumps(params))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and check_envelope(reply) == []


def test_no_result_can_be_verified() -> None:
    with pytest.raises(ValidationError):
        OptimResult(operation="solve_lp", completion_state="verified")


_FORBIDDEN_MODULES = {"subprocess", "os", "socket", "urllib", "http", "shutil", "multiprocessing", "ctypes", "asyncio",
                      "requests", "httpx", "pickle", "marshal", "g6.core.sandbox", "g6.core.llm", "g6.research"}
_FORBIDDEN_CALLS = {"exec", "eval", "compile", "__import__", "open", "breakpoint"}


def test_the_package_runs_no_caller_code_writes_no_file_and_reaches_no_network() -> None:
    """Inputs are declarative data: linear terms as {variable: coefficient}, never a formula string to evaluate. The
    earlier version's run_code op executed caller Python; nothing here can."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found += [f"{path.name}: {word}" for word in ("exec(", "eval(", "subprocess", "os.system") if word in text]
        for node in ast.walk(ast.parse(text)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            found += [f"{path.name}: import {n}" for n in names
                      if any(n == m or n.startswith(m + ".") for m in _FORBIDDEN_MODULES)]
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                found.append(f"{path.name}:{node.lineno}: {node.func.id}()")
    assert found == []


def test_every_file_is_apache_2() -> None:
    for path in sorted([*SRC.rglob("*.py"), *Path(__file__).parent.glob("*.py")]):
        assert path.read_text(encoding="utf-8").startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name


def test_importing_the_package_loads_no_ortools_and_raises_no_deprecation(tmp_path) -> None:
    subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", "import g6.optimisation"],
                   cwd=tmp_path, check=True)
    probe = ("import sys, g6.optimisation\n"
             "assert not [m for m in sys.modules if m.split('.')[0] == 'ortools'], 'importing g6.optimisation loaded "
             "ortools'\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
    without = ("import sys\nsys.modules['ortools'] = None\nimport g6.optimisation as o\n"
               "r = o.solve_lp({'x': {}}, objective={'x': 1})\n"
               "assert r.code == 'G6_E_OPTIM_SOLVER_UNAVAILABLE' and r.completion_state == 'blocked-escalated'\n")
    subprocess.run([sys.executable, "-c", without], cwd=tmp_path, check=True)


@needs_ortools
def test_solving_with_deprecation_warnings_as_errors_does_not_crash(tmp_path) -> None:
    """OR-Tools' SWIG modules (the routing library) warn while their C extension loads on Python 3.12; with that
    warning raised as an error, the interpreter died with an access violation. The package loads them so it cannot."""
    script = ("import g6.optimisation as o\n"
              f"r = o.solve_routing({DISTANCES!r}, time_limit=1)\n"
              "assert r.completion_state == 'qualified-draft', (r.code, r.reason)\n"
              "l = o.solve_lp({'x': {'ub': 2}}, objective={'x': 1}, sense='maximise')\n"
              "assert l.completion_state == 'qualified-draft' and l.objective == 2, (l.code, l.reason)\n")
    done = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", script], cwd=tmp_path,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
