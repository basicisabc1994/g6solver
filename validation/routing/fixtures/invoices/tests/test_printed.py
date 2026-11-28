# SPDX-License-Identifier: Apache-2.0
from invoices.printed import render


def test_one_row_per_item_and_a_total():
    assert render([("Kettle", 25), ("Lamp", 12.5)]) == "Kettle  25.00\nLamp  12.50\nTotal  37.50"
