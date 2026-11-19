"""Synthetic census mutations: no process, SDK or network is invoked."""
import ast

import pytest

from tests.core.test_model_default_send_gate import send_sites, unguarded_sends
from tests.core.test_model_default_send_gate import test_every_physical_model_send_reaches_the_one_gate as check_routes


@pytest.mark.parametrize('imports,call', [
    ('from g6.core.component_process import run_process as launch', 'launch(["codex", "exec", "synthetic"])'),
    ('from g6.core.component_process import run_process as launch', 'launch(computed_program)'),
    ('from g6.core.component_process import run_contained as launch', 'launch(computed_program)'),
    ('from g6.core.component_process import run_process as launch', 'launch(["non-model", "--version"])'),
    ('from .core.component_process import run_process as launch', 'launch(computed_program)'),
    ('from g6.core.host_models import _run as launch', 'launch(computed_program)'),
    ('from g6.core.llm import _post_json as post', 'post(url, payload, {}, 1)'),
    ('import urllib.request as http', 'http.urlopen(request)'),
    ('import requests as http', 'http.post(url, json=payload)'),
    ('import httpx', 'httpx.post(url, json=payload)'),
    ('import httpx', 'httpx.request("POST", url, json=payload)'),
    ('', 'client.request(method, url, body=payload)'),
    ('', 'client.post_json(url, payload)'),
    ('import urllib.request\ntransport = urllib.request.build_opener()', 'transport.open(request)'),
    ('', 'opener.open(request)'),
    ('', 'client.messages.stream(model="synthetic")'),
    ('', 'client.chat.completions.create(model="synthetic")'),
    ('', 'client.responses.stream(model="synthetic")'),
    ('import subprocess as sp', 'sp.run(["codex", "exec", "synthetic"])'),
    ('from subprocess import Popen as launch', 'launch(computed_program)'),
    ('import os', 'os.system("claude -p synthetic")'),
    ('import os', 'os.spawnvp(os.P_WAIT, "codex", ["codex", "exec", "synthetic"])'),
    ('import subprocess', '(spawn or subprocess.Popen)(computed_program)'),
])
def test_new_module_primitive_requires_review_and_gate(imports, call):
    source = f'{imports}\ndef new_send():\n    {call}\n'
    line = imports.count('\n') + 3
    assert send_sites({'new_module.py': source}) == {('new_module.py', 'new_send'): [line]}
    fn = ast.parse(source).body[-1]
    assert unguarded_sends(fn, '_check_model_send', [line]) == [line]


@pytest.mark.parametrize('statement', [
    '_check_model_send() if False else None',
    'False and _check_model_send()',
    'True or _check_model_send()',
    '[_check_model_send() for _ in []]',
    '(_check_model_send() for _ in [])',
    'lambda: _check_model_send()',
    'def nested(value=_check_model_send()):\n    pass',
    'if allowed:\n    _check_model_send()',
    'while allowed:\n    _check_model_send()\n    break',
    'wrapper(_check_model_send())',
    'checked = _check_model_send()',
    'assert _check_model_send()',
])
def test_only_dominating_statement_gates_count(statement):
    source = 'def send(allowed):\n' + ''.join(f'    {line}\n' for line in statement.splitlines())
    source += '    launch()\n'
    fn = ast.parse(source).body[0]
    sink = fn.body[-1].lineno
    assert unguarded_sends(fn, '_check_model_send', [sink]) == [sink]


@pytest.mark.parametrize('statement', [
    '_check_model_send()',
    'with _check_model_send():\n    pass',
    'if allowed:\n    _check_model_send()\nelse:\n    _check_model_send()',
])
def test_unconditional_statement_gate_controls(statement):
    source = 'def send(allowed):\n' + ''.join(f'    {line}\n' for line in statement.splitlines())
    source += '    launch()\n'
    fn = ast.parse(source).body[0]
    assert unguarded_sends(fn, '_check_model_send', [fn.body[-1].lineno]) == []


@pytest.mark.parametrize('expression', [
    'launch() if allowed else None',
    'allowed and launch()',
    'allowed or launch()',
    '[launch() for _ in items]',
    'lambda: launch()',
])
def test_conditional_sinks_are_still_reported(expression):
    fn = ast.parse(f'def send():\n    {expression}\n').body[0]
    assert unguarded_sends(fn, '_check_model_send', [2]) == [2]


def test_deferred_lambda_cannot_borrow_its_creators_gate():
    fn = ast.parse('def send():\n    _check_model_send()\n    return lambda: launch()\n').body[0]
    assert unguarded_sends(fn, '_check_model_send', [3]) == [3]


def test_finally_sink_cannot_assume_a_gate_returned():
    fn = ast.parse('def send():\n    try:\n        _check_model_send()\n'
                   '    finally:\n        launch()\n').body[0]
    assert unguarded_sends(fn, '_check_model_send', [5]) == [5]


def test_suppressing_context_cannot_propagate_a_body_gate():
    fn = ast.parse('def send():\n    with suppress(Exception):\n        prepare()\n'
                   '        _check_model_send()\n    launch()\n').body[0]
    assert unguarded_sends(fn, '_check_model_send', [5]) == [5]


def test_separate_scope_alias_does_not_erase_an_imported_primitive():
    source = ('from g6.core.component_process import run_process as launch\n'
              'def new_send():\n    launch(computed_program)\n'
              'def unrelated():\n    launch = other_function\n    launch()\n')
    assert send_sites({'new.py': source}).get(('new.py', 'new_send')) == [3]


def test_reviewed_nonmodel_disposition_expires_when_program_changes(monkeypatch):
    from pathlib import Path

    original = Path.read_text

    def mutation(path, *args, **kwargs):
        source = original(path, *args, **kwargs)
        if path.as_posix().endswith('/core/git_env.py'):
            before = '[git_program(), "-C", str(workdir), *args]'
            assert source.count(before) == 1
            return source.replace(before, '["codex", "exec", "synthetic"]')
        return source

    monkeypatch.setattr(Path, 'read_text', mutation)
    with pytest.raises(AssertionError, match='reviewed disposition'):
        check_routes()
