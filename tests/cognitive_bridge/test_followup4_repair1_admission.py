"""Pre-work admission, including conversion protocols without a measurable size."""
from array import array

import pytest

from g6.cognitive_bridge import EvidenceArtifact, snapshot, xai
from g6.cognitive_bridge.codes import NotPlainData


def test_an_unmeasured_array_cannot_run_its_converter():
    calls = []

    class Unmeasured:
        def tolist(self):
            calls.append('converted')
            return [1, 2]

    with pytest.raises(NotPlainData, match='size'):
        xai._native(Unmeasured(), where='array')
    assert calls == []


def test_empty_multidimensional_array_counts_generated_containers():
    calls = []

    class EmptyRows:
        shape = (snapshot.MAX_ITEMS, 0)

        def tolist(self):
            calls.append('converted')
            return [[]] * self.shape[0]

    with pytest.raises(NotPlainData, match='size'):
        xai._native(EmptyRows(), where='array')
    assert calls == []


def test_measured_builtin_array_still_converts():
    assert xai._native(array('i', [1, 2]), where='array') == [1, 2]


@pytest.mark.parametrize('factory', [bytes, bytearray,
    lambda size: memoryview(array('I', [0]) * (size // 4 + 1))])
def test_buffer_cap_uses_bytes_including_nonbyte_views(factory):
    value = factory(2_000_001)
    with pytest.raises(ValueError, match='artifact exceeds 2MB'):
        EvidenceArtifact('e', value, 'report', 'candidate', 'store:e', 'claim')


@pytest.mark.parametrize('value', ['\x00', '\U0001f600', '\\', '"', '\n', 'a', '\ud800'])
def test_encoded_admission_preserves_exact_boundary(monkeypatch, value):
    expected = snapshot.json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()
    monkeypatch.setattr(snapshot, 'MAX_SIZE', len(expected))
    assert snapshot.canonical_json(value) == expected
    monkeypatch.setattr(snapshot, 'MAX_SIZE', len(expected) - 1)
    dumps = snapshot.json.dumps
    outputs = []

    def observed(item, *args, **kwargs):
        out = dumps(item, *args, **kwargs)
        if len(out) > snapshot.MAX_SIZE:
            outputs.append(out)
        return out

    monkeypatch.setattr(snapshot.json, 'dumps', observed)
    with pytest.raises(NotPlainData, match='size'):
        snapshot.canonical_json(value)
    assert outputs == []
