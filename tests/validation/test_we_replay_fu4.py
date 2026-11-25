# SPDX-License-Identifier: Apache-2.0
"""Follow-up 4: shared source pins and honest source-unavailable reasons."""
import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

_spec = importlib.util.spec_from_file_location(
    "fu4_trust", Path(__file__).with_name("test_we_replay_saved_trust.py"))
trust = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trust)
batch = trust.batch


def case(name, digest="a"):
    return SimpleNamespace(origins=[{"run": name}], digest=digest * 64)


def open_count():
    if os.name != "nt":
        return len(os.listdir("/proc/self/fd"))
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetProcessHandleCount.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    count = w.DWORD()
    assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
    return count.value


def test_shared_ancestors_fit_a_folder_budget(tmp_path, monkeypatch):
    names = [f"run/synthetic/folder-{number}" for number in range(48)]
    for name in names:
        (tmp_path / name).mkdir(parents=True)
    baseline, peak = open_count(), []
    original = rc._origin_run_identity

    def identify(run, source=None):
        identity = original(run, source)
        peak.append(open_count() - baseline)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    assert rc._unique_origin_digests([case(name) for name in names], source=tmp_path)
    assert max(peak) <= len(names) + len(tmp_path.parts) + 8
    assert abs(open_count() - baseline) <= 3


@pytest.fixture
def source_drive():
    if os.name != "nt":
        pytest.skip("Windows temporary DOS drive mapping")
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.DefineDosDeviceW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR]
    kernel.DefineDosDeviceW.restype = w.BOOL
    kernel.QueryDosDeviceW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
    kernel.QueryDosDeviceW.restype = w.DWORD
    occupied = kernel.GetLogicalDrives()
    buffer = ctypes.create_unicode_buffer(32768)
    letter = next((letter + ":" for letter in "ZYXWVUTSRQPONMLKJIHG"
                   if not occupied & (1 << (ord(letter) - 65))
                   and not kernel.QueryDosDeviceW(letter + ":", buffer, len(buffer))), None)
    if letter is None:
        pytest.skip("no unused drive letter")
    target = None

    def point(path):
        nonlocal target
        if target is not None:
            assert kernel.DefineDosDeviceW(2 | 4 | 8, letter, target), ctypes.get_last_error()
            target = None
        assert kernel.DefineDosDeviceW(8, letter, str(path)), ctypes.get_last_error()
        target = str(path)
        return Path(letter + "\\source")

    try:
        yield point
    finally:
        if target is not None:
            assert kernel.DefineDosDeviceW(2 | 4 | 8, letter, target), ctypes.get_last_error()


def test_repointed_drive_uses_one_pinned_root(tmp_path, monkeypatch, source_drive):
    name, alias = "run/synthetic/Origin", "run/synthetic/origin"
    first, second = tmp_path / "first", tmp_path / "second"
    for tree in (first, second):
        (tree / "source" / name).mkdir(parents=True)
    source = source_drive(first)
    original, reads = rc._origin_run_identity, []

    def identify(run, root=None):
        identity = original(run, root)
        reads.append(identity)
        if len(reads) == 1:
            source_drive(second)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    assert not rc._unique_origin_digests([case(name), case(alias, "b")], source=source)
    assert len(reads) == 2 and None not in reads and reads[0] == reads[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows rename sharing")
@pytest.mark.parametrize("level", ["origin", "parent", "source"])
def test_directory_pins_still_refuse_renames(tmp_path, monkeypatch, level):
    source = tmp_path / "source"
    name = "run/synthetic/Origin"
    folder = source / name
    folder.mkdir(parents=True)
    target = {"origin": folder, "parent": folder.parent, "source": source}[level]
    original = rc._origin_run_identity

    def identify(run, root=None):
        identity = original(run, root)
        assert identity is not None
        with pytest.raises(PermissionError):
            target.rename(tmp_path / "during-comparison")
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    assert rc._unique_origin_digests([case(name)], source=source)
    target.rename(tmp_path / "after-comparison")


@pytest.mark.parametrize("old", [False, True], ids=["current", "older"])
@pytest.mark.parametrize("error,reason", [
    (errno.EMFILE, "source folders could not be held: too many open files"),
    (errno.EACCES, "source folders could not be held: access denied"),
    (errno.ENOENT, "source folders could not be held: a folder is missing"),
    (errno.ENOTDIR, "source folders could not be held: a path is not a folder"),
    (errno.EIO, "source folders could not be held: identity or directory read failed"),
], ids=["descriptors", "access", "missing", "file", "read"])
def test_pin_failure_scores_its_own_reason(batch, monkeypatch, capsys, old, error, reason):
    held_status, held = trust.score(batch, capsys, True)
    assert held_status == 0 and held["evidence_source"] == "batch"
    if old:
        batch.saved["evidence_rule_version"] = 0

    def unavailable(*args, **kwargs):
        raise OSError(error, "synthetic pin failure")

    if os.name == "nt":
        monkeypatch.setattr(rc._WindowsAPI, "file_identity", unavailable)
        status, result = trust.score(batch, capsys, True)
    else:
        real = rc.os.fstat

        def fstat(fd):
            if rc._ORIGIN_PINS.get() is not None:
                return unavailable()
            return real(fd)

        with monkeypatch.context() as scoped:
            scoped.setattr(rc.os, "fstat", fstat)
            status, result = trust.score(batch, capsys, True)
    assert status == 2 and result["evidence_source"] == "unavailable"
    assert result["index_excluded"][0]["source_unavailable"] == reason
    assert all(result["predictions"][key]["status"] == "incomplete" for key in ("P1", "P2", "P3"))
    for key in ("P4", "P5", "P6", "P7"):
        assert result["predictions"][key]["status"] == held["predictions"][key]["status"] == "disabled"


@pytest.mark.skipif(os.name == "nt", reason="POSIX root open flags")
@pytest.mark.parametrize("link", [False, True], ids=["file", "link"])
def test_posix_file_and_link_roots_keep_distinct_refusals(tmp_path, link):
    source = tmp_path / "source"
    target = tmp_path / "target"
    target.write_bytes(b"synthetic file")
    if link:
        source.symlink_to(target)
    else:
        source.write_bytes(b"synthetic file")
    try:
        reason = "archive links are refused" if link else "source root is missing or unreadable"
        with pytest.raises(ValueError, match=reason):
            rc.Source(source, required=True)
    finally:
        if link:
            source.unlink()


@pytest.mark.skipif(os.name == "nt", reason="POSIX scoped fstat failure")
def test_unreadable_folder_identity_is_unbound_scoped(tmp_path, monkeypatch):
    name = "run/synthetic/folder"
    (tmp_path / name).mkdir(parents=True)

    def unreadable(*args, **kwargs):
        raise PermissionError("synthetic unreadable folder")

    with monkeypatch.context() as scoped:
        scoped.setattr(rc.os, "fstat", unreadable)
        accepted = rc._unique_origin_digests([case(name)], source=tmp_path)
    assert not accepted
    assert rc._ORIGIN_PINS.get() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows directory share modes")
@pytest.mark.parametrize("old", [False, True], ids=["current", "older"])
def test_delete_access_handle_scores_honest_incomplete(batch, capsys, old):
    from ctypes import wintypes as w

    held_status, held = trust.score(batch, capsys, True)
    assert held_status == 0 and held["evidence_source"] == "batch"
    if old:
        batch.saved["evidence_rule_version"] = 0
    name = batch.saved["plan"]["cases"][0]["origins"][0]["run"]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateFileW(str(batch.source / name), 0x10001 | 0x100000, 7, None, 3, 0x02000000, None)
    assert handle != w.HANDLE(-1).value, ctypes.get_last_error()
    try:
        assert not rc._unique_origin_digests([case(name)], source=batch.source)
        batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
        status = rc.main(["--score", str(batch.path), "--source", str(batch.source)])
        result = json.loads(capsys.readouterr().out)
    finally:
        assert kernel.CloseHandle(handle), ctypes.get_last_error()
    assert status == 2 and result["evidence_source"] == "unavailable"
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: the origin folder is in use by another program")
    assert all(result["predictions"][key]["status"] == "incomplete" for key in ("P1", "P2", "P3"))
    for key in ("P4", "P5", "P6", "P7"):
        assert result["predictions"][key]["status"] == held["predictions"][key]["status"] == "disabled"


@pytest.mark.skipif(os.name != "nt", reason="Windows open hook and network spelling")
@pytest.mark.parametrize("source", [False, True], ids=["batch-only", "explicit-source"])
@pytest.mark.parametrize("old", [False, True], ids=["current", "older"])
def test_score_keeps_open_seam_and_ignores_batch_network_root(batch, monkeypatch, capsys, old, source):
    if old:
        batch.saved["evidence_rule_version"] = 0
    batch.saved["plan"]["source_snapshot"]["root"] = r"\\synthetic-no-host.invalid\share\source"
    real_init, real_open = rc._WindowsAPI.__init__, rc._WindowsAPI.open
    opened = []

    def init(self):
        real_init(self)
        create = self.kernel.CreateFileW

        def local_only(path, *args):
            # Stop before Windows sees a share on either ordinary or pin opens.
            assert not str(path).startswith("\\\\"), "batch data reached a Windows network open"
            opened.append((str(path), args[0]))
            return create(path, *args)

        self.kernel.CreateFileW = local_only

    def recording(self, path, readable):
        return real_open(self, path, readable)

    monkeypatch.setattr(rc._WindowsAPI, "__init__", init)
    monkeypatch.setattr(rc._WindowsAPI, "open", recording)
    status, result = trust.score(batch, capsys, source)
    assert opened
    assert status == (0 if source else 2)
    assert result["evidence_source"] == ("source" if source else "unavailable")
    if source:
        assert any(access == 0x81 for _, access in opened)
        assert result["predictions"] == batch.live["predictions"]


@pytest.mark.parametrize("route", [
    "current", "current-refresh", "older", "absent", "invalid", "boolean",
    "absent-no-source", "string-no-source",
])
def test_pin_failure_exclusion_names_the_actual_rule_and_cause(batch, monkeypatch, capsys, route):
    source = not route.endswith("-no-source")
    reason = ("source folders could not be held: access denied" if source
              else "--source is required to re-derive saved evidence")
    if route == "older":
        batch.saved["evidence_rule_version"] = 0
    elif route in ("absent", "absent-no-source"):
        batch.saved.pop("evidence_rule_version")
    elif route in ("invalid", "string-no-source", "boolean"):
        batch.saved["evidence_rule_version"] = True if route == "boolean" else "1"
    elif route == "current-refresh":
        # Reject saved structure before identity lookup, then translate its
        # legacy root as data and reach the source-refresh comparison instead.
        batch.saved["plan"]["source_snapshot"]["root"] = "synthetic-unused-root"

    def unavailable(*args, **kwargs):
        raise PermissionError(errno.EACCES, "synthetic pin failure")

    with monkeypatch.context() as scoped:
        if os.name == "nt":
            scoped.setattr(rc._WindowsAPI, "file_identity", unavailable)
        else:
            real = rc.os.fstat

            def fstat(fd):
                if rc._ORIGIN_PINS.get() is not None:
                    return unavailable()
                return real(fd)

            scoped.setattr(rc.os, "fstat", fstat)
        status, result = trust.score(batch, capsys, source)
    assert status == 2 and result["evidence_source"] == "unavailable"
    gap, = result["index_excluded"]
    assert gap["source_unavailable"] == reason
    assert gap["current_evidence_rule_version"] == 1
    assert gap["saved_evidence_rule_version"] == batch.saved.get("evidence_rule_version")
    if route not in ("current", "current-refresh"):
        assert gap["reason"] == "saved under an older evidence rule"
    else:
        assert gap["reason"] != "saved under an older evidence rule"
        assert gap["reason"] == reason
