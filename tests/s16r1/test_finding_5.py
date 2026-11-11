import asyncio

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud
from g6.mcp import build_server, mission


def test_agent_supplied_repository_is_not_personal_consent(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(mission.repo_tests, 'checked_repo', lambda repo: tmp_path)
    monkeypatch.setattr(mission.repo_tests, '_git', lambda *args: b'tests/test_PRIVATE_REPOSITORY_NAME.py\0')
    questions = []

    async def decline(message, response_type, params, context):
        questions.append(message)
        return response_type(action='decline')

    async def run():
        async with Client(build_server(), client_info=Implementation(name='codex', version='review'),
                          elicitation_handler=decline) as client:
            return await client.call_tool('g6_premortem', {'repo': str(tmp_path), 'task': 'Repair the code'})

    reply = asyncio.run(run())
    assert cloud.grants() == []
    assert questions == []
    assert 'PRIVATE_REPOSITORY_NAME' not in str(reply), 'repo tool argument authorized sharing without a person accepting'
