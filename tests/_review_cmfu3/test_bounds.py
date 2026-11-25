"""Findings 7 and 8 (review 3): work repair 2's checks spend outside the map's bounds.

7: the collection reader re-parses a body's whole file once per collected node, with no deadline and no
   checkpoint, so a window around it cannot stop it.
8: a module is scanned once per admission, not once per map: explicit selections in one file rescan it.
"""
import time

from g6.workforce import repo_tests
from tests._review_cmfu3.helpers import HONEST_API, HONEST_TEST, NODE, change, oracle, scenario, show

FILLER = "".join(f"def helper_{i}(value):\n    return [value, {i}, 'text', (1, 2, 3)]\n" for i in range(600))
CASES = "".join(f"def test_case_{i}():\n    value = {{'a': [1, 2, 3], 'b': (4, 5)}}\n"
                f"    assert sorted(value) == ['a', 'b']\n    assert len(value['a']) == 3\n" for i in range(150))


def _reader_seconds(tmp_path, count, window):
    values = ", ".join(str(i) for i in range(count))
    source = ("import pytest\nfrom api import neighbour\n" + FILLER +
              f"@pytest.mark.parametrize('value', [{values}])\ndef test_call(value):\n    assert neighbour() == 2\n")
    assert len(source) < 100_000
    change(tmp_path, {"test_api.py": source}, api=HONEST_API)
    run, _ = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    report = tmp_path.parent / (tmp_path.name + "-reports") / "counted.json.collection.json"
    started = time.monotonic()
    answer = repo_tests.within(window, lambda: repo_tests._read_collection(report, run.returncode, tmp_path))
    return time.monotonic() - started, answer


def test_reader_stays_inside_its_window(tmp_path):
    # An honest, passive repository: one parametrized test, 500 literal values, a 42 KB file.
    seconds, answer = _reader_seconds(tmp_path, 500, window=1)
    assert seconds < 3.0, show({"window_seconds": 1, "reader_seconds": round(seconds, 2),
                                "nodes": len((answer or {}).get("nodes", [])),
                                "unresolved": len((answer or {}).get("unresolved", []))})


def test_control_reader_on_a_few_nodes(tmp_path):
    seconds, answer = _reader_seconds(tmp_path, 10, window=1)
    assert seconds < 3.0 and len(answer["nodes"]) == 10 and "unresolved" not in answer, (seconds, answer)


def _explicit(tmp_path, count):
    checks = [NODE, *(f"test_api.py::test_case_{i}" for i in range(count - 1))]
    return scenario({"root": str(tmp_path), "files": {"test_api.py": HONEST_TEST + CASES}, "api": HONEST_API,
                     "mode": "many", "checks": checks})


def test_explicit_selections_in_one_file_keep_the_map(tmp_path):
    # 40 stated node ids of one 603-line test file, collection unavailable.
    found = _explicit(tmp_path, 40)
    assert found["targets"] and not found["limits"], show(
        {"targets": found["targets"], "limits": found["limits"], "seconds": found["seconds"]})


def test_control_a_few_explicit_selections(tmp_path):
    found = _explicit(tmp_path, 5)
    assert found["targets"] == 2 and not found["limits"], found
    assert found["siblings"][0]["exercised"] is True and found["siblings"][0]["checks"] == [NODE], found
