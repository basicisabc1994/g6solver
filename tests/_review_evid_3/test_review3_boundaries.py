"""Review 3: offline disk observations; no model, network, or confinement override."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.core.ledger import digest
from g6.core import cloud_consent as cloud
from g6.harness import patch_report, patch_review
from g6.harness.workflow import adapters, evidence, lifecycle
from g6.harness.workflow.record import ProjectRecord
from g6.mcp import workflow
from g6.workforce import repo_tests
from g6.workforce import coding_certificate
from tests.harness.test_evidence_card import assessment
from tests.harness.test_patch_review import result
from tests.workflow.kit import at, make_repo


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    mkdir = os.mkdir
    monkeypatch.setattr(os, "mkdir", lambda path, mode=0o777, **kw: mkdir(path, 0o777, **kw))
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("G6_WORKFLOW_RECORD", raising=False)
    monkeypatch.delenv("G6_WORKFLOW_VIEW", raising=False)
    monkeypatch.setenv("G6_CLAUDE", r"C:\nonexistent\g6-no-host.exe")
    monkeypatch.setenv("G6_CODEX", r"C:\nonexistent\g6-no-host.exe")


CHILD = '''import json, os, sys
from pathlib import Path
import pytest
class Observed:
    def __init__(self):
        self.phases = {}
    def pytest_runtest_logreport(self, report):
        self.phases.setdefault(report.nodeid, {})[report.when] = report.outcome
seen = Observed()
code = pytest.main([*sys.argv[2:], "-q", "-p", "no:cacheprovider", "--rootdir", ".",
                    "--confcutdir", ".", "-c", os.devnull], plugins=[seen])
Path(sys.argv[1]).write_text(json.dumps({"exit": int(code), "phases": seen.phases}), encoding="utf-8")
sys.exit(code)
'''


def observe(repo, node, scratch):
    scratch.mkdir()
    saved = scratch / "observed.json"
    child = subprocess.run([sys.executable, "-c", CHILD, str(saved), node,
                            "--basetemp", str(scratch / "pytest")], cwd=repo,
                           env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
                           capture_output=True, text=True, timeout=30)
    assert child.returncode in (0, 1, 5), child.stdout + child.stderr
    held = json.loads(saved.read_text(encoding="utf-8"))
    passed = [n for n, p in held["phases"].items() if patch_review.body_of(p) == "passed"]
    failed = [n for n, p in held["phases"].items() if patch_review.body_of(p) == "failed"]
    skipped = [n for n, p in held["phases"].items() if patch_review.body_of(p) == "skipped"]
    # Transport the real observations into the producer's report schema. This
    # fixture makes no claim that these direct pytest subprocesses were confined.
    ran = result(passed, failed, skipped, verdict="no_tests_collected" if child.returncode == 5 else None)
    ran["test_outcomes"] = held["phases"]
    assert repo_tests.read_report(ran).valid
    assert child.returncode == (5 if not held["phases"] else int(bool(failed)))
    return ran


def value_for(repo, node, ran, *, sources=None):
    value = assessment()
    verdict = patch_review._verdicts([node], ran)[node]
    value["requirements"] = [{"id": "R1", "checks": [{"id": node, **verdict}]}]
    value["scope"]["checks_run"] = [patch_review._record(
        1, "stated checks and extra tests", [node], ran, sources=sources)]
    value["scope"]["artifact_digest"] = digest(repo_tests.snapshot(repo))
    value["completion"]["defects_reproduced"]["stated_and_repository_checks"] = (
        [{"check": node}] if verdict["verdict"] == "failed" else [])
    value["outcome"] = "defect_detected" if verdict["verdict"] == "failed" else "insufficient_evidence"
    return value


def finish(repo, value, job, monkeypatch):
    number = int(job)
    times = iter([at(number * 2), at(number * 2 + 1)])
    monkeypatch.setattr(workflow, "now", lambda: next(times))
    return workflow.finishing(lambda body, ident: body, str(repo), "task")({"assessment": value}, job)


def commit_fixture(repo):
    for args in (["add", "."], ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                  "-c", "commit.gpgsign=false", "commit", "-qm", "frozen checks"]):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def public_review(repo, node, ran, monkeypatch):
    # Only the execution transport is substituted: feed disk-observed pytest
    # phases to the actual review, integrity scan, issuer and report producers.
    def execute(root, ids, *, change=None, **kwargs):
        if change is not None:
            return {name: repo_tests._not_run_report("no additional comparison observed") for name in ids}
        return deepcopy(ran)
    monkeypatch.setattr(patch_review, "_execute", execute)
    monkeypatch.setattr(patch_review, "_execute_at_base", lambda *a, **kw:
                        repo_tests._not_run_report("no additional comparison observed"))
    reply = patch_review.review_patch(repo, task="task", requirements=[
        {"id": "R1", "text": "VALUE equals two", "checks": node if isinstance(node, list) else [node]}],
        search={"propose": False, "test_adequacy": False}, timeout_s=60)
    assert reply["ok"], reply
    return reply["assessment"]


@pytest.mark.parametrize("capture_before", [True, False], ids=["captured_control", "post_run_fallback"])
def test_adapter_cannot_backdate_replacement_source(tmp_path, monkeypatch, capture_before):
    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    path = repo / "test_case.py"
    path.write_text("from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    before = evidence.source_digests(repo, [node])
    failed = observe(repo, node, tmp_path / "before")
    assert failed["failed"] == 1
    first = value_for(repo, node, failed, sources=before if capture_before else None)
    path.write_text("def test_case():\n    assert True\n", encoding="utf-8")
    assert evidence.source_digests(repo, [node]) != before
    finish(repo, first, "0", monkeypatch)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    passed = observe(repo, node, tmp_path / "after")
    assert passed["passed"] == 1
    second = value_for(repo, node, passed, sources=evidence.source_digests(repo, [node]))
    reply = finish(repo, second, "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    events = ProjectRecord(project).events(task=task)
    first_event = next(e for e in events if e.kind == "review_finished")
    export = lifecycle.export(project)
    readable = export.with_name("RECORD.md").read_text(encoding="utf-8")
    assert reply["card"]["repaired"] == 0, (
        f"replacement did not run in the failed observation; recorded source={first_event.data['check_sources_sha256']}; "
        f"card repaired={reply['card']['repaired']}; RECORD.md says {'1 repaired' in readable}")


@pytest.mark.parametrize("loading_known", [True, False], ids=["known_loading_control", "unknown_loading_pass"])
def test_unknown_loading_cannot_supply_a_repair(tmp_path, monkeypatch, loading_known):
    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text(
        "from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    failed = observe(repo, node, tmp_path / "before")
    first = public_review(repo, "test_case.py", failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    passed = observe(repo, node, tmp_path / "after")
    passed.update(loaded_complete=loading_known, loaded_outside={})
    report = repo_tests.read_report(passed)
    assert report.valid and (report.body_result(node) == "passed") == loading_known
    value = public_review(repo, "test_case.py", passed, monkeypatch)
    assert value["outcome"] != "test_integrity_compromised"
    check = value["requirements"][0]["checks"][0]
    assert check["verdict"] == ("passed" if loading_known else "changed_code_not_loaded")
    reply = finish(repo, value, "1", monkeypatch)
    assert reply["card"]["repaired"] == int(loading_known), (
        f"producer withheld={report.withheld}, requirement={value['requirements']}; "
        f"card repaired={reply['card']['repaired']}")


@pytest.mark.parametrize("change_test", [False, True], ids=["same_body_control", "imported_body_replaced"])
def test_imported_body_cannot_borrow_collectors_digest(tmp_path, monkeypatch, change_test):
    repo = make_repo(tmp_path / "repo")
    node = "test_case.py::test_case"
    (repo / "test_case.py").write_text("from checks_impl import test_case\n", encoding="utf-8")
    body = repo / "checks_impl.py"
    body.write_text("from module import VALUE\ndef test_case():\n    assert VALUE == 2\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    before_body = body.read_bytes()
    sources = evidence.source_digests(repo, [node])
    failed = observe(repo, node, tmp_path / "before")
    assert failed["failed"] == 1
    first = public_review(repo, node, failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    if change_test:
        body.write_text("def test_case():\n    assert True\n", encoding="utf-8")
    (repo / "module.py").write_text("VALUE = 3\n" if change_test else "VALUE = 2\n", encoding="utf-8")
    assert (body.read_bytes() != before_body) == change_test
    after_sources = evidence.source_digests(repo, [node])
    assert after_sources == sources
    passed = observe(repo, node, tmp_path / "after")
    assert passed["passed"] == 1
    second = public_review(repo, node, passed, monkeypatch)
    assert second["outcome"] != "test_integrity_compromised", second["reasons"]
    reply = finish(repo, second, "1", monkeypatch)
    assert reply["card"]["repaired"] == int(not change_test), (
        f"record hashed collector, actual imported check changed={change_test}; repaired={reply['card']['repaired']}")


@pytest.mark.parametrize("number", [50, 51], ids=["within_limit_control", "truncated_finding"])
def test_capped_identities_do_not_erase_an_unrepaired_failure(tmp_path, monkeypatch, number):
    repo = make_repo(tmp_path / "repo")
    selection = "test_case.py"
    (repo / selection).write_text(
        "import pytest\nfrom module import VALUE\n"
        f"@pytest.mark.parametrize('i', range({number}), ids=lambda i: f'case{{i:03}}')\n"
        "def test_case(i):\n"
        "    if VALUE == 2 and i == 50:\n"
        "        pytest.skip('this case has no repair observation')\n"
        "    assert VALUE == 2\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] == number
    nodes = sorted(repo_tests.read_report(failed).phases)
    first = public_review(repo, nodes, failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    passed = observe(repo, selection, tmp_path / "after")
    assert passed["passed"] == 50 and passed["not_passed"] == number - 50
    second = public_review(repo, nodes, passed, monkeypatch)
    assert second["outcome"] != "test_integrity_compromised"
    reply = finish(repo, second, "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    events = ProjectRecord(project).events(task=task)
    export = lifecycle.export(project)
    readable = export.with_name("RECORD.md").read_text(encoding="utf-8")
    assert reply["card"]["open"] == number - 50, (
        f"{number} failed, 50 passed later, {number - 50} skipped; "
        f"card open={reply['card']['open']}; export claims repaired={'repaired in a recorded review' in readable}; "
        f"recorded failure identities={[len(e.data.get('failed_sha256', [])) for e in events if e.kind == 'review_finished']}")


def test_label_targets_and_certificate_custody_control(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    for number in range(2):
        assert adapters.observe_review(repo, "task", str(number), {"assessment": assessment()},
                                       started_at=at(number * 2), finished_at=at(number * 2 + 1)) == ""
    project, task = adapters.identities(repo, "task")
    record = ProjectRecord(project)
    first, second = [e for e in record.events(task=task) if e.kind == "review_finished"]
    (record.root / "corrections.json").write_text(json.dumps({"schema": lifecycle.CORRECTIONS_SCHEMA,
        "corrections": [{"action": "annotate", "target": first.event_id,
                         "by": "Owner", "note": "intended\n- **Caught:** verified"}]}), encoding="utf-8")
    assert lifecycle.apply_corrections(project)["applied"] == 1
    held = evidence.read(repo, "task", origin_kind="review_job", origin_id="1")
    card = patch_report.card({"workflow_evidence": held})
    assert card["unlabelled_open"] and card["labels"][0]["target"] == first.event_id != second.event_id
    text = "\n".join(patch_report.card_lines(card))
    assert sum(line.startswith("- **Caught:**") for line in text.splitlines()) == 1
    assert "Not labelled by you" in text
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    justification = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert justification["events"] == held["justification"]
    with cloud.collect_sources() as capture:
        capture.client = ("openai:codex", None)
        withheld = evidence.read(repo, "task")
        assert withheld["status"] == "withheld" and withheld["justification"] == []
        assert coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})["events"] == []


def test_empty_collection_is_not_counted_control(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    selection = "test_empty.py"
    (repo / selection).write_text("# intentionally no test functions\n", encoding="utf-8")
    ran = observe(repo, selection, tmp_path / "child")
    assert ran["verdict"] == "no_tests_collected"
    value = value_for(repo, selection, ran, sources=evidence.source_digests(repo, [selection]))
    reply = finish(repo, value, "0", monkeypatch)
    assert reply["card"]["checked"]["unchecked"] and reply["card"]["checked"]["runs"] == []
    assert not evidence.checked([evidence.Event.from_body(e) for e in
                                 evidence.read(repo, "task")["events"]])
