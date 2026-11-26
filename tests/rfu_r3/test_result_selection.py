"""A final ambiguous result cannot resurrect an earlier success."""
import json

import pytest


@pytest.mark.parametrize('last', [
    {}, {'type': None}, {'type': ['result']}, {'type': 'result', 'is_error': 'false'},
    {'type': 'result', 'is_error': False, 'result': []},
    {'type': 'assistant', 'is_error': True, 'result': 'latest failed'},
])
def test_claude_cannot_use_an_old_success_after_an_ambiguous_object(last):
    from g6.agents.agent_claude.adapter import _result
    assert _result('{"type":"result","is_error":false,"result":"old"}\n' + json.dumps(last)) is None


def test_claude_ignores_a_typed_nonresult_event():
    from g6.agents.agent_claude.adapter import _result
    earlier = {'type': 'result', 'is_error': False, 'result': 'done'}
    assert _result(json.dumps(earlier) + '\n{"type":"system","message":"end"}') == earlier


