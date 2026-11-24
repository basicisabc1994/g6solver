# SPDX-License-Identifier: Apache-2.0
"""Collection source identities retain git names as data through effect reads."""
import ast

import pytest

from g6.core.git_name import GitName
from g6.workforce._collection_effects import CollectionEffects


@pytest.mark.parametrize("raw", [b"test_api.py", b'odd"name\n\xff.py'])
@pytest.mark.parametrize("source,expected", [
    ("def setup():\n    pass\nsetup()\n", True),
    ("HANDLERS = []\nSTATE = {}\ndef register(function):\n"
     "    HANDLERS.append(function)\n    STATE['last'] = function\n"
     "    return function\n@register\ndef test_call(): pass\n", True),
    ("class Setup:\n    @staticmethod\n    def run():\n        return 2\nSetup.run()\n", True),
    ("import pytest\nclass Setup:\n    @staticmethod\n    def run():\n"
     "        pytest.Function.runtest = None\nSetup.run()\n", False),
])
def test_effect_references_keep_git_names_as_data(raw, source, expected):
    file = GitName(raw)
    reader = CollectionEffects({file: ast.parse(source)})
    assert reader.passive(file) is expected


def test_equal_lines_in_distinct_git_names_keep_distinct_callees():
    inert, active, caller = (GitName(raw) for raw in (b"inert.py", b"active.py", b"test_api.py"))
    reader = CollectionEffects({
        inert: ast.parse("def setup():\n    pass\n"),
        active: ast.parse("def setup():\n    import pytest\n    pytest.Function.runtest = None\n"),
        caller: ast.parse("from inert import setup\nsetup()\nfrom active import setup as change\nchange()\n"),
    })
    assert reader.passive(caller) is False
