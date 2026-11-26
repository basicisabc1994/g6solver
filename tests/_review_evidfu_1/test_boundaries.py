"""First-card totals and conservative static-discovery controls."""
import pytest

from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import repo_tests
from tests._review_evidfu_1.test_machinery import tmp_path, isolated
from tests._review_evid_3.test_review3_boundaries import commit_fixture, finish, observe, public_review
from tests.workflow.kit import make_repo


@pytest.mark.parametrize("file_selection", [False, True], ids=["nodes_control", "file_selection"])
def test_first_caught_count_agrees_with_export(tmp_path, monkeypatch, file_selection):
    repo = make_repo(tmp_path / "repo")
    (repo / "test_case.py").write_text(
        "import pytest\nfrom module import VALUE\n"
        "@pytest.mark.parametrize('i', [0, 1])\n"
        "def test_case(i):\n    assert VALUE == 2\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    ran = observe(repo, "test_case.py", tmp_path / "child")
    assert ran["failed"] == 2
    selection = "test_case.py" if file_selection else sorted(repo_tests.read_report(ran).phases)
    value = public_review(repo, selection, ran, monkeypatch)
    assert value["outcome"] == "defect_detected"
    reply = finish(repo, value, "0", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    counts = record_md.counts(rows)
    readable = lifecycle.export(project).with_name("RECORD.md").read_text(encoding="utf-8")
    shared = evidence.check_counts(rows)
    assert shared["reproduced"] == counts["open"] == reply["card"]["open"] == 2
    assert f"{counts['reproduced']} reproduced" in readable
    assert reply["card"]["caught"]["reproduced"] == counts["reproduced"] == shared["reproduced"], (
        f"first card caught={reply['card']['caught']['reproduced']}, open={reply['card']['open']}; "
        f"RECORD.md reproduced={counts['reproduced']}, open={counts['open']}; "
        f"shared reproduced={shared['reproduced']}"
    )


@pytest.mark.parametrize("kind", [
    "cycle", "deep", "plugin", "hook", "star", "above_root", "namespace", "malformed", "encoding",
])
def test_discovery_withholds_uncertain_identity_control(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    collector = repo / "test_case.py"
    if kind in {"cycle", "deep"}:
        length = 2 if kind == "cycle" else 140
        collector.write_text("from chain0 import case as test_case\n", encoding="utf-8")
        for number in range(length):
            source = ("def case(): pass\n" if number == length - 1 and kind == "deep" else
                      f"from chain{(number + 1) % length} import case\n")
            (repo / f"chain{number}.py").write_text(source, encoding="utf-8")
    elif kind == "star":
        collector.write_text("from oracle import *\n", encoding="utf-8")
        (repo / "oracle.py").write_text("def test_case(): pass\n", encoding="utf-8")
    elif kind in {"above_root", "namespace"}:
        collector.write_text("from ..oracle import test_case\n" if kind == "above_root" else
                             "from ns import test_case\n", encoding="utf-8")
        (repo / "ns").mkdir()
        (repo / "ns" / "test_case.py").write_text("def test_case(): pass\n", encoding="utf-8")
    elif kind in {"malformed", "encoding"}:
        collector.write_bytes(b"def test_case(:\n" if kind == "malformed" else b"\xff\xfe\xff")
    else:
        collector.write_text("def test_case(): pass\n", encoding="utf-8")
        (repo / "conftest.py").write_text("pytest_plugins = ['oracle']\n" if kind == "plugin" else
            "def pytest_generate_tests(metafunc): pass\n", encoding="utf-8")
    assert evidence.capture_sources(repo, ["test_case.py"]) == {"test_case.py": ""}


def test_relative_package_reexport_control(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "pkg" / "sub"
    package.mkdir(parents=True)
    for folder in (package, package.parent):
        (folder / "__init__.py").write_text("", encoding="utf-8")
    (package / "test_case.py").write_text("from .. import case as test_case\n", encoding="utf-8")
    (package.parent / "__init__.py").write_text("from .impl import case\n", encoding="utf-8")
    implementation = package.parent / "impl.py"
    implementation.write_text("def case():\n    assert False\n", encoding="utf-8")
    shadow = repo / "impl.py"
    shadow.write_text("def case(): pass\n", encoding="utf-8")
    node = "pkg/sub/test_case.py"
    before = evidence.capture_sources(repo, [node])
    assert before[node]
    shadow.write_text("def case():\n    assert True\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) == before
    implementation.write_text("def case():\n    assert True\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [node]) != before
