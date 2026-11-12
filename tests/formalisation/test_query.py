# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""Named engine questions and their polarity (15.4): what SAT and UNSAT mean depends on the question asked."""
from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

from g6.formal import z3_bridge  # noqa: E402
from g6.formal.envelope import SOLVER_TIMEOUT, SOLVER_UNAVAILABLE, SOLVER_UNKNOWN, Refusal  # noqa: E402
from g6.harness.formalisation import smt_ir  # noqa: E402
from g6.harness.formalisation.query import DECIDED, READINGS, QueryResult, differ, valid_under, witness  # noqa: E402

X = {"x": "Int"}
XR = {"x": "Int", "result": "Int"}
DOMAIN = ["(>= x 0)", "(<= x 10)"]
SHA = "0" * 64


def test_polarity_table():
    found = witness(X, [*DOMAIN, "(> x 0)"], statement_sha256=SHA)
    assert (found.native, found.reading, found.query.quantifier) == ("sat", "witness", "exists")
    assert 0 < found.witness["x"] <= 10

    holds = valid_under(X, [*DOMAIN, "(> x 0)"], "(>= x 0)", statement_sha256=SHA)
    assert (holds.native, holds.reading, holds.query.quantifier) == ("unsat", "entailed_under_assumptions", "forall")
    assert "(not (>= x 0))" in holds.query.native_query and holds.witness is None

    broken = valid_under(X, [*DOMAIN, "(> x 0)"], "(> x 5)", statement_sha256=SHA)
    assert (broken.native, broken.reading) == ("sat", "counterexample") and 0 < broken.witness["x"] <= 5

    apart = differ(XR, DOMAIN, "(>= result x)", "(> result x)", statement_sha256=SHA)
    assert (apart.native, apart.reading) == ("sat", "distinguished")
    assert apart.witness["result"] == apart.witness["x"]

    same = differ(XR, DOMAIN, "(= result (+ x 2))", "(= result (+ x 2))", statement_sha256=SHA)
    assert (same.native, same.reading) == ("unsat", "equivalent_under_assumptions")
    for one in (found, holds, broken, apart, same):
        assert one.decided and one.query.engine == "z3" and one.query.engine_version
        assert one.query.statement_sha256 == SHA and one.query.native_query.endswith("(check-sat)")


def test_no_witness_is_named_for_this_encoding():
    found = witness(X, ["(> x 10)", "(< x 0)"], statement_sha256=SHA)
    assert (found.native, found.reading, found.witness) == ("unsat", "no_witness_for_this_encoding", None)


@pytest.mark.parametrize("why, code, reading", [("incomplete", SOLVER_UNKNOWN, "undecided"),
                                                ("timeout", SOLVER_TIMEOUT, "undecided"),
                                                ("canceled", SOLVER_TIMEOUT, "undecided")])
def test_undecided_never_decides(monkeypatch, why, code, reading):
    monkeypatch.setattr(z3_bridge, "run_query", lambda query, timeout: ("unknown", {}, why))
    for found in (witness(X, ["(> x 0)"], statement_sha256=SHA),
                  valid_under(X, DOMAIN, "(> x 5)", statement_sha256=SHA),
                  differ(XR, DOMAIN, "(>= result x)", "(> result x)", statement_sha256=SHA)):
        assert (found.native, found.reading, found.code) == ("unknown", reading, code)
        assert found.decided is False and found.witness is None and found.reading not in DECIDED


def test_unavailable_engine_never_decides(monkeypatch):
    def gone(query, timeout):
        raise Refusal(SOLVER_UNAVAILABLE, "z3-solver is not importable here")

    monkeypatch.setattr(z3_bridge, "run_query", gone)
    for found in (witness(X, ["(> x 0)"], statement_sha256=SHA),
                  valid_under(X, DOMAIN, "(> x 5)", statement_sha256=SHA),
                  differ(XR, DOMAIN, "(>= result x)", "(> result x)", statement_sha256=SHA)):
        assert (found.reading, found.code, found.native) == ("unavailable", SOLVER_UNAVAILABLE, SOLVER_UNAVAILABLE)
        assert found.decided is False


def test_refused_input_never_decides():
    found = witness(X, ["(set-option :produce-models true)"], statement_sha256=SHA)
    assert found.reading == "refused" and found.decided is False and found.code.startswith("G6_E_FORMAL_")
    assert valid_under({"x": "Float"}, [], "(> x 0)", statement_sha256=SHA).reading == "refused"


def test_parse_only_is_not_evidence():
    """Archive defect fixture 1: parsed ``(assert false)`` mints nothing; a real consistency query records UNSAT."""
    read = smt_ir.read("(assert false)", {})
    assert read == smt_ir.BoolLit(False) and not isinstance(read, QueryResult)
    assert not hasattr(read, "reading") and not hasattr(read, "status")
    asked = witness({}, [smt_ir.write(read)], statement_sha256=SHA)
    assert (asked.native, asked.reading) == ("unsat", "no_witness_for_this_encoding")


def test_a_reading_outside_the_list_is_refused():
    asked = witness(X, ["(> x 0)"], statement_sha256=SHA)
    with pytest.raises(ValueError):
        QueryResult(asked.query, "sat", "proved")
    assert set(DECIDED) < set(READINGS) and {"undecided", "unavailable", "refused"}.isdisjoint(DECIDED)