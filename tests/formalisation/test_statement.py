# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""A statement is its bytes: any changed part is another statement, and three digests are kept apart."""
from __future__ import annotations

import dataclasses
import json

import pytest

from g6.harness.formalisation import FRAGMENTS, UNSUPPORTED, Unsupported, supported
from g6.harness.formalisation.statement import STATEMENT_FIELDS, FormalStatement, Observation
from tests.formalisation._cases import relation


def test_record_is_canonical_json_of_every_field():
    one = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError", domain=())
    held = json.loads(one.record())
    assert set(held) == set(STATEMENT_FIELDS) and held["text"] == "(= result (+ x 1))"
    assert held["observation"]["inputs"] == [["x", "Int"]] and held["definitions"] == [["raises_when", "(< x 0)"]]
    assert one.record() == relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError", domain=()).record()


@pytest.mark.parametrize("part, changed", [
    ("text", "(= result (+ x 2))"), ("definitions", (("raises_when", "(< x 1)"),)), ("domain", ("(>= x 1)",)),
    ("assumptions", ("(> x 3)",)), ("bounds", (("depth", 8),)), ("adapter", "smt.return.int_bool.v2"),
    ("fragment", "smt.relation.int_bool.v2"), ("language", "lean4"),
    ("observation", Observation("returns", "shop.g", (("x", "Int"),), "Int", "ValueError")),
])
def test_one_changed_part_is_another_statement(part, changed):
    one = relation("(= result (+ x 1))", raises_when="(< x 0)", error="ValueError", domain=("(>= x 0)",))
    other = dataclasses.replace(one, **{part: changed})
    assert other.sha256 != one.sha256
    assert (other.definitions_sha256 != one.definitions_sha256) == (part == "definitions")
    assert (other.assumptions_sha256 != one.assumptions_sha256) == (part in ("domain", "assumptions", "bounds"))


def test_every_field_is_covered_by_the_changed_part_test():
    covered = {"text", "definitions", "domain", "assumptions", "bounds", "adapter", "fragment", "language",
               "observation"}
    assert covered == set(STATEMENT_FIELDS)


@pytest.mark.parametrize("bad", [{"domain": "(> x 0)"}, {"domain": (1,)}, {"definitions": (("a",),)},
                                 {"definitions": (("a", "b"), ("a", "c"))}, {"bounds": (("depth", "8"),)},
                                 {"text": None}, {"observation": None}])
def test_malformed_parts_are_refused(bad):
    base = {"language": "smt", "fragment": "f", "text": "true",
            "observation": Observation("returns", "shop.f", (("x", "Int"),), "Int")}
    with pytest.raises(ValueError):
        FormalStatement(**{**base, **bad})
    with pytest.raises(ValueError):
        Observation("yields", "shop.f", ())


def test_unlisted_language_fragment_or_adapter_is_refused_with_the_code():
    one = relation("(= result (+ x 1))")
    supported(one)
    for change in ({"language": "curry_howard"}, {"language": "z3_python"}, {"fragment": "smt.anything.v9"},
                   {"adapter": "smt.other.v1"}, {"language": "lean4"}):
        with pytest.raises(Unsupported, match=UNSUPPORTED) as refused:
            supported(dataclasses.replace(one, **change))
        assert refused.value.code == UNSUPPORTED
    assert all(entry["language"] in ("smt", "lean4", "tla", "datalog") for entry in FRAGMENTS.values())