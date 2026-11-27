"""Review 4: retain static integrity findings when the final review ran no tests."""
from pathlib import Path
from uuid import uuid4

import pytest

from g6.harness import patch_report
from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import repo_tests
from tests._review_evid_3.test_review3_boundaries import (
    commit_fixture, finish, isolated, observe, public_review,
    test_adapter_cannot_backdate_replacement_source,
    test_capped_identities_do_not_erase_an_unrepaired_failure,
    test_empty_collection_is_not_counted_control,
    test_imported_body_cannot_borrow_collectors_digest,
    test_label_targets_and_certificate_custody_control,
    test_unknown_loading_cannot_supply_a_repair,
)
from tests.workflow.kit import make_repo


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


@pytest.mark.parametrize("executed", [True, False], ids=["ran_control", "no_tests_static_review"])
def test_loop_report_keeps_final_reviews_integrity_findings(tmp_path, monkeypatch, executed):
    repo = make_repo(tmp_path / "repo")
    (repo / "conftest.py").write_text("FLAG = 1\n", encoding="utf-8")
    (repo / "test_case.py").write_text("def test_case():\n    assert True\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "conftest.py").write_text("FLAG = 2\n", encoding="utf-8")
    ran = observe(repo, "test_case.py", tmp_path / "child") if executed else repo_tests._not_run_report("not selected")
    value = public_review(repo, "test_case.py" if executed else [], ran, monkeypatch)
    assert value["outcome"] == "test_integrity_compromised"
    # _final_review calls review_patch directly, without the MCP finishing wrapper.
    # The hypothetical one-line finished MCP card is not this production path.
    inner = value["report"]
    findings = [line for line in inner.splitlines() if line.startswith("- **Test integrity:")]
    assert len(findings) == 2, inner
    assert len(patch_report.card_lines(value["card"])) == 5
    combined = patch_report.completion_report({"reviews": [], "assessment": value}, inner)
    (tmp_path / "inner.md").write_text(inner, encoding="utf-8")
    (tmp_path / "combined.md").write_text(combined, encoding="utf-8")
    lost = [line for line in findings if line not in combined.splitlines()]
    assert not lost, f"unchecked={not executed}; removed integrity findings={lost}"


@pytest.mark.parametrize("select_file", [False, True], ids=["explicit_nodes_control", "file_selection"])
def test_first_card_open_count_agrees_with_record(tmp_path, monkeypatch, select_file):
    repo = make_repo(tmp_path / "repo")
    (repo / "test_case.py").write_text(
        "import pytest\nfrom module import VALUE\n@pytest.mark.parametrize('i', [0, 1])\n"
        "def test_case(i):\n    assert VALUE == 2\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    failed = observe(repo, "test_case.py", tmp_path / "child")
    assert failed["failed"] == 2
    nodes = sorted(repo_tests.read_report(failed).phases)
    value = public_review(repo, "test_case.py" if select_file else nodes, failed, monkeypatch)
    assert value["outcome"] == "defect_detected"
    reply = finish(repo, value, "0", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    counts = record_md.counts(rows)
    assert evidence.check_counts(rows)["open"] == counts["open"] == 2
    path = lifecycle.export(project).with_name("RECORD.md")
    readable = path.read_text(encoding="utf-8")
    assert "2 open at loop stops" in readable
    assert reply["card"]["open"] == counts["open"], (
        f"two executed failures: card open={reply['card']['open']}, RECORD.md open={counts['open']}; "
        f"selected={'file' if select_file else 'nodes'}"
    )
