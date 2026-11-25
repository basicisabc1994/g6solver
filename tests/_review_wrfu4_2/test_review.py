"""Review 2: real local fixtures, recording seams, no ACL/transport calls.

Windows denied pytest's normal basetemp. The local tmp_path fixture therefore
uses real disk below this review directory, as the review request permits.
The imported fixture builds every archive afresh and uses a recording transport
which runs the reviewer's input validation without executing a review or Docker.
"""
import ctypes
import errno
import importlib.util
import json
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "wrfu4_2_synthetic", HERE.parent / "_review_wrfu3_2/test_review.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)
batch = synthetic.batch
pytestmark = pytest.mark.skipif(os.name != "nt", reason="reviewed on Windows")


@pytest.fixture
def tmp_path(monkeypatch):
    folder = HERE / "disk" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    for key in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(key, r"C:\nonexistent\g6-no-host.exe")
    return folder


def case(name, digest="a"):
    return SimpleNamespace(origins=[{"run": name}], digest=digest * 64)


def parent_module():
    name = "worked_examples._wrfu4_2_parent"
    if name not in sys.modules:
        source = subprocess.run(
            ["git", "show", "cb7dd9de:validation/worked_examples/replay_rc.py"],
            cwd=HERE, capture_output=True, check=True).stdout.decode("utf-8")
        module = types.ModuleType(name)
        module.__package__, module.__file__ = "worked_examples", rc.__file__
        sys.modules[name] = module
        exec(compile(source, "replay_rc@cb7dd9de", "exec"), module.__dict__)
    return sys.modules[name]


def score(batch, capsys, source=True, module=rc):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    status = module.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return status, json.loads(captured.out)


# Finding 1: malformed saved origins are treated as failures of the real source.
@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("revision", ["parent-control", "head"])
@pytest.mark.parametrize("name", [None, "run/black-5634/not-recorded"],
                         ids=["null-origin", "absent-origin"])
def test_current_bad_origin_recovers_from_unchanged_source(
        batch, capsys, monkeypatch, revision, name):
    module = parent_module() if revision == "parent-control" else rc
    monkeypatch.setattr(module, "load_registration", rc.load_registration)
    status, original = score(batch, capsys, module=module)
    assert (status, original["evidence_source"]) == (0, "batch")
    expected = original["predictions"]
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = name
    # Untrusted plan metadata changed; every byte of the source is unchanged.
    status, without = score(batch, capsys, source=False, module=module)
    assert (status, without["evidence_source"]) == (
        (2, "unavailable") if name is None else (0, "batch"))
    status, result = score(batch, capsys, module=module)
    observed = (status, result["evidence_source"], result["predictions"]["P3"]["status"])
    assert observed == (0, "source", "met"), (observed, result["index_excluded"])
    assert result["predictions"] == expected


# Finding 2: a real non-folder root loses the dedicated non-folder reason.
@pytest.mark.parametrize("location", ["origin-control", "source-root"])
def test_nonfolder_pin_has_nonfolder_reason(tmp_path, location):
    root, name = tmp_path / "source", "run/synthetic/Origin"
    if location == "source-root":
        root.write_bytes(b"a real regular file, not a folder")
    else:
        (root / name).parent.mkdir(parents=True)
        (root / name).write_bytes(b"a real regular file, not a folder")
    failure = []
    assert not rc._unique_origin_digests([case(name)], source=root, failure=failure)
    assert failure == ["source folders could not be held: a path is not a folder"]


# Passing controls for repair 1 and shared-pin lifecycle.
@pytest.mark.parametrize("route", ["pin", "read", "comparison"])
def test_traverse_ancestors_need_no_listing(tmp_path, monkeypatch, route):
    root = tmp_path / "traverse" / "source"
    name = "run/synthetic/Origin"
    (root / name).mkdir(parents=True)
    (root / "payload.txt").write_bytes(b"fixture")
    real, calls = rc._WindowsAPI._open, []

    def opening(api, path, access):
        calls.append((path, access))
        if path in root.parents and access & 1:
            raise PermissionError(errno.EACCES, "recorded ancestor denies listing")
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    if route == "pin":
        with rc._open_handle(root, directory=True, identify=True) as opened:
            assert opened.identity is not None
    elif route == "read":
        assert rc.Source(root).read_bytes("payload.txt") == b"fixture"
    else:
        assert rc._unique_origin_digests([case(name)], source=root)
    assert calls
    assert all(access == 0x80 for path, access in calls if path in root.parents)


@pytest.mark.parametrize("alias", ["case", "short"])
@pytest.mark.parametrize("conflict", [False, True], ids=["same-digest", "conflict"])
def test_aliases_keep_shared_pins_alive(tmp_path, monkeypatch, alias, conflict):
    name = "run/synthetic/LongOriginFolder"
    (tmp_path / name).mkdir(parents=True)
    if alias == "case":
        other = name.swapcase()
    else:
        kernel = rc._WindowsAPI().kernel
        kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
        kernel.GetShortPathNameW.restype = ctypes.c_ulong
        buffer = ctypes.create_unicode_buffer(32768)
        assert kernel.GetShortPathNameW(str(tmp_path / name), buffer, len(buffer))
        short = Path(buffer.value).name
        if short == Path(name).name:
            pytest.skip("this volume did not assign a DOS short name")
        other = "run/synthetic/" + short
    assert (tmp_path / name).samefile(tmp_path / other)
    real, identities = rc._origin_run_identity, []

    def identify(run, source=None):
        identity = real(run, source)
        pins = rc._ORIGIN_PINS.get()
        assert identity is not None
        # Every shared retained handle remains usable during the second lookup.
        for opened in pins.distinct.values():
            rc._WindowsAPI().describe(opened.fd, opened.path)
        identities.append(identity)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    assert rc._unique_origin_digests(
        [case(name), case(other, "b" if conflict else "a")],
        source=Path("\\\\?\\" + str(tmp_path))) is (not conflict)
    assert len(identities) == 2 and identities[0] == identities[1]
    (tmp_path / name).rename(tmp_path / "released")


def handle_count():
    kernel = rc._WindowsAPI().kernel
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    count = ctypes.c_ulong()
    assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
    return count.value


@pytest.mark.parametrize("level", ["origin", "source", "ancestor"])
def test_pins_block_rename_until_comparison_finishes(tmp_path, monkeypatch, level):
    root = tmp_path / "ancestor" / "source"
    name = "run/synthetic/Origin"
    (root / name).mkdir(parents=True)
    target = {"origin": root / name, "source": root, "ancestor": root.parent}[level]
    real = rc._origin_run_identity
    attempts = []

    def identify(run, source=None):
        identity = real(run, source)
        assert identity is not None
        with pytest.raises(PermissionError):
            target.rename(tmp_path / "moved")
        attempts.append(True)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    assert rc._unique_origin_digests([case(name)], source=root)
    assert attempts == [True]
    target.rename(tmp_path / "moved")


def test_content_descriptor_exhaustion_closes_native_handles(tmp_path, monkeypatch):
    import msvcrt

    (tmp_path / "payload.txt").write_bytes(b"fixture")
    source = rc.Source(tmp_path)
    assert source.read_bytes("payload.txt") == b"fixture"

    def exhausted(*args):
        raise OSError(errno.EMFILE, "recorded CRT descriptor exhaustion")

    before = handle_count()
    with monkeypatch.context() as scoped:
        scoped.setattr(msvcrt, "open_osfhandle", exhausted)
        with pytest.raises(OSError) as caught:
            source.read_bytes("payload.txt")
    assert caught.value.errno == errno.EMFILE
    assert handle_count() == before


@pytest.mark.parametrize("where", ["ancestor", "root", "root-recheck", "leaf", "later", "identity", "folder-key"])
def test_exhaustion_releases_every_handle(tmp_path, monkeypatch, where):
    root = tmp_path / "source"
    names = ["run/synthetic/one", "run/synthetic/two"]
    for name in names:
        (root / name).mkdir(parents=True)
    cases = [case(name) for name in names]
    assert rc._unique_origin_digests(cases, source=root)
    real_open = rc._WindowsAPI._open
    targets = {"ancestor": root.parent, "root": root, "root-recheck": root,
               "leaf": root / names[0], "later": root / names[1]}
    counts = []

    def exhausted(*args):
        raise OSError(errno.EMFILE, "recorded descriptor exhaustion")

    def opening(api, path, access):
        if path == targets.get(where):
            counts.append(path)
            if where != "root-recheck" or len(counts) == 2:
                exhausted()
        return real_open(api, path, access)

    before = handle_count()
    with monkeypatch.context() as scoped:
        scoped.setattr(rc._WindowsAPI, "_open", opening)
        if where == "identity":
            scoped.setattr(rc._WindowsAPI, "file_identity", exhausted)
        if where == "folder-key":
            scoped.setattr(rc._WindowsAPI, "directory_identity", exhausted)
        failure = []
        assert not rc._unique_origin_digests(cases, source=root, failure=failure)
    assert failure == ["source folders could not be held: too many open files"]
    assert rc._ORIGIN_PINS.get() is None
    assert handle_count() == before
    root.rename(tmp_path / "released")


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("source", [False, True], ids=["batch-only", "explicit-source"])
@pytest.mark.parametrize("old", [False, True], ids=["current", "older"])
def test_network_root_in_saved_data_reaches_neither_open_seam(
        batch, capsys, monkeypatch, source, old):
    if old:
        batch.saved["evidence_rule_version"] = 0
    batch.saved["plan"]["source_snapshot"]["root"] = r"\\no-review-host.invalid\share\source"
    real, calls = rc._WindowsAPI._open, []

    def opening(api, path, access):
        assert not str(path).startswith("\\\\"), "batch supplied a network open"
        calls.append(access)
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    status, result = score(batch, capsys, source=source)
    assert calls
    assert status == (0 if source else 2)
    assert result["evidence_source"] == ("source" if source else "unavailable")
    if source:
        assert 0x81 in calls
        assert result["predictions"]["P3"]["status"] == "met"


@pytest.mark.parametrize("name", ["run/x/origin.", "run/x/origin ",
                                  "//no-review-host.invalid/share/run", r"\\?\C:\origin"])
def test_unsafe_origin_names_reach_no_open(tmp_path, monkeypatch, name):
    def unexpected(*args):
        pytest.fail("unsafe saved origin reached an open")

    monkeypatch.setattr(rc._WindowsAPI, "_open", unexpected)
    assert not rc._unique_origin_digests([case(name)], source=tmp_path)


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("version,changed", [(1, False), (0, False), (0, True)],
                         ids=["current-in-use", "older-in-use", "older-changed-source"])
def test_delete_handle_reason_and_changed_manifest_precedence(batch, capsys, version, changed):
    name = batch.saved["plan"]["cases"][0]["origins"][0]["run"]
    if changed:
        (batch.source / name / "final.patch").write_bytes(b"changed fixture patch")
    batch.saved["evidence_rule_version"] = version
    api = rc._WindowsAPI()
    # This foreign handle shares everything, but itself requests DELETE.
    handle = api.kernel.CreateFileW(str(batch.source / name), 0x10001, 7, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        status, result = score(batch, capsys)
    finally:
        assert api.kernel.CloseHandle(handle)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    gap, = result["index_excluded"]
    assert gap["source_unavailable"] == (
        "source identity, selection or evidence bytes changed" if changed else
        "source folders could not be held: the origin folder is in use by another program")


class IntVersion(int):
    pass


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("version", [None, False, True, IntVersion(1), 10**1000,
                                     -(10**1000), -1, 0, 1, 1.0, "1", [], {}],
                         ids=["null", "false", "true", "int-subclass", "huge-newer",
                              "huge-negative", "negative", "zero", "current", "float", "string", "list", "object"])
@pytest.mark.parametrize("source", [False, True])
def test_rule_spelling_keeps_reason_and_cause_distinct(batch, monkeypatch, version, source):
    batch.saved["evidence_rule_version"] = version

    def denied(*args):
        raise PermissionError(errno.EACCES, "recorded identity denial")

    monkeypatch.setattr(rc._WindowsAPI, "file_identity", denied)
    result, status = rc.score_saved(
        batch.saved, source=batch.source if source else None, start=batch.path)
    if type(version) is int and version == 1 and not source:
        assert (status, result["evidence_source"]) == (0, "batch")
        return
    assert (status, result["evidence_source"]) == (2, "unavailable")
    gap, = result["index_excluded"]
    newer = type(version) is int and version > 1
    cause = ("newer evidence rules cannot be reviewed by this code" if newer else
             "source folders could not be held: access denied" if source else
             "--source is required to re-derive saved evidence")
    reason = ("saved under a newer evidence rule than this code" if newer else
              cause if type(version) is int and version == 1 else
              "saved under an older evidence rule")
    assert gap["reason"] == reason and gap["source_unavailable"] == cause
