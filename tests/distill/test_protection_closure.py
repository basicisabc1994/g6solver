"""Static gate dependencies and unresolved imports close the merge boundary."""
import pytest

from g6.self_training.merge_gate import FileChange


def test_every_gate_closure_and_verdict_consumer_is_protected():
    from g6.distill import protection_gate as closure
    from g6.distill.merge_evidence_gate import protected_set
    sources = closure.package_sources()
    manifest = protected_set()
    gates = [path for path in sources if 'gate' in path.rsplit('/', 1)[-1]]
    for gate in gates:
        result = closure.import_closure(sources, (gate,))
        for path in result.files:
            assert manifest.hits((FileChange(path=path, before='a', after='b'),)) == (path,)
    assert manifest.hits((FileChange(path='src/g6/self_model/simulate.py', before='a', after='b'),))


def test_relative_imports_package_initializers_and_handoffs_are_closed():
    from g6.distill.protection_gate import import_closure
    sources = {'src/g6/sample/rule_gate.py': 'from .supplier import decide\n',
        'src/g6/sample/supplier.py': 'from ..data import flags\n',
        'src/g6/sample/__init__.py': '', 'src/g6/data.py': 'flags = True\n',
        'src/g6/consumer.py': 'from .sample.rule_gate import result\n',
        'src/g6/__init__.py': ''}
    result = import_closure(sources, ('src/g6/sample/rule_gate.py',), consumers=True)
    assert set(result.files) == set(sources)
    assert not result.unresolved


def test_planted_dynamic_supplier_refuses_autonomous_merge(monkeypatch):
    from g6.distill import protection_gate as closure
    from g6.distill.merge_evidence_gate import protected_set
    from tests.self_training.test_research_merge_gate import fixture
    m, gate, host, _, original, measured = fixture()
    sources = {'src/g6/sample/rule_gate.py': 'from .supplier import decide\n',
        'src/g6/sample/supplier.py': 'def decide():\n    return True\n',
        'src/g6/sample/__init__.py': '', 'src/g6/__init__.py': ''}
    monkeypatch.setattr(closure, 'package_sources', lambda: sources)
    path = 'src/g6/sample/new_method.py'
    change = FileChange(path=path, before='a = 1\n', after='a = 2\n')
    assert protected_set().hits((change,)) == ()
    sources['src/g6/sample/supplier.py'] += '\n__import__(chosen_at_runtime)\n'
    gate.manifest = protected_set()
    assert gate.manifest.hits((change,)) == (path,)
    changed = type(original).model_validate(original.model_dump() | {'files': (change,)})
    evidence = m.HostEvidence.model_validate(measured | {
        'change_sha256': changed.sha256, 'manifest_sha256': gate.manifest.sha256})
    request = m.MergeRequest(change=changed, evidence=evidence,
        signature=host.sign(m.canonical(evidence.model_dump(mode='json'))))
    assert gate.assess(request, now=1100.).status == 'draft_only'


def test_unresolved_internal_import_closes_the_gate_package():
    from g6.distill.protection_gate import import_closure
    sources = {'src/g6/sample/rule_gate.py': 'from .missing import decide\n'}
    result = import_closure(sources, tuple(sources))
    assert result.unresolved
    assert 'g6/sample/' in result.closed_packages


@pytest.mark.parametrize('dynamic', [
    'from builtins import __import__ as load\nload(chosen)\n',
    'load = __import__\nload(chosen)\n'])
def test_aliased_runtime_imports_also_close_the_gate_package(dynamic):
    from g6.distill.protection_gate import import_closure
    sources = {'src/g6/sample/rule_gate.py': 'from .supplier import decide\n',
               'src/g6/sample/supplier.py': dynamic}
    result = import_closure(sources, ('src/g6/sample/rule_gate.py',))
    assert result.unresolved
    assert 'g6/sample/' in result.closed_packages


def test_host_merge_gate_requires_the_complete_manifest_injection():
    from tests.self_training.test_research_merge_gate import fixture
    m, gate, host, repo, _, _ = fixture()
    with pytest.raises(ValueError, match='manifest'):
        m.MergeGate(home=None, config=gate.config, host=host, repository=repo, policy=gate.policy)


def test_merge_fixture_exercises_unprotected_application_changes():
    from tests.self_training.test_research_merge_gate import fixture
    _, gate, _, _, change, _ = fixture()
    assert gate.manifest.hits(change.files) == ()
