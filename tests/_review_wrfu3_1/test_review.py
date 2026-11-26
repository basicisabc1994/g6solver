"""Synthetic archives on real disk; no network, model, or Docker process."""
import ast
import ctypes
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

COMMIT = "a" * 40
IMAGE = "sha256:" + "b" * 64
TASK = SimpleNamespace(id="black-5634", repo="/synthetic/project", parent="c" * 40)


@pytest.fixture
def tmp_path(request):
    base = Path(request.config.getoption("basetemp"))
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid.uuid4().hex
    path.mkdir()
    return path


class Transport:
    def __call__(self, *args, **kwargs):
        value = None
        if args[:2] == ("image", "inspect"):
            value = [{"Id": IMAGE, "Config": {"Labels": {"we.g6.commit": COMMIT}}}]
        elif args[0] == "exec":
            from g6.harness import patch_review as pr
            assignment = ast.parse(kwargs["input"].decode()).body[2]
            request = json.loads(ast.literal_eval(assignment.value.args[0]))
            pr._inputs(request["task"], "", [], None, request["timeout_s"],
                       request["requirements"], template_instances=request.get("template_instances"),
                       search=request.get("search"))
            value = {"ok": True, "assessment": {
                "outcome": "insufficient_evidence", "completion": {}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b"")

    def sh(self, *args, **kwargs):
        state = {"head": TASK.parent, "tree": "d" * 40, "parent_tree": "d" * 40,
                 "dirty": 0, "commits": 1, "oracle": False}
        return subprocess.CompletedProcess(args, 0, json.dumps(state).encode(), b"")


@pytest.fixture
def batch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("G6_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("G6_LLM_PROVIDER", "none")
    for key in ("G6_CLAUDE", "G6_CODEX"):
        monkeypatch.setenv(key, r"C:\nonexistent\g6-no-host.exe")
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    monkeypatch.setattr(rc, "load_registration", lambda: registration)
    monkeypatch.setattr(rc.we, "load_tasks", lambda: [TASK])
    monkeypatch.setattr(rc.we, "image_names", lambda *a: (f"g6we-task:{TASK.id}-synthetic", "unused"))
    source, output = tmp_path / "source", tmp_path / "output"
    archive = rc.c.Archive(source)
    names = []
    for name, passes in (("RIGHTI", True), ("WRONGORIGIN", False)):
        rel = f"run/{TASK.id}/{name}"
        folder = source / rel
        folder.mkdir(parents=True)
        run = rc.c.RunFolder(archive, folder, rel)
        archive.append({"event": "started", "run": rel, "kind": "run", "task": TASK.id, "arm": "B"})
        patch = b"right" if passes else b"wrong"
        run.write("final.patch", patch)
        run.write("prompt.txt", "Issue #1: synthetic issue")
        run.write("rounds/round-1.patch", patch)
        run.write_json("rounds.json", [{"round": 1, "input": {
            "task": "Synthetic task", "requirements": [], "timeout_s": 600}}])
        run.finish({}, "finished")
        archive.start("check", TASK.id, checked_run=rel).finish({"checks": [
            {"patch": "final", "patch_applied": True, "passes": passes}]}, "finished")
        names.append(rel)
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT,
                    "--mode", "agent", "--s0", "after"], docker=Transport()) == 0
    live = json.loads(capsys.readouterr().out)
    assert live["predictions"]["P2"]["status"] == "met"
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    return SimpleNamespace(source=source, path=path, names=names,
                           saved=json.loads(path.read_text(encoding="utf-8")))


def score(batch, capsys, source):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    status = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    result = json.loads(captured.out)
    return status, result["evidence_source"], result["predictions"]["P2"]["status"]


def rebind_right(batch, name):
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    case["origins"][0]["run"] = right["origin_run"] = name


@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
@pytest.mark.parametrize("old", [False, True], ids=["current-rule", "old-rule"])
def test_honest_replay_save_and_score(batch, capsys, source, old):
    if old:
        batch.saved["evidence_rule_version"] = 0
    observed = score(batch, capsys, source)
    expected = ((2, "unavailable", "incomplete") if old and not source else
                (0, "source" if old else "batch", "met"))
    assert observed == expected


def set_short_name(folder, name):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.SetFileShortNameW.argtypes = [w.HANDLE, w.LPCWSTR]
    kernel.SetFileShortNameW.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateFileW(str(folder), 0x40010000, 3, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        assert kernel.SetFileShortNameW(handle, name), ctypes.get_last_error()
    finally:
        kernel.CloseHandle(handle)


@pytest.mark.skipif(os.name != "nt", reason="Windows DOS short names")
@pytest.mark.parametrize("source", [False, True], ids=["batch", "source"])
@pytest.mark.parametrize("alias", [False, True], ids=["control-exact", "short-without-tilde"])
def test_dos_alias_cannot_supply_two_digests(batch, capsys, source, alias):
    name = batch.names[1]
    if alias:
        set_short_name(batch.source / name, "CUSTOM")
        alias_name = name.rsplit("/", 1)[0] + "/CUSTOM"
        assert (batch.source / name).samefile(batch.source / alias_name)
        assert rc._origin_run_identity(name, batch.source) == rc._origin_run_identity(alias_name, batch.source)
        assert not rc._origin_names_may_alias(name, alias_name)
        name = alias_name
    rebind_right(batch, name)
    observed = score(batch, capsys, source)
    assert observed[2] == "incomplete", observed


@pytest.mark.parametrize("swap", [False, True], ids=["control-stable", "replace-after-first-id"])
def test_repeated_exact_name_keeps_one_digest_during_comparison(
        batch, tmp_path, monkeypatch, capsys, swap):
    name = batch.names[1]
    rebind_right(batch, name)
    original = rc._origin_run_identity
    calls = []

    def identify(run, source=None):
        identity = original(run, source)
        if source is not None and run == name:
            calls.append(identity)
            if swap and len(calls) == 1:
                folder = source / name
                parked = tmp_path / "retained-original"
                folder.rename(parked)
                shutil.copytree(parked, folder)
                assert not folder.samefile(parked)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    observed = score(batch, capsys, source=True)
    assert len(calls) >= 2
    if swap:
        assert calls[0] != calls[1]
    assert observed[2] == "incomplete", observed


@pytest.mark.parametrize("kind", ["missing", "file", "junction", "parent-junction", "symlink"])
def test_unbound_origins_never_supply_a_complete_batch(batch, tmp_path, capsys, kind):
    folder = batch.source / batch.names[1]
    parked = tmp_path / "parked"
    if kind == "parent-junction":
        folder = folder.parent
    folder.rename(parked)
    if kind == "file":
        folder.write_bytes(b"synthetic non-directory")
    elif kind in ("junction", "parent-junction"):
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder), str(parked)],
                              capture_output=True, check=False)
        assert made.returncode == 0, made.stderr
    elif kind == "symlink":
        try:
            folder.symlink_to(parked, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable")
            raise
    assert rc._origin_run_identity(batch.names[1], batch.source) is None
    observed = score(batch, capsys, source=True)
    assert observed == (2, "unavailable", "incomplete"), observed


@pytest.mark.skipif(os.name != "nt", reason="Windows retained handles")
def test_identity_handles_are_closed_on_success_and_failure(tmp_path, monkeypatch):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]

    def count():
        value = ctypes.c_ulong()
        assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(value))
        return value.value

    name = "run/synthetic/folder"
    (tmp_path / name).mkdir(parents=True)
    assert rc._origin_run_identity(name, tmp_path) is not None
    before = count()
    for _ in range(30):
        assert rc._origin_run_identity(name, tmp_path) is not None
    after_success = count()

    def denied(*args):
        raise PermissionError("synthetic identity query failure")

    monkeypatch.setattr(rc._WindowsAPI, "file_identity", denied)
    for _ in range(30):
        assert rc._origin_run_identity(name, tmp_path) is None
    assert count() == after_success == before


@pytest.mark.skipif(os.name != "nt", reason="Windows junction race")
def test_parent_junction_swap_during_walk_is_refused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    name = "run/synthetic/origin"
    folder = source / name
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "origin").mkdir(parents=True)
    baseline = rc._origin_run_identity(name, source)
    assert baseline is not None
    parent, parked = folder.parent, tmp_path / "parked-parent"
    original = rc._WindowsAPI.describe
    attempt = []

    def describe(api, handle, path):
        result = original(api, handle, path)
        if path == parent and not attempt:
            try:
                parent.rename(parked)
            except PermissionError:
                attempt.append("rename blocked by retained handle")
            else:
                attempt.append("junction installed after parent open")
                made = subprocess.run(["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
                                      capture_output=True, check=False)
                assert made.returncode == 0, made.stderr
        return result

    monkeypatch.setattr(rc._WindowsAPI, "describe", describe)
    identity = rc._origin_run_identity(name, source)
    assert attempt
    assert identity == (None if attempt[0].startswith("junction") else baseline)
