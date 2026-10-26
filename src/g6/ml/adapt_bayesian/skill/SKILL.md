---
name: g6-adapt-bayesian
description: Use when a scalar beta-binomial, gamma-Poisson or normal mean model needs an explicit posterior, credible interval, prior sensitivity, predictive check or checked sampling, and a point estimate would hide uncertainty.
---

# G6 adapt_bayesian

`g6.ml.adapt_bayesian` computes scalar Bayesian posteriors under explicit model assumptions. Best state is
`qualified-draft`. The statistical source register is packaged with the component. No qualified person has reviewed this
component; its rules are checked against their sources by G6's certificate and an independent reader.

## When to use it

Use the exact conjugate op first. It returns the distribution, parameters, mean, variance, sd, median, mode where
unique and interior, and an equal-tailed credible interval (default mass 0.95). Parameters update analytically;
interval endpoints use bounded numerical inversion. Gamma parameters are shape and **rate**, not scale.
Before interpreting a result, run prior sensitivity with a plausible stated alternative and a predictive check
on a statistic chosen before looking at its result. Neither check is silently claimed by an update or sampler.

## When not to use it

Not for multivariate, hierarchical, regression, unknown-noise, time-series or dependent-observation models. No
caller code, files, pickles, expression evaluators or distribution plugins enter through the tool. No fitting of
likelihood noise from the data: `normal_known` requires `sigma` separately from prior `variance`.
Do not treat a credible interval as a frequentist confidence interval or a diagnostic pass as proof of fit.

## Ops

- `beta_binomial`: `model` with `kind="beta_binomial"`, positive `alpha`, `beta`, and integer `trials` per observation
  (default 1). Each observation is an integer success count in `[0,trials]`. Returns exact log marginal likelihood.
- `gamma_poisson`: `model` with `kind="gamma_poisson"`, `shape`, `rate`, and `prior="gamma"` (default). Counts have
  unit exposure. Alternatively `prior="positive_uniform"`, with no shape/rate, explicitly requests the improper
  uniform prior on positive rates. Its posterior is proper with nonempty data; its marginal likelihood is undefined.
- `normal_known`: `model` with `kind="normal_known"`, prior `mean`, prior `variance`, and likelihood `sigma`.
- `compare`: `model` and 1 to 10 `alternatives` on the same observations and likelihood family. Binomial trial counts
  must agree. Normal noise may differ. Returns log evidence and log Bayes factors relative to the best evidence;
  improper priors are refused. No model probabilities or prior odds are assumed.
- `sample`: `model`, explicit `seed`, optional `draws` (8 to 4000 per chain, default 2000), `chains` (2 to 8, default
  4), `warmup` (0 to 4000, default 1000), and `proposal_scale` (default 2.4 posterior sd units, fixed throughout).
  Seeded Metropolis-Hastings runs without an extra. Initial states are dispersed posterior quantiles.
- `sample_pymc`: same model and draw settings, explicit `seed`, `dry_run=true` by default. Sensitive: starts G6's
  fixed child and probes the compiler only after the action gate allows it. `proposal_scale` applies only to MH.
- `prior_sensitivity`: `model` and 1 to 10 `alternatives` changing only priors. Reruns exact updates, reports each
  alternative posterior and signed/absolute mean shift. More than one baseline posterior sd triggers the
  prior-sensitive card. This threshold is G6's descriptive warning rule, not a calibrated scientific cutoff.
- `predictive_check`: `model`, explicit `seed` and `statistic` (`mean`, sample `variance`, or `maximum`), plus
  `replicates` (50 to 1000, default 300). Draws a posterior parameter per replicate, then a full replicated dataset.
  The observed statistic outside the stated predictive interval gets the misfit card. The upper-tail probability
  is a Monte Carlo proportion with ties included, not a frequentist p-value.

Each substantive op needs exactly one of `observations` (numeric list) or `dataset` (the family's `DatasetSpec`).
For a dataset, only its named target column is used; features are unused. All observations enter inference; this
is not a held-out evaluation. Missing, nonfinite or out-of-support data are refused. Approval/reviewer fields and
extra model fields are refused. Dataset rows may contain only the declared target and features; text cells are
limited to 10000 characters each and 5000000 characters in total. These shared family bounds are checked before
target extraction or dataset hashing. `request_id` and `run_id` are carried through.

Common bounds: 1 to 20000 observations, magnitude at most 1000000; total counts at most 1000000; binomial trials
at most 10000; positive model parameters 0.01 to 10000; prior means within +/-1000000; `mass` from 0.5 to 0.999;
`timeout_s` at most 120 (default 30); seed 0 to 4294967295. Predictive work is capped at two million replicated
observations and two million Poisson exponential arrivals. Numerical calculations that exhaust their iteration
bounds refuse. MH/predictive deadlines are cooperative, including diagnostics; the PyMC child has a process deadline.

## Alone and composed

From Python use `get_registry().invoke("adapt_bayesian", "normal_known", params)`. Over MCP use
`g6_invoke("adapt_bayesian", "normal_known", params_json)`. `ops` and `help` describe the same door.
A data-preparation component can provide rows for `DatasetSpec`; read its missing-value report first. Follow an exact
update with `prior_sensitivity` and `predictive_check`, and preserve all warning cards when composing outputs.
Trusted host Python can call `predictive.register_statistic(name, function)` to add a pure bounded statistic; this
is not a tool input, and the host is responsible for installed extension code.

Over MCP, `sample_pymc` is refused on a surface with `block_sensitive=True`. The server owner must enable that
sensitive op separately. Setting `dry_run=false` requests execution; it is no substitute for the action gate.
The current `run_code` policy may hold the action for review even with the extra installed. No caller approval
claim overrides it. Compiler probes, gate failures and child errors are reported; there is no automatic MH fallback.

## Failure modes it catches

- `hidden_uncertainty`: intervals accompany moments, known likelihood noise is separate, and failed convergence
  withholds the entire sampled posterior (`G6_E_ML_NOT_CONVERGED`, `blocked-escalated`).

G6 splits chains, pools average ranks for ties, normalises ranks through the normal inverse CDF, and reports the
maximum of rank and folded split R-hat. R-hat must be strictly below 1.01; bulk ESS and tail ESS must both be
strictly above 400. Tail ESS is the minimum for the 5% and 95% quantile indicators. Constant or incomplete chains
are refused. Exactly duplicated complete chains are refused before diagnostic assessment because repeated draws
cannot count as independent chains. PyMC also needs a measured zero divergence count. G6 computes these diagnostics
from returned draws.

## Blind spots

- `overstated_certainty`: a sampler can pass every diagnostic and still miss a mode it never visited.
- Conjugate results are exact only for the stated model. Measurement error, dependence, selection and a poor
  likelihood can invalidate their interpretation. Floating-point CDFs have finite accuracy.
- With very small beta shapes (for example alpha=0.01), an interval endpoint can lie beyond the resolution of
  200 bounded bisections. Such requests refuse rather than returning an unsupported interval.
- Prior sensitivity depends on the alternatives chosen; it cannot justify a prior chosen after seeing the data.
- A single predictive statistic can miss other kinds of misfit. Predictive checks reuse the data and are conservative.
- Passing diagnostics does not certify model correctness.

## What it needs

The exact core, MH, sensitivity and predictive checks need no extra, model service or network. Optional `g6[bayes]`
adds PyMC and ArviZ (Apache-2.0), with PyTensor (BSD-3-Clause). Without it `sample_pymc` says not installed with
`G6_E_ML_BACKEND_NOT_INSTALLED`; other ops retain their own computations.

The outside program is **C compiler for pytensor (g++)**, found on PATH. Install a GCC C/C++ toolchain yourself
(MinGW-w64 on Windows). GCC is GPL with its runtime-library exception, used only as an outside program and never
bundled or imported. Compilation stays in the child's scratch cache. The child uses one core and sequential
chains, a 1536 MB family memory cap (none on macOS, which refuses it) and a 32 MB reply cap. The family sandbox's Python socket guard permits
loopback and is not an operating-system security boundary. No caller code runs there.

## Examples

```g6-example op=beta_binomial
{"model":{"kind":"beta_binomial","alpha":1,"beta":1,"trials":10},"observations":[2]}
```

```g6-example op=gamma_poisson
{"model":{"kind":"gamma_poisson","shape":1,"rate":1},"observations":[3,2,0,8,2,4,6,1]}
```

```g6-example op=normal_known
{"model":{"kind":"normal_known","mean":30,"variance":16,"sigma":2},"observations":[31,33,32]}
```

```g6-example op=compare
{"model":{"kind":"normal_known","mean":30,"variance":16,"sigma":2},"alternatives":[{"kind":"normal_known","mean":28,"variance":16,"sigma":2}],"observations":[31,33,32]}
```

```g6-example op=sample
{"model":{"kind":"normal_known","mean":30,"variance":16,"sigma":2},"observations":[31,33,31,33,31,33,31,33,31,33,31,33],"seed":55,"draws":2000}
```

```g6-example op=prior_sensitivity
{"model":{"kind":"beta_binomial","alpha":4.8,"beta":19.2,"trials":100},"alternatives":[{"kind":"beta_binomial","alpha":1,"beta":1,"trials":100}],"observations":[26]}
```

```g6-example op=predictive_check
{"model":{"kind":"normal_known","mean":30,"variance":16,"sigma":2},"observations":[31,33,32],"seed":42,"statistic":"variance","replicates":100}
```

## The four open problems

- **Learning signals:** report computed shifts, predictive discrepancies and diagnostics; nothing automatically learns
  a better prior or changes a model.
- **Evaluators the model cannot influence:** model inputs cannot set diagnostic values, thresholds or labels; G6
  recomputes them. Selecting data, priors and statistics can still bias conclusions.
- **Transfer by failure mode:** `hidden_uncertainty` and the shared envelope keep the same meaning across surfaces.
- **Enough held-out cases:** worked examples and failure cases support implementation checks, not general calibration
  or held-out model validity. No such validation or qualified review is claimed.