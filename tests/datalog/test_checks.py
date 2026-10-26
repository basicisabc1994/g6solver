# SPDX-License-Identifier: Apache-2.0
"""G6's own check of a Datalog model, in pure Python (owner, 2026-10-20): closure, support and negation. Each check is
fed the engine's model, which must pass, and planted wrong answers it must catch: a missing tuple fails closure, an
extra tuple fails support, and a negation read against the wrong facts fails the negation check. No Z3 needed."""
from __future__ import annotations

import time

import pytest

from g6.datalog import checks, seminaive
from g6.datalog.envelope import CERTIFICATE_REFUTED, TIME_LIMIT, Refusal
from g6.datalog.language import parse_program
from g6.datalog.seminaive import Justification

TC = ("edge(a, b). edge(b, c). edge(c, d). edge(d, e).\n"
      "path(X, Y) :- edge(X, Y).\n"
      "path(X, Z) :- edge(X, Y), path(Y, Z).\n")
GUARD = "node(a). node(b). node(c). banned(a).\nok(X) :- node(X), not banned(X).\n"


def _model(text: str) -> tuple:
    program = parse_program(text)
    model = seminaive.solve(program)
    return program, {r: set(rows) for r, rows in model.relations.items()}, dict(model.support)


def _failures(check: checks.Check, kind: str) -> list[str]:
    return [f for f in check.failures if f.startswith(kind + ":")]


@pytest.mark.parametrize("text", [TC, GUARD, "raining. dry :- not raining. wet :- raining.",
                                  "n(1). n(2). n(3). lt(X, Y) :- n(X), n(Y), X < Y.",
                                  "e(a, b). e(b, a). r(X, Y) :- e(X, Y). r(X, Z) :- r(X, Y), r(Y, Z)."])
def test_the_engines_model_passes_every_check(text: str) -> None:
    check = checks.check_model(*_model(text))
    assert check.passed and check.code == "" and check.failures == (), check.failures
    assert check.checked == ("closure", "support", "negation")
    assert check.details["tuples"] == sum(len(rows) for rows in _model(text)[1].values())


def test_a_missing_derived_tuple_fails_closure() -> None:
    program, relations, support = _model(TC)
    relations["path"].discard(("a", "e"))
    del support[("path", ("a", "e"))]
    check = checks.check_model(program, relations, support)
    assert not check.passed and check.code == CERTIFICATE_REFUTED
    assert any("path(a, e)" in f for f in _failures(check, "closure")), check.failures


def test_a_missing_fact_fails_closure() -> None:
    program, relations, support = _model(GUARD)
    relations["banned"].discard(("a",))
    del support[("banned", ("a",))]
    check = checks.check_model(program, relations, support)
    assert any("banned(a)" in f for f in _failures(check, "closure")), check.failures


def test_an_extra_tuple_with_no_derivation_fails_support() -> None:
    program, relations, support = _model(TC)
    relations["path"].add(("a", "a"))  # no edge leads into a, so no rule builds on it
    check = checks.check_model(program, relations, support)
    assert any("path(a, a)" in f and "no derivation" in f for f in _failures(check, "support")), check.failures
    assert _failures(check, "closure") == []  # an extra tuple breaks no rule here: only support can catch it


def test_an_extra_tuple_with_a_derivation_from_tuples_not_in_the_model_fails_support() -> None:
    program, relations, support = _model(TC)
    relations["path"].add(("e", "a"))
    support[("path", ("e", "a"))] = Justification(1, (("X", "e"), ("Y", "d"), ("Z", "a")), 9)
    check = checks.check_model(program, relations, support)
    assert any("edge(e, d)" in f for f in _failures(check, "support")), check.failures


def test_tuples_that_only_support_each_other_fail_support() -> None:
    text = "q(a). p(X) :- q(X). p(X) :- r(X). r(X) :- p(X)."
    program, relations, support = _model(text)
    relations["p"].add(("b",))
    relations["r"].add(("b",))
    support[("p", ("b",))] = Justification(1, (("X", "b"),), 5)
    support[("r", ("b",))] = Justification(2, (("X", "b"),), 5)
    check = checks.check_model(program, relations, support)
    assert _failures(check, "closure") == []                       # the planted model is closed
    assert any("not derived before" in f for f in _failures(check, "support")), check.failures


def test_a_derivation_that_does_not_give_its_tuple_fails_support() -> None:
    program, relations, support = _model(GUARD)
    support[("ok", ("b",))] = Justification(0, (("X", "c"),), 1)
    check = checks.check_model(program, relations, support)
    assert any("ok(c)" in f and "ok(b)" in f for f in _failures(check, "support")), check.failures


def test_a_comparison_that_does_not_hold_fails_support() -> None:
    program, relations, support = _model("n(1). n(2). lt(X, Y) :- n(X), n(Y), X < Y.")
    relations["lt"].add((2, 1))
    support[("lt", (2, 1))] = Justification(0, (("X", 2), ("Y", 1)), 1)
    check = checks.check_model(program, relations, support)
    assert any("2 < 1" in f for f in _failures(check, "support")), check.failures


def test_a_tuple_passed_off_as_a_fact_fails_support() -> None:
    program, relations, support = _model(TC)
    support[("path", ("a", "b"))] = Justification(None)
    check = checks.check_model(program, relations, support)
    assert any("path(a, b)" in f and "no such fact" in f for f in _failures(check, "support")), check.failures


def test_a_wrong_negation_fails_the_negation_check() -> None:
    program, relations, support = _model(GUARD)
    assert relations["ok"] == {("b",), ("c",)}
    relations["ok"].add(("a",))
    support[("ok", ("a",))] = Justification(0, (("X", "a"),), 1)
    check = checks.check_model(program, relations, support)
    assert any("not banned(a)" in f for f in _failures(check, "negation")), check.failures
    assert _failures(check, "closure") == [] and _failures(check, "support") == []


def test_a_negation_read_against_an_incomplete_lower_stratum_is_caught() -> None:
    program, relations, support = _model(GUARD)
    relations["banned"].clear()                    # the lower stratum lost its fact, so not banned(a) looks true
    del support[("banned", ("a",))]
    relations["ok"].add(("a",))
    support[("ok", ("a",))] = Justification(0, (("X", "a"),), 1)
    check = checks.check_model(program, relations, support)
    assert any("banned(a)" in f for f in _failures(check, "closure")), check.failures


def test_a_model_missing_a_relation_or_naming_an_unknown_one_is_refuted() -> None:
    program, relations, support = _model(TC)
    del relations["path"]
    relations["ghost"] = {("a",)}
    check = checks.check_model(program, relations, support)
    assert any("path" in f for f in check.failures) and any("ghost" in f for f in check.failures), check.failures


def test_the_check_stops_at_its_deadline() -> None:
    with pytest.raises(Refusal) as caught:
        checks.check_model(*_model(TC), deadline=time.monotonic() - 1)
    assert caught.value.code == TIME_LIMIT
