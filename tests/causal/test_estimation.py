# SPDX-License-Identifier: Apache-2.0
"""g6.causal's estimate_effect on seeded synthetic data with a known effect: DoWhy's estimate recovers it where the
naive one does not, G6's own least-squares recomputation must match it, the refuters must not knock it down, and a
planted artefact, a planted wrong estimate and a planted warning are each caught. Without DoWhy, estimation refuses and
says what to install; the input is checked before DoWhy is needed."""
from __future__ import annotations

import importlib.util
import random
import subprocess
import sys
import warnings

import pytest

from g6.causal import estimation
from g6.causal.envelope import (
    CERTIFICATE_REFUTED,
    ENGINE_UNAVAILABLE,
    ESTIMATE,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    METHOD_NOT_APPLICABLE,
    NOT_IDENTIFIABLE,
    REFUTATION_INCOMPLETE,
    REFUTATION_OFF,
    REFUTED,
    TIME_LIMIT,
    UNKNOWN_NODE,
)
from g6.causal.estimation import MAX_ROWS, MAX_SIMULATIONS, estimate_effect

HAS_DOWHY = importlib.util.find_spec("dowhy") is not None
needs_dowhy = pytest.mark.skipif(not HAS_DOWHY, reason="DoWhy is not installed (pip install 'g6[causal]')")

CONFOUNDED = "digraph { Z -> T; Z -> Y; T -> Y }"
INSTRUMENT = "digraph { U [observed=no]; W -> T; U -> T; U -> Y; T -> Y }"
FRONTDOOR = "digraph { U [observed=no]; U -> T; U -> Y; T -> M -> Y }"
HIDDEN = "digraph { U [observed=no]; U -> T; U -> Y; T -> Y }"


def _linear(n: int = 2000, seed: int = 11) -> list[dict]:
    """T = 0.8 Z + e, Y = 2 T + 1.5 Z + e: the true effect is 2; leaving Z out biases it up to about 2.73."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        z = rng.gauss(0, 1)
        t = 0.8 * z + rng.gauss(0, 1)
        rows.append({"Z": z, "T": t, "Y": 2.0 * t + 1.5 * z + rng.gauss(0, 1)})
    return rows


def _binary(n: int = 2000, seed: int = 12) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        z = rng.gauss(0, 1)
        t = 0.8 * z + rng.gauss(0, 1) > 0
        rows.append({"Z": z, "T": t, "Y": 2.0 * t + 1.5 * z + rng.gauss(0, 1)})
    return rows


def _instrumented(n: int = 3000, seed: int = 13) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        w, u = rng.gauss(0, 1), rng.gauss(0, 1)
        t = w + u + rng.gauss(0, 1)
        rows.append({"W": w, "T": t, "Y": 3.0 * t + 2.0 * u + rng.gauss(0, 1)})
    return rows


def _mediated(n: int = 3000, seed: int = 14) -> list[dict]:
    """The effect of T on Y runs through M only: 1.2 * 0.5 = 0.6, with U confounding T and Y."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        u = rng.gauss(0, 1)
        t = u + rng.gauss(0, 1)
        m = 1.2 * t + rng.gauss(0, 1)
        rows.append({"T": t, "M": m, "Y": 0.5 * m + 2.0 * u + rng.gauss(0, 1)})
    return rows


def _artefact(n: int = 300, seed: int = 15) -> list[dict]:
    """T has no effect on Y; one far-out row makes it look as if it had an effect of about 1.8."""
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        z = rng.gauss(0, 1)
        rows.append({"Z": z, "T": 0.5 * z + rng.gauss(0, 1), "Y": z + rng.gauss(0, 1)})
    return rows + [{"Z": 0.0, "T": 60.0, "Y": 120.0}]


def _slope(rows: list[dict]) -> float:
    """The naive estimate: the least-squares slope of Y on T alone."""
    n = len(rows)
    mt, my = sum(r["T"] for r in rows) / n, sum(r["Y"] for r in rows) / n
    return sum((r["T"] - mt) * (r["Y"] - my) for r in rows) / sum((r["T"] - mt) ** 2 for r in rows)


@pytest.fixture
def no_dowhy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "dowhy" or m.startswith("dowhy.")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "dowhy", None)


# ---- known effects ---------------------------------------------------------------------------------------------------

@needs_dowhy
def test_linear_regression_recovers_the_effect_that_the_naive_estimate_misses() -> None:
    rows = _linear()
    assert abs(_slope(rows) - 2.0) > 0.4                       # leaving the confounder out is badly biased
    r = estimate_effect(CONFOUNDED, rows, "T", "Y", request_id="q", run_id="r")
    assert r.completion_state == "qualified-draft", (r.code, r.reason)
    assert abs(r.estimate - 2.0) < 0.1 and r.confidence_interval[0] < 2.0 < r.confidence_interval[1]
    assert r.answer["adjustment_set"] == ["Z"] and r.answer["route"] == "backdoor"
    assert r.answer["estimand"] == "E[Y | do(T=t)] = sum over Z of E[Y | T=t, Z] * P(Z)"
    assert r.recomputation["matched"] is True and r.recomputation["difference"] < 1e-8
    assert r.recomputation["model"] == "Y ~ 1 + T + Z"
    assert r.engines == ["pure_python", "dowhy"] and r.agreement["agreed"] is True
    card = r.warning_card
    assert card["code"] == ESTIMATE and "rests on the graph's assumptions" in card["message"]
    assert "data cannot confirm" in card["message"] and "least-squares" in card["message"]
    assert (r.request_id, r.run_id) == ("q", "r")


@needs_dowhy
def test_the_refuters_run_and_a_true_effect_survives_them() -> None:
    r = estimate_effect(CONFOUNDED, _linear(), "T", "Y")
    names = [x["refuter"] for x in r.refutations]
    assert names == ["placebo_treatment_refuter", "random_common_cause", "data_subset_refuter"]
    assert all(x["passed"] for x in r.refutations), [x["verdict"] for x in r.refutations]
    placebo = r.refutations[0]
    assert abs(placebo["new_effect"]) <= placebo["tolerance"] < 0.2 and "dowhy_p_value" in placebo


@needs_dowhy
@pytest.mark.parametrize("method", ["backdoor.propensity_score_stratification", "backdoor.propensity_score_weighting"])
def test_propensity_methods_on_a_binary_treatment(method: str) -> None:
    r = estimate_effect(CONFOUNDED, _binary(), "T", "Y", method=method, simulations=10)
    assert r.completion_state == "qualified-draft", (r.code, r.reason)
    assert abs(r.estimate - 2.0) < 0.3 and r.recomputation == {}
    assert "did not recompute" in r.warning_card["message"] and all(x["passed"] for x in r.refutations)


@needs_dowhy
def test_an_instrument_recovers_the_effect_through_an_unobserved_confounder() -> None:
    rows = _instrumented()
    assert abs(_slope(rows) - 3.0) > 0.4
    r = estimate_effect(INSTRUMENT, rows, "T", "Y", method="iv.instrumental_variable")
    assert r.completion_state == "qualified-draft", (r.code, r.reason)
    assert abs(r.estimate - 3.0) < 0.2 and r.answer["instruments"] == ["W"] and r.answer["route"] == "iv"
    # DoWhy 0.14's random-common-cause refuter fails on an instrumental-variable estimate with no backdoor set
    # (KeyError in get_backdoor_variables); it is reported as not run, never as a pass.
    by = {x["refuter"]: x for x in r.refutations}
    assert by["random_common_cause"]["ran"] is False and "KeyError" in by["random_common_cause"]["error"]
    assert by["placebo_treatment_refuter"]["passed"] is True and by["data_subset_refuter"]["passed"] is True
    assert r.warning_card["code"] == REFUTATION_INCOMPLETE and "random_common_cause" in r.warning_card["message"]


@needs_dowhy
def test_the_frontdoor_recovers_the_effect_and_refuters_that_cannot_reach_it_are_untested() -> None:
    """DoWhy 0.14 copies the two-stage estimator with only its outer estimand changed, so the placebo and the random
    common cause never reach the two stages: both come back exactly the estimate. G6 reports them as untested, so the
    placebo is not a false refutation and the random common cause not a pass."""
    r = estimate_effect(FRONTDOOR, _mediated(), "T", "Y", method="frontdoor.two_stage_regression")
    assert r.completion_state == "qualified-draft", (r.code, r.reason)
    assert abs(r.estimate - 0.6) < 0.15 and r.answer["mediators"] == ["M"]
    by = {x["refuter"]: x for x in r.refutations}
    for name in ("placebo_treatment_refuter", "random_common_cause"):
        assert by[name]["ran"] is True and by[name]["tested"] is False and by[name]["passed"] is None
        assert "never reached the estimator" in by[name]["verdict"]
    assert by["data_subset_refuter"]["tested"] is True and by["data_subset_refuter"]["passed"] is True
    assert r.warning_card["code"] == REFUTATION_INCOMPLETE


@needs_dowhy
def test_turning_refutation_off_is_said_on_the_card() -> None:
    r = estimate_effect(CONFOUNDED, _linear(), "T", "Y", refute=False)
    assert r.completion_state == "qualified-draft" and r.refutations == []
    assert r.warning_card["code"] == REFUTATION_OFF and "turned off" in r.warning_card["message"]


@needs_dowhy
def test_the_same_seed_gives_the_same_numbers() -> None:
    a = estimate_effect(CONFOUNDED, _binary(400), "T", "Y", method="backdoor.propensity_score_weighting", seed=3,
                        simulations=5)
    b = estimate_effect(CONFOUNDED, _binary(400), "T", "Y", method="backdoor.propensity_score_weighting", seed=3,
                        simulations=5)
    assert (a.estimate, a.confidence_interval) == (b.estimate, b.confidence_interval)
    assert [x["new_effect"] for x in a.refutations] == [x["new_effect"] for x in b.refutations]


# ---- planted failures ------------------------------------------------------------------------------------------------

@needs_dowhy
def test_an_effect_that_one_row_makes_is_refuted_and_keeps_its_numbers() -> None:
    rows = _artefact()
    r = estimate_effect(CONFOUNDED, rows, "T", "Y")
    assert r.completion_state == "blocked-escalated" and r.code == REFUTED, r.reason
    subset = next(x for x in r.refutations if x["refuter"] == "data_subset_refuter")
    assert subset["passed"] is False and subset["moved"] > subset["tolerance"]
    assert r.estimate is None and r.answer == {}
    kept = r.evidence[0]["refuted_estimate"]
    assert kept["value"] > 1.0 and len(kept["confidence_interval"]) == 2
    assert r.recomputation["matched"] is True              # the arithmetic was right; the effect was an artefact
    without = estimate_effect(CONFOUNDED, rows[:-1], "T", "Y")
    assert without.completion_state == "qualified-draft" and abs(without.estimate) < 0.2, without.reason


@needs_dowhy
def test_a_planted_wrong_estimate_is_refuted_by_the_recomputation(monkeypatch: pytest.MonkeyPatch) -> None:
    real = estimation._dowhy_estimate

    def off_by_half(*args, **kwargs):
        estimate = real(*args, **kwargs)
        estimate.value = estimate.value + 0.5
        return estimate

    monkeypatch.setattr(estimation, "_dowhy_estimate", off_by_half)
    r = estimate_effect(CONFOUNDED, _linear(), "T", "Y")
    assert r.completion_state == "blocked-escalated" and r.code == CERTIFICATE_REFUTED
    assert abs(r.recomputation["dowhy"] - r.recomputation["g6"] - 0.5) < 1e-9 and r.recomputation["matched"] is False
    assert r.estimate is None and r.evidence[0]["refuted_estimate"]["value"] == r.recomputation["dowhy"]
    assert r.refutations == []                              # a refuted number is not refuted again


@needs_dowhy
def test_a_warning_dowhy_raises_is_on_the_card_and_the_two_known_ones_are_not(monkeypatch) -> None:
    real = estimation._dowhy_estimate

    def warns(*args, **kwargs):
        warnings.warn("planted warning", RuntimeWarning, stacklevel=1)
        return real(*args, **kwargs)

    monkeypatch.setattr(estimation, "_dowhy_estimate", warns)
    r = estimate_effect(INSTRUMENT, _instrumented(), "T", "Y", method="iv.instrumental_variable", refute=False)
    assert r.completion_state == "qualified-draft", r.reason
    risks = " ".join(r.warning_card["risks"])
    assert "RuntimeWarning: planted warning" in risks and "warning" in r.warning_card["message"]
    assert "copy keyword" not in risks and "assumed unobserved" not in risks


@needs_dowhy
def test_estimating_with_deprecation_warnings_as_errors_answers(tmp_path) -> None:
    script = ("import random\nimport g6.causal as c\n"
              "rng = random.Random(1)\nrows = []\n"
              "for _ in range(300):\n"
              "    z = rng.gauss(0, 1); t = z + rng.gauss(0, 1)\n"
              "    rows.append({'Z': z, 'T': t, 'Y': 2 * t + z + rng.gauss(0, 1)})\n"
              "r = c.estimate_effect('digraph { Z -> T; Z -> Y; T -> Y }', rows, 'T', 'Y', simulations=3)\n"
              "assert r.completion_state == 'qualified-draft', (r.code, r.reason)\n")
    done = subprocess.run([sys.executable, "-W", "error::DeprecationWarning", "-c", script], cwd=tmp_path,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]


# ---- refusals, checked before DoWhy is needed ------------------------------------------------------------------------

def _rows(n: int = 30, **extra) -> list[dict]:
    rng = random.Random(5)
    return [{"Z": rng.gauss(0, 1), "T": rng.gauss(0, 1), "Y": rng.gauss(0, 1), **extra} for _ in range(n)]


@pytest.mark.parametrize(("call", "code", "says"), [
    (lambda: estimate_effect(HIDDEN, [{"T": i % 3, "Y": i} for i in range(30)], "T", "Y"), NOT_IDENTIFIABLE,
     "T <- U -> Y"),
    (lambda: estimate_effect(INSTRUMENT, [{"W": i % 2, "T": i % 3, "Y": i} for i in range(30)], "T", "Y"),
     METHOD_NOT_APPLICABLE, "iv.instrumental_variable"),
    (lambda: estimate_effect("digraph { Z -> T; Z -> Y }", _rows(), "T", "Y"), METHOD_NOT_APPLICABLE,
     "no directed path"),
    (lambda: estimate_effect(CONFOUNDED, _rows(), "T", "Y", method="backdoor.propensity_score_weighting"),
     INPUT_INVALID, "binary"),
    (lambda: estimate_effect(CONFOUNDED, _rows(), "T", "Y", method="backdoor.econml.dml.DML"), INPUT_INVALID,
     "method"),
    (lambda: estimate_effect(CONFOUNDED, [], "T", "Y"), INPUT_INVALID, "list of rows"),
    (lambda: estimate_effect(CONFOUNDED, "data.csv", "T", "Y"), INPUT_INVALID, "list of rows"),
    (lambda: estimate_effect(CONFOUNDED, _rows()[:5], "T", "Y"), INPUT_INVALID, "at least"),
    (lambda: estimate_effect(CONFOUNDED, _rows(Q=1.0), "T", "Y"), UNKNOWN_NODE, "'Q'"),
    (lambda: estimate_effect(CONFOUNDED, [{"T": 1.0, "Y": 2.0}] * 30, "T", "Y"), INPUT_INVALID, "'Z'"),
    (lambda: estimate_effect(HIDDEN, [{"T": i, "Y": i, "U": 0.0} for i in range(30)], "T", "Y"), INPUT_INVALID,
     "unobserved"),
    (lambda: estimate_effect(CONFOUNDED, [*_rows(29), {"Z": "high", "T": 1.0, "Y": 2.0}], "T", "Y"), INPUT_INVALID,
     "row 29, column 'Z'"),
    (lambda: estimate_effect(CONFOUNDED, [*_rows(29), {"Z": None, "T": 1.0, "Y": 2.0}], "T", "Y"), INPUT_INVALID,
     "None"),
    (lambda: estimate_effect(CONFOUNDED, [*_rows(29), {"Z": float("nan"), "T": 1.0, "Y": 2.0}], "T", "Y"),
     INPUT_INVALID, "nan"),
    (lambda: estimate_effect(CONFOUNDED, [*_rows(29), {"Z": 1.0, "T": 1.0}], "T", "Y"), INPUT_INVALID, "row 29"),
    (lambda: estimate_effect(CONFOUNDED, [*_rows(29), {"Z": True, "T": 1.0, "Y": 2.0}], "T", "Y"), INPUT_INVALID,
     "mixes"),
    (lambda: estimate_effect(CONFOUNDED, [{"Z": float(i), "T": 1.0, "Y": float(i)} for i in range(30)], "T", "Y"),
     INPUT_INVALID, "one value"),
    (lambda: estimate_effect("digraph { strata -> T; strata -> Y; T -> Y }", [
        {"strata": float(i), "T": float(i % 2), "Y": float(i)} for i in range(30)], "T", "Y"), INPUT_INVALID, "strata"),
    (lambda: estimate_effect(CONFOUNDED, _rows(MAX_ROWS + 1), "T", "Y"), INPUT_TOO_LARGE, str(MAX_ROWS)),
    (lambda: estimate_effect(CONFOUNDED, _rows(), "T", "Y", simulations=MAX_SIMULATIONS + 1), INPUT_TOO_LARGE,
     str(MAX_SIMULATIONS)),
    (lambda: estimate_effect(CONFOUNDED, _rows(), "T", "Y", simulations=0), INPUT_INVALID, "simulations"),
    (lambda: estimate_effect(CONFOUNDED, _rows(), "T", "Y", seed=-1), INPUT_INVALID, "seed"),
    (lambda: estimate_effect(CONFOUNDED, _rows(), "T", "Y", time_limit=1e-9), TIME_LIMIT, "time limit"),
])
def test_refusals_come_before_dowhy_and_are_blocked_with_their_code(call, code: str, says: str, no_dowhy) -> None:
    r = call()
    assert r.completion_state == "blocked-escalated" and r.code == code and says in r.reason, (r.code, r.reason)
    assert r.estimate is None and r.answer == {} and "dowhy" not in r.engines


def test_without_dowhy_estimation_refuses_and_says_what_to_install(no_dowhy) -> None:
    r = estimate_effect(CONFOUNDED, _rows(), "T", "Y")
    assert r.completion_state == "blocked-escalated" and r.code == ENGINE_UNAVAILABLE
    assert "pip install 'g6[causal]'" in r.warning_card["suggested_action"] and r.estimate is None
    assert r.engines == ["pure_python"]
