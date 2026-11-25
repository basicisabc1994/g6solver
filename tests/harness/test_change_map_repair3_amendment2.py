# SPDX-License-Identifier: Apache-2.0
"""File-local selection failures and nested pytest names keep independent advice."""
import pytest

from g6.harness import change_map
from tests.harness.test_change_map import change, target


API = "def changed():\n    return 1\ndef neighbour():\n    return changed()\n"


@pytest.mark.parametrize("error", [ValueError, RecursionError])
@pytest.mark.parametrize("rel,name", [("bad.py", "f"), ("test_bad.py", "test_call")])
def test_selection_failure_discards_only_its_file(monkeypatch, error, rel, name):
    before = {
        "api.py": API,
        rel: f"def {name}():\n    return 1\ndef caller():\n    return {name}()\n",
        "test_good.py": "from api import neighbour\ndef test_call():\n    neighbour()\n",
    }
    after = {**before, "api.py": API.replace("return 1", "return 2"),
             rel: before[rel].replace("return 1", "return 3")}
    original = change_map._selected

    def selected(index, file, selection):
        if file == rel:
            raise error("selection failed")
        return original(index, file, selection)

    monkeypatch.setattr(change_map, "_selected", selected)
    record = change_map.build(change(before, after), before, after,
                              stated_checks=(f"{rel}::{name}", "test_good.py::test_call"))
    neighbour = target(record, "neighbour")
    assert neighbour["exercised"] is True
    assert neighbour["checks"] == ["test_good.py::test_call"]
    assert not any(item["file"] == rel for item in record["targets"])
    assert any(rel in item["check"] and error.__name__ in item["reason"] for item in record["skipped"])


@pytest.mark.parametrize("selection,expected", [
    ("TestOuter::TestInner::test_call", True),
    ("TestOuter.TestInner.test_call", True),
    ("TestOuter.TestInner::test_call", True),
    ("TestOuter.TestInner.test_call[value.with.dots[brackets]]", True),
    ("TestOuter.TestInner.test_call[value::with.dots[brackets]]", True),
    ("TestOuter.OtherInner.test_call", False),
    ("OtherOuter.TestInner.test_call", False),
    ("TestOuter.TestInner.helper", False),
])
def test_nested_selection_checks_each_class_and_function_name(selection, expected):
    source = """from api import neighbour
class Base:
    def test_call(self):
        neighbour()
    def helper(self):
        neighbour()
class TestOuter:
    class TestInner(Base):
        pass
    class OtherInner(Base):
        pass
class OtherOuter:
    class TestInner(Base):
        pass
"""
    node = "test_api.py::" + selection
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}) == {
        "api.neighbour": [node] if expected else []}


@pytest.mark.parametrize("selection,accepted", [
    ("CheckOuter.CheckInner.check_call", True),
    ("CheckOuter::CheckInner::check_call[value.with.dots[brackets]]", True),
    ("OtherOuter.CheckInner.check_call", False),
    ("CheckOuter.OtherInner.check_call", False),
    ("CheckOuter.CheckInner.test_call", False),
])
def test_configured_patterns_apply_to_each_nested_name(selection, accepted):
    texts = {"pytest.ini": "[pytest]\npython_classes = Check*\npython_functions = check_*\n"}
    if accepted:
        change_map._static_names(texts, "test_api.py", selection, {})
    else:
        with pytest.raises(change_map._FileOmitted, match="body does not match pytest collection names"):
            change_map._static_names(texts, "test_api.py", selection, {})
