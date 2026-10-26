# SPDX-License-Identifier: Apache-2.0
"""Without z3-solver: importing g6.formal works and loads no z3, every Z3 check is an honest refusal, and the
pure-Python path still answers. The import is blocked in-process (``sys.modules["z3"] = None``), so these run whether
or not z3 is installed."""
from __future__ import annotations

import subprocess
import sys

import pytest

from g6.formal import (
    ProofArtifact,
    SolverCheck,
    check_entailment,
    check_sat,
    check_validity,
    find_counterexample,
    recheck,
    solve,
    solver_support,
)
from g6.formal.envelope import SOLVER_UNAVAILABLE
from g6.invariants import check_envelope


@pytest.fixture
def no_z3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError


def test_importing_g6_formal_loads_no_z3_and_works_without_it(tmp_path) -> None:
    script = (
        "import sys\n"
        "sys.modules['z3'] = None\n"
        "import g6.formal as f\n"
        "assert f.solve({'solver': 'propositional', 'formula': 'A & ~B'}).satisfiable is True\n"
        "assert f.check_sat('(> x 0)', {'x': 'Int'}).code == 'G6_E_FORMAL_SOLVER_UNAVAILABLE'\n"
    )
    subprocess.run([sys.executable, "-c", script], cwd=tmp_path, check=True)
    probe = "import sys, g6.formal\nassert 'z3' not in sys.modules, 'importing g6.formal imported z3'\n"
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)


@pytest.mark.parametrize("call", [
    lambda: check_sat("(> x 0)", {"x": "Int"}),
    lambda: check_validity(["(> x 1)"], "(> x 0)", sorts={"x": "Int"}),
    lambda: check_entailment("(> x 1)", "(> x 0)", sorts={"x": "Int"}),
    lambda: find_counterexample("(> x 0)", sorts={"x": "Int"}),
    lambda: solve({"formula": "(> x 0)", "variables": {"x": "Int"}}),
])
def test_without_z3_every_check_is_blocked_unavailable(no_z3, call) -> None:
    result = call()
    assert result.code == SOLVER_UNAVAILABLE and result.completion_state == "blocked-escalated"
    assert "g6[formal]" in result.reason and result.artifact is None
    assert result.warning_card["code"] == SOLVER_UNAVAILABLE and check_envelope(result.model_dump()) == []


def test_without_z3_solver_support_says_so(no_z3) -> None:
    rows = {row["backend"]: row for row in solver_support()}
    assert rows["z3"]["available"] is False and rows["z3"]["version"] == "" and "not importable" in rows["z3"]["reason"]
    assert rows["propositional"]["available"] is True


def test_without_z3_the_propositional_path_still_answers(no_z3) -> None:
    """Survival control: the missing extra blocks only the Z3 checks."""
    result = solve({"solver": "propositional", "formula": "(A -> B) & A", "request_id": "q"})
    assert result.satisfiable is True and result.model["B"] is True
    assert result.completion_state == "qualified-draft" and result.request_id == "q"


def test_without_z3_recheck_fails_closed(no_z3) -> None:
    query = "(declare-const x Int)\n(assert (> x 0))\n(check-sat)"
    artifact = ProofArtifact(operation="check_sat", question="satisfiable", statement="(> x 0)", sorts={"x": "Int"},
                             assumptions=("(> x 0)",), solver="z3", solver_version="4.16.0",
                             checks=(SolverCheck(query=query, result="sat"),))
    ok, why = recheck(artifact)
    assert ok is False and "not importable" in why
