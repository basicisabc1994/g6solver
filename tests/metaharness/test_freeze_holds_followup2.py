# SPDX-License-Identifier: Apache-2.0
"""The freeze's second follow-up: review 4's six findings (the reviewer's own tests are run as unchanged copies), and the
inventories, built from the code, that remove their roots.

One answer per entry, of the entry the listing named (findings 1, 5 and 6). The reader asks every listed entry by a
path the platform does not reread (``exact``): Windows opens ``nul`` as its null device and drops trailing dots and
spaces from a name, so a stat of such a path answers for something else. Every stat, open and listing on the freeze's
reading path is found from the code and must go through that one rule; every reader is then crossed with names the
platform rereads, as files and as folders, in the root and under a middle folder, alone and beside the entry the
platform would read them as. What the lister's stat found is handed on with each name, and a later ask of the same
entry that finds another entry, or another kind, is unreadable.

What a pass holds is its own (findings 2, 3 and 4). A pass that fails before its tick's body holds nothing of an
earlier tick; a provider's limit is known to this process before its note is written; and an interrupt or an exit that
lands inside any write a tick makes on its way out still leaves the safety stop on disk. FakeLLM is the only model;
nothing here starts a runner or a process."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import os
import re
import shutil
import stat
import sys
import textwrap
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
import g6.integrity as integrity
from g6.core import reading
from g6.core.ledger import LedgerError
from g6.core.llm import FakeLLM
from g6.core.reading import Unreadable
from g6.core.vault import Vault
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import BUDGET_WAIT, CLASSIFIER_STOP, CYCLE_FAILED, PAUSED, ShadowLoop
from g6.metaharness.runner import Runner
from g6.self_maintenance.envelope import STOPPED, CycleLog


def _load(file: str, name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: Review 3's file (its rig, its walkers and its ways of failing a call) and the freeze's own inventory of its reading path.
fu3 = _load("test_freeze_holds_followup_review3.py", "frzfu2a_review3")
holds = _load("test_freeze_holds.py", "frzfu2a_holds")
fu2, enrolled, NOW = fu3.fu2, fu3.enrolled, fu3.NOW
_NeverSeen = fu3._NeverSeen
_process_record = fu2._process_record      # each restart's new process record is set aside after the test
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="names the platform rereads, and junctions, are Windows's")


def _same(path, target) -> bool:
    return os.path.normcase(os.fspath(path)) == os.path.normcase(str(target))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _put(path: str, data: bytes) -> None:
    with open(path, "wb") as handle:
        handle.write(data)


# The name rule: one function, and every ask on the reading path goes through it ------------------------------------------

#: Names Windows has reread, now or once: its devices, bare and with an extension, and names ending in dots and spaces.
NAMES = ("nul", "NUL", "con", "aux.py", "COM1", "nul.txt", "rules.", "rules ", "rules..", "rules. ")


@WINDOWS
@pytest.mark.parametrize("name", NAMES)
def test_exact_opens_the_entry_a_name_spells_and_says_whether_the_platform_reads_it_as_itself(tmp_path, name):
    spelled = "\\\\?\\" + str(tmp_path.resolve()) + "\\" + name      # the entry itself, by a path nothing rereads
    plain = str(tmp_path.resolve() / name)
    _put(spelled, name.encode())
    try:
        with open(reading.exact(plain), "rb") as handle:
            assert handle.read() == name.encode()                    # the rule's path opens the entry so spelled
        try:
            with open(plain, "rb") as handle:
                seen = handle.read()
        except OSError:
            seen = None
        # the rule hands a path back as it was given exactly when the platform reads it as itself: this machine is asked
        assert (reading.exact(plain) == plain) == (seen == name.encode()), (name, reading.exact(plain), seen)
        assert reading.exact(Path(plain)) == reading.exact(plain) and reading.exact(spelled) == spelled
    finally:
        os.remove(spelled)


@WINDOWS
def test_exact_reads_a_relative_path_from_the_folder_the_process_is_in(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert reading.exact("rules") == "rules" and reading.exact(os.path.join("a", "..", "rules")) == os.path.join("a", "..", "rules")
    assert reading.exact("nul") == "\\\\?\\" + os.path.join(os.getcwd(), "nul")
    assert reading.exact(os.path.join("sub", "rules.")) == "\\\\?\\" + os.path.join(os.getcwd(), "sub", "rules.")


def test_exact_is_the_path_itself_where_no_name_is_reread():
    here = str(Path(__file__).resolve())
    assert reading.exact(here) == here and reading.exact(Path(here)) == here
    if os.name != "nt":
        assert reading.exact("nul") == "nul" and reading.exact("rules.") == "rules."


#: What asks the file system about a path: a stat, an open, a listing, or a resolution of the name.
ASKS = {"stat", "lstat", "scandir", "listdir", "open", "read_bytes", "read_text", "realpath", "resolve", "samefile",
        "exists", "lexists", "is_file", "is_dir", "is_symlink", "is_junction", "isfile", "isdir", "islink", "getsource",
        "getsourcefile", "abspath", "walk", "fwalk", "iterdir", "glob", "rglob", "readlink", "getmtime", "getsize", "access"}
#: Each ask on the freeze's reading path outside the one reader, and why it may read a path as the platform reads it:
#: none of them is of an entry a listing named.
NOT_LISTED = {
    ("metaharness/api.py", "judging_sha256", "resolve"): "G6's own package, where Python imported it from",
    ("metaharness/loop.py", "_code_file", "resolve"): "the file Python loaded a judge's code from, and the interpreter's "
                                                      "own prefix: paths Python itself opened",
    ("metaharness/loop.py", "_loaded_outside", "abspath"): "a loaded module's file, as Python named it",
    ("metaharness/loop.py", "_roots", "resolve"): "a root a domain declares: it is read as the platform reads it, which "
                                                  "is how the judge's own code reaches it",
    ("metaharness/loop.py", "_within", "stat"): "the loop's folder, a root and the folders above them, compared by "
                                                "identity as their callers named them",
    ("metaharness/loop.py", "does", "getsource"): "a step's source text for what a profile may express (the freeze's "
                                                  "own inventory allows it)",
}


def _asks() -> list[tuple[str, str, ast.Call]]:
    """Every call on the freeze's reading path that asks the file system about a path, as (file, function, call)."""
    package = Path(g6.__file__).resolve().parent
    sources = {rel: (package / rel).read_text(encoding="utf-8") for rel in holds.FREEZE_SOURCES}
    found = []
    for rel, node, parts in holds._reached(sources, holds.FREEZE_ENTRIES):
        for part in parts:
            if isinstance(part, ast.Call):
                called = part.func.attr if isinstance(part.func, ast.Attribute) else getattr(part.func, "id", "")
                if called in ASKS and (isinstance(part.func, ast.Attribute) or called == "open"):
                    found.append((rel, node.name, part))
    return found


def _through_the_rule(call: ast.Call) -> bool:
    """Whether what ``call`` asks about is the rule's own answer: ``exact(...)`` itself, as its first argument or as the
    path a ``Path(...)`` is made of."""
    def ruled(node) -> bool:
        return isinstance(node, ast.Call) and ast.unparse(node.func) == "exact"

    asked = call.args[0] if call.args else getattr(call.func, "value", None)     # os.stat(x), or Path(x).read_bytes()
    if isinstance(asked, ast.Call) and ast.unparse(asked.func) == "Path" and asked.args:
        asked = asked.args[0]
    return ruled(asked)


def test_every_ask_on_the_reading_path_is_made_by_the_one_name_rule_or_is_of_no_listed_entry():
    asks = _asks()
    outside = {(rel, name, call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id)
               for rel, name, call in asks if rel != "core/reading.py" and not _through_the_rule(call)}
    assert outside - set(NOT_LISTED) == set(), sorted(outside - set(NOT_LISTED))
    assert set(NOT_LISTED) <= outside, sorted(set(NOT_LISTED) - outside)      # each allowance names an ask that is there
    # the one reader's own asks, the rule's own question to the platform aside (``exact`` asks how a path is read)
    mine = [(name, call) for rel, name, call in asks if rel == "core/reading.py" and name != "exact"]
    assert {"os.lstat", "os.stat", "os.scandir"} <= {ast.unparse(call.func) for _, call in mine}, mine
    unruled = [(name, ast.unparse(call)) for name, call in mine if not _through_the_rule(call)]
    assert unruled == [], unruled
    ruled = {(rel, name, ast.unparse(call.func)) for rel, name, call in asks if _through_the_rule(call)}
    assert ("metaharness/loop.py", "_in_reach", "os.path.realpath") in ruled, sorted(ruled)   # a listed link's target
    old = ast.parse("os.lstat(path)").body[0].value                 # what the reader asked before, the test finds
    assert not _through_the_rule(old) and _through_the_rule(ast.parse("os.lstat(exact(path))").body[0].value)
    assert _through_the_rule(ast.parse("Path(exact(path)).read_bytes()").body[0].value)


# Every reader against every name the platform rereads --------------------------------------------------------------------

def _spelled(top: str, under: str = "") -> dict[str, bytes]:
    """Every file under ``top``, by name below it and bytes, listed and read by paths nothing rereads: what is there."""
    found = {}
    with os.scandir(top + ("\\" + under.replace("/", "\\") if under else "")) as listing:
        entries = [(entry.name, entry.is_dir(follow_symlinks=False)) for entry in listing]
    for name, folder in entries:
        rel = f"{under}/{name}" if under else name
        if folder:
            found |= _spelled(top, rel)
        else:
            with open(top + "\\" + rel.replace("/", "\\"), "rb") as handle:
                found[rel] = handle.read()
    return found


def _reads_as(name: str) -> str | None:
    """The name Windows reads ``name`` as when it is a path's last name and that is another entry's name: the name less
    its trailing dots and spaces; ``None`` for a name it reads as itself or as a device."""
    bare = name.rstrip(". ")
    return bare if bare != name else None


#: Each case: the name, whether its entry is a file or a folder, where it sits, and whether the entry the platform would
#: read it as is beside it.
CASES = [(name, kind, place, beside) for name in NAMES for kind in ("a file", "a folder")
         for place in ("in the root", "under a middle folder") for beside in (False, True)
         if not beside or _reads_as(name)]


def _made(top: str, name: str, kind: str, place: str, beside: bool) -> str:
    """Make the case's tree under ``top`` (a path nothing rereads) and return the one file an edit is then made to."""
    _put(top + "\\keep.py", b"KEEP = 1\n")
    held = top
    if place == "under a middle folder":
        held = top + "\\mid"
        os.mkdir(held)
        _put(held + "\\beside.py", b"BESIDE = 1\n")
    if beside:
        _put(held + "\\" + _reads_as(name), b"the entry the platform reads the name as")
    entry = held + "\\" + name
    if kind == "a file":
        _put(entry, b"a file of this name")
        return entry
    os.makedirs(entry + "\\deep")
    _put(entry + "\\gate.json", b'{"accept": "yes"}')
    _put(entry + "\\deep\\leaf.py", b"LEAF = 1\n")
    return entry + "\\deep\\leaf.py"


def _or_unreadable(read):
    try:
        return read()
    except Unreadable:
        return Unreadable


@WINDOWS
@pytest.mark.parametrize("name, kind, place, beside", CASES)
def test_every_reader_reads_every_name_the_platform_rereads_whole_or_not_at_all(tmp_path, name, kind, place, beside):
    root = tmp_path.resolve() / "judge"
    root.mkdir()
    top = "\\\\?\\" + str(root)
    try:
        edited = _made(top, name, kind, place, beside)
        there = _spelled(top)
        assert any(name in rel.split("/") for rel in there), there      # the case is on disk as it was meant
        digests = {rel: _sha(data) for rel, data in there.items()}
        names = {
            "the lister": lambda: {os.path.join(here, each)[len(str(root)) + 1:].replace("\\", "/")
                                   for here, _, others in reading.walk(root) for each in others},
            "files_under": lambda: {path.relative_to(root).as_posix() for path in reading.files_under(root)},
        }
        for reader, read in names.items():
            found = _or_unreadable(read)
            assert found is Unreadable or found == set(there), (reader, sorted(found), sorted(there))
        whole = {
            "files_under and the one reader": lambda: {path.relative_to(root).as_posix(): reading.sha256_of(path)
                                                       for path in reading.files_under(root)},
            "the integrity manifest": lambda: integrity.build_manifest(root, patterns=("**/*",)),
            "the sealed split": lambda: {rel.partition("/")[2]: sha for rel, sha in Vault.seal([root], ledger=None).manifest.items()},
        }
        for reader, read in whole.items():
            found = _or_unreadable(read)
            assert found is Unreadable or found == digests, (reader, found, digests)
        links = _or_unreadable(lambda: loop_module._links_under([root]))
        assert links is Unreadable or links[0] == [], links          # no link is there, so none is found
        # the caches' reader pins only what a cache folder holds, and this tree has none
        assert _or_unreadable(lambda: loop_module._importable_caches(root)) in (Unreadable, {})
        vault = _or_unreadable(lambda: Vault.seal([root], ledger=None))
        moving = {
            "files_under and the one reader": whole["files_under and the one reader"],
            "the integrity manifest": whole["the integrity manifest"],
            "the freeze's pin of the root": lambda: loop_module._pinned([root]),
        }
        if edited.endswith(".py"):                    # the envelope chooses what Python imports
            moving["the envelope"] = lambda: fu3._enveloped(root)
        if vault is not Unreadable:
            moving["the sealed split, against its seal"] = lambda: vault.verify_unchanged()
        before = {reader: _or_unreadable(read) for reader, read in moving.items()}
        _put(edited, b"edited after the reading")
        unmoved = [reader for reader, read in moving.items()
                   if before[reader] is not Unreadable and _or_unreadable(read) == before[reader]]
        assert unmoved == [], f"an edit to {edited[len(top) + 1:]!r} changed nothing for {unmoved}"
    finally:
        shutil.rmtree(top)
    assert not os.path.lexists(top)


@WINDOWS
@pytest.mark.parametrize("there", [False, True])
@pytest.mark.parametrize("name", ["rules.", "rules ", "nul"])
def test_a_root_whose_own_name_the_platform_rereads_is_unreadable_whichever_entry_is_there(tmp_path, name, there):
    """A root is a caller's name, not a listed entry: ordinary code reaches it as the platform reads it. A root spelled
    with a name the platform rereads names two things at once, so no reading stands on it: it is unreadable whether the
    entry so spelled is there, the entry the platform reads it as, or both."""
    base = tmp_path.resolve()
    top = "\\\\?\\" + str(base)
    if _reads_as(name):
        os.mkdir(top + "\\" + _reads_as(name))
        _put(top + "\\" + _reads_as(name) + "\\gate.py", b"RULE = True\n")
    if there:
        os.mkdir(top + "\\" + name)
        _put(top + "\\" + name + "\\gate.py", b"RULE = False\n")
    root = base / name
    try:
        for read in (lambda: reading.files_under(root), lambda: list(reading.walk(root)),
                     lambda: integrity.build_manifest(root, patterns=("**/*",)), lambda: loop_module._pinned([root]),
                     lambda: Vault.seal([root], ledger=None)):
            with pytest.raises(Unreadable):
                read()
        skipped: list[str] = []
        assert list(reading.walk(root, skipped=skipped)) == [] and skipped
        assert loop_module._links_under([root])[1]
    finally:
        for made in (name, _reads_as(name)):
            if made and os.path.lexists(top + "\\" + made):
                shutil.rmtree(top + "\\" + made)


# One answer per entry: what the lister found is handed on, and a later ask must find the same entry ------------------------

def test_the_lister_hands_on_what_its_stat_found_with_every_name(tmp_path):
    root = fu3._tree(tmp_path / "tree")
    seen = 0
    for here, folders, others in reading.walk(root):
        for name in (*folders, *others):
            now = os.lstat(os.path.join(here, name))
            assert isinstance(name, str) and (name.info.st_ino, name.info.st_dev, name.info.st_mode) == (
                now.st_ino, now.st_dev, now.st_mode)
            assert stat.S_ISDIR(name.info.st_mode) is (name in folders)
            seen += 1
    assert seen == 8                                  # the tree's four folders and four files


def _swapped_after_its_first_ask(patch, target: Path, swap) -> list:
    """Run ``swap`` once, the moment the first stat of ``target`` has answered: the entry is another from then on."""
    real, done = os.lstat, []

    def lstat(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if _same(path, target) and not done:
            done.append(True)
            swap()
        return info

    patch.setattr(os, "lstat", lstat)
    return done


def _a_folder_in_its_place(target: Path) -> None:
    target.unlink()
    target.mkdir()
    (target / "gate.py").write_text("RULE = False\n", encoding="utf-8")


def _another_file_in_its_place(target: Path) -> None:
    target.rename(target.with_name("moved.py"))
    target.write_text("RULE = False\n", encoding="utf-8")


def _another_folder_in_its_place(target: Path) -> None:
    target.rename(target.with_name("moved"))
    target.mkdir()
    (target / "planted.py").write_text("PLANTED = True\n", encoding="utf-8")


def _a_junction_in_its_place(target: Path) -> None:
    import _winapi

    outside = target.parent.parent / "outside"
    outside.mkdir()
    (outside / "planted.py").write_text("PLANTED = True\n", encoding="utf-8")
    target.rename(target.with_name("moved"))
    _winapi.CreateJunction(str(outside), str(target))


#: Each way an entry is another by a later ask, the entry it happens to (a file or a folder of the tree), and whether
#: only Windows has it.
SWAPS = {"a file that is a folder by the second ask": ("sub/inner.py", _a_folder_in_its_place, False),
         "a file that is another file by the second ask": ("sub/inner.py", _another_file_in_its_place, False),
         "a folder that is another folder when the walk comes to go into it": ("sub", _another_folder_in_its_place, False),
         "a folder that is a junction when the walk comes to go into it": ("sub", _a_junction_in_its_place, True)}


@pytest.mark.parametrize("swap", list(SWAPS))
@pytest.mark.parametrize("walker", list(fu3.PRODUCERS))
def test_an_entry_that_is_another_by_a_later_ask_is_unreadable_or_named_for_every_walker(tmp_path, walker, swap):
    rel, act, windows_only = SWAPS[swap]
    if windows_only and os.name != "nt":
        pytest.skip("a junction is Windows's")
    root = fu3._tree(tmp_path / "tree")
    target = root / rel
    read = fu3.PRODUCERS[walker]
    if walker == "g6.core.vault._files":
        vault = Vault.seal([root], ledger=None)
        read = lambda root: vault.verify_unchanged()  # noqa: E731 - one producer among the table's
    done: list = []
    try:
        with pytest.MonkeyPatch.context() as patch:
            done = _swapped_after_its_first_ask(patch, target, lambda: act(target))
            try:
                found = read(root)
            except Unreadable:
                found = Unreadable
        assert done, f"the walk never asked {rel}"
        if found is Unreadable:
            return                                    # refused: nothing stood on the listing
        if walker == "g6.metaharness.loop._links_under":
            assert found[1], found                    # it names what it could not read whole
        elif walker == "g6.core.reading.walk" and swap.startswith("a file"):
            # the lister asks a file once and hands that answer on with its name: the later ask is its caller's
            assert all(hasattr(name, "info") for _, _, others in reading.walk(root / "empty") for name in others)
        else:
            raise AssertionError(f"{walker} read {swap} as {found!r}")
    finally:
        if "junction" in swap and done:
            os.rmdir(target)                          # the junction itself, never what it points at


@WINDOWS
@pytest.mark.parametrize("reader", ["_listing", "_links_under"])
def test_a_folder_swapped_for_a_junction_after_the_loops_own_ask_is_never_gone_into(tmp_path, monkeypatch, reader):
    """Finding 6's moment: the loop's listings ask each folder whether it is a link (``_is_link``) and then let the walk
    go in; a junction put there after that ask is another entry than the walk's stat found."""
    root = fu3._tree(tmp_path / "tree")
    real, done = loop_module._is_link, []

    def is_link(path, **kwargs):
        answer = real(path, **kwargs)
        if _same(path, root / "sub") and not done:
            done.append(True)
            _a_junction_in_its_place(root / "sub")
        return answer

    monkeypatch.setattr(loop_module, "_is_link", is_link)
    try:
        found = _or_unreadable(lambda: loop_module._listing(root) if reader == "_listing" else loop_module._links_under([root]))
        assert done
        if reader == "_listing":
            assert found is Unreadable or not any(name.startswith("sub/") for name in found), found
        else:
            assert found is Unreadable or found[1] or found[0] == [root / "sub"], found
    finally:
        monkeypatch.undo()
        if done:
            os.rmdir(root / "sub")                    # the junction itself, never what it points at


# Finding 2: a pass holds nothing of an earlier tick -----------------------------------------------------------------------

def _set_on_the_handle(function) -> set[str]:
    """The handle's attributes ``function`` assigns (``self.x = ...``, alone or in a tuple), from its code."""
    found = set()
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(function)))):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, (ast.AnnAssign, ast.AugAssign)) else []
        for target in targets:
            for part in ast.walk(target):
                if isinstance(part, ast.Attribute) and isinstance(part.value, ast.Name) and part.value.id == "self":
                    found.add(part.attr)
    return found


def _read_off_the_handle(function) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(function))))
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"
            and isinstance(node.ctx, ast.Load)}


#: What a tick's body runs through and sets on the handle, and what a fault and a lost cycle read off it.
BODY = ("_tick", "_refused", "_run", "_record", "_keep")
FAULT = ("fault", "_owed", "_made", "_carried", "_still_frozen", "lost_cycle")
#: What a fault reads that a tick's body sets and that is not one tick's: why each may outlive a tick.
ACROSS_TICKS = {"lost_cycles": "the ticks in a row the log would not take: the streak's own count, read from no log"}


def test_everything_a_fault_reads_that_a_ticks_body_sets_is_dropped_before_the_lock_is_asked_for():
    body = set().union(*(_set_on_the_handle(getattr(ShadowLoop, name)) for name in BODY if hasattr(ShadowLoop, name)))
    read = set().union(*(_read_off_the_handle(getattr(ShadowLoop, name)) for name in FAULT))
    held = (body & read) - set(ACROSS_TICKS)
    assert {"_met", "_began", "_written", "_frozen_seen"} <= held, sorted(held)
    assert not hasattr(ShadowLoop.tick, "__wrapped__"), "tick's first statements run only once the lock is held"
    assert held <= _set_on_the_handle(ShadowLoop.tick), sorted(held - _set_on_the_handle(ShadowLoop.tick))
    assert "_owed" in {ast.unparse(node.func).removeprefix("self.") for node in ast.walk(ast.parse(textwrap.dedent(
        inspect.getsource(ShadowLoop.fault)))) if isinstance(node, ast.Call)}
    assert "_carried" not in {ast.unparse(node.func).removeprefix("self.") for node in ast.walk(ast.parse(textwrap.dedent(
        inspect.getsource(ShadowLoop.fault)))) if isinstance(node, ast.Call)}


#: The calls a pass makes before its tick's body, from the code of the lock every operation takes: the folder's lock is
#: taken through ``folder_lock`` (its ``Coordination`` mutex) since the merges joined this follow-up.
BEFORE_THE_BODY = [name for name in fu3._calls(loop_module._exclusive) if name in ("folder_lock", "_reloaded", "self.mh.refresh")]


@pytest.mark.parametrize("step", ["folder_lock", "_reloaded", "self.mh.refresh"])
def test_a_pass_that_fails_before_its_ticks_body_says_nothing_of_an_earlier_tick(tmp_path, step):
    assert step in BEFORE_THE_BODY, BEFORE_THE_BODY
    met = []
    loop, domain = enrolled(tmp_path, proposer=fu3._refusing(met))
    assert loop.tick()["warning_card"]["code"] == CLASSIFIER_STOP
    rows, sent = len(loop.log.cycles()), len(met)
    assert loop.resume("Ada")["completion_state"] == "qualified-draft"
    owner, attr = fu3._owner(loop, step)
    with pytest.MonkeyPatch.context() as patch:
        failed = fu3._fail_once(patch, owner, attr, PermissionError)
        with pytest.raises(PermissionError) as raised:
            loop.tick()
        assert failed and (loop._met, loop._began, loop._written, loop._frozen_seen) == (None, None, False, None)
    Runner._fault(SimpleNamespace(loop=loop), raised.value)
    last = loop.log.cycles()[-1]
    assert len(met) == sent and len(loop.log.cycles()) == rows + 1
    assert [status["code"] for status in last["statuses"]] == [CYCLE_FAILED] and "classifier_stop" not in last
    assert fu3._held_after_restart(loop, domain) is None


def test_a_second_row_for_a_tick_whose_row_is_written_does_not_say_its_safety_stop_again(tmp_path, monkeypatch):
    """The row is written and its pause is not (the log refused the pause, which is left): a fault for what failed after
    the row writes a failed cycle of its own, and the refusal is on the first row alone."""
    met = []
    loop, _ = enrolled(tmp_path, proposer=fu3._refusing(met))
    beat = loop._beat
    monkeypatch.setattr(loop.log, "pause", lambda reason: (_ for _ in ()).throw(OSError("full")))

    def after_the_row(state, code, **said):
        if code == CLASSIFIER_STOP:
            raise OSError("the heartbeat cannot be written")
        return beat(state, code, **said)

    monkeypatch.setattr(loop, "_beat", after_the_row)
    with pytest.raises(OSError) as raised:
        loop.tick()
    Runner._fault(SimpleNamespace(loop=loop), raised.value)
    first, second = loop.log.cycles()
    assert "classifier_stop" in first and "classifier_stop" not in second
    assert [status["code"] for status in second["statuses"]] == [CYCLE_FAILED]


# Finding 3: a provider's limit is this process's before its note is written ------------------------------------------------

#: What the note of a limit can fail with: what its handler named, an error it did not, and an interrupt or an exit.
NOTE_FAILS = {"an OSError": OSError, "a LedgerError": LedgerError, "an error never seen": _NeverSeen,
              "an interrupt": KeyboardInterrupt, "an exit": SystemExit}


@pytest.mark.parametrize("failure", list(NOTE_FAILS))
def test_a_limit_is_waited_out_by_this_process_whatever_its_note_fails_with(tmp_path, monkeypatch, failure):
    met = []

    def at_the_limit(system, user):
        met.append(True)
        # as a provider's limit reaches a tick since the refusals follow-up: latched on the meter, which decides the end
        loop.requests.met_a_limit(NOW + timedelta(hours=5), "HTTP 429: the provider's limit was met")

    loop, _ = enrolled(tmp_path, proposer=FakeLLM(at_the_limit))
    with monkeypatch.context() as patch:
        patch.setattr(loop.log, "limit", lambda *args, **kwargs: (_ for _ in ()).throw(NOTE_FAILS[failure]("the note")))
        try:
            first = loop.tick()["warning_card"]["code"]
        except BaseException as exc:  # noqa: BLE001 - an interrupt or an exit leaves the tick; the process goes on here
            first = type(exc).__name__
    assert len(met) == 1 and first == (BUDGET_WAIT if issubclass(NOTE_FAILS[failure], Exception) else NOTE_FAILS[failure].__name__)
    again = loop.tick()
    assert len(met) == 1 and again["warning_card"]["code"] == BUDGET_WAIT, (first, again["warning_card"]["code"], len(met))


def test_the_meter_is_told_of_a_limit_before_its_note_and_no_error_of_the_notes_is_named():
    # the limit's own note is ``_keep_limit`` since the refusals follow-up joined this one (its ``_limited`` gone)
    tree = ast.parse(textwrap.dedent(inspect.getsource(ShadowLoop._keep_limit)))
    calls = [(node.lineno, ast.unparse(node.func)) for node in ast.walk(tree) if isinstance(node, ast.Call)]
    told = min(line for line, name in calls if name == "self.meter.limit_reached")
    noted = min(line for line, name in calls if name == "self.log.limit")
    assert told < noted, calls
    handlers = [ast.unparse(node.type) if node.type is not None else "" for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)]
    assert handlers and all(kind in ("Exception", "BaseException", "") for kind in handlers), handlers


# Finding 4: an interrupt or an exit inside any write a tick makes on its way out -------------------------------------------

#: The records' own writers, from their code: every method that appends a row, by the log's own appender or by the one
#: that writes a row with its identity (``_write_record``, which a stop's row is written by since the stop follow-up).
WRITERS = {name for name, member in vars(CycleLog).items() if inspect.isfunction(member) and name != "_write_record"
           and any(writer in inspect.getsource(member) for writer in ("self._append(", "self._write_record("))}
#: The stop's own writers, which the loop calls by name.
STOP_WRITERS = {"ask_to_stop", "end_start"}


def _writes_on_the_way_out() -> set[str]:
    """Every write a tick makes once its cycle has ended, from the code: the calls ``_run`` makes after its cycle
    (review 2's inventory) and the tick's own handler (``_keep``), followed through the handle's own methods, and in them
    every call of a writer of the records (``self.log.<writer>``) or of the stop."""
    todo = [name.removeprefix("self.") for name in fu2.STEPS if name.startswith("self._")] + ["_keep"]
    seen, found = set(), set()
    while todo:
        name = todo.pop()
        member = vars(ShadowLoop).get(name)
        if name in seen or not inspect.isfunction(member):
            continue
        seen.add(name)
        for called in fu3._calls(member):
            if called in STOP_WRITERS or (called.startswith("self.log.") and called.removeprefix("self.log.") in WRITERS):
                found.add(called)
            elif re.fullmatch(r"self\.\w+", called):
                todo.append(called.removeprefix("self."))
    return found


#: How a tick that met a classifier's refusal leaves: what else fails on its way, each with an error its handler names.
LEAVES = {"it returns": (),
          "its settlement fails": (("self.meter.settle", OSError),),
          "the log refuses its row": (("self.log.cycle", OSError),),
          "its settlement fails and the log refuses its pause": (("self.meter.settle", OSError), ("self.log.pause", OSError))}
CUTS = {"an interrupt": KeyboardInterrupt, "an exit": SystemExit}


def test_the_writes_on_a_ticks_way_out_are_the_ones_this_file_cuts_short():
    assert _writes_on_the_way_out() == {"self.log.cycle", "self.log.pause", "self.log.limit", "self.log.stop",
                                        "ask_to_stop", "end_start"}, sorted(_writes_on_the_way_out())


@pytest.mark.parametrize("cut", list(CUTS))
@pytest.mark.parametrize("leaves", list(LEAVES))
def test_an_interrupt_inside_any_write_on_a_ticks_way_out_leaves_the_safety_stop_on_disk(tmp_path, leaves, cut):
    reached, wrong = set(), []
    for write in sorted(_writes_on_the_way_out()):
        if write in [name for name, _ in LEAVES[leaves]]:
            continue                                  # this way of leaving already has that write fail
        folder = tmp_path / re.sub(r"\W", "_", write)
        folder.mkdir()
        met = []
        loop, domain = enrolled(folder, proposer=fu3._refusing(met))
        with pytest.MonkeyPatch.context() as patch:
            for name, error in LEAVES[leaves]:
                owner, attr = fu3._owner(loop, name)
                patch.setattr(owner, attr, lambda *args, _error=error, **kwargs: (_ for _ in ()).throw(_error("it failed")))
            owner, attr = fu3._owner(loop, write)
            failed = fu3._fail_once(patch, owner, attr, CUTS[cut], lambda *args: bool(met))
            try:
                loop.tick()
            except BaseException:  # noqa: BLE001 - the interrupt, the exit, or the failure the tick left by
                pass
        if not failed:
            continue                                  # this way of leaving never makes this write
        reached.add(write)
        held = fu3._held_after_restart(loop, domain)
        if held not in (PAUSED, STOPPED):
            wrong.append(f"{cut} inside {write}: the restart refused with {held}")
    assert not wrong, wrong
    expected = {"it returns": {"self.log.cycle"}, "its settlement fails": {"self.log.pause"},
                "the log refuses its row": {"ask_to_stop"},
                "its settlement fails and the log refuses its pause": {"ask_to_stop"}}[leaves]
    assert expected <= reached, (leaves, sorted(reached))
