# SPDX-License-Identifier: Apache-2.0
"""Real collection retains raw filenames and can resolve static suppression signs."""
import os
import time

import pytest

from g6.harness import change_map, patch_review
from tests._review_cmfu2.test_authority import oracle
from tests._review_changemap1.test_review import git, repository
from tests.harness.test_change_map import target
from tests.harness.test_change_map_repair2 import API


def collected_map(delta, selection):
    """Each collector call runs the shipped producer and reader on disk."""
    observed = []

    def collect(repo, checks, **kwargs):
        assert repo == delta.repo
        assert checks == [selection]
        assert not kwargs.get("sources")
        run, answer = oracle(repo, selection)
        assert run.returncode == 0, run.stdout + run.stderr
        observed.append(answer)
        return answer

    record = patch_review._change_neighbourhood(
        delta, (selection,), (), (), deadline=time.monotonic() + 45, collector=collect)
    assert len(observed) == 1
    return target(record, "neighbour"), observed[0]


@pytest.mark.parametrize("name", [
    pytest.param('test_"api.py', id="quoted", marks=pytest.mark.skipif(
        os.name == "nt", reason="Windows cannot store a double quote in a filename")),
    pytest.param("test_\u00e9api.py", id="non_ascii"),
])
def test_real_collection_keeps_git_quoted_raw_filename(tmp_path, name):
    source = "from api import neighbour\ndef test_call():\n    neighbour()\n"
    before = {"api.py": API, name: source}
    delta = repository(tmp_path, before, {**before, "api.py": API.replace("return 1", "return 2")})
    assert git(tmp_path, "-c", "core.quotePath=true", "ls-files", "--", name).startswith('"')
    sibling, answer = collected_map(delta, name)
    node = name + "::test_call"
    assert answer["nodes"] == [node]
    assert answer["bodies"][node] == {"file": name, "line": 2}
    assert answer.get("unresolved", []) == []
    assert answer["codes"][node]
    assert sibling["exercised"] is True
    assert sibling["checks"] == [name]


@pytest.mark.parametrize("sign", [
    pytest.param("def suppress():\n    TestAPI.__test__ = False\n", id="uncalled_helper"),
    pytest.param("__test__ = True\n", id="enabled_module"),
])
def test_real_collection_selects_body_despite_static_suppression_sign(tmp_path, sign):
    source = ("from api import neighbour\nclass TestAPI:\n"
              "    def test_call(self):\n        neighbour()\n" + sign)
    selection = "test_api.py::TestAPI::test_call"
    before = {"api.py": API, "test_api.py": source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    delta = repository(tmp_path, before, after)
    static = change_map.build(delta, before, after, stated_checks=(selection,))
    assert target(static, "neighbour")["exercised"] == "uncertain"
    assert target(static, "neighbour")["checks"] == []
    sibling, answer = collected_map(delta, selection)
    assert answer["nodes"] == [selection]
    assert answer["bodies"][selection] == {"file": "test_api.py", "line": 3}
    assert answer.get("unresolved", []) == []
    assert answer["codes"][selection]
    assert sibling["exercised"] is True
    assert sibling["checks"] == [selection]
