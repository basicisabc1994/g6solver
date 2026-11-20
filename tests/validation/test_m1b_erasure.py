"""Synthetic construction and counts checks for the M1b arm."""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
from copy import deepcopy

import pytest

from validation.distill.pilot import funnel

THREE = "def test_value():\n    assert value(1) == 2\n    assert value(2) == 3\n    assert value(3) == 4\n"
OLD = "def test_value():\n    assert value(1) == 1\n"


def module(name):
    path = "validation.distill.pilot.m1b." + name
    assert importlib.util.find_spec(path) is not None, f"Missing M1b module: {name}"
    return importlib.import_module(path)


def erasures(before, after):
    return module("erasure").indexed_erasures(before, after)


def case(before=OLD, after=THREE):
    return funnel.CaseRecord.model_validate({
        "unit_id": "repo-a-1",
        "provenance": {"source": "maintainer", "cohort": "development",
                       "source_ref": "pilot-acquisition", "source_sha256": "a" * 64},
        "files": {"subject.py": "def value(x):\n    return x + 1\n",
                  "other.py": "def other():\n    return 2\n"},
        "target": "subject.py", "changed_lines": [2],
        "before_tests": before, "after_tests": after,
        "known_wrong": ["def value(x):\n    return x\n"],
    })


def test_added_function_has_three_independent_erasures():
    result = erasures("", THREE)
    assert isinstance(result, list) and result.reason == "ok"
    assert [v.variant_id for v in result] == [
        "indexed_erasure:test_value:1", "indexed_erasure:test_value:2", "indexed_erasure:test_value:3"]
    assert [v.after_tests for v in result] == [
        "def test_value():\n    pass\n    assert value(2) == 3\n    assert value(3) == 4\n",
        "def test_value():\n    assert value(1) == 2\n    pass\n    assert value(3) == 4\n",
        "def test_value():\n    assert value(1) == 2\n    assert value(2) == 3\n    pass\n",
    ]
    assert all(v.kind == "indexed_erasure" for v in result)


def test_changed_function_includes_all_direct_assertions():
    assert len(erasures(OLD, THREE)) == 3


@pytest.mark.parametrize("before,after", [
    (THREE, THREE), (THREE, "# moved\n\n" + THREE),
    (THREE, THREE.replace("assert value(1)", "assert   value(1)")),
])
def test_ast_unchanged_function_has_no_erasures(before, after):
    result = erasures(before, after)
    assert result == [] and result.reason == "no_eligible_assert"


def test_nested_assertions_and_non_top_level_tests_are_not_indexed():
    source = """def helper():
    assert False
class Tests:
    def test_method(self):
        assert False
if True:
    def test_conditional():
        assert False
def test_nested():
    with context():
        assert False
    if True:
        assert False
    for item in values:
        assert item
    while condition:
        assert False
    def test_helper():
        assert False
    assert True
"""
    result = erasures("", source)
    assert [v.variant_id for v in result] == ["indexed_erasure:test_nested:1"]
    original = ast.parse(source)
    expected = deepcopy(original)
    expected.body[-1].body[-1] = ast.Pass()
    assert result[0].after_tests == ast.unparse(ast.fix_missing_locations(expected)) + "\n"
    result = erasures("", source.replace("    assert True\n", ""))
    assert result == [] and result.reason == "no_eligible_assert"


def test_cap_is_global_and_order_is_source_order():
    source = ("def test_z():\n" + "    assert True\n" * 6
              + "def test_a():\n" + "    assert False\n" * 5)
    result = erasures("", source)
    assert [v.variant_id for v in result] == [
        "indexed_erasure:test_z:1", "indexed_erasure:test_z:2", "indexed_erasure:test_z:3",
        "indexed_erasure:test_z:4", "indexed_erasure:test_z:5", "indexed_erasure:test_z:6",
        "indexed_erasure:test_a:1", "indexed_erasure:test_a:2"]


def test_ids_and_sources_are_stable_across_calls():
    assert erasures(OLD, THREE) == erasures(OLD, THREE)


def test_each_variant_changes_exactly_one_statement_and_preserves_rest():
    source = "import subject\nVALUE = 3\n" + THREE + "def helper():\n    assert VALUE\n"
    original = ast.parse(source)
    for variant in erasures(OLD, source):
        tree = ast.parse(variant.after_tests)
        changed = [i for i, (a, b) in enumerate(zip(original.body[2].body, tree.body[2].body, strict=True))
                   if ast.dump(a) != ast.dump(b)]
        assert len(changed) == 1
        index = changed[0]
        assert isinstance(tree.body[2].body[index], ast.Pass)
        tree.body[2].body[index] = original.body[2].body[index]
        assert ast.dump(tree) == ast.dump(original)
        assert variant.after_tests == ast.unparse(ast.parse(variant.after_tests)) + "\n"


def test_async_top_level_function_is_indexed():
    result = erasures("", "async def test_async():\n    assert await value()\n")
    assert [v.variant_id for v in result] == ["indexed_erasure:test_async:1"]
    assert result[0].after_tests == "async def test_async():\n    pass\n"


@pytest.mark.parametrize("before,after", [("def test_x(:", THREE), (OLD, "def test_x(:"),
                                          ("\x00", THREE), (OLD, "\x00")])
def test_unparseable_input_is_empty_with_reason(before, after):
    result = erasures(before, after)
    assert result == [] and result.reason == "unparseable_tests"


@pytest.mark.parametrize("before,after", [(THREE + THREE, THREE), (OLD, THREE + THREE)])
def test_duplicate_test_names_are_refused(before, after):
    result = erasures(before, after)
    assert result == [] and result.reason == "ambiguous_test_function"


def test_arm_tags_legacy_and_indexed_constructions_and_keeps_case_fields():
    item = case()
    snapshot = item.model_dump()
    constructions = module("arm").m1b_variants(item)
    assert [v.variant_id for v in constructions] == [
        *funnel.VARIANTS, "indexed_erasure:test_value:1", "indexed_erasure:test_value:2",
        "indexed_erasure:test_value:3"]
    for construction in constructions[:8]:
        name = construction.variant_id
        assert construction.kind == ("reversed_maintainer" if name == "reversed_maintainer" else "operator:" + name)
        assert construction.reason == "ok"
        assert construction.case.model_dump() == funnel._variant(item, "multi_target", name).model_dump()
    for construction in constructions[8:]:
        assert construction.kind == "indexed_erasure" and construction.reason == "ok"
        expected = {**snapshot, "before_tests": THREE, "after_tests": construction.case.after_tests}
        assert construction.case.model_dump() == expected
    assert constructions[0].case.after_tests == constructions[8].case.after_tests
    assert item.model_dump() == snapshot


def test_arm_keeps_inapplicable_constructions_with_reason():
    result = module("arm").m1b_variants(case(after="def test_value():\n    pass\n"))
    assert len(result) == 8
    assert all(v.case is None and v.reason == "operator_not_applicable" for v in result[:7])
    assert result[-1].kind == "reversed_maintainer" and result[-1].reason == "ok"


def rows(kind, repositories, labels=("positive", "negative")):
    return [{"repository": repository, "kind": kind, "label": label}
            for repository in repositories for label in labels]


def validity(data):
    return module("validity").construction_validity(data)


def test_kind_only_label_rule_has_accuracy_one_and_no_informative_kind():
    data = rows("indexed_erasure", ("r1", "r2", "r3"), ("positive",))
    data += rows("reversed_maintainer", ("r1", "r2", "r3"), ("negative",))
    result = validity(data)
    assert result["cv1"] == {
        "table": {"indexed_erasure": {"positive": {"r1": 1, "r2": 1, "r3": 1}},
                  "reversed_maintainer": {"negative": {"r1": 1, "r2": 1, "r3": 1}}},
        "labelled_count": 6, "best_rule_correct": 6, "best_rule_accuracy": 1.0}
    assert result["cv2"]["informative_kinds"] == []
    assert result["cv3"] == {"repository_count": 0, "stop_count": 3, "stop_reached": False}


def test_informative_kinds_require_three_mixed_repositories_and_cv3_deduplicates():
    data = rows("indexed_erasure", ("r1", "r2", "r3"))
    data += rows("operator:erase_assertion", ("r2", "r3", "r4"))
    data += rows("reversed_maintainer", ("r5", "r6"))
    data += rows("reversed_maintainer", ("r7",), ("negative",))
    result = validity(data)
    assert result["cv2"] == {
        "repositories_by_kind": {"indexed_erasure": ["r1", "r2", "r3"],
                                 "operator:erase_assertion": ["r2", "r3", "r4"],
                                 "reversed_maintainer": ["r5", "r6"]},
        "informative_kinds": ["indexed_erasure", "operator:erase_assertion"]}
    assert result["cv3"] == {"repository_count": 4, "stop_count": 3, "stop_reached": True}
    assert result["cv1"]["best_rule_correct"] == 9
    assert result["cv1"]["best_rule_accuracy"] == 9 / 17


def test_two_mixed_repositories_do_not_meet_cv2_or_cv3():
    result = validity(rows("indexed_erasure", ("r1", "r2")))
    assert result["cv2"]["informative_kinds"] == []
    assert result["cv3"]["repository_count"] == 0
    assert not result["cv3"]["stop_reached"]


def test_cv1_weights_rows_and_counts_duplicates():
    data = rows("indexed_erasure", ("r1", "r2"), ("positive",))
    data += rows("indexed_erasure", ("r3",), ("negative",)) * 3
    data += rows("reversed_maintainer", ("r1",), ("positive",))
    result = validity(data)
    assert result["cv1"]["table"]["indexed_erasure"]["negative"]["r3"] == 3
    assert result["cv1"]["labelled_count"] == 6
    assert result["cv1"]["best_rule_correct"] == 4
    assert result["cv1"]["best_rule_accuracy"] == 4 / 6


def test_non_headline_rows_are_counted_separately_and_do_not_add_evidence():
    data = rows("indexed_erasure", ("r1", "r2", "r3"), ("positive",))
    data += rows("indexed_erasure", ("r1", "r2", "r3"), ("excluded", None, "n1"))
    data += [{"repository": "", "kind": "indexed_erasure", "label": "negative"},
             {"repository": "r1", "kind": None, "label": "negative"}]
    result = validity(data)
    assert result["ignored_count"] == 11
    assert result["cv1"]["labelled_count"] == 3
    assert result["cv1"]["best_rule_accuracy"] == 1.0
    assert result["cv2"]["informative_kinds"] == []
    assert result["cv3"]["repository_count"] == 0


def test_empty_validity_has_no_accuracy_and_is_json_safe():
    result = validity([])
    assert result["code"] == "no_headline_rows"
    assert result["cv1"] == {"table": {}, "labelled_count": 0,
                             "best_rule_correct": 0, "best_rule_accuracy": None}
    assert result["cv2"] == {"repositories_by_kind": {}, "informative_kinds": []}
    assert result["cv3"] == {"repository_count": 0, "stop_count": 3, "stop_reached": False}
    json.dumps(result, allow_nan=False)


def test_validity_is_order_independent_and_does_not_mutate_rows():
    data = rows("indexed_erasure", ("r3", "r1", "r2"))
    snapshot = deepcopy(data)
    result = validity(iter(data))
    assert result == validity(reversed(data))
    assert result["code"] == "ok" and result["ignored_count"] == 0
    assert data == snapshot


FUTURE = "from __future__ import annotations\n" + THREE
DOC_FUTURE = '"""Module docstring."""\nfrom __future__ import annotations\n' + THREE
OUTPUT_INVALID = ("extract_helper", "named_constant")


@pytest.mark.parametrize("after", [FUTURE, DOC_FUTURE])
def test_arm_records_operator_output_that_does_not_compile(after):
    result = module("arm").m1b_variants(case(after=after))
    assert [v.variant_id for v in result] == [
        *funnel.VARIANTS, "indexed_erasure:test_value:1", "indexed_erasure:test_value:2",
        "indexed_erasure:test_value:3"]
    for construction in result:
        if construction.variant_id in OUTPUT_INVALID:
            assert construction.case is None and construction.reason == "operator_output_invalid"
        else:
            assert construction.case is not None and construction.reason == "ok"


def _operator_applies(source, name):
    from g6.distill import operators
    try:
        operators.apply_operator(source, name)
    except (ValueError, SyntaxError, TypeError):
        return False
    return True


@pytest.mark.parametrize("after", [THREE, FUTURE, DOC_FUTURE, "def test_value():\n    pass\n"])
def test_arm_refusals_match_the_fit_filter(after):
    result = {v.variant_id: v for v in module("arm").m1b_variants(case(after=after))}
    for name in funnel.VARIANTS:
        if name == "reversed_maintainer":
            continue
        assert (result[name].case is not None) == _operator_applies(after, name), name


def test_arm_records_a_type_error_from_one_operator(monkeypatch):
    original = funnel.operators.apply_operator

    def apply(source, name):
        if name == "rename_test":
            raise TypeError("synthetic")
        return original(source, name)

    monkeypatch.setattr(funnel.operators, "apply_operator", apply)
    result = {v.variant_id: v for v in module("arm").m1b_variants(case())}
    assert result["rename_test"].case is None and result["rename_test"].reason == "operator_output_invalid"
    assert all(v.case is not None for name, v in result.items() if name != "rename_test")


@pytest.mark.parametrize("error", [RecursionError, MemoryError, OverflowError])
def test_arm_lets_resource_errors_through(monkeypatch, error):
    def apply(source, name):
        raise error("synthetic")

    monkeypatch.setattr(funnel.operators, "apply_operator", apply)
    with pytest.raises(error):
        module("arm").m1b_variants(case())