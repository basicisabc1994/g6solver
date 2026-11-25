# SPDX-License-Identifier: Apache-2.0
"""Repair-3 refusal/holder regressions, written before the implementation.

Runtime acceptance belongs to the lead's lane; the builder runs no pytest.
"""
import ast

import pytest

from g6.core.git_name import GitName
from g6.workforce._collection_effects import CollectionEffects
from tests.core.test_change_map_fu2_effects import inventory


@pytest.mark.parametrize("route", ["alias", "subscript", "factory", "with-target", "with-keyword", "walrus"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "collection-effect"])
def test_resolved_class_keeps_its_collecting_name(tmp_path, monkeypatch, route, effect):
    source = "import os\nclass Settings:\n"
    if effect:
        source += ("    def __getattribute__(self, name):\n"
                   "        os.environ['PATH'] = 'repair-three'\n"
                   "        return object.__getattribute__(self, name)\n")
    source += "    def test_call(self): pass\n"
    source += {
        "alias": "TestSettings = Settings\n",
        "subscript": "classes = [Settings]\nTestSettings = classes[0]\n",
        "factory": "classes = [Settings]\ndef make(): return classes[0]\nTestSettings = make()\n",
        "with-target": "from contextlib import nullcontext\nwith nullcontext(Settings) as TestSettings: pass\n",
        "with-keyword": "from contextlib import nullcontext\nwith nullcontext(enter_result=Settings) as TestSettings: pass\n",
        "walrus": "classes = [Settings]\nheld = (TestSettings := classes[0])\n",
    }[route]
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not effect)


@pytest.mark.parametrize("binding", ["?unresolved", "?argument", "?instance", "?missing",
                                     frozenset(), ("?list", None, 0, ())],
                         ids=["unresolved", "argument", "receiver", "unknown-kind", "empty", "missing-container"])
def test_unknown_collection_values_have_no_inert_fallback(binding):
    file = GitName(b"test_api.py")
    reader = CollectionEffects.from_trees({file: ast.parse("def test_call(): pass\n")})
    assert reader._pytest_value(binding, 0, binding_name="TestSettings") is False
    assert reader._descriptor(binding, 0) is False


def test_known_literal_collection_value_control():
    file = GitName(b"test_api.py")
    reader = CollectionEffects.from_trees({file: ast.parse("VALUE = None\n")})
    assert reader.passive(file) is True


@pytest.mark.parametrize("statement", [
    "classes = [Settings]\nTestSettings = classes[int('0')]\n",
    "from contextlib import nullcontext\nheld = nullcontext(Settings)\nwith held as TestSettings: pass\n",
    "classes = [Settings]\nclasses[0] = Settings\nTestSettings = classes[0]\n",
    "values = (1, 2)\nfirst, second = values\n",
    "value = 1\nvalue += 1\n",
    "values = [None]\nvalues[0] = MISSING\n",
    "TestSettings = MISSING\nfor TestSettings in (): pass\n",
], ids=["unmodeled-index", "unmodeled-context-alias", "mutated-index", "unresolved-unpacking",
        "unresolved-augassign", "unknown-item-value", "loop-cannot-erase-unknown"])
def test_unresolved_binding_stays_unavailable(tmp_path, monkeypatch, statement):
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": "class Settings: pass\n" + statement,
    })
    assert usable is False


@pytest.mark.parametrize("placement", ["bound", "list", "nested", "dict"])
@pytest.mark.parametrize("flag", [False, True], ids=["disabled-control", "enabled"])
def test_only_bound_classes_become_collection_holders(tmp_path, monkeypatch, placement, flag):
    helper = ("import os\nclass Settings:\n    __test__ = " + str(flag) + "\n"
              "    def __getattribute__(self, name):\n"
              "        os.environ['PATH'] = 'repair-three'\n"
              "        return object.__getattribute__(self, name)\n")
    helper += "DATA = " + {"bound": "Settings", "list": "[Settings]", "nested": "[(Settings,)]",
                          "dict": "{'setting': Settings}"}[placement] + "\n"
    _, usable = inventory(tmp_path, monkeypatch, {
        "helper.py": helper,
        "test_api.py": "from helper import DATA\ndef test_call(): pass\n",
    })
    assert usable is (placement != "bound" or not flag)


@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "reachable-effect"])
def test_container_instances_keep_protocol_audit(tmp_path, monkeypatch, effect):
    source = "import os\nclass Settings:\n"
    source += ("    def __getattribute__(self, name):\n"
               "        os.environ['PATH'] = 'repair-three'\n"
               "        return object.__getattribute__(self, name)\n" if effect else "    pass\n")
    _, usable = inventory(tmp_path, monkeypatch, {
        "test_api.py": source + "DATA = [Settings()]\n",
    })
    assert usable is (not effect)


@pytest.mark.parametrize("source", [
    "ANSWER = 1 + 2\n",
    "def make(): return 1 + 2\nANSWER = make()\n",
    "from contextlib import nullcontext\nwith nullcontext((1,)) as (answer,): pass\n",
    "for number in (1, 2): pass\n",
    "first, second = (1, 2)\n",
    "values = [number for number in (1, 2)]\n",
    "import logging\nLOGGER = logging.getLogger(__name__)\n",
    "TestSettings = 1\nfor TestSettings in (): pass\n",
], ids=["computed-literal", "returned-computed-literal", "unpacked-context", "plain-loop", "literal-unpacking",
        "plain-comprehension", "native-module-name", "loop-keeps-known-value"])
def test_modeled_plain_results_keep_identity(tmp_path, monkeypatch, source):
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is True


@pytest.mark.parametrize("mutation", ["append", "insert", "store"])
@pytest.mark.parametrize("effect", [False, True], ids=["inert-control", "effect"])
def test_mutated_container_contents_are_still_audited(tmp_path, monkeypatch, mutation, effect):
    source = "import os\nclass Settings:\n"
    source += ("    def __getattribute__(self, name):\n"
               "        os.environ['PATH'] = 'repair-three'\n"
               "        return object.__getattribute__(self, name)\n" if effect else "    pass\n")
    source += {"append": "DATA = []\nDATA.append(Settings())\n",
               "insert": "DATA = []\nDATA.insert(0, Settings())\n",
               "store": "DATA = [None]\nDATA[0] = Settings()\n"}[mutation]
    _, usable = inventory(tmp_path, monkeypatch, {"test_api.py": source})
    assert usable is (not effect)
