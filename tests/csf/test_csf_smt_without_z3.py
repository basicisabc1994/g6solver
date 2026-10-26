# SPDX-License-Identifier: Apache-2.0
"""Without z3-solver: g6.csf and its SMT module import without loading z3, ``check_z3`` is an honest refusal, and the
rest of the package still answers. The import is blocked in-process (``sys.modules["z3"] = None``), so these run
whether or not z3 is installed."""
from __future__ import annotations

import subprocess
import sys

import pytest

from g6.csf import BoundedAgent, ResourceTriple, reachability_bound
from g6.csf import smt
from g6.invariants import check_envelope


@pytest.fixture
def no_z3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "z3", None)  # ``import z3`` now raises ImportError


def _agent():
    rows = [{"from": "s0", "to": "s1", "probability": 1.0}, {"from": "s1", "to": "s1", "probability": 1.0}]
    return BoundedAgent(states=["s0", "s1"], transitions=rows, initial_state="s0", unsafe_states=["s1"],
                        resources=ResourceTriple(N=2))


def test_importing_g6_csf_and_its_smt_module_loads_no_z3(tmp_path) -> None:
    probe = ("import sys\n"
             "import g6.csf, g6.csf.smt\n"
             "assert 'z3' not in sys.modules, 'importing g6.csf imported z3'\n")
    subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, check=True)
    blocked = ("import sys\n"
               "sys.modules['z3'] = None\n"
               "from g6.csf import BoundedAgent, ResourceTriple\n"
               "from g6.csf.smt import check_z3\n"
               "rows = [{'from': 'a', 'to': 'a', 'probability': 1.0}]\n"
               "agent = BoundedAgent(['a'], rows, 'a', [], ResourceTriple(N=1))\n"
               "assert check_z3(agent, 'G(a)').code == 'G6_E_FORMAL_SOLVER_UNAVAILABLE'\n")
    subprocess.run([sys.executable, "-c", blocked], cwd=tmp_path, check=True)


def test_without_z3_check_z3_is_blocked_unavailable(no_z3) -> None:
    result = smt.check_z3(_agent(), "G(!s1)", request_id="q")
    assert result.holds is None and result.code == "G6_E_FORMAL_SOLVER_UNAVAILABLE"
    assert "g6[formal]" in result.reason and result.completion_state == "blocked-escalated"
    envelope = result.to_envelope()
    assert check_envelope(envelope) == [] and envelope["request_id"] == "q"


def test_without_z3_the_encoding_and_the_bound_still_work(no_z3) -> None:
    """Survival control: the missing extra blocks only the solver call."""
    encoding = smt.agent_to_smt(_agent(), "G(!s1)")
    assert encoding.property == "G(!s1)" and encoding.variables["x_2"] == "Int" and encoding.goal.startswith("(not ")
    assert "(= x_0 0)" in encoding.assertions
    assert reachability_bound(_agent(), 0.5).vub == 1.0
