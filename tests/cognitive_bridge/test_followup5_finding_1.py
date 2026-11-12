"""The walker copies a caller's collection whole before it reads its size (a regression of repair 3).

Repair 3 takes a list, a tuple or a mapping once (``tuple(value)``, ``tuple(dict.items(value))``) so that what is
counted is what is copied. The take stands before the count is read against the visit budget and before any declared
bound, so a collection far past every bound is copied in full, then refused. On b683e40 the same calls read the
native length first and allocated about 2 kB (35 kB through the planner, 24 kB through the block).
"""
import pytest

from g6.cognitive_bridge import MetacognitiveMethodSelector, MetacognitivePlanner, ProblemSignature, snapshot
from g6.cognitive_bridge.block import CognitiveBridgeBlock
from tests.cognitive_bridge.followup5_support import claim, peak_of

#: Far more than a refusal needs, far less than a copy of any case below (16 MB to 32 MB).
SMALL = 1_000_000


def _list():
    value = [None] * 2_000_000
    return lambda: snapshot.snapshot(value, where='allow')


def _mapping():
    value = dict.fromkeys(map(str, range(500_000)))
    return lambda: snapshot.plain(value, where='a mapping')


def _claims():
    # a parameter with a declared cap of 20, at an entry with no earlier check of its own
    value = [claim()] * 2_000_000
    planner = MetacognitivePlanner(MetacognitiveMethodSelector())
    return lambda: planner.plan(value, ProblemSignature('review', 'logic', 'validity', 1, 2), [],
                                required_methods=['formal'])


def _block():
    # the MCP door's plain JSON: a probe's arguments, which the input model does not bound
    raw = {'op': 'rank', 'hypotheses': [{'id': 'a', 'statement': 's', 'prior': 0.5},
                                        {'id': 'b', 'statement': 's', 'prior': 0.5}],
           'probes': [{'id': 'p', 'question': 'q', 'tool': 't', 'positive_if': {'a': True, 'b': False},
                       'assumed_match_probability': 0.9, 'arguments': dict.fromkeys(map(str, range(500_000)))}]}
    block = CognitiveBridgeBlock()
    return lambda: block.screen_raw(raw).unwrap()['reason']


@pytest.mark.parametrize('case', [_list, _mapping, _claims, _block], ids=lambda case: case.__name__.strip('_'))
def test_a_collection_past_the_budget_is_refused_before_it_is_copied(case):
    refused, peak = peak_of(case())
    assert 'size exceeds' in refused
    assert peak < SMALL, f'{peak:,} bytes were allocated before the refusal: the collection was copied first'
