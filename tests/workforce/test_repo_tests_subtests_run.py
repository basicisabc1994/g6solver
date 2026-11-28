# SPDX-License-Identifier: Apache-2.0
"""Real confined repository runs for the captured pytest subtest cases."""
import pytest

from g6.harness import patch_review
from g6.workforce import repo_tests as rt
from tests.workforce.test_repo_tests_run import git
from tests.workforce.test_repo_tests_subtests import CASES, EXPECTED


@pytest.mark.parametrize("case", CASES)
def test_repository_runner_understands_subtests(tmp_path, case):
    repo = tmp_path / "repository"
    repo.mkdir()
    shape = CASES[case]
    node = shape["reports"][0]["nodeid"]
    filename = node.partition("::")[0]
    (repo / filename).write_text(shape["source"], encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "synthetic subtests")
    raw = rt.run(repo, [filename])
    want, counts = EXPECTED[case]
    assert raw["verdict"] == want, raw
    report = rt.read_report(raw)
    assert report.valid and report.body_result(node) == want, raw
    assert raw["test_outcomes"][node]["call"] == next(
        row["outcome"] for row in shape["reports"] if row["class"] == "TestReport" and row["when"] == "call")
    assert tuple(raw["subtests"][node][key] for key in ("passed", "failed", "skipped")) == counts
    assert (raw["passed"], raw["failed"], raw["total"]) == (int(want == "passed"), int(want == "failed"), 1)
    assert report.failed == ({node} if want == "failed" else set())
    assert patch_review._record(2, "derived", [filename], raw)["executed_outcomes"] == {node: want}
