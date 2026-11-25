"""Retained acceptance tests, with disk fixtures confined to review 4."""
import importlib
import sys

import pytest

from tests._review_tmfu2_4.test_review import tmp_path

MODULES = [
    'tests._review_tmfu2_3.test_checks',
    'tests._review_tmfu2_3.test_review',
    'tests.harness.test_template_tmfu2_r3',
    'tests.harness.test_template_tmfu2_r3_am2',
]

for module_name in MODULES:
    module = importlib.import_module(module_name)
    for name, function in vars(module).items():
        if name.startswith('test_') and callable(function):
            globals()['test_retained_' + module_name.replace('.', '_') + '__' + name] = function


@pytest.fixture(autouse=True)
def redirect_disks(tmp_path, monkeypatch):
    for name in ('tmfu1_support', 'tmfu2_support', 'tmfu3_support', 'tmfu4_support'):
        if name in sys.modules:
            monkeypatch.setattr(sys.modules[name], '__file__', __file__)

@pytest.fixture(autouse=True)
def portable_temp_permissions(request, monkeypatch):
    # Python 3.12's mode 0700 temporary folders exclude this managed sandbox's writer.
    if 'portable_preparation_type_text_cannot_replace_structured_authority' in request.node.name:
        import os
        original = os.mkdir

        def mkdir(path, mode=0o777, *, dir_fd=None):
            return original(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, 'mkdir', mkdir)

@pytest.mark.parametrize('alias', ['junction', 'short-path'])
def test_real_disk_filesystem_alias_identity(tmp_path, alias):
    import ctypes
    import subprocess
    from g6.harness import behaviour_runner as runner, verification_search as vs

    original = tmp_path / 'a long directory for identity'
    original.mkdir()
    owned = original / 'owned.py'
    owned.write_text('VALUE = 1\n', encoding='utf-8')
    if alias == 'junction':
        linked = tmp_path / 'alias'
        ran = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(original)], capture_output=True)
        assert ran.returncode == 0, ran.stderr
        candidate = str(linked / owned.name)
    else:
        buffer = ctypes.create_unicode_buffer(32768)
        assert ctypes.windll.kernel32.GetShortPathNameW(str(owned), buffer, len(buffer))
        candidate = buffer.value
    assert runner.path_key(candidate) == runner.path_key(owned)
    assert runner.path_inside(original, candidate)
    assert vs._same_copy({candidate: 'a' * 64}, {str(owned): 'a' * 64}, str(tmp_path / 'check.py'))
