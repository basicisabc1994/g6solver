# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The research ``MissionIR`` and its validator (integrated audit ENG-01, 2026-10-11). Research code only, default OFF.

A mission graph is checked before anything runs. What must hold, each with a control that fails for the stated reason:
- the flag is off unless the environment says a plain yes, and with it off nothing is built or judged;
- an edge whose provided and required types differ, a requirement nothing provides, and a name two nodes provide are
  each found, and a cycle is found;
- a node whose source operation is missing, unknown to the catalogue or at class A0 has no authority; a node that
  declares a different effect from the catalogue's, or whose operation is below the class its effect needs, is found;
- a node that weakens the mission's standard (another hash, or fewer predicates) is found;
- external data egress needs an approval for that node, and sensitive data needs the wider one;
- a budget over the mission's, an unknown cost on a charged mission, and a charged mission with no budget are found;
- a required role nothing fills, and a check made by the component that made the output, are found.
The pure modules import nothing from the product and no product module imports them.
"""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from g6.research.mission_bridge import (
    FLAG,
    IRRejected,
    MissionIR,
    OpFact,
    ResearchDisabled,
    research_enabled,
    validate,
)

import g6 as _g6

SRC = Path(_g6.__file__).resolve().parent
BRIDGE = SRC / "research" / "mission_bridge"
SHA = "ab" * 32
OTHER_SHA = "cd" * 32

CATALOG = {
    "alpha.ask": OpFact(effect="read", authority="A1"),
    "beta.fetch": OpFact(effect="read", authority="A1"),
    "gamma.write": OpFact(effect="local_write", authority="A1"),
    "delta.check": OpFact(effect="read", authority="A4"),
    "eps.send": OpFact(effect="external", authority="A2"),
    "zeta.dead": OpFact(effect="read", authority="A0"),
    "eta.weak_send": OpFact(effect="external", authority="A1"),
}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv(FLAG, "1")


def node(node_id, op, requires=None, provides=None, **over):
    body = {"id": node_id, "role": over.pop("role", node_id), "source_operation": op,
            "declared_effect": CATALOG[op].effect if op in CATALOG else "read",
            "requires": requires or {}, "provides": provides or {}, "data_class": "public", "egress": "none",
            "budget_cents": 10}
    body.update(over)
    return body


def mission(**over):
    body = {
        "mission_id": "m-1", "domain": "synthetic", "high_stakes": False,
        "inputs": {"question": "text"},
        "standard": {"standard_id": "std-1", "standard_sha256": SHA, "predicates": ["p-a", "p-b"]},
        "nodes": [
            node("scope", "alpha.ask", {"question": "text"}, {"scope": "text"}),
            node("gather", "beta.fetch", {"scope": "text"}, {"passages": "list"}),
            node("check", "delta.check", {"passages": "list"}, {"verdict": "text"}, verifies="gather"),
        ],
        "budget_cents": 100, "charged": False, "approvals": [], "required_roles": ["scope", "check"],
    }
    body.update(over)
    return body


def codes(raw, catalog=None):
    return sorted({f.code for f in validate(MissionIR.from_dict(raw), CATALOG if catalog is None else catalog)})


def with_node(raw, node_id, **changes):
    out = copy.deepcopy(raw)
    for item in out["nodes"]:
        if item["id"] == node_id:
            item.update(changes)
    return out


# --- the flag ---------------------------------------------------------------------------------------------------------


def test_the_flag_is_off_unless_the_environment_says_a_plain_yes(monkeypatch):
    for value in ("", "0", "no", "off", "maybe", "yess"):
        monkeypatch.setenv(FLAG, value)
        assert research_enabled() is False, value
    for value in ("1", "true", "YES", " on "):
        monkeypatch.setenv(FLAG, value)
        assert research_enabled() is True, value


def test_with_the_flag_off_nothing_is_built_or_judged(monkeypatch):
    built = MissionIR.from_dict(mission())
    monkeypatch.delenv(FLAG, raising=False)
    with pytest.raises(ResearchDisabled):
        MissionIR.from_dict(mission())
    with pytest.raises(ResearchDisabled):
        validate(built, CATALOG)


# --- the control: a sound mission has nothing to find ----------------------------------------------------------------


def test_a_sound_mission_has_no_findings_and_a_stable_digest():
    first = MissionIR.from_dict(mission())
    assert validate(first, CATALOG) == []
    again = MissionIR.from_dict(copy.deepcopy(mission()))
    assert first.sha256 == again.sha256 and len(first.sha256) == 64
    changed = MissionIR.from_dict(mission(budget_cents=101))
    assert changed.sha256 != first.sha256


# --- the document is read strictly ------------------------------------------------------------------------------------


@pytest.mark.parametrize("change", [
    lambda m: m.update(surprise=1),
    lambda m: m["nodes"][0].update(surprise=1),
    lambda m: m["standard"].update(surprise=1),
    lambda m: m["nodes"][0].update(source_operation=""),
    lambda m: m["nodes"][0].pop("source_operation"),
    lambda m: m["nodes"][0].update(declared_effect="teleport"),
    lambda m: m["nodes"][0].update(budget_cents=True),
    lambda m: m["nodes"][0].update(budget_cents=-1),
    lambda m: m["nodes"][0].update(data_class="secret"),
    lambda m: m["nodes"][0].update(egress="yes"),
    lambda m: m["nodes"].append(copy.deepcopy(m["nodes"][0])),
    lambda m: m.update(nodes=[]),
    lambda m: m["standard"].update(standard_sha256="not-a-digest"),
    lambda m: m["standard"].update(predicates=[]),
    lambda m: m.update(approvals=[{"node": "scope", "scope": "everything"}]),
    lambda m: m.update(high_stakes="yes"),
])
def test_a_malformed_document_is_refused_not_repaired(change):
    raw = mission()
    change(raw)
    with pytest.raises(IRRejected):
        MissionIR.from_dict(raw)


def test_every_problem_in_a_document_is_reported_together():
    raw = mission()
    raw["nodes"][0]["declared_effect"] = "teleport"
    raw["nodes"][1]["data_class"] = "secret"
    with pytest.raises(IRRejected) as caught:
        MissionIR.from_dict(raw)
    assert len(caught.value.problems) >= 2, caught.value.problems


# --- the edges --------------------------------------------------------------------------------------------------------


def test_an_edge_whose_types_differ_is_found():
    raw = with_node(mission(), "scope", provides={"scope": "number"})
    assert codes(raw) == ["edge_type_mismatch"]


def test_a_requirement_nothing_provides_is_found():
    raw = with_node(mission(), "gather", requires={"scope": "text", "ghost": "text"})
    assert codes(raw) == ["missing_prerequisite"]


def test_a_requirement_only_a_later_node_provides_is_a_cycle():
    raw = with_node(mission(), "scope", requires={"question": "text", "verdict": "text"})
    assert "cycle" in codes(raw)


def test_a_name_two_nodes_provide_is_found():
    raw = with_node(mission(), "check", provides={"verdict": "text", "scope": "text"})
    assert "duplicate_provider" in codes(raw)


def test_a_mission_input_is_a_provider_of_its_own_type():
    raw = mission(inputs={"question": "number"})
    assert codes(raw) == ["edge_type_mismatch"]


# --- authority --------------------------------------------------------------------------------------------------------


def test_a_node_whose_operation_the_catalogue_does_not_know_has_no_authority():
    raw = with_node(mission(), "gather", source_operation="nowhere.op")
    assert codes(raw) == ["missing_source_authority"]


def test_a_node_whose_operation_is_class_a0_has_no_authority():
    raw = with_node(mission(), "gather", source_operation="zeta.dead")
    assert codes(raw) == ["missing_source_authority"]


def test_a_node_that_declares_another_effect_than_the_catalogue_is_found():
    raw = with_node(mission(), "gather", declared_effect="read", source_operation="gamma.write")
    assert codes(raw) == ["effect_misdeclared"]


def test_an_operation_below_the_class_its_effect_needs_is_found():
    raw = with_node(mission(), "gather", source_operation="eta.weak_send", declared_effect="external", egress="declared")
    raw["approvals"] = [{"node": "gather", "scope": "egress"}]
    assert codes(raw) == ["authority_too_low"]


def test_the_effect_table_is_the_products_own():
    """The bridge keeps its own copy of the workforce's effect-to-authority table (it imports no product code); the
    copy must not drift (re-ported from the archive once ``g6.workforce`` landed)."""
    from g6.research.mission_bridge import checks
    from g6.workforce.plans import EFFECT_AUTHORITY

    assert checks.EFFECT_AUTHORITY == EFFECT_AUTHORITY


# --- the standard -----------------------------------------------------------------------------------------------------


def test_a_node_that_drops_a_mission_predicate_lowers_the_standard():
    raw = with_node(mission(), "gather", standard={"standard_sha256": SHA, "predicates": ["p-a"]})
    assert codes(raw) == ["standard_decrease"]


def test_a_node_that_cites_another_standard_hash_lowers_the_standard():
    raw = with_node(mission(), "gather", standard={"standard_sha256": OTHER_SHA, "predicates": ["p-a", "p-b"]})
    assert codes(raw) == ["standard_decrease"]


def test_a_node_may_add_a_predicate_without_lowering_the_standard():
    raw = with_node(mission(), "gather", standard={"standard_sha256": SHA, "predicates": ["p-a", "p-b", "p-c"]})
    assert codes(raw) == []


# --- egress -----------------------------------------------------------------------------------------------------------


def _sending(raw, **changes):
    return with_node(raw, "gather", source_operation="eps.send", declared_effect="external", egress="declared", **changes)


def test_external_data_egress_without_an_approval_for_that_node_is_found():
    assert codes(_sending(mission())) == ["unapproved_egress"]


def test_an_approval_for_another_node_does_not_cover_this_one():
    raw = _sending(mission(), )
    raw["approvals"] = [{"node": "scope", "scope": "egress"}]
    assert codes(raw) == ["unapproved_egress"]


def test_an_approval_for_that_node_allows_public_egress():
    raw = _sending(mission())
    raw["approvals"] = [{"node": "gather", "scope": "egress"}]
    assert codes(raw) == []


def test_sensitive_data_needs_the_wider_approval():
    raw = _sending(mission(), data_class="sensitive")
    raw["approvals"] = [{"node": "gather", "scope": "egress"}]
    assert codes(raw) == ["unapproved_egress"]
    raw["approvals"] = [{"node": "gather", "scope": "egress_sensitive"}]
    assert codes(raw) == []


def test_an_external_effect_is_egress_even_when_the_node_says_none():
    raw = with_node(mission(), "gather", source_operation="eps.send", declared_effect="external", egress="none")
    assert codes(raw) == ["unapproved_egress"]


def test_a_node_that_hides_an_external_op_behind_a_read_declaration_still_needs_the_approval():
    raw = with_node(mission(), "gather", source_operation="eps.send", declared_effect="read", egress="none")
    assert codes(raw) == ["effect_misdeclared", "unapproved_egress"]

# --- gates ------------------------------------------------------------------------------------------------------------


def gate(node_id="approval", **over):
    body = {"id": node_id, "role": "approval", "gate": True, "source_operation": "", "declared_effect": "read",
            "requires": {}, "provides": {"approved": "approval"}, "budget_cents": 0}
    body.update(over)
    return body


def test_a_gate_is_a_person_s_decision_and_names_no_operation():
    raw = mission()
    raw["nodes"].append(gate())
    assert codes(raw) == []
    named = mission()
    named["nodes"].append(gate(source_operation="alpha.ask"))
    with pytest.raises(IRRejected):
        MissionIR.from_dict(named)


def test_a_gate_is_not_judged_against_the_catalogue_and_does_not_check_anything():
    raw = mission()
    raw["nodes"].append(gate(verifies="gather"))
    assert codes(raw) == []


def test_two_gates_have_no_shared_component_to_trip_the_independence_rule():
    raw = mission()
    raw["nodes"] += [gate("first"), gate("second", verifies="first", provides={"second_ok": "approval"})]
    assert codes(raw) == []

def test_a_node_that_is_not_a_gate_still_names_an_operation():
    raw = mission()
    raw["nodes"][0]["gate"] = False
    raw["nodes"][0]["source_operation"] = ""
    with pytest.raises(IRRejected):
        MissionIR.from_dict(raw)

# --- budget -----------------------------------------------------------------------------------------------------------


def test_a_budget_over_the_missions_is_found():
    raw = mission(budget_cents=25)
    assert codes(raw) == ["budget_exceeded"]


def test_an_unknown_cost_on_a_charged_mission_is_found_and_on_another_is_not():
    raw = with_node(mission(), "gather", budget_cents=None)
    assert codes(raw) == []
    raw["charged"] = True
    assert codes(raw) == ["budget_unknown"]


def test_a_charged_mission_with_no_budget_is_found():
    raw = mission(budget_cents=None, charged=True)
    assert "budget_unknown" in codes(raw)


# --- roles and independence -------------------------------------------------------------------------------------------


def test_a_required_role_nothing_fills_is_found():
    raw = mission(required_roles=["scope", "check", "adversarial_review"])
    assert codes(raw) == ["missing_role"]


def test_a_check_made_by_the_component_that_made_the_output_is_found():
    raw = with_node(mission(), "check", source_operation="beta.fetch", declared_effect="read")
    assert codes(raw) == ["self_verification"]


def test_a_check_must_name_a_node_that_exists():
    raw = with_node(mission(), "check", verifies="nobody")
    assert codes(raw) == ["missing_prerequisite"]


def test_every_finding_is_reported_not_only_the_first():
    raw = with_node(mission(budget_cents=25), "gather", requires={"scope": "text", "ghost": "text"})
    assert codes(raw) == ["budget_exceeded", "missing_prerequisite"]


# --- isolation --------------------------------------------------------------------------------------------------------

#: Only the lowering module reaches the product (it must, to run the product's own plan checks); nothing else may.
PURE = ("__init__.py", "flag.py", "ir.py", "checks.py", "replay.py", "fixtures.py", "bundle.py", "csir/__init__.py",
        "csir/judgement.py", "csir/standard.py", "csir/solvability.py")  # rvc/* join when ported


#: The ECHA kernel is research code of the same kind (standard library only, outside the product); the bundle builds on it.
SIBLING = "g6.research.echa"


def _outside_the_standard_library(paths) -> set[str]:
    import sys

    found: set[str] = set()
    for path in paths:
        for item in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in item.names] if isinstance(item, ast.Import)
                     else [item.module or ""] if isinstance(item, ast.ImportFrom) and not item.level else [])
            for name in names:
                top = name.split(".")[0]
                if (top not in sys.stdlib_module_names and top != "__future__" and not name.startswith(SIBLING)
                        and not name.startswith("g6.research.mission_bridge")):
                    found.add(top)
    return found

def test_the_pure_modules_import_nothing_outside_the_standard_library(tmp_path):
    assert _outside_the_standard_library(BRIDGE / name for name in PURE) == set()
    (tmp_path / "bad.py").write_text("from g6.venture import harness\nimport requests\n", encoding="utf-8")
    assert _outside_the_standard_library([tmp_path / "bad.py"]) == {"g6", "requests"}


def test_no_product_module_reaches_the_bridge_or_its_flag(tmp_path):
    def reaching(directory: Path) -> list[str]:
        return [str(p) for p in directory.rglob("*.py")
                if "mission_bridge" in (t := p.read_text(encoding="utf-8", errors="ignore")) or FLAG in t]

    for folder in SRC.iterdir():
        if folder.is_dir() and folder.name not in {"research", "__pycache__"}:
            assert reaching(folder) == [], folder.name
    (tmp_path / "wired.py").write_text("from g6.research.mission_bridge import validate\n", encoding="utf-8")
    assert len(reaching(tmp_path)) == 1
