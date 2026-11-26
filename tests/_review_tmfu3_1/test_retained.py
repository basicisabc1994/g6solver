"""Run retained acceptance cases with all their disk fixtures in this review."""
import importlib
import os
import sys

import pytest

from tests._review_tmfu3_1.test_review import tmp_path

for module_name in ('tests._review_tmfu2_4.test_review',
                    'tests._review_tmfu2_4.test_regressions',
                    'tests.harness.test_template_tmfu3_1'):
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
    if 'portable_preparation_type_text_cannot_replace_structured_authority' in request.node.name:
        original = os.mkdir

        def mkdir(path, mode=0o777, *, dir_fd=None):
            return original(path, 0o777 if mode == 0o700 else mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, 'mkdir', mkdir)
