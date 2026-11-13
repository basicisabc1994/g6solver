"""A size admission hashes a caller's bytes, whole, before the boundary refuses them.

``_admit_json`` projects the value with ``view`` before the JSON check reads it, and ``view`` writes bytes as their
SHA-256. The walker charges bytes one unit of its size budget whatever their length, so the hash is bounded by nothing.
Handed to a raw caller value (compile's ``variant_models`` since repair 2, through ``bound=_admit_models``; a prompt's
context and evidence summary since repair 1), the admission hashes bytes that the boundary then refuses as not JSON.
On 6e39ab0 compile refused the same value with no hash taken.
"""
import pytest

from g6.cognitive_bridge import (
    CognitiveAssuranceOrchestrator, LLMClaimCompiler, MetacognitiveMethodSelector,
)
from g6.cognitive_bridge.critique import LLMMetacognitiveCritic
from tests.cognitive_bridge.followup4_repair3_support import NoModel, compile_inputs, watch_hashes


def _compile(blob):
    inputs = compile_inputs()
    inputs['variant_models'] = {'v': {'formula': 'A', 'attachment': blob}}
    return CognitiveAssuranceOrchestrator(MetacognitiveMethodSelector()).compile(**inputs)


@pytest.mark.parametrize('entry', [
    _compile,
    lambda blob: LLMClaimCompiler(NoModel()).draft('q', [blob]),
    lambda blob: LLMMetacognitiveCritic(NoModel({'candidates': []})).propose('q', 'm', [blob]),
], ids=['compile:variant_models', 'draft:context', 'critic:evidence_summary'])
def test_bytes_in_untyped_data_are_refused_before_they_are_hashed(monkeypatch, entry):
    blob = b'\x00' * 4096  # any length: the walker charges bytes one unit, so a gigabyte is hashed the same way
    hashed = watch_hashes(monkeypatch, blob)
    with pytest.raises(ValueError, match='bytes is not JSON data'):
        entry(blob)
    assert hashed == [], 'the caller\'s bytes were hashed, whole, before the boundary refused them'
