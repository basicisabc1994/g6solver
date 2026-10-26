# SPDX-License-Identifier: Apache-2.0
"""Bounded DLMF 8.7.1, 8.9 and 8.17 calculations, without SciPy, from the core's statistics (``g6.core.special``).

Source register: dlmf-gamma and dlmf-beta. Continued fractions use modified
Lentz evaluation; exhausting a bound is an error, never an approximate success.
Here every such error, and every argument outside a function's domain, is the
family's refusal (``MLError``, ``G6_E_ML_INPUT_INVALID``).
"""
from __future__ import annotations

import functools
from collections.abc import Callable

from g6.core import special
from g6.ml._types import INPUT_INVALID, MLError

EPS = special.EPS
TINY = special.TINY
#: The bound on fraction and series terms; read at each call, so it can be lowered for a test.
MAX_ITER = special.MAX_ITER
log_beta = special.log_beta


def _refused(function: Callable) -> Callable:
    @functools.wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except MLError:
            raise
        except ValueError as exc:
            raise MLError(INPUT_INVALID, str(exc)) from exc
    return call


@_refused
def beta_cdf(x: float, a: float, b: float) -> float:
    return special.beta_cdf(x, a, b, MAX_ITER)


@_refused
def gamma_cdf(x: float, a: float) -> float:
    """Regularised lower gamma P(a,x); rate one."""
    return special.gamma_cdf(x, a, MAX_ITER)


@_refused
def bisect_quantile(q: float, cdf: Callable[[float], float], low: float, high: float) -> float:
    return special.bisect_quantile(q, cdf, low, high)


@_refused
def beta_quantile(q: float, a: float, b: float) -> float:
    return special.beta_quantile(q, a, b, MAX_ITER)


@_refused
def gamma_quantile(q: float, a: float) -> float:
    return special.gamma_quantile(q, a, MAX_ITER)
