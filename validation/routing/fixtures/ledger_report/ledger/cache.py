# SPDX-License-Identifier: Apache-2.0
"""Answers kept for the ledger they were worked out for."""

_kept = {}


def remembered(function):
    """``function``, answering again for a ledger it has already been asked about."""
    def wrapper(entries):
        key = id(entries)
        if key not in _kept:
            _kept[key] = function(entries)
        return _kept[key]
    return wrapper
