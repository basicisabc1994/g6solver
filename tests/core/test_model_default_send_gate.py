"""Send-time opt-out and request lifetime; all provider traffic is synthetic."""
import sys
from contextvars import copy_context
from threading import Event

import pytest

from g6.core import cloud_consent as cloud, consent_callers, host_models, llm
from g6.core.consent_declarations import contract
from tests.host_stand_in import stand_in_hosts

#: The reviewed bodies are `ast.dump` text as Python 3.12 writes it; from 3.13 the dump leaves out empty fields unless
#: asked to show them (as tests/s16r1/test_source_test_inventory.py asks), so 3.13 asks.
_DUMP = {'show_empty': True} if sys.version_info >= (3, 13) else {}


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
        imports, bindings = {}, {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if node.level:
                    package = ['g6', *relative.split('/')[:-1]]
                    module = '.'.join([*package[:len(package) - node.level + 1], module]).rstrip('.')
                for alias in node.names:
                    imports.setdefault(alias.asname or alias.name, set()).add(module + '.' + alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split('.')[0]
                    imports.setdefault(name, set()).add(alias.name if alias.asname else name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, (ast.Name, ast.Attribute, ast.Call)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, (ast.Name, ast.Attribute)):
                        bindings.setdefault(ast.unparse(target), []).append(node.value)

        def symbols(node, seen=frozenset()):
            # Union all possible bindings. An unrelated scope/shadowing assignment
            # must never erase a primitive; over-discovery requires human review.
            name = ast.unparse(node)
            if isinstance(node, ast.Name):
                found = {name} | imports.get(name, set())
            elif isinstance(node, ast.Attribute):
                found = {s + '.' + node.attr for s in symbols(node.value, seen)}
            elif isinstance(node, (ast.BoolOp, ast.IfExp)):
                return set().union(*(symbols(n, seen) for n in ast.iter_child_nodes(node)))
            elif isinstance(node, ast.Call):
                return {s + '()' for s in symbols(node.func, seen)}
            else:
                return set()
            if name not in seen:
                for value in bindings.get(name, ()):
                    found.update(symbols(value, seen | {name}))
            return found

        def primitive(target):
            native = relative == 'core/host_models.py' and target == '_run'
            sdk = (target.endswith(('.create', '.stream')) and
                   any(part.lstrip('_') in ('messages', 'completions', 'responses')
                       for part in target.split('.')))
            http = (target.endswith(('.post', '.post_json', '.request', '.urlopen')) or target == 'opener.open' or
                    target.endswith('.open') and 'urllib.request.build_opener()' in target or
                    target in ('_post_json', 'g6.core.llm._post_json'))
            # Computed programs are included even for non-model tools: every
            # launcher needs a reviewed disposition, never an owner-name filter.
            process = (target in ('g6.core.component_process.run_process', 'g6.core.component_process.run_contained') or
                       target == 'run_process' and relative == 'core/component_process.py' or
                       target.startswith('subprocess.') and target.rpartition('.')[2] in
                       ('Popen', 'run', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput') or
                       target.startswith('os.') and target.rpartition('.')[2].startswith(
                           ('spawn', 'exec', 'posix_spawn', 'system', 'popen')))
            return native or sdk or http or process or target == 'g6.core.host_models._run'

        def walk(node, owner=''):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = (owner + '.' if owner else '') + node.name
            if isinstance(node, ast.Call):
                if any(primitive(target) for target in symbols(node.func)):
                    found.setdefault((relative, owner), []).append(node.lineno)
            for child in ast.iter_child_nodes(node):
                walk(child, owner)

        walk(tree)
    return found


def test_every_physical_model_send_reaches_the_one_gate():
    import ast
    import hashlib
    from pathlib import Path

    from tests.core.model_send_dispositions import REVIEWED_TRANSPORT_BODIES

    root = Path(__file__).resolve().parents[2] / 'src/g6'
    sources = {p.relative_to(root).as_posix(): p.read_text(encoding='utf-8') for p in root.rglob('*.py')}
    routes = {
        ('core/llm.py', '_post_json'): '_check_model_send',
        ('core/llm.py', 'OllamaCaller._chat'): '_start_model_call',
        ('core/llm.py', 'OpenAICompatibleCaller._chat'): '_start_model_call',
        ('core/llm.py', 'AnthropicCaller._create'): '_check_model_send',
        ('core/host_models.py', '_HostCaller.complete'): '_check_model_send',
        ('self_maintenance/usage.py', '_complete_host'): '_note_launch',
        ('self_maintenance/usage.py', 'WatchedClaude._launch'): '_note_launch',
        ('harness/completion_loop.py', 'ToolSession._launch'): '_start_model_call',
        ('harness/verification_proposer.py', '_RecordedMessages.create'): 'sdk_send',
        ('agents/_runtime/cli.py', 'CliAgentRunner.run'): 'process_admission',
    }
    sites = send_sites(sources)
    # These are explicit dispositions, not discovery filters. A computed program
    # is visible even where review establishes a non-model or forwarding role.
    # Counts also prevent another call in a reviewed owner inheriting its disposition.
    dispositions = {
        ('core/host_models.py', '_run'): (1, 'forwarding: all host _run callers are gated routes above'),
        ('core/component_process.py', 'run_contained'): (1, 'generic confined transport; callers require review'),
        ('core/outside.py', '_run'): (1, 'version/capability probe via confined transport, no model prompt'),
        ('core/outside.py', '_service'): (1, 'GET service health probe'),
        ('core/outside.py', 'call_service'): (1, 'generic local service HTTP; component consent, not default LLM'),
        ('core/http.py', 'Client.get'): (1, 'GET helper, no model prompt'),
        ('core/http.py', 'stdlib_transport'): (1, 'generic HTTP forwarding; callers require review'),
        ('core/http.py', 'stdlib_stream_transport'): (1, 'generic HTTP streaming forwarding; callers require review'),
        ('core/component_certificate.py', 'signed_review'): (1, 'isolated local certificate evaluator process'),
        ('core/sandbox.py', 'run_script'): (1, 'generic confined script runner, not model selection'),
        ('core/process_tree.py', '_Tree.__init__'): (2, 'generic owned-tree spawn; callers require review'),
        ('core/confine.py', '_Bubblewrap.start'): (1, 'generic Linux containment spawn, not model selection'),
        ('core/confine.py', '_Bubblewrap._features'): (1, 'bubblewrap --help capability inspection'),
        ('core/confine.py', '_Bubblewrap._probe'): (1, 'bubblewrap containment self-test'),
        ('cli/setup.py', 'register_clients'): (1, 'claude/codex mcp add registration, no completion prompt'),
        ('self_maintenance/usage.py', '_ran'): (1, 'allowance/status observation protocol, no model prompt'),
        ('_allowance/statusline.py', 'observe_in_worker.hand_over'): (1, 'local Python allowance observer'),
        ('_allowance/statusline.py', 'run_wrapper'): (1, 'person-configured status renderer, no model request'),
        ('algebra/runner.py', '_execute'): (1, 'local algebra tool command'),
        ('cegis/runner.py', 'SubprocessRunner._call'): (1, 'local synthesis/checker command'),
        ('lean/runner.py', '_execute'): (1, 'local Lean compiler command'),
        ('model_checking/tlc.py', '_execute'): (1, 'local Java TLC model checker'),
        ('prolog/runner.py', '_execute'): (1, 'local Prolog interpreter command'),
        ('prolog/ops.py', '_run_driver'): (1, 'Prolog driver request serialization, not HTTP'),
        ('media/_programs.py', 'run'): (1, 'local allowlisted media utility'),
        ('media/adapt_diagrams/dot.py', 'run_dot'): (1, 'local Graphviz renderer'),
        ('media/adapt_comfyui/client.py', 'Comfy.submit'): (1, 'explicit image workflow; separate media consent, not LLM'),
        ('media/adapt_image/paid.py', '_Paid._post'): (1, 'explicit paid image API; separate media consent, not LLM'),
        ('media/adapt_voice/engines.py', 'OpenAIVoice.speak'): (1, 'explicit speech synthesis; separate media consent'),
        ('media/adapt_voice/engines.py', 'OpenAIVoice.transcribe'): (1, 'explicit transcription; separate media consent'),
        ('media/adapt_voice/engines.py', 'ElevenLabsVoice.speak'): (1, 'explicit speech synthesis; separate media consent'),
        ('knowledge/knowledge_converter/converters.py', '_read_pdf'): (1, 'local PDF text extractor'),
        ('sources/ctx_vision/ocr.py', 'read'): (1, 'local Tesseract OCR command'),
        ('sources/ctx_git/runner.py', '_runner.run'): (1, 'local Git source query'),
        ('sources/ctx_geo/backends.py', 'GoogleBackend._send'): (1, 'explicit maps API with its component consent'),
        ('core/git_env.py', 'safe_git.ask'): (1, 'local safe Git query'),
        ('csf/rollback.py', 'GitCheckpoint._git'): (1, 'local Git checkpoint operation'),
        ('core/promotion_custody.py', '_call_service'): (1, 'local isolated evaluator service'),
        ('mcp/improve.py', '_spawn'): (1, 'local metaharness Python loop; model calls gated in child'),
        ('_agentic/_child.py', 'launch'): (1, 'agentic kernel confined Python child: no network, key or sign-in; the '
                                              'parent asks the model through its gated caller'),
        ('mcp/improve.py', '_run_act'): (1, 'local confirmed action runner; not a model client'),
        ('mcp/mission.py', 'register'): (1, 'local Git setup operation'),
        ('workforce/repo_tests.py', '_bounded'): (1, 'local repository test command'),
        ('workforce/truth.py', '_run_owned'): (1, 'confined repository truth/test command'),
        ('workforce/coding_learning.py', '_ask'): (1, 'local workforce decision record, not HTTP'),
        ('workforce/decisions.py', 'Decisions.reask'): (1, 'local workforce decision record, not HTTP'),
        ('workforce/runtime.py', 'Runtime._step'): (2, 'local workforce decision records, not HTTP'),
        ('workforce/repo_tests.py', '_git'): (2, 'local Git driver queries'),
        ('workforce/verify.py', 'changed_files._git'): (1, 'local Git diff query'),
        ('workforce/verify.py', 'head_snapshot'): (1, 'local Git archive snapshot'),
    }
    assert sites.keys() == (routes | dispositions).keys(), (
        'transport needs a reviewed gate route or disposition (including computed programs)',
        sites.keys() - (routes | dispositions).keys(), (routes | dispositions).keys() - sites.keys())
    def definitions(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                yield node
            else:
                yield from definitions(ast.iter_child_nodes(node))

    def definition(relative, owner):
        nodes = ast.parse(sources[relative]).body
        for name in owner.split('.'):
            fn = next(n for n in definitions(nodes) if n.name == name)
            nodes = fn.body
        return fn

    assert dispositions.keys() == REVIEWED_TRANSPORT_BODIES.keys(), 'unbound reviewed dispositions'
    for key, (count, reason) in dispositions.items():
        assert reason and len(sites[key]) == count, (key, 'changed transport needs reviewed disposition', sites[key])
        fingerprint = hashlib.sha256(ast.dump(definition(*key), **_DUMP).encode()).hexdigest()
        assert fingerprint == REVIEWED_TRANSPORT_BODIES[key], (key, 'changed body needs reviewed disposition')
    # A route is credited only when its body reaches the gate, including shared helpers.
    helpers = {
        ('self_maintenance/usage.py', '_note_launch'): '_start_model_call',
        ('core/llm.py', '_start_model_call'): '_check_model_send',
        ('core/consent_callers.py', 'sdk_send'): '_check_model_send',
        ('agents/_runtime/consent.py', 'process_admission'): '_check_model_send',
    }
    for (relative, owner), gate in (routes | helpers).items():
        fn = definition(relative, owner)
        calls = {ast.unparse(n.func).rpartition('.')[2] for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert gate in calls, (relative, owner, 'bypasses send-time gate', gate)
        if (relative, owner) in sites:
            assert not unguarded_sends(fn, gate, sites[relative, owner]), (relative, owner, 'conditional gate bypass')
        else:
            # Helpers must dominate their continuation too, not merely contain
            # the gate's name. local=True in process_admission is explicitly a
            # non-model inspection; specialize only that documented caller term.
            import copy

            checked_fn = copy.deepcopy(fn)
            if owner == 'process_admission':
                class ModelAdmission(ast.NodeTransformer):
                    def visit_If(self, node):
                        if ast.unparse(node.test) == 'not local':
                            return [self.visit(n) for n in node.body]
                        return self.generic_visit(node)

                checked_fn = ModelAdmission().visit(checked_fn)
            yields = [n.lineno for n in ast.walk(checked_fn) if isinstance(n, ast.Yield)]
            if not yields:
                end = fn.end_lineno + 1
                checked_fn.body.append(ast.Expr(value=ast.Call(
                    func=ast.Name(id='_gate_exit'), args=[], keywords=[], lineno=end), lineno=end))
                yields = [end]
            assert not unguarded_sends(checked_fn, gate, yields), (relative, owner, 'conditional helper gate bypass')


def unguarded_sends(fn, gate, sinks):
    """Require the gate on every structural path to a discovered handoff."""
    import ast

    gaps = []

    def expression(node, checked):
        # Expressions may hide short-circuiting, deferred execution or reordered
        # arguments. Inspect every possible sink, but never establish authority here.
        if node is None:
            return checked
        if isinstance(node, (ast.Lambda, ast.GeneratorExp)):
            checked = False  # deferred code cannot borrow its creator's authority
        if isinstance(node, (ast.Call, ast.Yield, ast.YieldFrom)) and node.lineno in sinks and not checked:
            gaps.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            expression(child, checked)
        return checked

    def gate_statement(node, checked):
        expression(node, checked)
        return checked or (isinstance(node, ast.Call) and ast.unparse(node.func).rpartition('.')[2] == gate)

    def block(statements, checked):
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Defaults/decorators and deferred bodies cannot establish this
                # function's gate, but discovered sinks must still be reported.
                expression(node, False)
                continue
            if isinstance(node, ast.If):
                checked = expression(node.test, checked)
                yes, no = block(node.body, checked), block(node.orelse, checked)
                checked = yes and no
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                inside = checked
                for item in node.items:
                    inside = gate_statement(item.context_expr, inside)
                block(node.body, inside)
                # __exit__ may suppress an exception that skipped a body gate.
                # Only authority established before entering the body survives.
                checked = inside
            elif isinstance(node, (ast.For, ast.While)):
                expression(node.iter if isinstance(node, ast.For) else node.test, checked)
                block(node.body, checked)
                block(node.orelse, checked)  # a loop may run zero times
            elif isinstance(node, ast.Try):
                body = block(node.body, checked)
                handlers = [block(h.body, checked) for h in node.handlers]
                otherwise = block(node.orelse, body)
                # finally also executes when preparation or the gate raises.
                # Its own sinks must be safe under the incoming authority.
                block(node.finalbody, checked)
                checked = block(node.finalbody, otherwise and all(handlers))
            elif isinstance(node, ast.Expr):
                checked = gate_statement(node.value, checked)
            else:
                expression(node, checked)
        return checked

    block(fn.body, False)
    return sorted(set(gaps))


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
