# SPDX-License-Identifier: Apache-2.0
"""Abstractions as first-class objects (framework sections 2, 8.1, 8.3).

Every mechanism implements the Abstraction interface:

    fit(domain, upto)       -> alpha: build abstract state from the visited
                               prefix of the concrete trace (INCREMENTAL:
                               extending the same domain's prefix absorbs
                               only the new records, so streaming refits in
                               replay/gate/MDL are O(n) not O(n^2))
    covers(config)          -> gamma-membership
    bound(config)           -> upper bound on score over the covering cell
    prune(config, incumbent)-> safe-pruning decision
    cell_of / n_cells       -> abstract cell identity / count
    refine(cex, score)      -> locally refined copy (CEGAR operator R)
    spec()                  -> JSON-serialisable description (validated)
    signature               -> "trace-generic" mechanisms read only the
                               normalised vector + score stream; the Lift
                               functor applies to exactly those.

Denotation from_spec() VALIDATES specs against hard resource limits before
construction — an agent-supplied spec is untrusted input.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from typing import Any

import numpy as np

from .domain import Config, ConcreteDomain

TRACE_GENERIC = "trace-generic"

# Hard limits on agent-supplied specs (remediation: spec bombs).
LIMITS = {
    "max_depth": 4,        # product nesting
    "max_bins": 64,
    "max_arms": 64,
    "min_radius": 1e-3,
    "max_radius": 2.0,
    "max_lipschitz": 1e6,
}


class SpecError(ValueError):
    """Raised when an agent-supplied spec violates validation limits."""


class Abstraction:
    """Base class with incremental fitting.

    Subclasses implement _reset(domain) and _absorb(record); fit() routes to
    the incremental path when the same domain's prefix is being extended.
    """

    KIND = "abstract"
    signature: str = TRACE_GENERIC

    def __init__(self) -> None:
        self._domain: ConcreteDomain | None = None
        self._fitted_n: int | None = None

    # -- alpha (incremental) --------------------------------------------- #
    def fit(self, domain: ConcreteDomain, upto: int | None = None) -> "Abstraction":
        n = len(domain.records) if upto is None else min(upto, len(domain.records))
        if (self._domain is domain and self._fitted_n is not None
                and self._fitted_n <= n):
            for rec in domain.records[self._fitted_n:n]:
                self._absorb(rec)
        else:
            self._domain = domain
            self._reset(domain)
            for rec in domain.records[:n]:
                self._absorb(rec)
        self._fitted_n = n
        return self

    def _reset(self, domain: ConcreteDomain) -> None:
        raise NotImplementedError

    def _absorb(self, rec: Any) -> None:
        raise NotImplementedError

    # -- interface -------------------------------------------------------- #
    def covers(self, config: Config) -> bool:
        raise NotImplementedError

    def bound(self, config: Config) -> float:
        raise NotImplementedError

    def prune(self, config: Config, incumbent: float) -> bool:
        return self.covers(config) and self.bound(config) <= incumbent

    def cell_of(self, config: Config) -> Any:
        raise NotImplementedError

    def n_cells(self) -> int:
        raise NotImplementedError

    def refine(self, cex_config: Config,
               cex_score: float | None = None) -> "Abstraction":
        raise NotImplementedError

    def spec(self) -> dict[str, Any]:
        raise NotImplementedError

    def mdl_bits(self) -> float:
        return 8.0 * len(json.dumps(self.spec(), sort_keys=True))

    def _vec(self, config: Config) -> tuple[float, ...]:
        assert self._domain is not None, "fit() before use"
        return self._domain.vector(config)


# ======================================================================== #
# Quotient: similarity quotient over ALL visited points (monotone gamma),
# numpy-vectorised neighbourhood queries.
# ======================================================================== #
@dataclass
class _Ball:
    centre: tuple[float, ...]
    radius: float
    best: float
    count: int = 1


class Quotient(Abstraction):
    """Similarity quotient of the trace space.

    gamma is defined over ALL visited points: covers(c) iff some visited
    point lies within `radius` — monotone in the visited set by
    construction (the property-based gate caught the greedy-centre version
    violating exactly this). Balls (greedy centres) remain the cell
    structure for MDL/precision accounting only.

    bound(c) = max score among visited points within radius + lipschitz *
    radius: sound whenever f is `lipschitz`-Lipschitz under the normalised
    metric; with an estimated constant it is epsilon-sound and the
    prospective gate + CEGAR measure and repair the discrepancy.
    """

    KIND = "quotient"
    signature = TRACE_GENERIC

    def __init__(self, radius: float = 0.1, lipschitz: float = 1.0,
                 metric: str = "linf") -> None:
        super().__init__()
        if not (LIMITS["min_radius"] <= radius <= LIMITS["max_radius"]):
            raise SpecError(f"radius must be in "
                            f"[{LIMITS['min_radius']}, {LIMITS['max_radius']}]")
        if not (0.0 <= lipschitz <= LIMITS["max_lipschitz"]):
            raise SpecError("lipschitz out of range")
        if metric not in ("linf", "l2"):
            raise SpecError("metric must be 'linf' or 'l2'")
        self.radius = float(radius)
        self.lipschitz = float(lipschitz)
        self.metric = metric
        self._pts: list[tuple[float, ...]] = []
        self._scores: list[float] = []
        self._ball_of: list[int] = []
        self._balls: list[_Ball] = []
        self._P: np.ndarray | None = None  # cached matrix, invalidated on absorb
        self._S: np.ndarray | None = None

    # -- incremental alpha ------------------------------------------------ #
    def _reset(self, domain: ConcreteDomain) -> None:
        self._pts, self._scores, self._ball_of, self._balls = [], [], [], []
        self._P = self._S = None

    def _absorb(self, rec: Any) -> None:
        assert self._domain is not None
        v = self._domain.vector(rec.config)
        s = self._domain.score(rec.objective)
        bid = None
        for j, b in enumerate(self._balls):
            if self._dist1(v, b.centre) <= b.radius:
                b.best = max(b.best, s)
                b.count += 1
                bid = j
                break
        if bid is None:
            self._balls.append(_Ball(centre=v, radius=self.radius, best=s))
            bid = len(self._balls) - 1
        self._pts.append(v)
        self._scores.append(s)
        self._ball_of.append(bid)
        self._P = self._S = None

    def _dist1(self, a: tuple[float, ...], b: tuple[float, ...]) -> float:
        if self.metric == "l2":
            return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
        return max((abs(x - y) for x, y in zip(a, b)), default=0.0)

    def _mat(self) -> tuple[np.ndarray, np.ndarray]:
        if self._P is None:
            self._P = (np.asarray(self._pts, dtype=float)
                       if self._pts else np.zeros((0, 1)))
            self._S = np.asarray(self._scores, dtype=float)
        return self._P, self._S  # type: ignore[return-value]

    def _near_mask(self, v: tuple[float, ...]) -> np.ndarray:
        P, _ = self._mat()
        if P.shape[0] == 0:
            return np.zeros(0, dtype=bool)
        diff = np.abs(P - np.asarray(v, dtype=float))
        d = (np.sqrt((diff ** 2).sum(axis=1)) if self.metric == "l2"
             else diff.max(axis=1))
        return d <= self.radius

    # -- interface -------------------------------------------------------- #
    def covers(self, config: Config) -> bool:
        return bool(self._near_mask(self._vec(config)).any())

    def bound(self, config: Config) -> float:
        m = self._near_mask(self._vec(config))
        if not m.any():
            return math.inf
        _, S = self._mat()
        return float(S[m].max()) + self.lipschitz * self.radius

    def support_distance(self, config: Config) -> float:
        """Normalised distance to the nearest visited point (extrapolation
        guard input; +inf when nothing is fitted)."""
        P, _ = self._mat()
        if P.shape[0] == 0:
            return math.inf
        diff = np.abs(P - np.asarray(self._vec(config), dtype=float))
        d = (np.sqrt((diff ** 2).sum(axis=1)) if self.metric == "l2"
             else diff.max(axis=1))
        return float(d.min())

    def cell_of(self, config: Config) -> Any:
        m = self._near_mask(self._vec(config))
        if not m.any():
            return None
        P, _ = self._mat()
        diff = np.abs(P - np.asarray(self._vec(config), dtype=float))
        d = (np.sqrt((diff ** 2).sum(axis=1)) if self.metric == "l2"
             else diff.max(axis=1))
        d = np.where(m, d, np.inf)
        return self._ball_of[int(d.argmin())]

    def n_cells(self) -> int:
        return max(1, len(self._balls))

    def refine(self, cex_config: Config,
               cex_score: float | None = None) -> "Quotient":
        """Adaptive CEGAR split: if the counterexample's score is known,
        raise lipschitz by exactly the violation gap / radius (the minimal
        repair making that cex sound) and shrink the radius by one-third to
        localise; otherwise halve the radius. Both strictly refine."""
        new_r, new_l = self.radius, self.lipschitz
        if cex_score is not None and self._pts:
            m = self._near_mask(self._vec(cex_config))
            _, S = self._mat()
            below = S[m][S[m] < cex_score] if m.any() else np.zeros(0)
            if below.size:
                gap = cex_score - (float(below.max()) + self.lipschitz * self.radius)
                if gap > 0:
                    new_l = min(LIMITS["max_lipschitz"],
                                self.lipschitz + gap / self.radius + 1e-6)
            new_r = max(LIMITS["min_radius"], self.radius * (2 / 3))
        else:
            new_r = max(LIMITS["min_radius"], self.radius / 2)
        new = Quotient(new_r, new_l, self.metric)
        if self._domain is not None:
            new.fit(self._domain, upto=self._fitted_n)
        return new

    def spec(self) -> dict[str, Any]:
        return {"kind": self.KIND, "radius": self.radius,
                "lipschitz": self.lipschitz, "metric": self.metric,
                "signature": self.signature}


# ======================================================================== #
# Project: quotient by projection onto a subset of dims, discretised
# ======================================================================== #
class Project(Abstraction):
    """Quotient by projection onto `dims`, each split into `bins` intervals.
    Abstract state: per-cell best observed score. bound = cell best + slack
    for in-cell width plus forgotten dimensions (conservative; the
    prospective gate measures its adequacy empirically)."""

    KIND = "project"
    signature = TRACE_GENERIC

    def __init__(self, dims: list[str], bins: int = 4,
                 lipschitz: float = 1.0) -> None:
        super().__init__()
        if not dims or len(set(dims)) != len(dims):
            raise SpecError("dims must be a non-empty list of unique names")
        if not (2 <= int(bins) <= LIMITS["max_bins"]):
            raise SpecError(f"bins must be in [2, {LIMITS['max_bins']}]")
        if not (0.0 <= lipschitz <= LIMITS["max_lipschitz"]):
            raise SpecError("lipschitz out of range")
        self.dims = list(dims)
        self.bins = int(bins)
        self.lipschitz = float(lipschitz)
        self._cells: dict[tuple[int, ...], float] = {}

    def _key(self, config: Config) -> tuple[int, ...]:
        assert self._domain is not None
        out = []
        for d in self.dims:
            x = self._domain.params[d].normalise(config[d])
            out.append(min(int(x * self.bins), self.bins - 1))
        return tuple(out)

    def _reset(self, domain: ConcreteDomain) -> None:
        missing = [d for d in self.dims if d not in domain.params]
        if missing:
            raise SpecError(f"unknown dims: {missing}")
        self._cells = {}

    def _absorb(self, rec: Any) -> None:
        assert self._domain is not None
        k = self._key(rec.config)
        s = self._domain.score(rec.objective)
        self._cells[k] = max(self._cells.get(k, -math.inf), s)

    def covers(self, config: Config) -> bool:
        return self._key(config) in self._cells

    def bound(self, config: Config) -> float:
        k = self._key(config)
        if k not in self._cells:
            return math.inf
        assert self._domain is not None
        n_ignored = len(self._domain.params) - len(self.dims)
        slack = self.lipschitz * (1.0 / self.bins + 0.5 * n_ignored /
                                  max(1, len(self._domain.params)))
        return self._cells[k] + slack

    def cell_of(self, config: Config) -> Any:
        return self._key(config)

    def n_cells(self) -> int:
        return max(1, len(self._cells))

    def refine(self, cex_config: Config,
               cex_score: float | None = None) -> "Project":
        new = Project(self.dims, min(LIMITS["max_bins"], self.bins * 2),
                      self.lipschitz)
        if self._domain is not None:
            new.fit(self._domain, upto=self._fitted_n)
        return new

    def spec(self) -> dict[str, Any]:
        return {"kind": self.KIND, "dims": self.dims, "bins": self.bins,
                "lipschitz": self.lipschitz, "signature": self.signature}


# ======================================================================== #
# Sufficient: per-dimension sufficient statistics (bandit arms)
# ======================================================================== #
class Sufficient(Abstraction):
    """Forget everything about a dimension except per-arm score statistics.
    Guidance-only: bound is +inf so safe pruning never fires; value shows up
    through UCB reordering in replay."""

    KIND = "sufficient"
    signature = TRACE_GENERIC

    def __init__(self, dim: str, arms: int = 4) -> None:
        super().__init__()
        if not (2 <= int(arms) <= LIMITS["max_arms"]):
            raise SpecError(f"arms must be in [2, {LIMITS['max_arms']}]")
        self.dim = dim
        self.arms = int(arms)
        self._stats: dict[int, list[float]] = {}

    def _arm(self, config: Config) -> int:
        assert self._domain is not None
        x = self._domain.params[self.dim].normalise(config[self.dim])
        return min(int(x * self.arms), self.arms - 1)

    def _reset(self, domain: ConcreteDomain) -> None:
        if self.dim not in domain.params:
            raise SpecError(f"unknown dim {self.dim!r}")
        self._stats = {}

    def _absorb(self, rec: Any) -> None:
        assert self._domain is not None
        self._stats.setdefault(self._arm(rec.config), []).append(
            self._domain.score(rec.objective))

    def covers(self, config: Config) -> bool:
        return self._arm(config) in self._stats

    def bound(self, config: Config) -> float:
        return math.inf

    def ucb(self, config: Config, c: float = 1.0) -> float:
        arm = self._arm(config)
        vals = self._stats.get(arm)
        if not vals:
            return math.inf
        total = sum(len(v) for v in self._stats.values())
        return statistics.fmean(vals) + c * math.sqrt(
            math.log(max(2, total)) / len(vals))

    def cell_of(self, config: Config) -> Any:
        return self._arm(config)

    def n_cells(self) -> int:
        return max(1, len(self._stats))

    def refine(self, cex_config: Config,
               cex_score: float | None = None) -> "Sufficient":
        new = Sufficient(self.dim, min(LIMITS["max_arms"], self.arms * 2))
        if self._domain is not None:
            new.fit(self._domain, upto=self._fitted_n)
        return new

    def spec(self) -> dict[str, Any]:
        return {"kind": self.KIND, "dim": self.dim, "arms": self.arms,
                "signature": self.signature}


# ======================================================================== #
# Product: composition (Proposition 1 executable)
# ======================================================================== #
class Product(Abstraction):
    """Conjunction: gamma is the intersection, so a sound bound is min of
    component bounds — composition tightens, never loosens."""

    KIND = "product"

    def __init__(self, left: Abstraction, right: Abstraction) -> None:
        super().__init__()
        self.left = left
        self.right = right
        self.signature = (TRACE_GENERIC
                          if left.signature == right.signature == TRACE_GENERIC
                          else "domain-specific")

    def fit(self, domain: ConcreteDomain, upto: int | None = None) -> "Product":
        self._domain = domain
        self.left.fit(domain, upto)
        self.right.fit(domain, upto)
        self._fitted_n = self.left._fitted_n
        return self

    def _reset(self, domain: ConcreteDomain) -> None:  # delegated via fit
        pass

    def _absorb(self, rec: Any) -> None:
        # Delegate so record-by-record learning (counterfactual replay) reaches both children.
        self.left._absorb(rec)
        self.right._absorb(rec)

    def covers(self, config: Config) -> bool:
        return self.left.covers(config) and self.right.covers(config)

    def bound(self, config: Config) -> float:
        return min(self.left.bound(config), self.right.bound(config))

    def cell_of(self, config: Config) -> Any:
        return (self.left.cell_of(config), self.right.cell_of(config))

    def n_cells(self) -> int:
        return self.left.n_cells() * self.right.n_cells()

    def refine(self, cex_config: Config,
               cex_score: float | None = None) -> "Product":
        # The product's bound is min(left, right), so a violation is caused by every child
        # whose own bound lies below the observed score; refine exactly those (audit 001,
        # F-07: the archive refined the LARGER-bound child, which cannot repair a min).
        # Without a score, refine the binding (smaller-bound) child.
        lb, rb = self.left.bound(cex_config), self.right.bound(cex_config)
        if cex_score is not None:
            left_bad, right_bad = lb < cex_score, rb < cex_score
        else:
            left_bad, right_bad = lb <= rb, rb < lb
        left = self.left.refine(cex_config, cex_score) if left_bad else self.left
        right = self.right.refine(cex_config, cex_score) if right_bad else self.right
        return Product(left, right)

    def spec(self) -> dict[str, Any]:
        return {"kind": self.KIND, "left": self.left.spec(),
                "right": self.right.spec(), "signature": self.signature}


# ======================================================================== #
# Validated denotation [[.]]
# ======================================================================== #
def from_spec(spec: dict[str, Any], _depth: int = 1) -> Abstraction:
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    if _depth > LIMITS["max_depth"]:
        raise SpecError(f"product nesting exceeds max depth {LIMITS['max_depth']}")
    kind = spec.get("kind")
    if kind == "quotient":
        return Quotient(radius=spec.get("radius", 0.1),
                        lipschitz=spec.get("lipschitz", 1.0),
                        metric=spec.get("metric", "linf"))
    if kind == "project":
        return Project(dims=spec.get("dims", []), bins=spec.get("bins", 4),
                       lipschitz=spec.get("lipschitz", 1.0))
    if kind == "sufficient":
        return Sufficient(dim=spec.get("dim", ""), arms=spec.get("arms", 4))
    if kind == "product":
        if "left" not in spec or "right" not in spec:
            raise SpecError("product requires 'left' and 'right'")
        return Product(from_spec(spec["left"], _depth + 1),
                       from_spec(spec["right"], _depth + 1))
    raise SpecError(f"unknown combinator kind: {kind!r}; "
                    f"valid kinds: {sorted(COMBINATORS)}")


COMBINATORS = {
    "quotient": "Similarity quotient over the visited trace (Tabu-style). "
                "Params: radius in [1e-3, 2], lipschitz >= 0, metric in "
                "{linf, l2}.",
    "project": "Quotient by projection onto a dim subset, discretised. "
               "Params: dims [unique names], bins in [2, 64], lipschitz.",
    "sufficient": "Per-dimension sufficient statistics (bandit arms); "
                  "guidance-only, never prunes. Params: dim, arms in [2, 64].",
    "product": "Conjunction of two abstractions; bounds tighten (min). "
               "Params: left, right (nested specs, depth <= 4).",
}
