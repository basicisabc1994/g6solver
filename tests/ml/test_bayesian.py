# SPDX-License-Identifier: Apache-2.0
"""Source worked examples and failure checks for the Bayesian member."""
import math
import random
from statistics import NormalDist

import pytest


def api():
    from g6.ml.adapt_bayesian import block, diagnostics, exact, models, numerics
    return block, diagnostics, exact, models, numerics


def call(op, **params):
    block, *_ = api()
    return block.AdaptBayesianBlock().infer(block.BayesianInput(op=op, **params)).unwrap()


BETA = {"kind": "beta_binomial", "alpha": 4.8, "beta": 19.2, "trials": 100}
NORMAL = {"kind": "normal_known", "mean": 30, "variance": 16, "sigma": 2}


def test_bolstad_14_beta_moments_not_garbled_table():
    # Removal target: exact.update: `a, b = model.alpha + total, model.beta + n * model.trials - total`.
    out = call("beta_binomial", model=BETA, observations=[26])
    p = out["posterior"]
    assert p["parameters"] == {"alpha": 30.8, "beta": 93.2}
    assert p["mean"] == pytest.approx(30.8 / 124)
    assert p["variance"] == pytest.approx(30.8 * 93.2 / (124**2 * 125))
    assert p["sd"] == pytest.approx(math.sqrt(p["variance"]))
    out = call("beta_binomial", model={**BETA, "alpha": 1, "beta": 1}, observations=[26])
    assert out["posterior"]["parameters"] == {"alpha": 27, "beta": 75}
    assert out["completion_state"] == "qualified-draft"


def test_bolstad_17_improper_uniform_and_table_10_2():
    # Removal target: exact.py:21 `a0, b0 = (1., 0.) if model.prior == "positive_uniform" else (model.shape, model.rate)`; block.py:91 `if lm is None:`.
    spec = {"kind": "gamma_poisson", "prior": "positive_uniform"}
    out = call("gamma_poisson", model=spec, observations=[3, 2, 0, 8, 2, 4, 6, 1])
    p = out["posterior"]
    assert p["parameters"] == {"shape": 27, "rate": 8}
    assert p["mean"] == 3.375
    assert p["median"] == pytest.approx(3.333, abs=0.0005)
    assert p["mode"] == 3.25
    assert p["sd"] == pytest.approx(.6495, abs=.00005)
    assert out["log_marginal_likelihood"] is None
    out = call("compare", model=spec, alternatives=[{"kind": "gamma_poisson", "shape": 1, "rate": 1}],
               observations=[3, 2, 0, 8, 2, 4, 6, 1])
    assert out["completion_state"] == "blocked-escalated"


def test_adapt_bayesian_01_bolstad_20_noise_is_separate():
    # Removal target: exact.update: `noise = model.sigma**2` (ADAPT_BAYESIAN-01).
    # Any 12 observations of this mean have the same known-variance posterior.
    out = call("normal_known", model=NORMAL, observations=[31, 33] * 6)
    p = out["posterior"]
    assert p["variance"] == pytest.approx(1 / 3.0625)
    assert p["mean"] == pytest.approx((30 / 16 + 12 * 32 / 4) / 3.0625)
    noisy = call("normal_known", model={**NORMAL, "sigma": 10}, observations=[22, 42] * 6)
    assert noisy["posterior"]["sd"] > p["sd"]
    assert p["interval"]["mass"] == .95
    assert p["interval"]["method"] == "equal-tailed"


def test_barber_9_1_4_and_dlmf_known_values():
    # Removal target: numerics.py:62 `return min(1.0, max(0.0, front * _beta_fraction(x, a, b) / a))`.
    *_, num = api()
    assert num.beta_cdf(.5, 3, 9) == pytest.approx(1981 / 2048, abs=1e-13)
    assert num.gamma_cdf(2, 1) == pytest.approx(1 - math.exp(-2), abs=1e-13)
    assert 1 - num.gamma_cdf(7, 6) == pytest.approx(.3007082762, abs=5e-11)
    for a in (.7, 3, 10):
        assert num.beta_cdf(.5, a, a) == pytest.approx(.5, abs=1e-13)
    assert num.beta_cdf(.4, 2.5, 1) == pytest.approx(.4**2.5, abs=1e-13)
    for a, b in ((.7, 3), (3, 9), (30.8, 93.2)):
        for q in (.025, .5, .975):
            assert num.beta_cdf(num.beta_quantile(q, a, b), a, b) == pytest.approx(q, abs=1e-10)
    for q in (.025, .5, .975):
        assert num.gamma_cdf(num.gamma_quantile(q, 27), 27) == pytest.approx(q, abs=1e-10)


def test_vehtari_equations_1_to_4_hand_computed():
    # Removal target: diagnostics._rhat: `return math.sqrt(((n-1)*w/n + b/n) / w)`.
    _, diag, *_ = api()
    # Four length-2 split chains: means 1,3,2,4; W=2, B=10/3, var+=8/3.
    assert diag.split_rhat([[0, 2, 2, 4], [1, 3, 3, 5]]) == pytest.approx(math.sqrt(4 / 3))


def test_vehtari_equation_10_weights_each_chains_autocorrelation_by_its_variance():
    # Removal target: diagnostics.ess: `fmean(c[t] for c in covs)*n/(n-1)` (s2_m times rho_t,m, equation 10).
    _, diag, *_ = api()
    # M=2, N=6, autocovariances with divisor N: W=61/60, B=25/12, var+=43/36; rho_1..5 = -17/86, 48/215, -59/430,
    # 11/43, 15/86; paired sums 69/86, 37/430 and 37/86 (monotone: 37/430), positive to the last lag, so no even
    # lag follows; tau = -1 + 2(69/86 + 37/430 + 37/430) = 204/215 and ESS = 12/tau = 215/17.
    assert diag.ess([[0, 0, 0, 2, 0, 0], [1, 0, 2, 1, 3, 0]]) == pytest.approx(215 / 17)


def test_vehtari_page_8_averages_the_odd_lag_sum_with_the_next_even_lag():
    # Removal target: diagnostics.ess: `+ (rho[even] if even < n else 0.)` (the average with the next even lag, p. 8).
    _, diag, *_ = api()
    # M=2, N=8: W=39/28, B=9/4, var+=3/2; rho_1..4 = 17/112, -5/56, 65/336, -5/28 and rho_5 = -41/336; paired sums
    # 129/112, 5/48, then -101/336, so truncation at lag 3: the odd-lag sum -1 + 2(129/112 + 5/48) = 127/84; with the
    # next even lag the average is 127/84 + rho_4 = 4/3, and ESS = 16/tau = 12 (the odd-lag sum alone gives 10.58).
    assert diag.ess([[3, 1, 2, 3, 0, 0, 1, 0], [3, 1, 0, 2, 3, 2, 3, 2]]) == pytest.approx(12)


def test_adapt_bayesian_02_measured_diagnostics_and_folded_heavy_tails(monkeypatch):
    # Removal target: diagnostics.assess: `rhat = max(rank_rhat, folded_rhat)` (ADAPT_BAYESIAN-02).
    _, diag, *_ = api()
    rng = random.Random(112)
    well = [[rng.gauss(0, 1) for _ in range(1600)] for _ in range(4)]
    assert diag.assess(well)["converged"]
    shifted = [[v + 4 * k for v in c] for k, c in enumerate(well)]
    assert diag.assess(shifted)["rhat"] > 1.01
    assert not diag.assess(shifted)["converged"]
    # Same symmetric Cauchy quantiles, permuted independently, differing only in scale.
    heavy = []
    for scale in (1, 1, 12, 12):
        c = [scale * math.tan(math.pi * ((i + .5) / 1600 - .5)) for i in range(1600)]
        rng.shuffle(c)
        heavy.append(c)
    d = diag.assess(heavy)
    assert d["rank_rhat"] < 1.01
    assert d["folded_rhat"] > 1.01
    assert not d["converged"]
    assert not diag.assess([[1.] * 800 for _ in range(4)])["converged"]

    # G6-constructed case applying Vehtari section 4.2, not printed example data.
    # Each split half is symmetric with identical outer 5% tails. One chain's
    # central 90% has eight times the scale, so tail ESS cannot hide the R-hat bug.
    scale_rng = random.Random(4202)
    different_scales = []
    for scale in (1, 1, 1, 8):
        chain = []
        for _ in range(2):
            half = [sign * (10 + (i + .5)/4) for i in range(40) for sign in (-1, 1)]
            half += [sign * scale * (i + .5)/360 for i in range(360) for sign in (-1, 1)]
            scale_rng.shuffle(half)
            chain.extend(half)
        different_scales.append(chain)
    scale_diagnostics = diag.assess(different_scales)
    assert scale_diagnostics["rank_rhat"] < 1.01
    assert scale_diagnostics["folded_rhat"] > 1.01
    assert scale_diagnostics["bulk_ess"] > 400
    assert scale_diagnostics["tail_ess"] > 400
    assert scale_diagnostics["rhat"] == scale_diagnostics["folded_rhat"]
    assert not scale_diagnostics["converged"]

    # Replace only the draw producer: real diagnostics and the reply gate run.
    from g6.ml.adapt_bayesian import sampling
    monkeypatch.setattr(sampling, "mh", lambda *a, **k: {"draws": different_scales, "backend": "mh"})
    out = call("sample", model=NORMAL, observations=[31, 33]*6, seed=4202, draws=1600, chains=4)
    assert out["code"] == "G6_E_ML_NOT_CONVERGED"
    assert out["completion_state"] == "blocked-escalated"
    assert "posterior" not in out
    measured = next(e for e in out["evidence"] if e["kind"] == "mcmc_diagnostics")
    assert measured["rank_rhat"] < 1.01 < measured["folded_rhat"]
    assert measured["rhat"] == measured["folded_rhat"]
    assert measured["bulk_ess"] > 400 and measured["tail_ess"] > 400


def test_d1_nonconverged_sampler_refused():
    # Removal target: sampling.checked: `if not found["converged"] or result.get("divergences", 0) != 0:` (D1).
    out = call("sample", model=NORMAL, observations=[31, 33] * 6, seed=91,
               draws=600, warmup=0, proposal_scale=1e-9)
    assert out["code"] == "G6_E_ML_NOT_CONVERGED"
    assert out["completion_state"] == "blocked-escalated"
    assert "posterior" not in out


def test_marginal_likelihood_known_integrals():
    # Removal target: exact.py:18 `lm += log_beta(a, b) - log_beta(model.alpha, model.beta)`.
    # Flat beta-binomial marginal is 1/(n+1), any count.
    out = call("beta_binomial", model={**BETA, "alpha": 1, "beta": 1}, observations=[26])
    assert out["log_marginal_likelihood"] == pytest.approx(-math.log(101))
    # One normal observation marginal: N(prior mean, prior variance + noise variance).
    out = call("normal_known", model=NORMAL, observations=[32])
    assert out["log_marginal_likelihood"] == pytest.approx(math.log(NormalDist(30, math.sqrt(20)).pdf(32)))
    out = call("gamma_poisson", model={"kind": "gamma_poisson", "shape": 1, "rate": 1}, observations=[0])
    assert out["log_marginal_likelihood"] == pytest.approx(-math.log(2))

def test_dataset_missing_values_and_claims_fail_closed():
    # Removal target: block.py:36 `model_config = ConfigDict(extra="forbid", allow_inf_nan=False)`.
    block, *_ = api()
    from pydantic import ValidationError
    from g6.ml._types import DatasetSpec
    dataset = DatasetSpec(rows=[{"y": 31}, {"y": 33}], target="y", features=["y"], task="regression")
    out = call("normal_known", model=NORMAL, dataset=dataset, request_id="r", run_id="u")
    assert out["request_id"] == "r" and out["run_id"] == "u"
    assert out["completion_state"] == "qualified-draft"
    assert call("normal_known", model=NORMAL)["completion_state"] == "blocked-escalated"
    missing = dataset.model_copy(update={"rows": [{"y": None}]})
    assert call("normal_known", model=NORMAL, dataset=missing)["completion_state"] == "blocked-escalated"
    for claim in ({"approved": True}, {"reviewer": "expert"}, {"completion_state": "verified"}):
        with pytest.raises(ValidationError):
            block.BayesianInput(op="normal_known", model=NORMAL, observations=[32], **claim)
    with pytest.raises(ValidationError):
        block.BayesianInput(model={**NORMAL, "code": "import os"})
    assert call("beta_binomial", model={**BETA, "trials": 1}, observations=[.5])["completion_state"] == "blocked-escalated"


def test_dataset_oversized_feature_refused_before_hash(monkeypatch):
    from unittest.mock import Mock
    from g6.ml._types import DatasetSpec
    hashed = Mock(side_effect=AssertionError("rows hashed before bounds check"))
    monkeypatch.setattr(DatasetSpec, "rows_sha256", hashed)
    dataset = DatasetSpec(rows=[{"y": 32, "feature": "x" * 10001}], target="y",
                          features=["feature"], task="regression")
    out = call("normal_known", model=NORMAL, dataset=dataset)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_ML_TOO_LARGE"
    assert "posterior" not in out
    hashed.assert_not_called()
    # Removal target: models.observations: `bounded(dataset.rows, {dataset.target, *dataset.features}, "row")`.


def test_dataset_undeclared_column_refused_before_hash(monkeypatch):
    from unittest.mock import Mock
    from g6.ml._types import DatasetSpec
    hashed = Mock(side_effect=AssertionError("rows hashed before bounds check"))
    monkeypatch.setattr(DatasetSpec, "rows_sha256", hashed)
    dataset = DatasetSpec(rows=[{"y": 32, "feature": 1, "undeclared": "text"}], target="y",
                          features=["feature"], task="regression")
    out = call("normal_known", model=NORMAL, dataset=dataset)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_ML_INPUT_INVALID"
    assert "posterior" not in out
    hashed.assert_not_called()
    # Removal target: models.observations: `bounded(dataset.rows, {dataset.target, *dataset.features}, "row")`.


def test_prior_sensitivity_shift_and_predictive_misfit():
    # Removal target: block.py:114 `card = PRIOR_SENSITIVE if any(s["large_shift"] for s in shifts) else POSTERIOR`.
    out = call("prior_sensitivity", model=NORMAL, observations=[32],
               alternatives=[{**NORMAL, "mean": -30, "variance": .01}])
    assert out["warning_card"]["code"] == "G6_E_ML_PRIOR_SENSITIVE"
    assert out["sensitivity"][0]["absolute_shift"] > 50
    # Known noise 2 cannot explain this variance, regardless of the unknown mean.
    out = call("predictive_check", model=NORMAL, observations=[-100, 100]*6,
               seed=42, statistic="variance", replicates=300)
    assert out["warning_card"]["code"] == "G6_E_ML_MODEL_MISFIT"
    assert out["predictive_check"]["observed"] > out["predictive_check"]["interval"]["high"]
    assert call("prior_sensitivity", model=NORMAL, observations=[32])["completion_state"] == "blocked-escalated"


def test_sampling_success_reproducible_and_timeout():
    # Removal target: sampling.py:22 `seeds = random.Random(seed)`.
    kwargs = dict(model=NORMAL, observations=[31, 33]*6, seed=55, draws=2000)
    a = call("sample", **kwargs)
    b = call("sample", **kwargs)
    assert a["completion_state"] == "qualified-draft"
    assert a["posterior"] == b["posterior"]
    assert a["posterior"]["mean"] == pytest.approx(31.95918367, abs=.1)
    assert call("sample", **kwargs, timeout_s=1e-12)["code"] == "G6_E_ML_DEADLINE"
    assert call("sample", model=NORMAL, observations=[32])["code"] == "G6_E_ML_SEED_MISSING"


def test_pymc_absent_dry_run_gate_and_diagnostics(monkeypatch, tmp_path):
    # Removal target: pymc_child.installed: missing-extra refusal; block.run: `if d.dry_run:` before execute.
    block, *_ = api()
    from g6.ml.adapt_bayesian import pymc_child
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.setattr(pymc_child, "missing_extra", lambda *args: "PyMC is not installed: g6[bayes]")
    out = call("sample_pymc", model=NORMAL, observations=[32], seed=1)
    assert out["code"] == "G6_E_ML_BACKEND_NOT_INSTALLED"
    monkeypatch.setattr(pymc_child, "missing_extra", lambda *args: "")
    monkeypatch.setattr(pymc_child, "run", lambda *a, **kw: pytest.fail("child started"))
    out = call("sample_pymc", model=NORMAL, observations=[32], seed=1)
    assert out["dry_run"] and "posterior" not in out
    assert "sample_pymc" in block.SENSITIVE_OPS


def test_autocovariance_and_rank_ties():
    # Removal target: diagnostics.py:96 `return [v.real/n for v in _fft([v*v.conjugate() for v in freq], True)[:n]]`.
    _, diag, *_ = api()
    v = [1., 3., -1., 5., 2.]
    avg = sum(v)/len(v)
    expected = [sum((v[i]-avg)*(v[i+t]-avg) for i in range(len(v)-t))/len(v) for t in range(len(v))]
    assert diag.autocovariance(v) == pytest.approx(expected, abs=1e-12)
    ranked = diag.rank_normalise([[1, 2], [2, 3]])
    assert ranked[0][1] == ranked[1][0] == 0


def test_source_register_offline_and_vetting_state():
    # Removal target: __init__.py:21 `__g6_vetting__ = {` declaration (remove the complete declaration).
    from g6.core import vetting
    from g6.ml.adapt_bayesian import sources
    assert sources.register_problems() == []
    assert vetting.status("g6.ml.adapt_bayesian") == vetting.REVIEWED   # its certificate is current (DECISIONS #39)
    assert sources.entries()["bolstad-17"]["sha256"] == "b3f8db1852becc0fa0da3fc75012c885f0b2f2500555ca9406c0ed5a3b7d1924"


@pytest.mark.parametrize("source_id,clause,section", [
    ("bolstad-20", "the posterior's precision is the prior's precision plus the sample mean's precision, n / "
     "sigma^2; and m', the posterior mean, averages m and the sample mean", "Known Variance"),
    ("barber-mh", "a = q~(x_(l-1) | x_cand) p(x_cand) / (q~(x_cand | x_(l-1)) p(x_(l-1)))", "Algorithm 27.3"),
])
def test_source_register_records_update_and_acceptance_rules(source_id, clause, section):
    import hashlib
    from g6.ml.adapt_bayesian import sources
    entry = sources.entries()[source_id]
    assert clause in entry["clause"]
    assert entry["clause_sha256"] == hashlib.sha256(entry["clause"].encode("utf-8")).hexdigest()
    assert section in entry["section"]
    assert entry["hash_scope"] == "corpus_file"
    assert entry["conversion_limitation"]
    # The book is all rights reserved (owner, 2026-10-25): the clause is the register's own words, no file is named.
    assert entry["withheld"] is True and "file_name" not in entry
    # Removal target: source_register.json: the `clause` field in bolstad-20 or barber-mh stating the rule.


def test_clauses_hold_the_formulas_and_three_sources_are_held_as_bytes():
    # Certificates v2a fix 2, B1-B3, B5 and B6: a clause stopping short of its formula again, a DLMF or Vehtari entry
    # back on the owner's notes, beta's moments and intervals left in functions no rule names, or the guard texts
    # silent on their further refusals, fails this.
    import hashlib
    from g6.ml.adapt_bayesian import sources
    entries, rules = sources.entries(), sources.RULES
    assert "gamma with shape (sum of y) + 1 and rate n" in entries["bolstad-17"]["clause"]
    assert "each weighted by its precision's share of the posterior precision" in entries["bolstad-20"]["clause"]
    assert "= 0.9673" in entries["barber-beta"]["clause"] and "(12.2.7)" in entries["barber-evidence"]["clause"]
    assert "(18.1.45)" in entries["barber-prediction"]["clause"]
    for name in ("dlmf-beta", "dlmf-gamma", "vehtari-2021"):
        entry = entries[name]
        assert entry["hash_scope"] == "retrieved_bytes" and entry["retrieved"] == "2026-10-23", name
        assert entry["clause_sha256"] == hashlib.sha256(entry["clause"].encode("utf-8")).hexdigest(), name
        assert entry["withheld"] is True, name  # cited only (owner, 2026-10-25): the clause is in G6's words
    assert "rank-normalized, folded draws" in entries["vehtari-2021"]["clause"]
    assert "only when it is below 1.01" in entries["vehtari-2021"]["clause"]
    assert "(ESS) to exceed 400" in entries["vehtari-2021"]["clause"]
    assert [rules[n].implements.rpartition(":")[2] for n in ("beta", "moments", "credible_interval")] == [
        "_beta_posterior", "summary", "quantile"]
    assert "in CDF units" in rules["numerics"].rule and "127 times" in rules["numerics"].rule
    assert "non-finite observed statistic" in rules["misfit"].rule and "undeclared column" in rules["bounds"].rule
    assert "R-hat is not below 1.01" in rules["sampler_metadata"].rule


def test_predictive_statistic_extension():
    # Removal target: predictive.register_statistic: `STATISTICS[name] = function`.
    from g6.ml.adapt_bayesian import predictive
    predictive.register_statistic("span", lambda y: max(y)-min(y))
    try:
        out = call("predictive_check", model=NORMAL, observations=[31, 33]*6,
                   seed=4, statistic="span", replicates=100)
        assert out["predictive_check"]["statistic"] == "span"
        assert out["predictive_check"]["observed"] == 2
    finally:
        predictive.STATISTICS.pop("span")

def test_pymc_bound_child_and_gate_refusals(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from g6.core.result import Result
    from g6.ml.adapt_bayesian import pymc_child
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    monkeypatch.setattr(pymc_child, "missing_extra", lambda *a: "")
    monkeypatch.setattr(pymc_child, "evaluate_action", lambda *a: SimpleNamespace(
        gate_decision=SimpleNamespace(decision="block", reason_codes=["G6_E_TEST_BLOCK"],
                                     required_user_message="test block")))
    monkeypatch.setattr(pymc_child, "require", lambda *a, **k: pytest.fail("probe before gate"))
    monkeypatch.setattr(pymc_child, "run", lambda *a, **k: pytest.fail("child before gate"))
    out = call("sample_pymc", model=NORMAL, observations=[32], seed=1, dry_run=False)
    assert out["completion_state"] == "blocked-escalated" and "posterior" not in out
    monkeypatch.setattr(pymc_child, "evaluate_action", lambda *a: SimpleNamespace(
        gate_decision=SimpleNamespace(decision="allow", reason_codes=[], required_user_message="allowed")))
    monkeypatch.setattr(pymc_child, "require", lambda *a, **k: Result.fail("compiler missing; install g++"))
    out = call("sample_pymc", model=NORMAL, observations=[32], seed=1, dry_run=False)
    assert out["code"] == "G6_E_OUTSIDE_PROGRAM_MISSING"
    monkeypatch.setattr(pymc_child, "require", lambda *a, **k: Result.ok(SimpleNamespace(where="/compiler/g++")))
    seen = []
    def child(payload, timeout_s, **kwargs):
        seen.append((payload, timeout_s, kwargs))
        return {"draws": [[1.]*600 for _ in range(4)], "divergences": 0, "backend": "pymc"}
    monkeypatch.setattr(pymc_child, "run", child)
    out = call("sample_pymc", model=NORMAL, observations=[32], seed=1, draws=600, dry_run=False)
    assert out["code"] == "G6_E_ML_NOT_CONVERGED" and "posterior" not in out
    assert seen[0][2]["script"] == pymc_child.SCRIPT
    assert seen[0][0]["model"] == NORMAL
    assert 0 < seen[0][1] <= 30
    # Removal target: pymc_child.execute: `if decision.decision not in ("allow", "allow_with_notice"):`.


@pytest.mark.parametrize("reply", [None, [], [[1.]*8]*3, [[float("nan")]*8]*4,
                                  [[True]*8]*4, [[.2]*7]*4])
def test_bad_child_draws_never_posterior(reply):
    import time
    from g6.ml._types import MLError
    from g6.ml.adapt_bayesian import sampling
    with pytest.raises(MLError, match="G6_E_ML_NOT_CONVERGED"):
        sampling.checked({"draws": reply}, chains=4, draws=8, mass=.95, deadline=time.monotonic()+30)
    # Removal target: sampling.py:47 `raise MLError(NOT_CONVERGED, "sampler returned missing or incomplete chains")`; finite-draw guard follows.


def test_pymc_draw_support_divergences_and_missing_metadata(monkeypatch):
    import time
    from g6.ml._types import MLError
    from g6.ml.adapt_bayesian import sampling
    rng = random.Random(44)
    draws = [[rng.gauss(0, 1) for _ in range(1200)] for _ in range(4)]
    for fields in ({"backend": "pymc"}, {"backend": "pymc", "divergences": 1},
                   {"backend": "pymc", "divergences": False}):
        with pytest.raises(MLError, match="G6_E_ML_NOT_CONVERGED"):
            sampling.checked({"draws": draws, **fields}, chains=4, draws=1200, mass=.95,
                             deadline=time.monotonic()+30)
    with pytest.raises(MLError, match="G6_E_ML_NOT_CONVERGED"):
        sampling.checked({"draws": draws, "backend": "pymc", "divergences": 0}, chains=4, draws=1200,
                         mass=.95, deadline=time.monotonic()+30, support="beta")
    # Removal target: sampling.py:57 `if type(divergences) is not int or divergences != 0:` (remove the guarded refusal).


def test_sensitivity_requires_only_prior_change_and_comparison_is_computed():
    out = call("prior_sensitivity", model=NORMAL, observations=[32], alternatives=[{**NORMAL, "sigma": 8}])
    assert out["completion_state"] == "blocked-escalated"
    out = call("compare", model=NORMAL, observations=[32], alternatives=[{**NORMAL, "mean": 25}])
    a, b = out["comparison"]
    assert a["log_bayes_factor_to_best"] == 0
    assert b["log_bayes_factor_to_best"] == pytest.approx(-45/40)
    out = call("compare", model=NORMAL, observations=[32], alternatives=[BETA])
    assert out["completion_state"] == "blocked-escalated"
    # Removal target: block.py:107 `raise MLError(INPUT_INVALID, "prior sensitivity changes only the prior, never the likelihood or noise")`.


@pytest.mark.parametrize("kind,model,y", [
    ("beta", {"kind": "beta_binomial", "alpha": 1, "beta": 1, "trials": 10}, [2, 5, 6]),
    ("gamma", {"kind": "gamma_poisson", "shape": 1, "rate": 1}, [2, 3, 5]),
    ("normal", NORMAL, [31, 33, 32]),
])
def test_predictive_all_models_seeded(kind, model, y):
    kwargs = dict(model=model, observations=y, seed=23, statistic="mean", replicates=100)
    a, b = call("predictive_check", **kwargs), call("predictive_check", **kwargs)
    assert a["completion_state"] == "qualified-draft"
    assert a["predictive_check"] == b["predictive_check"]
    assert 0 <= a["predictive_check"]["upper_tail_probability"] <= 1
    assert a["predictive_check"]["replicates"] == 100
    # Removal target: predictive.check: `rng = random.Random(seed)` and per-model replicated observations.


def test_predictive_bounds_statistic_and_missing_seed():
    assert call("predictive_check", model=NORMAL, observations=[32], statistic="variance", seed=4)[
        "completion_state"] == "blocked-escalated"
    assert call("predictive_check", model=NORMAL, observations=[32], statistic="not_registered", seed=4)[
        "completion_state"] == "blocked-escalated"
    assert call("predictive_check", model=NORMAL, observations=[32], statistic="mean")[
        "code"] == "G6_E_ML_SEED_MISSING"
    assert call("predictive_check", model=NORMAL, observations=[32]*20000, statistic="mean", seed=4,
                replicates=1000)["code"] == "G6_E_ML_TOO_LARGE"
    # Removal target: predictive.py:59 `raise MLError(TOO_LARGE, "predictive check exceeds 2000000 replicated observations")`.


def test_source_integrity_missing_entry_and_changed_clause_block(monkeypatch):
    import copy
    from g6.ml.adapt_bayesian import sources
    original = sources.register()
    for changed in ("missing", "hash"):
        data = copy.deepcopy(original)
        if changed == "missing":
            data["entries"] = [e for e in data["entries"] if e["id"] != "vehtari-2021"]
        else:
            next(e for e in data["entries"] if e["id"] == "vehtari-2021")["clause"] += "changed"
        monkeypatch.setattr(sources, "register", lambda: data)
        assert sources.register_problems()
        out = call("sample", model=NORMAL, observations=[32], seed=4)
        assert out["completion_state"] == "blocked-escalated" and "posterior" not in out
    # Removal target: sources.ensure: `if problems:` blocks missing rules and clause-hash mismatch.


def test_registry_mcp_skill_and_sensitive_surface():
    import json
    from g6.core import discovery, skills
    from g6.core.registry import get_registry
    from g6.mcp import describe_reply, invoke_reply
    info = describe_reply("adapt_bayesian")
    assert info["sensitive_ops"] == ["sample_pymc"]
    assert sorted(skills.skill_ops(info["skill"])) == sorted(discovery.ops("adapt_bayesian"))
    for op, params in skills.skill_examples(info["skill"]):
        if op != "sample_pymc":
            reply = invoke_reply("adapt_bayesian", op, json.dumps(params))
            assert reply["ok"], reply
    result = get_registry().invoke("adapt_bayesian", "sample_pymc", {}, block_sensitive=True)
    assert result.__class__.__name__ == "Failure"
    assert "G6_E_" in str(result.failure())
    # Removal target: __init__.py:15 `__g6_sensitive_ops__ = ("sample_pymc",)`.


@pytest.mark.parametrize("copies", [2, 4])
@pytest.mark.parametrize("backend", ["mh", "pymc"])
def test_identical_well_mixed_sequences_refused_before_assessment(monkeypatch, copies, backend):
    import time
    from unittest.mock import Mock
    from g6.ml._types import MLError
    from g6.ml.adapt_bayesian import sampling
    _, diag, *_ = api()
    rng = random.Random(112)
    chains = [[rng.gauss(0, 1) for _ in range(1600)] for _ in range(4)]
    for i in range(1, copies):
        chains[i] = chains[0][:]
    assessed = Mock(wraps=diag.assess)
    monkeypatch.setattr(diag, "assess", assessed)
    with pytest.raises(MLError, match="duplicated complete chains") as caught:
        sampling.checked({"draws": chains, "backend": backend, "divergences": 0},
                         chains=4, draws=1600, mass=.95, deadline=time.monotonic()+30)
    assert caught.value.code == "G6_E_ML_NOT_CONVERGED"
    assessed.assert_not_called()
    monkeypatch.setattr(sampling, "mh", lambda *a, **k: {"draws": chains, "backend": "mh"})
    out = call("sample", model=NORMAL, observations=[32], seed=112, draws=1600)
    assert out["completion_state"] == "blocked-escalated"
    assert out["code"] == "G6_E_ML_NOT_CONVERGED"
    assert "posterior" not in out
    assessed.assert_not_called()
    # Removal target: sampling.checked: `raise MLError(NOT_CONVERGED, "sampler returned duplicated complete chains; draws are not independent")`.


def test_independent_well_mixed_sequences_and_strict_thresholds(monkeypatch):
    from g6.ml.adapt_bayesian import sampling
    _, diag, *_ = api()
    rng = random.Random(112)
    chains = [[rng.gauss(0, 1) for _ in range(1600)] for _ in range(4)]
    monkeypatch.setattr(sampling, "mh", lambda *a, **k: {"draws": chains, "backend": "mh"})
    out = call("sample", model=NORMAL, observations=[32], seed=112, draws=1600)
    assert out["completion_state"] == "qualified-draft"
    assert out["diagnostics"]["converged"]
    assert "posterior" in out
    monkeypatch.setattr(diag, "_rhat", lambda c: 1.01)
    assert not diag.assess(chains)["converged"]
    monkeypatch.setattr(diag, "_rhat", lambda c: 1.)
    monkeypatch.setattr(diag, "ess", lambda c, deadline=None: 400.)
    assert not diag.assess(chains)["converged"]
    # Removal target: diagnostics.assess: `rhat < 1.01 and bulk > 400 and tail > 400`.


def test_raw_boolean_data_refused_and_no_missing_model_defaults():
    from pydantic import ValidationError
    block, *_ = api()
    with pytest.raises(ValidationError):
        block.BayesianInput(model=NORMAL, observations=[True])
    with pytest.raises(ValidationError):
        block.BayesianInput(model={"kind": "normal_known", "mean": 30, "variance": 16})
    assert call("normal_known", observations=[32])["completion_state"] == "blocked-escalated"
    # Removal target: block.py:57 `@field_validator("observations", mode="before")`.


def test_numerical_iteration_bounds_fail_closed(monkeypatch):
    from g6.ml._types import MLError
    *_, num = api()
    monkeypatch.setattr(num, "MAX_ITER", 0)
    with pytest.raises(MLError):
        num.beta_cdf(.4, 2.5, 1)
    with pytest.raises(MLError):
        num.gamma_cdf(2, 1)
    # Removal target: numerics.py:45 `_fail()` after the beta fraction exhausts MAX_ITER.

def test_pymc_fixed_script_likelihood_noise_and_serial_chains(monkeypatch, tmp_path):
    import json
    import sys
    from types import SimpleNamespace
    from g6.ml.adapt_bayesian import pymc_child
    calls = []
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    class Array:
        def tolist(self):
            return [[31.]*8 for _ in range(4)]
        def sum(self):
            return 0
    def normal(name, **kwargs):
        calls.append((name, kwargs))
        return "theta_variable"
    def sample(**kwargs):
        calls.append(("sample", kwargs))
        return SimpleNamespace(posterior={"theta": SimpleNamespace(values=Array())},
                               sample_stats={"diverging": SimpleNamespace(values=Array())})
    pm = SimpleNamespace(Model=Context, Normal=normal, sample=sample, __version__="test")
    pt = SimpleNamespace(config=SimpleNamespace(cxx=""), __version__="test")
    monkeypatch.setitem(sys.modules, "pymc", pm)
    monkeypatch.setitem(sys.modules, "pytensor", pt)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTENSOR_FLAGS", "")
    monkeypatch.setenv("MPLCONFIGDIR", "")
    (tmp_path / "job.json").write_text(json.dumps({"model": NORMAL, "observations": [32], "seed": 7,
        "draws": 8, "chains": 4, "warmup": 10, "compiler": "/compiler/g++"}), encoding="utf-8")
    pymc_child.child_main()
    assert calls[0] == ("theta", {"mu": 30, "sigma": 4})
    assert calls[1][1]["sigma"] == 2
    assert calls[2][1]["cores"] == 1 and calls[2][1]["chains"] == 4
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["divergences"] == 0 and result["backend"] == "pymc"
    assert pt.config.cxx == "/compiler/g++"
    # Removal target: pymc_child.child_main: likelihood sigma=spec["sigma"], cores=1, and measured divergence count.


def test_declarations_extra_and_failure_registry():
    import tomllib
    from pathlib import Path
    import g6.ml.adapt_bayesian as package
    from g6.core.failure_modes import FailureModeRegistry
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extra = pyproject["project"]["optional-dependencies"]["bayes"]
    assert any(s.startswith("pymc") for s in extra) and any(s.startswith("arviz") for s in extra)
    assert package.__g6_extras__["bayes"] == ("pymc", "arviz", "pytensor")
    assert package.__g6_mitigates__ == ("hidden_uncertainty",)
    for code in (*package.__g6_mitigates__, *package.__g6_introduces__):
        assert FailureModeRegistry.is_registered(code)
    assert package.__g6_outside__[0].command == "g++"
    # Removal target: pyproject.toml:71 `bayes = ["pymc>=5,<6", "arviz>=0.20,<1"]`.