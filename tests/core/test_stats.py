# SPDX-License-Identifier: Apache-2.0
"""g6.core.stats: the normal, t, chi-squared and F distribution functions, upper tails and inverses, Welch's
two-sample t, the pooled two-proportion z (NIST 7.3.3) and one-sample sample sizes (NIST 7.2.2.2), on g6.core.special.

Each printed case comes from tests/core/data/stats_cases: the inputs, printed values and printed digits of the
frozen calc case files (written by a different author before this code), each with its citation, and is met at its
printed digits, half-up. Each function also meets a second route of its own (a closed form or an independent
computation), and refuses inputs outside its domain."""
from __future__ import annotations

import json
import math
import statistics
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from g6.core import stats

CASES = Path(__file__).resolve().parent / "data" / "stats_cases"


def _case(entry: str) -> dict:
    return json.loads((CASES / f"{entry}.json").read_text(encoding="utf-8"))


def _decimal(text: str) -> Decimal:
    """A printed number: an en dash as a minus, no dollar sign or thousands separator."""
    return Decimal(text.replace("–", "-").replace("$", "").replace(",", "").strip())


def _meets(actual: float, expected: dict) -> bool:
    """``actual`` rounded half-up at the case's printed digits is the printed value."""
    places = Decimal(1).scaleb(-expected["printed_digits"])
    return Decimal(repr(actual)).quantize(places, rounding=ROUND_HALF_UP) == _decimal(expected["value"])


def _percent(text: str) -> float:
    return float(_decimal(text.rstrip("%"))) / 100 if text.endswith("%") else float(_decimal(text))


# --- the printed cases -----------------------------------------------------------------------------------------------

def test_normal_meets_kreyszig_examples_1_and_3() -> None:
    # Fails if normal_cdf or normal_ppf in g6/core/stats.py returns a wrong value (for example the tails swapped).
    tail, inverse = _case("stats.normal")["cases"]
    z = float(tail["inputs"]["z"]["value"])
    assert _meets(stats.normal_cdf(z), tail["expected"]["cdf"])
    assert _meets(stats.normal_cdf(-z), tail["expected"]["cdf_at_minus_z"])
    assert _meets(stats.normal_sf(z), tail["expected"]["cdf_at_minus_z"])
    assert _meets(stats.normal_ppf(_percent(inverse["inputs"]["p"]["value"])), inverse["expected"]["z"])


def test_t_meets_kreyszig_table_a9_and_nist_1_3_5_3() -> None:
    # Fails if t_ppf or t_cdf in g6/core/stats.py computes the t distribution wrongly.
    kreyszig, nist = _case("stats.t")["cases"]
    p, df = float(kreyszig["inputs"]["p"]["value"]), float(kreyszig["inputs"]["df"]["value"])
    assert _meets(stats.t_ppf(p, df), kreyszig["expected"]["t"])
    assert round(stats.t_cdf(float(kreyszig["expected"]["t"]["value"]), df), 3) == p   # read as a tail case
    alpha, df = float(nist["inputs"]["alpha"]["value"]), float(nist["inputs"]["df"]["value"])
    assert _meets(stats.t_ppf(1 - alpha / 2, df), nist["expected"]["t_1_minus_alpha_over_2"])
    assert _meets(stats.t_ppf(1 - alpha, df), nist["expected"]["t_1_minus_alpha"])


def test_chi2_meets_kreyszig_table_a10() -> None:
    # Fails if chi2_ppf or chi2_cdf in g6/core/stats.py computes the chi-squared distribution wrongly.
    for case in _case("stats.chi2")["cases"]:
        p, df = float(case["inputs"]["p"]["value"]), float(case["inputs"]["df"]["value"])
        assert _meets(stats.chi2_ppf(p, df), case["expected"]["x"])
        assert round(stats.chi2_cdf(float(case["expected"]["x"]["value"]), df), 2) == p


def test_f_meets_kreyszig_example_6_and_table_a11_and_its_order_of_degrees_of_freedom() -> None:
    # Fails if f_ppf or f_cdf in g6/core/stats.py computes the F distribution wrongly or ignores the order of df.
    for case in _case("stats.f")["cases"]:
        p = _percent(case["inputs"]["p"]["value"])
        m, n = float(case["inputs"]["df_num"]["value"]), float(case["inputs"]["df_den"]["value"])
        assert _meets(stats.f_ppf(p, m, n), case["expected"]["x"])
        assert round(stats.f_cdf(float(case["expected"]["x"]["value"]), m, n), 2) == p
    assert round(stats.f_ppf(0.95, 4, 7), 2) != 6.09   # (4, 7) is not (7, 4)


def test_welch_meets_urdan_table_9_4() -> None:
    # Fails if welch_t in g6/core/stats.py uses a pooled variance, rounds the degrees of freedom or swaps the groups.
    case = _case("stats.welch_t")["cases"][0]
    given = {k: float(v["value"]) for k, v in case["inputs"].items()}
    found = stats.welch_t(given["mean1"], given["sd1"], int(given["n1"]), given["mean2"], given["sd2"],
                          int(given["n2"]))
    expected = case["expected"]
    assert _meets(found.t, expected["t"])
    assert _meets(found.df, expected["df"])
    assert _meets(found.se, expected["se_diff"])
    assert _meets(found.mean_difference, expected["mean_difference"])
    assert found.p_two_sided < 0.0005   # SPSS prints .000 for p below .0005


def test_sample_size_meets_nist_7_2_2_2_quantiles_and_its_t_iteration() -> None:
    # Fails if sample_size_normal or sample_size_t in g6/core/stats.py takes the wrong quantiles.
    first, second = _case("stats.power_two_sample")["related_cases"]
    alpha, beta = float(first["inputs"]["alpha"]["value"]), float(first["inputs"]["beta"]["value"])
    found = stats.sample_size_normal(alpha, beta, shift_in_sd=1.0, sides=1)
    assert _meets(found.quantile_alpha, first["expected"]["z_1_minus_alpha"])
    assert _meets(found.quantile_beta, first["expected"]["z_1_minus_beta"])
    assert math.ceil(found.n) == int(first["expected"]["n_rounded"]["value"])
    step = stats.sample_size_t(alpha, beta, shift_in_sd=1.0, sides=1, df=float(second["inputs"]["df"]["value"]))
    assert _meets(step.quantile_alpha, second["expected"]["t_1_minus_alpha_df8"])
    assert _meets(step.quantile_beta, second["expected"]["t_1_minus_beta_df8"])
    assert _meets(step.n, second["expected"]["n"])
    assert math.ceil(step.n) == int(second["expected"]["n_rounded"]["value"])


def test_nist_7_2_2_2_prints_8_567_from_its_rounded_quantiles_and_full_precision_gives_8_564() -> None:
    # The evidence for the lead's ruling: the page squares the rounded quantiles (1.645 + 1.282)^2; G6 computes from
    # the source's equation at full precision and never adjusts to the printed digits. Fails if n is computed from
    # rounded quantiles in g6/core/stats.py.
    first = _case("stats.power_two_sample")["related_cases"][0]
    found = stats.sample_size_normal(0.05, 0.10, shift_in_sd=1.0, sides=1)
    assert round(found.n, 3) == 8.564
    assert round((1.645 + 1.282) ** 2, 3) == float(first["expected"]["n"]["value"]) == 8.567


# --- second routes ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("z", [-8.0, -3.0, -1.16, -0.3, 0.0, 0.01, 1.0, 2.5, 6.0])
def test_the_normal_tail_agrees_with_erfc(z: float) -> None:
    # Fails if normal_cdf in g6/core/stats.py strays from 0.5 erfc(-z / sqrt 2) by more than 1e-12 of its size (a
    # lower tail computed as 1 - P, which reads 0 far out, fails at z = -8).
    assert math.isclose(stats.normal_cdf(z), 0.5 * math.erfc(-z / math.sqrt(2)), rel_tol=1e-12, abs_tol=0)
    assert math.isclose(stats.normal_sf(z), 0.5 * math.erfc(z / math.sqrt(2)), rel_tol=1e-12, abs_tol=0)


def test_far_upper_tails_keep_their_digits() -> None:
    # Fails if normal_cdf (z < 0), chi2_sf or f_sf in g6/core/stats.py is computed as 1 minus the lower tail: each of
    # these reads 0 that way.
    assert math.isclose(stats.normal_sf(9), 0.5 * math.erfc(9 / math.sqrt(2)), rel_tol=1e-12, abs_tol=0)
    assert math.isclose(stats.chi2_sf(100, 1), math.erfc(math.sqrt(50)), rel_tol=1e-12, abs_tol=0)
    assert math.isclose(stats.f_sf(1e12, 2, 2), 1 / (1 + 1e12), rel_tol=1e-12, abs_tol=0)
    assert stats.two_proportion_z(0, 3000, 300, 3000).p_two_sided > 0


def test_a_quantile_on_a_positive_support_stops_on_its_own_size() -> None:
    # Fails if chi2_ppf's bisection in g6/core/stats.py stops on an absolute width (1e-14) below 1.
    assert math.isclose(stats.chi2_ppf(1e-10, 2), -2 * math.log1p(-1e-10), rel_tol=1e-12, abs_tol=0)
    assert math.isclose(stats.chi2_ppf(1e-10, 1), math.pi / 2 * 1e-20, rel_tol=1e-9, abs_tol=0)
    assert math.isclose(stats.f_cdf(stats.f_ppf(1e-10, 2, 2), 2, 2), 1e-10, rel_tol=1e-9, abs_tol=0)


@pytest.mark.parametrize(("p", "within"), [(1e-10, 1e-12), (1e-6, 1e-12), (0.001, 1e-12), (0.123, 1e-12),
                                            (0.5, 1e-12), (0.9, 1e-12), (0.999999, 1e-10), (1 - 1e-10, 1e-6)])
def test_the_normal_inverse_agrees_with_the_standard_librarys(p: float, within: float) -> None:
    # Fails if normal_ppf in g6/core/stats.py strays from statistics.NormalDist().inv_cdf by more than the accuracy
    # its docstring states: the lower tail is computed directly, so 1e-12 there; 1e-6 near 1 - 1e-10, where Phi's
    # value is no finer than floating point's spacing below one.
    assert abs(stats.normal_ppf(p) - statistics.NormalDist().inv_cdf(p)) < within


@pytest.mark.parametrize("x", [0.0, 0.01, 1.0, 5.99, 40.0, 700.0])
def test_the_chi2_tail_at_two_degrees_of_freedom_is_exp_of_minus_half_x(x: float) -> None:
    # Fails if chi2_cdf or chi2_sf in g6/core/stats.py strays from the closed form 1 - exp(-x/2) at 2 df by more
    # than 1e-12 of its size (an upper tail computed as 1 - P reads 0 at x = 700).
    assert math.isclose(stats.chi2_sf(x, 2), math.exp(-x / 2), rel_tol=1e-12, abs_tol=0)
    assert math.isclose(stats.chi2_cdf(x, 2), -math.expm1(-x / 2), rel_tol=1e-12, abs_tol=0)


@pytest.mark.parametrize("t", [-1e6, -12.7, -1.0, 0.0, 0.5, 3.0, 63.66])
def test_t_at_one_degree_of_freedom_is_the_cauchy_closed_form(t: float) -> None:
    # Fails if t_cdf in g6/core/stats.py strays from 1/2 + atan(t)/pi at 1 df.
    assert abs(stats.t_cdf(t, 1) - (0.5 + math.atan(t) / math.pi)) < 1e-13


@pytest.mark.parametrize("x", [0.0, 0.25, 1.0, 19.0, 1000.0])
def test_f_with_two_and_two_degrees_of_freedom_is_x_over_one_plus_x(x: float) -> None:
    # Fails if f_cdf in g6/core/stats.py strays from x / (1 + x) at (2, 2) df.
    assert abs(stats.f_cdf(x, 2, 2) - x / (1 + x)) < 1e-13


@pytest.mark.parametrize(("q", "args"), [(stats.t_ppf, (7.0,)), (stats.chi2_ppf, (3.5,)), (stats.f_ppf, (3.0, 11.0))])
@pytest.mark.parametrize("p", [1e-10, 0.025, 0.5, 0.95, 1 - 1e-10])
def test_each_inverse_inverts_its_distribution_function(q, args, p: float) -> None:
    # Fails if a quantile's bisection in g6/core/stats.py stops short of its root.
    cdf = {stats.t_ppf: stats.t_cdf, stats.chi2_ppf: stats.chi2_cdf, stats.f_ppf: stats.f_cdf}[q]
    assert abs(cdf(q(p, *args), *args) - p) < 1e-11


def test_welch_reduces_to_the_pooled_degrees_of_freedom_only_when_the_groups_match() -> None:
    # The falsifier the case file asks for (Urdan's case cannot separate Welch's df from the pooled df). Fails if
    # welch_t in g6/core/stats.py returns n1 + n2 - 2 instead of the Welch-Satterthwaite formula.
    same = stats.welch_t(10.0, 2.0, 12, 9.0, 2.0, 12)
    assert abs(same.df - 22) < 1e-12
    differ = stats.welch_t(10.0, 1.0, 10, 9.0, 4.0, 12)
    a, b = 1.0 / 10, 16.0 / 12
    assert abs(differ.df - (a + b) ** 2 / (a * a / 9 + b * b / 11)) < 1e-12
    assert abs(differ.df - 20) > 7   # the pooled test's n1 + n2 - 2 is 20; Welch's is about 12.6
    assert abs(differ.p_two_sided - 2 * stats.t_sf(abs(differ.t), differ.df)) < 1e-15
    assert stats.welch_t(9.0, 4.0, 12, 10.0, 1.0, 10).t == -differ.t


def test_two_proportion_is_symmetric_zero_at_equal_proportions_and_its_square_is_pearsons_chi2() -> None:
    # Fails if two_proportion_z in g6/core/stats.py does not pool the proportions as NIST 7.3.3 states.
    found = stats.two_proportion_z(32, 38, 39, 44)
    assert stats.two_proportion_z(39, 44, 32, 38).z == -found.z
    assert stats.two_proportion_z(5, 20, 10, 40).z == 0
    table = [[32, 6], [39, 5]]
    rows, cols, total = [sum(r) for r in table], [sum(c) for c in zip(*table)], 82
    pearson = sum((table[i][j] - rows[i] * cols[j] / total) ** 2 / (rows[i] * cols[j] / total)
                  for i in range(2) for j in range(2))
    assert abs(found.z ** 2 - pearson) < 1e-12
    assert abs(found.pooled - 71 / 82) < 1e-15
    assert abs(found.p_two_sided - 2 * stats.normal_sf(abs(found.z))) < 1e-15


# --- the domain ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("function", "args"), [
    (stats.normal_ppf, (0.0,)), (stats.normal_ppf, (1.0,)), (stats.normal_ppf, (1e-12,)), (stats.normal_cdf, (math.nan,)),
    (stats.t_cdf, (1.0, 0.0)), (stats.t_ppf, (0.5, -1.0)), (stats.t_cdf, (math.inf, 3.0)),
    (stats.chi2_cdf, (-0.1, 3.0)), (stats.chi2_ppf, (1.0, 3.0)), (stats.f_cdf, (1.0, 0.0, 3.0)),
    (stats.f_ppf, (0.5, 3.0, 0.0)), (stats.f_cdf, (-1.0, 2.0, 2.0)),
    (stats.welch_t, (1.0, 1.0, 1, 2.0, 1.0, 5)), (stats.welch_t, (1.0, 0.0, 5, 2.0, 1.0, 5)),
    (stats.welch_t, (1.0, 1.0, 5.5, 2.0, 1.0, 5)),
    (stats.two_proportion_z, (5, 4, 1, 4)), (stats.two_proportion_z, (0, 10, 0, 10)),
    (stats.two_proportion_z, (10, 10, 10, 10)), (stats.two_proportion_z, (1.5, 4, 1, 4)),
    (stats.sample_size_normal, (0.0, 0.1, 1.0, 2)), (stats.sample_size_normal, (0.05, 1.0, 1.0, 2)),
    (stats.sample_size_normal, (0.05, 0.1, 0.0, 2)), (stats.sample_size_normal, (0.05, 0.1, 1.0, 3)),
    (stats.sample_size_t, (0.05, 0.1, 1.0, 1, 0.0)),
])
def test_inputs_outside_the_domain_raise_and_are_never_clipped(function, args) -> None:
    # Fails if a domain check in g6/core/stats.py is removed (the function would clip, or answer nan).
    with pytest.raises(ValueError):
        function(*args)
