"""Code-derived reply paths, compared losslessly with frozen 68ca0eb functions."""
import ast
import copy
import inspect
import json
from pathlib import Path
from types import FunctionType, SimpleNamespace
from uuid import UUID

import pytest

from g6.core import component_process
from g6.harness import patch_report, patch_review, standard_publication
from g6.mcp import behaviour, review_reply
from tests.repair_question5 import renderer_68ca0eb
from tests.repair_question5.test_rendering_differential import strings
from tests.repair_question6 import review_entry_68ca0eb, reviewed_68ca0eb, tool_68ca0eb, transport_68ca0eb


TEXTS = ['caf\u00e9 \u20ac # < > &', 'unknown\nfield',
         'a\r\n b\t c\x00 d\x7f e\x85 f\u2028 g\u2029 h\f i\v',
         'Requirement: Scope: Rule: G6 observed: Answer:', '  a   b  ']


def frozen(function, module):
    bound = FunctionType(function.__code__, vars(module), function.__name__, function.__defaults__,
                         function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


def same(actual, expected):
    assert dict(strings(actual)) == dict(strings(expected))


def return_paths(source, only=None):
    found = {}
    def walk(node, parents=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parents = (*parents, node.name)
        if isinstance(node, ast.Return) and (only is None or any(p in only for p in parents)):
            key = '.'.join(parents)
            found.setdefault(key, []).append(ast.unparse(node.value) if node.value is not None else 'None')
        if parents and (only is None or any(p in only for p in parents)):
            key = '.'.join(parents) + '/branches'
            if isinstance(node, (ast.If, ast.IfExp)):
                found.setdefault(key, []).append('if ' + ast.unparse(node.test))
            elif isinstance(node, ast.ExceptHandler):
                found.setdefault(key, []).append('except ' + (ast.unparse(node.type) if node.type else '*'))
            elif isinstance(node, ast.Raise):
                found.setdefault(key, []).append('raise ' + (ast.unparse(node.exc) if node.exc else '*'))
        for child in ast.iter_child_nodes(node):
            walk(child, parents)
    walk(ast.parse(source))
    return found


def assert_inventory(source, expected, only=None):
    assert return_paths(source, only) == expected, 'reply branch missing from named inventory'


def test_every_return_path_is_named_and_an_unnamed_branch_fails():
    root = Path(__file__).resolve().parents[2]
    inventory = json.loads(Path(__file__).with_name('reply_paths.json').read_text())
    for path, expected in inventory.items():
        only = ({'reviewed', 'bound'} if path.endswith('/behaviour.py') else
                {'g6_review_patch'} if path.endswith('/__init__.py') else None)
        source = (root / path).read_text(encoding='utf-8')
        assert_inventory(source, expected, only)
        # Plant an extra branch into an existing reply producer; counts cannot hide it.
        function = next(iter(expected)).split('/')[0].split('.')[-1]
        for planted in ('if planted:\n    return {"error": "unlisted"}',
                        'if planted:\n    reply = {"error": "unlisted"}'):
            tree = ast.parse(source)
            node = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and n.name == function)
            node.body.insert(0, ast.parse(planted).body[0])
            with pytest.raises(AssertionError, match='missing from named inventory'):
                assert_inventory(ast.unparse(tree), expected, only)


# All admission families in _inputs, plus the confirmed-standard validation before it.
INVALID = {
    'task': {'task': ''}, 'claim': {'claim': None}, 'tests': {'tests': [1]},
    'sources': {'sources': {'x': 1}}, 'timeout': {'timeout_s': True},
    'requirements-type': {'requirements': None},
    'g6-owned-field': {'requirements': [{'id': 'R', 'text': 'x', 'checks': [], 'provenance': 'x'}]},
    'requirement-schema': {'requirements': [{'id': 'R'}]},
    'duplicate-id': {'requirements': [{'id': 'R', 'text': 'x', 'checks': []}] * 2},
    'intent': {'intent': {'PLACEHOLDER': 'x'}},
    'proposed': {'proposed_checks': [{'id': 'PLACEHOLDER'}]},
    'search': {'search': {'PLACEHOLDER': True}},
    'template': {'template_instances': [{'id': 'PLACEHOLDER'}]},
    'confirmed': {'confirmed': 'PLACEHOLDER'},
}


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('name, extra', INVALID.items(), ids=INVALID)
def test_every_input_refusal_keeps_historical_text(monkeypatch, text, name, extra):
    # Unknown keys carry the exact text, including line breaks, through actual validators.
    extra = json.loads(json.dumps(extra).replace('PLACEHOLDER', json.dumps(text)[1:-1]))
    args = {'repo': 'unused', 'task': 'ordinary task', 'requirements': [], **extra}
    same(patch_review.review_patch(**args), frozen(review_entry_68ca0eb.review_patch, patch_review)(**args))


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('branch', ['stale', 'refused', 'isolation', 'help', 'oserror', 'valueerror', 'typeerror',
                                   'placements', 'assessment'])
def test_every_review_return_keeps_historical_text(monkeypatch, text, branch):
    monkeypatch.setattr(patch_review, '_inputs', lambda *a, **k: ([], [], [], [], [], None))
    monkeypatch.setattr(patch_review.repo_tests, 'checked_repo', lambda root: Path('ordinary'))
    monkeypatch.setattr(patch_review, '_import_roots', lambda *a: ())
    monkeypatch.setattr(patch_review.repo_tests, 'snapshot', lambda *a: {})
    monkeypatch.setattr(patch_review.repo_tests, 'change', lambda *a: SimpleNamespace())
    args = dict(repo='unused', task='ordinary task', requirements=[])
    if branch == 'stale':
        args['expected_artifact_digest'] = 'different'
    elif branch == 'assessment':
        monkeypatch.setattr(patch_review, '_assess', lambda *a, **k: {'text': text})
    elif branch == 'placements':
        from g6.harness import verification_search
        monkeypatch.setattr(patch_review, '_inputs', lambda *a, **k: ([], [], [], [1], [], None))
        def fail(*a, **k):
            raise ValueError(text)
        monkeypatch.setattr(verification_search, 'placements', fail)
    else:
        if branch in ('refused', 'isolation', 'help'):
            exc = patch_review.repo_tests.RepoRefused(
                patch_review.ISOLATION_UNAVAILABLE if branch == 'isolation' else 'G6_E_INPUT_VALIDATION', text)
            if branch == 'help':
                exc.help = text
        else:
            exc = {'oserror': OSError, 'valueerror': ValueError, 'typeerror': TypeError}[branch](text)
        def fail(*a, **k):
            raise exc
        monkeypatch.setattr(patch_review.repo_tests, 'change', fail)
    same(patch_review.review_patch(**args), frozen(review_entry_68ca0eb.review_patch, patch_review)(**args))


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('branch', ['unreadable', 'superseded', 'normal', 'unbound'])
def test_queued_review_branches_keep_historical_text(monkeypatch, text, branch):
    monkeypatch.setattr(behaviour, 'identity_of', lambda *a: None)
    def enforced(*a):
        if branch == 'unreadable':
            raise behaviour.qs.StoreUnreadable(Path('store.json'), text)
        if branch == 'superseded':
            exc = standard_publication.StandardSuperseded()
            exc.args = (text,)
            raise exc
        return None
    monkeypatch.setattr(behaviour, 'enforced', enforced)
    def review(*a, **k):
        return {'assessment': {'report': text}} if branch == 'normal' else {'error': text}
    same(behaviour.reviewed(review, 'unused', 'task'),
         frozen(reviewed_68ca0eb.reviewed, behaviour)(review, 'unused', 'task'))


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('branch', ['stopped', 'timeout', 'containment', 'normal'])
def test_job_adapter_error_and_normal_returns_keep_historical_text(monkeypatch, text, branch):
    def review(*a, **k):
        if branch == 'normal':
            return {'assessment': {'report': text}}
        if branch == 'containment':
            raise component_process.ContainmentFailure(text)
        raise component_process.ExecutionInterrupted(text, stopped=branch == 'stopped')
    monkeypatch.setattr(patch_review, 'review_patch', review)
    same(review_reply.review('unused'), transport_68ca0eb.review('unused'))


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('saved', [None, 'ordinary.json'])
def test_compact_refusal_report_and_shortening_branches(text, saved):
    for reply in ({'error': text}, {'assessment': {'report': text, 'outcome': 'insufficient_evidence',
                 'reasons': [{'stage': 'integrity', 'detail': text}, {'stage': 'evidence', 'detail': text}],
                 'next_actions': [text * 2000] * 20, 'unknowns': [text]}, 'evidence': [],
                 'warning_card': {'next_step': text}}):
        same(review_reply.compact(copy.deepcopy(reply), 'job', saved),
             transport_68ca0eb.compact(copy.deepcopy(reply), 'job', saved))


@pytest.mark.parametrize('text', TEXTS)
def test_report_error_return_and_all_outcome_renderers(tmp_path, monkeypatch, text):
    from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, stated
    root = repo(tmp_path)
    stated(monkeypatch)
    monkeypatch.setattr(patch_review, 'uuid', SimpleNamespace(uuid4=lambda: UUID(int=1)))
    reply = patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS)
    assessment = reply['assessment']
    for outcome in patch_report.OUTCOMES:
        one = copy.deepcopy(assessment)
        one.update(outcome=outcome, summary=text, unknowns=[text], next_actions=[text])
        same(patch_report.safe_render(one), renderer_68ca0eb.safe_render(one))
    monkeypatch.setattr(patch_report, 'render', lambda _: (_ for _ in ()).throw(ValueError(text)))
    monkeypatch.setattr(renderer_68ca0eb, 'render', patch_report.render)
    same(patch_report.safe_render(assessment), renderer_68ca0eb.safe_render(assessment))


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('branch', ['denied', 'unreadable', 'queued'])
def test_mcp_tool_return_paths_keep_historical_text(monkeypatch, text, branch):
    import g6.mcp as mcp
    tree = ast.parse(Path(mcp.__file__).read_text(encoding='utf-8'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'g6_review_patch')
    node.decorator_list = []
    namespace = {**vars(mcp), '_allowed': lambda *a: branch != 'denied', 'allowed_sensitive': lambda: (),
                 '_DENIED': '[G6_E_SENSITIVE_DENIED]',
                 'jobs': SimpleNamespace(start=lambda run, **k: run())}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<current MCP tool>', 'exec',
                 flags=__import__('__future__').annotations.compiler_flag), namespace)
    def enforced(*a):
        if branch == 'unreadable':
            raise behaviour.qs.StoreUnreadable(Path('store.json'), text)
        return None
    monkeypatch.setattr(behaviour, 'enforced', enforced)
    monkeypatch.setattr(behaviour, 'reviewed', lambda *a, **k: {'assessment': {'report': text}})
    expected = FunctionType(tool_68ca0eb.g6_review_patch.__code__, namespace,
                            argdefs=tool_68ca0eb.g6_review_patch.__defaults__)
    same(namespace['g6_review_patch']('unused', 'task'), expected('unused', 'task'))


@pytest.mark.parametrize('text', TEXTS)
@pytest.mark.parametrize('branch', ['saved', 'save-error', 'refusal', 'non-dict'])
def test_finish_and_save_return_paths(tmp_path, monkeypatch, text, branch):
    from g6.core import storage
    reply = {'assessment': {'report': text, 'outcome': 'insufficient_evidence'}, 'evidence': []}
    if branch == 'non-dict':
        reply = text
    elif branch == 'refusal':
        reply = {'error': text}
    target = tmp_path / 'reply.json'
    monkeypatch.setattr(storage, 'state_path', lambda *a: target)
    if branch == 'save-error':
        def fail(*a, **k):
            raise OSError(text)
        monkeypatch.setattr(review_reply.os, 'open', fail)
    actual = review_reply.finish(copy.deepcopy(reply), 'job')
    data = target.read_bytes() if target.exists() else None
    if target.exists():
        target.unlink()
    expected = transport_68ca0eb.finish(copy.deepcopy(reply), 'job')
    same(actual, expected)
    if data is not None:
        assert target.read_bytes() == data


@pytest.mark.parametrize('branch', ['too-many', 'collision'])
def test_combined_check_validation_returns_keep_historical_text(monkeypatch, branch):
    from g6.harness import verification_search, verification_templates
    one = SimpleNamespace(id='same')
    monkeypatch.setattr(verification_search, 'proposed_checks', lambda *a: [one])
    monkeypatch.setattr(verification_templates, 'template_instances',
                        lambda *a: [one] * (21 if branch == 'too-many' else 1))
    args = dict(repo='unused', task='ordinary', requirements=[], proposed_checks=[{}], template_instances=[{}])
    same(patch_review.review_patch(**args), frozen(review_entry_68ca0eb.review_patch, patch_review)(**args))


@pytest.mark.parametrize('text', TEXTS)
def test_full_reply_and_report_each_outcome_keep_every_text_leaf(tmp_path, monkeypatch, text):
    from tests.repair_question2.helpers import REQUIREMENTS, TASK, repo, stated
    root = repo(tmp_path)
    stated(monkeypatch)
    captured = []
    original = patch_review._reply
    def capture(*a, **k):
        bound = inspect.signature(original).bind(*a, **k)
        bound.apply_defaults()
        captured.append(bound)
        return original(*a, **k)
    monkeypatch.setattr(patch_review, '_reply', capture)
    monkeypatch.setattr(patch_review, 'uuid', SimpleNamespace(uuid4=lambda: UUID(int=1)))
    patch_review.review_patch(root, task=TASK, requirements=REQUIREMENTS)
    # The report states the seconds since the review started, to a tenth: both renders read one fixed instant,
    # as they share one request-ID source. The seconds text stays compared.
    instant = captured[-1].arguments['started'] + 1.0
    monkeypatch.setattr(patch_review, 'time', SimpleNamespace(monotonic=lambda: instant))
    for reason in (None, ('integrity', 'test_weakened'), ('tier_1', 'check_failed'),
                   ('evidence', 'requirement_not_established')):
        bound = copy.deepcopy(captured[-1])
        bound.arguments.update(reasons=[] if reason is None else [
            {'stage': reason[0], 'code': reason[1], 'detail': text}], unknowns=[text],
            follow_ups=[text], first_action=text)
        for req in bound.arguments['evaluated']:
            req.update(text=text, reason=text, suggested_test=text)
        actual = original(*bound.args, **bound.kwargs)
        with monkeypatch.context() as old:
            old.setattr(patch_report, 'render', renderer_68ca0eb.render)
            expected = frozen(review_entry_68ca0eb._reply, patch_review)(*bound.args, **bound.kwargs)
        same(actual, expected)
