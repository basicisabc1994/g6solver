# SPDX-License-Identifier: Apache-2.0
"""g6.formal's four-valued logic (the archive's ``qa_logic``, owner 2026-10-20): the connectives' truth tables, written
out by hand as an independent oracle, the value g6.evidence gives a predicate, claim states that never reach
``verified``, and modal frames whose conditions are checked before a modality is trusted."""
from __future__ import annotations

import itertools

import pytest

from g6.evidence.certificate import PredicateStatement
from g6.formal import four_valued as fv
from g6.formal.envelope import (
    CLAIM_CONFLICT,
    CLAIM_REFUTED,
    CLAIM_UNKNOWN,
    FRAME_UNFIT,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    PARSE_ERROR,
    SOLVER_DECISION,
)
from g6.formal.four_valued import B, F, N, T, TruthValue
from g6.invariants import check_envelope

VALUES = {"T": T, "F": F, "B": B, "N": N}

# Belnap's tables, rows p and columns q in the order T F B N (truth order: B and N are incomparable, B & N is F).
NOT = {"T": "F", "F": "T", "B": "B", "N": "N"}
AND = {"T": "TFBN", "F": "FFFF", "B": "BFBF", "N": "NFFN"}
OR = {"T": "TTTT", "F": "TFBN", "B": "TBBT", "N": "TNTN"}
IMPLIES = {"T": "TFBN", "F": "TTTT", "B": "TBBT", "N": "TNTN"}


def _pairs():
    for (p, row_and), (_, row_or), (_, row_implies) in zip(AND.items(), OR.items(), IMPLIES.items(), strict=True):
        for q, a, o, i in zip("TFBN", row_and, row_or, row_implies, strict=True):
            yield p, q, a, o, i


@pytest.mark.parametrize(("p", "q", "and_", "or_", "implies"), list(_pairs()))
def test_the_connectives_match_belnaps_tables(p: str, q: str, and_: str, or_: str, implies: str) -> None:
    left, right = VALUES[p], VALUES[q]
    assert (left & right).state.value == and_ and (left | right).state.value == or_
    assert left.implies(right).state.value == implies
    assert (~left).state.value == NOT[p]


def test_the_two_bits_are_the_evidence_and_both_is_a_conflict() -> None:
    assert [(v.support_for, v.support_against) for v in (T, F, B, N)] == [(1, 0), (0, 1), (1, 1), (0, 0)]
    assert B.requires_rca is True and not any(v.requires_rca for v in (T, F, N))
    assert TruthValue.parse("both") == B and TruthValue.parse("neither") == N and TruthValue.parse("t") == T


def test_a_g6_evidence_statement_reads_as_its_belnap_value() -> None:
    """g6.evidence gives each predicate (support, refute) and no connectives; the connectives here take it as is."""
    supported = PredicateStatement("p", True, False, ("c1",), (), ())
    contested = PredicateStatement("q", True, True, ("c1",), ("c2",), ())
    assert TruthValue.of(supported) == T and TruthValue.of(contested) == B
    assert (TruthValue.of(supported) & TruthValue.of(contested)) == B


def test_excluded_middle_is_not_a_tautology_here() -> None:
    """p or not p is N when there is no evidence and B when the evidence conflicts: the logic keeps both visible."""
    table = fv.truth_table("p | ~p")
    assert [(row["p"], row["value"]) for row in table.table] == [("T", "T"), ("F", "T"), ("B", "B"), ("N", "N")]
    assert table.completion_state == "qualified-draft" and check_envelope(table.model_dump()) == []


def test_evaluate_a_formula_and_the_claim_state_of_each_value() -> None:
    # N, no evidence either way, blocks too: a claim nothing supports has no answer to qualify, and its card says so
    for value, state, code in (("T", "qualified-draft", SOLVER_DECISION), ("N", "blocked-escalated", CLAIM_UNKNOWN),
                               ("F", "blocked-escalated", CLAIM_REFUTED), ("B", "blocked-escalated", CLAIM_CONFLICT)):
        result = fv.evaluate("(p & q) -> r", {"p": "T", "q": "T", "r": value}, request_id="q", run_id="r")
        assert result.value == value and result.completion_state == state and result.warning_card["code"] == code
        assert check_envelope(result.model_dump()) == [] and (result.request_id, result.run_id) == ("q", "r")
        assert result.evidence[0]["pure_python"] is True and result.evidence[0]["external_solver_ran"] is False
    assert fv.claim_state(T) == "qualified-draft"
    assert fv.claim_state(F) == fv.claim_state(B) == fv.claim_state(N) == "blocked-escalated"


def test_the_formula_language_is_the_propositional_one() -> None:
    assert fv.evaluate("IFF(p, NOT(q))", {"p": "B", "q": "B"}).value == "B"
    assert fv.evaluate("TRUE & p", {"p": "N"}).value == "N"
    assert fv.evaluate("FALSE | p", {"p": "F"}).value == "F"


@pytest.mark.parametrize(("formula", "values", "code"), [
    ("p & q", {"p": "T"}, INPUT_INVALID),          # q has no value: nothing is assumed for you
    ("p", {"p": "maybe"}, INPUT_INVALID),
    ("p &", {"p": "T"}, PARSE_ERROR),
    ("p", ["T"], INPUT_INVALID),
])
def test_bad_inputs_are_refused(formula, values, code: str) -> None:
    result = fv.evaluate(formula, values)
    assert result.code == code and result.completion_state == "blocked-escalated" and result.value == ""


def test_a_truth_table_has_four_to_the_atoms_rows_and_a_size_bound() -> None:
    table = fv.truth_table("a & b")
    assert len(table.table) == 16 and {row["value"] for row in table.table} == {"T", "F", "B", "N"}
    for row in table.table:
        assert row["value"] == (VALUES[row["a"]] & VALUES[row["b"]]).state.value
    assert fv.truth_table(" & ".join(f"a{i}" for i in range(7))).code == INPUT_TOO_LARGE


# --- modal frames ----------------------------------------------------------------------------------------------------

WORLDS = {"w1": {"p": "N"}, "w2": {"p": "T"}, "w3": {"p": "B"}}
ACCESS = [["w1", "w2"], ["w1", "w3"], ["w2", "w2"], ["w3", "w3"]]


def test_necessity_meets_and_possibility_joins_over_the_accessible_worlds() -> None:
    box = fv.modal_evaluate("p", WORLDS, ACCESS, "w1", modality="necessarily")
    assert box.value == "B" and box.frame["successors"] == ["w2", "w3"] and box.code == CLAIM_CONFLICT
    diamond = fv.modal_evaluate("p", WORLDS, ACCESS, "w1", modality="possibly")
    assert diamond.value == "T" and diamond.completion_state == "qualified-draft"


def test_frame_properties_are_computed_with_a_witness_for_each_failure() -> None:
    worlds = ["a", "b", "c"]
    equivalence = [[u, v] for u, v in itertools.product(worlds, repeat=2)]
    fit = fv.check_frame(worlds, equivalence, "alethic")
    assert fit.frame["missing"] == [] and fit.completion_state == "qualified-draft"
    assert all(fit.frame["properties"][name]["holds"] for name in ("reflexive", "symmetric", "transitive", "serial"))
    chain = fv.check_frame(worlds, [["a", "b"], ["b", "c"]], "temporal")
    assert chain.frame["missing"] == ["transitive"] and chain.code == FRAME_UNFIT
    assert chain.frame["properties"]["transitive"]["witness"] == ["a", "b", "c"]


def test_a_planted_dead_end_in_a_deontic_frame_is_caught() -> None:
    """Deontic reasoning needs every world to see one (seriality, axiom D). At a dead end "p is obligatory" holds
    vacuously while "p is permitted" fails, so an obligation without permission would be reported as true."""
    worlds = {"w1": {"p": "T"}, "w2": {"p": "F"}}
    access = [["w1", "w2"]]
    unfit = fv.check_frame(list(worlds), access, "deontic")
    assert unfit.code == FRAME_UNFIT and unfit.frame["properties"]["serial"]["witness"] == ["w2"]
    obligation = fv.modal_evaluate("p", worlds, access, "w2", modality="necessarily", modal_frame="deontic")
    assert obligation.code == FRAME_UNFIT and obligation.completion_state == "blocked-escalated"
    plain = fv.modal_evaluate("p", worlds, access, "w2", modality="necessarily")
    assert plain.vacuous is True and plain.code == FRAME_UNFIT and plain.value == "T"
    assert fv.modal_evaluate("p", worlds, access, "w2", modality="possibly").value == "F"


def test_the_archives_modal_frame_names_are_kept() -> None:
    assert [m.value for m in fv.ModalFrame] == ["actual", "alethic", "epistemic", "deontic", "temporal"]
    assert fv.FRAME_CONDITIONS["deontic"] == ("serial",)


@pytest.mark.parametrize(("worlds", "access", "world", "modal_frame"), [
    (WORLDS, [["w1", "w9"]], "w1", ""),
    (WORLDS, ACCESS, "w9", ""),
    (WORLDS, ACCESS, "w1", "doxastic"),
    ({"w1": {"p": "T"}, "w2": {}}, [["w1", "w2"]], "w1", ""),
])
def test_bad_frames_are_refused(worlds, access, world: str, modal_frame: str) -> None:
    result = fv.modal_evaluate("p", worlds, access, world, modal_frame=modal_frame)
    assert result.code == INPUT_INVALID and result.completion_state == "blocked-escalated"


def test_no_four_valued_result_is_verified() -> None:
    for result in (fv.evaluate("p", {"p": "T"}), fv.truth_table("p"), fv.check_frame(["a"], [["a", "a"]], "alethic"),
                   fv.modal_evaluate("p", {"a": {"p": "T"}}, [["a", "a"]], "a")):
        assert result.completion_state == "qualified-draft" and result.warning_card["verified"] is False
