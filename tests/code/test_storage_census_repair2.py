# SPDX-License-Identifier: Apache-2.0
"""Census recognises Roots rewrites and qualified I/O without matching visitors/dicts."""
import pytest

from tests.code.test_storage_followup_roots import _unchecked_calls


@pytest.mark.parametrize("text", [
    'def f(p, roots):\n roots.__init__((p,), "x")',
    'from g6.code._roots import Roots\nRoots.__init__(roots, (p,), "x")',
    'from g6.code._roots import Roots\nsuper(Roots, roots).__setattr__("roots", (p,))',
    'import operator\noperator.setitem(vars(roots), "roots", (p,))',
    'dict.__setitem__(roots.__dict__, "roots", (p,))',
    'import shutil\nshutil.move(p, q)',
    'import shutil\nshutil.make_archive(q, "zip", root_dir=p)',
    'import shutil\nshutil.rmtree(p)',
    'import filecmp\nfilecmp.cmp(p, q)',
    'import filecmp\nfilecmp.dircmp(p, q)',
    'import os\nos.path.getctime(p)',
    'from shutil import copy as cp\ncp(p, q)',
    'from pathlib import Path as P\nP(p).walk()',
    'from pathlib import Path\np = Path("inside")\np.walk()',
    'import pathlib\ndef f(p: pathlib.Path):\n return p.walk()',
    'import operator as op\nput = op.setitem\nfields = vars(roots)\nput(fields, "roots", (p,))',
])
@pytest.mark.parametrize("module", ["g6.code.x.probe", "g6.sources.ctx_git.probe"])
def test_census_repair2_rejects_io_and_reinitialisation(text, module):
    assert _unchecked_calls(text, module)


@pytest.mark.parametrize("text", [
    'def f(env):\n return env.copy()',
    'import copy\ncopy.copy([1, 2])',
    'class V:\n def walk(self, node):\n  return [self.walk(c) for c in node]',
    'import ast\nast.walk(node)',
])
def test_census_repair2_accepts_non_filesystem_copy_and_walk(text):
    assert not _unchecked_calls(text, "g6.code.x.probe")


@pytest.mark.parametrize("text", [
    'class Block(Base):\n def __init__(self):\n  super().__init__(name="x")',
    'class Block(Base):\n def __init__(self):\n  super(Block, self).__init__(name="x")',
    'class Block(Base):\n def __init__(self):\n  start = super().__init__\n  start(name="x")',
])
def test_census_accepts_plain_super_construction(text):
    assert _unchecked_calls(text, "g6.code.example.block") == []


@pytest.mark.parametrize("text", [
    'super().__setattr__("roots", (p,))',
    'from g6.code._roots import Roots\nsuper(Roots, roots).__init__((p,), "x")',
])
def test_census_super_init_exception_preserves_rewrite_detection(text):
    assert _unchecked_calls(text, "g6.code.example.block")


def test_census_accepts_local_confined_reader_forwarding_with_roots():
    text = ('def read_bytes(path, cap, *, roots):\n pass\n'
            'def read_text(path, cap, *, roots):\n return read_bytes(path, cap, roots=roots)')
    assert _unchecked_calls(text, "g6.code._roots") == []


@pytest.mark.parametrize("argument", ["", ", roots=None"])
def test_census_rejects_local_confined_reader_forwarding_without_roots(argument):
    text = ('def read_bytes(path, cap, *, roots):\n pass\n'
            f'def read_text(path, cap, *, roots):\n return read_bytes(path, cap{argument})')
    assert _unchecked_calls(text, "g6.code._roots")
