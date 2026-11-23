# SPDX-License-Identifier: Apache-2.0
"""Every installer mutation has a process-death case, including recovery's mutations."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from g6.skills import install


# This is a closed inventory of mutation call sites. Adding any call, even to an
# existing primitive, changes its count and requires updating the crash matrix.
# Filled with the intended repaired module's counts; not derived by the test.
MUTATIONS = {
    "mkdir": 4, "rename": 7, "unlink": 2, "rmdir": 1, "replace": 2,
    "write": 2, "open": 3,
}
HELPERS = {"_save": 18, "_write_bytes": 2, "_remove_tree": 1, "_stage_entry": 3}
NAMED_READS = {
    "OSError", "FileNotFoundError", "ValueError", "isinstance", "sorted", "str", "Path", "PurePosixPath",
    "visit", "set", "all", "dict", "hasattr", "any", "len", "g6_home", "_plain_ancestors", "_matches",
    "_recover", "_load", "_record", "_plain", "_cleanup", "_tree", "_lock", "_install", "recover_installs",
    "unique_destinations", "_sync_dir", "_staging_inventory", "reversed", "bytes",
}
READS = {
    "lstat", "is_symlink", "is_dir", "is_file", "stat", "iterdir", "relative_to", "as_posix",
    "read_bytes", "lexists", "exists", "absolute", "resolve", "is_relative_to", "is_absolute",
    "glob", "rglob", "seek", "tell", "fileno", "flush", "fsync", "close", "locking", "flock",
    "add", "append", "extend", "items", "get", "casefold", "rstrip", "startswith", "encode",
    "hexdigest", "sha256", "loads", "dumps", "with_suffix", "normcase", "join", "uuid4", "S_ISREG", "warn", "sleep",
    "hex", "fromhex", "update", "count",
}
INSTALL_MODES = ["install", "cleanup-error", "fail-backup", "fail-publish", "fail-record"]
RECOVERY_PHASES = ["prepared", "published", "rolled_back", "backup",
                   "initial", "legacy", "legacy-backup", "cleanup-error", "garbage"]

WORKER = r'''
import inspect
import json
import os
import sys
from pathlib import Path
from g6.skills import install

source, target, trace = map(Path, sys.argv[1:4])
mode, stop, moment = sys.argv[4], int(sys.argv[5]), sys.argv[6]
count = 0
busy = False
fired = False
events = []
module = str(Path(install.__file__).resolve())

def event(kind, when, number=None):
    global count, busy
    frame = inspect.currentframe().f_back
    while frame and str(Path(frame.f_code.co_filename).resolve()) != module:
        frame = frame.f_back
    if frame is None or busy:
        return None
    if when == 'before':
        count += 1
    number = count if number is None else number
    label = [number, kind, when, frame.f_code.co_name, frame.f_lineno]
    events.append(label)
    busy = True
    trace.write_text(json.dumps(events), encoding='utf-8')
    busy = False
    if number == stop and when == moment:
        os._exit(73)
    return number

def wrap(owner, name):
    original = getattr(owner, name)
    def call(*args, **kwargs):
        global fired
        number = event(name, 'before')
        if (mode == 'cleanup-error' and not fired and name == 'unlink'
                and args[0].parent.name == 'old' and args[0].name == 'SKILL.md'):
            fired = True
            raise OSError('cleanup unavailable')
        result = original(*args, **kwargs)
        if mode == 'personal-destination' and name == '_stage_entry' and not fired:
            fired = True
            (target / 'SKILL.md').write_bytes(b'personal destination edit\n')
        if mode == 'personal-return' and name == 'rename' and args[0] == target and not fired:
            fired = True
            (Path(args[1]) / 'SKILL.md').write_bytes(b'personal live edit\n')
        event(name, 'after', number)
        if not fired and (
            mode == 'fail-backup' and name == 'rename' and args[0] == target
            or mode == 'fail-publish' and name == 'rename' and args[0].name == 'new'
            or mode == 'fail-record' and name == 'replace' and Path(args[1]).suffix == '.json'
        ):
            fired = True
            raise OSError('interrupted after mutation')
        return result
    setattr(owner, name, call)

for name in ('mkdir', 'rename', 'unlink', 'rmdir'):
    wrap(Path, name)
wrap(os, 'replace')
wrap(install, '_save')
wrap(install, '_remove_tree')
wrap(install, '_stage_entry')
original_open = Path.open
class Stream:
    def __init__(self, stream):
        self.stream = stream
    def __getattr__(self, name):
        return getattr(self.stream, name)
    def __enter__(self):
        self.stream.__enter__()
        return self
    def __exit__(self, *args):
        return self.stream.__exit__(*args)
    def write(self, data):
        number = event('write', 'before')
        if number == stop and moment == 'partial':
            self.stream.write(data[:max(1, len(data) // 2)])
            self.stream.flush()
            os.fsync(self.stream.fileno())
            os._exit(73)
        result = self.stream.write(data)
        event('write', 'after', number)
        return result

def opened(path, mode='r', *args, **kwargs):
    if busy or not any(c in mode for c in 'wax+'):
        return original_open(path, mode, *args, **kwargs)
    number = event('open', 'before')
    stream = original_open(path, mode, *args, **kwargs)
    event('open', 'after', number)
    return Stream(stream)
Path.open = opened
original_write = install._write_bytes
def write(path, data):
    # Include an interrupted file copy/journal write with only a byte prefix.
    number = event('_write_bytes', 'before')
    if number == stop and moment == 'partial':
        with path.open('wb') as stream:
            stream.write(data[:max(1, len(data) // 2)])
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(73)
    result = original_write(path, data)
    event('_write_bytes', 'after', number)
    return result
install._write_bytes = write
if mode in ('recover', 'recover-cleanup-error'):
    if mode == 'recover-cleanup-error':
        mode = 'cleanup-error'
    install.recover_installs(target)
else:
    try:
        install.install_files({'SKILL.md': source, 'nested/data.txt': source}, target)
    except OSError:
        if not mode.startswith('fail-') or not fired:
            raise
'''


def snapshot(folder):
    if not folder.exists():
        return None
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            for p in folder.rglob("*")}


def seed(folder, monkeypatch, *, present=True):
    folder.mkdir(parents=True)
    monkeypatch.setenv("G6_HOME", str(folder / "state"))
    source, target = folder / "source", folder / "installed"
    source.write_bytes(b"old instructions\n")
    files = {"SKILL.md": source, "nested/data.txt": source}
    if present:
        assert install.install_files(files, target) == install.INSTALLED
    before = snapshot(target)
    source.write_bytes(b"new instructions\n")
    return source, target, files, before


def worker(source, target, mode, stop=0, moment="after", *, trace_name="trace.json"):
    trace = source.parent / trace_name
    result = subprocess.run(
        [sys.executable, "-c", WORKER, str(source), str(target), str(trace), mode, str(stop), moment],
        capture_output=True, timeout=60,
        env={**os.environ, "G6_CLAUDE": str(source.parent / "no-host"),
             "G6_CODEX": str(source.parent / "no-host")},
    )
    assert result.returncode == (73 if stop else 0), result.stderr.decode(errors="replace")
    return json.loads(trace.read_text(encoding="utf-8"))


def decision(target, before):
    records = target.parent / "state/skill-installs"
    pending = list(records.glob("*.pending"))
    if pending:
        journal = json.loads(pending[0].read_bytes())
        return journal["after"]["tree"] if journal.get("phase") == "published" else before
    # The journal has been removed only after its selected authority was saved.
    states = list(records.glob("*.json"))
    return json.loads(states[0].read_bytes())["tree"] if states else before


def finish(target, files, expected):
    install.recover_installs(target)
    assert snapshot(target) == expected, "recovery violated the recorded-publish decision"
    result = install.install_files(files, target)
    assert result == (install.INSTALLED if expected is None else install.UPDATED)
    assert (target / "SKILL.md").read_bytes() == b"new instructions\n"
    assert install.install_files(files, target) == install.UPDATED
    assert not list(target.parent.glob(".g6-install-*"))
    assert not list((target.parent / "state/skill-installs").glob("*.pending"))


def test_mutation_inventory_is_closed(tmp_path, monkeypatch):
    tree = ast.parse(inspect.getsource(install))
    actual = {}
    helpers = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id in HELPERS or node.func.id in NAMED_READS
            if node.func.id in HELPERS:
                helpers[node.func.id] = helpers.get(node.func.id, 0) + 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr in MUTATIONS or node.func.attr in READS, (
                f"unclassified call {ast.unparse(node.func)} needs a mutation/crash audit"
            )
            if node.func.attr in MUTATIONS:
                actual[node.func.attr] = actual.get(node.func.attr, 0) + 1
            assert node.func.attr not in {"rmtree", "mkdtemp", "write_text", "write_bytes", "touch", "remove"}
    assert actual == MUTATIONS, "new filesystem mutation needs an inventory entry and a crash case"
    assert helpers == HELPERS, "new journal/copy/delete call needs a crash case"
    expected = set()
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in HELPERS:
                expected.add((function.name, node.lineno))
            if isinstance(node.func, ast.Attribute) and node.func.attr in MUTATIONS:
                if ast.unparse(node.func) not in ("os.open", "staging.name.replace"):
                    expected.add((function.name, node.lineno))
    observed = set()
    for present in (False, True):
        for mode in INSTALL_MODES:
            if not present and mode in ("cleanup-error", "fail-backup"):
                continue
            source, target, _, _ = seed(tmp_path / f"install-{present}-{mode}", monkeypatch, present=present)
            events = worker(source, target, mode)
            observed.update(tuple(event[3:]) for event in events if event[2] == "after")
    for phase in RECOVERY_PHASES:
        source, target, _, _, _ = abandoned(tmp_path / phase, monkeypatch, phase)
        mode = "recover-cleanup-error" if phase == "cleanup-error" else "recover"
        observed.update(tuple(event[3:]) for event in worker(source, target, mode) if event[2] == "after")
    source, target, _, _ = seed(tmp_path / "personal-return", monkeypatch)
    source.write_bytes(b"old instructions\n")
    observed.update(tuple(event[3:]) for event in worker(source, target, "personal-return") if event[2] == "after")
    source, target, _, _ = seed(tmp_path / "personal-destination", monkeypatch)
    observed.update(tuple(event[3:]) for event in worker(source, target, "personal-destination") if event[2] == "after")
    assert expected <= observed, f"mutation sites without a subprocess crash scenario: {expected - observed}"


@pytest.mark.parametrize("present", [False, True])
@pytest.mark.parametrize("mode", INSTALL_MODES)
def test_exit_at_every_install_mutation(tmp_path, monkeypatch, present, mode):
    if not present and mode in ("cleanup-error", "fail-backup"):
        pytest.skip("first installation has no backup")
    source, target, _, _ = seed(tmp_path / "discovery", monkeypatch, present=present)
    events = worker(source, target, mode)
    assert events
    completed = {event[0] for event in events if event[2] == "after"}
    for number, kind, when, _, _ in events:
        if when != "before" or number not in completed:
            continue
        moments = ("before", "after", "partial") if kind in ("_write_bytes", "write") else ("before", "after")
        for moment in moments:
            case = tmp_path / f"case-{number}-{moment}"
            source, target, files, before = seed(case, monkeypatch, present=present)
            worker(source, target, mode, number, moment)
            expected = decision(target, before)
            finish(target, files, expected)


def abandoned(folder, monkeypatch, phase):
    source, target, files, before = seed(folder, monkeypatch)
    state, = (folder / "state/skill-installs").glob("*.json")
    old = json.loads(state.read_bytes())
    staging = folder / ".g6-install-abandoned"
    incoming, backup = staging / "new", staging / "old"
    incoming.mkdir(parents=True)
    for name in files:
        (incoming / name).parent.mkdir(parents=True, exist_ok=True)
        (incoming / name).write_bytes(source.read_bytes())
    receipt = {"owner": "g6", "version": 1, "tree": snapshot(incoming)}
    (incoming / install.MARKER).write_bytes((json.dumps(receipt, sort_keys=True) + "\n").encode())
    after = {**old, "tree": snapshot(incoming)}
    if phase == "initial":
        shutil.rmtree(target)
        state.unlink()
        before = old = None
    else:
        target.rename(backup)
    if phase not in ("backup", "changed-gap", "legacy-backup"):
        incoming.rename(target)
    if phase in ("changed", "changed-gap"):
        (backup / "SKILL.md").write_bytes(b"personal backup\n")
    key = hashlib.sha256(after["destination"].encode()).hexdigest()
    journal = {"before": old, "after": after, "staging": staging.name,
               "phase": "published" if phase in ("cleanup-error", "garbage") else (
                   phase if phase in ("published", "rolled_back") else "prepared")}
    if phase.startswith("legacy"):
        del journal["phase"]
        if phase == "legacy":
            install._save(state, after)
    if phase == "rolled_back":
        target.rename(incoming)
        backup.rename(target)
    if phase == "garbage":
        journal["garbage"] = snapshot(staging)
        journal["removing"] = "old/nested/data.txt"
        (backup / "nested/data.txt").unlink()
    install._save(state.parent / f"{key}.pending", journal)
    return source, target, files, before, after["tree"]


@pytest.mark.parametrize("phase", RECOVERY_PHASES)
def test_exit_at_every_recovery_mutation(tmp_path, monkeypatch, phase):
    source, target, _, _, _ = abandoned(tmp_path / "discovery", monkeypatch, phase)
    mode = "recover-cleanup-error" if phase == "cleanup-error" else "recover"
    events = worker(source, target, mode)
    completed = {event[0] for event in events if event[2] == "after"}
    for number, kind, when, _, _ in events:
        if when != "before" or number not in completed:
            continue
        moments = ("before", "after", "partial") if kind in ("_write_bytes", "write") else ("before", "after")
        for moment in moments:
            source, target, files, before, after = abandoned(tmp_path / f"case-{number}-{moment}", monkeypatch, phase)
            worker(source, target, mode, number, moment)
            expected = after if phase in ("published", "legacy", "cleanup-error", "garbage") else before
            finish(target, files, expected)


def test_successful_publish_reports_cleanup_error_and_retries(tmp_path, monkeypatch):
    source, target, files, _ = seed(tmp_path / "case", monkeypatch)
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path.parent.name == "old" and path.name == "SKILL.md":
            raise OSError("cleanup unavailable")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", unlink)
        reply = install.install_files(files, target)
    assert reply.startswith(install.UPDATED)
    assert "G6_SKILL_CLEANUP_PENDING" in reply
    pending, = (source.parent / "state/skill-installs").glob("*.pending")
    assert "cleanup unavailable" in json.loads(pending.read_bytes())["cleanup_error"]
    finish(target, files, snapshot(target))


def test_journal_disappearing_after_listing_is_skipped_under_lock(tmp_path, monkeypatch):
    source, target, _, _ = seed(tmp_path / "case", monkeypatch)
    root = source.parent / "state/skill-installs"
    pending = root / ("a" * 64 + ".pending")
    pending.write_bytes(b"{}")
    original = install._load
    locks = []
    original_lock = install._lock

    @contextmanager
    def locked(path):
        with original_lock(path):
            locks.append(path)
            try:
                yield
            finally:
                locks.pop()

    def load(path):
        if path == pending:
            assert locks, "journal read outside destination lock"
            pending.unlink()
        return original(path)

    monkeypatch.setattr(install, "_lock", locked)
    monkeypatch.setattr(install, "_load", load)
    install.recover_installs(target)


@pytest.mark.parametrize("phase", ["changed", "changed-gap"])
def test_changed_backup_is_retained_in_place_and_refuses(tmp_path, monkeypatch, phase):
    _, target, files, _, _ = abandoned(tmp_path / "case", monkeypatch, phase)
    backup, = target.parent.glob(".g6-install-*/old")
    expected, destination = snapshot(backup), snapshot(target)
    for operation in (lambda: install.recover_installs(target), lambda: install.install_files(files, target)):
        with pytest.raises(OSError, match="G6_SKILL_RECOVERY_REQUIRED"):
            operation()
        assert snapshot(backup) == expected
        assert snapshot(target) == destination
    assert not list(target.parent.glob(".g6-quarantine-*"))


def test_published_changed_backup_is_retained(tmp_path, monkeypatch):
    _, target, files, _, _ = abandoned(tmp_path / "case", monkeypatch, "published")
    backup, = target.parent.glob(".g6-install-*/old")
    (backup / "SKILL.md").write_bytes(b"changed after publish\n")
    (backup / "addition").write_bytes(b"changed after publish\n")
    expected = snapshot(backup)
    with pytest.raises(OSError, match="G6_SKILL_RECOVERY_REQUIRED"):
        install.install_files(files, target)
    assert snapshot(backup) == expected
    assert not list(target.parent.glob(".g6-quarantine-*"))


def test_backup_changed_during_rollback_is_never_restored(tmp_path, monkeypatch):
    _, target, _, _, _ = abandoned(tmp_path / "case", monkeypatch, "prepared")
    original = Path.rename

    def rename(path, destination):
        result = original(path, destination)
        if path == target and Path(destination).name == "new":
            backup = Path(destination).parent / "old"
            (backup / "SKILL.md").write_bytes(b"personal backup\n")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", rename)
        with pytest.raises(OSError, match="G6_SKILL_RECOVERY_REQUIRED"):
            install.recover_installs(target)
    assert not target.exists()
    backup, = target.parent.glob(".g6-install-*/old")
    assert (backup / "SKILL.md").read_bytes() == b"personal backup\n"
    assert list((target.parent / "state/skill-installs").glob("*.pending"))


def test_no_complete_recorded_tree_refuses_without_moving_foreign_backup(tmp_path, monkeypatch):
    _, target, _, _, _ = abandoned(tmp_path / "case", monkeypatch, "changed-gap")
    incoming, = target.parent.glob(".g6-install-*/new")
    (incoming / "SKILL.md").write_bytes(b"incomplete replacement\n")
    with pytest.raises(OSError, match="G6_SKILL_RECOVERY_REQUIRED"):
        install.recover_installs(target)
    assert not target.exists()
    backup, = target.parent.glob(".g6-install-*/old")
    assert (backup / "SKILL.md").read_bytes() == b"personal backup\n"
    assert list((target.parent / "state/skill-installs").glob("*.pending"))


def personal_return(folder, monkeypatch):
    source, target, files, _ = seed(folder, monkeypatch)
    state, = (folder / "state/skill-installs").glob("*.json")
    before = json.loads(state.read_bytes())
    staging = folder / ".g6-install-personal-return"
    incoming, backup = staging / "new", staging / "old"
    shutil.copytree(target, incoming)
    target.rename(backup)
    incoming.rename(target)
    (backup / "SKILL.md").write_bytes(b"personal return edit\n")
    key = hashlib.sha256(before["destination"].encode()).hexdigest()
    install._save(state.parent / f"{key}.pending", {
        "before": before, "after": before, "staging": staging.name, "phase": "prepared",
        "staged": True, "personal_return": {"tree": snapshot(backup)},
    })
    return source, target, files, snapshot(backup)


def test_exit_at_every_personal_return_mutation(tmp_path, monkeypatch):
    source, target, _, _ = personal_return(tmp_path / "discovery", monkeypatch)
    events = worker(source, target, "recover")
    completed = {event[0] for event in events if event[2] == "after"}
    for number, kind, when, _, _ in events:
        if when != "before" or number not in completed:
            continue
        moments = ("before", "after", "partial") if kind in ("_write_bytes", "write") else ("before", "after")
        for moment in moments:
            source, target, files, personal = personal_return(tmp_path / f"case-{number}-{moment}", monkeypatch)
            worker(source, target, "recover", number, moment)
            install.recover_installs(target)
            assert snapshot(target) == personal
            assert install.install_files(files, target) == install.KEPT
            assert snapshot(target) == personal
            assert not list(target.parent.glob(".g6-install-*"))


CONCURRENT_WORKER = r'''
import os
import sys
import time
from pathlib import Path
from g6.skills import install
source, target, ready, release, started = map(Path, sys.argv[1:6])
if sys.argv[6] == 'first':
    original = Path.rename
    def rename(path, destination):
        result = original(path, destination)
        if path == target and Path(destination).name == 'old':
            ready.write_bytes(b'ready')
            deadline = time.monotonic() + 30
            while not release.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError('release was not signalled')
                time.sleep(.01)
            os._exit(73)
        return result
    Path.rename = rename
else:
    # Signal only after actual lock contention, rather than relying on scheduling.
    if os.name == 'nt':
        import msvcrt
        original_lock = msvcrt.locking
        def locking(*args):
            try:
                return original_lock(*args)
            except OSError:
                started.write_bytes(b'contended')
                raise
        msvcrt.locking = locking
    else:
        import fcntl
        original_lock = fcntl.flock
        def flock(descriptor, operation):
            if operation & fcntl.LOCK_EX:
                try:
                    return original_lock(descriptor, operation | fcntl.LOCK_NB)
                except BlockingIOError:
                    started.write_bytes(b'contended')
            return original_lock(descriptor, operation)
        fcntl.flock = flock
reply = install.install_files({'SKILL.md': source, 'nested/data.txt': source}, target)
assert reply == install.UPDATED, reply
'''


def test_second_installer_waits_for_crashed_installer_then_recovers(tmp_path, monkeypatch):
    source, target, files, _ = seed(tmp_path / "case", monkeypatch)
    ready, release, started = (source.parent / name for name in ("ready", "release", "started"))
    arguments = [sys.executable, "-c", CONCURRENT_WORKER, str(source), str(target),
                 str(ready), str(release), str(started)]
    environment = {**os.environ, "G6_CLAUDE": str(source.parent / "no-host"),
                   "G6_CODEX": str(source.parent / "no-host")}
    processes = []

    def wait_for(path):
        deadline = time.monotonic() + 15
        while not path.exists():
            assert time.monotonic() < deadline, f"child did not create {path.name}"
            time.sleep(.01)

    try:
        first = subprocess.Popen([*arguments, "first"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        processes.append(first)
        wait_for(ready)
        assert not target.exists(), "first worker did not stop in the rename gap"
        second = subprocess.Popen([*arguments, "second"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        processes.append(second)
        wait_for(started)
        with pytest.raises(subprocess.TimeoutExpired):
            second.communicate(timeout=0.2)
        release.write_bytes(b"release")
        _, error = first.communicate(timeout=30)
        assert first.returncode == 73, error.decode(errors="replace")
        _, error = second.communicate(timeout=30)
        assert second.returncode == 0, error.decode(errors="replace")
        finish(target, files, snapshot(target))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=30)
