"""Review 4: synthetic archives on real Windows disk; no Docker or model calls."""
import ctypes
import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "review4_shared", Path(__file__).parents[1] / "_review_wrfu2fu3/test_review.py")
shared = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shared)
rc = shared.rc
build = shared.build
tmp_path = shared.tmp_path


def set_case_sensitive(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint,
                                  ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.SetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                ctypes.c_void_p, ctypes.c_uint]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x100, 3, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        flag = ctypes.c_uint(1)
        if not kernel.SetFileInformationByHandle(handle, 23, ctypes.byref(flag), 4):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def short_path(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    buffer = ctypes.create_unicode_buffer(32768)
    count = kernel.GetShortPathNameW(str(path), buffer, len(buffer))
    assert 0 < count < len(buffer), ctypes.get_last_error()
    return Path(buffer.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory identities")
@pytest.mark.parametrize("refresh", ["batch", "current-source", "old-source"])
@pytest.mark.parametrize("distinct_case", [False, True], ids=["control", "case-sensitive"])
def test_honest_distinct_folders_rescore(build, tmp_path, monkeypatch, capsys,
                                       distinct_case, refresh):
    if distinct_case:
        parent = tmp_path / "source/run/black-5634"
        parent.mkdir(parents=True)
        set_case_sensitive(parent)
    original = build()
    if distinct_case:
        right, wrong = original.runs
        alias = right.path.with_name(right.path.name.lower())
        assert str(alias) != str(right.path)
        wrong.path.rename(alias)
        assert not right.path.samefile(alias)
        index = original.source / "runs.jsonl"
        replacement = alias.relative_to(original.source).as_posix()
        index.write_text(index.read_text(encoding="utf-8").replace(wrong.rel, replacement),
                         encoding="utf-8")
    # Replay the valid source afresh. No edits to this batch, plan, or responses.
    output = tmp_path / "fresh-output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(original.source), "--registered-commit", shared.COMMIT,
                    "--mode", "agent", "--s0", "after"], docker=shared.ReplyStub()) == 0
    live = json.loads(capsys.readouterr().out)
    assert live["predictions"]["P2"]["status"] == "met"
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    if refresh == "old-source":
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["evidence_rule_version"] = 0
        path.write_text(json.dumps(saved), encoding="utf-8")
    argv = ["--score", str(path)]
    if refresh != "batch":
        argv += ["--source", str(original.source)]
    status = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    result = json.loads(captured.out)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    assert observed == (0, "source" if refresh == "old-source" else "batch", "met"), observed


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
@pytest.mark.parametrize("alias", [False, True], ids=["control-exact", "short-alias"])
def test_short_alias_cannot_bind_two_patches_without_source(build, capsys, alias):
    batch = build()
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    run = wrong["origin_run"]
    if alias:
        folder = batch.source / run
        short_name = short_path(folder).name
        assert "~" in short_name, "8.3 names must exist for this reproduction"
        run = run.rsplit("/", 1)[0] + "/" + short_name
        assert run != wrong["origin_run"]
        assert folder.samefile(batch.source / run)
        # The source index validator refuses this spelling; it is not writer evidence.
        with pytest.raises(ValueError, match="invalid run path"):
            rc._index_paths(rc.Source(batch.source), {"run": run, "kind": "run"})
    case["origins"][0]["run"] = right["origin_run"] = run
    # Keep arguments, evaluation, source manifest and batch start exactly as saved.
    status, result = shared.score(batch, capsys)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    # With source access the new identity gate does detect the same-folder conflict.
    _, checked = shared.score(batch, capsys, source=True)
    assert checked["predictions"]["P2"]["status"] == "incomplete"
    assert observed == (2, "unavailable", "incomplete"), observed


@pytest.mark.skipif(os.name != "nt", reason="Windows short names")
@pytest.mark.parametrize("missing", [False, True], ids=["control-present", "folder-gone"])
def test_unresolved_folder_identity_is_not_checked_evidence(build, tmp_path, capsys, missing):
    batch = build()
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    wrong = next(r for r in batch.saved["records"] if r["label"] == "wrong")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    folder = batch.source / wrong["origin_run"]
    alias = wrong["origin_run"].rsplit("/", 1)[0] + "/" + short_path(folder).name
    assert "~" in alias and folder.samefile(batch.source / alias)
    case["origins"][0]["run"] = right["origin_run"] = alias
    if missing:
        moved = tmp_path / "removed-run"
        assert folder.resolve().is_relative_to(tmp_path.resolve())
        folder.rename(moved)
        assert not folder.exists() and not (batch.source / alias).exists()
    status, result = shared.score(batch, capsys, source=True)
    observed = status, result["evidence_source"], result["predictions"]["P2"]["status"]
    assert result["predictions"]["P2"]["status"] == "incomplete", observed
