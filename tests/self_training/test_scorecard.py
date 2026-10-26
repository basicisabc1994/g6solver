# SPDX-License-Identifier: Apache-2.0
"""Behaviour of the shape rubrics, the scorecard store and the diagnosis floor. Offline; every file under ``tmp_path``."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from g6.self_training import (
    ComponentRubric,
    PromotionRefused,
    RubricAssertion,
    RubricProvenance,
    RubricRegistry,
    ScorecardStore,
    audit_output,
    classify_tier,
    diagnose,
    diagnose_audit,
    evaluate_assertion,
    evaluate_rubric,
)

REVIEWED = RubricProvenance(reviewed_by="a reviewer")


def _rubric(*assertions: RubricAssertion, reviewed: bool = True) -> ComponentRubric:
    return ComponentRubric(component="c", op="infer", assertions=list(assertions),
                           provenance=REVIEWED if reviewed else RubricProvenance())


# --- assertions ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("check", "expected", "actual", "want"), [
    ("not_empty", None, "hello", True), ("not_empty", None, "", False), ("not_empty", None, 0, False),
    ("not_empty", None, None, False),
    ("gt", 5, 10, True), ("gt", 5, 1, False), ("gte", 5, 5, True), ("lt", 5, 1, True), ("lte", 5, 5, True),
    ("gt", 0, float("nan"), False), ("lt", 0, float("nan"), False),
    ("eq", "a", "a", True), ("eq", "a", "b", False),
    ("contains", "abc", "xx abc yy", True), ("contains", "abc", "no", False),
    ("type_is", "str", "x", True), ("type_is", "list", [], True), ("type_is", "str", 5, False),
    ("length_gte", 3, [1, 2, 3], True), ("length_gte", 3, [1], False),
    ("in_set", ["a", "b"], "a", True), ("in_set", ["a", "b"], "z", False), ("in_set", "solo", "solo", True),
])
def test_each_check(check, expected, actual, want) -> None:
    assert evaluate_assertion(RubricAssertion(field="v", check=check, expected=expected), {"v": actual}).passed is want


def test_dot_paths_reach_into_mappings_and_attributes() -> None:
    a = RubricAssertion(field="x.y", check="eq", expected=42)
    assert evaluate_assertion(a, {"x": {"y": 42}}).passed
    assert evaluate_assertion(a, SimpleNamespace(x=SimpleNamespace(y=42))).passed


def test_a_missing_field_fails_with_a_reason() -> None:
    assert evaluate_assertion(RubricAssertion(field="v", check="exists"), {}).actual_value == "<missing>"
    result = evaluate_assertion(RubricAssertion(field="v", check="gt", expected=0), {"x": 1})
    assert not result.passed and "not found" in result.error


@pytest.mark.parametrize("assertion", [
    RubricAssertion(field="v", check="gt", expected=0),            # float("text") raises
    RubricAssertion(field="v", check="type_is", expected="number"),  # an unknown type name (the archive checked str)
])
def test_an_assertion_that_cannot_be_evaluated_fails(assertion) -> None:
    result = evaluate_assertion(assertion, {"v": "text"})
    assert not result.passed and result.error


def test_weights_must_be_finite_and_not_negative() -> None:
    for bad in (-1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            RubricAssertion(field="x", weight=bad)


# --- rubrics and tiers --------------------------------------------------------------------------------------------

def test_weighted_score() -> None:
    scored = evaluate_rubric(_rubric(RubricAssertion(field="x", weight=3), RubricAssertion(field="y", weight=1)),
                             {"x": 1})
    assert scored.evaluable and scored.score == 0.75 and scored.kind == "shape" and len(scored.results) == 2


@pytest.mark.parametrize(("error", "tier"), [("Component not found", 0), ("No AIBlock subclass", 0),
                                             ("Cannot import module foo", 1), ("some runtime error", 2), ("", 2)])
def test_failed_invocations(error, tier) -> None:
    assert classify_tier(invoke_ok=False, invoke_error=error) == tier
    assert audit_output("c", invoke_ok=False, invoke_error=error).tier == tier


@pytest.mark.parametrize(("share", "reviewed", "tier"), [(1.0, True, 5), (0.75, True, 4), (0.5, True, 4),
                                                         (0.25, True, 3), (0.0, True, 2), (1.0, False, 3),
                                                         (0.5, False, 3), (0.0, False, 2)])
def test_tiers_from_shape_scores(share, reviewed, tier) -> None:
    passing = int(share * 4)
    output = {f"f{i}": 1 for i in range(passing)}
    rubric = _rubric(*[RubricAssertion(field=f"f{i}") for i in range(4)], reviewed=reviewed)
    audit = audit_output("c", output=output, rubric=rubric)
    assert audit.tier == tier and audit.score == pytest.approx(passing / 4)
    assert audit.rubric_reviewed is reviewed and audit.passed_threshold is (tier >= 5)


def test_no_audit_earns_t6() -> None:
    perfect = audit_output("c", output={"x": 1}, rubric=_rubric(RubricAssertion(field="x")))
    assert perfect.tier == 5


# --- the rubric registry ------------------------------------------------------------------------------------------

def test_registry_loads_objects_and_lists_and_names_the_files_it_skipped(tmp_path) -> None:
    item = {"component": "c1", "op": "infer", "assertions": [{"field": "x"}]}
    (tmp_path / "one.json").write_text(json.dumps(item), encoding="utf-8")
    (tmp_path / "many.json").write_text(json.dumps([item, item]), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    reg = RubricRegistry()
    assert reg.load_from_dir(tmp_path) == 3 and reg.list_components() == ["c1"]
    assert [e.split(":")[0] for e in reg.load_errors] == ["broken.json"]
    assert reg.load_from_dir(tmp_path / "missing") == 0


def test_registry_filters_by_op_and_round_trips() -> None:
    reg = RubricRegistry()
    reg.register(ComponentRubric(component="c", op="a"))
    reg.register(ComponentRubric(component="c", op="b"))
    assert [r.op for r in reg.get("c", op="a")] == ["a"] and len(reg.get("c")) == 2
    assert [r["op"] for r in reg.to_dict()["c"]] == ["a", "b"]


# --- the store ----------------------------------------------------------------------------------------------------

def _passing_audit(component: str = "c"):
    return audit_output(component, output={"x": 1}, rubric=_rubric(RubricAssertion(field="x")))


def test_the_store_survives_a_reopen(tmp_path) -> None:
    store = ScorecardStore(tmp_path)
    store.append_audit(_passing_audit())
    store.flag("d", "under review")
    again = ScorecardStore(tmp_path)
    assert again.get_score("c") == store.get_score("c") and again.get_score("d").flagged
    card = again.get_scorecard()
    assert (card.total_components, card.above_threshold, card.below_threshold, card.flagged) == (2, 1, 0, 1)


def test_an_unreadable_scorecard_raises_instead_of_dropping_its_flags(tmp_path) -> None:
    (tmp_path / "scorecard.json").write_text("{not valid", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot read the scorecard"):
        ScorecardStore(tmp_path)


def test_audit_history_filters_and_skips_lines_that_do_not_parse(tmp_path) -> None:
    store = ScorecardStore(tmp_path)
    store.append_audit(_passing_audit("alpha"))
    store.append_audit(_passing_audit("beta"))
    with open(tmp_path / "audit_log.jsonl", "a", encoding="utf-8") as log:
        log.write("\nnot-json\n")
    assert len(store.audit_history()) == 2
    assert [e["component"] for e in store.audit_history("alpha")] == ["alpha"]
    assert ScorecardStore(tmp_path / "empty").audit_history() == []


def test_a_low_tier_promotion_is_recorded_as_a_promotion(tmp_path) -> None:
    store = ScorecardStore(tmp_path)
    score = store.promote("new", 4)
    assert (score.current_tier, score.tier_source, score.tier_name) == (4, "promotion", "shape-partial")


@pytest.mark.parametrize("tier", [5, 6])
def test_a_high_tier_promotion_needs_a_passing_reviewed_audit(tmp_path, tier) -> None:
    store = ScorecardStore(tmp_path)
    with pytest.raises(PromotionRefused, match="no audit|none on record"):
        store.promote("c", tier)
    store.append_audit(audit_output("c", invoke_ok=False, invoke_error="boom"))
    with pytest.raises(PromotionRefused, match="did not"):
        store.promote("c", tier)
    store.append_audit(_passing_audit())
    assert store.promote("c", tier).current_tier == tier


def test_a_promotion_stands_until_the_next_audit(tmp_path) -> None:
    store = ScorecardStore(tmp_path)
    store.append_audit(_passing_audit())
    store.promote("c", 6)
    assert store.append_audit(_passing_audit()).current_tier == 5


def test_promote_rejects_a_tier_out_of_range(tmp_path) -> None:
    with pytest.raises(ValueError):
        ScorecardStore(tmp_path).promote("c", 7)


def test_a_flagged_component_can_be_lowered_but_not_raised(tmp_path) -> None:
    store = ScorecardStore(tmp_path)
    store.append_audit(_passing_audit())
    store.flag("c", "wrong")
    assert store.promote("c", 3).flagged
    with pytest.raises(PromotionRefused, match="flagged"):
        store.promote("c", 4)


def test_resolve_flag_needs_a_flag_and_evidence(tmp_path) -> None:
    store = ScorecardStore(tmp_path)
    with pytest.raises(ValueError, match="not flagged"):
        store.resolve_flag("c", "evidence")
    store.flag("c", "wrong")
    with pytest.raises(ValueError, match="evidence"):
        store.resolve_flag("c", "")
    assert not store.resolve_flag("c", "fixed and re-audited").flagged


# --- diagnosis ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("args", "kw", "cls", "basis"), [
    (("c", "", 5, 1.0), {}, "none", "tier_and_score"),
    (("c", "", 3, 0.0), {"evaluable": False}, "rubric_gap", "tier_and_score"),
    (("c", "", 3, 1.0), {"reviewed": False}, "rubric_unreviewed", "tier_and_score"),
    (("c", "", 4, 0.6), {}, "low_shape_score", "tier_and_score"),
    (("c", "", 2, 0.1), {}, "low_shape_score", "tier_and_score"),
    (("c", "ModuleNotFoundError: No module named 'z3'", 1, 0.0), {}, "import_error", "error_pattern"),
    (("c", "TIMEOUT after 30s", 2, 0.0), {}, "timeout", "error_pattern"),
    (("c", "Connection refused", 2, 0.0), {}, "external_dep", "error_pattern"),
    (("c", "the routine kept spinning and never returned", 2, 0.0), {}, "runtime_error", "fallback"),
])
def test_diagnosis_rules(args, kw, cls, basis) -> None:
    d = diagnose(*args, **kw)
    assert (d.failure_class, d.basis) == (cls, basis)
    assert d.blocking is (cls != "none")


def test_diagnose_audit_reads_the_audit() -> None:
    unreviewed = audit_output("c", output={"x": 1}, rubric=_rubric(RubricAssertion(field="x"), reviewed=False))
    assert diagnose_audit(unreviewed).failure_class == "rubric_unreviewed"
    assert diagnose_audit(audit_output("c", output={"x": 1})).failure_class == "rubric_gap"
    assert diagnose_audit(_passing_audit()).failure_class == "none"
