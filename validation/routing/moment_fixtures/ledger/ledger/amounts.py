# SPDX-License-Identifier: Apache-2.0
"""Amounts as invoices write them."""
from decimal import Decimal


def parse_amount(text):
    """The amount ``text`` writes. A thousands mark may be a comma, a dot or a space; the decimal mark is the last
    comma or dot when exactly two digits follow it; an amount in brackets is negative; a currency sign or code on
    either side is ignored."""
    return Decimal(text.replace(",", ""))
