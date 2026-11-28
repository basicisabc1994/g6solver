# SPDX-License-Identifier: Apache-2.0
"""Every public map producer returns plain JSON data, including nested paths."""
import json

import pytest

from g6.core.git_name import GitName, shown_name
from g6.harness import change_map, patch_review
from tests.harness.test_change_map import change


def assert_json_native(value):
    # isinstance(str) and json.dumps both accept GitName; callers cannot print it.
    assert type(value) in (dict, list, str, int, bool, float, type(None)), type(value).__name__
    if type(value) is dict:
        for key, item in value.items():
            assert type(key) is str, type(key).__name__
            assert_json_native(item)
    elif type(value) is list:
        for item in value:
            assert_json_native(item)


@pytest.mark.parametrize("raw", [b"api.py", b'odd"api.py', b"odd\napi.py", b"odd\xffapi.py"])
@pytest.mark.parametrize("producer", ["changed_symbols", "decision_points", "siblings", "build"])
def test_map_producers_return_only_json_native_values(raw, producer):
    rel = GitName(raw)
    before = {rel: "def changed(x):\n    return x == 1\ndef neighbour(x):\n    return changed(x)\n"
              "if flag:\n    alias = changed\nelse:\n    alias = neighbour\n"}
    after = {rel: before[rel].replace("x == 1", "x == 2")}
    record = getattr(change_map, producer)(change(before, after), before, after)
    assert record
    assert_json_native(record)
    entries = record["targets"] if producer == "build" else record
    assert entries and all(item["file"] == shown_name(rel) for item in entries)
    assert json.loads(json.dumps(record)) == record
    assert isinstance(repr(record), str)


@pytest.mark.parametrize("producer", ["skip", "json_record", "exercised", "collect_checks"])
def test_other_map_boundaries_convert_names_in_every_field(producer):
    rel = GitName(b'test_odd"name.py')
    if producer == "skip":
        record = change_map.skip(rel, rel)
        assert_json_native(record)
        assert record["check"] == record["reason"] == shown_name(rel)
    elif producer == "json_record":
        record = change_map.json_record({**change_map.empty(), "targets": [{"file": rel, "paths": [rel]}],
                                        "skipped": [{"tier": 0, "check": rel, "reason": rel}]})
        assert_json_native(record)
        assert record["targets"][0]["file"] == shown_name(rel)
    elif producer == "exercised":
        record = change_map.exercised([{"binding": rel}], {})
    else:
        delta = change({}, {rel: "def test_call():\n    pass\n"})
        record = change_map.collect_checks(delta, (), (), (), deadline=1e20, collector=lambda *a, **k: {
            "status": "uncertain", "reason": rel})
        assert_json_native(record)
        assert list(record) == [shown_name(rel)]
    assert_json_native(record)
    assert isinstance(repr(record), str)


def test_reader_record_contains_only_json_native_values(tmp_path):
    from tests._review_changemap1.test_review import repository

    before = {"api.py": "def changed(x):\n    return x == 1\ndef neighbour(x):\n    return changed(x)\n"}
    delta = repository(tmp_path, before, {"api.py": before["api.py"].replace("x == 1", "x == 2")})
    record = patch_review._change_neighbourhood(delta, (), (), (), deadline=1e20,
                                               collector=lambda *a, **k: {"status": "available", "nodes": []})
    assert record["targets"]
    assert_json_native(record)
    assert all(item["file"] == "api.py" for item in record["targets"])
    assert isinstance(repr(record), str)
