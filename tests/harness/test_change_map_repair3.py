# SPDX-License-Identifier: Apache-2.0
"""Regression classes for file identity, pytest collectors and source headers."""
import pytest

from tests.harness.collection_fixtures import collection_answer

from g6.harness import change_map, patch_review
from tests.harness.test_change_map import change, mapped, target
from tests.harness.test_change_map_repair2 import API, reader_record


@pytest.mark.parametrize("other", ["src/api.py", "lib/api.py", "api/__init__.py"])
def test_colliding_files_keep_both_changes_and_local_callers(other):
    before = {"api.py": API, other: API.replace("return 1", "return 10")}
    after = {p: s.replace("return 1", "return 2") for p, s in before.items()}
    roots = ("lib",)
    symbols = change_map.changed_symbols(change(before, after), before, after, roots=roots)
    assert {(s["file"], s["symbol"]) for s in symbols} == {("api.py", "target"), (other, "target")}
    record = mapped(before, after, roots=roots)
    callers = [t for t in record["targets"] if t.get("symbol") == "neighbour"]
    assert {t["file"] for t in callers} == {"api.py", other}
    assert len({t["binding"] for t in callers}) == 2
    for rel in before:
        assert any(rel in s["check"] and "ambig" in s["reason"] for s in record["skipped"])


def test_ambiguous_import_does_not_choose_either_file():
    before = {"api.py": API, "src/api.py": API,
              "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                    stated_checks=("test_api.py",), collector=lambda *a, **k: collection_answer(before, {
                        "status": "available", "nodes": ["test_api.py::test_call"]}))
    assert target(record, "neighbour")["exercised"] is False


def test_adding_a_collision_keeps_the_original_files_comparison():
    before = {"api.py": API}
    after = {"api.py": API.replace("return 1", "return 2"), "src/api.py": API}
    symbols = change_map.changed_symbols(change(before, after), before, after)
    assert [(s["file"], s["change"]) for s in symbols if s["symbol"] == "target"] == [
        ("api.py", "changed"), ("src/api.py", "added")]


def test_failed_colliding_file_does_not_make_the_import_unambiguous(monkeypatch):
    original = change_map._Index._load_file

    def load(index, rel, *args):
        if rel == "src/api.py":
            raise RecursionError("index unavailable")
        return original(index, rel, *args)

    monkeypatch.setattr(change_map._Index, "_load_file", load)
    before = {"api.py": API, "src/api.py": API,
              "test_api.py": "from api import neighbour\ndef test_call():\n    neighbour()\n"}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                    stated_checks=("test_api.py",), collector=lambda *a, **k: collection_answer(before, {
                        "status": "available", "nodes": ["test_api.py::test_call"]}))
    assert target(record, "neighbour")["exercised"] is False
    assert any("src/api.py" in s["check"] and "RecursionError" in s["reason"] for s in record["skipped"])


@pytest.mark.parametrize("parent", [
    "class Hidden:\n", "class TestOuter:\n    __test__ = False\n",
    "class TestOuter:\n    def __init__(self):\n        pass\n",
    "class TestOuter:\n    def __new__(cls):\n        pass\n",
])
@pytest.mark.parametrize("selection", ["", "TestOuter::TestNested::test_call"])
def test_nested_collectors_require_all_parents(parent, selection):
    source = ("from api import neighbour\n" + parent +
              "    class TestNested:\n        def test_call(self):\n            neighbour()\n")
    node = "test_api.py" + ("::" + selection if selection else "")
    assert change_map.exercised([{"binding": "api.neighbour"}], {node: source}) == {"api.neighbour": []}


@pytest.mark.parametrize("config, text", [
    ("pytest.ini", "[pytest]\npython_classes = Spec*\npython_functions = check_*\n"),
    (".pytest.ini", "[pytest]\npython_classes = Spec*\npython_functions = check_*\n"),
    ("pyproject.toml", '[tool.pytest.ini_options]\npython_classes = ["Spec*"]\n'
                       'python_functions = ["check_*"]\n'),
    ("tox.ini", "[pytest]\npython_classes = Spec*\npython_functions = check_*\n"),
    ("setup.cfg", "[tool:pytest]\npython_classes = Spec*\npython_functions = check_*\n"),
])
@pytest.mark.parametrize("parent, expected", [("SpecOuter", True), ("TestOuter", False)])
def test_configured_patterns_apply_to_the_whole_chain(config, text, parent, expected):
    source = (f"from api import neighbour\nclass {parent}:\n    class SpecInner:\n"
              "        def check_call(self):\n            neighbour()\n")
    before = {"api.py": API, "test_api.py": source, config: text}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                    stated_checks=("test_api.py",))
    # 6ef4fdc4: no collection establishes neither a positive nor a negative.
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert target(record, "neighbour")["checks"] == []


def test_empty_primary_config_masks_lower_priority_patterns():
    before = {"api.py": API, "pytest.ini": "[pytest]\n",
              "pyproject.toml": '[tool.pytest.ini_options]\npython_classes = ["Spec*"]\n',
              "test_api.py": "from api import neighbour\nclass TestAPI:\n"
                             "    def test_call(self):\n        neighbour()\n"}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                    stated_checks=("test_api.py",))
    # 6ef4fdc4: config precedence alone cannot expand an uncollected file.
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert target(record, "neighbour")["checks"] == []


@pytest.mark.parametrize("config", ["[pytest]\npython_functions = check_*\n", "[pytest\n"])
def test_reader_supplies_collection_config_or_disables_selection(tmp_path, monkeypatch, config):
    source = "from api import neighbour\ndef check_call():\n    neighbour()\n"
    before = {"api.py": API, "test_api.py": source, "pytest.ini": config}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    for rel, text in after.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    rt = patch_review.repo_tests
    monkeypatch.setattr(rt, "_git", lambda *a, **k: b"api.py\0test_api.py\0pytest.ini\0")
    entries = {rel: ("100644", "blob", str(i) * 40, str(len(text)))
               for i, (rel, text) in enumerate(before.items(), 1)}
    monkeypatch.setattr(rt, "_listed_at", lambda *a, **k: entries)
    blobs = {entries[rel][2]: text.encode() for rel, text in before.items()}
    monkeypatch.setattr(rt, "blob_bytes", lambda repo, key: blobs[key])
    record = patch_review._change_neighbourhood(change(before, after, tmp_path), ("test_api.py",), (), (),
                                                deadline=10**20)
    assert target(record, "neighbour")["exercised"] == "uncertain"
    if not config.startswith("[pytest]"):
        assert any("pytest.ini" in s["check"] for s in record["skipped"])


@pytest.mark.parametrize("base, expected", [
    ("class Base:\n    def test_call(self):\n        neighbour()\n"
     "class TestBad(object, Base):\n    pass\n", False),
    ("class object:\n    def test_call(self):\n        neighbour()\n"
     "class TestGood(object):\n    pass\n", True),
    ("from builtins import object as Root\nclass Base:\n"
     "    def test_call(self):\n        neighbour()\nclass TestBad(Root, Base):\n    pass\n", False),
    ("class object:\n    def test_call(self):\n        neighbour()\n"
     "Alias = object\nclass TestGood(Alias):\n    pass\n", True),
])
def test_resolved_object_participates_in_c3(base, expected):
    before = {"api.py": API, "test_api.py": "from api import neighbour\n" + base}
    record = mapped(before, {**before, "api.py": API.replace("return 1", "return 2")},
                    stated_checks=("test_api.py",))
    # 6ef4fdc4: no collection establishes neither a positive nor a negative.
    assert target(record, "neighbour")["exercised"] == "uncertain"
    assert target(record, "neighbour")["checks"] == []
    if not expected:
        assert any("test_api.py" in s["check"] and "hierarchy" in s["reason"] for s in record["skipped"])


@pytest.mark.parametrize("prefix", [
    b"# coding: latin-1 # caf\xe9\n", b"#!/caf\xe9/python\n# coding: latin-1\n",
    b"# caf\xe9\n# coding: latin-1\n", b"\xef\xbb\xbf# caf\xc3\xa9\n# coding: utf-8\n",
])
@pytest.mark.parametrize("side", ["base", "patch"])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_nonascii_headers_reach_the_map(tmp_path, monkeypatch, prefix, side, newline):
    body = b"def target(x):\n    return x == 'caf\xc3\xa9'\n" if prefix.startswith(b"\xef\xbb\xbf") else (
        b"def target(x):\n    return x == 'caf\xe9'\n")
    raw = (prefix + body).replace(b"\n", newline)
    old = raw.replace(b" == ", b" != ") if side == "base" else b"def target(x):\n    return False\n"
    record = reader_record(tmp_path, monkeypatch, old, raw)
    assert "'caf\u00e9'" in [v for t in record["targets"] for v in t.get("literals", [])]
    assert not any("bad.py" in s["check"] for s in record["skipped"])


@pytest.mark.parametrize("raw", [
    b"# header\n# header\n# coding: latin-1\nx = '\xe9'\n",
    b"x = 1\n# coding: latin-1\nx = '\xe9'\n",
    b"\xef\xbb\xbf# coding: latin-1\nx = 1\n",
])
def test_cookie_placement_and_bom_conflicts_are_rejected(raw):
    with pytest.raises((SyntaxError, UnicodeError)):
        patch_review._python_source(raw)
