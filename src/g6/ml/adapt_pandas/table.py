# SPDX-License-Identifier: Apache-2.0
"""A table the caller gives, read by one stated rule, and what can be said about it from its values alone.

:func:`read` takes CSV text, JSON text (an array of objects) or rows, within :data:`MAX_TEXT_CHARS`, ``MAX_ROWS`` and
:data:`MAX_COLUMNS`, and returns a :class:`Table`: its columns in order, its rows with each value read by
:data:`READING_RULE`, how many lines went in and rows came out, and for each column what the rule found
(:class:`Reading`). Nothing is converted silently: a value the rule cannot read as a number stays text, so a column of
numbers with a stray ``n/a`` is ``mixed`` and says so, and only the tokens the caller names count as missing.

:func:`profile` describes a :class:`Table`: per column its kind, missing count, distinct values, number summary or
most common texts and notes; for the table, the rows that repeat an earlier row, the key values that repeat (for the
key columns the caller names), constant and empty columns, and a warning for each finding that commonly turns into a
data mistake.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from g6.ml._types import INPUT_INVALID, MAX_ROWS, TOO_LARGE, Cell, MLError, cell_kind, column_kind

MAX_TEXT_CHARS = 5_000_000
MAX_COLUMNS = 200
MAX_FIELD_CHARS = 10_000
#: What a CSV field is read as, in order. The rule a profile reports.
READING_RULE = ("a CSV field is missing when, without its surrounding spaces, it is one of the missing tokens given "
                "(by default only the empty field); a column is numbers when every other field is a plain decimal "
                "number (optional sign, digits, optional fraction and exponent, no thousands separator) and none "
                "starts with a zero followed by a digit; a column whose numbers include one like 007 is text, kept "
                "as written; a column of true and false (any case) is booleans; any other column keeps each field "
                "as written, its numbers read as numbers, and is mixed when it holds both. JSON values and rows "
                "keep their own types.")
_NUMBER = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")
_ZERO_LED = re.compile(r"[+-]?0\d")
_THOUSANDS = re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?")
_DECIMAL_COMMA = re.compile(r"[+-]?\d+,\d+")
#: Texts that often stand for "no value"; a profile names each one found, and never reads it as missing unasked.
LOOKS_MISSING = frozenset({"NA", "N/A", "n/a", "na", "null", "NULL", "Null", "None", "none", "NaN", "nan", "-", "--",
                           "?", "#N/A", "missing", "MISSING", "unknown", "UNKNOWN"})


@dataclass
class Reading:
    """What the rule found in one column."""

    kind: str = "empty"
    zero_led: int = 0
    trimmed: int = 0
    thousands: int = 0
    decimal_comma: int = 0
    numbers_as_text: int = 0
    looks_missing: Counter = field(default_factory=Counter)


@dataclass
class Table:
    """A table as read: its columns in order, its rows, where it came from and the line accounting."""

    columns: list[str]
    rows: list[dict[str, Cell]]
    source: str
    accounting: dict[str, int]
    readings: dict[str, Reading]
    missing_tokens: list[str]


def _number(text: str) -> int | float | None:
    try:
        value: int | float = int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)
    except ValueError:  # an integer of more digits than Python converts
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _check_names(columns: list[str]) -> None:
    if not columns:
        raise MLError(INPUT_INVALID, "the table has no columns")
    if len(columns) > MAX_COLUMNS:
        raise MLError(TOO_LARGE, f"the table has {len(columns)} columns; at most {MAX_COLUMNS} are read")
    blank = [i for i, c in enumerate(columns) if not c.strip()]
    if blank:
        raise MLError(INPUT_INVALID, f"column {blank[0] + 1} has no name; name every column")
    twice = sorted(c for c, n in Counter(columns).items() if n > 1)
    if twice:
        raise MLError(INPUT_INVALID, f"the column name {twice[0]!r} is used twice; a column is never guessed between "
                                     "two of the same name")
    long = [c for c in columns if len(c) > 100]
    if long:
        raise MLError(INPUT_INVALID, f"a column name is longer than 100 characters: {long[0][:40]!r}...")


def _read_csv_column(raw: list[str], missing: set[str]) -> tuple[list[Cell], Reading]:
    reading = Reading()
    classes: list[tuple[str, Any]] = []
    for value in raw:
        text = value.strip()
        if text != value:
            reading.trimmed += 1
        if text in missing:
            classes.append(("missing", None))
            continue
        if text in LOOKS_MISSING:
            reading.looks_missing[text] += 1
        if _NUMBER.fullmatch(text) and (number := _number(text)) is not None:
            if _ZERO_LED.match(text):
                reading.zero_led += 1
                classes.append(("zero_led", value))
            else:
                classes.append(("number", number))
            continue
        if _THOUSANDS.fullmatch(text):
            reading.thousands += 1
        elif _DECIMAL_COMMA.fullmatch(text):
            reading.decimal_comma += 1
        if text.lower() in ("true", "false"):
            classes.append(("boolean", text.lower() == "true"))
        else:
            classes.append(("text", value))
    seen = {c for c, _ in classes} - {"missing"}
    if not seen:
        reading.kind = "empty"
        return [None] * len(raw), reading
    if seen == {"number"}:
        reading.kind = "number"
        return [v for _, v in classes], reading
    if seen <= {"number", "zero_led"}:
        reading.kind = "text"  # codes: every number kept as written, leading zeros and all
        return [None if c == "missing" else r for (c, _), r in zip(classes, raw)], reading
    if seen == {"boolean"}:
        reading.kind = "boolean"
        return [v for _, v in classes], reading
    values = [v for _, v in classes]  # a number like 007 stays as written here too
    reading.kind = column_kind(values)
    return values, reading


def read_csv(text: str, delimiter: str = ",", missing: list[str] | None = None) -> Table:
    """CSV text with a header line, read by :data:`READING_RULE`. A line with more or fewer fields than the header,
    a column with no name, a name used twice, or broken quoting is refused, never repaired."""
    tokens = [""] if missing is None else list(missing)
    if len(text) > MAX_TEXT_CHARS:
        raise MLError(TOO_LARGE, f"the CSV text is {len(text):,} characters; at most {MAX_TEXT_CHARS:,} are read")
    reader = csv.reader(io.StringIO(text.lstrip("\ufeff"), newline=""), delimiter=delimiter, strict=True)
    header: list[str] | None = None
    data: list[list[str]] = []
    ragged: list[tuple[int, int]] = []
    blank = 0
    try:
        for fields in reader:
            if not fields:
                blank += 1
                continue
            if header is None:
                header = [f.strip() for f in fields]
                _check_names(header)
                continue
            if len(data) >= MAX_ROWS:
                raise MLError(TOO_LARGE, f"the CSV has more than {MAX_ROWS:,} data rows")
            if len(fields) != len(header):
                ragged.append((reader.line_num, len(fields)))
            if any(len(f) > MAX_FIELD_CHARS for f in fields):
                raise MLError(TOO_LARGE, f"line {reader.line_num} has a field longer than {MAX_FIELD_CHARS:,} "
                                         "characters")
            data.append(fields)
    except csv.Error as exc:
        raise MLError(INPUT_INVALID, f"the CSV cannot be read at line {reader.line_num}: {exc}") from None
    if header is None:
        raise MLError(INPUT_INVALID, "the CSV text has no header line")
    if ragged:
        shown = ", ".join(f"line {n} has {k}" for n, k in ragged[:5])
        raise MLError(INPUT_INVALID, f"{len(ragged)} line(s) do not have the header's {len(header)} fields ({shown}"
                                     f"{', ...' if len(ragged) > 5 else ''}); nothing was read, since a field in the "
                                     "wrong column is a wrong value")
    missing_set = set(tokens)
    columns: dict[str, list[Cell]] = {}
    readings: dict[str, Reading] = {}
    for j, name in enumerate(header):
        columns[name], readings[name] = _read_csv_column([row[j] for row in data], missing_set)
    rows = [{name: columns[name][i] for name in header} for i in range(len(data))]
    accounting = {"lines_read": reader.line_num, "blank_lines_skipped": blank, "header_lines": 1,
                  "rows_out": len(rows)}
    return Table(header, rows, "csv", accounting, readings, tokens)


def _typed_reading(values: list[Cell]) -> Reading:
    reading = Reading(kind=column_kind(values))
    for v in values:
        if isinstance(v, str):
            text = v.strip()
            if text != v:
                reading.trimmed += 1
            if text in LOOKS_MISSING:
                reading.looks_missing[text] += 1
            if _NUMBER.fullmatch(text):
                reading.numbers_as_text += 1
    return reading


def read_rows(rows: list[dict[str, Any]], source: str = "rows") -> Table:
    """Rows of values (or a parsed JSON array of objects), their types kept. A row missing a column has no value
    there; a nested list or object is refused."""
    if len(rows) > MAX_ROWS:
        raise MLError(TOO_LARGE, f"{len(rows):,} rows were given; at most {MAX_ROWS:,} are read")
    columns: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise MLError(INPUT_INVALID, f"row {i} is not an object of column names and values")
        for name, value in row.items():
            if name not in seen:
                seen.add(name)
                columns.append(name)
            if value is not None and not isinstance(value, (bool, int, float, str)):
                raise MLError(INPUT_INVALID, f"row {i}, column {name!r} holds a {type(value).__name__}; a table cell "
                                             "is a number, text, true or false, or nothing")
            if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
                raise MLError(TOO_LARGE, f"row {i}, column {name!r} is longer than {MAX_FIELD_CHARS:,} characters")
    _check_names(columns)
    table = [{c: row.get(c) for c in columns} for row in rows]
    readings = {c: _typed_reading([r[c] for r in table]) for c in columns}
    accounting = {"rows_in": len(rows), "rows_out": len(table)}
    return Table(columns, table, source, accounting, readings, [])


def _once(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object whose keys are each given once (the parser would keep the last value of a repeated key)."""
    found: dict[str, Any] = {}
    for key, value in pairs:
        if key in found:
            raise MLError(INPUT_INVALID, f"the JSON text gives the key {key[:60]!r} twice in one object; a value is "
                                         "never chosen between two of the same name")
        found[key] = value
    return found


def read_json(text: str) -> Table:
    """JSON text holding an array of objects, read with :func:`read_rows`. An object that gives a key twice is
    refused."""
    if len(text) > MAX_TEXT_CHARS:
        raise MLError(TOO_LARGE, f"the JSON text is {len(text):,} characters; at most {MAX_TEXT_CHARS:,} are read")
    try:
        data = json.loads(text, object_pairs_hook=_once)
    except MLError:
        raise
    except (ValueError, RecursionError) as exc:
        raise MLError(INPUT_INVALID, f"the JSON text cannot be read: {exc}"[:300]) from None
    if not isinstance(data, list):
        raise MLError(INPUT_INVALID, "the JSON text must be an array of objects, one per row")
    return read_rows(data, "json")


# --- the profile ----------------------------------------------------------------------------------------------------

def _summary(numbers: list[float]) -> dict[str, Any]:
    out: dict[str, Any] = {"count": len(numbers), "min": min(numbers), "max": max(numbers),
                           "mean": math.fsum(numbers) / len(numbers), "median": statistics.median(numbers),
                           "zeros": sum(1 for x in numbers if x == 0), "negatives": sum(1 for x in numbers if x < 0)}
    out["std"] = statistics.stdev(numbers) if len(numbers) > 1 else None
    return out


def _notes(name: str, reading: Reading, kind: str, values: list[Cell], present: int) -> list[str]:
    notes = []
    if kind == "mixed":
        numbers = sum(1 for v in values if cell_kind(v) == "number")
        texts = [v for v in values if cell_kind(v) == "text"]
        shown = ", ".join(repr(t) for t in list(dict.fromkeys(texts))[:5])
        notes.append(f"{name}: {len(texts)} text value(s) among {numbers} number(s) ({shown}); a model or a sum over "
                     "this column would mistake them")
    if reading.looks_missing:
        shown = ", ".join(f"{t!r} x{n}" for t, n in reading.looks_missing.most_common(5))
        notes.append(f"{name}: value(s) that often stand for no value were read as text ({shown}); pass them in "
                     "missing if they mean no value")
    if reading.zero_led:
        notes.append(f"{name}: {reading.zero_led} number(s) start with a zero (such as 007), so the column is text, "
                     "kept as written: codes, not quantities")
    if reading.thousands:
        notes.append(f"{name}: {reading.thousands} value(s) look like numbers with a thousands separator "
                     "(1,234) and were read as text")
    if reading.decimal_comma:
        notes.append(f"{name}: {reading.decimal_comma} value(s) look like numbers with a decimal comma (12,5) and "
                     "were read as text")
    if reading.numbers_as_text:
        notes.append(f"{name}: {reading.numbers_as_text} number(s) were given as text (such as \"12\")")
    if reading.trimmed:
        notes.append(f"{name}: {reading.trimmed} value(s) have spaces around them")
    if present == 0:
        notes.append(f"{name}: no row has a value")
    elif present < len(values):
        share = (len(values) - present) / len(values)
        if share >= 0.2:
            notes.append(f"{name}: {share:.0%} of rows have no value")
    return notes


def _column(name: str, table: Table) -> dict[str, Any]:
    values = [r[name] for r in table.rows]
    reading = table.readings[name]
    kind = column_kind(values)
    present = sum(1 for v in values if cell_kind(v) != "missing")
    distinct = len({json.dumps(v, sort_keys=True) for v in values if cell_kind(v) != "missing"})
    out: dict[str, Any] = {"name": name, "kind": kind, "present": present, "missing": len(values) - present,
                           "missing_share": round((len(values) - present) / len(values), 6) if values else 0.0,
                           "distinct": distinct}
    numbers = [float(v) for v in values if cell_kind(v) == "number"]
    if numbers:
        out["numbers"] = _summary(numbers)
    texts = [str(v) for v in values if cell_kind(v) == "text"]
    if texts:
        top = Counter(texts).most_common(5)
        out["texts"] = {"count": len(texts), "top": [{"value": v[:100], "count": n} for v, n in top],
                        "shortest": min(len(t) for t in texts), "longest": max(len(t) for t in texts)}
    booleans = [bool(v) for v in values if cell_kind(v) == "boolean"]
    if booleans:
        out["booleans"] = {"true": sum(booleans), "false": len(booleans) - sum(booleans)}
    if present and distinct == 1 and present == len(values):
        out["constant"] = True
    out["notes"] = _notes(name, reading, kind, values, present)
    return out


def profile(table: Table, key: list[str] | None = None) -> dict[str, Any]:
    """Each column described, the rows that repeat, the key values that repeat, and a warning per finding."""
    key = list(key or [])
    unknown = [k for k in key if k not in table.columns]
    if unknown:
        raise MLError(INPUT_INVALID, f"the key column {unknown[0]!r} is not in the table "
                                     f"(columns: {', '.join(table.columns[:20])})")
    columns = [_column(name, table) for name in table.columns]
    warnings = [n for c in columns for n in c["notes"] if "has spaces" not in n]
    first_seen: dict[str, int] = {}
    repeats: list[int] = []
    for i, row in enumerate(table.rows):
        body = json.dumps([row[c] for c in table.columns])
        if body in first_seen:
            repeats.append(i)
        else:
            first_seen[body] = i
    if repeats:
        warnings.append(f"{len(repeats)} row(s) repeat an earlier row exactly (first: row {repeats[0]})")
    keyed: dict[str, Any] = {}
    if key:
        counts: Counter = Counter()
        missing_key = 0
        for row in table.rows:
            parts = [row[k] for k in key]
            if any(cell_kind(p) == "missing" for p in parts):
                missing_key += 1
                continue
            counts[json.dumps(parts)] += 1
        twice = [(json.loads(k), n) for k, n in counts.most_common() if n > 1]
        keyed = {"columns": key, "rows_with_missing_key": missing_key, "repeated_values": len(twice),
                 "rows_sharing_a_key": sum(n for _, n in twice),
                 "examples": [{"key": k, "rows": n} for k, n in twice[:5]]}
        if twice:
            warnings.append(f"the key ({', '.join(key)}) repeats: {len(twice)} value(s) appear on more than one row "
                            f"(such as {twice[0][0]} on {twice[0][1]} rows); a join on it would multiply rows")
        if missing_key:
            warnings.append(f"{missing_key} row(s) have no value in a key column")
    constant = [c["name"] for c in columns if c.get("constant")]
    if constant and len(table.rows) > 1:
        warnings.append(f"{len(constant)} column(s) hold one value on every row ({', '.join(constant[:5])}): they "
                        "carry no information")
    return {"rows": len(table.rows), "columns": columns, "column_count": len(table.columns),
            "duplicate_rows": {"count": len(repeats), "first": repeats[:5]}, "key": keyed or None,
            "constant_columns": constant, "warnings": warnings}


__all__ = ["LOOKS_MISSING", "MAX_COLUMNS", "MAX_FIELD_CHARS", "MAX_TEXT_CHARS", "READING_RULE", "Reading", "Table",
           "profile", "read_csv", "read_json", "read_rows"]
