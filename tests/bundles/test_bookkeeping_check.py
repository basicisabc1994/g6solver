# SPDX-License-Identifier: Apache-2.0
"""Bookkeeping and GST check (audits 034/035): matching, GST arithmetic, BAS figures, honest parsing. Offline."""
from __future__ import annotations

from g6.bundles import catalog
from g6.bundles.bookkeeping_check import run
from g6.bundles.server import run_bundle
from g6.invariants import check_envelope

PERIOD = "2026-07-01 to 2026-09-30"
BANK = "date,description,amount\n03/07/2026,Client A,1100.00\n05/07/2026,Officeworks,-55.00\n10/08/2026,Client B,330.00"
LEDGER = ("date,description,amount,gst_code,gst\n03/07/2026,Client A,1100.00,GST,100.00\n"
          "06/07/2026,Officeworks,-55.00,GST,5.00\n10/08/2026,Client B,330.00,FRE,0")


def _checks(result) -> dict[str, dict]:
    return {row["check"]: row for row in result.rows}


def _run(**kw):
    return run({"bank": BANK, "ledger": LEDGER, "date_format": "DD/MM/YYYY", "period": PERIOD, **kw})


def test_a_clean_quarter_is_a_qualified_draft_with_the_bas_recomputed() -> None:
    r = _run(bas="G1=1430, 1A=100, 1B=5")
    rows = _checks(r)
    assert r.completion_state == "qualified-draft" and "3 of 3 bank line(s) matched" in r.summary
    assert rows["BAS G1"]["result"] == "ok" and "recomputed 1430.00" in rows["BAS G1"]["detail"]
    assert "recomputed 100.00" in rows["BAS 1A"]["detail"] and "recomputed 5.00" in rows["BAS 1B"]["detail"]


def test_acc006_dates_follow_the_stated_format_and_unreadable_values_are_reported() -> None:
    bank = "date,description,amount\n03/04/2026,Client A,1100.00\n05/07/2026,Officeworks,abc"
    details = [r["detail"] for r in run({"bank": bank, "ledger": LEDGER, "date_format": "DD/MM/YYYY",
                                         "period": PERIOD}).rows]
    assert "2026-04-03 is outside the period" in details                      # 3 April, not 4 March
    assert any(d.startswith("cannot read the amount") for d in details)       # never read as 0


def test_acc004_an_amount_only_pair_is_a_suggestion_not_a_match() -> None:
    bank = "date,description,amount\n03/07/2026,Client A,1100.00\n20/07/2026,Officeworks,-55.00\n10/08/2026,Client B,330.00"
    r = run({"bank": bank, "ledger": LEDGER, "date_format": "DD/MM/YYYY", "period": PERIOD})
    rows = _checks(r)
    assert rows["possible match"]["result"] == "suggestion" and "14 days apart" in rows["possible match"]["detail"]
    assert "2 of 3 bank line(s) matched" in r.summary and r.completion_state == "blocked-escalated"


def test_gst_must_be_one_eleventh_and_gst_free_lines_carry_none() -> None:
    ledger = ("date,description,amount,gst_code,gst\n03/07/2026,Client A,1100.00,GST,110.00\n"
              "06/07/2026,Officeworks,-55.00,GST,5.00\n10/08/2026,Client B,330.00,FRE,30.00")
    rows = _checks(run({"bank": BANK, "ledger": ledger, "date_format": "DD/MM/YYYY", "period": PERIOD}))
    assert "1/11 of 1100.00 is 100.00" in rows["ledger line 2 GST"]["detail"]
    assert "coded FRE (no GST)" in rows["ledger line 4 GST"]["detail"]


def test_an_uncoded_line_is_not_assessed_and_blocks() -> None:
    ledger = LEDGER.replace("330.00,FRE,0", "330.00,,")
    r = run({"bank": BANK, "ledger": ledger, "date_format": "DD/MM/YYYY", "period": PERIOD})
    assert _checks(r)["GST coding"]["result"] == "not assessed" and r.completion_state == "blocked-escalated"


def test_a_reported_bas_figure_that_differs_is_a_conflict_but_cents_are_rounding() -> None:
    rows = _checks(_run(bas="G1=1430.40, 1A=120, 1B=5"))
    assert rows["BAS G1"]["result"] == "ok" and rows["BAS 1A"]["result"] == "conflict"


def test_duplicates_and_out_of_period_lines_are_found() -> None:
    ledger = LEDGER + "\n03/07/2026,Client A,1100.00,GST,100.00\n15/10/2026,Late,22.00,GST,2.00"
    rows = run({"bank": BANK, "ledger": ledger, "date_format": "DD/MM/YYYY", "period": PERIOD}).rows
    assert {"check": "ledger line 5", "detail": "the same entry as line 2"}.items() <= next(
        r for r in rows if r["detail"] == "the same entry as line 2").items()
    assert any(r["check"] == "ledger line 6" and "outside the period" in r["detail"] for r in rows)


def test_a_period_before_gst_or_a_missing_format_is_refused() -> None:
    assert run({"bank": BANK, "ledger": LEDGER, "date_format": "DD/MM/YYYY",
                "period": "1999-07-01 to 1999-09-30"}).completion_state == "blocked-escalated"
    assert "date format" in run({"bank": BANK, "ledger": LEDGER, "date_format": "D/M", "period": PERIOD}).summary


def test_the_runner_reply_is_an_envelope_and_never_verified() -> None:
    reply = run_bundle(catalog()["bookkeeping-check"],
                       {"bank": BANK, "ledger": LEDGER, "date_format": "DD/MM/YYYY", "period": PERIOD})
    assert check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
