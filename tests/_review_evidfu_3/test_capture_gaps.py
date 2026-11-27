"""Captured identity gaps must remain explicit on every evidence surface."""
from types import SimpleNamespace

import pytest

from g6.harness import patch_review
from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import coding_certificate
from tests._review_evidfu_3.test_boundaries import tmp_path, isolated
from tests._review_evid_3.test_review3_boundaries import (
    commit_fixture, finish, observe, public_review, value_for,
)
from tests.workflow.kit import make_repo


@pytest.mark.parametrize("unresolved", [False, True], ids=["ordinary_control", "captured_gap"])
def test_gap_is_unknown_on_selected_card(tmp_path, monkeypatch, unresolved):
    repo = make_repo(tmp_path / "repo")
    selection = "test_case.py"
    (repo / selection).write_text(
        "from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    if unresolved:
        (repo / "conftest.py").write_text("def pytest_generate_tests(metafunc): pass\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    assert bool(evidence.capture_sources(repo, [selection])[selection]) == (not unresolved)
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] == 1
    finish(repo, public_review(repo, selection, failed, monkeypatch), "0", monkeypatch)
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    passed = observe(repo, selection, tmp_path / "after")
    assert passed["passed"] == 1
    reply = finish(repo, public_review(repo, selection, passed, monkeypatch), "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    counts = record_md.counts(rows)
    text = lifecycle.export(project).with_name("RECORD.md").read_text(encoding="utf-8")
    assert counts["repaired"] == int(not unresolved) and counts["open"] == int(unresolved)
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    companion = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert companion["events"] == evidence.read(repo, "task")["justification"]
    card = reply["card"]
    expected = (None, None, True) if unresolved else (1, 0, False)
    assert (card["repaired"], card["open"], card["identities_incomplete"]) == expected, (
        f"card={card['repaired'], card['open'], card['identities_incomplete']}; "
        f"record incomplete={counts['identities_incomplete']}; export warns={'identities are incomplete' in text}; "
        f"companion incomplete={companion.get('identities_incomplete', False)}"
    )


@pytest.mark.parametrize("mixed", [False, True], ids=["no_capture_control", "mixed_capture"])
def test_any_capture_disables_legacy_fallback(tmp_path, monkeypatch, mixed):
    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    other = "test_other.py::test_other"
    (repo / "test_case.py").write_text(
        "from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    (repo / "test_other.py").write_text("def test_other(): pass\n", encoding="utf-8")
    commit_fixture(repo)
    for number in range(2):
        (repo / "module.py").write_text(f"VALUE = {3 - number}\n", encoding="utf-8")
        ran = observe(repo, node, tmp_path / f"main{number}")
        assert ran["failed" if number == 0 else "passed"] == 1
        value = value_for(repo, node, ran)
        if mixed:
            sources = evidence.capture_sources(repo, [other])
            extra = observe(repo, other, tmp_path / f"other{number}")
            assert extra["passed"] == 1
            value["scope"]["checks_run"].append(patch_review._record(2, "repository checks", [other], extra, sources=sources))
        reply = finish(repo, value, str(number), monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    counts = record_md.counts(rows)
    expected = int(not mixed)
    assert counts["repaired"] == expected, (
        f"mixed_capture={mixed}: card repaired={reply['card']['repaired']}, open={reply['card']['open']}; "
        f"record repaired={counts['repaired']}, open={counts['open']}; "
        f"incomplete sources={[e.data.get('source_incomplete_sha256') for e in rows if e.kind == 'review_finished']}"
    )
