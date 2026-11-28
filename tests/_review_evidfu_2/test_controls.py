"""Controls for unresolved sources, relative imports, and evidence authority."""
import pytest

from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import repo_tests
from tests._review_evidfu_2.test_machinery import tmp_path, isolated
from tests._review_evid_3.test_review3_boundaries import (
    test_label_targets_and_certificate_custody_control as check_labels_and_custody,
    commit_fixture, finish, observe, public_review,
)
from tests.workflow.kit import make_repo


@pytest.mark.parametrize("kind", [
    "cycle", "deep", "star", "hook", "plugin_hook", "above_root", "malformed", "encoding",
])
def test_uncertain_machinery_withholds_control(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    collector = repo / "test_case.py"
    collector.write_text("def test_case(): pass\n", encoding="utf-8")
    if kind in {"cycle", "deep"}:
        length = 2 if kind == "cycle" else 140
        collector.write_text("from chain0 import case as test_case\n", encoding="utf-8")
        for number in range(length):
            text = ("def case(): pass\n" if number == length - 1 and kind == "deep" else
                    f"from chain{(number + 1) % length} import case\n")
            (repo / f"chain{number}.py").write_text(text, encoding="utf-8")
    elif kind == "star":
        collector.write_text("from oracle import *\n", encoding="utf-8")
        (repo / "oracle.py").write_text("def test_case(): pass\n", encoding="utf-8")
    elif kind == "above_root":
        collector.write_text("from ..oracle import test_case\n", encoding="utf-8")
    elif kind in {"malformed", "encoding"}:
        collector.write_bytes(b"def test_case(:\n" if kind == "malformed" else b"\xff\xfe\xff")
    elif kind == "hook":
        (repo / "conftest.py").write_text("def pytest_generate_tests(metafunc): pass\n", encoding="utf-8")
    else:
        (repo / "conftest.py").write_text("pytest_plugins = ['plugin']\n", encoding="utf-8")
        (repo / "plugin.py").write_text("def pytest_generate_tests(metafunc): pass\n", encoding="utf-8")
    assert evidence.capture_sources(repo, ["test_case.py"]) == {"test_case.py": ""}


@pytest.mark.parametrize("kind", ["relative_reexport", "namespace", "module_parameter", "indirect_fixture"])
def test_static_source_changes_identity_control(tmp_path, kind):
    repo = tmp_path / "repo"
    repo.mkdir()
    if kind == "relative_reexport":
        package = repo / "pkg" / "sub"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package.parent / "__init__.py").write_text("from .oracle import case\n", encoding="utf-8")
        (package / "test_case.py").write_text("from .. import case as test_case\n", encoding="utf-8")
        source = package.parent / "oracle.py"
        source.write_text("def case(): pass\n", encoding="utf-8")
        selection = "pkg/sub/test_case.py"
    else:
        selection = "test_case.py"
        if kind == "namespace":
            (repo / "ns").mkdir()
            (repo / selection).write_text("from ns.oracle import case as test_case\n", encoding="utf-8")
            source = repo / "ns" / "oracle.py"
            source.write_text("def case(): pass\n", encoding="utf-8")
        elif kind == "module_parameter":
            (repo / selection).write_text(
                "import pytest\nimport oracle as o\n@pytest.mark.parametrize('x', o.VALUES)\n"
                "def test_case(x): pass\n", encoding="utf-8")
            source = repo / "oracle.py"
            source.write_text("VALUES = [2]\n", encoding="utf-8")
        else:
            (repo / selection).write_text(
                "import pytest\nfrom oracle import observed\n"
                "@pytest.mark.parametrize('observed', [2], indirect=True)\n"
                "def test_case(observed): pass\n", encoding="utf-8")
            source = repo / "oracle.py"
            source.write_text("import pytest\n@pytest.fixture\ndef observed(request):\n    return request.param\n",
                              encoding="utf-8")
    before = evidence.capture_sources(repo, [selection])
    assert before[selection]
    source.write_text(source.read_text(encoding="utf-8") + "# changed machinery\n", encoding="utf-8")
    assert evidence.capture_sources(repo, [selection]) != before


def test_labels_do_not_gain_authority_or_disclosure_control(tmp_path, monkeypatch):
    check_labels_and_custody(tmp_path, monkeypatch)


@pytest.mark.parametrize("granularity", ["file", "class", "nodes"])
def test_distinct_executed_counts_agree_control(tmp_path, monkeypatch, granularity):
    repo = make_repo(tmp_path / "repo")
    (repo / "test_case.py").write_text(
        "import pytest\nfrom module import VALUE\nclass TestCase:\n"
        "    @pytest.mark.parametrize('i', [0, 1])\n"
        "    def test_case(self, i):\n        assert VALUE == 2\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    ran = observe(repo, "test_case.py", tmp_path / "child")
    assert ran["failed"] == 2
    selection = {"file": "test_case.py", "class": "test_case.py::TestCase",
                 "nodes": sorted(repo_tests.read_report(ran).phases)}[granularity]
    for job in ("0", "1"):
        value = public_review(repo, selection, ran, monkeypatch)
        reply = finish(repo, value, job, monkeypatch)
        project, task = adapters.identities(repo, "task")
        rows = ProjectRecord(project).events(task=task)
        counts = record_md.counts(rows)
        readable = lifecycle.export(project).with_name("RECORD.md").read_text(encoding="utf-8")
        assert reply["card"]["caught"]["reproduced"] == reply["card"]["open"] == counts["reproduced"] == counts["open"] == 2
        assert "2 reproduced findings" in readable
