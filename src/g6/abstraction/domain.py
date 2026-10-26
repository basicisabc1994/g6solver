# SPDX-License-Identifier: Apache-2.0
"""Concrete stratum (framework section 1).

A ConcreteDomain is C = (S, A, T, f, s0, B) restricted to what a dataset can
witness: a parameter space spec, a trace of evaluated configurations in the
order they were evaluated, and the fixed objective f read directly from data.

Axiom (A0), evaluator isolation: the objective is *data*, never computed from
abstraction state. Nothing in this package may write to `objective` values.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

Number = float
Config = dict[str, Any]


@dataclass(frozen=True)
class ParamSpec:
    """Specification of one dimension of the concrete search space."""

    name: str
    kind: str  # "float" | "int" | "categorical"
    low: float | None = None
    high: float | None = None
    choices: tuple[Any, ...] | None = None

    def validate(self, value: Any) -> bool:
        if self.kind == "categorical":
            return self.choices is not None and value in self.choices
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if self.kind == "int" and float(v) != int(v):
            return False
        lo = -math.inf if self.low is None else self.low
        hi = math.inf if self.high is None else self.high
        return lo <= v <= hi

    def normalise(self, value: Any) -> float:
        """Map a value into [0, 1] for metric-based abstractions."""
        if self.kind == "categorical":
            assert self.choices is not None
            return self.choices.index(value) / max(1, len(self.choices) - 1)
        lo, hi = self.low, self.high
        if lo is None or hi is None or hi <= lo:
            return float(value)
        return (float(value) - lo) / (hi - lo)


@dataclass
class Record:
    """One evaluated point of the concrete trace: (config, f(config))."""

    config: Config
    objective: Number
    index: int = 0  # position in the evaluation trace

    def __post_init__(self) -> None:
        # Single trace-integrity boundary (audit 001, F-04): every ingress — dataset import,
        # active-mode append, state-file hydration — builds a Record, so a NaN or infinite
        # objective can never enter the trace. The archive checked only the import path.
        value = float(self.objective)
        if not math.isfinite(value):
            raise ValueError(f"objective must be finite (NaN/inf rejected), got {self.objective!r}")
        self.objective = value


@dataclass
class ConcreteDomain:
    """The concrete search structure witnessed by a dataset.

    `records` is the evaluation *trace* in temporal order — this ordering is
    what replay-based evaluation of abstractions (section 4.iii) exploits.
    `maximise` fixes the direction of f. `budget` is B.
    """

    params: dict[str, ParamSpec]
    records: list[Record] = field(default_factory=list)
    objective_name: str = "objective"
    maximise: bool = True
    budget: int | None = None
    name: str = "domain"

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_rows(
        cls,
        rows: Iterable[dict[str, Any]],
        objective: str,
        maximise: bool = True,
        param_spec: dict[str, dict[str, Any]] | None = None,
        name: str = "domain",
    ) -> "ConcreteDomain":
        rows = list(rows)
        if not rows:
            raise ValueError("empty dataset")
        if objective not in rows[0]:
            raise ValueError(f"objective column {objective!r} not in dataset")
        param_names = [k for k in rows[0] if k != objective]

        specs: dict[str, ParamSpec] = {}
        for p in param_names:
            if param_spec and p in param_spec:
                d = param_spec[p]
                specs[p] = ParamSpec(
                    name=p,
                    kind=d.get("kind", "float"),
                    low=d.get("low"),
                    high=d.get("high"),
                    choices=tuple(d["choices"]) if "choices" in d else None,
                )
                continue
            values = [r[p] for r in rows]
            numeric = _all_numeric(values)
            if numeric:
                nums = [float(v) for v in values]
                is_int = all(float(v) == int(float(v)) for v in nums)
                specs[p] = ParamSpec(
                    name=p,
                    kind="int" if is_int else "float",
                    low=min(nums),
                    high=max(nums),
                )
            else:
                specs[p] = ParamSpec(
                    name=p, kind="categorical",
                    choices=tuple(sorted({str(v) for v in values})),
                )

        records = []
        for i, r in enumerate(rows):
            cfg = {p: _coerce(specs[p], r[p]) for p in param_names}
            records.append(Record(config=cfg, objective=float(r[objective]), index=i))
        return cls(params=specs, records=records, objective_name=objective,
                   maximise=maximise, name=name)

    @classmethod
    def from_csv(cls, path: str, objective: str, **kw: Any) -> "ConcreteDomain":
        with open(path, newline="") as fh:
            return cls.from_rows(list(csv.DictReader(fh)), objective, **kw)

    @classmethod
    def from_json(cls, path: str, objective: str, **kw: Any) -> "ConcreteDomain":
        with open(path) as fh:
            data = json.load(fh)
        rows = data["rows"] if isinstance(data, dict) and "rows" in data else data
        return cls.from_rows(rows, objective, **kw)

    # ------------------------------------------------------------------ #
    # Views
    # ------------------------------------------------------------------ #
    @property
    def param_names(self) -> list[str]:
        return list(self.params)

    def score(self, objective_value: Number) -> Number:
        """Canonical 'higher is better' view of f (A0-safe: pure reorientation)."""
        return objective_value if self.maximise else -objective_value

    def best(self) -> Record:
        return max(self.records, key=lambda r: self.score(r.objective))

    def vector(self, config: Config) -> tuple[float, ...]:
        """Normalised numeric embedding of a config (for metric abstractions)."""
        return tuple(self.params[p].normalise(config[p]) for p in self.param_names)

    def append(self, config: Config, objective: Number) -> Record:
        """Register a newly evaluated point (agent-in-the-loop active mode)."""
        for p, spec in self.params.items():
            if p not in config or not spec.validate(config[p]):
                raise ValueError(f"config invalid at parameter {p!r}: {config.get(p)!r}")
        rec = Record(config=dict(config), objective=float(objective),
                     index=len(self.records))
        self.records.append(rec)
        return rec

    def describe(self) -> dict[str, Any]:
        scores = [self.score(r.objective) for r in self.records]
        return {
            "name": self.name,
            "n_records": len(self.records),
            "objective": self.objective_name,
            "maximise": self.maximise,
            "best_score": max(scores) if scores else None,
            "worst_score": min(scores) if scores else None,
            "params": {
                p: {
                    "kind": s.kind,
                    **({"low": s.low, "high": s.high} if s.kind != "categorical" else {}),
                    **({"choices": list(s.choices)} if s.choices else {}),
                }
                for p, s in self.params.items()
            },
        }


def _all_numeric(values: Iterable[Any]) -> bool:
    for v in values:
        try:
            float(v)
        except (TypeError, ValueError):
            return False
    return True


def _coerce(spec: ParamSpec, value: Any) -> Any:
    if spec.kind == "categorical":
        return str(value)
    if spec.kind == "int":
        return int(float(value))
    return float(value)
