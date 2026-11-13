"""The block's plan_methods op is a public caller of the router that encodes an oversized model before the cap.

Review 2's rule 4: the 250,000-byte declarative-model cap precedes model encoding through every public caller of the
router. The block door (the MCP surface) takes a claim's models as JSON: its schema declares no bound for ``models``,
the walker reads the model cap only where it detaches a ClaimIR record, and the block makes the record only after its
own round trips. A 250,001-character model is encoded several times before the router refuses it. No in-process code is
needed: the arguments are plain JSON.
"""
import pytest
from pydantic import ValidationError

from g6.cognitive_bridge.block import CognitiveBridgeBlock, CognitiveInput
from g6.cognitive_bridge.codes import INPUT
from tests.cognitive_bridge.followup4_repair3_support import watch_encodings


def _arguments(formula):
    return {'op': 'plan_methods', 'claims': [{'id': 'c', 'statement': 's', 'evidence_needs': ['formal'],
                                              'models': {'formal': {'formula': formula}}}]}


@pytest.mark.parametrize('route', ['infer', 'door'])
def test_block_plan_methods_reads_the_model_cap_before_any_encoding(monkeypatch, route):
    formula = 'A' * 250_001
    raw = _arguments(formula)
    encoded = watch_encodings(monkeypatch, formula)
    block = CognitiveBridgeBlock()
    if route == 'infer':
        answer = block.infer(raw).unwrap()
    else:
        # as G6's registry calls a block: the raw arguments screened, then the typed input it rebuilt answered
        screened = block.screen_raw(raw)
        if screened is not None:
            answer = screened.unwrap()
        else:
            try:
                answer = block.infer(CognitiveInput.model_validate(raw)).unwrap()
            except ValidationError as exc:
                answer = {'code': INPUT, 'reason': str(exc)}
    assert answer['code'] == INPUT
    assert '250 kB' in answer['reason']
    assert encoded == [], f'an oversized declarative model was encoded {len(encoded)} times before the router cap'
