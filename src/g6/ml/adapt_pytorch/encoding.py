# SPDX-License-Identifier: Apache-2.0
"""Rows to numbers for a network, fitted on the rows it trains on and nothing else, in G6's process (pure Python).

* Numbers: a missing value takes the fitting rows' median, with a missing-value column beside a feature that had a
  missing value there; then each is standardised by the fitting rows' mean and standard deviation.
* Text: one column per value among the fitting rows' :data:`MAX_CATEGORIES` most frequent (a missing value is its own
  value), the rarer ones together in one more column; a value never seen there sets none of them.
* Targets: a class becomes its index among the training classes; a regression target is standardised by the fitting
  rows' mean and standard deviation, and predictions are turned back into its units.

The same encoding, kept as JSON beside a kept model, encodes the rows it later predicts. The encoded rows are built in
G6's own process and written to the child as JSON, so their size is bounded before any row is encoded
(:data:`MAX_VALUES`, :func:`bounded`).
"""
from __future__ import annotations

import random
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any

from g6.ml._fit import Encoded
from g6.ml._types import INPUT_OVERFLOW, TOO_LARGE, MLError, Prepared, cell_kind

MAX_CATEGORIES = 50
#: The most encoded values one request builds (rows to train on, held out and to predict, times the encoded columns):
#: a text column encodes to as many as 51, so 20,000 rows of 200 such columns would be about 200 million floats in G6's
#: own process and in the job, outside the child's memory cap and its deadline (G6's own bound: it can only refuse).
MAX_VALUES = 20_000_000
#: The largest magnitude a 32-bit float holds. The network computes in them, so a number beyond it, or one that becomes
#: so when standardised, would be infinite inside it: such a row is refused before the child starts.
FLOAT32_MAX = 3.4028234663852886e38
MISSING, OTHER = "(missing)", "(infrequent)"
#: The share of the training rows set aside to stop training early, when patience is on.
VALID_SHARE = 0.2


@dataclass
class Encoding:
    features: list[str]
    numeric: dict[str, dict[str, Any]] = field(default_factory=dict)
    categorical: dict[str, list[str]] = field(default_factory=dict)
    classes: list[str] = field(default_factory=list)
    target: dict[str, float] = field(default_factory=dict)

    def width(self) -> int:
        return sum(1 + int(s["indicator"]) for s in self.numeric.values()) + sum(
            len(c) for c in self.categorical.values())

    def kinds(self) -> dict[str, str]:
        """Each feature's kind as the training rows set it, for the family's rules (``g6.ml._fit.encode_with``)."""
        return {f: "numeric" if f in self.numeric else "categorical" for f in self.features}

    def row(self, values: list[Any], where: str = "a row") -> list[float]:
        """One row as the network takes it, from the family's encoding of it (``g6.ml._fit``, which holds every row to
        the kinds the training rows set: a number as a float, text as text, a missing value as ``None``); a number
        beyond :data:`FLOAT32_MAX`, given or standardised, is refused ``G6_E_ML_INPUT_OVERFLOW``."""
        out: list[float] = []
        for f, value in zip(self.features, values, strict=True):
            if f in self.numeric:
                s = self.numeric[f]
                given = s["median"] if value is None else value
                z = (given - s["mean"]) / s["sd"]
                if abs(given) > FLOAT32_MAX or not abs(z) <= FLOAT32_MAX:
                    raise MLError(INPUT_OVERFLOW, f"{where}: feature {f!r} is {given:g}"
                                                  + ("" if abs(given) > FLOAT32_MAX else
                                                     f", {z:g} once standardised by the fitting rows (mean "
                                                     f"{s['mean']:g}, standard deviation {s['sd']:g})")
                                                  + ", beyond the range of the 32-bit floats the network computes in "
                                                    f"(about {FLOAT32_MAX:.2g}); nothing was trained or predicted")
                out.append(z)
                if s["indicator"]:
                    out.append(1.0 if value is None else 0.0)
            else:
                cats = self.categorical[f]
                text = MISSING if value is None else value
                hot = text if text in cats else (OTHER if OTHER in cats else None)
                out.extend(1.0 if c == hot else 0.0 for c in cats)
        return out

    def imputed(self, rows: list[list[Any]]) -> dict[str, Any]:
        """What :meth:`row` fills in for ``rows`` (encoded as for it): how many values are missing, and how many are
        text never among the categories kept from the fitting rows."""
        unseen = sum(1 for values in rows for f, v in zip(self.features, values, strict=True)
                     if v is not None and f in self.categorical and v not in self.categorical[f])
        return {"rows": len(rows), "missing_imputed": sum(v is None for values in rows for v in values),
                "unseen_categories": unseen,
                "how": "a missing number takes the fitting rows' median (and sets its missing-value column, when the "
                       "fitting rows had one); a missing text value is its own value, a column only when the fitting "
                       f"rows had one; text never among a column's {MAX_CATEGORIES} most frequent in the fitting rows "
                       "sets the infrequent column when there is one, and no column otherwise"}

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Encoding:
        return cls(**data)


def fit(prepared: Prepared, rows: list[int]) -> Encoding:
    """The encoding of ``prepared``'s features, from the rows at ``rows`` only."""
    spec = prepared.spec
    enc = Encoding(features=list(spec.features))
    for f in spec.features:
        values = [spec.rows[i].get(f) for i in rows]
        if prepared.kinds[f] == "numeric":
            present = [float(v) for v in values if cell_kind(v) != "missing"]
            median = statistics.median(present) if present else 0.0
            filled = [median if cell_kind(v) == "missing" else float(v) for v in values]
            sd = statistics.pstdev(filled) if len(filled) > 1 else 0.0
            enc.numeric[f] = {"median": median, "mean": statistics.fmean(filled), "sd": sd or 1.0,
                              "indicator": len(present) < len(values)}
        else:
            counts: dict[str, int] = {}
            for v in values:
                key = MISSING if cell_kind(v) == "missing" else str(v)
                counts[key] = counts.get(key, 0) + 1
            ranked = sorted(counts, key=lambda k: (-counts[k], k))
            enc.categorical[f] = ranked[:MAX_CATEGORIES] + ([OTHER] if len(ranked) > MAX_CATEGORIES else [])
    return enc


def bounded(enc: Encoding, rows: int) -> None:
    """Refuse with ``G6_E_ML_TOO_LARGE`` when ``rows`` rows would encode to more than :data:`MAX_VALUES` values."""
    values = rows * enc.width()
    if values > MAX_VALUES:
        raise MLError(TOO_LARGE, f"{rows:,} rows of {enc.width():,} encoded columns are {values:,} values; at most "
                                 f"{MAX_VALUES:,} are encoded (each text value among a column's {MAX_CATEGORIES} most "
                                 "frequent is a column of its own): send fewer rows, or fewer or plainer text features")


def validation(prepared: Prepared, encoded: Encoded, seed: int) -> list[int]:
    """Positions within the training rows set aside to stop early: about :data:`VALID_SHARE` of them, whole groups of
    identical rows, within each class for classification, drawn with ``seed`` (these are training rows: the held-out
    rows are never among them)."""
    train = prepared.split.train
    strata: dict[str, dict[int, list[int]]] = {}
    for pos, i in enumerate(train):
        stratum = str(encoded.y[i]) if prepared.spec.task == "classification" else "all"
        strata.setdefault(stratum, {}).setdefault(encoded.groups[i], []).append(pos)
    rng = random.Random(seed)
    chosen: list[int] = []
    for stratum in sorted(strata):
        groups = [strata[stratum][g] for g in sorted(strata[stratum])]
        if len(groups) < 2:
            continue
        rng.shuffle(groups)
        want = max(1, round(sum(len(g) for g in groups) * VALID_SHARE))
        taken = 0
        for members in groups[:-1]:
            if taken >= want:
                break
            chosen.extend(members)
            taken += len(members)
    return sorted(chosen)


def targets(prepared: Prepared, encoded: Encoded, enc: Encoding, fit_rows: list[int]) -> list[Any]:
    """The training rows' targets as the network takes them, setting ``enc``'s classes or target scale."""
    train = prepared.split.train
    if prepared.spec.task == "classification":
        enc.classes = sorted({str(encoded.y[i]) for i in train})
        index = {c: k for k, c in enumerate(enc.classes)}
        return [index[str(encoded.y[i])] for i in train]
    ys = [float(encoded.y[i]) for i in fit_rows]
    sd = statistics.pstdev(ys) if len(ys) > 1 else 0.0
    enc.target = {"mean": statistics.fmean(ys), "sd": sd or 1.0}
    return [(float(encoded.y[i]) - enc.target["mean"]) / enc.target["sd"] for i in train]


def decode(enc: Encoding, task: str, values: list[Any]) -> list[Any]:
    """The child's outputs as the family's targets: class label keys, or numbers in the target's units."""
    if task == "classification":
        return [enc.classes[int(v)] for v in values]
    return [float(v) * enc.target["sd"] + enc.target["mean"] for v in values]


__all__ = ["FLOAT32_MAX", "MAX_CATEGORIES", "MAX_VALUES", "VALID_SHARE", "Encoding", "bounded", "decode", "fit",
           "targets", "validation"]
