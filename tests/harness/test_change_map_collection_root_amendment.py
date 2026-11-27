# SPDX-License-Identifier: Apache-2.0
"""Collection identity belongs to the reviewed repository, even under another root."""
import json

import pytest

from g6.core.git_name import GitName, shown_name
from g6.harness import change_map
from g6.workforce import repo_tests
from tests.harness.test_change_map import change, target
from tests.harness.test_change_map_repair2 import API


# test_collection_identity_uses_reviewed_root_despite_cwd_and_pytest_root[False|True] was superseded by review 2's
# content-identity requirement (lead ruling, 794f766b): it fed a body location with no code object as a positive.


def test_reader_normalizes_absolute_callable_location_without_cwd(tmp_path, monkeypatch):
    repo = tmp_path / "reviewed"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    node = "test_api.py::test_call"
    report = tmp_path / "collection.json"
    report.write_text(json.dumps({"root": str(repo), "exit": 0, "nodes": [node],
                                  "bodies": {node: {"file": str(repo / "test_api.py"), "line": 1}}}),
                      encoding="utf-8")
    found = repo_tests._read_collection(report, 0, repo)
    assert found["bodies"] == {node: {"file": "test_api.py", "line": 1}}


# [False] (a location-only positive) was superseded by review 2's content-identity requirement (lead ruling, 794f766b);
# [True], the display-quoted path answering uncertain, still holds.
@pytest.mark.parametrize("display_path", [True])
def test_display_quoted_path_is_not_a_different_raw_callable_path(display_path):
    rel = GitName(b'test_"api.py')
    node = 'test_"api.py::test_call'
    source = "from api import neighbour\ndef test_call():\n    neighbour()\n"
    answer = {"status": "available", "nodes": [node],
              "bodies": {node: {"file": shown_name(rel) if display_path else 'test_"api.py', "line": 2}}}
    before = {"api.py": API, rel: source}
    after = {**before, "api.py": API.replace("return 1", "return 2")}
    record = change_map.build(change(before, after), before, after, stated_checks=(node,),
                              collection={node: answer})
    sibling = target(record, "neighbour")
    assert sibling["exercised"] == ("uncertain" if display_path else True)
    assert sibling["checks"] == ([] if display_path else [node])
