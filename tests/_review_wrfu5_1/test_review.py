"""Synthetic disk review; recording seams only, no ACL or external calls."""
import ctypes
import errno
import importlib.util
import uuid
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location(
    "wrfu5_previous", HERE.parent / "_review_wrfu4_4/test_review.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
batch = previous.batch


@pytest.fixture
def tmp_path(request, monkeypatch):
    base = Path(request.config.getoption("basetemp"))
    try:
        base.mkdir(parents=True, exist_ok=True)
        folder = base / uuid.uuid4().hex
        folder.mkdir()
    except PermissionError:
        folder = HERE / "disk" / uuid.uuid4().hex
        folder.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(folder / "home"))
    monkeypatch.setenv("G6_LLM_PROVIDER", "none")
    for key in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(key, r"C:\nonexistent\g6-no-host.exe")
    return folder


# Re-run the retained capture with this review's permitted disk fixture.
for name, test in vars(previous).items():
    if name.startswith("test_"):
        globals()["test_previous_" + name.removeprefix("test_")] = test


def handles():
    kernel = rc._WindowsAPI().kernel
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    count = ctypes.c_ulong()
    assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
    return count.value


ERRORS = [
    (errno.EACCES, 32, "the origin folder is in use by another program"),
    (errno.EACCES, None, "access denied"),
    (errno.EMFILE, None, "too many open files"),
    (errno.ENFILE, None, "too many open files"),
    (errno.ENOENT, None, "a folder is missing"),
    (errno.ENOTDIR, None, "a path is not a folder"),
    (errno.EIO, None, "identity or directory read failed"),
]


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("route", ["invalid-origin", "older-rule"])
@pytest.mark.parametrize("recheck", [False, True], ids=["readable-root", "failed-root"])
@pytest.mark.parametrize("code,winerror,reason", ERRORS)
def test_enumeration_error_and_recheck_precedence(
        batch, monkeypatch, capsys, route, recheck, code, winerror, reason):
    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    else:
        batch.saved["evidence_rule_version"] = 0
    real = rc._WindowsAPI._open
    real_read = rc.Source.read_bytes
    failure = OSError(code, "recorded root error", str(batch.source))
    if winerror is not None:
        failure.winerror = winerror
    calls = []

    def opening(api, path, access):
        if path == batch.source and access == 0x81:
            calls.append(path)
            # A genuine first root-open failure, followed by the new recheck.
            if len(calls) == 1 or recheck:
                raise failure
        return real(api, path, access)

    def read(archive, rel):
        # Source's explicit missing-root fallback accepts a successful index
        # read. Keep that index unavailable until the diagnostic root recheck.
        if code == errno.ENOENT and len(calls) == 1 and archive.root == batch.source:
            raise failure
        return real_read(archive, rel)

    baseline = handles()
    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    monkeypatch.setattr(rc.Source, "read_bytes", read)
    status, result = previous.score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    assert result["index_excluded"][0]["source_unavailable"] == (
        "source folders could not be held: " + reason)
    assert len(calls) == 2
    assert handles() == baseline
    assert rc._ORIGIN_PINS.get() is None


@pytest.mark.parametrize("batch", [["agent"]], indirect=True)
@pytest.mark.parametrize("change", ["none", "missing", "file", "denied", "in-use"])
def test_root_changes_after_enumeration_refusal(batch, tmp_path, monkeypatch, capsys, change):
    batch.saved["evidence_rule_version"] = 0
    root = batch.source
    real_read = rc.Source.read_bytes
    real_open = rc._WindowsAPI._open
    changed = []

    def read(archive, rel):
        if archive.root == root and str(rel) == "runs.jsonl" and not changed:
            changed.append(True)
            if change in ("missing", "file"):
                root.rename(tmp_path / "parked-source")
                if change == "file":
                    root.write_bytes(b"synthetic non-folder root")
            raise ValueError("recorded enumeration refusal")
        return real_read(archive, rel)

    def opening(api, path, access):
        if changed and path == root and access == 0x81 and change in ("denied", "in-use"):
            error = PermissionError(errno.EACCES, "recorded root denial", str(path))
            if change == "in-use":
                error.winerror = 32
            raise error
        return real_open(api, path, access)

    monkeypatch.setattr(rc.Source, "read_bytes", read)
    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    baseline = handles()
    status, result = previous.score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    expected = {
        "none": "recorded enumeration refusal",
        "missing": "source folders could not be held: a folder is missing",
        "file": "source folders could not be held: a path is not a folder",
        "denied": "source folders could not be held: access denied",
        "in-use": "source folders could not be held: the origin folder is in use by another program",
    }[change]
    assert result["index_excluded"][0]["source_unavailable"] == expected
    assert changed == [True] and handles() == baseline


@pytest.mark.parametrize("route", ["index", "source", "pins", "walk", "saved"])
@pytest.mark.parametrize("component", [
    "NUL:stream", "NUL::$DATA", "NUL.tar.gz", "NUL .tar.gz", "nUl... ",
    "COM0", "LPT0", "COM\u00b9.tar.gz", "LPT\u00b2.. ", "AUX.txt...",
])
def test_device_and_stream_names_never_reach_open(tmp_path, monkeypatch, route, component):
    source = rc.Source(tmp_path)
    pins = rc._OriginPins(tmp_path)
    pins.root = rc._Opened(tmp_path, None, None)

    def unexpected(*args, **kwargs):
        pytest.fail("refused name reached a native open")

    monkeypatch.setattr(rc._WindowsAPI, "_open", unexpected)
    name = "run/synthetic/" + component
    if route == "saved":
        assert not rc._relative_evidence_name(name)
        assert rc._origin_run_identity(name, tmp_path) is None
    else:
        with pytest.raises(ValueError):
            if route == "index":
                rc._index_paths(source, {"event": "started", "kind": "run", "run": name})
            elif route == "source":
                source.read_bytes(name + "/final.patch")
            elif route == "pins":
                pins.path(name)
            else:
                rc.canonical_path(tmp_path / name)
