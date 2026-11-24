"""Keep disk fault injection compatible with file callers and memory journals."""
import json

import pytest

from g6.core.ledger import ChainLedger
from g6.distill import acceptance_gate as gate, ledger
from tests.distill.test_repair2_atomicity import interrupt


def test_fault_injector_preserves_direct_file_and_path_helpers(tmp_path, monkeypatch):
    hit = interrupt(monkeypatch, 'object')
    path = tmp_path / 'ordinary.json'
    stream = path.open('x', encoding='utf-8')
    try:
        assert stream.write('{"value": 1}') == 12
        stream.flush()
        assert not stream.closed
        assert stream.fileno() >= 0
    finally:
        stream.close()
    assert stream.closed
    assert json.loads(path.read_text(encoding='utf-8')) == {'value': 1}
    assert path.write_text('updated', encoding='utf-8') == 7
    assert path.read_bytes() == b'updated'
    assert not hit


def test_fault_injector_raises_after_real_object_close(tmp_path, monkeypatch):
    path = tmp_path / 'objects' / 'entry.json'
    path.parent.mkdir()
    hit = interrupt(monkeypatch, 'object')
    with pytest.raises(OSError, match='injected after durable object'):
        with path.open('x', encoding='utf-8') as stream:
            stream.write('{"value": 1}')
            assert not hit
    assert stream.closed
    assert hit == [True]
    assert json.loads(path.read_text(encoding='utf-8')) == {'value': 1}


def test_fault_injector_preserves_original_write_exception(tmp_path, monkeypatch):
    path = tmp_path / 'objects' / 'entry.json'
    path.parent.mkdir()
    hit = interrupt(monkeypatch, 'object')
    with pytest.raises(ValueError, match='original failure'):
        with path.open('x', encoding='utf-8') as stream:
            stream.write('partial')
            raise ValueError('original failure')
    assert stream.closed
    assert not hit
    assert path.read_text(encoding='utf-8') == 'partial'


@pytest.mark.parametrize('failing', [False, True])
def test_memory_journal_does_not_enlist_a_disk_restore(tmp_path, monkeypatch, failing):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    journal = ChainLedger()
    path = tmp_path / 'retained.txt'
    path.write_text('before', encoding='utf-8')

    def unexpected_recovery():
        pytest.fail('an in-memory ledger has no disk recovery')

    def write():
        with ledger.failure_writer():
            ledger.retain_before_write(journal.path, recover=unexpected_recovery)
            gate._append(journal, 'decision', {'verdict': 'refuse'})
            ledger.retain_before_write(path)
            path.write_text('after', encoding='utf-8')
            if failing:
                raise OSError('abort disk transaction')

    if failing:
        with pytest.raises(OSError, match='abort disk transaction'):
            write()
    else:
        write()
    assert path.read_text(encoding='utf-8') == ('before' if failing else 'after')
    assert journal.entries()[0]['body'] == {'verdict': 'refuse'}
    assert journal.path is None
