# SPDX-License-Identifier: Apache-2.0
"""The collection reader's mapping entry retains names, roots and effect checks."""
import ast

import pytest

from g6.core.git_name import GitName
from g6.workforce._collection_effects import CollectionEffects


@pytest.mark.parametrize("source,expected", [
    ("def setup():\n    pass\n", True),
    ("import pytest\ndef setup():\n    pytest.Function.runtest = None\n", False),
])
def test_reader_from_trees_keeps_git_names_and_import_roots(source, expected):
    helper = GitName(b"lib/helper.py")
    caller = GitName(b'test_odd"name\n\xff.py')
    trees = {
        helper: ast.parse(source),
        caller: ast.parse("from helper import setup\nsetup()\n"),
    }
    reader = CollectionEffects.from_trees(trees, roots=("lib",))
    assert reader.trees is trees
    assert reader.passive(caller) is expected
