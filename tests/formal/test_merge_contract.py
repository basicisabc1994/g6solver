# SPDX-License-Identifier: Apache-2.0
import pytest

from g6.formal import FormalBlock, FormalInput, check_contract
from g6.formal import contract


def test_contract_checks_every_property_through_existing_block():
    # Removal: contract.py `pieces = [check_validity(...) ...]` removes G6's actual property checks.
    pytest.importorskip("z3")
    out = FormalBlock().infer(FormalInput(op="check_contract", properties=["(> x 0)", "(< x 10)"],
                                        premises=["(= x 4)"], sorts={"x": "Int"})).unwrap()
    assert out["value"] == "T" and out["completion_state"] == "qualified-draft"
    assert len(out["inputs"]["properties"]) == 2
    assert all(p["result"]["artifact"] for p in out["inputs"]["properties"])
    assert any(e["external_solver_ran"] for e in out["evidence"])
    assert "no external solver ran" not in out["warning_card"]["message"]


def test_missing_property_is_refused_by_default():
    # Removal: contract.py `if not properties:` admits an empty contract (FORMAL_VERIFICATION-01).
    out = check_contract([])
    assert out.value == "N" and out.completion_state == "blocked-escalated"
    assert out.code == "G6_E_FORMAL_NO_PROPERTY"


def test_contradictory_premises_are_not_a_proof():
    # Removal: contract.py `if any(p.vacuous for p in pieces):` hides vacuity (FORMAL_VERIFICATION-01/D1).
    pytest.importorskip("z3")
    out = check_contract(["(> x 9)"], premises=["(< x 0)", "(> x 1)"], sorts={"x": "Int"})
    assert out.vacuous and out.value == "N" and out.completion_state == "blocked-escalated"


def test_conflicting_contract_properties_surface_both():
    # Removal: contract.py `value = B` hides jointly inconsistent properties.
    pytest.importorskip("z3")
    out = check_contract(["(> x 4)", "(< x 2)"], sorts={"x": "Int"})
    assert out.value == "B" and out.completion_state == "blocked-escalated"
    assert out.inputs["consistency"]["satisfiable"] is False


def test_false_property_keeps_counterexample():
    # Removal: contract.py `value = value & ...` drops refutation from aggregation.
    pytest.importorskip("z3")
    out = check_contract(["(> x 10)", "(< x 20)"], premises=["(= x 5)"], sorts={"x": "Int"})
    assert out.value == "F" and out.completion_state == "blocked-escalated"
    assert out.inputs["properties"][0]["result"]["counterexample"] == {"x": 5}


@pytest.mark.parametrize("dependency", ["check_validity", "check_sat"])
def test_solver_dependency_failure_blocks_contract(monkeypatch, dependency):
    # Removal: contract.py `except Exception as exc:` loses the fail-closed solver refusal.
    pytest.importorskip("z3")
    def broken(*a, **kw):
        raise RuntimeError("solver down")
    monkeypatch.setattr(contract, dependency, broken)
    out = check_contract(["(> x 0)", "(< x 10)"], premises=["(= x 5)"], sorts={"x": "Int"})
    assert out.completion_state == "blocked-escalated" and out.code == "G6_E_FORMAL_CONTRACT_FAILED"


def test_a_property_that_holds_without_the_premises_is_not_a_contract_pass():
    # Removal: contract.py `alone = check_validity((), prop, ...)` lets a tautology pass as T under the premises.
    pytest.importorskip("z3")
    out = check_contract(["(=> (> x 5) (> x 5))"], premises=["(= x 4)"], sorts={"x": "Int"})
    assert out.value == "N" and out.completion_state == "blocked-escalated"
    assert out.code == "G6_E_FORMAL_VACUOUS_PROPERTY" and out.vacuous
    assert out.inputs["properties"][0]["without_premises"]["valid"] is True


def test_the_aggregate_evidence_agrees_that_z3_ran():
    # Removal: contract.py `"external_solver_ran": z3_ran` leaves the aggregate entry saying no external solver ran.
    pytest.importorskip("z3")
    out = check_contract(["(> x 0)", "(< x 10)"], premises=["(= x 4)"], sorts={"x": "Int"})
    assert out.value == "T" and {e["external_solver_ran"] for e in out.evidence} == {True}


def test_the_contract_has_one_deadline_for_the_whole_call(monkeypatch):
    # Removal: contract.py `timeout=left(2)` (half the time that remains) gives every property the full timeout.
    from g6.formal.schema import FormalResult, ValidityResult
    clock, given = [0.0], []
    def check(premises, prop, *, sorts, timeout):
        given.append(2 * timeout)
        clock[0] += 2 * timeout  # check_validity runs up to two queries, each with the timeout it is given
        return ValidityResult(operation="check_validity", backend="z3", valid=False, counterexample={"x": 0})
    def sat(formula, sorts, *, timeout):
        given.append(timeout)
        clock[0] += timeout
        return FormalResult(operation="check_sat", backend="z3", satisfiable=True)
    monkeypatch.setattr(contract, "monotonic", lambda: clock[0], raising=False)
    monkeypatch.setattr(contract, "check_validity", check)
    monkeypatch.setattr(contract, "check_sat", sat)
    out = check_contract(["(> x 0)", "(< x 10)", "(> x 1)", "(< x 9)"], sorts={"x": "Int"}, timeout=10)
    assert sum(given) <= 10 + 1e-9 and clock[0] <= 10 + 1e-9
    assert out.value == "N" and out.code == "G6_E_FORMAL_SOLVER_TIMEOUT"
    assert out.completion_state == "blocked-escalated"


def test_no_solver_and_caller_truth_claim_cannot_pass(monkeypatch):
    # Removal: contract.py `if failed:` would use an unavailable solver as a property pass.
    import sys
    from pydantic import ValidationError
    monkeypatch.setitem(sys.modules, "z3", None)
    assert check_contract(["true"]).completion_state == "blocked-escalated"
    with pytest.raises(ValidationError):
        FormalInput(op="check_contract", properties=[{"formula": "false", "verified": True}])
