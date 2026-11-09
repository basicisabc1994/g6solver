"""The confirmed runner owns import roots and compares the entire copy, including dependencies."""
import ast
import hashlib
from pathlib import Path

import pytest

from g6.harness import verification_search as vs
from g6.workforce import repo_tests

from .helpers import repo, result


@pytest.mark.parametrize("change", ["dependency", "added", "missing", "unreadable"])
def test_full_copy_binding_refuses_every_unrepresented_byte(tmp_path, change):
    root = repo(tmp_path, extra={"helper.py": "VALUE = 1\n"})
    expected = vs._copy_manifest(repo_tests.change(root), deadline=vs._clock() + 60)
    copied = {**expected, "test_g6.py": "the generated check"}
    if change == "dependency":
        copied["helper.py"] = "different bytes"
    elif change == "added":
        copied["startup.py"] = "not in the artefact"
    elif change == "missing":
        del copied["helper.py"]
    else:
        expected["helper.py"] = "unreadable"
    assert not vs._same_copy(copied, expected, "test_g6.py")


def test_complete_copy_and_manifest_have_one_comparison(tmp_path):
    root = repo(tmp_path, extra={"helper.py": "VALUE = 1\n"})
    expected = vs._copy_manifest(repo_tests.change(root), deadline=vs._clock() + 60)
    assert "helper.py" in expected
    assert vs._same_copy({**expected, "test_g6.py": "check"}, expected, "test_g6.py")
    assert vs._same_copy(dict(expected), expected, "test_g6.py")


def test_unreadable_manifest_never_reaches_the_copy(tmp_path, monkeypatch):
    root = repo(tmp_path)
    monkeypatch.setattr(repo_tests, "_digest", lambda path: "unreadable")
    with pytest.raises(OSError):
        vs._copy_manifest(repo_tests.change(root), deadline=vs._clock() + 60)


def test_manifest_identity_comes_from_the_saved_artefact_not_another_live_read(tmp_path):
    root = repo(tmp_path, extra={"helper.py": "VALUE = 1\n"})
    change = repo_tests.change(root)
    # The sparse artifact identity intentionally has no hashes for unchanged
    # dependencies. Capture the separately versioned executable-byte binding.
    before = vs.bind_execution(change, repo_tests.snapshot(root), deadline=vs._clock() + 60)
    frozen = hashlib.sha256((root / "helper.py").read_bytes()).hexdigest()
    (root / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    expected = vs._copy_manifest(change, deadline=vs._clock() + 60, artefact=before)
    assert expected["helper.py"] == frozen


@pytest.mark.parametrize("start", [None, "different-starting-bytes"])
def test_result_without_the_checked_starting_manifest_is_unavailable(tmp_path, monkeypatch, start):
    root = repo(tmp_path)
    before = vs.bind_execution(repo_tests.change(root), repo_tests.snapshot(root), deadline=vs._clock() + 60)
    monkeypatch.setattr(repo_tests, "_run", lambda *a, **k: {
        **result("test_g6.py::test_example"), **({"source_before": start} if start else {})})
    got = vs._run_check(repo_tests.change(root), "test_g6.py", "def test_example():\n    pass\n",
                        "test_g6.py::test_example", at_base=False, timeout_s=60, artefact=before)
    assert got.get(vs.NOT_THE_ARTEFACT_KEY) is True
    assert vs.reading(got, "test_g6.py::test_example")["result"] != "passed"


def test_inventory_confirmed_imports_and_each_bound_copy_are_owned_by_g6():
    root = Path(__file__).resolve().parents[2] / "src/g6/harness"
    runner = ast.parse((root / "confirmed_checks.py").read_text(encoding="utf-8"))
    run = next(n for n in runner.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    prepares = [n for n in ast.walk(run) if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "verification_templates.prepare"]
    assert len(prepares) == 1
    assert all(k.arg != "roots" for k in prepares[0].keywords)
    assert "rooted = {}" in ast.unparse(run)
    executor = ast.parse((root / "verification_search.py").read_text(encoding="utf-8"))
    execute = next(n for n in executor.body if isinstance(n, ast.FunctionDef) and n.name == "_run_check")
    calls = [ast.unparse(n.func) for n in ast.walk(execute) if isinstance(n, ast.Call)]
    assert calls.count("repo_tests.repository_copy") == 1
    runs = [n for n in ast.walk(execute) if isinstance(n, ast.Call) and ast.unparse(n.func) == "repo_tests._run"]
    assert len(runs) == 2
    # The ordinary and owned alternatives share this checked copy, not two independently captured copies.
    revision = next(n for n in ast.walk(execute) if isinstance(n, ast.With)
                    and any(ast.unparse(item.context_expr) == "revision" for item in n.items))
    assert all(any(node is run for node in ast.walk(revision)) for run in runs)
    assert sorted("observation" in {keyword.arg for keyword in run.keywords} for run in runs) == [False, True]
    assert calls.count("_copy_manifest") == calls.count("_same_copy") == 1
    body = ast.unparse(execute)
    assert body.index("_copy_manifest(") < body.index("repository_copy(")
    assert "_same_copy(" in body and "_copy_snapshot(copy" in body
    assert "ran.get('source_before') != digest(copied)" in body
