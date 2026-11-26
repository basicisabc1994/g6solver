"""Finding 3: after a request went out and its save was refused, the prompt hook tells the agent and shows the person
nothing; the Stop hook shows the person a count and an operating-system error that names neither G6 nor the work.

2b07c0bf catches the state-bearing storage error in ``on_prompt`` (completion_hook.py lines 294-302) and returns it
as ``hookSpecificOutput.additionalContext``: by this module's own words that is "what the agent is told as a task
starts" (``_context``), and Claude Code adds it to the model's context. The hook then exits 0 with nothing on
standard error and no ``systemMessage``, the two places a person is shown anything ("it ends with one line on
standard error", the module's docstring; ``systemMessage`` is what ``on_stop`` uses). Before this change the same
error left the hook and ``main`` printed its one line for the person (without the count, review 2's finding); every
other failure of the prompt hook still does. So the count review 2 asked for reaches the agent, and the person, whose
request went out to the reviewer's provider, is told nothing at the prompt. The committed test of this repair
(``test_repair2_prompt_prints_the_send_bound_before_a_long_storage_path``) replaces ``HOOKS['prompt']`` and so reads
``main``'s line on standard error, cut at 300 characters: a path the real prompt hook no longer takes for this error.

On the Stop hook the same error is ``{'systemMessage': _storage_error_text(exc, state)}`` (line 334): "Up to 1
request(s) of this run may have gone out. [WinError 5] Access is denied: '...state.json.new' -> '...state.json'".
Nothing in it says the completion loop stopped or that the work is not tested, which the prompt's text and ``main``'s
line both say for the same error ("G6's completion loop stopped, so this work is not tested: ..."), and which the
module promises of every stop ("a turn never ends with the work silently untested").

Real disk and real open handles (review 1's ``Reader``), as in review 2's prompt test: the reader arrives while the
scout is out, after its reservation was saved.

* ``test_a_prompt_shows_the_person_...`` (fails);
* ``test_a_stop_says_the_work_is_not_tested_...`` (fails);
* ``test_control_the_agent_is_told_at_the_prompt``: the same call's ``additionalContext`` has the count and the words;
* ``test_control_a_stop_shows_the_person_the_count``: the Stop hook's ``systemMessage`` has the count;
* ``test_control_any_other_prompt_failure_is_shown_to_the_person``: a storage error of the prompt hook that carries
  no count ends on standard error with exit 1 and says the work is not tested.
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
COUNT = 'Up to 1 request(s) of this run may have gone out.'
UNTESTED = 'not tested'


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


def _prompt(world, monkeypatch, capsys):
    """The prompt hook as Claude Code runs it. Returns its exit code, what it gave the agent (``additionalContext``)
    and what it showed the person (standard error and ``systemMessage``)."""
    event = json.dumps({**world.event, 'prompt': 'old task'}).encode('utf-8')
    monkeypatch.setattr(hook.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(event)))
    exit_code = hook.main(['prompt'])
    shown = capsys.readouterr()
    reply = json.loads(shown.out) if shown.out.strip() else {}
    agent = (reply.get('hookSpecificOutput') or {}).get('additionalContext', '')
    return exit_code, agent, shown.err + str(reply.get('systemMessage', ''))


def _prompt_after_the_send(tmp_path, monkeypatch, key_file, capsys, name):
    world = Session(tmp_path, monkeypatch, key_file, name)
    key_file('G6_LOOP_MODE=scouts\nG6_LOOP_SCOUTS=before_fix\n')
    monkeypatch.setattr(loop.repo_tests, '_git', lambda *args, **kwargs: b'mod.py\x00')
    store.mkdir(world.folder, parents=True, exist_ok=True)
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    reader = _reader_after_the_send(world, monkeypatch)
    try:
        said = _prompt(world, monkeypatch, capsys)
    finally:
        reader.leave()
    assert len(world.reviewed) == 1 and reader.replaced, 'one scout went out, then a save met the reader'
    assert loop.attempted(loop._load(world.folder)) == 1
    return said


def _stop_after_the_send(tmp_path, monkeypatch, key_file, name):
    world = Session(tmp_path, monkeypatch, key_file, name)
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    reader = _reader_after_the_send(world, monkeypatch)
    try:
        said = hook.on_stop(world.event).get('systemMessage', '')
    finally:
        reader.leave()
    assert len(world.reviewed) == 1 and reader.replaced
    return said


def test_a_prompt_shows_the_person_the_request_that_went_out(tmp_path, monkeypatch, key_file, capsys):
    exit_code, agent, person = _prompt_after_the_send(tmp_path, monkeypatch, key_file, capsys, 'rev3-prompt-person')
    assert COUNT in person, (f'exit {exit_code}; the person is shown {person!r}; the count went to the agent alone: '
                             f'{agent[:120]!r}')


def test_a_stop_says_the_work_is_not_tested_when_a_save_was_refused_after_a_send(tmp_path, monkeypatch, key_file):
    said = _stop_after_the_send(tmp_path, monkeypatch, key_file, 'rev3-stop-untested')
    assert COUNT in said
    assert UNTESTED in said and 'G6' in said, f'the whole of what the person is shown: {said[:160]!r}'


def test_control_the_agent_is_told_at_the_prompt(tmp_path, monkeypatch, key_file, capsys):
    exit_code, agent, _ = _prompt_after_the_send(tmp_path, monkeypatch, key_file, capsys, 'rev3-prompt-agent')
    assert exit_code == 0 and COUNT in agent and UNTESTED in agent and agent.startswith("G6's completion loop stopped")


def test_control_a_stop_shows_the_person_the_count(tmp_path, monkeypatch, key_file):
    assert COUNT in _stop_after_the_send(tmp_path, monkeypatch, key_file, 'rev3-stop-count')


def test_control_any_other_prompt_failure_is_shown_to_the_person(tmp_path, monkeypatch, key_file, capsys):
    world = Session(tmp_path, monkeypatch, key_file, 'rev3-prompt-other')

    def refused(*args, **kwargs):
        raise PermissionError(13, 'held by another program')

    monkeypatch.setattr(loop, 'prepare', refused)
    exit_code, agent, person = _prompt(world, monkeypatch, capsys)
    assert exit_code == 1 and not agent
    assert person.startswith("G6's completion loop stopped, so this work is not tested: PermissionError")
