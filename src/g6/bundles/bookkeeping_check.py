# SPDX-License-Identifier: Apache-2.0
"""Bundle: Bookkeeping and GST check (Australia) — does the ledger match the bank, and does the GST add up?

Works offline, with no AI model. Paste the bank statement and the ledger (CSV), say how dates are written, and give the
period; optionally paste the figures reported on the BAS. G6 then:

- matches bank and ledger lines one to one, on the exact amount and a date within a few days; a pair that agrees only
  on the amount is shown as a suggestion, never counted as a match (audit 034 ACC-004);
- lists unmatched lines, duplicates and lines outside the period;
- checks each ledger line's GST: a line coded ``GST`` must carry 1/11 of its GST-inclusive amount (to the cent), a line
  coded ``FRE`` (GST-free) or ``INP`` (input taxed) must carry none, and an uncoded line is "not assessed";
- recomputes BAS G1 (total sales), 1A (GST on sales) and 1B (GST on purchases) and compares any reported figures.

The rule it applies: GST is 10% of the value of a taxable supply (A New Tax System (Goods and Services Tax) Act 1999,
s 9-70), so a GST-inclusive price carries 1/11 of itself as GST (s 9-75). Periods before GST began (1 July 2000) are
refused. A date or an amount it cannot read is reported, never read as zero (ACC-006). Nothing here is tax advice, and
it does not know what a supply's correct GST treatment is: it checks the coding you gave against the arithmetic.
"""
from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from g6.bundles import Bundle, BundleResult, Field, weakest

GST_START = date(2000, 7, 1)
CENT = Decimal("0.01")
_FORMATS = {"DD/MM/YYYY": "%d/%m/%Y", "YYYY-MM-DD": "%Y-%m-%d", "MM/DD/YYYY": "%m/%d/%Y"}
_CODES = {"GST", "FRE", "INP", "N-T"}
_BAS = re.compile(r"\b(G1|1A|1B)\s*[:=]?\s*\$?\s*(-?[\d,]+(?:\.\d+)?)", re.IGNORECASE)
_BENFORD = {d: math.log10(1 + 1 / d) for d in range(1, 10)}


def _money(text: str) -> Decimal | None:
    cleaned = text.replace(",", "").replace("$", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value.quantize(CENT, ROUND_HALF_UP) if value.is_finite() else None


def _lines(text: str, fmt: str, kind: str) -> tuple[list[dict], list[dict]]:
    """Rows with parsed ``date`` / ``amount`` (and ``gst_code`` / ``gst`` for the ledger), and problem rows."""
    rows, problems = [], []
    reader = csv.DictReader(io.StringIO(text.strip()))
    fields = {(f or "").strip().lower(): f for f in reader.fieldnames or []}
    for need in ("date", "amount"):
        if need not in fields:
            return [], [{"check": f"{kind} columns", "result": "problem", "detail": f"no '{need}' column",
                         "what to do": "Give a header row with date, description and amount.",
                         "label": "blocked-escalated"}]
    for n, raw in enumerate(reader, start=2):
        get = {k: (raw.get(v) or "").strip() for k, v in fields.items()}
        try:
            when = datetime.strptime(get["date"], _FORMATS[fmt]).date()
        except ValueError:
            when = None
        amount = _money(get["amount"])
        if when is None or amount is None:
            problems.append({"check": f"{kind} line {n}", "result": "problem",
                             "detail": f"cannot read {'the date' if when is None else 'the amount'}: "
                                       f"{get['date'] if when is None else get['amount']!r}",
                             "what to do": "Fix the line (dates as " + fmt + "); it was left out, not read as 0.",
                             "label": "blocked-escalated"})
            continue
        row = {"line": n, "date": when, "amount": amount, "description": get.get("description", "")}
        if kind == "ledger":
            row["gst_code"] = get.get("gst_code", "").upper()
            row["gst"] = _money(get["gst"]) if get.get("gst") else None
        rows.append(row)
    return rows, problems


def _match(bank: list[dict], ledger: list[dict], window: int) -> tuple[list, list, list, list]:
    """One-to-one matches on amount and a date within ``window`` days; amount-only pairs come back as suggestions."""
    free = list(ledger)
    matched, unmatched_bank = [], []
    for b in sorted(bank, key=lambda r: (r["date"], r["line"])):
        candidates = [r for r in free if r["amount"] == b["amount"] and abs((r["date"] - b["date"]).days) <= window]
        if candidates:
            best = min(candidates, key=lambda r: (abs((r["date"] - b["date"]).days), r["line"]))
            free.remove(best)
            matched.append((b, best))
        else:
            unmatched_bank.append(b)
    suggestions = [(b, r) for b in unmatched_bank for r in free if r["amount"] == b["amount"]]
    return matched, unmatched_bank, free, suggestions


def _period(text: str) -> tuple[date, date] | None:
    found = re.findall(r"\d{4}-\d{2}-\d{2}", text or "")
    if len(found) != 2:
        return None
    start, end = (date.fromisoformat(d) for d in found)
    return (start, end) if start <= end else None


def _benford(amounts: list[Decimal]) -> dict | None:
    """A chi-squared first-digit test on counts (8 degrees of freedom, 5% critical value 15.507), for 200+ amounts."""
    digits = [int(str(abs(a)).lstrip("0.")[0]) for a in amounts if a != 0]
    if len(digits) < 200:
        return None
    n = len(digits)
    chi = sum((digits.count(d) - _BENFORD[d] * n) ** 2 / (_BENFORD[d] * n) for d in range(1, 10))
    return {"n": n, "chi_squared": round(chi, 2), "unusual": chi > 15.507}


def run(inputs: dict[str, str], llm: Any = None) -> BundleResult:
    fmt = inputs.get("date_format", "DD/MM/YYYY").strip().upper() or "DD/MM/YYYY"
    period = _period(inputs.get("period", ""))
    if fmt not in _FORMATS or period is None or not inputs.get("bank", "").strip() or not inputs.get("ledger", "").strip():
        return BundleResult("blocked-escalated", "Paste the bank and ledger CSVs, choose the date format "
                                                 f"({', '.join(_FORMATS)}) and give the period as YYYY-MM-DD to "
                                                 "YYYY-MM-DD.", code="G6_E_INPUT_VALIDATION")
    if period[0] < GST_START:
        return BundleResult("blocked-escalated", "GST began on 1 July 2000; G6 checks no period before it.",
                            code="G6_E_INPUT_VALIDATION")
    window = 3
    bank, rows = _lines(inputs["bank"], fmt, "bank")
    ledger, ledger_problems = _lines(inputs["ledger"], fmt, "ledger")
    rows += ledger_problems
    for kind, items in (("bank", bank), ("ledger", ledger)):
        rows += [{"check": f"{kind} line {r['line']}", "result": "problem", "detail": f"{r['date']} is outside the period",
                  "what to do": "Move it to its own period or correct the date.", "label": "blocked-escalated"}
                 for r in items if not period[0] <= r["date"] <= period[1]]
    seen: dict[tuple, int] = {}
    for r in ledger:
        key = (r["date"], r["amount"], r["description"].lower())
        if key in seen:
            rows.append({"check": f"ledger line {r['line']}", "result": "problem",
                         "detail": f"the same entry as line {seen[key]}", "what to do": "Check it is not entered twice.",
                         "label": "blocked-escalated"})
        seen.setdefault(key, r["line"])
    matched, lonely_bank, lonely_ledger, suggestions = _match(bank, ledger, window)
    rows += [{"check": f"bank line {b['line']}", "result": "problem",
              "detail": f"{b['date']} {b['amount']} {b['description']!r} has no ledger line within {window} days",
              "what to do": "Record it in the ledger, or find the entry it belongs to.", "label": "blocked-escalated"}
             for b in lonely_bank]
    rows += [{"check": f"ledger line {r['line']}", "result": "problem",
              "detail": f"{r['date']} {r['amount']} {r['description']!r} has no bank line within {window} days",
              "what to do": "Check it was paid or received, or that its date is right.", "label": "blocked-escalated"}
             for r in lonely_ledger]
    rows += [{"check": "possible match", "result": "suggestion",
              "detail": f"bank line {b['line']} and ledger line {r['line']} agree on {b['amount']} but are "
                        f"{abs((r['date'] - b['date']).days)} days apart", "what to do": "Not counted as a match; check it.",
              "label": "qualified-draft"} for b, r in suggestions]
    g1 = one_a = one_b = Decimal("0.00")
    not_assessed = 0
    for r in ledger:
        code, amount = r["gst_code"], r["amount"]
        if amount > 0:
            g1 += amount
        if code not in _CODES:
            not_assessed += 1
            continue
        expected = (abs(amount) / 11).quantize(CENT, ROUND_HALF_UP) if code == "GST" else Decimal("0.00")
        stated = r["gst"] if r["gst"] is not None else expected
        if abs(abs(stated) - expected) > CENT:
            rows.append({"check": f"ledger line {r['line']} GST", "result": "problem",
                         "detail": f"coded {code}, GST {stated} but 1/11 of {amount} is {expected}" if code == "GST"
                         else f"coded {code} (no GST) but GST {stated} is recorded",
                         "what to do": "Correct the GST amount or the code.", "label": "blocked-escalated"})
        if code == "GST":
            if amount > 0:
                one_a += expected
            else:
                one_b += expected
    if not_assessed:
        rows.append({"check": "GST coding", "result": "not assessed",
                     "detail": f"{not_assessed} ledger line(s) have no GST code ({', '.join(sorted(_CODES))})",
                     "what to do": "Code them; G6 does not guess a line's GST treatment.", "label": "blocked-escalated"})
    reported = {k.upper(): _money(v) for k, v in _BAS.findall(inputs.get("bas", ""))}
    for label, value in (("G1", g1), ("1A", one_a), ("1B", one_b)):
        detail = f"recomputed {value}"
        row = {"check": f"BAS {label}", "result": "computed", "detail": detail, "what to do": "—",
               "label": "qualified-draft"}
        if label in reported and reported[label] is not None:
            # the BAS is lodged in whole dollars, so a difference under one dollar is rounding
            close = abs(reported[label] - value) < Decimal("1.00")
            row.update(result="ok" if close else "conflict", detail=f"reported {reported[label]}; {detail}",
                       label="qualified-draft" if close else "blocked-escalated",
                       **({} if close else {"what to do": "Find the lines behind the difference before lodging."}))
        rows.append(row)
    benford = _benford([r["amount"] for r in ledger])
    if benford and benford["unusual"]:
        rows.append({"check": "first digits", "result": "read closely",
                     "detail": f"chi-squared {benford['chi_squared']} over {benford['n']} amounts (5% level 15.507)",
                     "what to do": "Unusual digit patterns are a reason to look, not evidence of a problem.",
                     "label": "qualified-draft"})
    problems = sum(r["label"] == "blocked-escalated" for r in rows)
    summary = (f"{len(matched)} of {len(bank)} bank line(s) matched; " +
               (f"{problems} problem(s) to resolve." if problems else "no problem found in what was checked."))
    return BundleResult(
        weakest([r["label"] for r in rows]) if rows else "qualified-draft", summary, rows,
        ["Matches need the exact amount and a date within 3 days; anything looser is only a suggestion.",
         "GST is checked against the codes you gave (GST, FRE, INP, N-T), using 1/11 of the GST-inclusive amount "
         "(GST Act 1999 ss 9-70, 9-75). Whether a supply is taxable is not something G6 decides.",
         "Not tax advice. Have a registered tax or BAS agent check anything you lodge."],
        code="G6_E_BOOKKEEPING_PROBLEM" if problems else "")


BUNDLE = Bundle(
    id="bookkeeping-check",
    title="Bookkeeping and GST check (Australia)",
    summary="Match your ledger to the bank, check each line's GST against its code, and recompute BAS G1, 1A and 1B.",
    fields=(
        Field("bank", "Bank statement (CSV: date, description, amount)",
              placeholder="date,description,amount\n03/07/2026,Client A,1100.00\n05/07/2026,Officeworks,-55.00"),
        Field("ledger", "Ledger (CSV: date, description, amount, gst_code, gst)",
              placeholder="date,description,amount,gst_code,gst\n03/07/2026,Client A,1100.00,GST,100.00"),
        Field("date_format", "How dates are written", kind="text", placeholder="DD/MM/YYYY",
              help="DD/MM/YYYY, YYYY-MM-DD or MM/DD/YYYY. G6 never guesses."),
        Field("period", "Period", kind="text", placeholder="2026-07-01 to 2026-09-30"),
        Field("bas", "Figures reported on the BAS (optional)", kind="text", placeholder="G1=1100, 1A=100, 1B=5"),
    ),
    run=run,
    disclaimer="Not tax advice. A registered tax or BAS agent should check anything you lodge.",
)
