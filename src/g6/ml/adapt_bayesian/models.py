# SPDX-License-Identifier: Apache-2.0
"""Strict scalar model data and the shared family DatasetSpec adapter."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.ml._types import INPUT_INVALID, DatasetSpec, MLError, bounded

Positive = Annotated[float, Field(ge=.01, le=10000, allow_inf_nan=False)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class BetaBinomial(Model):
    kind: Literal["beta_binomial"]
    alpha: Positive
    beta: Positive
    trials: int = Field(1, ge=1, le=10000, strict=True)


class GammaPoisson(Model):
    kind: Literal["gamma_poisson"]
    prior: Literal["gamma", "positive_uniform"] = "gamma"
    shape: Positive | None = None
    rate: Positive | None = None

    @model_validator(mode="after")
    def prior_fields(self):
        if self.prior == "gamma" and (self.shape is None or self.rate is None):
            raise ValueError("a gamma prior needs shape and rate")
        if self.prior == "positive_uniform" and (self.shape is not None or self.rate is not None):
            raise ValueError("positive_uniform is explicitly improper; it has no gamma prior parameters")
        return self


class NormalKnown(Model):
    kind: Literal["normal_known"]
    mean: float = Field(ge=-1e6, le=1e6)
    variance: Positive
    sigma: Positive


ModelSpec = Annotated[BetaBinomial | GammaPoisson | NormalKnown, Field(discriminator="kind")]


def observations(model: ModelSpec, values: list[float] | None, dataset: DatasetSpec | None) -> list[float]:
    if (values is None) == (dataset is None):
        raise MLError(INPUT_INVALID, "give exactly one of observations or dataset; missing data cannot be assessed")
    if dataset is not None:
        bounded(dataset.rows, {dataset.target, *dataset.features}, "row")
    raw = values if dataset is None else [row.get(dataset.target) for row in dataset.rows]
    if not raw or len(raw) > 20000:
        raise MLError(INPUT_INVALID, "give 1 to 20000 observations")
    out = []
    for i, value in enumerate(raw):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise MLError(INPUT_INVALID, f"observation {i} is missing or is not a finite number")
        if abs(value) > 1e6:
            raise MLError(INPUT_INVALID, f"observation {i} exceeds the magnitude cap 1000000")
        if isinstance(model, (BetaBinomial, GammaPoisson)):
            if value < 0 or int(value) != value or (isinstance(model, BetaBinomial) and value > model.trials):
                raise MLError(INPUT_INVALID, f"observation {i} is not an integer count in the model's support")
        out.append(float(value))
    if isinstance(model, (BetaBinomial, GammaPoisson)) and sum(out) > 1e6:
        raise MLError(INPUT_INVALID, "total counts exceed 1000000")
    return out


def same_likelihood(a: ModelSpec, b: ModelSpec) -> bool:
    # Normal noise may differ for model comparison, but not for prior sensitivity.
    return a.kind == b.kind and (not isinstance(a, BetaBinomial) or a.trials == b.trials)