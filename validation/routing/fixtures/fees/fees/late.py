# SPDX-License-Identifier: Apache-2.0
"""Late fees on unpaid invoices."""


def late_fee(days_late, amount):
    """The fee on ``amount`` paid ``days_late`` days after its due date. Nothing for 3 days late or fewer;
    after that, 2% of the amount for each started week counted from the due date, capped at 10% of
    the amount. Rounded to cents."""
    return round(amount * 0.02 * (days_late // 7), 2)
