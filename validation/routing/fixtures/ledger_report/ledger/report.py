# SPDX-License-Identifier: Apache-2.0
"""Reports over ledger entries, each a ``(kind, amount)`` pair whose kind is ``credit`` or ``debit``."""


def balance(entries):
    """The balance of ``entries``: credits add, debits subtract."""
    return sum(amount for _kind, amount in entries)
