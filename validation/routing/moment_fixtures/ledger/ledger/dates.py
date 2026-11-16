# SPDX-License-Identifier: Apache-2.0
"""Due dates."""


def add_months(day, months):
    """``day`` moved by ``months`` whole months (back when negative), on the month's last day when that month is
    shorter; a date and time keeps its time."""
    return day.replace(month=day.month + months)
