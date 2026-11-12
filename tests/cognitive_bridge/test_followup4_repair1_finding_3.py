"""The encoder must admit escaped output size before allocating that output."""
import pytest

from g6.cognitive_bridge import snapshot
from g6.cognitive_bridge.codes import INPUT, NotPlainData


def test_escaped_size_is_bounded_before_json_allocation(monkeypatch):
    # One million code points fit the four-million size budget; JSON escaping
    # makes six million ASCII characters, before the additional UTF-8 copy.
    payload = "\x00" * 1_000_000
    native_dumps = snapshot.json.dumps
    oversized_outputs = []

    def observed_dumps(value, *args, **kwargs):
        answer = native_dumps(value, *args, **kwargs)
        if value is payload and len(answer) > snapshot.MAX_SIZE:
            oversized_outputs.append(len(answer))
        return answer

    monkeypatch.setattr(snapshot.json, "dumps", observed_dumps)
    with pytest.raises(NotPlainData) as caught:
        snapshot.plain(payload, where="caller.payload")

    assert caught.value.code == INPUT
    assert oversized_outputs == [], f"allocated oversized JSON outputs: {oversized_outputs}"
