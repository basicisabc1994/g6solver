# SPDX-License-Identifier: Apache-2.0
"""One typed Bayesian door; exact conjugates or checked sampling, qualified-draft at best."""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.errors import unknown_op
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.ml._types import (
    DESCRIBED, INPUT_INVALID, MISFIT, OP_FAILED, POSTERIOR, PRIOR_SENSITIVE, SEED_MISSING,
    DatasetSpec, MLError, answer, refuse,
)
from g6.ml.adapt_bayesian import exact, models, predictive, sampling, sources
from g6.ml.adapt_bayesian.models import ModelSpec, NormalKnown

OPS = {
    "beta_binomial": "Exact beta-binomial update from independent binomial counts.",
    "gamma_poisson": "Exact gamma-Poisson update; rate parameterisation, unit exposure per count.",
    "normal_known": "Exact normal mean update with separately stated known likelihood sigma.",
    "compare": "Exact log marginal likelihoods on the same observations, including normalising constants.",
    "sample": "Seeded Metropolis-Hastings for a scalar model; refuse failing diagnostics.",
    "sample_pymc": "Optional PyMC in a fixed child; sensitive, dry-run by default, action gate before programs.",
    "prior_sensitivity": "Exact reruns with stated alternative priors; report shifts and warn above one baseline sd.",
    "predictive_check": "Seeded posterior predictive replication on a stated statistic: mean, variance or maximum.",
}
SENSITIVE_OPS = ("sample_pymc",)


class BayesianInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    op: Literal["beta_binomial", "gamma_poisson", "normal_known", "compare", "sample", "sample_pymc",
                "prior_sensitivity", "predictive_check", "ops", "help"] = "help"
    model: ModelSpec | None = None
    observations: list[float] | None = Field(None, min_length=1, max_length=20000)
    dataset: DatasetSpec | None = None
    alternatives: list[ModelSpec] = Field(default_factory=list, max_length=10)
    mass: float = Field(.95, ge=.5, le=.999)
    seed: int | None = Field(None, ge=0, le=2**32-1, strict=True)
    draws: int = Field(2000, ge=8, le=4000, strict=True)
    chains: int = Field(4, ge=2, le=8, strict=True)
    warmup: int = Field(1000, ge=0, le=4000, strict=True)
    proposal_scale: float = Field(2.4, ge=1e-12, le=100)
    timeout_s: float = Field(30, gt=0, le=120)
    dry_run: bool = True
    statistic: str = Field("", max_length=100)
    replicates: int = Field(300, ge=50, le=1000, strict=True)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("observations", mode="before")
    @classmethod
    def numeric_observations(cls, values):
        if values is not None and (not isinstance(values, list) or
                                  any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values)):
            raise ValueError("observations must be numeric data, never booleans, strings or missing values")
        return values


def run(d: BayesianInput):
    ids = {"request_id": d.request_id, "run_id": d.run_id}
    if d.op in ("ops", "help"):
        return answer(d.op, "Scalar Bayesian models with bounded numerical calculations.", card=DESCRIBED,
                      ops=list(OPS), sensitive=list(SENSITIVE_OPS), help=OPS, **ids)
    deadline = time.monotonic() + d.timeout_s
    evidence = sources.ensure()
    if d.model is None:
        raise MLError(INPUT_INVALID, "model is required; no default prior or noise is assumed")
    y = models.observations(d.model, d.observations, d.dataset)
    if d.op in ("beta_binomial", "gamma_poisson", "normal_known") and d.model.kind != d.op:
        raise MLError(INPUT_INVALID, "the model kind must match the conjugate op")
    p = exact.update(d.model, y)
    evidence.append({"kind": "bayesian_model", "model": d.model.model_dump(), "observations": len(y),
                     "data": "all supplied observations; dataset features are unused; no held-out score is claimed",
                     "dataset_sha256": d.dataset.rows_sha256() if d.dataset else None,
                     "checks_not_run": "Use prior_sensitivity and predictive_check before interpreting this model."})
    if d.op == "compare":
        if not d.alternatives:
            raise MLError(INPUT_INVALID, "comparison needs at least one alternative model")
        table = []
        for candidate in [d.model, *d.alternatives]:
            if not models.same_likelihood(d.model, candidate):
                raise MLError(INPUT_INVALID, "compare only models of the same observations and binomial trials")
            lm = exact.update(candidate, y)["log_marginal_likelihood"]
            if lm is None:
                raise MLError(INPUT_INVALID, "an improper prior has no defined marginal likelihood")
            table.append({"model": candidate.model_dump(), "log_marginal_likelihood": lm})
        best = max(r["log_marginal_likelihood"] for r in table)
        for row in table:
            row["log_bayes_factor_to_best"] = row["log_marginal_likelihood"] - best
        return answer(d.op, "Exact log evidence under each stated model; prior dependent.", card=POSTERIOR,
                      comparison=table, evidence=evidence, **ids)
    if d.op == "prior_sensitivity":
        if not d.alternatives:
            raise MLError(INPUT_INVALID, "prior sensitivity requires a stated alternative prior")
        baseline = exact.summary(p, d.mass)
        shifts = []
        for candidate in d.alternatives:
            if not models.same_likelihood(d.model, candidate) or (isinstance(d.model, NormalKnown) and
                                                                 d.model.sigma != candidate.sigma):
                raise MLError(INPUT_INVALID, "prior sensitivity changes only the prior, never the likelihood or noise")
            alternative = exact.summary(exact.update(candidate, y), d.mass)
            shifts.append({"alternative_model": candidate.model_dump(), "posterior": alternative,
                           **exact.prior_shift(baseline, alternative)})
        card = PRIOR_SENSITIVE if any(s["large_shift"] for s in shifts) else POSTERIOR
        return answer(d.op, "Posterior means rerun under each stated alternative prior; large means over one "
                      "baseline posterior sd (a G6 warning rule, not a validated cutoff).", card=card,
                      posterior=baseline, sensitivity=shifts, evidence=evidence, **ids)
    if d.op in ("sample", "sample_pymc", "predictive_check"):
        if d.seed is None:
            raise MLError(SEED_MISSING, "sampling and predictive checks require an explicit seed")
        if d.op == "predictive_check":
            check = predictive.check(d.model, y, p, seed=d.seed, statistic=d.statistic, replicates=d.replicates,
                                     mass=d.mass, deadline=deadline)
            return answer(d.op, "Observed statistic compared with replicated data from the stated model's posterior.",
                          card=MISFIT if check["misfit"] else POSTERIOR, predictive_check=check,
                          evidence=[*evidence, {"kind": "posterior_predictive_check", **check}], **ids)
        if d.op == "sample_pymc":
            from g6.ml.adapt_bayesian import pymc_child
            pymc_child.installed()
            if d.dry_run:
                return answer(d.op, "Dry run: no compiler probe or child started. Execution requires dry_run=false "
                              "and an allowing action-gate decision; caller approval claims cannot open the gate.",
                              card=DESCRIBED, dry_run=True, evidence=evidence, **ids)
            result = pymc_child.execute(d.model, y, seed=d.seed, draws=d.draws, chains=d.chains,
                                        warmup=d.warmup, deadline=deadline)
        else:
            result = sampling.mh(p, seed=d.seed, draws=d.draws, chains=d.chains, warmup=d.warmup,
                                 proposal_scale=d.proposal_scale, deadline=deadline)
        post, diag = sampling.checked(result, chains=d.chains, draws=d.draws, mass=d.mass, deadline=deadline,
                                      support=p["distribution"])
        return answer(d.op, "Sampled posterior passed the stated diagnostics, conditional on the model; "
                      "diagnostics cannot discover a mode the sampler never visited.", card=POSTERIOR,
                      posterior=post, diagnostics=diag,
                      evidence=[*evidence, {"kind": "mcmc_diagnostics", **diag},
                                {"kind": "sampling", "seed": d.seed, "draws": d.draws, "chains": d.chains,
                                 "warmup": d.warmup, "backend": result["backend"], "timeout_s": d.timeout_s,
                                 "versions": result.get("versions"), "limits": result.get("limits"),
                                 "gate": result.get("gate"), "acceptance_rates": result.get("acceptance_rates")}], **ids)
    return answer(d.op, "Exact conjugate posterior for the stated model; interval evaluated numerically.",
                  card=POSTERIOR, posterior=exact.summary(p, d.mass),
                  log_marginal_likelihood=p["log_marginal_likelihood"], evidence=evidence, **ids)


@block_contract(purpose="expose uncertainty with exact conjugate updates and checked posterior sampling",
                mitigates_failure_modes=("hidden_uncertainty",),
                verification_method="source known values, computed rank and folded R-hat, bulk and tail ESS")
@component_maturity("beta", reason="bounded scalar Bayesian core; qualified-draft at best; awaiting review")
class AdaptBayesianBlock(AIBlock[BayesianInput, dict, dict]):
    def __init__(self, name: str = "adapt_bayesian", state: dict | None = None):
        super().__init__(name=name, state=state or {})

    def infer(self, data: BayesianInput) -> Result[dict]:
        if data.op not in (*OPS, "ops", "help"):
            return unknown_op(self.name, data.op, (*OPS, "ops", "help"))
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        try:
            result = run(data)
        except MLError as exc:
            result = refuse(data.op, exc.code, exc.reason, evidence=exc.evidence, **ids)
        except Exception as exc:  # noqa: BLE001 - malformed backend replies fail closed
            result = refuse(data.op, OP_FAILED, f"{type(exc).__name__}: {exc}"[:400], **ids)
        return Result.ok({"component": self.name, "op": data.op, **result.model_dump(mode="json")})