import asyncio
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from g6.core import cloud_consent as cloud, llm
from g6.mcp.cloud_consent import request_grant


@pytest.mark.parametrize('length,valid', [(0, False), (1, True), (200, True), (201, False)])
def test_source_id_edges(length, valid):
    args = ('a' * length, cloud.sha256('item'), cloud.sha256('text'))
    if valid:
        assert cloud.Source(*args).recorded
    else:
        with pytest.raises(ValueError):
            cloud.Source(*args)


def test_os_lock_orders_a_different_python_process(tmp_path, monkeypatch):
    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    class Dialog:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': 'Review person'})
    context = SimpleNamespace(session=Dialog(), request_id='grant')
    assert asyncio.run(request_grant(context, 'document', 'openai:codex'))['status'] == 'granted'
    ready = tmp_path / 'ready'
    code = '''
import sys
from pathlib import Path
from g6.core import cloud_consent as c
with c.admission('openai:codex', (c.Source.text('document', 'item', 'synthetic'),), c.sha256('synthetic')):
    Path(sys.argv[1]).write_text('ready', encoding='ascii')
    sys.stdin.readline()
'''
    revoking, revoked = threading.Event(), threading.Event()
    errors = []
    def revoke():
        try:
            revoking.set()
            cloud.revoke('document', 'openai:codex')
            revoked.set()
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=revoke)
    child = subprocess.Popen([sys.executable, '-c', code, str(ready)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ))
    try:
        deadline = time.monotonic() + 6
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.exists(), 'synthetic child did not enter admission'
        worker.start()
        assert revoking.wait(3)
        assert not revoked.wait(.05)
    finally:
        try:
            output, error = child.communicate('release\n', timeout=6)
        except subprocess.TimeoutExpired:
            child.kill()
            output, error = child.communicate(timeout=6)
        if worker.ident is not None:
            worker.join(12)
    assert child.returncode == 0, error
    assert not worker.is_alive() and not errors and revoked.is_set()
    ref = cloud.Source.text('document', 'item', 'synthetic')
    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        with cloud.admission('openai:codex', (ref,), cloud.sha256('synthetic')):
            pytest.fail('post-revoke admission')


def test_agent_and_tool_declarations_match_actual_inventory():
    from tests.core.test_producer_declarations import test_code_derived_agent_inventory, test_registry_covers_registered_tools
    test_code_derived_agent_inventory()
    test_registry_covers_registered_tools()
