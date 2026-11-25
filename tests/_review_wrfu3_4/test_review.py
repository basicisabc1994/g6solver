"""Review 4 (Claude stand-in): real folders, self-built batches, no external transport.

Every folder, batch and index here is built by this file under the pytest
basetemp (a real disk inside the review worktree). No network, model or Docker
call; the transport is review 2's local stand-in. Replacements are real renames
or a real, temporary drive-letter definition that points into the basetemp and
is removed on teardown. Concurrency is simulated deterministically by wrapping
the module-level lookup functions, as reviews 1 to 3 did.
"""
import ctypes
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from worked_examples import replay_rc as rc

HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "review4_synthetic", HERE.parent / "_review_wrfu3_2/test_review.py")
synthetic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(synthetic)
tmp_path = synthetic.tmp_path
batch = synthetic.batch

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows drive letters and share modes")
posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX descriptors and open flags")
BASE = "c817440f"
LINUX_DEFAULT_DESCRIPTORS = 1024  # the usual soft RLIMIT_NOFILE; macOS ships 256


def case(run, digest):
    return SimpleNamespace(origins=[{"run": run}], digest=digest * 64)


def base_module():
    """replay_rc.py as it stood before the reviewed change, loaded beside HEAD."""
    name = "worked_examples._review4_base"
    if name in sys.modules:
        return sys.modules[name]
    try:
        text = subprocess.run(
            ["git", "-C", str(HERE), "show", f"{BASE}:validation/worked_examples/replay_rc.py"],
            capture_output=True, check=True).stdout.decode("utf-8")
    except (OSError, subprocess.CalledProcessError):
        saved = HERE / f"base_{BASE}_replay_rc.py.txt"  # the same git show output, saved by the reviewer
        if not saved.exists():
            pytest.skip("base revision unavailable")
        text = saved.read_text(encoding="utf-8")
    module = types.ModuleType(name)
    module.__package__ = "worked_examples"
    module.__file__ = rc.__file__
    sys.modules[name] = module
    exec(compile(text, f"replay_rc@{BASE}", "exec"), module.__dict__)
    return module


def open_handles():
    if os.name == "nt":
        from ctypes import wintypes as w
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = w.HANDLE
        kernel.GetProcessHandleCount.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        count = w.DWORD()
        assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(count))
        return count.value
    return len(os.listdir("/proc/self/fd"))


# --- Finding 1: a drive letter is re-resolved at every lookup -----------------

class Drive:
    """A temporary drive letter for this logon session, pointing into the basetemp."""

    def __init__(self):
        from ctypes import wintypes as w
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.DefineDosDeviceW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR]
        self.kernel.DefineDosDeviceW.restype = w.BOOL
        self.kernel.QueryDosDeviceW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        self.kernel.QueryDosDeviceW.restype = w.DWORD
        taken = self.kernel.GetLogicalDrives()
        buffer = ctypes.create_unicode_buffer(1024)
        self.name = self.target = None
        for letter in "ZYXWVUTSRQPONMLKJIHG":
            if not taken >> (ord(letter) - 65) & 1 and not self.kernel.QueryDosDeviceW(letter + ":", buffer, 1024):
                self.name = letter + ":"
                break

    def point(self, target):
        self.remove()
        # 8 = DDD_NO_BROADCAST_SYSTEM: no shell notification for this short-lived letter.
        if not self.kernel.DefineDosDeviceW(8, self.name, str(target)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.target = str(target)

    def remove(self):
        if self.target is not None:
            # REMOVE_DEFINITION | EXACT_MATCH_ON_REMOVE | NO_BROADCAST_SYSTEM
            self.kernel.DefineDosDeviceW(2 | 4 | 8, self.name, self.target)
            self.target = None


@pytest.fixture
def drive():
    letter = Drive()
    if letter.name is None:
        pytest.skip("no free drive letter")
    try:
        yield letter
    finally:
        letter.remove()


@windows_only
@pytest.mark.parametrize("remap", [False, True],
                         ids=["stable-drive-control", "drive-repointed-after-first-lookup"])
def test_drive_letter_cannot_supply_a_second_folder(tmp_path, monkeypatch, drive, remap):
    """One folder reached by two spellings must never bind two digests.

    --source names a drive letter. Repointing the letter between two lookups
    renames nothing, so no pinned handle refuses it. At every instant both
    spellings name the same single folder under the source.
    """
    name, alias = "run/synthetic/Origin", "run/synthetic/origin"
    first, second = tmp_path / "first", tmp_path / "second"
    for tree in (first, second):
        (tree / "source" / name).mkdir(parents=True)
    drive.point(first)
    source = Path(drive.name + "\\source")
    assert (source / name).samefile(source / alias)
    original = rc._origin_run_identity
    reads, pinned = [], []

    def identify(run, src=None):
        identity = original(run, src)
        reads.append(identity)
        if remap and len(reads) == 1:
            try:  # the first tree's folder really is pinned: its rename is refused
                (first / "source" / name).rename(first / "source/run/synthetic/moved")
            except PermissionError:
                pinned.append(True)
            drive.point(second)
            assert (source / name).samefile(source / alias)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    accepted = rc._unique_origin_digests([case(name, "a"), case(alias, "b")], source=source)
    assert None not in reads and len(reads) == 2
    assert pinned == ([True] if remap else [])
    assert not accepted, f"two digests bound through one folder name; identities {reads}"


@windows_only
@pytest.mark.parametrize("batch", [["agent"], ["hook"]], indirect=True, ids=["agent", "hook"])
@pytest.mark.parametrize("scenario", ["honest-on-drive-control", "stable-alias-control", "alias-and-repointed-drive"])
def test_cli_score_through_a_repointed_drive(batch, tmp_path, monkeypatch, capsys, drive, scenario):
    """The saved right patch claims the wrong run's folder through a case alias."""
    real = batch.source
    drive.point(tmp_path)
    batch.source = Path(drive.name + "\\source")
    assert batch.source.samefile(real)
    if scenario == "honest-on-drive-control":
        assert synthetic.score(batch, capsys, source=True) == (0, "batch", "met")
        return
    name = batch.names[1]
    alias = name.rsplit("/", 1)[0] + "/" + name.rsplit("/", 1)[1].swapcase()
    right = next(r for r in batch.saved["records"] if r["label"] == "right")
    saved_case = next(c for c in batch.saved["plan"]["cases"] if c["digest"] == right["digest"])
    saved_case["origins"][0]["run"] = alias
    if right["mode"] == "agent":
        right["origin_run"] = alias
    assert synthetic.score(batch, capsys, source=False) == (2, "unavailable", "incomplete")
    second = tmp_path / "second"
    shutil.copytree(real, second / "source")
    original = rc._origin_run_identity
    reads = []

    def identify(run, src=None):
        identity = original(run, src)
        if src is not None and run in (name, alias):
            reads.append((run.rsplit("/", 1)[1], identity))
            if scenario == "alias-and-repointed-drive" and len(reads) == 1:
                drive.point(second)
                assert (batch.source / name).samefile(batch.source / alias)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    observed = synthetic.score(batch, capsys, source=True)
    assert len(reads) >= 2 and reads[0][1] is not None
    assert observed[1] != "batch", (observed, reads)
    if right["mode"] == "agent":
        assert observed[2] == "incomplete", (observed, reads)


# --- Finding 2: every origin's whole walk stays open at once -----------------

def many_origins(tmp_path, count):
    names = [f"run/synthetic/origin-{number}" for number in range(count)]
    for name in names:
        (tmp_path / name).mkdir(parents=True)
    # Honest: distinct folders, one digest each.
    return [SimpleNamespace(origins=[{"run": name}], digest=f"{number:064x}") for number, name in enumerate(names)]


def exhausting_count(tmp_path):
    return LINUX_DEFAULT_DESCRIPTORS // len((tmp_path / "run/synthetic/origin-0").parts) + 8


@pytest.mark.parametrize("many", [False, True], ids=["three-origins-control", "many-origins"])
def test_comparison_fits_the_default_descriptor_limit(tmp_path, monkeypatch, many):
    """Handles held at once grow with origins times path depth, without bound."""
    cases = many_origins(tmp_path, exhausting_count(tmp_path) if many else 3)
    original = rc._origin_run_identity
    before = open_handles()
    held = []

    def identify(run, src=None):
        identity = original(run, src)
        held.append(open_handles() - before)
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    assert rc._unique_origin_digests(cases, source=tmp_path)
    assert abs(open_handles() - before) <= 3  # all released afterwards
    assert max(held) < LINUX_DEFAULT_DESCRIPTORS, (
        f"{len(cases)} honest origins held {max(held)} handles at once")


@posix_only
@pytest.mark.parametrize("many", [False, True], ids=["three-origins-control", "many-origins"])
def test_many_honest_origins_stay_bound_under_the_default_limit(tmp_path, many):
    import resource
    cases = many_origins(tmp_path, exhausting_count(tmp_path) if many else 3)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(LINUX_DEFAULT_DESCRIPTORS, soft), hard))
    try:
        accepted = rc._unique_origin_digests(cases, source=tmp_path)
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert accepted, f"{len(cases)} honest distinct origin folders read unbound"


# --- Finding 3: POSIX reports a regular-file source root as a link -----------

@posix_only
@pytest.mark.parametrize("revision", ["base", "head"], ids=["base-control", "head"])
def test_posix_file_source_root_keeps_its_refusal(tmp_path, monkeypatch, capsys, revision):
    module = base_module() if revision == "base" else rc
    source = tmp_path / "source"
    source.write_bytes(b"not a directory")
    monkeypatch.setenv("G6_WE_ARCHIVE", str(tmp_path / "output"))
    code = module.main(["--source", str(source), "--registered-commit", "a" * 40, "--dry"])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""
    assert captured.err.strip() == "replay_rc: source root is missing or unreadable"


# --- Finding 4: POSIX rechecks each pinned name one at a time ----------------

@posix_only
@pytest.mark.parametrize("swaps", [0, 1, 2, 3], ids=[
    "stable-alias-control", "swap-between-lookups-control", "swap-after-each-lookup",
    "swap-after-each-lookup-and-between-rechecks"])
def test_posix_alias_keeps_one_digest_across_rechecks(tmp_path, monkeypatch, swaps):
    """Case-insensitive POSIX folder: two real folders alternate under one name.

    At every instant both spellings name the same folder. The closing recheck
    opens and closes each pinned name in turn, so each can be answered by a
    different folder.
    """
    name, alias = "run/synthetic/Origin", "run/synthetic/origin"
    folder, parked = tmp_path / name, tmp_path / "parked"
    folder.mkdir(parents=True)
    parked.mkdir()
    if not (tmp_path / alias).exists():
        pytest.skip("case-sensitive filesystem")
    original_identity, original_open = rc._origin_run_identity, rc._open_handle
    reads, rechecks = [], []

    def swap():
        spare = tmp_path / "spare"
        folder.rename(spare)
        parked.rename(folder)
        spare.rename(parked)
        # On disk the folder holds exactly one entry for both spellings.
        assert os.listdir(folder.parent) == ["Origin"]

    def identify(run, src=None):
        identity = original_identity(run, src)
        reads.append(identity)
        if min(swaps, 2) >= len(reads):  # after the first lookup; after the second (back again)
            swap()
        return identity

    def opened(path, **options):
        if len(reads) == 2 and options.get("identify"):
            rechecks.append(str(path))
            if swaps == 3 and len(rechecks) == 2:
                swap()
        return original_open(path, **options)

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    monkeypatch.setattr(rc, "_open_handle", opened)
    accepted = rc._unique_origin_digests([case(name, "a"), case(alias, "b")], source=tmp_path)
    assert None not in reads and len(reads) == 2
    assert not accepted, f"two digests bound through one folder name; identities {reads}, rechecks {rechecks}"


# --- Finding 5: another program's handle makes an honest origin unbound ------

def foreign_handle(path, access):
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    # Shares read, write and delete with everyone; opens a directory.
    handle = kernel.CreateFileW(str(path), access, 7, None, 3, 0x02000000, None)
    assert handle != w.HANDLE(-1).value, ctypes.get_last_error()
    return lambda: kernel.CloseHandle(handle)


LIST, DELETE = 0x1 | 0x100000, 0x1 | 0x100000 | 0x10000


@windows_only
@pytest.mark.parametrize("revision,access", [("head", None), ("head", LIST), ("base", DELETE), ("head", DELETE)],
                         ids=["no-other-handle-control", "listing-handle-control",
                              "base-delete-access-handle-control", "delete-access-handle"])
def test_other_programs_handle_keeps_honest_origin_bound(tmp_path, revision, access):
    module = base_module() if revision == "base" else rc
    name = "run/synthetic/Origin"
    (tmp_path / name).mkdir(parents=True)
    close = foreign_handle(tmp_path / name, access) if access else lambda: None
    try:
        accepted = module._unique_origin_digests([case(name, "a")], source=tmp_path)
    finally:
        close()
    assert accepted, "one honest origin folder with one digest read unbound"


@windows_only
@pytest.mark.parametrize("access", [LIST, DELETE], ids=["listing-handle-control", "delete-access-handle"])
def test_cli_honest_batch_scores_beside_another_programs_handle(batch, capsys, access):
    close = foreign_handle(batch.source / batch.names[0], access)
    try:
        status = rc.main(["--score", str(batch.path), "--source", str(batch.source)])
    finally:
        close()
    result = json.loads(capsys.readouterr().out)
    reasons = sorted(set(re.findall(r'"source_unavailable": "([^"]*)"', json.dumps(result))))
    observed = status, result["evidence_source"], result["predictions"][batch.prediction]["status"], reasons
    assert observed == (0, "batch", "met", []), observed


# --- Finding 6: a tracked test of this change breaks pytest's capture on POSIX

@posix_only
@pytest.mark.parametrize("scoped", [True, False], ids=["scoped-patch-control", "tracked-test-body"])
def test_tracked_unreadable_folder_test_body(tmp_path, monkeypatch, scoped):
    """The POSIX branch of tests/validation/test_we_replay_folder_identity.py::
    test_unreadable_folder_identity_is_unbound, verbatim, beside a scoped patch."""
    path = tmp_path / "run/synthetic/folder"
    path.mkdir(parents=True)

    def unreadable(*args, **kwargs):
        raise PermissionError("synthetic unreadable folder")

    cases = [SimpleNamespace(origins=[{"run": "run/synthetic/folder"}], digest="a" * 64)]
    if scoped:
        with monkeypatch.context() as patch:
            patch.setattr(rc.os, "fstat", unreadable)
            accepted = rc._unique_origin_digests(cases, source=tmp_path)
        assert not accepted
    else:
        monkeypatch.setattr(rc.os, "fstat", unreadable)
        assert not rc._unique_origin_digests(cases, source=tmp_path)


# --- Checked, not reproduced (passing controls) ------------------------------

def throw(error):
    raise error()


@pytest.mark.parametrize("where", ["between-lookups", "inside-a-walk"])
@pytest.mark.parametrize("error", [KeyboardInterrupt, MemoryError])
def test_interrupted_comparison_releases_every_pin_control(tmp_path, monkeypatch, error, where):
    if where == "inside-a-walk" and os.name != "nt":
        pytest.skip("Windows handle query")
    cases = many_origins(tmp_path, 4)
    original = rc._origin_run_identity
    before = open_handles()
    held = []

    def identify(run, src=None):
        if where == "inside-a-walk" and len(held) == 2:
            # The third origin's walk has opened every handle when its identity query raises.
            monkeypatch.setattr(rc._WindowsAPI, "file_identity", lambda *a: throw(error))
        identity = original(run, src)
        held.append(open_handles() - before)
        if len(held) == 3:
            raise error()
        return identity

    monkeypatch.setattr(rc, "_origin_run_identity", identify)
    with pytest.raises(error):
        rc._unique_origin_digests(cases, source=tmp_path)
    assert held[0] > 0 and held[1] > held[0]
    assert abs(open_handles() - before) <= 3
    assert rc._ORIGIN_PINS.get() is None


@windows_only
@pytest.mark.parametrize("digest", ["a", "b"], ids=["same-digest", "different-digest"])
def test_junction_alias_is_refused_control(tmp_path, digest):
    import _winapi
    name, link = "run/synthetic/Origin", "run/synthetic/Linked"
    (tmp_path / name).mkdir(parents=True)
    _winapi.CreateJunction(str(tmp_path / name), str(tmp_path / link))
    try:
        assert (tmp_path / name).samefile(tmp_path / link)
        assert not rc._unique_origin_digests([case(name, "a"), case(link, digest)], source=tmp_path)
    finally:
        os.rmdir(tmp_path / link)
