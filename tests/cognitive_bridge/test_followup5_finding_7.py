"""The block reads a claim's declared bounds on the caller's arguments, then the walker takes them: what was admitted
is not what is copied and encoded (review 3's finding 1, at the door repair 3 added for finding 2).

Repair 3: "what it counts, hands to a declared bound and copies is that take", and "the block's admission of its raw
arguments reads [a claim's bounds] for every claim, before anything is encoded". The block's admission
(``_as_given`` -> ``_admit_input`` -> ``_admit_claim``) runs on the caller's own mapping, before the walker's take. A
model that grows past the 250 kB cap between the two is copied and encoded by the boundary before the router's cap
refuses it. The writer here is a deterministic stand-in for a second thread, as review 3's was.
"""
import pytest

from g6.cognitive_bridge.block import CognitiveBridgeBlock
from tests.cognitive_bridge.followup5_support import raw_claim, watch_encodings, writer_before_the_take

OVER = 'B' * 250_001


@pytest.mark.parametrize('route', ['screen_raw', 'infer'])
def test_a_model_that_grows_after_the_blocks_admission_is_not_encoded(route, monkeypatch):
    raw = {'op': 'plan_methods', 'claims': [raw_claim('c', {'formal': {'formula': 'A'}})]}

    def grow():
        raw['claims'][0]['models']['formal']['formula'] = OVER

    encoded = watch_encodings(monkeypatch, OVER)
    with writer_before_the_take(raw, grow) as writer:
        getattr(CognitiveBridgeBlock(), route)(raw)
    assert writer['done'], 'the writer never ran: the instrument is broken'
    assert encoded == [], f'a model past the 250 kB cap was encoded {len(encoded)} times after the block admitted it'
