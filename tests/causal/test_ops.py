# SPDX-License-Identifier: Apache-2.0
"""g6.causal's graph ops: each answers in the envelope, the pure-Python algorithms and DoWhy must agree where DoWhy is
installed (a planted disagreement and a real one, DoWhy naming an unobserved instrument, both block), and without
DoWhy each op still answers, with a card that says only the pure-Python check ran."""
from __future__ import annotations

import importlib.util
import sys

import pytest

from g6.causal import dowhy_engine
from g6.causal.criteria import MAX_CANDIDATES
from g6.causal.envelope import (
    ENGINE_UNAVAILABLE,
    ENGINES_DISAGREE,
    GRAPH_ANSWER,
    GRAPH_CYCLE,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    ONE_ENGINE,
    TIME_LIMIT_TOO_LONG,
    UNKNOWN_NODE,
)
from g6.causal.ops import MAX_TIME_LIMIT_S, adjustment_sets, backdoor_paths, d_separated, identify, instruments

HAS_DOWHY = importlib.util.find_spec("dowhy") is not None
needs_dowhy = pytest.mark.skipif(not HAS_DOWHY, reason="DoWhy is not installed (pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver')")

CONFOUNDER = {"nodes": ["Z", "T", "Y"], "edges": [["Z", "T"], ["Z", "Y"], ["T", "Y"]]}
M_GRAPH = "digraph { A -> T; A -> M; B -> M; B -> Y; T -> Y }"
FRONTDOOR = "digraph { U [observed=no]; U -> T; U -> Y; T -> M -> Y }"
INSTRUMENT = "digraph { U [observed=no]; W -> T; U -> T; U -> Y; T -> Y }"
HIDDEN = "digraph { U [observed=no]; U -> T; U -> Y; T -> Y }"
HIDDEN_INSTRUMENT = "digraph { U [observed=no]; Q [observed=no]; Q -> T; U -> T; U -> Y; T -> Y }"


@pytest.fixture
def no_dowhy(monkeypatch: pytest.MonkeyPatch) -> None:
    """``import dowhy`` (and every ``dowhy.*`` already loaded) now raises ImportError, as on a machine without it."""
    for name in [m for m in sys.modules if m == "dowhy" or m.startswith("dowhy.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "dowhy", None)


CALLS = {
    "d_separated": lambda **kw: d_separated(M_GRAPH, "A", "B", ["M"], **kw),
    "backdoor_paths": lambda **kw: backdoor_paths(M_GRAPH, "T", "Y", ["M"], **kw),
    "adjustment_sets": lambda **kw: adjustment_sets(CONFOUNDER, "T", "Y", **kw),
    "instruments": lambda **kw: instruments(INSTRUMENT, "T", "Y", **kw),
    "identify": lambda **kw: identify(FRONTDOOR, "T", "Y", **kw),
}


def test_d_separated_names_the_open_path() -> None:
    r = d_separated(M_GRAPH, "A", "B", ["M"], engine="python", request_id="q", run_id="r")
    assert r.completion_state == "qualified-draft" and (r.request_id, r.run_id) == ("q", "r")
    assert r.answer["separated"] is False and r.answer["open_path"] == "A -> M <- B"
    assert r.warning_card["code"] == ONE_ENGINE and "engine='python'" in r.warning_card["message"]
    closed = d_separated(M_GRAPH, ["A"], ["B"], engine="python")
    assert closed.answer["separated"] is True and closed.answer["open_path"] is None


def test_the_answers_are_the_graphs() -> None:
    paths = backdoor_paths(M_GRAPH, "T", "Y", engine="python").answer
    assert paths["count"] == 1 and paths["open"] == 0 and paths["paths"][0]["rendered"] == "T <- A -> M <- B -> Y"
    assert adjustment_sets(M_GRAPH, "T", "Y", engine="python").answer["sets"] == [[]]
    assert adjustment_sets(CONFOUNDER, "T", "Y", engine="python").answer["sets"] == [["Z"]]
    assert instruments(INSTRUMENT, "T", "Y", engine="python").answer["instruments"] == ["W"]
    found = identify(FRONTDOOR, "T", "Y", engine="python").answer
    assert found["identifiable"] is True and found["route"] == "frontdoor"
    hidden = identify(HIDDEN, "T", "Y", engine="python")
    assert hidden.completion_state == "qualified-draft" and hidden.answer["identifiable"] is False
    assert "T <- U -> Y" in hidden.answer["reason"]


@needs_dowhy
@pytest.mark.parametrize("op", list(CALLS))
def test_with_dowhy_both_engines_run_and_agree(op: str) -> None:
    r = CALLS[op](request_id="q", run_id="r")
    assert r.completion_state == "qualified-draft", (r.code, r.reason)
    assert r.engines == ["pure_python", "dowhy"] and r.agreement["agreed"] is True and r.agreement["compared"]
    assert r.warning_card["code"] == GRAPH_ANSWER and "DoWhy" in r.warning_card["message"]
    assert "data cannot confirm" in r.warning_card["message"].lower()


@needs_dowhy
@pytest.mark.parametrize(("graph", "route"), [
    (CONFOUNDER, "backdoor"), (M_GRAPH, "backdoor"), (FRONTDOOR, "frontdoor"), (INSTRUMENT, "iv"), (HIDDEN, None),
    ("digraph { Z -> T; Z -> Y }", "no_directed_path"),
])
def test_identification_agrees_with_dowhy_on_the_textbook_graphs(graph, route) -> None:
    r = identify(graph, "T", "Y")
    assert r.completion_state == "qualified-draft" and r.agreement["agreed"] is True, r.reason
    assert r.answer["route"] == route and r.agreement["dowhy"]["identifiable"] is (route is not None)


@needs_dowhy
def test_dowhy_naming_an_unobserved_instrument_is_a_disagreement_that_blocks() -> None:
    """DoWhy 0.14 lists every parent of the treatment that passes its exclusion test as an instrument, observed or not,
    and so calls this effect identifiable; G6 does not use an unobserved variable as an instrument."""
    r = identify(HIDDEN_INSTRUMENT, "T", "Y")
    assert r.completion_state == "blocked-escalated" and r.code == ENGINES_DISAGREE
    assert "Q" in r.reason and "unobserved" in r.reason and r.answer == {}
    assert r.agreement["agreed"] is False and r.agreement["dowhy"]["instruments"] == ["Q"]


@needs_dowhy
def test_a_planted_disagreement_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dowhy_engine, "d_separated", lambda *a: True)
    r = d_separated(M_GRAPH, "A", "B", ["M"])
    assert r.code == ENGINES_DISAGREE and r.completion_state == "blocked-escalated"
    assert r.answer == {} and r.agreement["differences"]


@pytest.mark.parametrize("op", list(CALLS))
def test_without_dowhy_every_graph_op_answers_and_says_one_engine_ran(op: str, no_dowhy) -> None:
    r = CALLS[op]()
    assert r.completion_state == "qualified-draft" and r.engines == ["pure_python"], (r.code, r.reason)
    card = r.warning_card
    assert card["code"] == ONE_ENGINE and "DoWhy is not installed here" in card["message"]
    assert "pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver'" in card["suggested_action"]


@pytest.mark.parametrize("op", list(CALLS))
def test_without_dowhy_asking_for_it_is_refused(op: str, no_dowhy) -> None:
    r = CALLS[op](engine="dowhy")
    assert r.completion_state == "blocked-escalated" and r.code == ENGINE_UNAVAILABLE
    assert "pip install 'g6[causal] @ git+https://github.com/basicisabc1994/g6solver'" in r.warning_card["suggested_action"] and r.answer == {}


@pytest.mark.parametrize(("call", "code", "says"), [
    (lambda: d_separated(M_GRAPH, "A", "Q", engine="python"), UNKNOWN_NODE, "'Q'"),
    (lambda: d_separated(M_GRAPH, "A", "B", ["A"], engine="python"), INPUT_INVALID, "A"),
    (lambda: d_separated(M_GRAPH, [], "B", engine="python"), INPUT_INVALID, "x"),
    (lambda: identify(M_GRAPH, "T", "T", engine="python"), INPUT_INVALID, "differ"),
    (lambda: identify(M_GRAPH, "", "Y", engine="python"), INPUT_INVALID, "treatment needs at least one node"),
    (lambda: identify(HIDDEN, "U", "Y", engine="python"), INPUT_INVALID, "unobserved"),
    (lambda: identify("digraph { T -> Y -> T }", "T", "Y", engine="python"), GRAPH_CYCLE, "T -> Y -> T"),
    (lambda: identify(M_GRAPH, "T", "Y", engine="pydot"), INPUT_INVALID, "engine"),
    (lambda: identify(M_GRAPH, "T", "Y", time_limit=MAX_TIME_LIMIT_S + 1), TIME_LIMIT_TOO_LONG, "cap"),
    (lambda: identify(M_GRAPH, "T", "Y", time_limit=0), INPUT_INVALID, "time_limit"),
])
def test_refusals_are_blocked_with_their_code(call, code: str, says: str) -> None:
    r = call()
    assert r.completion_state == "blocked-escalated" and r.code == code and says in r.reason, r.reason
    assert r.warning_card["code"] == code and r.answer == {}


def test_an_exhaustive_search_over_too_many_candidates_is_refused() -> None:
    names = [f"C{i}" for i in range(MAX_CANDIDATES + 1)]
    graph = {"nodes": [*names, "T", "Y"], "edges": [[c, "T"] for c in names] + [[c, "Y"] for c in names]
             + [["T", "Y"]]}
    r = adjustment_sets(graph, "T", "Y", engine="python")
    assert r.code == INPUT_TOO_LARGE and str(MAX_CANDIDATES) in r.reason
