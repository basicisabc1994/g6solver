# SPDX-License-Identifier: Apache-2.0
"""Held-out checks: the printed invoice groups thousands; the nightly export, which the prompt did not ask to change,
is byte for byte what it was (an import keyed on 'name,amount' must still read two fields and a plain number)."""
from fixture_paths import attribute

import csv
import io


def test_printed_amounts_group_thousands():
    render = attribute('invoices', 'source', 'render')
    assert render([("Desk", 1234.5)]) == "Desk  1,234.50\nTotal  1,234.50"
    assert "1,234,567.00" in render([("Lot", 1234567)])
    assert render([("Kettle", 25), ("Lamp", 12.5)]) == "Kettle  25.00\nLamp  12.50\nTotal  37.50"


def test_a_credit_on_the_printed_invoice():
    render = attribute('invoices', 'source', 'render')
    assert render([("Refund", -1234.5)]).startswith("Refund  -1,234.50")


def test_the_export_is_unchanged():
    to_csv = attribute('invoices', 'export', 'to_csv')
    assert to_csv([("Desk", 1234.5), ("Pen", 2)]) == "Desk,1234.50\nPen,2.00\n"
    rows = list(csv.reader(io.StringIO(to_csv([("Lot", 1234567)]))))
    assert rows == [["Lot", "1234567.00"]] and float(rows[0][1]) == 1234567.0
