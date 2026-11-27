# SPDX-License-Identifier: Apache-2.0
"""Order-sensitive expressions keep distinct method identities.

The rewrite-invariance pins this file held (every descriptive rewrite combination, the safe-literal inventory, and
different execution reaching admission with no recorded cases) were superseded by the recorded-behaviour identity
design (07e0ffe8, lead ruling 63266342): identity is an exact canonical match, or a replay of the inactive entry's
recorded cases, held by tests/distill/test_inactive_behaviour_identity.py and test_inactive_recorded_cases.py.
"""
import pytest

from g6.distill import acceptance_gate as gate, records as r
from tests.distill.test_records import bodies


@pytest.mark.parametrize('left,right', [
    ("{'a': facts.pop(), 'b': facts.pop()}", "{'b': facts.pop(), 'a': facts.pop()}"),
    ("{'a': 1, 'a': 2}", "{'a': 2, 'a': 1}"),
])
def test_order_sensitive_dictionary_expressions_stay_distinct(left, right):
    def candidate(body):
        return r.VerifierCandidate(**(bodies()[3][1] | {'body': 'def solve(facts):\n return ' + body}))
    assert gate._method_identity(candidate(left)) != gate._method_identity(candidate(right))
