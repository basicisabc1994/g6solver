# SPDX-License-Identifier: Apache-2.0
"""Vehtari et al. (2021): split, rank and folded R-hat; bulk and tail ESS.

All diagnostics are recomputed here, including for draws returned by PyMC.
Constant chains have undefined R-hat and are refused. FFT autocovariances use
1/N (biased) normalisation; ESS uses between-chain variance, each chain's
autocorrelation weighted by its variance (equation 10), Geyer's initial positive,
monotone paired sequence, and the average of the sums truncated at the odd lag
and at the next even lag (p. 8). Source register: vehtari-2021.
"""
from __future__ import annotations

import math
import time
from statistics import NormalDist, fmean, median, variance

from g6.ml._types import DEADLINE, INPUT_INVALID, MLError


def deadline_check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise MLError(DEADLINE, "sampling and diagnostics exceeded timeout_s; no posterior returned")


def split(chains: list[list[float]]) -> list[list[float]]:
    if not 2 <= len(chains) <= 8 or not chains or not 4 <= len(chains[0]) <= 4000:
        raise MLError(INPUT_INVALID, "diagnostics need 2 to 8 chains, each with 4 to 4000 draws")
    n = len(chains[0])
    if any(len(c) != n or any(not math.isfinite(x) for x in c) for c in chains):
        raise MLError(INPUT_INVALID, "chains must have equal lengths and only finite draws")
    h = n // 2
    return [half for c in chains for half in (c[:h], c[-h:])]


def _rhat(chains: list[list[float]]) -> float | None:
    n = len(chains[0])
    w = fmean(variance(c) for c in chains)
    if w <= 0:
        return None
    b = n * variance([fmean(c) for c in chains])
    return math.sqrt(((n-1)*w/n + b/n) / w)


def split_rhat(chains: list[list[float]]) -> float | None:
    """Untransformed split R-hat, equations 1-4 (exposed for known-value tests)."""
    return _rhat(split(chains))


def rank_normalise(chains: list[list[float]]) -> list[list[float]]:
    flat = [v for c in chains for v in c]
    order = sorted(range(len(flat)), key=flat.__getitem__)
    ranks = [0.] * len(flat)
    i = 0
    normal = NormalDist()
    while i < len(order):
        j = i + 1
        while j < len(order) and flat[order[j]] == flat[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2
        z = normal.inv_cdf((rank - 3/8) / (len(flat) + 1/4))
        for k in order[i:j]:
            ranks[k] = z
        i = j
    n = len(chains[0])
    return [ranks[i:i+n] for i in range(0, len(flat), n)]


def _fft(values: list[complex], inverse: bool = False) -> list[complex]:
    """Radix-two transform, zero-padded by autocovariance; no optional package."""
    a, n, j = list(values), len(values), 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        angle = (2 if inverse else -2) * math.pi / size
        root = complex(math.cos(angle), math.sin(angle))
        for start in range(0, n, size):
            w = 1+0j
            for k in range(start, start + size//2):
                u, v = a[k], a[k+size//2]*w
                a[k], a[k+size//2] = u+v, u-v
                w *= root
        size *= 2
    return [v/n for v in a] if inverse else a


def autocovariance(values: list[float]) -> list[float]:
    n = len(values)
    size = 1 << (2*n - 1).bit_length()
    avg = fmean(values)
    freq = _fft([complex(v-avg) for v in values] + [0j]*(size-n))
    return [v.real/n for v in _fft([v*v.conjugate() for v in freq], True)[:n]]


def ess(chains: list[list[float]], deadline: float | None = None) -> float:
    m, n = len(chains), len(chains[0])
    covs = []
    for c in chains:
        deadline_check(deadline)
        covs.append(autocovariance(c))
    w = fmean(c[0] for c in covs) * n/(n-1)
    var_plus = w*(n-1)/n + variance([fmean(c) for c in chains])
    if var_plus <= 0 or w <= 0:
        return 0.
    # Equation 10: each chain's s2_m * rho_t,m, which with divisor-N autocovariances is acov_t * N/(N-1).
    rho = [1.] + [1 - (w - fmean(c[t] for c in covs)*n/(n-1))/var_plus for t in range(1, n)]
    # Equation 13: P_0, then each further pair while it is positive, made monotone.
    pairs = [rho[0] + rho[1]]
    for i in range(2, n-1, 2):
        pair = rho[i] + rho[i+1]
        if pair <= 0:
            break
        pairs.append(min(pair, pairs[-1]))
    # Page 8: the average of the sums truncated at the usual odd lag and at the next even lag.
    even = 2*len(pairs)
    tau = max(-1 + 2*math.fsum(pairs) + (rho[even] if even < n else 0.), 1/math.log10(m*n))
    return m*n/tau


def empirical_quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    at = (len(ordered)-1)*q
    i = int(at)
    return ordered[i] + (at-i)*(ordered[min(i+1, len(ordered)-1)] - ordered[i])


def assess(chains: list[list[float]], deadline: float | None = None) -> dict:
    deadline_check(deadline)
    halves = split(chains)
    flat = [x for c in halves for x in c]
    ranked = rank_normalise(halves)
    centre = median(flat)
    folded = rank_normalise([[abs(v-centre) for v in c] for c in halves])
    rank_rhat, folded_rhat = _rhat(ranked), _rhat(folded)
    rhat = max(rank_rhat, folded_rhat) if rank_rhat is not None and folded_rhat is not None else None
    bulk = ess(ranked, deadline)
    tails = []
    for q in (.05, .95):
        threshold = empirical_quantile(flat, q)
        tails.append(ess([[float(x <= threshold) for x in c] for c in halves], deadline))
    tail = min(tails)
    deadline_check(deadline)
    return {"rank_rhat": rank_rhat, "folded_rhat": folded_rhat, "rhat": rhat,
            "bulk_ess": bulk, "tail_ess": tail, "tail_ess_05": tails[0], "tail_ess_95": tails[1],
            "converged": rhat is not None and rhat < 1.01 and bulk > 400 and tail > 400,
            "thresholds": {"rhat_below": 1.01, "bulk_ess_above": 400, "tail_ess_above": 400},
            "method": "split, pooled average ranks, rank-normalised and folded R-hat maximum; bulk and tail ESS",
            "split_chains": len(halves), "draws_per_split": len(halves[0]),
            "dropped_middle_draws": len(chains) if len(chains[0]) % 2 else 0}