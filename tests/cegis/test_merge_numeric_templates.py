# SPDX-License-Identifier: Apache-2.0
"""Fitting proposes; a separate held-out execution judges."""
import pytest

from g6.cegis import Sketch, fit_template
from g6.cegis import templates
from g6.distillation import Candidate, HoldoutCase

TRAIN = [HoldoutCase(f"t{i}", x, 2.5 * x - 3) for i, x in enumerate([-2, 0, 2, 4])]
HELD = [HoldoutCase("h1", 3, 4.5), HoldoutCase("h2", 7, 14.5)]
NEGATIVE = Candidate("def solve(x):\n    return 0\n")


def test_fitted_candidate_is_a_cegis_sketch_judged_by_distillation():
    # Removal: templates.py `report = admission.evaluate(...)` loses independent held-out judgement.
    pytest.importorskip("scipy")
    out = fit_template("linear", TRAIN, HELD, negative_control=NEGATIVE)
    assert isinstance(out.sketch, Sketch)
    assert out.report.passed == ("h1", "h2")
    assert out.report.negative_control_failed == ("h1", "h2")
    assert out.completion_state == "qualified-draft"


@pytest.mark.parametrize("case", [HoldoutCase("t0", 19, 44.5), HoldoutCase("renamed", -2, -8)])
def test_training_ids_and_renamed_training_inputs_are_not_holdout(case):
    # Removal: templates.py `if ... same(c.input, t.input) ...` permits renamed leakage (TEMPLATE_SYNTHESIS-01).
    out = fit_template("linear", TRAIN, [case], negative_control=NEGATIVE)
    assert out.completion_state == "blocked-escalated" and "held-out" in out.report.reason


def test_training_by_held_out_comparisons_are_capped_before_the_leak_scan(monkeypatch):
    # Removal: templates.py `if len(train) * len(held) > MAX_COMPARISONS:` runs a million-plus same() calls in process.
    calls = []
    monkeypatch.setattr(templates, "same", lambda a, b: calls.append(1) or False)
    train = [HoldoutCase(f"t{i}", i, 2.5 * i - 3) for i in range(10000)]
    held = [HoldoutCase(f"h{i}", 10000 + i, 0.0) for i in range(101)]
    out = fit_template("linear", train, held, negative_control=NEGATIVE)
    assert out.report.code == "G6_E_CEGIS_TEMPLATE_INPUT" and "1000000" in out.report.reason
    assert calls == [] and out.completion_state == "blocked-escalated"


def test_a_perfect_training_fit_fails_on_an_unseen_case():
    # Removal: templates.py `report = admission.evaluate(...)` misses overfitting (D1).
    pytest.importorskip("scipy")
    out = fit_template("linear", TRAIN, [HoldoutCase("h", 5, 999)], negative_control=NEGATIVE)
    assert out.report.failed == ("h",) and out.completion_state == "blocked-escalated"


@pytest.mark.parametrize("dependency", ["fit", "run", "judge"])
def test_dependency_failure_never_becomes_empirical_success(monkeypatch, dependency):
    # Removal: templates.py `except Exception as exc:` lets failed fitting/execution/judging escape (TEMPLATE_SYNTHESIS-02).
    pytest.importorskip("scipy")
    def broken(*a, **kw):
        raise RuntimeError("dependency failed")
    target, name = {"fit": (templates, "_fit"), "run": (templates, "run_batch"),
                    "judge": (templates.admission, "evaluate")}[dependency]
    monkeypatch.setattr(target, name, broken)
    out = fit_template("linear", TRAIN, HELD, negative_control=NEGATIVE)
    assert out.completion_state == "blocked-escalated" and "dependency failed" in out.report.reason


def test_missing_numeric_extra_is_an_honest_refusal(monkeypatch):
    # Removal: templates.py `except ImportError:` loses the named missing-extra refusal.
    import sys
    monkeypatch.setitem(sys.modules, "numpy", None)
    out = fit_template("linear", TRAIN, HELD, negative_control=NEGATIVE)
    assert out.report.code == "G6_E_CEGIS_TEMPLATE_EXTRA" and "g6[optim]" in out.report.reason


def test_negative_control_must_run_and_fail():
    # Removal: templates.py `report = admission.evaluate(...)` permits an unrun control to pass.
    pytest.importorskip("scipy")
    out = fit_template("linear", TRAIN, HELD, negative_control=Candidate("def nope(x):\n    return 0\n"))
    assert out.completion_state == "blocked-escalated"
    assert out.report.code == "G6_E_DISTILLATION_NEGATIVE_CONTROL_DID_NOT_RUN"


@pytest.mark.parametrize("family,train,held", [
    ("polynomial", [(0, 1), (1, 4), (2, 9), (3, 16)], [(4, 25)]),
    ("power", [(1, 2), (2, 8), (3, 18)], [(4, 32)]),
    ("clamp", [(-2, 0), (0.4, 0.4), (2, 1)], [(3, 1)]),
    ("exponential", [(0, 2), (1, 4), (2, 8)], [(3, 16)]),
    ("log", [(1, 0), (2, 0.6931471805599453), (4, 1.3862943611198906)], [(8, 2.0794415416798357)]),
    ("rational", [(0, 1), (1, 0.5), (2, 1/3), (3, 0.25)], [(4, 0.2)]),
    ("piecewise_linear", [(0, 0), (1, 1), (2, 4), (3, 6), (4, 8)], [(5, 10)]),
    ("affine_vec", [([0, 0], [1]), ([1, 0], [3]), ([0, 1], [4])], [([2, 3], [14])]),
    ("scaled_sigmoid", [(-1, 0.2689414213699951), (0, 0.5), (1, 0.7310585786300049)], [(2, 0.8807970779778823)]),
])
def test_numeric_families_use_the_same_held_out_path(family, train, held):
    # Removal: templates.py `_fit` family branch removes that numeric sketch's executable candidate.
    pytest.importorskip("scipy")
    out = fit_template(family, [HoldoutCase(f"t{i}", x, y) for i, (x, y) in enumerate(train)],
                       [HoldoutCase(f"h{i}", x, y) for i, (x, y) in enumerate(held)], negative_control=NEGATIVE)
    assert out.completion_state == "qualified-draft", out.report

@pytest.mark.parametrize("runs", [False, True])
def test_failed_training_execution_cannot_reach_judgement(monkeypatch, runs):
    # Removal: templates.py `if len(runs) != len(train) or any(...)` skips the required candidate execution.
    from g6.distillation.sandbox import Run
    monkeypatch.setattr(templates, "_fit", lambda *a: ("2.5 * x - 3", ""))
    monkeypatch.setattr(templates, "run_batch", lambda *a, **kw:
                        [Run("error", reason="child failed")] * len(TRAIN) if runs else [])
    out = fit_template("linear", TRAIN, HELD, negative_control=NEGATIVE)
    assert out.report.code == "G6_E_CEGIS_TEMPLATE_TRAINING"
    assert out.completion_state == "blocked-escalated"
