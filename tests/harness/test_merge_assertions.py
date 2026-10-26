# SPDX-License-Identifier: Apache-2.0
"""Declarative assertions build ordinary harness Checks, with explicit suite replacement."""
import json

import pytest

from g6.harness import Check, Memory, Standard, check_field, confirm_suite, resolve_field, run


@pytest.mark.parametrize("op,expected", [("eq", 4), ("equals", 4), ("ne", 3), ("not_equals", 3),
    ("gt", 3), ("gte", 4), ("lt", 5), ("lte", 4), ("truthy", None)])
def test_golden_tests_02_assertions_use_the_callers_output(op, expected):
    # Removal makes this fail: `return _compare(actual, self.op, json.loads(self.expected_json))` in _FieldPredicate.
    check = check_field("summary.values.0", op, expected)
    assert isinstance(check, Check) and check.run('{"summary":{"values":[4]}}') is True
    assert resolve_field({"summary": {"values": [4, 7]}}, "summary.values.count") == 2


@pytest.mark.parametrize("op,value,actual", [("contains", "cat", "CATCH"), ("not_contains", "dog", "cat"),
                                            ("falsey", None, []), ("len_gte", 2, [1, 2])])
def test_remaining_assertion_operations(op, value, actual):
    # Removal makes this fail: `return _compare(actual, self.op, json.loads(self.expected_json))` in _FieldPredicate.
    assert check_field("value", op, value).run(json.dumps({"value": actual})) is True


def test_golden_tests_01_d1_wrong_value_blocks_and_equality_is_only_a_draft():
    # Removal makes this fail: `return _compare(actual, self.op, json.loads(self.expected_json))` in _FieldPredicate.
    memory = Memory(":memory:")
    try:
        check = check_field("order.total", "eq", 73, known_bad=('{"order":{"total":72}}',))
        standard = Standard("Summarise the refund policy", (check,))
        bad = run(standard, lambda c: '{"order":{"total":74}}', memory=memory)
        assert bad["completion_state"] == "blocked-escalated"
        good = run(standard, lambda c: '{"order":{"total":73}}', memory=memory)
        assert good["completion_state"] == "qualified-draft"
    finally:
        memory.close()


def test_golden_tests_03_suite_keeps_builtin_checks_without_explicit_override():
    # Removal makes this fail: `if item.check_id in by_id and not item.override:` in confirm_suite.
    builtin = check_field("total", "gte", 73, check_id="total")
    manifest = [{"check_id": "total", "field": "total", "op": "gte", "value": 1}]
    with pytest.raises(ValueError, match="G6_E_HARNESS_OVERRIDE"):
        confirm_suite([builtin], manifest)
    manifest[0]["override"] = True
    assert confirm_suite([builtin], manifest)[0].run('{"total":2}') is True
    assert confirm_suite([builtin], [])[0] is builtin


@pytest.mark.parametrize("op,value", [("ne", 42), ("not_contains", "text"), ("falsey", None), ("eq", None)])
def test_missing_path_never_satisfies_a_negative_assertion(op, value):
    # Removal makes this fail: `if actual is _MISSING: return False` in _FieldPredicate.
    assert check_field("items.3.value", op, value).run('{"items":[]}') is False


def test_manifest_schema_refuses_claims_and_textual_override():
    # Removal makes this fail: `model_config = ConfigDict(extra="forbid", frozen=True)` in _AssertionSpec.
    with pytest.raises(ValueError):
        confirm_suite([], [{"check_id": "c", "field": "x", "op": "eq", "value": 1, "approved": True}])
    with pytest.raises(ValueError):
        confirm_suite([], [{"check_id": "c", "field": "x", "op": "eq", "value": 1, "override": "true"}])


def test_frozen_check_hash_covers_the_assertion_parameters():
    # Removal makes this fail: `return Check(f"{field} {op} {expected}", _FieldPredicate(field, op, expected), check_id=check_id,` in check_field.
    a = check_field("total", "gte", 73, check_id="total")
    b = check_field("total", "gte", 1, check_id="total")
    assert a.code_sha() != b.code_sha()
    assert a.code_sha() == check_field("total", "gte", 73, check_id="total").code_sha()
