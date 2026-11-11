"""Ownership follows the run's source listing and the lifetime of its folder."""
from contextlib import contextmanager
import sys

import pytest

from g6.core import confine
from g6.workforce import bench, repo_tests as rt
from tests.harness.efu2_cases import PASSED, report
from tests.workforce.test_workforce_isolation import _off


@pytest.fixture
def folders(tmp_path, monkeypatch):
    @contextmanager
    def folder(**kwargs):
        path = tmp_path / ("scratch" + str(len(list(tmp_path.iterdir()))))
        path.mkdir()
        yield str(path)
        # Observe the actual lifetime without changing owner-only permissions.
        import shutil
        shutil.rmtree(path)

    monkeypatch.setattr(confine, "run_folder", folder)
    _off(monkeypatch)
    monkeypatch.setattr(confine, "plan", lambda *a: confine.Plan(None, "switched off by this test"))
    monkeypatch.setattr(rt, "interpreter", lambda repo: sys.executable)


def test_non_git_folder_keeps_its_passing_in_place_result(tmp_path, monkeypatch, folders):
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "test_plain.py").write_text("def test_plain(): assert 1 + 1 == 2\n", encoding="utf-8")
    # Non-Git folders use the producer's existing copy execution path and its
    # own before/after snapshot. No repository validation policy is replaced.
    raw = rt._run(repo, ["test_plain.py::test_plain"], roots=(), timeout_s=30,
                  network=False, copied=True, python=sys.executable, scratch_dir=tmp_path)
    assert raw["verdict"] == "passed", raw
    assert raw["python_sources"] == ["test_plain.py"]

    def no_second_listing(*args, **kwargs):
        raise AssertionError("ownership must use the pre-run snapshot")
    monkeypatch.setattr(rt, "_git", no_second_listing)
    monkeypatch.setattr(rt, "_copy_snapshot", no_second_listing)
    assert rt.loaded_from(repo, raw) == {}
    assert rt.loaded_from(repo, {**raw, "loaded_outside": {"test_plain": "outside/test_plain.py"}}) != {}
    assert rt.loaded_from(repo, {**raw, "loaded_complete": False}) is None
    monkeypatch.setattr(bench, "_repo", lambda *a: repo)
    monkeypatch.setattr(rt, "run", lambda *a, **kw: raw)
    assert bench.run(bench.IN_PLACE, {"repo": "plain", "tests": ["test_plain.py::test_plain"]})["result"]["passed"] is True


def test_probe_ownership_is_decided_before_its_scratch_folder_is_removed(folders, monkeypatch):
    def probe(scratch):
        repo = scratch / "probe"
        repo.mkdir()
        return repo

    def listing(repo, *args, **kwargs):
        if not repo.is_dir():
            raise OSError("source folder has been removed")
        return b"tests/test_probe.py\0"

    # A completed producer report whose external modules do not shadow its
    # sources still needs the live source listing (legacy reports lack it).
    raw = rt._not_run_report("")
    raw.update(report(PASSED, loaded_outside={"pytest": "installed/pytest/__init__.py"}, loaded_complete=True))
    raw.update(passed=1, failed=0, not_passed=0, total=1, seconds=0.0, interpreter_source="g6")
    raw["test_outcomes"] = {bench.PROBE_TEST: PASSED}
    raw["collected_ids"] = [bench.PROBE_TEST]
    monkeypatch.setattr(rt, "probe_repo", probe)
    monkeypatch.setattr(rt, "run", lambda *a, **kw: raw)
    monkeypatch.setattr(rt, "_git", listing)
    reply = bench.run(bench.IN_PLACE, {"repo": bench.PROBE, "tests": [bench.PROBE_TEST]})
    assert reply["ok"] and reply["result"]["passed"] is True, reply


@pytest.mark.parametrize("outside,complete", [({"test_plain": "elsewhere/test_plain.py"}, True), ({}, False)])
def test_folder_listing_never_authorizes_outside_or_incomplete_loading(tmp_path, outside, complete):
    (tmp_path / "test_plain.py").write_text("def test_plain(): pass\n", encoding="utf-8")
    raw = report(PASSED, loaded_outside=outside, loaded_complete=complete)
    assert rt.loaded_from(tmp_path, raw) != {}


def test_a_folder_whose_listing_fails_has_unknown_ownership(tmp_path, monkeypatch):
    def unreadable(*args, **kwargs):
        raise OSError("listing unavailable")
    monkeypatch.setattr(rt, "_git", unreadable)
    monkeypatch.setattr(rt, "_copy_snapshot", unreadable)
    raw = report(PASSED, loaded_outside={"calc": "elsewhere/calc.py"}, loaded_complete=True)
    assert rt.loaded_from(tmp_path, raw) is None


def test_a_folder_snapshot_does_not_silently_treat_an_unreadable_directory_as_empty(tmp_path, monkeypatch):
    def walk(*args, onerror=None, **kwargs):
        if onerror:
            onerror(OSError("directory unreadable"))
        return iter(())
    monkeypatch.setattr(rt.os, "walk", walk)
    with pytest.raises(OSError, match="directory unreadable"):
        rt._copy_snapshot(tmp_path)


@pytest.mark.parametrize("wrong", [False, [False], ["../test_plain.py"], ["/test_plain.py"], ["x.py", "x.py"]])
def test_unreadable_or_malformed_source_listing_is_unknown(wrong):
    assert not rt.read_report(report(PASSED, python_sources=wrong)).valid
