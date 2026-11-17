# SPDX-License-Identifier: Apache-2.0
"""How amounts are written."""


def money(amount):
    """``amount`` with two decimals, as 1234.50."""
    return f"{amount:.2f}"
