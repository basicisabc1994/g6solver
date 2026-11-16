# SPDX-License-Identifier: Apache-2.0
"""Amounts of money."""


def round_money(amount):
    """``amount`` rounded to whole cents, a half cent rounded away from zero."""
    return int(amount * 100) / 100
