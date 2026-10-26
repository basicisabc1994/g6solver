# SPDX-License-Identifier: Apache-2.0
"""The rows a chart is drawn from, read column by column, with every row that cannot be plotted counted and named.

Nothing is guessed: the caller names each column, and a column no row holds is refused. A value is a number when it is
an integer or a float that is finite, or text that is exactly a decimal number (counted as ``parsed_from_text``); a
boolean is never a number. A category is text, or a number written as :func:`shown` writes it; an empty one is
missing. A row whose value for a column is missing, not a number or not finite is dropped, and :class:`Coverage` says
which row, which column and why, so a chart never hides data silently.

Also here, each computed in the open: histogram bins (equal width, each ``[a, b)`` and the last ``[a, b]``) and box
statistics (quartiles by linear interpolation between order statistics, Hyndman and Fan's type 7, NumPy's default;
whiskers at the furthest values within 1.5 times the interquartile range, Tukey's rule; the rest are outliers).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

Value = str | bool | int | float | None
MAX_ROWS = 10_000
_DECIMAL = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SHOWN_DROPS = 20


def shown(value: float) -> str:
    """A number as a chart writes it: a whole number without a point, otherwise Python's shortest form, or six
    significant figures when that is longer than ten characters."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = repr(value)
    return text if len(text) <= 10 else f"{value:.6g}"


def same_number(text: str, value: float) -> bool:
    """Whether the label ``text`` shows ``value`` (to the six significant figures :func:`shown` may round to)."""
    try:
        found = float(text)
    except ValueError:
        return False
    return abs(found - value) <= 1e-12 + 5e-6 * abs(value)


def number(value: Value) -> tuple[float | None, str, bool]:
    """``(the number, why not, read from text)``: the number, or ``None`` with ``missing``, ``not a number`` or ``not
    finite``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing", False
    if isinstance(value, bool):
        return None, "not a number", False
    if isinstance(value, (int, float)):
        found = float(value)
        return (found, "", False) if math.isfinite(found) else (None, "not finite", False)
    if isinstance(value, str) and _DECIMAL.fullmatch(value.strip()):
        found = float(value)
        return (found, "", True) if math.isfinite(found) else (None, "not finite", True)
    return None, "not a number", False


def category(value: Value) -> tuple[str | None, str]:
    """``(the category as drawn, why not)``: text as given, a number as :func:`shown` writes it."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "missing"
    if isinstance(value, bool):
        return ("true" if value else "false"), ""
    if isinstance(value, (int, float)):
        return (shown(float(value)), "") if math.isfinite(float(value)) else (None, "not finite")
    return value, ""


@dataclass
class Coverage:
    """What happened to each row: how many were given and plotted, and each dropped row with its column and reason."""

    rows_given: int
    columns: dict[str, str]
    rows_plotted: int = 0
    dropped_count: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    parsed_from_text: int = 0
    notes: list[str] = field(default_factory=list)

    def drop(self, row: int, column: str, why: str) -> None:
        self.dropped_count += 1
        if len(self.dropped) < _SHOWN_DROPS:
            self.dropped.append({"row": row, "column": column, "reason": why})

    def report(self) -> dict[str, Any]:
        return {"rows_given": self.rows_given, "rows_plotted": self.rows_plotted, "dropped_count": self.dropped_count,
                "dropped": list(self.dropped), "parsed_from_text": self.parsed_from_text,
                "columns": dict(self.columns), "notes": list(self.notes)}

    def said(self) -> str:
        if not self.dropped_count:
            return f"all {self.rows_given} rows plotted"
        reasons = sorted({d["reason"] for d in self.dropped})
        return (f"{self.rows_plotted} of {self.rows_given} rows plotted; {self.dropped_count} dropped "
                f"({', '.join(reasons)})")


class ChartDataError(ValueError):
    """The rows cannot make the chart asked for (a column no row holds, a category given twice, too many bars)."""


def columns_present(rows: list[dict[str, Value]], wanted: dict[str, str]) -> None:
    held = set().union(*(r.keys() for r in rows)) if rows else set()
    for role, column in wanted.items():
        if not column:
            raise ChartDataError(f"name the {role} column ({role}=...): G6 does not guess which column to plot")
        if column not in held:
            listed = ", ".join(sorted(held)[:12]) or "none"
            raise ChartDataError(f"no row has a column named {column!r} (the {role} column); columns: {listed}")


def take(rows: list[dict[str, Value]], wanted: dict[str, tuple[str, str]]) -> tuple[list[dict[str, Any]], Coverage]:
    """The plottable records: for each row, each role's value read as ``number``, ``category`` or ``auto`` (numbers
    when every present value of that column is one, categories otherwise), and the coverage of the rows."""
    columns_present(rows, {role: column for role, (column, _) in wanted.items()})
    kinds = {}
    for role, (column, kind) in wanted.items():
        if kind == "auto":
            present = [r.get(column) for r in rows if number(r.get(column))[1] != "missing"]
            kind = "number" if present and all(number(v)[0] is not None for v in present) else "category"
        kinds[role] = kind
    coverage = Coverage(rows_given=len(rows), columns={role: column for role, (column, _) in wanted.items()})
    records = []
    for index, row in enumerate(rows):
        record: dict[str, Any] = {"row": index}
        for role, (column, _) in wanted.items():
            raw = row.get(column)
            if kinds[role] == "number":
                found, why, from_text = number(raw)
                coverage.parsed_from_text += int(from_text and found is not None)
            else:
                found, why = category(raw)
            if found is None:
                coverage.drop(index, column, why)
                break
            record[role] = found
        else:
            records.append(record)
    coverage.rows_plotted = len(records)
    coverage.columns |= {f"{role}_kind": kinds[role] for role in wanted}
    return records, coverage


def bins(values: list[float], count: int) -> tuple[list[float], list[int]]:
    """Equal-width bins over the values' range: ``count + 1`` edges and ``count`` counts; a single value gets a bin
    one unit wide around it."""
    lo, hi = min(values), max(values)
    if lo == hi:
        lo, hi = lo - 0.5, hi + 0.5
    width = (hi - lo) / count
    edges = [lo + i * width for i in range(count)] + [hi]
    counts = [0] * count
    for v in values:
        counts[min(int((v - lo) / width), count - 1)] += 1
    return edges, counts


def sturges(n: int) -> int:
    """Sturges' rule for the number of bins, from 1 to 50."""
    return max(1, min(50, math.ceil(math.log2(n)) + 1 if n > 1 else 1))


def box_stats(values: list[float]) -> dict[str, Any]:
    """Quartiles (type 7), Tukey whiskers and outliers of one group's values."""
    s = sorted(values)
    n = len(s)

    def q(p: float) -> float:
        h = (n - 1) * p
        lo = math.floor(h)
        return s[lo] + (h - lo) * (s[min(lo + 1, n - 1)] - s[lo])

    q1, median, q3 = q(0.25), q(0.5), q(0.75)
    iqr = q3 - q1
    inside = [v for v in s if q1 - 1.5 * iqr <= v <= q3 + 1.5 * iqr]
    return {"n": n, "q1": q1, "median": median, "q3": q3, "low": inside[0], "high": inside[-1],
            "outliers": [v for v in s if v < inside[0] or v > inside[-1]]}


__all__ = ["MAX_ROWS", "ChartDataError", "Coverage", "Value", "bins", "box_stats", "category", "columns_present",
           "number", "same_number", "shown", "sturges", "take"]
