# SPDX-License-Identifier: Apache-2.0
"""The nightly file the accounts system imports: one ``name,amount`` row per item, no header."""
from invoices.fmt import money


def to_csv(lines):
    return "".join(f"{name},{money(amount)}\n" for name, amount in lines)
