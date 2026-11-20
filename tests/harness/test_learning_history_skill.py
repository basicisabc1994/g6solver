# SPDX-License-Identifier: Apache-2.0
"""The history skill uses the generic MCP door with isolated local state."""
import os
import socket

from g6 import mcp
from g6.core import skills


def test_skill_lists_history_offline_in_a_temporary_home(monkeypatch):
    text = skills.skill_text('learning_history')
    assert text is not None, 'learning_history ships no skill/SKILL.md'
    examples = skills.skill_python_examples(text)
    assert len(examples) == 1
    original_home = os.environ['G6_HOME']
    calls = []

    def offline(*args, **kwargs):
        raise OSError('history skill runs offline')

    call_tool = mcp.invoke_reply

    def local_reply(component, op, params_json):
        assert component == 'learning_history'
        assert op == 'list'
        assert os.environ['G6_HOME'] != original_home
        reply = call_tool(component, op, params_json)
        calls.append(reply)
        return reply

    monkeypatch.setattr(socket.socket, 'connect', offline)
    monkeypatch.setenv('G6_DISABLE_LLM', '1')
    monkeypatch.setattr(mcp, 'invoke_reply', local_reply)
    exec(compile(examples[0], '<learning_history skill>', 'exec'), {'__name__': '__skill_example__'})
    assert len(calls) == 1 and calls[0]['ok']
    assert calls[0]['result']['retained_checks'] == 0
    assert calls[0]['result']['admitted_trials'] == 0
    assert calls[0]['result']['checks'] == []
    assert os.environ['G6_HOME'] == original_home
