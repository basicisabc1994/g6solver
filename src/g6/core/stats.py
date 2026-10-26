# SPDX-License-Identifier: Apache-2.0
"""The core's statistics block, in plain Python on :mod:`g6.core.special`'s regularised incomplete beta and gamma.

Distribution functions, their upper tails and inverses: the normal (``normal_cdf``, ``normal_sf``, ``normal_ppf``), t
(``t_*``, any positive degrees of freedom, Welch's included), chi-squared (``chi2_*``) and F (``f_*``, numerator then
denominator degrees of freedom). Tests: Welch's two-sample t with the Welch-Satterthwaite degrees of freedom (NIST
e-Handbook 1.3.5.3); the pooled two-proportion z (NIST 7.3.3, no continuity correction); one-sample sample sizes (NIST
7.2.2.2: the normal formula, and one step of its t iteration at given degrees of freedom).

Every input outside a function's domain raises :class:`ValueError`, never a clipped or approximate answer. Each
small tail is computed directly, never as one minus the other side: the normal's lower tail and the chi-squared upper
tail from the regularised upper gamma (:func:`g6.core.special.gamma_sf`), the F upper tail from the incomplete beta
with its arguments exchanged, the t tails from the incomplete beta. A quantile is found by bisection on its
distribution function: for chi-squared and F, whose support is positive, to about 1e-14 of its size; for the normal
and t, to about 1e-14 of its size above 1 and 1e-14 absolute below it. It is refused for a probability below 1e-10 or
above 1 - 1e-10 (G6's own bound); near 1 - 1e-10 a distribution function's value is no finer than floating point's
spacing below one (about 1e-16), so an upper quantile there is good to about 1e-6. Nothing here labels anything: the
components that use these functions cite the sources in their own certificates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from g6.core.special import NotConverged, beta_cdf, gamma_cdf, gamma_sf

#: The smallest and largest probabilities a quantile is computed for (G6's own bound, from the tails' accuracy).
P_MIN = 1e-10
P_MAX = 1 - 1e-10
#: The most bisections a quantile takes; one more is refused, never answered approximately.
MAX_BISECTIONS = 400
#: The most doublings of a bracket's upper end before a quantile is refused.
MAX_DOUBLINGS = 1100


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not {value!r}")
    return float(value)


def _positive(name: str, value: float) -> float:
    found = _finite(name, value)
    if found <= 0:
        raise ValueError(f"{name} must be greater than zero, not {value!r}")
    return found


def _nonnegative(name: str, value: float) -> float:
    found = _finite(name, value)
    if found < 0:
        raise ValueError(f"{name} must not be negative, not {value!r}")
    return found


def _probability(p: float) -> float:
    found = _finite("p", p)
    if not P_MIN <= found <= P_MAX:
        raise ValueError(f"p must lie between {P_MIN} and 1 - {P_MIN} for a quantile, not {p!r}")
    return found


def _invert(cdf, p: float, low: float, high: float, *, positive: bool = False) -> float:
    """The x in [low, high] with cdf(x) = p, by bisection; ``high`` is doubled until cdf(high) >= p. On a ``positive``
    support the bracket stops at 1e-14 of the root's size however small it is; otherwise at 1e-14 absolute below 1."""
    for _ in range(MAX_DOUBLINGS):
        if cdf(high) >= p:
            break
        low, high = high, high * 2 if high > 0 else 1.0
    else:
        raise NotConverged("no bracket for the quantile within its bound")
    for _ in range(MAX_BISECTIONS):
        middle = (low + high) / 2
        if high - low <= 1e-14 * (abs(middle) if positive else max(1.0, abs(middle))):
            return middle
        if cdf(middle) < p:
            low = middle
        else:
            high = middle
    raise NotConverged("the quantile did not converge within its bound")


# --- the normal distribution ---------------------------------------------------------------------------------------

def normal_cdf(z: float) -> float:
    """Phi(z), the standard normal distribution function: 1/2 (1 + P(1/2, z^2/2)) for z >= 0, and the lower tail
    Phi(z) = 1 - Phi(-z) = Q(1/2, z^2/2) / 2 for z < 0, from the upper gamma directly."""
    z = _finite("z", z)
    if z < 0:
        return 0.5 * gamma_sf(z * z / 2, 0.5)
    return 0.5 + 0.5 * gamma_cdf(z * z / 2, 0.5)


def normal_sf(z: float) -> float:
    """The upper tail, 1 - Phi(z) = Phi(-z)."""
    return normal_cdf(-_finite("z", z))


def normal_ppf(p: float) -> float:
    """The z with Phi(z) = p."""
    return _invert(normal_cdf, _probability(p), -40.0, 40.0)


# --- Student's t ---------------------------------------------------------------------------------------------------

def t_cdf(t: float, df: float) -> float:
    """F(t; df): with x = df / (df + t^2), the tail beyond |t| is I_x(df/2, 1/2) / 2."""
    t, df = _finite("t", t), _positive("df", df)
    tail = 0.5 * beta_cdf(df / (df + t * t), df / 2, 0.5)
    return 1 - tail if t > 0 else tail


def t_sf(t: float, df: float) -> float:
    """The upper tail, 1 - F(t; df) = F(-t; df)."""
    return t_cdf(-_finite("t", t), df)


def t_ppf(p: float, df: float) -> float:
    """The t with F(t; df) = p."""
    p, df = _probability(p), _positive("df", df)
    if p < 0.5:
        return -t_ppf(1 - p, df)
    return _invert(lambda t: t_cdf(t, df), p, 0.0, 1.0)


# --- chi-squared ---------------------------------------------------------------------------------------------------

def chi2_cdf(x: float, df: float) -> float:
    """F(x; df) = P(df/2, x/2), the regularised lower incomplete gamma."""
    x, df = _nonnegative("x", x), _positive("df", df)
    return gamma_cdf(x / 2, df / 2)


def chi2_sf(x: float, df: float) -> float:
    """The upper tail, 1 - F(x; df) = Q(df/2, x/2), the regularised upper incomplete gamma computed directly."""
    x, df = _nonnegative("x", x), _positive("df", df)
    return gamma_sf(x / 2, df / 2)


def chi2_ppf(p: float, df: float) -> float:
    """The x with F(x; df) = p."""
    p, df = _probability(p), _positive("df", df)
    return _invert(lambda x: chi2_cdf(x, df), p, 0.0, max(1.0, df), positive=True)


# --- F -------------------------------------------------------------------------------------------------------------

def f_cdf(x: float, df_num: float, df_den: float) -> float:
    """F(x; m, n) = I_{m x / (m x + n)}(m/2, n/2), m the numerator's and n the denominator's degrees of freedom."""
    x, m, n = _nonnegative("x", x), _positive("df_num", df_num), _positive("df_den", df_den)
    return beta_cdf(m * x / (m * x + n), m / 2, n / 2) if x > 0 else 0.0


def f_sf(x: float, df_num: float, df_den: float) -> float:
    """The upper tail, 1 - F(x; m, n) = I_{n / (m x + n)}(n/2, m/2), computed directly."""
    x, m, n = _nonnegative("x", x), _positive("df_num", df_num), _positive("df_den", df_den)
    return beta_cdf(n / (m * x + n), n / 2, m / 2) if x > 0 else 1.0


def f_ppf(p: float, df_num: float, df_den: float) -> float:
    """The x with F(x; m, n) = p."""
    p, m, n = _probability(p), _positive("df_num", df_num), _positive("df_den", df_den)
    return _invert(lambda x: f_cdf(x, m, n), p, 0.0, 1.0, positive=True)


# --- tests -----------------------------------------------------------------------------------------------------------

def _count(name: str, value: int, least: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        raise ValueError(f"{name} must be a whole number of at least {least}, not {value!r}")
    return value


@dataclass(frozen=True)
class Welch:
    """Welch's two-sample t: group 1 minus group 2."""

    t: float
    df: float
    se: float
    mean_difference: float
    p_two_sided: float


def welch_t(mean1: float, sd1: float, n1: int, mean2: float, sd2: float, n2: int) -> Welch:
    """NIST 1.3.5.3, unequal variances: T = (Y1 - Y2) / sqrt(s1^2/N1 + s2^2/N2), with the Welch-Satterthwaite
    degrees of freedom nu = (s1^2/N1 + s2^2/N2)^2 / [(s1^2/N1)^2/(N1 - 1) + (s2^2/N2)^2/(N2 - 1)], not rounded; the
    two-sided p from the t distribution at nu."""
    mean1, mean2 = _finite("mean1", mean1), _finite("mean2", mean2)
    sd1, sd2 = _positive("sd1", sd1), _positive("sd2", sd2)
    n1, n2 = _count("n1", n1, 2), _count("n2", n2, 2)
    a, b = sd1 * sd1 / n1, sd2 * sd2 / n2
    se = math.sqrt(a + b)
    difference = mean1 - mean2
    t = difference / se
    df = (a + b) ** 2 / (a * a / (n1 - 1) + b * b / (n2 - 1))
    return Welch(t, df, se, difference, 2 * t_sf(abs(t), df))


@dataclass(frozen=True)
class TwoProportion:
    z: float
    p1: float
    p2: float
    pooled: float
    se: float
    p_two_sided: float


def two_proportion_z(x1: int, n1: int, x2: int, n2: int) -> TwoProportion:
    """NIST 7.3.3: z = (p1 - p2) / sqrt(p (1 - p) (1/n1 + 1/n2)), p = (x1 + x2) / (n1 + n2), no continuity
    correction; ``x`` defects out of ``n``. A pooled proportion of 0 or 1 leaves no standard error and is refused."""
    n1, n2 = _count("n1", n1, 1), _count("n2", n2, 1)
    x1, x2 = _count("x1", x1, 0), _count("x2", x2, 0)
    if x1 > n1 or x2 > n2:
        raise ValueError("a count of defects cannot exceed its sample size")
    pooled = (x1 + x2) / (n1 + n2)
    if pooled in (0.0, 1.0):
        raise ValueError("a pooled proportion of 0 or 1 leaves no standard error")
    p1, p2 = x1 / n1, x2 / n2
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    z = (p1 - p2) / se
    return TwoProportion(z, p1, p2, pooled, se, 2 * normal_sf(abs(z)))


@dataclass(frozen=True)
class SampleSize:
    """A sample size before rounding, and the two critical values it rests on."""

    n: float
    quantile_alpha: float
    quantile_beta: float


def _size(alpha: float, beta: float, shift_in_sd: float, sides: int) -> tuple[float, float, float]:
    alpha, beta = _finite("alpha", alpha), _finite("beta", beta)
    if not 0 < alpha < 1 or not 0 < beta < 1:
        raise ValueError("alpha and beta must lie strictly between 0 and 1")
    if sides not in (1, 2) or isinstance(sides, bool):
        raise ValueError("sides is 1 (a one-sided test) or 2 (a two-sided test)")
    return (1 - alpha / 2 if sides == 2 else 1 - alpha), 1 - beta, _positive("shift_in_sd", shift_in_sd)


def sample_size_normal(alpha: float, beta: float, shift_in_sd: float, sides: int) -> SampleSize:
    """NIST 7.2.2.2, standard deviation known: N = (z_{1-alpha/2} + z_{1-beta})^2 (sigma/delta)^2 two-sided and
    N = (z_{1-alpha} + z_{1-beta})^2 (sigma/delta)^2 one-sided; ``shift_in_sd`` is delta/sigma. Not rounded."""
    upper, power, shift = _size(alpha, beta, shift_in_sd, sides)
    za, zb = normal_ppf(upper), normal_ppf(power)
    return SampleSize((za + zb) ** 2 / (shift * shift), za, zb)


def sample_size_t(alpha: float, beta: float, shift_in_sd: float, sides: int, df: float) -> SampleSize:
    """One step of NIST 7.2.2.2's iteration, standard deviation unknown: the same formula with t critical values at
    ``df`` degrees of freedom (N - 1 of the previous estimate). Not rounded."""
    upper, power, shift = _size(alpha, beta, shift_in_sd, sides)
    df = _positive("df", df)
    ta, tb = t_ppf(upper, df), t_ppf(power, df)
    return SampleSize((ta + tb) ** 2 / (shift * shift), ta, tb)


__all__ = ["MAX_BISECTIONS", "MAX_DOUBLINGS", "P_MAX", "P_MIN", "SampleSize", "TwoProportion", "Welch", "chi2_cdf",
           "chi2_ppf", "chi2_sf", "f_cdf", "f_ppf", "f_sf", "normal_cdf", "normal_ppf", "normal_sf",
           "sample_size_normal", "sample_size_t", "t_cdf", "t_ppf", "t_sf", "two_proportion_z", "welch_t"]
