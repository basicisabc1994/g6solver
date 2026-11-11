import asyncio
from types import SimpleNamespace

from fastmcp import Client
from mcp.types import Implementation

from g6.core import cloud_consent as cloud, registry
from g6.mcp import build_server


def test_installed_extension_names_are_not_public_program_text(tmp_path, monkeypatch, registry_reset):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(registry.metadata, 'entry_points', lambda **kwargs: [
        SimpleNamespace(name='PRIVATE_CUSTOMER_PROJECT_EXTENSION', value='fake_extension')])

    async def run():
        async with Client(build_server(), client_info=Implementation(name='codex', version='review')) as client:
            return await client.call_tool('g6_components', {})

    cloud.revoke('component_catalogue', 'openai:codex')
    reply = asyncio.run(run())
    assert cloud.grants() == []
    assert 'PRIVATE_CUSTOMER_PROJECT_EXTENSION' not in str(reply), 'installed extension inventory was declared operational'
