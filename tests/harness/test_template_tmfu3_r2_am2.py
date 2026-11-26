"""Disk spellings, rather than guarded git names, supply runtime ownership."""
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from g6.core.git_name import GitName
from g6.harness import behaviour_runner as runner, verification_templates as vt
from g6.workforce import repo_tests


@pytest.mark.parametrize('template', ['example.v1', 'differential.v1'])
@pytest.mark.parametrize('side', [vt.BASE, vt.PATCH])
@pytest.mark.parametrize('folder', ['library', 'caf\u00e9'])
def test_git_listed_module_locations_use_the_copy_spelling(tmp_path, template, side, folder):
    held = GitName((folder + '/subject.py').encode('utf-8'))
    deleted = GitName((folder + '/deleted.py').encode('utf-8'))
    tree = vt.Tree(tmp_path, SimpleNamespace(files=(deleted,)), frozenset({held}),
                   frozenset({held, deleted}), roots=(folder,))
    for on in (vt.BASE, vt.PATCH):
        tree._texts[(held, on)] = 'def run(value):\n    return value\n'
    definition = vt.REGISTRY[template]
    bindings = {'function': 'subject.run', **({'cases': [{'args': [1], 'returns': 1}]}
                if template == 'example.v1' else {'mode': 'outputs', 'calls': [{'args': [1]}]})}
    data = definition.applies(tree, definition.bindings.model_validate(bindings), side).data
    locations = data['own_module_files']
    assert all(type(path) is str for paths in locations.values() for path in paths)
    assert locations['subject'] == [repo_tests._on_disk(held)[0]]
    assert locations['deleted'] == [repo_tests._on_disk(deleted)[0]]
    # Both source generation and the real request encoder must accept the data.
    source = vt.compile_text(definition, data)
    compile(source, '<git-listed template>', 'exec')
    request = json.loads(runner._encoded(runner.request(data)))
    assert request['own_module_files'] == locations


def test_inexact_disk_name_withholds_only_its_location(tmp_path, monkeypatch):
    held = GitName(b'subject.py')
    unavailable = GitName(b'deleted.py')
    tree = vt.Tree(tmp_path, SimpleNamespace(files=(unavailable,)), frozenset({held}),
                   frozenset({held, unavailable}))
    original = repo_tests._on_disk

    def disk_name(path):
        return ('alias.py', False) if path == unavailable else original(path)

    monkeypatch.setattr(repo_tests, '_on_disk', disk_name)
    data = vt._own_modules(tree, vt.Target('subject', 'run', 'subject.py'), vt.PATCH)
    assert data['own_module_files'] == {'subject': ['subject.py']}
    assert 'deleted' in data['own_modules']


@pytest.mark.parametrize('exact', [True, False], ids=['exact', 'inexact'])
def test_archive_container_uses_the_copy_decoder_before_member_join(tmp_path, monkeypatch, exact):
    container = GitName(b'vendor.zip')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('held/child.py', '')
    tree = vt.Tree(tmp_path, SimpleNamespace(files=()), frozenset({container}), frozenset({container}))
    for side in (vt.BASE, vt.PATCH):
        tree._texts[(container, side)] = buffer.getvalue()
    original = repo_tests._on_disk

    def disk_name(path):
        return ('copy-vendor.zip', exact) if path == container else original(path)

    monkeypatch.setattr(repo_tests, '_on_disk', disk_name)
    data = vt._own_modules(tree, vt.Target('subject', 'run', 'subject.py'), vt.PATCH)
    assert data['own_module_files'] == ({'held.child': ['copy-vendor.zip/held/child.py']} if exact else {})
    assert data.get('own_module_paths', {}) == ({'copy-vendor.zip': ['held', 'held.child']} if exact else {})
    assert 'held.child' in data['own_modules']
