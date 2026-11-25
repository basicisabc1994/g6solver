"""Finding 2: a prompt whose scout went out and whose next save was refused says nothing of the request.

e375e476 gives a storage error raised after a request of the call its run's send bound (``error.state``), and words
it on two surfaces: the Stop hook ("Up to N request(s) of this run may have gone out.") and the tool
(``requests_sent``). ``_prepare_locked`` raises the same state-bearing error (``_call_read_error(exc, run.pending)``)
for the scouts a prompt runs (``G6_LOOP_SCOUTS=before_fix``), but ``on_prompt`` has no handler for it: the error
leaves the hook, and ``main`` prints its class and words with no count. The person is told the work is not tested and
nothing of the request that went out to the reviewer's provider.

Real disk and real open handles (review 1's ``Reader``: the receipt at once, the content once a save replaced it). The
reader arrives while the scout is out, after its reservation was saved.

* ``test_a_prompt_...`` (fails): the prompt hook, through ``main`` as Claude Code runs it;
* ``test_control_a_stop_...``: the Stop hook beside the same reader after the same send names the count.
"""
import io
import json
import os
from types import SimpleNamespace

import pytest

from g6.harness import _completion_storage as store, completion_hook as hook, completion_loop as loop
from tests._review_wtffu4_1.rev1_readers import Reader
from tests.harness.test_completion_fu3_repair2 import Session

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='a reader holds a file against replacement only on Windows')
COUNT = '1 request(s) of this run may have gone out'


def _reader_after_the_send(world, monkeypatch):
    reader = Reader(world.folder / 'state.json', also_content=True)
    sends = loop._scout

    def scout(*args):
        made = sends(*args)
        if not reader.handles:
            reader.arrive()
        return made

    monkeypatch.setattr(loop, '_scout', scout)
    return reader


def test_a_prompt_names_the_request_that_went_out_before_its_save_was_refused(tmp_path, monkeypatch, key_file, capsys):
    world = Session(tmp_path, monkeypatch, key_file, 'rev2-prompt-count')
    key_file('G6_LOOP_MODE=scouts\nG6_LOOP_SCOUTS=before_fix\n')
    monkeypatch.setattr(loop.repo_tests, '_git', lambda *args, **kwargs: b'mod.py\x00')
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    reader = _reader_after_the_send(world, monkeypatch)
    event = json.dumps({**world.event, 'prompt': 'old task'}).encode('utf-8')
    monkeypatch.setattr(hook.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(event)))
    try:
        exit_code = hook.main(['prompt'])
    finally:
        reader.leave()
    shown = capsys.readouterr()
    said = shown.out + shown.err
    assert len(world.reviewed) == 1 and reader.replaced, 'one scout went out, then a save met the reader'
    assert loop.attempted(loop._load(world.folder)) == 1, 'the run itself retains the request'
    assert COUNT in said, f'exit {exit_code}; the prompt hook said nothing of the request that went out: {said[:300]!r}'


def test_control_a_stop_beside_the_same_reader_names_the_request(tmp_path, monkeypatch, key_file):
    world = Session(tmp_path, monkeypatch, key_file, 'rev2-stop-count')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    reader = _reader_after_the_send(world, monkeypatch)
    try:
        said = hook.on_stop(world.event).get('systemMessage', '')
    finally:
        reader.leave()
    assert len(world.reviewed) == 1 and reader.replaced
    assert COUNT in said, said[:300]
