# SPDX-License-Identifier: Apache-2.0
"""A floating-point hole ``??f`` (agentic override kernel design 10.8): with no candidate grid cegis refuses with the
limit it declares, ``G6_E_CEGIS_HOLE_UNSUPPORTED``; the substitutable part is ``g6.cegis.hole:float_grid``; the runner
seam runs candidates in process only inside the kernel's confined child; the block op ``synthesize`` takes a table."""
from __future__ import annotations

import pytest

import g6
import g6.cegis as cegis_package
from g6.cegis import runner
from g6.cegis.hole import Hole, HoleKind, HoleUnsupported, float_grid, valid_float_grid
from g6.cegis.sketch import Sketch
from g6.invariants import check_envelope

FLOAT = "def f(x):\n    return x * ??f\n"
CASES = [{"input": {"x": 1}, "output": 0.5}, {"input": {"x": 2}, "output": 1.0}, {"input": {"x": 4}, "output": 2.0}]


def test_a_float_hole_with_no_candidate_grid_refuses_with_the_declared_limit() -> None:
    with pytest.raises(HoleUnsupported, match=r"^\[G6_E_CEGIS_HOLE_UNSUPPORTED\]"):
        Sketch.from_src(FLOAT)
    with pytest.raises(HoleUnsupported):
        float_grid(0, -32, 32, FLOAT)
    assert "G6_E_CEGIS_HOLE_UNSUPPORTED" in cegis_package.__g6_limits__


def test_the_float_grid_is_a_declared_part_of_the_synthesize_op_and_the_verifiers_are_declared() -> None:
    parts = cegis_package.__g6_agentic_parts__
    assert parts["float_grid"]["functions"] == ("g6.cegis.hole:float_grid",)
    assert parts["float_grid"]["ops"] == ("synthesize",)
    assert {"g6.cegis.verify", "g6.cegis.z3_verifier", "g6.cegis.loop:_verifier_says"} <= set(
        cegis_package.__g6_verifiers__)


@pytest.mark.parametrize(("value", "why"), [
    ((0.5, 1.0), ""),
    ((), "at least one"),
    ((0.5, 0.5), "twice"),
    ((100.0,), "outside"),
    ((float("inf"),), "finite"),
    ((float("nan"),), "finite"),
    ((1,), "float"),
    ((True,), "float"),
    (("0.5",), "float"),
    (tuple(float(i) / 8 for i in range(65)), "at most 64"),
])
def test_a_proposed_grid_is_checked_as_floats_within_the_holes_bounds(value, why) -> None:
    said = valid_float_grid(value, 0, -32, 32, FLOAT)
    assert (said == "") if not why else (why in said)


def test_a_float_hole_takes_only_a_float_of_its_grid() -> None:
    hole = Hole(id=0, kind=HoleKind.FLOAT, choices=(0.25, 0.5))
    assert hole.accepts(0.5) and hole.all_values == [0.25, 0.5]
    assert not hole.accepts(1) and not hole.accepts(0.75) and not hole.accepts(True) and not hole.accepts("0.5")


def test_a_float_grid_given_by_the_part_fills_the_hole_with_a_float_literal(monkeypatch) -> None:
    import g6.cegis.hole as hole

    monkeypatch.setattr(hole, "float_grid", lambda i, lo, hi, src: (0.25, 0.5))
    sketch = Sketch.from_src(FLOAT)
    assert sketch.holes[0].kind == HoleKind.FLOAT and sketch.holes[0].choices == (0.25, 0.5)
    assert sketch.instantiate({0: 0.5}) == "def f(x):\n    return x * 0.5\n"
    with pytest.raises(ValueError, match="cannot take"):
        sketch.instantiate({0: 0.75})


def test_g6s_own_process_never_runs_candidates_in_process() -> None:
    """The in-process runner is the kernel child's seam only (design 10.2): here it refuses, and the default is the
    out-of-process runner."""
    assert isinstance(runner.default_runner(), runner.SubprocessRunner)
    assert not isinstance(runner.default_runner(), runner.InProcessRunner)
    with pytest.raises(runner.RunnerRefused, match="G6_E_ISOLATION_UNAVAILABLE"):
        runner.InProcessRunner().evaluate(Sketch.from_src("def f(x):\n    return x\n"), [{}], [{"x": 1}])


def test_the_block_refuses_a_float_hole_with_the_limit_code_and_runs_nothing() -> None:
    reply = g6.invoke("cegis", "synthesize", {"sketch": FLOAT, "cases": CASES}, allow=("cegis.synthesize",))
    assert reply["ok"] is False and reply["completion_state"] == "blocked-escalated"
    assert reply["warning_card"]["code"] == "G6_E_CEGIS_HOLE_UNSUPPORTED"
    assert check_envelope(reply) == []


def test_synthesize_is_sensitive() -> None:
    reply = g6.invoke("cegis", "synthesize", {"sketch": FLOAT, "cases": CASES})
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED"


def test_the_recheck_refuses_a_program_that_is_not_the_sketch_filled_with_the_control() -> None:
    from g6.cegis.block import recheck

    said = recheck({"sketch": FLOAT, "cases": CASES}, {"control": {"0": 0.5}, "program": "def f(x):\n    return 1\n"})
    assert said["passed"] is False and said["status"] == "mismatch"
    said = recheck({"sketch": FLOAT, "cases": CASES}, {"control": {"0": 1e9}, "program": ""})
    assert said["passed"] is False and said["status"] == "mismatch"

def test_a_sketch_the_door_refuses_is_invalid_input_before_any_float_grid_is_asked(monkeypatch) -> None:
    import g6.cegis.hole as hole

    asked: list = []
    monkeypatch.setattr(hole, "float_grid", lambda *args: asked.append(args) or (0.5,))
    bad = "def f(x):\n    return x.__private_token_q7 * ??f\n"
    with pytest.raises(ValueError, match="the sketch is refused") as refused:
        Sketch.from_src(bad)
    assert not isinstance(refused.value, HoleUnsupported) and asked == []
    reply = g6.invoke("cegis", "synthesize", {"sketch": bad, "cases": CASES}, allow=("cegis.synthesize",))
    assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION" and asked == []


def _within(seconds: float, target):
    """``target()``'s value when it returns within ``seconds``; a failure that names where it is stuck otherwise."""
    import sys
    import threading
    import traceback

    box: dict = {}
    worker = threading.Thread(target=lambda: box.setdefault("value", target()), daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        frames = traceback.extract_stack(sys._current_frames()[worker.ident])[-3:]
        raise AssertionError(f"did not return within {seconds}s: " + " | ".join(f"{f.name}:{f.lineno}" for f in frames))
    return box.get("value")


def test_a_domain_with_repeated_values_is_drawn_from_its_distinct_values_and_returns() -> None:
    from g6.cegis.verify import InputDomain

    domain = InputDomain.of({"x": [0] * 2001})
    assert domain.size == 1 and domain.exhaustive
    assert _within(5.0, domain.inputs) == [{"x": 0}]
    wide = InputDomain.of({"x": list(range(1000)) * 3, "y": [True, False, 1, 0, True]}, max_inputs=2000)
    assert wide.size == 1000 * 4 and not wide.exhaustive
    drawn = _within(5.0, wide.inputs)
    assert len(drawn) == 2000 and len({(d["x"], type(d["y"]), d["y"]) for d in drawn}) == 2000


def test_sampling_a_domain_is_bounded_in_attempts() -> None:
    from g6.cegis.verify import InputDomain

    class Same(InputDomain):   # a domain whose draws all collide, as repeated values did
        def __init__(self) -> None:
            super().__init__(values=(("x", (0,) * 3000),), max_inputs=2000)

    drawn = _within(10.0, Same().inputs)
    assert drawn == [{"x": 0}]


@pytest.mark.parametrize("params", [
    {"sketch": "def f(x, y):\n    return ??\n", "cases": [{"input": {"x": float("nan"), "y": float("nan")}, "output": 1}] * 46},
    {"sketch": "def f(x):\n    return x * ??\n", "cases": [{"input": {"x": float("inf")}, "output": 1}]},
    {"sketch": "def f(x):\n    return x * ??\n", "cases": [{"input": {"x": 1}, "output": float("nan")}]},
    {"sketch": "def f(x):\n    return x * ??\n", "cases": [{"input": {"x": 1}, "output": 1}], "domain": {"x": [float("nan")]}},
    {"sketch": "def f(x):\n    return x * ??\n", "cases": [{"input": {"x": 0}, "output": 0}], "domain": {"x": [0] * 2001}},
])
def test_the_block_refuses_values_that_are_not_finite_and_returns_on_a_repeated_domain(params) -> None:
    reply = _within(20.0, lambda: g6.invoke("cegis", "synthesize", params, allow=("cegis.synthesize",)))
    assert check_envelope(reply) == []
    if "domain" in params and params["domain"]["x"][0] == 0:
        assert reply["warning_card"].get("code") != "G6_E_INPUT_VALIDATION"
    else:
        assert reply["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
