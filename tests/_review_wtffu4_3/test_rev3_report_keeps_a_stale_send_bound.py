"""Finding 1: a run's final report says "Up to 1 request(s) of this run may have gone out" after the run sent three.

2b07c0bf words the run's send bound on the "internal" surface too: where a scout's (or the critic's) own session
raises an ``OSError`` after its request was counted, ``_scout`` (completion_loop.py line 574) and ``run_critic`` (line
926) now keep ``"<Class>: Up to N request(s) of this run may have gone out. <error>"`` as that set's ``left_out`` (the
review's ``error``). Unlike the hook's and the tool's reply, this text is not the end of the call: it is saved in the
run's state, the run goes on, the next scouts send their own requests, and the report made at the end
(``patch_report``: "- Left out `scout` (...): `...`") prints the sentence as it was when the first scout failed. The
sentence is about the run ("of this run"), so in the report of a run whose retained bound is 3 the person reads that
up to 1 request may have gone out, and beside it "Up to 2" and "Up to 3" for the other two sets.

Before this change the same line read ``OSError: <error>``, which claims nothing about what was sent.

The scout here is the real ``_scout``; its session is a stand-in that notes one model call (as ``ToolSession._launch``
does with ``_start_model_call`` before it starts the program) and then fails as a program that cannot be started does.
The copy of the base commit and the probes' run are stood in, as no probe exists. Everything else is the Stop hook.

* ``test_the_final_report_...`` (fails, both forms): every send bound the report words is the run's own retained
  bound. ``the-first-scout-alone``: only the first session fails with an ``OSError`` (the others as a model call
  does), so "Up to 1" is the one bound the report words, of a run that retains 3. ``every-scout``: the report words
  "Up to 1", "Up to 2" and "Up to 3" for the same run;
* ``test_control_a_session_that_fails_with_no_oserror_...``: the same run with a session that fails as a model call
  does (``LLMError``) words no bound in the report, and the run's bound is 3 all the same;
* ``test_control_a_storage_error_that_ends_the_call_...``: where the sentence ends the call (the Stop hook's own
  storage error, review 2's reader) its number is the run's retained bound.
"""
import os
import re
from contextlib import contextmanager

import pytest

from g6.core.llm import LLMError
from g6.core.model_calls import note_model_call
from g6.harness import completion_hook as hook, completion_loop as loop
from tests._review_wtffu4_1.rev1_readers import Reader
from tests.harness.test_completion_fu3_repair2 import Session

REAL_SCOUT = loop._scout
BOUND = re.compile(r'Up to (\d+) request\(s\) of this run may have gone out')


def _real_scouts(world, monkeypatch, *failures):
    """The real ``_scout`` with a session that notes its request and then raises the next of ``failures`` (the last
    one again once they run out)."""
    @contextmanager
    def copy(change, seconds):
        yield world.repo

    def session(asked, cwd, where, seconds):
        note_model_call()
        world.reviewed.append(asked)
        raise failures[min(len(world.reviewed), len(failures)) - 1]

    monkeypatch.setattr(loop, '_scout', REAL_SCOUT)
    monkeypatch.setattr(loop, '_base_copy', copy)
    monkeypatch.setattr(loop, 'scout_host', lambda: session)
    monkeypatch.setattr(loop, 'run_set', lambda *args, **kwargs: loop.ProbeRun(None, {}, {}, 'no test file G6 could run'))
    monkeypatch.setattr(loop, '_checks', lambda *args, **kwargs: ([], {}, []))


def _stopped(world):
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    said = hook.on_stop(world.event).get('systemMessage', '')
    state = loop._load(world.folder)
    assert state.get('done') and len(world.reviewed) == 3, 'the run ended, and each of its three scouts sent once'
    assert loop.attempted(state) == 3, 'the run retains its bound: three requests'
    return said, state


@pytest.mark.parametrize('failures', [
    (OSError('the session program could not be started'), LLMError('the session program could not be started')),
    (OSError('the session program could not be started'),),
], ids=['the-first-scout-alone', 'every-scout'])
def test_the_final_report_words_no_send_bound_below_the_runs_own(tmp_path, monkeypatch, key_file, failures):
    world = Session(tmp_path, monkeypatch, key_file, 'rev3-stale-bound')
    _real_scouts(world, monkeypatch, *failures)
    said, state = _stopped(world)
    worded = [int(n) for n in BOUND.findall(said)]
    assert 'Left out' in said and 'could not be started' in said, said[:600]
    assert all(n == loop.attempted(state) for n in worded), (
        f'the report of a run whose retained bound is {loop.attempted(state)} words these bounds for "this run": '
        f'{worded}: ' + ' | '.join(line for line in said.splitlines() if 'Left out' in line))


def test_control_a_session_that_fails_with_no_oserror_words_no_bound_in_the_report(tmp_path, monkeypatch, key_file):
    world = Session(tmp_path, monkeypatch, key_file, 'rev3-stale-bound-control')
    _real_scouts(world, monkeypatch, LLMError('the session program could not be started'))
    said, _ = _stopped(world)
    assert 'Left out' in said and 'could not be started' in said and not BOUND.findall(said)


@pytest.mark.skipif(os.name != 'nt', reason='a reader holds a file against replacement only on Windows')
def test_control_a_storage_error_that_ends_the_call_words_the_runs_retained_bound(tmp_path, monkeypatch, key_file):
    world = Session(tmp_path, monkeypatch, key_file, 'rev3-stale-bound-stop')
    hook.on_prompt({**world.event, 'prompt': 'old task'})
    loop._save(world.folder, {loop.ATTEMPTED: 0})
    reader = Reader(world.folder / 'state.json', also_content=True)
    sends = loop._scout

    def scout(*args):
        made = sends(*args)
        if not reader.handles:
            reader.arrive()
        return made

    monkeypatch.setattr(loop, '_scout', scout)
    try:
        said = hook.on_stop(world.event).get('systemMessage', '')
    finally:
        reader.leave()
    assert reader.replaced and len(world.reviewed) == 1
    assert [int(n) for n in BOUND.findall(said)] == [loop.attempted(loop._load(world.folder))] == [1]
