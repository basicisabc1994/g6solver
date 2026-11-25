import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls


@pytest.mark.parametrize('assignment', ['host = p.write_text', 'host, = (p.write_text,)'],
                         ids=['control-direct-capture', 'unpacked-capture'])
def test_inventory_rejects_captured_writer(tmp_path, assignment):
    target = tmp_path / 'outside-the-gate.txt'
    source = assignment + "\nhost('written without the completion gate')\n"
    exec(source, {'p': target})
    assert target.read_text() == 'written without the completion gate'
    assert unguarded_calls(source), 'inventory admitted a captured filesystem writer as its reviewed host callback'
