"""Send-time opt-out and request lifetime; all provider traffic is synthetic."""
from contextvars import copy_context
from threading import Event

import pytest

from g6.core import cloud_consent as cloud, consent_callers, host_models, llm
from g6.core.consent_declarations import contract
from tests.host_stand_in import stand_in_hosts


@pytest.fixture(params=('native', 'watched'))
def hosts(request, monkeypatch, tmp_path):
    import g6.self_maintenance.usage  # noqa: F401  exercise the real construction hook

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('G6_DISABLE_LLM', raising=False)
    sent, filed = [], {}
    stand_in_hosts(monkeypatch, lambda system, user: sent.append(user) or 'answer')
    monkeypatch.setattr(llm.key_file, 'read', lambda: dict(filed))
    if request.param == 'native':
        monkeypatch.setattr(host_models._HostCaller, '__new__',
                            staticmethod(lambda kind, *a, **k: object.__new__(kind)))
    return sent, filed


def request_sources(capture, repo):
    capture.client = ('openai:codex', object())
    capture.client_roots = (repo,)
    capture.client_arguments = {'repo': str(repo), 'task': 'synthetic'}
    return contract('client_request', 'client_repository').sources(capture.client_arguments)


@pytest.mark.parametrize('path', ('default', 'critic', 'direct'))
def test_retained_callers_recheck_opt_out(hosts, tmp_path, path):
    from g6.harness.completion_loop import critic_host

    sent, filed = hosts
    with cloud.collect_sources() as capture:
        refs = request_sources(capture, tmp_path)
        filed['G6_LOOP_CRITIC'] = 'codex'
        caller = (critic_host() if path == 'critic' else
                  host_models.CodexCaller() if path == 'direct' else
                  consent_callers.provider_from_env(env={}))
        filed['G6_LLM_PROVIDER'] = ' NoNe '
        with cloud.request_sources(refs):
            with pytest.raises(llm.LLMError, match='G6_E_NO_MODEL'):
                caller.complete('system', 'synthetic')
    assert sent == []
    assert cloud.disclosures() == []


def test_unowned_nested_copy_expires_but_grant_still_works(hosts, tmp_path, accepted_cloud_dialog):
    sent, _ = hosts
    with cloud.collect_sources() as capture:
        refs = request_sources(capture, tmp_path)
        caller = consent_callers.provider_from_env(env={})
        with cloud.collect_sources():
            copied = copy_context()

    def send():
        with cloud.request_sources(refs):
            return caller.complete('system', 'synthetic')

    with pytest.raises(llm.LLMError, match=cloud.REQUIRED):
        copied.run(send)
    assert sent == []
    for ref in refs:
        accepted_cloud_dialog(ref.source_id, 'openai:codex', text='synthetic')
    assert copied.run(send) == 'answer'
    assert sent == ['synthetic']
    assert {s['basis'] for row in cloud.disclosures() for s in row['sources']} == {'grant'}


@pytest.mark.parametrize('opt_out', (False, True))
def test_owned_job_snapshot_survives_request_but_not_opt_out(hosts, tmp_path, opt_out):
    from g6.metaharness.jobs import Jobs

    sent, filed = hosts
    release, jobs = Event(), Jobs()
    try:
        with cloud.collect_sources() as capture:
            refs = request_sources(capture, tmp_path)
            caller = consent_callers.provider_from_env(env={})

            def operation():
                assert release.wait(5)
                with cloud.request_sources(refs):
                    value = caller.complete('system', 'synthetic')
                    return cloud.bind_output({'answer': value}, refs)

            pending = jobs.start(operation)
        if opt_out:
            filed['G6_LLM_PROVIDER'] = 'none'
        release.set()
        jobs.close()
        result = jobs.status(pending['job_id'])
        assert result['status'] == ('failed' if opt_out else 'completed')
        assert sent == ([] if opt_out else ['synthetic'])
    finally:
        release.set()
        jobs.close()


def test_http_resend_rechecks_opt_out(monkeypatch):
    import io
    import urllib.error

    sent, filed = [], {}

    class Opener:
        def open(self, request, timeout):
            sent.append(request.data)
            raise urllib.error.HTTPError(request.full_url, 503, 'busy', {'Retry-After': '0'}, io.BytesIO(b'busy'))

    monkeypatch.setattr(llm.key_file, 'read', lambda: dict(filed))
    monkeypatch.setattr(llm.urllib.request, 'build_opener', lambda *a: Opener())
    monkeypatch.setattr(llm, '_sleep', lambda seconds: filed.update(G6_LLM_PROVIDER='none'))
    with pytest.raises(llm.LLMError, match='G6_E_NO_MODEL'):
        llm.OllamaCaller().complete('system', 'synthetic')
    assert len(sent) == 1


def send_sites(sources):
    """Discover physical model handoffs, not a list of approved caller constructors."""
    import ast

    found = {}
    for relative, source in sources.items():
        tree = ast.parse(source)
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases.update({a.asname or a.name: (node.module or '') + '.' + a.name for a in node.names})
            elif isinstance(node, ast.Import):
                aliases.update({a.asname or a.name: a.name for a in node.names})

        def symbol(node):
            if isinstance(node, ast.Name):
                return aliases.get(node.id, node.id)
            return symbol(node.value) + '.' + node.attr if isinstance(node, ast.Attribute) else ''

        assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
        for _ in range(len(assignments) + 1):
            previous = dict(aliases)
            for node in assignments:
                if isinstance(node.value, (ast.Name, ast.Attribute)):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and not any(
                                isinstance(n, ast.Name) and n.id == target.id for n in ast.walk(node.value)):
                            aliases[target.id] = symbol(node.value)
            if aliases == previous:
                break

        def walk(node, owner=''):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = (owner + '.' if owner else '') + node.name
            if isinstance(node, ast.Call):
                target = symbol(node.func)
                native = relative == 'core/host_models.py' and target == '_run'
                sdk = target.endswith('.create') and 'messages' in target
                http = relative == 'core/llm.py' and target == 'opener.open'
                agent = target == 'g6.core.component_process.run_process' and owner == 'CliAgentRunner.run'
                if native or sdk or http or agent or target == 'g6.core.host_models._run':
                    found.setdefault((relative, owner), []).append(node.lineno)
            for child in ast.iter_child_nodes(node):
                walk(child, owner)

        walk(tree)
    return found


def test_every_physical_model_send_reaches_the_one_gate():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / 'src/g6'
    sources = {p.relative_to(root).as_posix(): p.read_text(encoding='utf-8') for p in root.rglob('*.py')}
    routes = {
        ('core/llm.py', '_post_json'): '_check_model_send',
        ('core/llm.py', 'AnthropicCaller._create'): '_check_model_send',
        ('core/host_models.py', '_HostCaller.complete'): '_check_model_send',
        ('self_maintenance/usage.py', '_complete_host'): '_note_launch',
        ('self_maintenance/usage.py', 'WatchedClaude._launch'): '_note_launch',
        ('harness/completion_loop.py', 'ToolSession._launch'): '_start_model_call',
        ('harness/verification_proposer.py', '_RecordedMessages.create'): 'sdk_send',
        ('agents/_runtime/cli.py', 'CliAgentRunner.run'): 'process_admission',
    }
    sites = send_sites(sources)
    assert sites.keys() == routes.keys(), 'new model transport needs a reviewed gate route'
    # A route is credited only when its body reaches the gate, including shared helpers.
    helpers = {
        ('self_maintenance/usage.py', '_note_launch'): '_start_model_call',
        ('core/llm.py', '_start_model_call'): '_check_model_send',
        ('core/consent_callers.py', 'sdk_send'): '_check_model_send',
        ('agents/_runtime/consent.py', 'process_admission'): '_check_model_send',
    }
    for (relative, owner), gate in (routes | helpers).items():
        nodes = ast.parse(sources[relative]).body
        for name in owner.split('.'):
            fn = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name)
            nodes = fn.body
        calls = {ast.unparse(n.func).rpartition('.')[2] for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert gate in calls, (relative, owner, 'bypasses send-time gate', gate)
        if (relative, owner) in sites:
            assert not unguarded_sends(fn, gate, sites[relative, owner]), (relative, owner, 'conditional gate bypass')


def unguarded_sends(fn, gate, sinks):
    """Require the gate on every structural path to a discovered handoff."""
    import ast

    gaps = []

    def expression(node, checked):
        if node is None:
            return checked
        if isinstance(node, ast.Call):
            # Arguments and preparation hooks run before the call they belong to.
            for arg in (*node.args, *(k.value for k in node.keywords)):
                checked = expression(arg, checked)
            if node.lineno in sinks and not checked:
                gaps.append(node.lineno)
            return checked or ast.unparse(node.func).rpartition('.')[2] == gate
        for child in ast.iter_child_nodes(node):
            checked = expression(child, checked)
        return checked

    def block(statements, checked):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.If):
                checked = expression(node.test, checked)
                yes, no = block(node.body, checked), block(node.orelse, checked)
                checked = yes and no
            elif isinstance(node, ast.With):
                inside = checked
                for item in node.items:
                    inside = expression(item.context_expr, inside)
                checked = block(node.body, inside)
            elif isinstance(node, (ast.For, ast.While)):
                expression(node.iter if isinstance(node, ast.For) else node.test, checked)
                block(node.body, checked)
                block(node.orelse, checked)  # a loop may run zero times
            elif isinstance(node, ast.Try):
                body = block(node.body, checked)
                handlers = [block(h.body, checked) for h in node.handlers]
                block(node.orelse, body)
                checked = block(node.finalbody, body and all(handlers))
            else:
                checked = expression(node, checked)
        return checked

    block(fn.body, False)
    return gaps


def test_send_census_discovers_new_aliased_host_and_sdk_handoffs():
    found = send_sites({'new.py': (
        'from g6.core.host_models import _run as launch\n'
        'again = launch\n'
        'def bypass(client):\n'
        '    launch([], "", None, None, {}, 1, "fake")\n'
        '    again([], "", None, None, {}, 1, "fake")\n'
        '    client.messages.create(model="fake")\n'
    )})
    assert found == {('new.py', 'bypass'): [4, 5, 6]}


def test_send_census_rejects_gate_only_on_one_branch():
    import ast

    fn = ast.parse('def send(allow):\n'
                   '    if allow:\n        _check_model_send()\n'
                   '    launch()\n').body[0]
    assert unguarded_sends(fn, '_check_model_send', [4]) == [4]
