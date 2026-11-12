# SPDX-License-Identifier: Apache-2.0
"""Review 2 of claude/freeze-holds-followup: the reviewer's four findings as it reproduced them (its own temporary folder
aside: pytest's serves here), and the inventories, built from the code, that remove their two classes at the root.

One listing, read once (findings 1, 2 and 4): a selection is made from the one listing and never by listing again, every
entry is decided by a stat that raises, and any error at all while listing or asking is unreadable. A safety status met
is kept before anything that can fail (finding 3): every row a tick writes carries what its cycle met, the fault's row
among them, so a failure at any step after it, then a restart, still pauses, waits or stops. FakeLLM is the only model;
every lock is a real Windows handle, closed before its test ends. The static inventory of the freeze's reading path is
``test_freeze_holds.py``'s, which this repair extends."""
from __future__ import annotations

import ast
import ctypes
import importlib.util
import inspect
import os
import re
import sys
import textwrap
import time
from collections import Counter
from ctypes import wintypes
from datetime import timedelta
from pathlib import Path

import pytest

import g6
import g6.integrity as integrity
from g6.core import reading
from g6.core.llm import FakeLLM, LLMError
from g6.core.reading import Unreadable
from g6.core.vault import AccessDenied, Vault
from g6.metaharness import api
from g6.metaharness import loop as loop_module
from g6.metaharness.domain import PAID_MODEL
from g6.metaharness.loop import (
    BUDGET_WAIT,
    CLASSIFIER_STOP,
    ENVELOPE_PATTERNS,
    JUDGE_UNPINNED,
    PAUSED,
    SEALED_BREACH,
    UNMETERED_CALLER,
    ShadowLoop,
    _AtLimit,
    _Halted,
    _roots,
)
from g6.self_maintenance.envelope import STOPPED, ask_to_stop
from g6.self_maintenance.usage import MODEL_REFUSED

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="a handle that shares nothing and a junction are Windows's")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rig = _load(Path(__file__).with_name("test_freeze_holds_review2.py"), "frzfu2_review_rig")
domains, enrolled, SETTINGS, NOW = rig.domains, rig.enrolled, rig.SETTINGS, rig.NOW


def runner_tick(loop: ShadowLoop) -> dict:
    """The runner's hand-off (``Runner._tick``): a tick that raises past its own guard is a fault."""
    try:
        return loop.tick()
    except Exception as exc:  # noqa: BLE001 - the hand-off under test
        return loop.fault(exc)


@pytest.fixture(autouse=True)
def _process_record(monkeypatch):
    """Whatever record of folders and sends a test's restart sets aside (``restarted``) is this process's again after it."""
    monkeypatch.setattr(loop_module, "_RECORD", loop_module._RECORD)


def restarted(loop: ShadowLoop, domain, proposer=None) -> ShadowLoop:
    """A new handle on the same folder, as a runner started again makes: a new process, so it has none of this process's
    record of the folder or of its sends (``loop._RECORD``, which the refusals follow-up made the process's), and only
    what the folder's files hold. Each test module that restarts restores the record after it (``_process_record``)."""
    loop_module._RECORD = loop_module._Record()
    return ShadowLoop(domain, loop.root, SETTINGS, proposer=proposer or domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())


def _held(path: Path):
    """What another process does when it opens a folder and shares nothing: it cannot be listed meanwhile."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return lambda: kernel.CloseHandle(handle)


class _NeverSeen(RuntimeError):
    """An error no handler names, and no ``OSError``."""


# Finding 1: the manifest selected by listing again -------------------------------------------------------------------

@WINDOWS
def test_finding_1_a_folder_locked_after_the_shared_listing_is_still_read_whole(tmp_path, monkeypatch):
    root = tmp_path / "manifest"
    folder = root / "judging"
    folder.mkdir(parents=True)
    source = folder / "gate.py"
    source.write_text("RULE = True\n", encoding="utf-8")
    expected = integrity.build_manifest(root)
    assert "judging/gate.py" in expected
    releases = []
    real_listing = integrity.files_under

    def list_then_lock(base):
        listed = real_listing(base)
        assert source in listed
        releases.append(_held(folder))
        with pytest.raises(PermissionError):
            list(folder.iterdir())
        return listed

    monkeypatch.setattr(integrity, "files_under", list_then_lock)
    try:
        try:
            actual = integrity.build_manifest(root)
        except Unreadable as exc:
            assert "judging" in str(exc)
            return
        assert actual == expected, f"a failed second listing silently dropped the listed source: {actual}"
    finally:
        for release in releases:
            assert release()


# Finding 2: a listed file gone before it was asked -------------------------------------------------------------------

def test_finding_2_a_listed_file_removed_before_its_stat_is_an_unreadable_listing(tmp_path, monkeypatch):
    source = tmp_path / "gate.py"
    source.write_text("RULE = True\n", encoding="utf-8")
    real_walk = reading.walk
    removed = []

    def walk_then_remove(root):
        for here, dirs, files in real_walk(root):
            if "gate.py" in files:
                source.unlink()
                removed.append(True)
            yield here, dirs, files

    monkeypatch.setattr(reading, "walk", walk_then_remove)
    with pytest.raises(reading.Unreadable, match="gate.py"):
        reading.files_under(tmp_path)
    assert removed


# Finding 3: a classifier's stop lost to a failed settlement ----------------------------------------------------------

def test_finding_3_a_settlement_error_does_not_erase_a_classifier_stop(tmp_path, monkeypatch):
    refused = []

    def responder(system, user):
        refused.append(True)
        raise LLMError(f"{MODEL_REFUSED}: permission denied")

    loop, domain = enrolled(tmp_path, proposer=FakeLLM(responder))

    def failed_settlement(*args, **kwargs):
        raise OSError("the budget store temporarily cannot be written")

    monkeypatch.setattr(loop.meter, "settle", failed_settlement)
    runner_tick(loop)
    assert refused
    row = loop.log.cycles()[-1]
    codes = {status["code"] for status in row["statuses"]}
    refusal = restarted(loop, domain, FakeLLM(responder))._refused()
    next_code = None if refusal is None else refusal["code"]
    assert CLASSIFIER_STOP in codes and next_code == PAUSED, (
        f"the fault cycle kept {sorted(codes)}; the restarted handle refused with {next_code}")


# Finding 4: an error that is no OSError escaped the listing ---------------------------------------------------------

def test_finding_4_an_unfamiliar_scandir_failure_refuses_enrolment_instead_of_escaping(tmp_path, monkeypatch):
    domain = domains.WordProblems(tmp_path)
    rules = tmp_path / "rules"
    rules.mkdir()
    domain.roots = (*_roots(domain), rules)
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    real_scandir = os.scandir
    attempts = []

    def unavailable(path):
        if os.path.normcase(str(path)) == os.path.normcase(str(rules)):
            attempts.append(True)
            raise _NeverSeen("the rules folder cannot be enumerated")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", unavailable)
    escaped = None
    reply = None
    try:
        reply = loop.enrol("Review owner")
    except _NeverSeen as exc:
        escaped = exc
    assert attempts
    assert escaped is None, f"enrolment leaked {type(escaped).__name__} instead of refusing {JUDGE_UNPINNED}"
    assert reply["code"] == JUDGE_UNPINNED and loop.log.enrolled() is None


# Class 1: one listing, read once -------------------------------------------------------------------------------------

def _listers() -> set:
    """The code of every function in the freeze's sources that lists a folder itself, found from that code: its own body
    calls the one lister (``walk``), ``files_under`` or ``os.walk``."""
    import g6.core.vault as vault

    found = set()
    for module in (reading, integrity, vault, loop_module, api):
        functions = [value for value in vars(module).values()
                     if inspect.isfunction(value) and value.__module__ == module.__name__]
        functions += [member for owner in vars(module).values() if inspect.isclass(owner) and owner.__module__ == module.__name__
                      for member in vars(owner).values() if inspect.isfunction(member)]
        for function in functions:
            if function.__name__ == "<lambda>" or function.__code__.co_filename != module.__file__:
                continue                                  # a lambda's line, or code a dataclass generated: no lister
            tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
            if {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)} & {"walk", "files_under",
                                                                                                    "os.walk"}:
                found.add(function.__code__)
    return found


def _counting(monkeypatch) -> Counter:
    """Every folder ``os.scandir`` lists (every listing is its: ``os.walk``'s and ``Path.glob``'s alike), keyed by the
    outermost lister on the stack that asked for it, so one digest listing a folder twice counts it twice."""
    listers, real, scanned, alive = _listers(), os.scandir, Counter(), []

    def scan(path="."):
        frame, outer = sys._getframe(1), None
        while frame is not None:
            if frame.f_code in listers:
                outer = frame
            frame = frame.f_back
        if outer is not None:
            alive.append(outer)                       # kept, so no later listing is given its id
            scanned[(id(outer), os.path.normcase(os.fspath(path)))] += 1
        return real(path)

    monkeypatch.setattr(os, "scandir", scan)
    return scanned


def test_each_digest_the_freeze_makes_lists_each_folder_once(tmp_path, monkeypatch):
    loop, domain = enrolled(tmp_path)
    scanned = _counting(monkeypatch)
    loop._frozen_now()
    loop_module._unpinned(domain)
    twice = sorted(folder for (_, folder), count in scanned.items() if count > 1)
    assert scanned and not twice, twice
    assert any(os.path.normcase(str(Path(g6.__file__).resolve().parent)) == folder for _, folder in scanned)
    scanned.clear()                                   # the instrument sees a manifest that lists again

    def listed_twice(base):
        reading.files_under(base)
        return reading.files_under(base)

    monkeypatch.setattr(integrity, "files_under", listed_twice)
    integrity.build_manifest(tmp_path, patterns=("**/*",))
    assert max(scanned.values()) == 2


PRODUCERS = ("the sealed split", "the integrity manifest", "the freeze's pin of a root", "the enrolment's link check")
FAILURES = ("a folder that cannot be listed", "an entry that cannot be asked", "a root that cannot be asked")


@pytest.mark.parametrize("failure", FAILURES)
@pytest.mark.parametrize("producer", PRODUCERS)
def test_an_error_never_seen_while_listing_or_asking_is_unreadable_for_every_producer(tmp_path, monkeypatch, producer,
                                                                                    failure):
    root = tmp_path / "tree"
    (root / "data").mkdir(parents=True)
    (root / "data" / "rule.py").write_text("RULE = True\n", encoding="utf-8")
    vault = Vault.seal([root], ledger=None)
    read = {"the sealed split": vault.verify_unchanged,
            "the integrity manifest": lambda: integrity.build_manifest(root, patterns=("**/*",)),
            "the freeze's pin of a root": lambda: loop_module._pinned([root]),
            "the enrolment's link check": lambda: loop_module._links_under([root])}[producer]
    before = read()
    target = {FAILURES[0]: root / "data", FAILURES[1]: root / "data" / "rule.py", FAILURES[2]: root}[failure]
    hit = []

    def failing(real):
        def asked(path, *args, **kwargs):
            if os.path.normcase(os.fspath(path)) == os.path.normcase(str(target)):
                hit.append(path)
                raise _NeverSeen(f"{path} cannot be asked")
            return real(path, *args, **kwargs)
        return asked

    for name in (("scandir",) if failure == FAILURES[0] else ("lstat", "stat")):
        monkeypatch.setattr(os, name, failing(getattr(os, name)))
    if producer == "the enrolment's link check":
        links, unlisted = read()
        assert hit and links == [] and unlisted, (links, unlisted)
    else:
        with pytest.raises(Unreadable):
            read()
        assert hit
    monkeypatch.undo()
    assert read() == before                           # readable again, it reads as it did


#: Patterns ``Path.glob`` reads, of every kind it has: recursive at any depth, one level, character sets and ranges,
#: case, hidden names, separators of both kinds, a cache folder, and every pattern G6 itself selects with.
GLOBS = ("**/*.py", "**/*", "*", "*.py", "a/*.py", "a/**/*.py", "a/**/b/*.txt", "**/b/**/*.txt", "**/**/*.py", "?.py",
         "[ab]*.py", "[!a]*", "[a-c].py", "*.PY", "A/*.PY", ".*", "**/.*", "a/./b/*.txt", "./*.py", "**/x.txt",
         "a/b/x.txt", "a\\b\\x.txt", "**/__pycache__/*", "*/*", "*/*/*", "a/*", "junction/*.py", "**/deep/*",
         "**/*/**/*.txt", "*/**/*.py", *ENVELOPE_PATTERNS, *api.JUDGING_PATTERNS)
#: Patterns that choose folders, or name nothing below the root: no file.
NO_FILE = ("**", "a/**", "a/", "**/", "a/../a/x.py")


def _tree(root: Path) -> Path:
    for rel in ("x.py", ".hidden.py", "B.PY", "ab.py", "c.py", "a/x.py", "a/y.txt", "a/b/x.txt", "a/b/deep/z.py",
                "a/__pycache__/x.cpython-312.pyc", "a/__pycache__/planted.py", "metaharness/gate.py", "harness/c.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(rel, encoding="utf-8")
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(root / "a"), str(root / "junction"))
    return root


def _as_globbed(root: Path, pattern: str) -> set[str]:
    """What the manifest chose before: ``Path.glob``'s paths that the whole listing holds, caches left out."""
    listed = set(reading.files_under(root))
    return {p.relative_to(root).as_posix() for p in root.glob(pattern)
            if p in listed and not integrity.in_cache(p.relative_to(root))}


@pytest.mark.parametrize("pattern", GLOBS)
def test_a_selection_from_the_listing_is_the_one_path_glob_made(tmp_path, pattern):
    root = _tree(tmp_path / "tree")
    assert set(integrity.build_manifest(root, patterns=(pattern,))) == _as_globbed(root, pattern)


@pytest.mark.parametrize("pattern", NO_FILE)
def test_a_pattern_that_chooses_folders_chooses_no_file(tmp_path, pattern):
    root = _tree(tmp_path / "tree")
    assert integrity.build_manifest(root, patterns=(pattern,)) == {}


@pytest.mark.parametrize("pattern, refused", [("", ValueError), ("a**b/*.py", ValueError), ("/abs/*.py", NotImplementedError)])
def test_a_pattern_path_glob_refuses_is_refused_listed_or_not(tmp_path, pattern, refused):
    with pytest.raises(refused):
        list(tmp_path.glob(pattern))
    with pytest.raises(refused):
        integrity.build_manifest(tmp_path / "nowhere", patterns=(pattern,))


def test_a_pattern_of_many_recursive_parts_is_decided_in_time_linear_in_its_parts():
    match = integrity._selects("/".join(["**", "*"] * 12) + "/x.py")     # a caller's pattern, through the door
    names = tuple(f"d{i}" for i in range(40))
    started = time.perf_counter()
    assert match((*names, "x.py")) and not match((*names, "y.py")) and not match(("x.py",))
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("patterns", [ENVELOPE_PATTERNS, api.JUDGING_PATTERNS, ("**/*",), ("**/*.py",)])
def test_g6s_own_digests_choose_the_files_they_chose_before(patterns):
    package = Path(g6.__file__).resolve().parent
    assert set(integrity.build_manifest(package, patterns=patterns)) == set().union(
        *(_as_globbed(package, pattern) for pattern in patterns))


# Class 2: a safety status met is kept before anything that can fail ---------------------------------------------------

def _steps_after_the_cycle() -> list[str]:
    """Every call ``_run`` makes from the line its cycle runs on, from its own code: on the handle (``self.<name>``,
    ``self.<part>.<name>``), the loop's own functions, and G6's modules it calls into (``reflex``). The cycle's own call
    is where the status is met, so it is not one of them."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(ShadowLoop._run)))
    cycle = next(node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._cycle")
    steps = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or node is cycle or node.lineno < cycle.lineno:
            continue
        name = ast.unparse(node.func)
        base = getattr(loop_module, name.split(".")[0], None)
        if re.fullmatch(r"[\w.]+", name) and (name.startswith("self.") or (
                "." not in name and inspect.isfunction(base)) or (
                inspect.ismodule(base) and base.__name__.startswith("g6."))):
            steps.add(name)
    return sorted(steps)


STEPS = _steps_after_the_cycle()
#: A status's own write: one that fails has its own rule, so a failure there is not a failure after the status is kept.
OWN_WRITES = {"a provider's limit": {"self._limited": "the limit's own note: one the records will not take leaves this "
                                                      "process waiting, and a restart's first request finds the limit again"}}
#: What every scenario reaches once its cycle has ended, so no scenario passes for reaching nothing.
ALWAYS = {"self.meter.settle", "self.requests.close"}
PROFILE = '{"prompt": "Work through the problem step by step."}'


def _meets(status: str, loop: ShadowLoop, domain) -> str:
    """The model call at which the cycle meets ``status``; returns the proposer's answer when the call goes on."""
    if status == "a classifier's refusal":
        raise LLMError(f"{MODEL_REFUSED}: permission denied")
    if status == "an unmetered caller":
        loop.requests.unseen = True
    elif status == "the sealed split within reach":
        try:
            domain.sealed_file.read_text(encoding="utf-8")
        except AccessDenied:
            pass
    elif status == "a barred sender":
        raise _Halted(PAID_MODEL, "a fake caller changed to a paid backend")
    elif status == "a provider's limit":
        raise _AtLimit(NOW + timedelta(hours=5), "HTTP 429: the provider's limit was met")
    elif status == "a stop":
        ask_to_stop(loop.root, "a person stops the loop")
        raise loop._halted()
    return PROFILE


#: Each status, the code its row carries (or none, for a wait or a stop, which write no status of their own), and what a
#: restart must do.
STATUSES = {"a classifier's refusal": CLASSIFIER_STOP, "an unmetered caller": UNMETERED_CALLER,
            "the sealed split within reach": SEALED_BREACH, "a barred sender": PAID_MODEL,
            "a provider's limit": None, "a stop": None}
#: The statuses whose every step is failed in turn; the others share their rule (``_carried``) and are failed at the first
#: step and the last.
EVERY_STEP = ("a classifier's refusal", "a provider's limit", "a stop")


def _fail_at(loop: ShadowLoop, step: str, met: list, failed: list, monkeypatch) -> None:
    """Make ``step`` raise once, the first time it is called after the status is met."""
    parts = step.split(".")
    owner = loop if parts[0] == "self" else loop_module
    for part in parts[1:-1] if parts[0] == "self" else parts[:-1]:
        owner = getattr(owner, part)
    real = getattr(owner, parts[-1])

    def once(*args, **kwargs):
        if met and not failed:
            failed.append(step)
            raise OSError(f"a failure injected at {step}")
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, parts[-1], once)


@pytest.mark.parametrize("status", list(STATUSES))
def test_a_failure_at_each_step_after_a_safety_status_is_met_then_a_restart_still_holds_it(tmp_path, monkeypatch, status):
    assert {"self.meter.settle", "self.requests.close", "self._record"} <= set(STEPS), STEPS
    steps = STEPS if status in EVERY_STEP else ["self.meter.settle", "self._record"]
    reached, wrong = [], []
    for step in steps:
        if step in OWN_WRITES.get(status, {}):
            continue
        folder = tmp_path / re.sub(r"\W", "_", step)
        folder.mkdir()
        domain, met, failed, holder = domains.WordProblems(folder), [], [], {}

        def propose(system, user):
            met.append(True)
            return _meets(status, holder["loop"], domain)

        loop, _ = enrolled(folder, domain=domain, proposer=FakeLLM(propose))
        holder["loop"] = loop
        with monkeypatch.context() as patch:
            _fail_at(loop, step, met, failed, patch)
            runner_tick(loop)
        if not failed:
            continue                                  # this status's cycle never makes this call
        reached.append(step)
        code = STATUSES[status]
        if code is not None:
            row = loop.log.cycles()[-1]
            if code not in {held["code"] for held in row["statuses"]}:
                wrong.append(f"{step}: the row kept {[held['code'] for held in row['statuses']]}")
        sent = []
        again = restarted(loop, domain, FakeLLM(lambda system, user: sent.append(user) or PROFILE))
        if status == "a provider's limit":
            got = again.tick()["warning_card"]["code"]
            if got != BUDGET_WAIT or sent:
                wrong.append(f"{step}: the restart answered {got} and sent {len(sent)} request(s)")
        else:
            refused = again._refused()
            got = None if refused is None else refused["code"]
            if got != (STOPPED if status == "a stop" else PAUSED):
                wrong.append(f"{step}: the restart refused with {got}")
    assert not wrong, wrong
    assert ALWAYS & set(steps) <= set(reached), (sorted(ALWAYS & set(steps) - set(reached)), reached)
