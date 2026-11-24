# SPDX-License-Identifier: Apache-2.0
"""Repair 2: file transactions, selection independence and Python source decoding."""
from itertools import combinations, permutations

import pytest

from g6.harness import change_map, patch_review
from tests.harness.test_change_map import change, mapped, target


API = "def target():\n    return 1\ndef neighbour():\n    return target()\n"
CHECKS = """import pytest
from api import neighbour
class Grand:
    @pytest.mark.parametrize("value", [0, 1], ids=["value.with.dots[brackets]", "value::with.dots[brackets]"])
    def test_call(self, value):
        neighbour()
class Base(Grand):
    pass
class TestA(Base):
    def test_other(self):
        pass
class TestB(Base):
    pass
class TestOuter:
    class TestInner(Base):
        pass
"""


@pytest.mark.parametrize("error", [ValueError, RecursionError])
@pytest.mark.parametrize("stage", ["_load_file", "_candidate", "resolve", "calls", "source", "_selected"])
def test_each_file_stage_keeps_other_files(monkeypatch, stage, error):
    # Each stage must discard all partial advice for bad.py, including earlier
    # decision points, and name that file while keeping the healthy caller.
    bad = "def f():\n    return 1\ndef caller():\n    return f()\nif flag:\n    alias = f\nelse:\n    alias = f\n"
    before = {"api.py": API, "bad.py": bad}
    after = {"api.py": API.replace("return 1", "return 2"), "bad.py": bad.replace("return 1", "return 3")}
    owner = change_map if stage == "_selected" else change_map._Index
    original = getattr(owner, stage)

    def fail(*args, **kwargs):
        if stage in ("_load_file", "source", "_selected"):
            rel = args[1]
        elif stage == "_candidate":
            rel = args[1].scope.file
        else:
            rel = args[2].file if stage == "resolve" else args[1].file
        if rel == "bad.py":
            raise error("file stage failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, stage, fail)
    record = mapped(before, after, stated_checks=("bad.py::f",))
    assert target(record, "neighbour")["rank"] == 10
    assert not any(t["file"] == "bad.py" for t in record["targets"])
    assert any("bad.py" in s["check"] and error.__name__ in s["reason"] for s in record["skipped"])


@pytest.mark.parametrize("expression", ["external" + ".part" * 600 + "()",
                                        "external" + ".part" * 1600 + "()", "+".join(["1"] * 1600)])
def test_input_depth_keeps_other_file_advice(expression):
    before = {"api.py": API, "deep.py": "def unrelated():\n    return " + expression + "\n"}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")})
    assert target(record, "neighbour")["rank"] == 10
    # If the interpreter itself rejects depth, the boundary must name the file.
    assert not any(s["check"] == "change map" for s in record["skipped"])


@pytest.mark.parametrize("cycle", [False, True])
def test_alias_chain_keeps_independent_resolution(cycle):
    aliases = "from api import neighbour as alias0\n"
    aliases += "".join(f"alias{i} = alias{i - 1}\n" for i in range(1, 40))
    if cycle:
        aliases = aliases.replace("from api import neighbour as alias0", "alias0 = alias39")
    aliases += "def test_alias():\n    alias39()\n"
    node = "test_alias.py::test_alias"
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: aliases}) == {
        "api.neighbour": [] if cycle else [node]}


def test_selection_subsets_and_orders_equal_each_alone():
    selections = ("TestA::test_other", "TestB::test_call", "TestOuter::TestInner::test_call")
    targets = [{"binding": "api.neighbour"}]

    def advice(items):
        return change_map.exercised(targets, {"test_api.py::" + item: CHECKS for item in items})["api.neighbour"]

    alone = {s: "test_api.py::" + s in advice((s,)) for s in selections}
    assert alone == {selections[0]: False, selections[1]: True, selections[2]: True}
    for size in range(1, len(selections) + 1):
        for subset in combinations(selections, size):
            for order in permutations(subset):
                assert advice(order) == sorted("test_api.py::" + s for s in order if alone[s])


@pytest.mark.parametrize("selection", [
    "TestOuter::TestInner::test_call", "TestOuter.TestInner.test_call",
    "TestOuter::TestInner::test_call[value.with.dots[brackets]]",
    "TestOuter.TestInner.test_call[value.with.dots[brackets]]",
    "TestB::test_call[value::with.dots[brackets]]", "TestOuter::TestInner",
])
def test_nested_and_inherited_parametrized_selections(selection):
    node = "test_api.py::" + selection
    collection = {node: {"status": "available", "nodes": ["test_api.py::TestOuter::TestInner::test_call"]}} \
        if selection == "TestOuter::TestInner" else None
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: CHECKS},
                                 collection=collection) == {"api.neighbour": [node]}


def test_call_cache_is_immutable():
    index = change_map._Index({"api.py": API}, (), [], "patch")
    calls = index.calls(index.symbols["api.neighbour"])
    assert isinstance(calls, frozenset)


def test_mro_cache_remains_immutable_after_other_merges():
    source = CHECKS + "class Side(Grand):\n    pass\nclass TestDiamond(Base, Side):\n    pass\n"
    index = change_map._Index({"test_api.py": source}, (), [], "check")
    base = change_map._mro(index, index.classes["test_api.Base"])
    assert isinstance(base, tuple)
    expected = tuple(cls.binding for cls in base)
    change_map._mro(index, index.classes["test_api.TestA"])
    change_map._mro(index, index.classes["test_api.TestB"])
    diamond = change_map._mro(index, index.classes["test_api.TestDiamond"])
    assert [cls.node.name for cls in diamond] == ["TestDiamond", "Base", "Side", "Grand"]
    assert tuple(cls.binding for cls in index.mros["test_api.Base"]) == expected


def test_deep_class_hierarchy_uses_a_work_stack():
    source = "from api import neighbour\nclass Base0:\n    def test_call(self):\n        neighbour()\n"
    source += "".join(f"class Base{i}(Base{i - 1}):\n    pass\n" for i in range(1, 450))
    source += "class TestDeep(Base449):\n    pass\n"
    node = "test_deep.py::TestDeep::test_call"
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}) == {"api.neighbour": [node]}


def test_failure_in_inherited_body_names_its_source_file(monkeypatch):
    original = change_map._Index.calls

    def calls(index, symbol):
        if symbol.file == "base.py":
            raise RecursionError("inherited body failed")
        return original(index, symbol)

    monkeypatch.setattr(change_map._Index, "calls", calls)
    before = {"api.py": API, "base.py": "class Base:\n    def test_call(self):\n        pass\n",
              "test_api.py": "from base import Base\nclass TestDerived(Base):\n    pass\n"}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                    stated_checks=("test_api.py::TestDerived::test_call",))
    assert target(record, "neighbour")
    assert any("base.py" in s["check"] and "RecursionError" in s["reason"] for s in record["skipped"])


@pytest.mark.parametrize("raw", [
    b"# coding: latin-1\ndef target(x):\n    return x == 'caf\xe9'\n",
    b"#!/usr/bin/python\n# coding: latin-1\ndef target(x):\n    return x == 'caf\xe9'\n",
    b"\xef\xbb\xbfdef target(x):\n    return x == 'caf\xc3\xa9'\n",
    b"# coding: utf-8\ndef target(x):\n    return x == 'caf\xc3\xa9'\n",
])
@pytest.mark.parametrize("side", ["base", "patch"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_reader_uses_python_encoding(tmp_path, monkeypatch, raw, side, newline):
    # For base decoding, a lost comparison must still retain the correct source
    # literal when the comparison changes in the patch.
    raw = raw.replace(b"\n", newline)
    old = raw.replace(b" == ", b" != ") if side == "base" else b"def target(x):\n    return False\n"
    record = reader_record(tmp_path, monkeypatch, old, raw)
    assert "'caf\u00e9'" in [v for t in record["targets"] for v in t.get("literals", [])]
    assert not any("bad.py" in s["check"] for s in record["skipped"])


@pytest.mark.parametrize("raw", [
    b"# coding: utf-8\nx = '\xff'\n", b"# coding: unknown-codec\nx = 1\n",
    b"\xef\xbb\xbf# coding: latin-1\nx = 1\n", b"# undecodable \xff\nx = 1\n",
])
@pytest.mark.parametrize("side", ["base", "patch"])
def test_undecodable_file_has_named_skip(tmp_path, monkeypatch, raw, side):
    clean = b"def target(x):\n    return False\n"
    record = reader_record(tmp_path, monkeypatch, raw if side == "base" else clean,
                           raw if side == "patch" else clean)
    assert target(record, "neighbour")["rank"] == 10
    assert not any(t["file"] == "bad.py" for t in record["targets"])
    assert any("bad.py" in s["check"] for s in record["skipped"])


def reader_record(tmp_path, monkeypatch, old, new):
    before = {"api.py": API, "bad.py": old}
    after = {"api.py": API.replace("return 1", "return 2"), "bad.py": new}
    (tmp_path / "api.py").write_bytes(after["api.py"].encode())
    (tmp_path / "bad.py").write_bytes(new)
    rt = patch_review.repo_tests
    monkeypatch.setattr(rt, "_git", lambda *a, **k: b"api.py\0bad.py\0")
    monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: {
        "api.py": ("100644", "blob", "a" * 40, str(len(API))),
        "bad.py": ("100644", "blob", "b" * 40, str(len(old)))})
    monkeypatch.setattr(rt, "blob_bytes", lambda repo, key: API.encode() if key == "a" * 40 else old)
    return patch_review._change_neighbourhood(change(before, after, tmp_path), (), (), (), deadline=10**20)
