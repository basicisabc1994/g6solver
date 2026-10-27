# SPDX-License-Identifier: Apache-2.0
import subprocess

import pytest

from g6.harness.patch_review import review_patch


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "test_calc.py").write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n")
    for command in (["init", "-q"], ["add", "."], ["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                                 "-c", "commit.gpgsign=false", "commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(tmp_path), *command], check=True, capture_output=True)
    return tmp_path


def review(repo, **kwargs):
    return review_patch(repo, task="Repair addition", claim="Addition returns the sum.",
                        sources={"requirement": "Addition returns the sum."}, tests=["test_calc.py"], **kwargs)


def test_real_failing_and_passing_tests_are_distinguished_without_false_assurance(repo, monkeypatch):
    monkeypatch.setenv("G6_ALLOW_UNCONFINED", "1")
    failed = review(repo)
    assert failed["tests"]["failed"] == 1 and failed["decision"] == "blocked"
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    passed = review(repo)
    assert passed["tests"]["passed"] == 1
    assert passed["completion_state"] != "verified"
    assert passed["artifact_digest"] != failed["artifact_digest"]
    assert passed["executed_checks"][0]["artifact_digest"] == passed["artifact_digest"]
    assert "complete patch correctness" in passed["not_checked"]
    if not passed["tests"]["isolation"].get("backend"):
        assert passed["decision"] == "blocked" and "unconfined test execution" in passed["unresolved"]


def test_weakened_test_and_stale_artifact_block_even_when_tests_pass(repo, monkeypatch):
    monkeypatch.setenv("G6_ALLOW_UNCONFINED", "1")
    (repo / "test_calc.py").write_text("def test_add():\n    assert True\n")
    got = review(repo)
    assert got["decision"] == "blocked"
    assert got["test_integrity"]
    assert review(repo, expected_artifact_digest="old")["warning_card"]["code"] == "G6_E_PATCH_STALE"


def test_missing_tests_cannot_be_replaced_with_source_claim(repo):
    got = review_patch(repo, task="Repair addition", claim="All tests passed.",
                       sources={"agent": "All tests passed."}, tests=[])
    assert got["completion_state"] == "blocked-escalated"


def test_adapter_contracts_connect_with_synthetic_successful_run(repo, monkeypatch):
    """Unit test of joins only; this fixture is not evidence of actual confinement."""
    from g6.workforce import repo_tests
    monkeypatch.setattr(repo_tests, "run", lambda *a, **kw: {
        "verdict": "passed", "passed": 1, "failed": 0, "degraded": False,
        "command": "pytest test_calc.py", "isolation": {"backend": "synthetic-unit-fixture"}})
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    got = review(repo)
    assert got["composition"]["assurance"]["status"] == "contract-checked", got
    assert len(got["composition"]["assurance"]["checks"]) == 5
    assert got["decision"] != "allow"
