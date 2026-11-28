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
    assert gap["current_evidence_rule_version"] == rc.EVIDENCE_RULE_VERSION
    assert gap["saved_evidence_rule_version"] == batch.saved.get("evidence_rule_version")
    if route not in ("current", "current-refresh"):
        assert gap["reason"] == "saved under an older evidence rule"
    else:
        assert gap["reason"] != "saved under an older evidence rule"
        assert gap["reason"] == reason


@pytest.fixture
def windows_open_calls(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows handle access recording")
    calls = []
    real_open = rc._WindowsAPI.open
    real_directory = rc._WindowsAPI.open_directory

    def open_file(self, path, readable):
        calls.append(("open", rc._path_key(path), readable))
        return real_open(self, path, readable)

    def open_directory(self, path):
        calls.append(("directory", rc._path_key(path), None))
        return real_directory(self, path)

    monkeypatch.setattr(rc._WindowsAPI, "open", open_file)
    monkeypatch.setattr(rc._WindowsAPI, "open_directory", open_directory)
    return calls


def test_directory_pin_lists_only_its_last_component(tmp_path, windows_open_calls):
    folder = tmp_path / "ancestor" / "source"
    folder.mkdir(parents=True)
    with rc._open_handle(folder, directory=True, identify=True) as opened:
        assert opened.identity is not None
        assert rc._path_key(opened.path) == rc._path_key(folder)
    expected = [("open", rc._path_key(parent), False) for parent in reversed(folder.parents)]
    expected.append(("directory", rc._path_key(folder), None))
    assert windows_open_calls == expected


def test_source_score_lists_only_pinned_folders(batch, capsys, windows_open_calls):
    windows_open_calls.clear()
    status, result = trust.score(batch, capsys, True)
    assert status == 0 and result["evidence_source"] == "batch"
    assert result["predictions"] == batch.live["predictions"]
    source = rc._path_key(batch.source)
    pinned = {source}
    for planned in batch.saved["plan"]["cases"]:
        for origin in planned["origins"]:
            folder = rc._path_key(source / origin["run"])
            pinned.add(folder)
            pinned.update(parent for parent in folder.parents if parent.is_relative_to(source))
    directory_calls = {path for route, path, _ in windows_open_calls if route == "directory"}
    assert directory_calls == pinned
    for ancestor in source.parents:
        assert ("open", ancestor, False) in windows_open_calls
        assert all(route == "open" and readable is False
                   for route, path, readable in windows_open_calls if path == ancestor)


def test_file_read_requests_readable_access_only_for_its_last_component(tmp_path, windows_open_calls):
    folder = tmp_path / "ancestor" / "source"
    folder.mkdir(parents=True)
    content = b"synthetic content"
    target = folder / "payload.txt"
    target.write_bytes(content)
    source = rc.Source(folder)
    windows_open_calls.clear()
    assert source.read_bytes("payload.txt") == content
    assert [(path, readable) for route, path, readable in windows_open_calls if readable] == [
        (rc._path_key(target), True)]
    assert all(route == "open" and (readable is False or path == rc._path_key(target))
               for route, path, readable in windows_open_calls)
    for ancestor in target.parents:
        assert ("open", rc._path_key(ancestor), False) in windows_open_calls


@pytest.mark.parametrize("edit,value", [
    ("null", None), ("absent-field", None), ("integer", 7),
    ("list", []), ("object", {}), ("empty", ""),
    ("malformed", "../outside"), ("unrecorded", "run/black-5634/not-recorded"),
])
def test_invalid_saved_origin_recovers_from_source(batch, capsys, edit, value):
    origin = batch.saved["plan"]["cases"][0]["origins"][0]
    if edit == "absent-field":
        origin.pop("run")
    else:
        origin["run"] = value
    status, result = trust.score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (0, "source")
    for field in ("predictions", "by_bug", "excluded", "index_excluded"):
        assert result[field] == batch.live[field]


def test_invalid_saved_origin_keeps_fresh_source_failure(batch, capsys):
    planned = batch.saved["plan"]["cases"][0]
    (batch.source / planned["origins"][0]["run"] / "final.patch").write_bytes(b"changed patch")
    planned["origins"][0]["run"] = None
    status, result = trust.score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source identity, selection or evidence bytes changed")


@pytest.mark.parametrize("location", ["root", "origin"])
def test_nonfolder_pin_reports_errno_and_path(tmp_path, location):
    source = tmp_path / "source"
    if location == "origin":
        source.mkdir()
        target = source / "origin"
    else:
        target = source
    target.write_bytes(b"regular file")
    if location == "origin":
        with rc._open_handle(source, directory=True) as parent:
            with pytest.raises(NotADirectoryError) as caught:
                with rc._open_handle(target, directory=True, parent=parent):
                    pytest.fail("regular file was pinned as a folder")
    else:
        with pytest.raises(NotADirectoryError) as caught:
            with rc._open_handle(target, directory=True):
                pytest.fail("regular file was pinned as a folder")
    assert caught.value.errno == errno.ENOTDIR
    assert caught.value.filename == str(target)


@pytest.mark.parametrize("dry", [False, True], ids=["live", "dry"])
def test_file_root_keeps_replay_refusal_and_score_reason(batch, tmp_path, monkeypatch, capsys, dry):
    source = tmp_path / "file-source"
    source.write_bytes(b"regular file")
    output = tmp_path / "refused-output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    with pytest.raises(ValueError, match="^source root is missing or unreadable$"):
        rc.Source(source, required=True)
    calls = []
    status = rc.main(["--source", str(source), "--registered-commit", trust.COMMIT,
                      *(["--dry"] if dry else [])], docker=lambda *args, **kwargs: calls.append(args))
    captured = capsys.readouterr()
    assert status == 1
    assert captured.err == "replay_rc: source root is missing or unreadable\n"
    assert captured.out == "" and calls == [] and not output.exists()
    batch.source = source
    status, result = trust.score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: a path is not a folder")
    assert source.read_bytes() == b"regular file" and not output.exists()


def test_regular_file_origin_scores_nonfolder_reason(batch, capsys):
    origin = batch.source / batch.saved["plan"]["cases"][0]["origins"][0]["run"]
    moved = origin.with_name(origin.name + "-held")
    origin.rename(moved)
    try:
        origin.write_bytes(b"regular file")
        status, result = trust.score(batch, capsys, True)
    finally:
        if origin.is_file():
            origin.unlink()
        moved.rename(origin)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: a path is not a folder")


@pytest.mark.parametrize("location", ["root", "origin"])
def test_missing_recorded_folder_keeps_replay_and_score_reasons(batch, capsys, location):
    folder = (batch.source if location == "root" else
              batch.source / batch.saved["plan"]["cases"][0]["origins"][0]["run"])
    moved = folder.with_name(folder.name + "-held")
    folder.rename(moved)
    try:
        if location == "root":
            assert rc.main(["--source", str(folder), "--registered-commit", trust.COMMIT, "--dry"]) == 1
            captured = capsys.readouterr()
            assert captured.err == "replay_rc: source root is missing or unreadable\n"
            assert captured.out == ""
        status, result = trust.score(batch, capsys, True)
    finally:
        moved.rename(folder)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: a folder is missing")


@pytest.mark.skipif(os.name != "nt", reason="Windows directory share modes")
def test_in_use_recorded_origin_never_falls_through_to_refresh(batch, monkeypatch, capsys):
    name = batch.saved["plan"]["cases"][0]["origins"][0]["run"]
    api = rc._WindowsAPI()
    handle = api.kernel.CreateFileW(str(batch.source / name), 0x10001, 7, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()

    def unexpected(*args, **kwargs):
        pytest.fail("in-use origin fell through to source refresh")

    try:
        monkeypatch.setattr(rc, "enumerate_cases", unexpected)
        status, result = trust.score(batch, capsys, True)
    finally:
        assert api.kernel.CloseHandle(handle), ctypes.get_last_error()
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: the origin folder is in use by another program")


DEVICE_NAMES = ["CON", "PRN", "AUX", "NUL", *[f"COM{n}" for n in "0123456789¹²³"],
                *[f"LPT{n}" for n in "0123456789¹²³"]]


@pytest.mark.skipif(os.name != "nt", reason="Windows saved device-name gate")
@pytest.mark.parametrize("device", DEVICE_NAMES)
def test_saved_device_forms_refused_before_any_open(tmp_path, monkeypatch, device):
    def unexpected(*args, **kwargs):
        pytest.fail("saved device metadata reached an open")

    monkeypatch.setattr(rc._WindowsAPI, "_open", unexpected)
    for spelling in (device, device.lower(), device.title()):
        for extension in ("", ".txt", ".tar.gz", " .txt"):
            for tail in ("", ".", " ", ". ", "..  "):
                component = spelling + extension + tail
                for name in (component, f"run/{component}/origin", f"run/synthetic/{component}"):
                    assert not rc._relative_evidence_name(name), name
                    assert rc._origin_run_identity(name, tmp_path) is None, name
                    assert not rc._unique_origin_digests([case(name)], source=tmp_path), name


@pytest.mark.skipif(os.name != "nt", reason="Windows saved device-name recovery")
@pytest.mark.parametrize("device", DEVICE_NAMES)
def test_saved_device_origin_refreshes_without_device_open(batch, monkeypatch, capsys, device):
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = f"run/black-5634/{device}"
    real = rc._WindowsAPI._open

    def opening(api, path, access):
        assert path.name.casefold() != device.casefold(), "saved device reached a native open"
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    status, result = trust.score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (0, "source")
    for field in ("predictions", "by_bug", "excluded", "index_excluded"):
        assert result[field] == batch.live[field]


@pytest.mark.skipif(os.name == "nt", reason="POSIX device names are ordinary folders")
@pytest.mark.parametrize("device", DEVICE_NAMES)
def test_posix_saved_device_names_remain_ordinary(tmp_path, device):
    for component in (device, device.lower() + ".txt"):
        name = f"run/synthetic/{component}"
        (tmp_path / name).mkdir(parents=True)
        assert rc._relative_evidence_name(name)
        assert rc._origin_run_identity(name) == name
        assert rc._unique_origin_digests([case(name)], source=tmp_path)


@pytest.mark.parametrize("route", ["current", "invalid-origin", "older-rule"])
@pytest.mark.parametrize("kind,code,reason", [
    ("in-use", errno.EACCES, "the origin folder is in use by another program"),
    ("access", errno.EACCES, "access denied"),
    ("descriptors", errno.EMFILE, "too many open files"),
    ("system-descriptors", errno.ENFILE, "too many open files"),
    ("missing", errno.ENOENT, "a folder is missing"),
    ("nonfolder", errno.ENOTDIR, "a path is not a folder"),
    ("read", errno.EIO, "identity or directory read failed"),
])
def test_root_hold_score_reason_and_replay_refusal(batch, tmp_path, monkeypatch, capsys, route, kind, code, reason):
    from contextlib import contextmanager

    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    elif route == "older-rule":
        batch.saved["evidence_rule_version"] = 0
    real = rc._open_handle
    failure = OSError(code, "synthetic root hold failure", str(batch.source))
    if kind == "in-use":
        failure.winerror = 32
    if kind == "missing":
        batch.source = tmp_path / "missing-source"

    @contextmanager
    def opening(path, **kwargs):
        if kind != "missing" and path == batch.source and kwargs.get("directory"):
            raise failure
        with real(path, **kwargs) as opened:
            yield opened

    monkeypatch.setattr(rc, "_open_handle", opening)
    output = tmp_path / "refused-replay-output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    with pytest.raises(ValueError, match="^source root is missing or unreadable$") as caught:
        rc.Source(batch.source, required=True)
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.__cause__.errno == code
    if kind == "in-use":
        assert caught.value.__cause__.winerror == 32

    def unexpected(*args, **kwargs):
        pytest.fail("unheld source reached a process")

    assert rc.main(["--source", str(batch.source), "--registered-commit", trust.COMMIT], docker=unexpected) == 1
    captured = capsys.readouterr()
    assert captured.err == "replay_rc: source root is missing or unreadable\n"
    assert captured.out == "" and not output.exists()
    status, result = trust.score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    gap, = result["index_excluded"]
    assert gap["source_unavailable"] == "source folders could not be held: " + reason
    assert gap["reason"] == ("saved under an older evidence rule" if route == "older-rule"
                             else gap["source_unavailable"])
    assert not output.exists()


@pytest.mark.parametrize("route", ["current", "invalid-origin", "older-rule"])
def test_file_ancestor_keeps_replay_refusal_and_score_reason(batch, tmp_path, monkeypatch, capsys, route):
    file = tmp_path / "file-ancestor"
    file.write_bytes(b"regular file")
    batch.source = file / "source"
    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    elif route == "older-rule":
        batch.saved["evidence_rule_version"] = 0
    with pytest.raises(NotADirectoryError) as caught:
        with rc._open_handle(batch.source, directory=True):
            pytest.fail("file ancestor was walked as a directory")
    assert caught.value.errno == errno.ENOTDIR
    assert caught.value.filename in (str(file), str(batch.source))
    output = tmp_path / "refused-ancestor-output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    with pytest.raises(ValueError, match="^source root is missing or unreadable$"):
        rc.Source(batch.source, required=True)
    assert rc.main(["--source", str(batch.source), "--registered-commit", trust.COMMIT, "--dry"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "replay_rc: source root is missing or unreadable\n"
    assert captured.out == "" and not output.exists()
    status, result = trust.score(batch, capsys, True)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: a path is not a folder")
    assert file.read_bytes() == b"regular file" and not output.exists()


def test_recorded_origin_file_ancestor_scores_nonfolder(batch, capsys):
    name = batch.saved["plan"]["cases"][0]["origins"][0]["run"]
    ancestor = (batch.source / name).parent
    moved = ancestor.with_name(ancestor.name + "-held")
    ancestor.rename(moved)
    try:
        ancestor.write_bytes(b"regular file")
        status, result = trust.score(batch, capsys, True)
    finally:
        if ancestor.is_file():
            ancestor.unlink()
        moved.rename(ancestor)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: a path is not a folder")


@pytest.mark.skipif(os.name != "nt", reason="Windows archive containment refusal")
def test_archive_file_ancestor_keeps_forbidden_path_text(tmp_path):
    file = tmp_path / "file-ancestor"
    file.write_bytes(b"regular file")
    with pytest.raises(ValueError, match="^forbidden archive path:"):
        rc.canonical_path(file / "run.json")
    with pytest.raises(ValueError, match="^forbidden archive path:"):
        rc.Source(tmp_path).read_bytes("file-ancestor/payload.txt")
