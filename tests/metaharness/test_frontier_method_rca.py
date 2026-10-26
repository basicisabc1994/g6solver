# SPDX-License-Identifier: Apache-2.0
"""Level 2 (frontier design §4.3): the method's own root causes, read from its tuning runs, each mapped to a method
edit; and the paired comparison that decides whether an edited method is better."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from g6.metaharness.method import Method, TuningRun
from g6.metaharness.method_rca import CAUSES, compare_methods, diagnose_method, edits_for


def _cycle(k, *, new=True, raced=None, attempts=4, duplicates=0):
    return {"cycle": k, "new_champion": new, "winner": "w" if new else None, "selection_calls": 100, "stopped": "",
            "strategies": {"reflect": {"proposed": 2, "code": "G6_E_META_PROPOSED", "attempts": attempts,
                                       "duplicates": duplicates}},
            "raced": raced if raced is not None else [
                {"candidate_digest": "a", "wins": 6, "losses": 1, "n": 30, "dropped_at_block": None, "fields": 2},
                {"candidate_digest": "b", "wins": 1, "losses": 3, "n": 10, "dropped_at_block": 1, "fields": 1}]}


def _run(cycles, *, outcome="activated", decision=None, success=None):
    return TuningRun("m", "w", "b", cycles=cycles, champion="a", outcome=outcome, decision=decision,
                     confirmation={"success": success or {}, "n": 60})


CLEAN = _run([_cycle(1), _cycle(2), _cycle(3)])


def test_a_clean_run_has_no_causes():
    assert diagnose_method([CLEAN]).causes == []


def test_underpowered_a_close_refusal_after_a_selection_win():
    run = _run([_cycle(1), _cycle(2)], outcome="refused",
               decision={"verdict": "refuse", "code": "G6_E_META_NO_GAIN", "reasons": []},
               success={"wins": 14, "losses": 5, "p_one_sided": 0.03, "boundary": 0.0125})
    assert [c.code for c in diagnose_method([run]).causes] == ["underpowered"]


def test_exhausted_later_cycles_add_nothing():
    run = _run([_cycle(1), _cycle(2, new=False), _cycle(3, new=False)])
    assert "exhausted" in [c.code for c in diagnose_method([run]).causes]


def test_repetitive_most_reflections_repeat():
    run = _run([_cycle(1), _cycle(2, attempts=8, duplicates=6)])
    assert "repetitive" in [c.code for c in diagnose_method([run]).causes]


def test_uncombined_no_multi_field_candidate_ever_raced():
    single = [{"candidate_digest": "a", "wins": 5, "losses": 1, "n": 30, "dropped_at_block": None, "fields": 1}]
    run = _run([_cycle(1, raced=single), _cycle(2, raced=single)])
    assert "uncombined" in [c.code for c in diagnose_method([run]).causes]


def test_misallocated_most_selection_spent_on_first_block_drops():
    raced = [{"candidate_digest": "a", "wins": 3, "losses": 1, "n": 10, "dropped_at_block": None, "fields": 2},
             *({"candidate_digest": f"x{i}", "wins": 0, "losses": 2, "n": 10, "dropped_at_block": 1, "fields": 1}
               for i in range(5))]
    run = _run([_cycle(1, raced=raced), _cycle(2)])
    assert "misallocated" in [c.code for c in diagnose_method([run]).causes]


def test_causes_read_ledger_rows_too():
    row = _run([_cycle(1), _cycle(2, new=False), _cycle(3, new=False)]).to_dict()
    assert "exhausted" in [c.code for c in diagnose_method([row]).causes]


@pytest.mark.parametrize("cause", CAUSES)
def test_every_edit_is_a_valid_different_method(cause):
    method = Method()
    edits = edits_for(cause, method)
    assert edits and all(isinstance(m, Method) and m.digest() != method.digest() for m in edits)


def test_a_method_cannot_name_alpha_the_looks_or_the_oracle():
    for field in ("alpha", "confirm_looks", "oracle", "looks"):
        with pytest.raises(ValidationError):
            Method(**{field: 1})


def test_compare_methods_accepts_a_consistent_gain_and_refuses_noise():
    gain = [(0.50, 0.58), (0.40, 0.47), (0.62, 0.66), (0.30, 0.41), (0.55, 0.60), (0.48, 0.55), (0.70, 0.72),
            (0.35, 0.44), (0.52, 0.59), (0.45, 0.49), (0.60, 0.63), (0.38, 0.46)]
    result = compare_methods(gain)
    assert result["accept"] and result["wins"] == 12 and result["ci95"][0] > 0
    noise = [(0.5, 0.52), (0.5, 0.47), (0.6, 0.61), (0.4, 0.38), (0.55, 0.55), (0.45, 0.47), (0.5, 0.49), (0.6, 0.6)]
    assert not compare_methods(noise)["accept"]
