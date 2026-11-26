"""Review 4: synthetic disk fixtures and recording seams, no host or ACL calls."""
import errno
import importlib.util
import json
import os
import uuid
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows review")

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "wrfu4_4_synthetic", HERE.parent / "_review_wrfu3_2/test_review.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)
batch = synthetic.batch


@pytest.fixture
def tmp_path(request, monkeypatch, tmp_path_factory):
    base = (Path(request.config.getoption("basetemp"))
            if request.config.getoption("basetemp") else tmp_path_factory.getbasetemp())
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


def score(batch, capsys, source=True):
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    argv = ["--score", str(batch.path)]
    if source:
        argv += ["--source", str(batch.source)]
    status = rc.main(argv)
    captured = capsys.readouterr()
    assert captured.out, captured.err
    return status, json.loads(captured.out)


@pytest.mark.parametrize("name", ["ordinary-control", "NUL", "COM1", "COM\u00b9", "LPT\u00b2"])
@pytest.mark.parametrize("component", ["task", "run"])
def test_source_index_devices_are_refused_before_open(tmp_path, monkeypatch, name, component):
    source = tmp_path / "source"
    task, run = (name, "ordinary") if component == "task" else ("synthetic", name)
    rel = f"dryrun/{task}/{run}"
    (source / "dryrun" / ("synthetic" if component == "run" else "safe-parent")).mkdir(parents=True)
    (source / "runs.jsonl").write_text(json.dumps({
        "event": "started", "kind": "dryrun", "run": rel, "task": task}) + "\n", encoding="utf-8")
    real = rc._WindowsAPI._open
    contacted = []

    def opening(api, path, access):
        if name != "ordinary-control" and path.name == name:
            contacted.append(str(path))
            raise OSError(errno.EIO, "recorded device open", str(path))
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    if name == "ordinary-control":
        cases, excluded = rc.enumerate_cases(source, [], require_index=True)
        assert not cases
        assert excluded.index_excluded[0]["reason"] == "dry run"
    else:
        with pytest.raises((ValueError, OSError)):
            rc.enumerate_cases(source, [], require_index=True)
    assert not contacted, contacted


@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
@pytest.mark.parametrize("route", ["invalid-origin", "older-rule"])
@pytest.mark.parametrize("replacement", ["missing", "file"])
@pytest.mark.parametrize("timing", ["before-control", "after-root-check"])
def test_disappearing_fresh_root_keeps_reason(batch, tmp_path, monkeypatch, capsys, route, timing, replacement):
    from contextlib import contextmanager

    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    else:
        batch.saved["evidence_rule_version"] = 0
    root = batch.source
    parked = tmp_path / "parked-source"
    def replace():
        root.rename(parked)
        if replacement == "file":
            root.write_bytes(b"synthetic non-folder root")

    if timing == "before-control":
        replace()
    real = rc._open_handle
    moved = []

    @contextmanager
    def opening(path, **kwargs):
        with real(path, **kwargs) as opened:
            yield opened
        if timing == "after-root-check" and path == root and kwargs.get("directory") and not moved:
            replace()
            moved.append(True)

    monkeypatch.setattr(rc, "_open_handle", opening)
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    reason = "a folder is missing" if replacement == "missing" else "a path is not a folder"
    assert result["index_excluded"][0]["source_unavailable"] == "source folders could not be held: " + reason
    if timing == "after-root-check":
        assert moved


@pytest.mark.parametrize("name", ["ordinary-control", "NUL"])
def test_index_nul_really_opens_a_character_device(tmp_path, monkeypatch, name):
    source = tmp_path / "source"
    (source / "dryrun/synthetic").mkdir(parents=True)
    rel = "dryrun/synthetic/" + name
    (source / "runs.jsonl").write_text(json.dumps({
        "event": "started", "kind": "dryrun", "run": rel, "task": "synthetic"}) + "\n", encoding="utf-8")
    real = rc._WindowsAPI._open
    types = []

    def opening(api, path, access):
        handle = real(api, path, access)
        if path.name == name:
            types.append(api.kernel.GetFileType(handle))
        return handle

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    if name == "ordinary-control":
        rc.enumerate_cases(source, [], require_index=True)
    else:
        with pytest.raises((ValueError, OSError)):
            rc.enumerate_cases(source, [], require_index=True)
    assert types == [], f"archive name opened native handle types {types}; FILE_TYPE_CHAR=2"


@pytest.mark.parametrize("name", ["ordinary-control", "CONIN$", "CONOUT$"])
def test_console_dollar_names_are_ordinary_absolute_folders(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    with rc._open_handle(folder, directory=True, identify=True) as opened:
        assert opened.path == folder
        assert opened.identity is not None
    assert rc._origin_run_identity(name, tmp_path) is not None


@pytest.mark.parametrize("batch", [["agent"], ["hook"]], indirect=True, ids=["agent", "hook"])
@pytest.mark.parametrize("source", [False, True], ids=["without-source", "with-source"])
@pytest.mark.parametrize("old", [False, True], ids=["current", "older"])
def test_replay_save_and_both_score_routes_control(batch, capsys, source, old):
    if old:
        batch.saved["evidence_rule_version"] = 0
    status, result = score(batch, capsys, source)
    expected = ((2, "unavailable", "incomplete") if old and not source else
                (0, "source" if old else "batch", "met"))
    assert (status, result["evidence_source"], result["predictions"][batch.prediction]["status"]) == expected


@pytest.mark.parametrize("route", ["current", "invalid-origin", "older-rule"])
@pytest.mark.parametrize("code,winerror,reason", [
    (errno.EACCES, 32, "the origin folder is in use by another program"),
    (errno.EACCES, None, "access denied"),
    (errno.EMFILE, None, "too many open files"),
    (errno.ENFILE, None, "too many open files"),
    (errno.ENOENT, None, "a folder is missing"),
    (errno.ENOTDIR, None, "a path is not a folder"),
    (errno.EIO, None, "identity or directory read failed"),
])
@pytest.mark.parametrize("batch", [["hook"]], indirect=True)
def test_root_failures_keep_score_and_replay_contract_control(batch, tmp_path, monkeypatch, capsys, route, code, winerror, reason):
    from contextlib import contextmanager

    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    elif route == "older-rule":
        batch.saved["evidence_rule_version"] = 0
    if code == errno.ENOENT:
        batch.source = tmp_path / "missing-source"
    failure = OSError(code, "recorded directory failure", str(batch.source))
    if winerror is not None:
        failure.winerror = winerror
    real = rc._open_handle

    @contextmanager
    def opening(path, **kwargs):
        if code != errno.ENOENT and path == batch.source and kwargs.get("directory"):
            raise failure
        with real(path, **kwargs) as opened:
            yield opened

    monkeypatch.setattr(rc, "_open_handle", opening)
    output = tmp_path / "refused-output"
    monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
    assert rc.main(["--source", str(batch.source), "--registered-commit", synthetic.COMMIT, "--dry"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "replay_rc: source root is missing or unreadable\n"
    assert captured.out == "" and not output.exists()
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"]) == (2, "unavailable")
    gap, = result["index_excluded"]
    assert gap["source_unavailable"] == "source folders could not be held: " + reason
    assert gap["reason"] == ("saved under an older evidence rule" if route == "older-rule" else gap["source_unavailable"])
    assert not output.exists()

# Run the previous focused controls with this file's permitted disk fixture.
_control_spec = importlib.util.spec_from_file_location(
    "wrfu4_4_inherited_controls", HERE.parent / "_review_wrfu4_3/test_controls.py")
controls = importlib.util.module_from_spec(_control_spec)
_control_spec.loader.exec_module(controls)
for _name, _test in vars(controls).items():
    if _name.startswith("test_"):
        globals()["test_inherited_" + _name.removeprefix("test_")] = _test

# Exercise repair 3's complete saved-device spelling matrix unchanged.
test_saved_device_forms_control = controls.fu4.test_saved_device_forms_refused_before_any_open


@pytest.mark.parametrize("directory", [False, True], ids=["archive", "source-folder"])
def test_file_ancestor_error_contract_control(tmp_path, directory):
    file = tmp_path / "file-ancestor"
    file.write_bytes(b"synthetic regular file")
    target = file / "child"
    if directory:
        with pytest.raises(NotADirectoryError) as caught:
            with rc._open_handle(target, directory=True):
                pytest.fail("regular file walked as a folder")
        assert caught.value.errno == errno.ENOTDIR
        assert caught.value.filename == str(file)
    else:
        with pytest.raises(ValueError, match="^forbidden archive path: "):
            rc.canonical_path(target)

@pytest.mark.parametrize("batch", [["agent"]], indirect=True)
@pytest.mark.parametrize("origin", ["not-recorded", "NUL"])
@pytest.mark.parametrize("refused", [False, True], ids=["accepted-control", "reviewer-refused"])
def test_device_metadata_cannot_hide_real_reviewer_refusal_control(batch, tmp_path, monkeypatch, capsys, origin, refused):
    if refused:
        for name in batch.names:
            path = batch.source / name / "rounds.json"
            rounds = json.loads(path.read_text(encoding="utf-8"))
            rounds[0]["input"]["timeout_s"] = 600.0
            path.write_text(json.dumps(rounds), encoding="utf-8")
        output = tmp_path / "refused-review-output"
        monkeypatch.setenv("G6_WE_ARCHIVE", str(output))
        assert rc.main(["--source", str(batch.source), "--registered-commit", synthetic.COMMIT,
                        "--mode", "agent", "--s0", "after"], docker=synthetic.Transport()) == 0
        live = json.loads(capsys.readouterr().out)
        assert live["predictions"]["P2"]["status"] == "incomplete"
        batch.path = next(output.glob("replay-rc-batch/development/*/run.json"))
        batch.saved = json.loads(batch.path.read_text(encoding="utf-8"))
        assert all(record["status"] == "failed" and record["result"] == "no review"
                   for record in batch.saved["records"])
    batch.saved["plan"]["cases"][0]["origins"][0]["run"] = "run/black-5634/" + origin
    status, result = score(batch, capsys, source=False)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (2, "unavailable", "incomplete")
    status, result = score(batch, capsys)
    assert (status, result["evidence_source"], result["predictions"]["P2"]["status"]) == (
        0, "source", "incomplete" if refused else "met")
