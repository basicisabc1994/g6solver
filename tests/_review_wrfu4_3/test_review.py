"""Review 3: synthetic disk fixtures, recording access seams, no ACL changes."""
import ctypes
import errno
import importlib.util
import json
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "wrfu4_3_synthetic", HERE.parent / "_review_wrfu3_2/test_review.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)
batch = synthetic.batch
tmp_path = synthetic.tmp_path


def score(batch, capsys):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    status = rc.main(["--score", str(batch.path), "--source", str(batch.source)])
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return status, json.loads(captured.out)


@pytest.mark.parametrize("batch", [["hook"]], indirect=True, ids=["hook"])
@pytest.mark.parametrize("route", ["valid-control", "invalid-origin", "older-rule"])
@pytest.mark.parametrize("kind,reason", [
    ("in-use", "the origin folder is in use by another program"),
    ("denied", "access denied"),
    ("descriptors", "too many open files"),
], ids=["in-use", "denied", "descriptors"])
def test_root_hold_reason_survives_refresh(batch, capsys, monkeypatch, route, kind, reason):
    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    elif route == "older-rule":
        batch.saved["evidence_rule_version"] = 0
    real = rc._WindowsAPI._open
    calls = []

    def opening(api, path, access):
        if path == batch.source and access == 0x81:
            calls.append(path)
            if kind == "in-use":
                raise ctypes.WinError(32)
            if kind == "denied":
                raise PermissionError(errno.EACCES, "recorded root access denial", str(path))
            raise OSError(errno.EMFILE, "recorded descriptor exhaustion", str(path))
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    status, result = score(batch, capsys)
    assert calls
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: " + reason)


@pytest.mark.parametrize("batch", [["hook"]], indirect=True, ids=["hook"])
@pytest.mark.parametrize("location", ["root-control", "ancestor"])
def test_file_component_has_nonfolder_reason(batch, tmp_path, capsys, location):
    file = tmp_path / "not-a-folder"
    file.write_bytes(b"synthetic regular file")
    batch.source = file if location == "root-control" else file / "source"
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: a path is not a folder")


@pytest.mark.parametrize("batch", [["hook"]], indirect=True, ids=["hook"])
@pytest.mark.parametrize("name", ["not-recorded", "NUL", "CON", "COM1"])
def test_unusable_device_origin_recovers_as_metadata(batch, capsys, monkeypatch, name):
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = "run/black-5634/" + name
    real = rc._WindowsAPI._open
    device_calls = []

    def opening(api, path, access):
        if path.name in {"NUL", "CON", "COM1"}:
            device_calls.append(str(path))
            # Record and refuse rather than contacting a real console or port.
            raise OSError(errno.ENOTDIR, "recorded non-folder device", str(path))
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (0, "source"), (device_calls, result["index_excluded"])
    assert not device_calls


@pytest.mark.parametrize("batch", [["hook"]], indirect=True, ids=["hook"])
@pytest.mark.parametrize("name", ["not-recorded", "NUL"], ids=["absent-control", "real-device"])
def test_real_nul_origin_is_metadata_not_a_source_folder(batch, capsys, monkeypatch, name):
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = "run/black-5634/" + name
    real = rc._WindowsAPI._open
    opened_types = []

    def opening(api, path, access):
        handle = real(api, path, access)
        if path.name == "NUL":
            opened_types.append(api.kernel.GetFileType(handle))
        return handle

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"], opened_types) == (0, "source", []), result["index_excluded"]


@pytest.mark.parametrize("batch", [["hook"]], indirect=True, ids=["hook"])
@pytest.mark.parametrize("invalid", [False, True], ids=["valid-control", "invalid-origin"])
def test_real_in_use_root_reason_is_retained(batch, capsys, invalid):
    if invalid:
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    api = rc._WindowsAPI()
    handle = api.kernel.CreateFileW(str(batch.source), 0x10001, 7, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        status, result = score(batch, capsys)
    finally:
        assert api.kernel.CloseHandle(handle)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: the origin folder is in use by another program")
