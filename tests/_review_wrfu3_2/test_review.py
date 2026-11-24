"""Review 2: self-built on-disk archives and a local transport stand-in only."""
import ast
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
    folder = base / uuid.uuid4().hex
    folder.mkdir()
    return folder


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
def batch(request, tmp_path, monkeypatch, capsys):
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
    modes = getattr(request, "param", ["agent", "hook"])
    flags = [flag for mode in modes for flag in ("--mode", mode)]
    assert rc.main(["--source", str(source), "--registered-commit", COMMIT,
                    *flags, "--s0", "after"], docker=Transport()) == 0
    live = json.loads(capsys.readouterr().out)
    prediction = "P2" if "agent" in modes else "P3"
    assert live["predictions"][prediction]["status"] == "met"
    path = next(output.glob("replay-rc-batch/development/*/run.json"))
    return SimpleNamespace(source=source, path=path, names=names, prediction=prediction,
                           saved=json.loads(path.read_text(encoding="utf-8")))


def score(batch, capsys, source=True):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    status = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    result = json.loads(captured.out)
    return status, result["evidence_source"], result["predictions"][batch.prediction]["status"]


@pytest.mark.skipif(os.name != "nt", reason="Windows case aliases")
@pytest.mark.parametrize("batch", [["agent"], ["hook"]], indirect=True, ids=["agent", "hook"])
@pytest.mark.parametrize("spelling,swap", [
    ("alias", False), ("exact", True), ("alias", True),
], ids=["stable-alias-control", "replaced-exact-control", "replaced-case-alias"])
def test_alias_keeps_one_digest_across_replacement(batch, tmp_path, monkeypatch, capsys, spelling, swap):
    name = batch.names[1]
    other = name if spelling == "exact" else name.rsplit("/", 1)[0] + "/wrongorigin"
    assert (batch.source / name).samefile(batch.source / other)
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    case["origins"][0]["run"] = other
    if right["mode"] == "agent":
        right["origin_run"] = other
    assert score(batch, capsys, source=False) == (2, "unavailable", "incomplete")
    original = rc._origin_run_identity
    identities = []

    def identify(run, source=None):
        identity = original(run, source)
        if source is not None and run in (name, other):
            identities.append(identity)
            if swap and len(identities) == 1:
                folder = source / name
                retained = tmp_path / "retained-origin"
                folder.rename(retained)
                shutil.copytree(retained, folder)
                assert not folder.samefile(retained)
                assert folder.samefile(source / other)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    observed = score(batch, capsys)
    assert len(identities) >= 2 and None not in identities
    if swap:
        assert identities[0] != identities[1]
    assert observed[1] != "batch", observed
    if right["mode"] == "agent":
        assert observed[2] == "incomplete", observed


@pytest.mark.parametrize("source", [False, True], ids=["without-source", "with-source"])
@pytest.mark.parametrize("old", [False, True], ids=["current-rule", "old-rule"])
def test_honest_replay_save_and_rescore(batch, capsys, source, old):
    if old:
        batch.saved["evidence_rule_version"] = 0
    expected = ((2, "unavailable", "incomplete") if old and not source else
                (0, "source" if old else "batch", "met"))
    assert score(batch, capsys, source) == expected


@pytest.mark.parametrize("source", [False, True], ids=["without-source", "with-source"])
@pytest.mark.parametrize("run", [None, False, 1, 1.0, [], {}, "", "../escape", "run/x/.",
                                  "run//x", "run/x.", "run/x ", "run/x\x00"])
def test_malformed_origin_is_unbound(tmp_path, run, source):
    case = SimpleNamespace(origins=[{"run": run}], digest="a" * 64)
    assert not rc._unique_origin_digests([case], source=tmp_path if source else None)


@pytest.mark.parametrize("source", [False, True], ids=["without-source", "with-source"])
def test_reused_exact_origin_one_patch_across_bugs(tmp_path, source):
    name = "run/synthetic/one"
    (tmp_path / name).mkdir(parents=True)
    cases = [SimpleNamespace(bug=bug, origins=[{"run": name}], digest="a" * 64)
             for bug in ("first", "second", "third")]
    assert rc._unique_origin_digests(cases, source=tmp_path if source else None)
    cases[-1].digest = "b" * 64
    assert not rc._unique_origin_digests(cases, source=tmp_path if source else None)


@pytest.mark.skipif(os.name != "nt", reason="Windows DOS aliases")
def test_real_short_alias_binds_one_digest(tmp_path):
    import ctypes

    name = "run/synthetic/LongFolderName"
    folder = tmp_path / name
    folder.mkdir(parents=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    short = ctypes.create_unicode_buffer(32768)
    assert 0 < kernel.GetShortPathNameW(str(folder), short, len(short)) < len(short)
    alias = "run/synthetic/" + Path(short.value).name
    if alias == name:
        pytest.skip("volume has no short alias")
    assert folder.samefile(tmp_path / alias)
    cases = [SimpleNamespace(origins=[{"run": run}], digest="a" * 64) for run in (name, alias)]
    assert rc._unique_origin_digests(cases, source=tmp_path)
    assert not rc._unique_origin_digests(cases)
    cases[-1].digest = "b" * 64
    assert not rc._unique_origin_digests(cases, source=tmp_path)


@pytest.mark.parametrize("kind", ["missing", "file", "junction", "parent-junction", "symlink"])
def test_unbound_source_origin_remains_incomplete(batch, tmp_path, capsys, kind):
    folder = batch.source / batch.names[1]
    if kind == "parent-junction":
        folder = folder.parent
    retained = tmp_path / "retained"
    folder.rename(retained)
    if kind == "file":
        folder.write_bytes(b"synthetic non-directory")
    elif "junction" in kind:
        if os.name != "nt":
            pytest.skip("Windows junction")
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder), str(retained)],
                              capture_output=True, check=False)
        assert made.returncode == 0, made.stderr
    elif kind == "symlink":
        try:
            folder.symlink_to(retained, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable")
            raise
    assert rc._origin_run_identity(batch.names[1], batch.source) is None
    assert score(batch, capsys) == (2, "unavailable", "incomplete")


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing and process handles")
def test_identity_handles_close_after_success_and_sharing_failure(batch, capsys):
    import ctypes
    from ctypes import wintypes as w

    api = rc._WindowsAPI()
    kernel = api.kernel
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetProcessHandleCount.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]

    def count():
        result = w.DWORD()
        assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(result))
        return result.value

    folder = batch.source / batch.names[1]
    baseline = count()
    for _ in range(20):
        assert rc._origin_run_identity(batch.names[1], batch.source) is not None
    assert count() == baseline
    locked = kernel.CreateFileW(str(folder), 0x80000000, 0, None, 3, 0x02000000, None)
    assert locked != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        baseline = count()
        for _ in range(20):
            assert rc._origin_run_identity(batch.names[1], batch.source) is None
        assert count() == baseline
        assert score(batch, capsys) == (2, "unavailable", "incomplete")
        assert count() == baseline
    finally:
        kernel.CloseHandle(locked)


@pytest.mark.skipif(os.name != "nt", reason="Windows parent junction race")
def test_parent_swap_after_open_never_uses_redirected_identity(tmp_path, monkeypatch):
    source = tmp_path / "source"
    name = "run/synthetic/origin"
    folder = source / name
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "origin").mkdir(parents=True)
    expected = rc._origin_run_identity(name, source)
    assert expected is not None
    parent = folder.parent
    original = rc._WindowsAPI.describe
    attempts = []

    def describe(api, handle, path):
        result = original(api, handle, path)
        if path == parent and not attempts:
            try:
                parent.rename(tmp_path / "retained-parent")
            except PermissionError:
                attempts.append("blocked")
            else:
                attempts.append("swapped")
                made = subprocess.run(["cmd", "/c", "mklink", "/J", str(parent), str(outside)],
                                      capture_output=True, check=False)
                assert made.returncode == 0, made.stderr
        return result

    monkeypatch.setattr(rc._WindowsAPI, "describe", describe)
    observed = rc._origin_run_identity(name, source)
    assert attempts
    assert observed == (None if attempts == ["swapped"] else expected)
