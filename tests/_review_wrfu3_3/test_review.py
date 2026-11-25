"""Review 3: real folders, self-built replay batches, no external transport.

Every folder, batch and index here is built by this file under the pytest
basetemp. No network, model or Docker call; the transport is review 2's local
stand-in. Folder replacements are real renames on the real disk.
"""
import ctypes
import importlib.util
import json
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc


_spec = importlib.util.spec_from_file_location(
    "review3_synthetic", Path(__file__).parents[1] / "_review_wrfu3_2/test_review.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)
tmp_path = synthetic.tmp_path
batch = synthetic.batch

windows_only = pytest.mark.skipif(os.name != "nt", reason="real Windows case aliases")


def rebind_right_to_wrong_alias(batch):
    """The saved right patch claims the wrong run's folder through a case alias."""
    name = batch.names[1]
    alias = name.rsplit("/", 1)[0] + "/" + name.rsplit("/", 1)[1].swapcase()
    assert alias != name and (batch.source / name).samefile(batch.source / alias)
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    case["origins"][0]["run"] = alias
    if right["mode"] == "agent":
        right["origin_run"] = alias
    return name, alias, right["mode"]


# --- Finding 1 -------------------------------------------------------------

@windows_only
@pytest.mark.parametrize("batch", [["agent"], ["hook"]], indirect=True, ids=["agent", "hook"])
@pytest.mark.parametrize("swaps,fresh", [
    ((), False), ((1,), False), ((1, 2), False), ((1, 2, 3), False), ((1, 2, 3), True),
], ids=["stable-alias-control", "one-swap-control", "two-swap-control",
        "alternating-two-folders", "fresh-copy-before-each-read"])
def test_alias_keeps_one_digest_across_repeated_replacement(
        batch, tmp_path, monkeypatch, capsys, swaps, fresh):
    """One folder reached by two spellings must never bind two digests.

    The folder is replaced between identity lookups: either two real folders
    alternate under the one name (A, B, A, B) or a new copy arrives each time
    (A, B, C, D). At every instant both spellings name the same folder.
    """
    name, alias, mode = rebind_right_to_wrong_alias(batch)
    folder = batch.source / name
    # Without a source the two spellings are ambiguous and the batch is refused.
    assert synthetic.score(batch, capsys, source=False) == (2, "unavailable", "incomplete")
    parked = [tmp_path / "parked-a", tmp_path / "parked-b"]
    shutil.copytree(folder, parked[1])
    original = rc._origin_run_identity
    reads, blocked = [], []

    def replace():
        try:
            if fresh:
                retired = tmp_path / f"retired-{len(reads)}"
                folder.rename(retired)
                shutil.copytree(retired, folder)
            else:
                incoming = parked[1] if parked[1].exists() else parked[0]
                outgoing = parked[0] if incoming is parked[1] else parked[1]
                folder.rename(outgoing)
                incoming.rename(folder)
        except PermissionError:
            blocked.append(len(reads))  # a repaired comparison may retain a handle
        assert folder.samefile(batch.source / alias)

    def identify(run, source=None):
        identity = original(run, source)
        if source is not None and run in (name, alias):
            reads.append((run.rsplit("/", 1)[1], identity))
            if len(reads) in swaps:
                replace()
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    observed = synthetic.score(batch, capsys, source=True)
    assert len(reads) >= 2 and reads[0][1] is not None
    assert observed[1] != "batch", (observed, reads, blocked)
    if mode == "agent":
        assert observed[2] == "incomplete", (observed, reads, blocked)


@windows_only
@pytest.mark.parametrize("conflict,swaps,level", [
    (True, (), "leaf"), (False, (1, 2, 3), "leaf"), (True, (1, 2, 3), "leaf"), (True, (1, 2, 3), "parent"),
], ids=["stable-conflict-control", "equal-digest-replacement-control",
        "conflict-across-leaf-alternation", "conflict-across-parent-alternation"])
def test_comparison_alone_alternating_real_folders(tmp_path, monkeypatch, conflict, swaps, level):
    """The comparison itself, on two real folders alternating under one name."""
    name, alias = "run/synthetic/Origin", "run/synthetic/origin"
    (tmp_path / name).mkdir(parents=True)
    parked = tmp_path / "parked"
    if level == "leaf":
        moved = tmp_path / name
        parked.mkdir()
    else:  # the run folder's parent is what alternates
        moved = tmp_path / "run/synthetic"
        (parked / "Origin").mkdir(parents=True)
    assert (tmp_path / name).samefile(tmp_path / alias)
    original = rc._origin_run_identity
    reads, blocked = [], []

    def identify(run, source=None):
        identity = original(run, source)
        reads.append(identity)
        if len(reads) in swaps:
            spare = tmp_path / "spare"
            try:
                moved.rename(spare)
            except PermissionError:
                blocked.append(len(reads))  # a repaired comparison may retain a handle
            else:
                parked.rename(moved)
                spare.rename(parked)
            assert (tmp_path / name).samefile(tmp_path / alias)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    cases = [SimpleNamespace(origins=[{"run": run}], digest=digest)
             for run, digest in [(name, "a" * 64), (alias, "b" * 64 if conflict else "a" * 64)]]
    accepted = rc._unique_origin_digests(cases, source=tmp_path)
    assert None not in reads
    assert accepted is (not conflict), (reads, blocked)


@windows_only
@pytest.mark.parametrize("batch", [["agent"]], indirect=True, ids=["agent"])
@pytest.mark.parametrize("replacer", [False, True], ids=["stable-control", "replacer-running"])
def test_unsynchronised_replacer_never_scores_from_batch(batch, tmp_path, capsys, replacer):
    """No hook inside the scorer: another thread keeps replacing the folder.

    The loop only renames prepared copies of the run folder into place. The
    scorer is the unmodified command line. A sound comparison refuses every
    attempt; the control shows the same tampered batch refused on a quiet disk.
    Timing decides which attempt is accepted, so this node is supporting
    evidence beside the two deterministic tests above.
    """
    name, _, _ = rebind_right_to_wrong_alias(batch)
    folder = batch.source / name
    pool = tmp_path / "pool"
    pool.mkdir()
    ring = [pool / f"copy-{number}" for number in range(16)]
    for copy in ring:
        shutil.copytree(folder, copy)
    stop, done, errors = threading.Event(), [0], []
    pause = 0.003  # seconds between replacements; one lookup takes about 7 ms here

    def retry(action):
        for _ in range(2000):
            try:
                return action()
            except OSError:
                time.sleep(0.0005)
        raise RuntimeError("replacement could not complete")

    def replace():
        spare = pool / "spare"
        try:
            while not stop.is_set():
                incoming = ring[done[0] % len(ring)]
                try:
                    folder.rename(spare)
                except OSError:
                    continue  # the scorer's handle excludes delete sharing; try again
                retry(lambda: incoming.rename(folder))
                retry(lambda: spare.rename(incoming))
                done[0] += 1
                time.sleep(pause)  # keep the folder present for most of each cycle
        except Exception as exc:  # reported below, never swallowed
            errors.append(exc)

    def score_once():
        batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
        status = rc.main(["--score", str(batch.path), "--source", str(batch.source)])
        out = capsys.readouterr().out
        if not out:
            return status, None, None
        result = json.loads(out)
        return status, result["evidence_source"], result["predictions"]["P2"]["status"]

    worker = threading.Thread(target=replace, daemon=True)
    outcomes = []
    if replacer:
        worker.start()
    try:
        for _ in range(150 if replacer else 3):
            outcomes.append(score_once())
            if outcomes[-1][1] == "batch":
                break
    finally:
        stop.set()
        if replacer:
            worker.join(30)
    assert not errors, errors
    assert folder.is_dir()
    if replacer:
        assert done[0] > 0
    else:
        assert outcomes == [(0, "source", "incomplete")] * 3
    assert all(outcome[1] != "batch" for outcome in outcomes), (len(outcomes), done[0], outcomes[-1])


# --- Controls: honest batches, the join, failures and handles ----------------

@pytest.mark.parametrize("source", [False, True], ids=["without-source", "with-source"])
@pytest.mark.parametrize("old", [False, True], ids=["current-rule", "old-rule"])
def test_honest_full_path_control(batch, capsys, source, old):
    if old:
        batch.saved["evidence_rule_version"] = 0
    expected = ((2, "unavailable", "incomplete") if old and not source else
                (0, "source" if old else "batch", "met"))
    assert synthetic.score(batch, capsys, source=source) == expected


@windows_only
@pytest.mark.parametrize("spelling", ["lower", "relative", "short", "extended", "navigated"])
def test_honest_source_spelling_control(batch, capsys, monkeypatch, spelling):
    """An honest batch stays met however the same source folder is spelled."""
    source = str(batch.source)
    if spelling == "lower":
        source = source.lower()
    elif spelling == "relative":
        monkeypatch.chdir(batch.source.parent)
        source = batch.source.name
    elif spelling == "short":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        short = ctypes.create_unicode_buffer(32768)
        assert 0 < kernel.GetShortPathNameW(source, short, len(short)) < len(short)
        source = short.value
    elif spelling == "extended":
        source = "\\\\?\\" + source
    elif spelling == "navigated":
        source += "\\run\\.."
    batch.path.write_text(json.dumps(batch.saved), encoding="utf-8")
    status = rc.main(["--score", str(batch.path), "--source", source])
    result = json.loads(capsys.readouterr().out)
    assert (status, result["evidence_source"], result["predictions"][batch.prediction]["status"]) == (
        0, "batch", "met")


@pytest.mark.parametrize("conflict", [False, True], ids=["equal-control", "conflict-control"])
def test_three_name_transitive_overlap_control(tmp_path, monkeypatch, conflict):
    names = ["run/first", "run/second", "run/third"]
    for name in names:
        (tmp_path / name).mkdir(parents=True)
    original = rc._origin_run_identity
    identities = [original(name, tmp_path) for name in names]
    assert None not in identities and len(set(identities)) == 3
    # Third name bridges the first two names' identities on its two reads.
    replies = iter([identities[0], identities[1], identities[0],
                    identities[0], identities[1], identities[1]])
    monkeypatch.setattr(rc, "_origin_run_identity", lambda *args: next(replies))
    cases = [SimpleNamespace(origins=[{"run": name}], digest=digest)
             for name, digest in zip(names, ["a" * 64, "b" * 64 if conflict else "a" * 64, "a" * 64])]
    assert rc._unique_origin_digests(cases, source=tmp_path) is (not conflict)


@pytest.mark.parametrize("kind", ["file", "missing", "identity-error"])
def test_second_read_failure_closes_handles_control(tmp_path, monkeypatch, kind):
    name = "run/synthetic/origin"
    folder = tmp_path / name
    folder.mkdir(parents=True)
    original = rc._origin_run_identity
    reads = []

    def broken_identity(*args):
        raise OSError("synthetic identity query failure")

    def identify(run, source=None):
        identity = original(run, source)
        reads.append(identity)
        if len(reads) == 1:
            if kind == "identity-error":
                monkeypatch.setattr(rc._WindowsAPI, "file_identity", broken_identity)
            else:
                folder.rename(tmp_path / "retained")
                if kind == "file":
                    folder.write_bytes(b"not a directory")
        return identity

    if os.name != "nt" and kind == "identity-error":
        pytest.skip("Windows handle identity error")
    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    case = SimpleNamespace(origins=[{"run": name}], digest="a" * 64)
    assert not rc._unique_origin_digests([case], source=tmp_path)
    assert len(reads) == 2 and reads[0] is not None and reads[1] is None
    # Windows rejects this rename if an identity lookup leaked its handle.
    if folder.exists():
        folder.rename(tmp_path / "after-comparison")
    else:
        (tmp_path / "retained").rename(tmp_path / "after-comparison")


@windows_only
@pytest.mark.parametrize("fail", [False, True], ids=["all-reads-succeed", "second-pass-read-raises"])
def test_whole_comparison_leaves_no_process_handles_control(tmp_path, monkeypatch, fail):
    """Process handle count, so ancestors' handles are covered as well as the leaf's."""
    from ctypes import wintypes as w

    kernel = rc._WindowsAPI().kernel
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetProcessHandleCount.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]

    def count():
        result = w.DWORD()
        assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(result))
        return result.value

    names = [f"run/synthetic/{number}" for number in range(5)]
    for name in names:
        (tmp_path / name).mkdir(parents=True)
    cases = [SimpleNamespace(origins=[{"run": name}], digest="a" * 64) for name in names]
    assert rc._unique_origin_digests(cases, source=tmp_path)  # warm up imports and DLL loads
    real = rc._WindowsAPI.file_identity
    lookups = []

    def file_identity(api, handle):
        lookups.append(handle)
        if fail and len(lookups) >= 8:  # on HEAD, the third name's second-pass read
            raise OSError("synthetic identity query failure")
        return real(api, handle)

    monkeypatch.setattr(rc._WindowsAPI, "file_identity", file_identity)
    before = count()
    for _ in range(10):
        del lookups[:]
        assert rc._unique_origin_digests(cases, source=tmp_path) is (not fail)
        assert len(lookups) >= 8
    assert count() == before
