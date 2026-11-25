"""Self-built disk fixtures only; no external transport or source archive."""
import ctypes
import importlib.util
import json
import os
import subprocess
import sys
import types
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

_spec = importlib.util.spec_from_file_location(
    "wrfu4_synthetic", Path(__file__).parents[1] / "_review_wrfu3_2/test_review.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)
batch = synthetic.batch

@pytest.fixture
def tmp_path(request):
    base = Path(request.config.getoption("basetemp"))
    base.mkdir(parents=True, exist_ok=True)
    folder = base / uuid.uuid4().hex
    folder.mkdir()
    return folder


def base_module():
    name = "worked_examples._wrfu4_parent"
    if name not in sys.modules:
        source = subprocess.run(
            ["git", "show", "cb7dd9de:validation/worked_examples/replay_rc.py"],
            capture_output=True, check=True).stdout.decode("utf-8")
        module = types.ModuleType(name)
        module.__package__ = "worked_examples"
        module.__file__ = rc.__file__
        sys.modules[name] = module
        exec(compile(source, "replay_rc@cb7dd9de", "exec"), module.__dict__)
    return sys.modules[name]


@contextmanager
def deny_parent_listing(path):
    """Create a fixture directory denying only FILE_LIST_DIRECTORY."""
    from ctypes import wintypes as w

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [w.LPVOID]
    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]
    kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    descriptor = w.LPVOID()
    assert api.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        "D:P(D;;0x1;;;WD)(A;;FA;;;WD)", 1, ctypes.byref(descriptor), None)
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        assert kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)), ctypes.get_last_error()
        try:
            yield
        finally:
            # All names are fixtures we created; delete without listing this parent.
            import shutil
            shutil.rmtree(path / "source")
            path.rmdir()
    finally:
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory ACL")
@pytest.mark.parametrize("revision", ["parent", "head"], ids=["parent-control", "head"])
def test_traverse_only_ancestor_remains_usable(tmp_path, revision):
    module = base_module() if revision == "parent" else rc
    parent = tmp_path / "traverse-only"
    source = parent / "source"
    name = "run/synthetic/Origin"
    cases = [SimpleNamespace(origins=[{"run": name}], digest="a" * 64)]
    with deny_parent_listing(parent):
        (source / name).mkdir(parents=True)
        # Known descendants remain readable, and no listing is needed by the replay.
        with pytest.raises(PermissionError):
            list(parent.iterdir())
        assert (source / name).is_dir()
        assert module.Source(source, required=True).root == source
        assert module._unique_origin_digests(cases, source=source)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory ACL")
@pytest.mark.parametrize("revision", ["parent", "head"], ids=["parent-control", "head"])
@pytest.mark.parametrize("source", [False, True], ids=["without-source-control", "with-source"])
def test_cli_honest_batch_below_traverse_only_ancestor(
        batch, tmp_path, monkeypatch, capsys, revision, source):
    module = base_module() if revision == "parent" else rc
    registration = module._parse_registration(module.REGISTRATION.read_bytes())
    monkeypatch.setattr(module, "load_registration", lambda: registration)
    monkeypatch.setattr(synthetic, "rc", module)
    with deny_parent_listing(tmp_path / "traverse-only"):
        relocated = tmp_path / "traverse-only/source"
        batch.source.rename(relocated)
        batch.source = relocated
        observed = synthetic.score(batch, capsys, source=source)
        assert observed == (0, "batch", "met"), observed


@pytest.mark.parametrize("source", [False, True], ids=["without-source", "with-source"])
@pytest.mark.parametrize("version", [0, 1, 2, None, True, 1.0, -1, 10**100])
def test_replay_save_score_controls(batch, capsys, source, version):
    batch.saved["evidence_rule_version"] = version
    observed = synthetic.score(batch, capsys, source=source)
    current = type(version) is int and version == 1
    newer = type(version) is int and version > 1
    expected = ((0, "batch", "met") if current else
                (0, "source", "met") if source and not newer else
                (2, "unavailable", "incomplete"))
    assert observed == expected


def handle_count():
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetProcessHandleCount.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
    count = w.DWORD()
    assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
    return count.value


@pytest.mark.skipif(os.name != "nt", reason="Windows handle accounting")
@pytest.mark.parametrize("phase", ["open_directory", "describe", "directory_identity", "file_identity"])
@pytest.mark.parametrize("error", ["exhaustion", "interrupt"])
def test_every_pin_failure_closes_handles_control(tmp_path, monkeypatch, phase, error):
    import errno
    names = ["run/synthetic/Origin/child", "RUN/synthetic/origin/other"]
    for name in names:
        (tmp_path / name).mkdir(parents=True)
    cases = [SimpleNamespace(origins=[{"run": name}], digest="a" * 64) for name in names]
    assert rc._unique_origin_digests(cases, source=tmp_path)
    baseline = handle_count()
    real = getattr(rc._WindowsAPI, phase)
    calls = []

    def failure(api, *args):
        calls.append(args)
        if len(calls) == 2:
            if error == "interrupt":
                raise KeyboardInterrupt()
            raise OSError(errno.EMFILE, "synthetic descriptor exhaustion")
        return real(api, *args)

    monkeypatch.setattr(rc._WindowsAPI, phase, failure)
    reasons = []
    if error == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            rc._unique_origin_digests(cases, source=tmp_path)
    else:
        assert not rc._unique_origin_digests(cases, source=tmp_path, failure=reasons)
        assert reasons == ["source folders could not be held: too many open files"]
    assert rc._ORIGIN_PINS.get() is None
    assert handle_count() == baseline


@pytest.mark.skipif(os.name != "nt", reason="Windows aliases and root rename sharing")
@pytest.mark.parametrize("spelling", ["ordinary", "lower", "extended", "short"])
def test_shared_alias_ancestors_and_root_swap_control(tmp_path, monkeypatch, spelling):
    source = tmp_path / "source"
    names = ["run/synthetic/Origin/first", "RUN/SYNTHETIC/origin/second"]
    for name in names:
        (source / name).mkdir(parents=True)
    supplied = source
    if spelling == "lower":
        supplied = Path(str(source).lower())
    elif spelling == "extended":
        supplied = Path("\\\\?\\" + str(source))
    elif spelling == "short":
        kernel = rc._WindowsAPI().kernel
        kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        short = ctypes.create_unicode_buffer(32768)
        assert kernel.GetShortPathNameW(str(source), short, len(short))
        supplied = Path(short.value)
        if "~" not in str(supplied):
            pytest.skip("volume does not provide short names")
    original = rc._open_directory_at
    attempted = []

    def opening(path, parent, **kwargs):
        # The source identity check has finished; the next child has not opened.
        with pytest.raises(PermissionError):
            source.rename(tmp_path / "swapped-root")
        attempted.append(path)
        return original(path, parent, **kwargs)

    monkeypatch.setattr(rc, "_open_directory_at", opening)
    cases = [SimpleNamespace(origins=[{"run": name}], digest=digest * 64)
             for name, digest in zip(names, ["a", "b"])]
    baseline = handle_count()
    assert rc._unique_origin_digests(cases, source=supplied)
    assert attempted and handle_count() == baseline
    source.rename(tmp_path / "after-comparison")


@pytest.mark.skipif(os.name != "nt", reason="Windows network-open guard")
@pytest.mark.parametrize("field", ["legacy-root", "origin", "patch", "manifest-name"])
@pytest.mark.parametrize("source", [False, True])
def test_batch_network_names_never_reach_an_open_control(batch, monkeypatch, capsys, field, source):
    remote = "//fixture-no-host.invalid/share/source"
    if field == "legacy-root":
        batch.saved["plan"]["source_snapshot"]["root"] = remote
    elif field == "origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = remote
    elif field == "patch":
        batch.saved["plan"]["cases"][0]["patch"] = remote
    else:
        batch.saved["plan"]["source_snapshot"]["files"][remote] = "a" * 64
    original = rc._WindowsAPI._open
    calls = []

    def local_only(api, path, access):
        assert not str(path).startswith("\\\\"), "batch data attempted a network open"
        calls.append(access)
        return original(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", local_only)
    observed = synthetic.score(batch, capsys, source=source)
    can_refresh = source and field in ("legacy-root", "patch")
    assert observed == ((0, "source", "met") if can_refresh else (2, "unavailable", "incomplete"))
    assert calls
    if can_refresh:
        assert 0x81 in calls


class IntegerSubclass(int):
    pass


@pytest.mark.parametrize("version", [None, False, True, 0, -1, 1.0, 1, 2, 10**100,
                                    IntegerSubclass(1), IntegerSubclass(2)])
def test_exclusion_reason_uses_exact_integer_control(batch, version):
    batch.saved["evidence_rule_version"] = version
    # Missing start evidence forces the incomplete route even for current-rule data.
    result, status = rc.score_saved(batch.saved)
    assert status == 2
    gap, = result["index_excluded"]
    newer = type(version) is int and version > 1
    current = type(version) is int and version == 1
    unavailable = ("newer evidence rules cannot be reviewed by this code" if newer else
                   "--source is required to re-derive saved evidence")
    reason = ("saved under a newer evidence rule than this code" if newer else
              unavailable if current else "saved under an older evidence rule")
    assert gap["reason"] == reason
    assert gap["source_unavailable"] == unavailable
