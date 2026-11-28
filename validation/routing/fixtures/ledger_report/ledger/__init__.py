# SPDX-License-Identifier: Apache-2.0
"""The synthetic ledger. A balance is remembered once it is worked out, so a large ledger is not added up twice."""
from ledger import cache, report

report.balance = cache.remembered(report.balance)
