"""Repair 3: storage failures tell both the person and the agent."""
import io
import json
from types import SimpleNamespace

from g6.harness import completion_hook as hook, completion_loop as loop
from tests.harness.test_completion_fu3_repair2 import Session


def test_prompt_and_stop_use_the_same_visible_storage_error(tmp_path, monkeypatch, key_file, capsys):
    world = Session(tmp_path, monkeypatch, key_file, 'repair3-hooks')
    error = loop._call_read_error(OSError('held publication'), {loop.ATTEMPTED: 4, 'call_requests': 1})

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr(loop, 'prepare', failed)
    monkeypatch.delenv(hook.CHILD, raising=False)
    event = {**world.event, 'prompt': 'task'}
    monkeypatch.setattr(hook.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(event).encode())))
    assert hook.main(['prompt']) == 0
    output = capsys.readouterr()
    reply = json.loads(output.out)
    agent = reply['hookSpecificOutput']['additionalContext']
    assert reply.get('systemMessage') == agent
    assert agent.startswith("G6's completion loop stopped, so this work is not tested: Up to 4 request(s)")
    monkeypatch.setattr(hook, '_on_stop', failed)
    assert hook.on_stop(world.event)['systemMessage'] == agent
    monkeypatch.setitem(hook.HOOKS, 'prompt', failed)
    monkeypatch.setattr(hook.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'{}')))
    assert hook.main(['prompt']) == 1
    assert capsys.readouterr().err.strip() == agent
