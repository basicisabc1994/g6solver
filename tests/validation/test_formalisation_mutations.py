# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Deterministic mutations and their labels: wrong only with a witness, equivalent never wrong."""
from __future__ import annotations

import pytest

from g6.harness.formalisation import smt_ir
from tests.validation._formalisation import load

MU = load().mutations
SORTS = {"x": "Int", "result": "Int"}
DOMAIN = ("(>= x 0)", "(<= x 5)")
TABLE = tuple(({"x": x}, x + 1) for x in range(6))


def term(text):
    return smt_ir.read(text, SORTS)


def test_equivalent_mutant_not_wrong():
    pytest.importorskip("z3")
    found = MU.labelled(term("(= result (+ x 1))"), term("(= result (+ 1 x))"), DOMAIN, sorts=SORTS, table=TABLE)
    assert found == {"label": "equivalent", "witness": None, "stratum": "equivalent"}


def test_wrong_needs_a_witness(monkeypatch):
    found = MU.labelled(term("(= result (+ x 1))"), term("(= result (+ x 2))"), DOMAIN, sorts=SORTS, table=TABLE)
    assert found["label"] == "wrong" and found["witness"]["reference"] != found["witness"]["mutant"]
    assert found["witness"]["inputs"] in [row[0] for row in TABLE]
    # no witness in the table and no solver decision: unlabelled, never wrong
    from g6.formal import z3_bridge

    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, "timeout"))
    outside = MU.labelled(term("(= result (+ x 1))"), term("(=> (< x 100) (= result (+ x 1)))"), DOMAIN, sorts=SORTS,
                          table=TABLE)
    assert outside == {"label": "unlabelled", "witness": None, "stratum": "unlabelled"}


def test_matches_example_fails_elsewhere():
    reference = term("(=> (> x 2) (= result (+ x 1)))")
    mutant = term("(=> (> x 3) (= result (+ x 1)))")     # the same at x = 5, another rule at x = 3
    found = MU.labelled(reference, mutant, DOMAIN, sorts=SORTS, table=TABLE, example={"x": 5})
    assert (found["label"], found["stratum"]) == ("wrong", "agrees_on_example") and found["witness"]["inputs"] == {"x": 3}
    again = MU.labelled(reference, mutant, DOMAIN, sorts=SORTS, table=TABLE, example={"x": 3})
    assert (again["label"], again["stratum"]) == ("wrong", "differs_on_example")


def test_every_mutation_kind_is_produced_and_none_is_the_original():
    text = "(=> (>= x 2) (= result (+ x 1)))"
    made = MU.mutants(term(text), SORTS)
    assert {kind for kind, _ in made} == set(MU.MUTATIONS) - {"domain"}
    assert all(smt_ir.write(m) != text for _, m in made) and len({smt_ir.write(m) for _, m in made}) == len(made)
    assert ("omitted_guard", term("(= result (+ x 1))")) in made and ("boundary", term("(=> (> x 2) (= result (+ x 1)))")) in made
    assert ("swap_result_input", term("(=> (>= result 2) (= x (+ result 1)))")) in made
    domains = MU.domain_mutants(DOMAIN, {"x": "Int"})
    assert {kind for kind, _ in domains} == {"domain"} and ("domain", ("(> x 0)", "(<= x 5)")) in domains
    assert ("domain", ("(>= x 0)", "(<= x 6)")) in domains


def test_true_is_never_counted_as_one_in_a_label():
    sorts = {"p": "Bool", "result": "Bool"}
    table = (({"p": True}, False), ({"p": False}, True))
    found = MU.labelled(smt_ir.read("(= result (not p))", sorts), smt_ir.read("(= result p)", sorts), (), sorts=sorts,
                        table=table)
    assert found["label"] == "wrong" and type(found["witness"]["result"]) is bool


def test_wrong_renderings_pair_a_mutants_words_with_the_original_text():
    from g6.harness.formalisation import smt_adapter
    from g6.harness.formalisation.render import render

    one = smt_adapter.statement(entry_point="cases.f", inputs=(("x", "Int"),), result_sort="Int",
                                relation="(=> (>= x 2) (= result (+ x 1)))", domain=DOMAIN)
    right = render(one)
    forged = MU.wrong_renderings(one)
    assert forged and all(shown.words != right.words for _, shown in forged)
    assert all(shown.formal_text == one.text and shown.statement_sha256 == one.sha256 for _, shown in forged)