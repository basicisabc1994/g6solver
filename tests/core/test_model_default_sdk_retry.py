"""Pin SDK-owned retry settings; bound changes await the issuance batch."""
import ast
import inspect
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('relative', ['core/llm.py', 'harness/verification_proposer.py'])
def test_g6_built_sdk_clients_disable_internal_retries(relative):
    root = Path(__file__).resolve().parents[2] / 'src/g6'
    tree = ast.parse((root / relative).read_text(encoding='utf-8'))
    clients = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
               and ast.unparse(n.func) == 'anthropic.Anthropic']
    assert len(clients) == 1, 'review every SDK client constructor'
    for client in clients:
        options = {k.arg: ast.literal_eval(k.value) for k in client.keywords if k.arg == 'max_retries'}
        assert options.get('max_retries') == 0, 'SDK retries bypass the G6 send-time gate'


def test_sdk_retry_cannot_resend_after_key_file_opt_out(monkeypatch):
    """Real SDK, in-memory HTTP only: changing opt-out cannot permit a retry."""
    anthropic = pytest.importorskip('anthropic')
    httpx = pytest.importorskip('httpx2')
    from g6.core import llm

    constructor = anthropic.Anthropic
    assert inspect.signature(constructor).parameters['max_retries'].default > 0
    sent, filed = [], {}

    def transport(request):
        sent.append(request.content)
        filed['G6_LLM_PROVIDER'] = 'none'
        return httpx.Response(503, headers={'retry-after': '0'}, json={
            'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'synthetic'}})

    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as http:
        def build(**options):
            return constructor(http_client=http, **options)

        monkeypatch.setitem(sys.modules, 'anthropic', SimpleNamespace(Anthropic=build))
        monkeypatch.setattr(llm.key_file, 'read', lambda: dict(filed))
        caller = llm.AnthropicCaller(api_key='synthetic-key', base_url='https://synthetic.invalid', fallbacks=False)
        try:
            with pytest.raises(llm.LLMError):
                caller.complete('system', 'synthetic')
        finally:
            if caller._client is not None:
                caller._client.close()
    assert len(sent) == 1, 'SDK retried internally after the key file opted out'
