# SPDX-License-Identifier: Apache-2.0
"""Regression tests for audit 016 (navigator -> g6.navigator): one op's card, and the one inventory behind it.

The archive cannot run here; each finding test says what it did, from the audit's source reading.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from g6.core.registry import get_registry
from g6.navigator import CARD_CHARS, card, check_plan, contract_for, ops_of, purpose
from g6.navigator import plan as nav_plan
from g6.workforce.capabilities import PARAMS_DIR, load_contracts


def _cleared(component: str, op: str) -> str:
    return "A4"


def test_card_carries_the_contract_and_what_the_component_declares() -> None:
    found = card("workforce", "run_tests")
    shipped = load_contracts()[("workforce", "run_tests")]
    assert found["required"] == {"repo": "str", "tests": "list"} and found["side_effect"] == "read"
    assert found["example"] == shipped.probe_params and found["contract_sha256"] == shipped.sha256
    assert found["model_free"] is True and found["cost"] == "unknown"  # the contract declares no cost ceiling
    assert found["authority"] == "A4"  # the library's own table, bound to this contract's digest
    assert found["purpose"] == ""  # the workforce has no block, so no help line to quote
    assert found["maturity"] == "beta"  # what its package declares, read rather than assumed


def test_card_for_a_block_op_with_no_contract_says_so() -> None:
    found = card("grounding", "check_claim")
    assert found["contract"] is None and "no parameter contract" in found["note"]
    assert found["purpose"].startswith("check_claim(")  # the component's own help line, not an inferred one
    assert found["maturity"] == "beta" and found["authority"] is None


def test_card_is_none_for_an_op_nothing_declares() -> None:
    assert card("grounding", "search") is None
    assert card("no_such_component", "anything") is None
    assert contract_for("grounding", "check_claim") is None


def test_purpose_is_never_inferred() -> None:
    assert purpose("action_logic", "detect_conflicts").startswith("detect_conflicts(")
    assert purpose("workforce", "run_tests") == ""
    assert purpose("grounding", "no_such_op") == ""


def test_card_fits_its_budget(tmp_path: Path) -> None:
    body = {"component": "wide", "op": "op", "required": {}, "optional": {f"option_{i:03d}": "str" for i in range(150)},
            "probe_params": {"option_000": "x" * 900}, "expect_field": "ok", "side_effect": "read",
            "check": "input_model"}
    (tmp_path / "wide.op.json").write_text(json.dumps(body), encoding="utf-8")
    found = card("wide", "op", contracts=load_contracts(tmp_path))
    assert len(json.dumps(found)) <= CARD_CHARS
    assert found["example"] is None and "optional_note" in found and found["optional"]


# --- NAV-014: introspection hard-coded maturity "production" -----------------------------------------------------


def test_nav014_the_card_reads_the_maturity_the_component_declares(monkeypatch: pytest.MonkeyPatch) -> None:
    # Archive: nav_inspect_agent set card["maturity"] = "production" whatever the block declared
    # (introspection_handlers.py:93).
    monkeypatch.setattr(get_registry().get_class("grounding"), "__g6_maturity__", "stubbed")
    assert card("grounding", "check_claim")["maturity"] == "stubbed"


# --- NAV-007: two inventories and two rankers answered the same question differently ----------------------------


@pytest.mark.parametrize("component", ["grounding", "action_logic", "workforce", "no_such_component"])
def test_nav007_card_ops_of_and_check_plan_read_one_inventory(component: str) -> None:
    # Archive: product nav_discover read 5,110 contracts while the block's nav_discover / nav_recommend / nav_compose
    # read 6,069 AST-scanned tool names, so the surfaces disagreed.
    listed = ops_of(component)
    for op in [*sorted(listed), "no_such_op"]:
        step = check_plan([{"component": component, "op": op}], authority=_cleared).steps[0]
        assert (card(component, op) is not None) == (op in listed) == step.exists, (component, op)


def test_nav007_survival_ops_of_holds_the_contracted_and_the_listed_ops() -> None:
    assert ops_of("workforce") == {"run_tests", "run_tests_in_place"}
    assert {"check_claim", "check_claims"} <= ops_of("grounding")
    assert nav_plan.listed_ops("grounding") <= ops_of("grounding")


# --- authority binds to the contract in use (the archive's digest binding, kept) ---------------------------------


def test_authority_binds_to_the_digest_of_the_contract_in_use(tmp_path: Path) -> None:
    # Archive: op_catalog.authority read a class only for the exact contract sha (op_catalog.py:279-293). Kept, so a
    # contract changed after the reading cannot inherit its class.
    body = json.loads((PARAMS_DIR / "workforce.run_tests.json").read_text(encoding="utf-8"))
    body["optional"]["note"] = "str"
    (tmp_path / "workforce.run_tests.json").write_text(json.dumps(body), encoding="utf-8")
    changed = load_contracts(tmp_path)
    step = {"component": "workforce", "op": "run_tests", "args": {"repo": "r", "tests": ["tests/t.py::test_t"]}}
    assert card("workforce", "run_tests", contracts=changed)["authority"] is None
    assert [i.code for i in check_plan([step], contracts=changed).issues] == ["G6_E_OP_NOT_CLEARED"]
    # survival: the shipped contract keeps its class
    assert card("workforce", "run_tests")["authority"] == "A4" and check_plan([step]).valid
