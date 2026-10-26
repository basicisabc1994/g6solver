# SPDX-License-Identifier: Apache-2.0
"""g6.cegis: synthesis runs candidates out of process and a result says only what its run compared (audit 027)."""
from __future__ import annotations

import pytest

from g6.cegis import InputDomain, RunnerTimeout, Sketch, SubprocessRunner, TableOracle, cegis
from g6.cegis.hole import HoleKind
from g6.invariants import check_envelope

LINEAR = "def f(x):\n    return x * ?? + ??\n"
X = InputDomain.ints(["x"], -10, 10)


def test_holes_of_every_kind_are_parsed() -> None:
    sketch = Sketch.from_src('def f(s):\n    return s + ??{"!","?"} if ??b else ??\n', lo=0, hi=3)
    assert [h.kind for h in sketch.holes] == [HoleKind.STR_ENUM, HoleKind.BOOL, HoleKind.INT]
    assert sketch.holes[0].choices == ("!", "?") and (sketch.holes[2].lo, sketch.holes[2].hi) == (0, 3)


@pytest.mark.parametrize("value", ["__import__('os')", 99, True, 1.5])
def test_a_control_value_a_hole_cannot_take_is_refused(value) -> None:
    """A control is checked before it is written into source: no code can ride in on a hole value."""
    with pytest.raises(ValueError, match="cannot take"):
        Sketch.from_src(LINEAR).instantiate({0: value, 1: 0})


@pytest.mark.parametrize("src", [
    "import os\ndef f(x):\n    return ??\n",
    "def f(x):\n    return __import__('os').getcwd()\n",
    "def f(x):\n    return ().__class__\n",
    "def f(x):\n    return open('x', 'w')\n",
    "def f(x):\n    return '{0.__class__.__base__}'.format(x)\n",
    "def f(x):\n    return str.format_map('{a.__class__}', {'a': x})\n",
])
def test_a_sketch_the_pre_filter_refuses_is_never_run(src: str) -> None:
    with pytest.raises(ValueError, match="refused"):
        Sketch.from_src(src)


def test_a_linear_function_is_found_and_named_by_its_boundary() -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X)
    assert result.status == "no_counterexample_in_domain" and result.control == {0: 3, 1: 1}
    assert result.boundary["compared"] == 21 and result.boundary["skipped"] == 0
    assert result.boundary["domain"]["exhaustive"] is True and result.boundary["model_calls"] == 0
    assert result.completion_state == "qualified-draft"
    envelope = result.to_envelope(request_id="q", run_id="r")
    assert check_envelope(envelope) == [] and envelope["warning_card"]["code"] == "G6_E_CEGIS_DOMAIN_BOUNDED"


def test_an_oracle_that_answers_nothing_is_inconclusive_never_a_pass() -> None:
    """CG-001: the archive skipped every input the oracle raised on, so a raising oracle verified anything."""
    def broken(x):  # noqa: ANN001, ANN202
        raise RuntimeError("no answer")

    result = cegis(Sketch.from_src(LINEAR), broken, domain=X)
    assert result.status == "inconclusive" and result.completion_state == "blocked-escalated"
    assert check_envelope(result.to_envelope()) == []


def test_inputs_the_oracle_does_not_answer_are_counted_as_skipped() -> None:
    oracle = TableOracle.of([({"x": x}, 2 * x) for x in range(0, 5)])
    result = cegis(Sketch.from_src(LINEAR), oracle, domain=X)
    assert result.status == "no_counterexample_in_domain" and result.control == {0: 2, 1: 0}
    assert (result.boundary["answered"], result.boundary["skipped"], result.boundary["compared"]) == (5, 16, 5)


def test_unsat_is_said_only_after_an_exhaustive_search() -> None:
    result = cegis(Sketch.from_src("def f(x):\n    return ??\n"), lambda x: x, domain=X)
    assert result.status == "unsat" and result.boundary["search"] == "exhaustive"
    hill = cegis(Sketch.from_src("def f(x):\n    return ??\n"), lambda x: x, domain=X, max_exhaustive=1)
    assert hill.status == "inconclusive" and hill.boundary["search"] == "adaptive"


def test_true_is_not_one() -> None:
    result = cegis(Sketch.from_src("def f(x):\n    return ??b\n"), lambda x: 1, domain=X)
    assert result.status == "unsat"


def test_bool_and_string_holes_are_synthesized() -> None:
    signs = cegis(Sketch.from_src("def f(x):\n    return x if ??b else -x\n"), lambda x: -x, domain=X)
    assert signs.status == "no_counterexample_in_domain" and signs.control == {0: False}
    words = InputDomain.of({"s": ["a", "bc", ""]})
    shout = cegis(Sketch.from_src('def f(s):\n    return s + ??{"!","?"}\n'), lambda s: s + "?", domain=words)
    assert shout.status == "no_counterexample_in_domain" and shout.control == {0: "?"}


def test_the_counterexample_budget_is_named_when_it_runs_out() -> None:
    result = cegis(Sketch.from_src(LINEAR), lambda x: 3 * x + 1, domain=X, max_iterations=1)
    assert result.status == "counterexample_budget_exhausted" and len(result.counterexamples) == 1


def test_a_candidate_that_never_returns_is_killed() -> None:
    """CG-005: the archive's in-process exec could not be pre-empted."""
    sketch = Sketch.from_src("def f(x):\n    while True:\n        x = x + ??\n    return x\n")
    runner = SubprocessRunner(timeout_s=2)
    with pytest.raises(RunnerTimeout):
        runner.evaluate(sketch, [{0: 1}], [{"x": 0}])
    assert cegis(sketch, lambda x: x, domain=X, runner=runner).status == "timeout"


def test_a_candidate_that_allocates_past_the_cap_does_not_answer() -> None:
    """CG-002: memory is capped on every platform (POSIX rlimit, Windows Job Object); a control shows the cap did it."""
    sketch = Sketch.from_src("def f(x):\n    return len('a' * (x * ??))\n")
    big = 700 * 1024 * 1024
    capped = SubprocessRunner(timeout_s=30, mem_mb=256).evaluate(sketch, [{0: 1}], [{"x": big}])
    assert "ok" not in capped[0][0]
    control = SubprocessRunner(timeout_s=30, mem_mb=2048).evaluate(sketch, [{0: 1}], [{"x": big}])
    assert control[0][0] == {"ok": big}


def test_a_sampled_domain_says_it_was_sampled() -> None:
    big = InputDomain.ints(["x", "y"], -100, 100, max_inputs=50, seed=7)
    assert big.exhaustive is False and len(big.inputs()) == 50 and big.inputs() == big.inputs()
    result = cegis(Sketch.from_src("def f(x, y):\n    return x * ?? + y\n"), lambda x, y: 2 * x + y, domain=big)
    assert result.status == "no_counterexample_in_domain"
    assert result.boundary["domain"] | {} == {"arguments": {"x": 201, "y": 201}, "size": 40401, "exhaustive": False,
                                              "drawn": 50, "seed": 7}
