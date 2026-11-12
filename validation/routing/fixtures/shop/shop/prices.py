# SPDX-License-Identifier: Apache-2.0
"""Order totals for the synthetic shop."""


def total(lines):
    """The sum of quantity times unit price over ``lines``."""
    return sum(quantity * unit_price for quantity, unit_price in lines)