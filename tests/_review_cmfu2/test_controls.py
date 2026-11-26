import json

import pytest

from g6.workforce import repo_tests
from tests._review_changemap1.test_review import repository
from tests._review_cmfu2.test_authority import API, mapped, oracle


@pytest.mark.parametrize("kind", ["plain", "staticmethod", "classmethod", "inherited", "parenthesized_mark"])
def test_honest_callable_keeps_positive_location(tmp_path, kind):
    api = API.replace("    raise AssertionError('unexercised neighbour')\n", "")
    if kind == "plain":
        source = "from api import neighbour\ndef test_call():\n    assert neighbour() == 2\n"
        others = {}
    elif kind == "parenthesized_mark":
        source = ("from api import neighbour\nimport pytest\n"
                  "@(\n    pytest.mark.parametrize('value', [1])\n)\n"
                  "def test_call(value):\n    assert neighbour() == 2\n")
        others = {}
    elif kind == "inherited":
        source = "from base import Parent\nclass TestAPI(Parent):\n    pass\n"
        others = {"base.py": "from api import neighbour\nclass Parent:\n    def test_call(self):\n        assert neighbour() == 2\n"}
    else:
        source = ("from api import neighbour\nclass TestAPI:\n    @" + kind + "\n"
                  "    def test_call(" + ("cls" if kind == "classmethod" else "") + "):\n"
                  "        assert neighbour() == 2\n")
        others = {}
    before = {"api.py": api, "test_api.py": source, **others}
    delta = repository(tmp_path, before, {**before, "api.py": api.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr
    assert mapped(delta, answer)["exercised"] is True


@pytest.mark.parametrize("variant", ["spaces", "ancestor_rootdir", "case_alias"])
def test_reviewed_root_spelling_and_pytest_ancestor(tmp_path, variant):
    root = tmp_path / "reviewed root \N{GREEK SMALL LETTER BETA}"
    source = "def test_call():\n    pass\n"
    repository(root, {"test_api.py": source}, {"test_api.py": source})
    extra = ("--rootdir", str(tmp_path)) if variant == "ancestor_rootdir" else ()
    launch_root = root.with_name(root.name.upper()) if variant == "case_alias" else root
    run, answer = oracle(launch_root, extra=extra)
    assert run.returncode == 0, run.stdout + run.stderr
    assert answer == {"status": "available", "nodes": ["test_api.py::test_call"],
                      "bodies": {"test_api.py::test_call": {"file": "test_api.py", "line": 1}}}


@pytest.mark.parametrize("location", ["../outside.py", "test_api.py/../../outside.py"])
def test_outside_callable_location_is_uncertain(tmp_path, location):
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"root": str(tmp_path), "exit": 0,
                                  "nodes": ["test_api.py::test_call"],
                                  "bodies": {"test_api.py::test_call": {"file": location, "line": 1}}}),
                      encoding="utf-8")
    assert repo_tests._read_collection(report, 0, tmp_path)["status"] == "uncertain"
    with pytest.raises(repo_tests.RepoRefused):
        repo_tests._test_id(tmp_path, location)


@pytest.mark.parametrize("kind", ["assigned_lambda", "assigned_partial", "redefined_base"])
def test_unbound_or_ambiguous_callable_stays_uncertain(tmp_path, kind):
    sources = {
        "assigned_lambda": "test_call = lambda: None\n",
        "assigned_partial": "from functools import partial\ndef helper():\n    pass\ntest_call = partial(helper)\n",
        "redefined_base": ("from api import neighbour\nclass Parent:\n"
                           "    def test_call(self):\n        neighbour()\n"
                           "class Parent:\n    def test_call(self):\n        pass\n"
                           "class TestAPI(Parent):\n    pass\n"),
    }
    before = {"api.py": API, "test_api.py": sources[kind]}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    run, answer = oracle(tmp_path)
    assert run.returncode == 0 and len(answer["nodes"]) == 1, run.stdout + run.stderr
    run, _ = oracle(tmp_path, collect_only=False)
    assert run.returncode == 0 and "1 passed" in run.stdout, run.stdout + run.stderr
    sibling = mapped(delta, answer)
    assert sibling["exercised"] == "uncertain" and sibling["checks"] == []
