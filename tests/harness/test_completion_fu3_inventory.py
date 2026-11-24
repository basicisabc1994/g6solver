# SPDX-License-Identifier: Apache-2.0
"""Collaborators grant named operations, never every imported operation."""
from pathlib import Path

import pytest

from tests.harness.test_completion_writer_allowlist import unguarded_calls


@pytest.mark.parametrize('body', [
    "cloud.write_retained(session / 'state.json', body)",
    "keys.path().write_text('changed')", "repo_tests.Path(p).unlink()", "io.open(p, 'w')",
    "importlib.import_module('shutil').rmtree(p)", "subprocess.run(['git', 'clean', '-fdx'])",
    "sqlite3.connect(p)", "exec('open(p, \"w\")')", "eval(code)", "__import__('os')",
    "unknown_function(p)", "from g6.core.cloud_consent import write_retained as save\nsave(p, body)",
    "from os import replace as move\nmove(a, b)", "getattr(p, 'write_text')('x')",
    "write = p.write_text\nwrite('x')", "import shutil as store\nstore.move(a, b)",
    "shutil.move(a, b)", "os.truncate(p, 0)", "tempfile.mkstemp(dir=session)",
    "shutil.copyfileobj(a, b)", "json.dump(state, handle)", "os.utime(p)", "os.removedirs(p)",
    "p.chmod(0)", "w = p.write_bytes\nw(b'')",
])
def test_unlisted_imported_and_plain_writers_are_refused(body):
    head = ('from g6.core import cloud_consent as cloud, keys\n'
            'from g6.workforce import repo_tests\nimport io, importlib, subprocess, sqlite3\n')
    assert unguarded_calls(head + body), body


def test_three_completion_surfaces_use_only_named_collaborator_operations():
    from g6.harness import completion_hook, completion_loop
    from g6.mcp import review_loop

    for module in (completion_hook, completion_loop, review_loop):
        assert not unguarded_calls(Path(module.__file__).read_text(encoding='utf-8')), module.__name__


@pytest.mark.parametrize('body', [
    "from g6.core import cloud_consent as retained\nretained.write_retained(p, body)",
    "from g6.core.keys import path as key_path\nkey_path().write_text('changed')",
    "from io import open as write\nwrite(p, 'w')",
    "import subprocess as process\nprocess.run(command)",
    "from builtins import exec as run_code\nrun_code(code)",
    "from builtins import eval as evaluate\nevaluate(code)",
    "from builtins import __import__ as load\nload('os')",
    "from g6.harness import _completion_storage as store\nstore.future_writer(p)",
    "from g6.core import keys\nkeys.future_writer(p)",
    "unknown_function(p)", "exec(code)", "eval(code)", "__import__('os')",
])
def test_inventory_refuses_unlisted_operations_through_aliases_and_plain_names(body):
    assert unguarded_calls(body), body


@pytest.mark.parametrize('body', [
    "from g6.harness import _completion_storage as retained\nretained.write_retained(p, body)",
    "from g6.harness._completion_storage import write_retained as save\nsave(p, body)",
    "from g6.core import cloud_consent as cloud\ncloud.read_retained(p)",
    "from g6.core import keys\nkeys.path().read_text()",
    "from pathlib import Path as FilePath\nFilePath(p).exists()",
])
def test_inventory_admits_named_operations_through_aliases(body):
    assert not unguarded_calls(body), body
