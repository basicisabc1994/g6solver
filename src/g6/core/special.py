# SPDX-License-Identifier: Apache-2.0
"""Regularised incomplete beta and gamma functions and their quantiles, in plain Python: DLMF 8.7.1, 8.9 and 8.17.

The core's statistics, shared by every component that needs them (``g6.ml.adapt_bayesian`` first); it imports nothing
beyond the standard library. Continued fractions use the modified Lentz evaluation, and exhausting a bound raises
:class:`NotConverged`, never an approximate success. An argument outside a function's domain raises
:class:`ValueError`. The sources these functions apply are cited in the register of each component that uses them.
"""
from __future__ import annotations

import math
from collections.abc import Callable

EPS = 2e-14
TINY = 1e-300
MAX_ITER = 10000
#: The most bisections a quantile may take (rule `numerics`): one more is refused, never answered approximately.
MAX_BISECTIONS = 200


class NotConverged(ValueError):
    """A bounded numerical calculation did not reach its tolerance within its bound."""


def _nonzero(x: float) -> float:
    return math.copysign(max(abs(x), TINY), x)


def _fail() -> None:
    raise NotConverged("numerical calculation did not converge within its bound")


def log_beta(a: float, b: float) -> float:
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_fraction(x: float, a: float, b: float, max_iter: int = MAX_ITER) -> float:
    # DLMF 8.17.22-23, alternating even and odd coefficients.
    c, d = 1.0, 1 / _nonzero(1 - (a + b) * x / (a + 1))
    h = d
    for m in range(1, max_iter + 1):
        even = m * (b - m) * x / ((a + 2*m - 1) * (a + 2*m))
        odd = -(a + m) * (a + b + m) * x / ((a + 2*m) * (a + 2*m + 1))
        for coefficient in (even, odd):
            d = 1 / _nonzero(1 + coefficient * d)
            c = _nonzero(1 + coefficient / c)
            delta = c * d
            h *= delta
        if abs(delta - 1) < EPS:
            return h
    _fail()


def beta_cdf(x: float, a: float, b: float, max_iter: int = MAX_ITER) -> float:
    """The regularised incomplete beta function I_x(a, b)."""
    if not all(math.isfinite(v) for v in (x, a, b)) or a <= 0 or b <= 0 or not 0 <= x <= 1:
        raise ValueError("beta CDF needs positive shapes and x in [0,1]")
    if x in (0, 1):
        return x
    # The fraction is used directly ONLY on its rapidly converging side.
    if x >= (a + 1) / (a + b + 2):
        return 1 - _beta_direct(1 - x, b, a, max_iter)
    return _beta_direct(x, a, b, max_iter)


def _beta_direct(x: float, a: float, b: float, max_iter: int = MAX_ITER) -> float:
    front = math.exp(a * math.log(x) + b * math.log1p(-x) - log_beta(a, b))
    return min(1.0, max(0.0, front * _beta_fraction(x, a, b, max_iter) / a))


def gamma_cdf(x: float, a: float, max_iter: int = MAX_ITER) -> float:
    """Regularised lower gamma P(a,x); rate one."""
    if not math.isfinite(x) or not math.isfinite(a) or x < 0 or a <= 0:
        raise ValueError("gamma CDF needs positive shape and nonnegative x")
    if x == 0:
        return 0.0
    front = math.exp(a * math.log(x) - x - math.lgamma(a))
    if x < a + 1:
        # 8.7.1: exp(-x) sum x**k / Gamma(a+k+1), scaled by x**a.
        term = total = 1 / a
        for k in range(1, max_iter + 1):
            term *= x / (a + k)
            total += term
            if abs(term) < abs(total) * EPS:
                return min(1.0, max(0.0, front * total))
    else:
        # 8.9.2, upper incomplete gamma, equivalent contracted fraction.
        b = x + 1 - a
        c, d = 1 / TINY, 1 / _nonzero(b)
        h = d
        for k in range(1, max_iter + 1):
            coefficient = k * (a - k)
            b += 2
            d = 1 / _nonzero(b + coefficient * d)
            c = _nonzero(b + coefficient / c)
            delta = c * d
            h *= delta
            if abs(delta - 1) < EPS:
                return min(1.0, max(0.0, 1 - front * h))
    _fail()


def gamma_sf(x: float, a: float, max_iter: int = MAX_ITER) -> float:
    """Regularised upper gamma Q(a,x) = 1 - P(a,x); rate one. On the continued fraction's side (x >= a + 1) it is the
    fraction itself (DLMF 8.9.2), never 1 - P, so a far tail keeps its digits; below that it is 1 - P, where P is far
    from one."""
    if not math.isfinite(x) or not math.isfinite(a) or x < 0 or a <= 0:
        raise ValueError("gamma upper tail needs positive shape and nonnegative x")
    if x < a + 1:
        return 1 - gamma_cdf(x, a, max_iter)
    front = math.exp(a * math.log(x) - x - math.lgamma(a))
    b = x + 1 - a
    c, d = 1 / TINY, 1 / _nonzero(b)
    h = d
    for k in range(1, max_iter + 1):
        coefficient = k * (a - k)
        b += 2
        d = 1 / _nonzero(b + coefficient * d)
        c = _nonzero(b + coefficient / c)
        delta = c * d
        h *= delta
        if abs(delta - 1) < EPS:
            return min(1.0, max(0.0, front * h))
    _fail()


def bisect_quantile(q: float, cdf: Callable[[float], float], low: float, high: float) -> float:
    if not 0 < q < 1:
        raise ValueError("quantile probability must be between zero and one")
    for _ in range(MAX_BISECTIONS):
        mid = (low + high) / 2
        value = cdf(mid)
        if abs(value - q) <= 1e-12:
            return mid
        if value < q:
            low = mid
        else:
            high = mid
    _fail()


def beta_quantile(q: float, a: float, b: float, max_iter: int = MAX_ITER) -> float:
    return bisect_quantile(q, lambda x: beta_cdf(x, a, b, max_iter), 0, 1)


def gamma_quantile(q: float, a: float, max_iter: int = MAX_ITER) -> float:
    high = max(1.0, a)
    for _ in range(128):
        if gamma_cdf(high, a, max_iter) >= q:
            return bisect_quantile(q, lambda x: gamma_cdf(x, a, max_iter), 0, high)
        high *= 2
    _fail()


__all__ = ["EPS", "MAX_BISECTIONS", "MAX_ITER", "NotConverged", "TINY", "beta_cdf", "beta_quantile",
           "bisect_quantile", "gamma_cdf", "gamma_quantile", "gamma_sf", "log_beta"]
