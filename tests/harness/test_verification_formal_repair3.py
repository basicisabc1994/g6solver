import itertools

import pytest

from g6.harness import verification_formal as formal, verification_search as search


OUTCOMES = ("broken", "holds", "operation_failed", "not_replayable")


def replay(outcome):
    value = {"outcome": outcome, "broken": ["nonnegative"], "why": "step refused"}
    return {"result": "failed" if outcome in ("broken", "operation_failed") else "passed",
            "detail": "replayed", "observations": [{"input": 0, "output": value}]}


@pytest.mark.parametrize("base,patch", list(itertools.product(OUTCOMES, repeat=2)))
def test_replay_comparison_distinguishes_refused_steps_from_violations(base, patch):
    found = formal._judge(1, "preserve", replay(patch), replay(base))
    if patch == "holds":
        expected = search.NOT_REFUTED
    elif patch == "not_replayable" or base == "not_replayable":
        expected = search.CHECK_ERROR
    elif base == "holds" or (base == "operation_failed" and patch == "broken"):
        expected = search.COUNTEREXAMPLE
    elif base == patch:
        expected = search.FAILS_ON_BASE_TOO
    else:
        expected = search.CHECK_ERROR
    assert found["result"] == expected
    if patch == 'operation_failed':
        assert 'break the invariants' not in found['detail']
    if base == "operation_failed":
        assert "break the invariants on the real code at the base commit" not in found["detail"]
    if base == "operation_failed" and patch == "broken":
        assert "base commit refused the step" in found["detail"]
        assert "patch ran it" in found["detail"] and "nonnegative" in found["detail"]
        assert not found["correspondence"].get("at_base_too")
