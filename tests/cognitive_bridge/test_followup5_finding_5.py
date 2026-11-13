"""A rule uses the items of a collection whose kind no one read: a mapping of records inside the cap, or a row that
is not a mapping, leaves a public entry as AttributeError (the second half of review 3's finding 9's root).

Repair 3 counts declared records in mappings too, so 39 claims in a mapping are refused. Inside the cap the bound,
the tally and the copier all pass a mapping of claims or of hypotheses, and the rule then iterates its keys. The
same shape at one of the 93 budget-only parameters: ``evaluated_trace_rows(history)`` copies a history whose row is
a list, then asks the row for ``.get``.
"""
import pytest

from g6.cognitive_bridge import Hypothesis, MethodRouter, check_beliefs, evaluated_trace_rows, sklearn_payload
from tests.cognitive_bridge.followup5_support import claim


def _how(call):
    try:
        call()
    except Exception as exc:
        return exc
    return None


CASES = {
    'a mapping of claims': lambda: MethodRouter().plan({'a': claim('a'), 'b': claim('b')}),
    'a mapping of hypotheses': lambda: check_beliefs({'a': Hypothesis('a', 's', 0.5), 'b': Hypothesis('b', 's', 0.5)}),
    'claims that are plain objects': lambda: MethodRouter().plan([{'id': 'c'}]),
    'a history row that is a list': lambda: evaluated_trace_rows([['measured_utility', 1.0]]),
    'a payload history row that is text': lambda: sklearn_payload(['a row']),
}


@pytest.mark.parametrize('name', sorted(CASES))
def test_a_collection_of_the_wrong_kind_is_refused_not_dereferenced(name):
    raised = _how(CASES[name])
    assert isinstance(raised, (ValueError, TypeError)), (
        f'{name}: the entry left with {type(raised).__name__}: {raised}')
