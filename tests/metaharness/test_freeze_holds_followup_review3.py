# SPDX-License-Identifier: Apache-2.0
"""Review 3 of claude/freeze-holds-followup: the reviewer's four findings as it reproduced them (its own temporary folder
aside: pytest's serves here), and the inventories, built from the code, that remove their two classes at the root.

Every entry is decided by the reader's own stat (finding 1): the one lister takes names from a folder's listing and
nothing else, so no predicate of a listing's entry, which the standard library reads as "not a folder" when it fails,
decides what is gone into. Every walker the freeze's sources hold is crossed with every kind of entry. What a cycle met
or made is never left in memory alone (findings 2 to 4): a tick that leaves by a raise writes the safety status its
cycle met on its way out, and a generation the cycle made is read from the chain of adoptions, so every way out of a
tick, the fault's own ways out and the runner's hand-off among them, leaves the loop held, and the row, when one is
written, says all of it. FakeLLM is the only model; nothing here starts a runner or a process."""
from __future__ import annotations

import ast
import importlib.util
import inspect
import os
import re
import shutil
import stat
import sys
import textwrap
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6.core.vault as vault_module
import g6.integrity as integrity
from g6.core import reading
from g6.core.ledger import LedgerError
from g6.core.llm import FakeLLM, LLMError
from g6.core.reading import Unreadable
from g6.core.vault import Vault
from g6.metaharness import api
from g6.metaharness import loop as loop_module
from g6.metaharness.loop import CANNOT_RECORD, CLASSIFIER_STOP, CYCLE_FAILED, PAUSED, ShadowLoop, _AtLimit, _Halted
from g6.metaharness.runner import Runner
from g6.metaharness.shadow import ADOPTED, ROLLED_BACK
from g6.self_maintenance.envelope import STOPPED, stop_in_force
from g6.self_maintenance.usage import MODEL_REFUSED


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: Review 2's file: its rig, its statuses and the calls ``_run`` makes after its cycle, which this review builds on.
fu2 = _load(Path(__file__).with_name("test_freeze_holds_followup_review2.py"), "frzfu3_review2")
rig, domains, enrolled, SETTINGS, NOW, restarted = fu2.rig, fu2.domains, fu2.enrolled, fu2.SETTINGS, fu2.NOW, fu2.restarted
_NeverSeen = fu2._NeverSeen
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="a junction is Windows's")


def _same(path, target) -> bool:
    return os.path.normcase(os.fspath(path)) == os.path.normcase(str(target))


# Finding 1: a failed classification hid a subtree -------------------------------------------------------------------

def test_finding_1_a_failed_directory_entry_classification_is_unreadable(tmp_path, monkeypatch):
    root = tmp_path / "sealed"
    hidden = root / "answers"
    hidden.mkdir(parents=True)
    answer = hidden / "case.json"
    answer.write_bytes(b"original answer")
    real_scandir = os.scandir
    failures = []

    class Entry:
        def __init__(self, entry):
            self.entry = entry

        def __getattr__(self, name):
            return getattr(self.entry, name)

        def is_dir(self, *args, **kwargs):
            if Path(self.entry.path) == hidden:
                failures.append(self.entry.path)
                raise PermissionError("directory attributes temporarily unavailable")
            return self.entry.is_dir(*args, **kwargs)

    class Scan:
        def __init__(self, path):
            self.scan = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.scan.close()

        def __iter__(self):
            return self

        def __next__(self):
            return Entry(next(self.scan))

    reads = (
        ("files_under", lambda: reading.files_under(root)),
        ("manifest", lambda: integrity.build_manifest(root, patterns=("**/*",))),
        ("vault", lambda: dict(Vault.seal([root], ledger=None).manifest)),
    )
    expected = {name: read() for name, read in reads}
    assert expected["files_under"] == [answer]
    monkeypatch.setattr(os, "scandir", Scan)
    accepted = []
    for name, read in reads:
        prior = len(failures)
        try:
            found = read()
        except reading.Unreadable:
            continue
        # A repair can either refuse the failed classification or stop using
        # DirEntry's suppressible predicate and obtain the whole listing by stat.
        if len(failures) > prior or found != expected[name]:
            accepted.append(name)
    assert not accepted, (
        f"directory classification failed, but these readers accepted the incomplete listing: "
        f"{accepted}"
    )


# Finding 2: a handled ledger error after a refusal ------------------------------------------------------------------

def _refusing(met: list):
    def responder(system, user):
        met.append(True)
        raise LLMError(f"{MODEL_REFUSED}: permission denied")

    return FakeLLM(responder)


def _held_after_restart(loop: ShadowLoop, domain) -> str | None:
    refusal = restarted(loop, domain)._refused()
    return None if refusal is None else refusal["code"]


def test_finding_2_a_handled_ledger_error_after_a_refusal_still_pauses_after_restart(tmp_path, monkeypatch):
    refused = []
    loop, domain = enrolled(tmp_path, proposer=_refusing(refused))
    ledger = loop.mh.workspace / "ledger.jsonl"
    saved = []

    def failed_settlement(*args, **kwargs):
        saved.append(ledger.read_bytes() if ledger.exists() else None)
        # A transiently damaged workspace ledger makes refresh raise its real
        # LedgerCorrupt (a LedgerError), rather than an injected exception type.
        ledger.write_bytes((saved[-1] or b"") + b"not a JSON ledger row\n")
        raise OSError("the accounting store is temporarily unavailable")

    monkeypatch.setattr(loop.meter, "settle", failed_settlement)
    try:
        try:
            reply = loop.tick()
        except Exception as exc:  # noqa: BLE001 - the hand-off under test
            reply = loop.fault(exc)
    finally:
        if saved:
            if saved[-1] is None:
                ledger.unlink()
            else:
                ledger.write_bytes(saved[-1])
    assert refused, "the model must have met a classifier refusal"
    got = _held_after_restart(loop, domain)
    assert got in (PAUSED, STOPPED), (
        f"fault handler returned {reply['code']}; {len(loop.log.cycles())} cycles recorded; restart refusal was {got}"
    )


# Finding 3: the runner's last fallback lost the safety status ----------------------------------------------------------

def test_finding_3_a_fault_that_cannot_record_keeps_the_safety_stop_across_restart(tmp_path, monkeypatch):
    met = []
    loop, domain = enrolled(tmp_path, proposer=_refusing(met))
    refresh = loop.mh.refresh

    def failed_settlement(*args, **kwargs):
        raise OSError("the accounting store is temporarily unavailable")

    def unavailable_workspace():
        if loop._met is not None:
            raise PermissionError("the workspace ledger is temporarily unavailable")
        return refresh()

    with monkeypatch.context() as patch:
        patch.setattr(loop.meter, "settle", failed_settlement)
        patch.setattr(loop.mh, "refresh", unavailable_workspace)
        try:
            loop.tick()
        except Exception as exc:  # noqa: BLE001 - the hand-off under test
            # Exercise only the real runner's exception hand-off; start no runner or process.
            reply = Runner._fault(SimpleNamespace(loop=loop), exc)
        else:
            raise AssertionError("the settlement fault must reach the runner's hand-off")
    assert met and loop._met[1] is not None
    got = _held_after_restart(loop, domain)
    assert got in (PAUSED, STOPPED), (
        f"fault fallback returned {reply['code']}; {len(loop.log.cycles())} cycles recorded; restart refusal was {got}"
    )


# Finding 4: a generation the cycle made was lost from its fault row ----------------------------------------------------

def _making(finished: str, folder: Path) -> tuple[ShadowLoop, object]:
    """A loop whose next tick makes a generation: an adoption, or the monitor's rollback of one made here first."""
    rollback = finished == "rollback"
    domain = domains.WordProblems(folder, pool=480 if rollback else 240, weak=0.7 if rollback else 0.3)
    domain.tutor.strong = 1.0
    settings = replace(SETTINGS, audit_per_tick=80 if rollback else 0)
    loop = ShadowLoop(domain, folder / "loop", settings, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    assert loop.enrol("Review owner")["completion_state"] == "qualified-draft"
    if rollback:
        assert loop._refused() is None
        assert loop.mh.adopt(rig.recommendation(loop))["head_version"] == 1
        domain.tutor.collapsed = True
    return loop, domain


def _made(finished: str, loop: ShadowLoop) -> bool:
    """Whether the generation this scenario's tick makes is in the chain."""
    rows = loop.mh.adoption_rows()
    return len(rows) == (2 if finished == "rollback" else 1) and rows[-1]["kind"] == (
        "rollback" if finished == "rollback" else "adopt")


@pytest.mark.parametrize("finished", ["adoption", "rollback"])
def test_finding_4_a_completed_generation_is_kept_on_the_fault_cycle(tmp_path, monkeypatch, finished):
    loop, _ = _making(finished, tmp_path)
    failed = []
    if finished == "rollback":
        settle = loop.meter.settle

        def after_rollback(*args, **kwargs):
            if loop.mh.adoption_rows()[-1]["kind"] == "rollback" and not failed:
                failed.append(True)
                raise OSError("settlement became unavailable after the monitor's rollback")
            return settle(*args, **kwargs)

        monkeypatch.setattr(loop.meter, "settle", after_rollback)
    else:
        read = loop.meter.reading

        def after_adoption(*args, **kwargs):
            if loop.mh.adoption_rows() and not failed:
                failed.append(True)
                raise OSError("the final budget reading failed after adoption")
            return read(*args, **kwargs)

        monkeypatch.setattr(loop.meter, "reading", after_adoption)
    try:
        loop.tick()
    except Exception as exc:  # noqa: BLE001 - the hand-off under test
        loop.fault(exc)
    assert failed and loop.mh.adoption_rows()[-1]["kind"] == ("rollback" if finished == "rollback" else "adopt")
    row = loop.log.cycles()[-1]
    codes = [status["code"] for status in row["statuses"]]
    code = ROLLED_BACK if finished == "rollback" else ADOPTED
    assert code in codes and (finished != "rollback" or row["code"] == ROLLED_BACK), (
        f"generation {loop.mh._head()[0]} is a completed {finished}, but the cycle kept {codes} and coded {row['code']}"
    )


# Class 1: every walker against every kind of entry ----------------------------------------------------------------------

#: The modules that read what the freeze stands on.
FREEZE_MODULES = (reading, integrity, vault_module, loop_module, api)
#: What lists a folder: the reader's own two functions, and every way the standard library has.
OWN_LISTERS = {"walk", "files_under"}
STANDARD_LISTERS = {"scandir", "listdir", "walk", "fwalk", "iterdir", "glob", "rglob", "iglob"}


def _walkers() -> dict[str, set[str]]:
    """Every function in the freeze's modules that lists a folder, from their code, with what it lists by: the reader's
    own lister (``walk``, ``files_under``) or the standard library's (``os.scandir``, ``os.walk``, ``Path.glob`` ...)."""
    found: dict[str, set[str]] = {}
    for module in FREEZE_MODULES:
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for part in ast.walk(node):
                if not isinstance(part, ast.Call):
                    continue
                name = ast.unparse(part.func)
                if name in OWN_LISTERS or ("." in name and name.rsplit(".", 1)[-1] in STANDARD_LISTERS
                                           and not name.startswith("ast.")):
                    found.setdefault(f"{module.__name__}.{node.name}", set()).add(name)
    return found


def _enveloped(root: Path) -> str:
    """``envelope_sha256`` of ``root`` in place of G6's own package, which is all it ever lists."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(loop_module, "_SOURCE", root)
        return loop_module.envelope_sha256()


#: Each walker, and how this file has it read a tree: the walker itself, or the producer of the freeze's that calls it.
PRODUCERS = {
    "g6.core.reading.walk": lambda root: sorted((os.path.relpath(here, root), sorted(dirs), sorted(files))
                                                for here, dirs, files in reading.walk(root)),
    "g6.core.reading.files_under": lambda root: reading.files_under(root),
    "g6.core.vault._files": lambda root: dict(Vault.seal([root], ledger=None).manifest),
    "g6.integrity.build_manifest": lambda root: integrity.build_manifest(root, patterns=("**/*",)),
    "g6.metaharness.loop._links_under": lambda root: loop_module._links_under([root]),
    "g6.metaharness.loop._listing": lambda root: loop_module._pinned([root]),
    "g6.metaharness.loop.envelope_sha256": _enveloped,
    "g6.metaharness.loop._importable_caches": lambda root: loop_module._importable_caches(root),
}


def test_every_walker_the_freeze_holds_is_one_this_file_reads_through():
    assert set(_walkers()) == set(PRODUCERS), sorted(set(_walkers()) ^ set(PRODUCERS))


def test_the_one_lister_is_the_only_function_that_lists_with_the_standard_library_and_it_takes_names_only():
    standard = {(walker, call) for walker, calls in _walkers().items() for call in calls - OWN_LISTERS}
    assert standard == {("g6.core.reading.walk", "os.scandir")}, sorted(standard)
    lister = next(node for node in ast.walk(ast.parse(inspect.getsource(reading)))
                  if isinstance(node, ast.FunctionDef) and node.name == "walk")
    listing = next(node for node in ast.walk(lister) if isinstance(node, ast.With)
                   and ast.unparse(node.items[0].context_expr.func) == "os.scandir")
    held = listing.items[0].optional_vars.id
    taken = [ast.unparse(node) for node in ast.walk(lister) if isinstance(node, ast.comprehension)
             and ast.unparse(node.iter) == held]
    assert len(taken) == 1, taken                                # the listing is read in one place
    entry = next(node for node in ast.walk(lister) if isinstance(node, ast.comprehension)
                 and ast.unparse(node.iter) == held).target.id
    asked = {node.attr for node in ast.walk(lister) if isinstance(node, ast.Attribute)
             and isinstance(node.value, ast.Name) and node.value.id == entry}
    assert asked == {"name"}, sorted(asked)                      # and each entry gives its name, nothing more


def _tree(root: Path) -> Path:
    for rel in ("keep.py", "sub/inner.py", "sub/deep/leaf.py", "sub/__pycache__/planted.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(rel, encoding="utf-8")
    (root / "empty").mkdir()
    return root


class _NamesOnly:
    """A folder's listing whose entries give their names and fail every other question, as an entry's own predicates
    can (``DirEntry.is_dir`` and the rest), recording each one asked."""

    def __init__(self, scan, error, asked: list) -> None:
        self.scan, self.error, self.asked = scan, error, asked

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.scan.close()

    def close(self):
        self.scan.close()

    def __iter__(self):
        return self

    def __next__(self):
        listing, name = self, next(self.scan).name

        class Entry:
            def __getattr__(self, asked):
                if asked == "name":
                    return name
                listing.asked.append(asked)
                raise listing.error(f"{name} cannot be asked for {asked}")

        return Entry()


ERRORS = {"a denial": PermissionError, "an error never seen": _NeverSeen}


@pytest.mark.parametrize("error", list(ERRORS))
@pytest.mark.parametrize("walker", list(PRODUCERS))
def test_no_predicate_of_a_listed_entry_decides_anything_for_any_walker(tmp_path, monkeypatch, walker, error):
    root = _tree(tmp_path / "tree")
    read = PRODUCERS[walker]
    whole = read(root)
    real, asked = os.scandir, []
    monkeypatch.setattr(os, "scandir", lambda path=".": _NamesOnly(real(path), ERRORS[error], asked))
    try:
        found = read(root)
    except Unreadable:
        found = whole                                 # refused: nothing stood on the listing
    assert found == whole and not asked, (sorted(set(asked)), found, whole)


def _fail_once(patch, owner, name: str, error, when=lambda *args: True) -> list:
    """Make ``owner.name`` raise ``error`` once, the first time ``when`` holds of its arguments."""
    real, failed = getattr(owner, name), []

    def once(*args, **kwargs):
        if not failed and when(*args):
            failed.append(name)
            raise error(f"a failure injected at {name}")
        return real(*args, **kwargs)

    patch.setattr(owner, name, once)
    return failed


def _after_listing(patch, folder: Path, act) -> list:
    """Run ``act`` once, the moment the listing of ``folder`` has been read to its end."""
    real, done = os.scandir, []

    class Listed:
        def __init__(self, scan, watched: bool) -> None:
            self.scan, self.watched = scan, watched

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.scan.close()

        def close(self):
            self.scan.close()

        def __iter__(self):
            return self

        def __next__(self):
            try:
                return next(self.scan)
            except StopIteration:
                if self.watched and not done:
                    done.append(True)
                    act()
                raise

    patch.setattr(os, "scandir", lambda path=".": Listed(real(path), _same(path, folder)))
    return done


def _unaskable(patch, target: Path, error) -> list:
    """Neither stat of ``target`` answers."""
    hit: list = []
    for name in ("lstat", "stat"):
        hit = _fail_many(patch, name, target, error, hit)
    return hit


def _fail_many(patch, name: str, target: Path, error, hit: list) -> list:
    real = getattr(os, name)

    def asked(path, *args, **kwargs):
        if _same(path, target):
            hit.append(name)
            raise error(f"{path} cannot be asked")
        return real(path, *args, **kwargs)

    patch.setattr(os, name, asked)
    return hit


def _a_link_at_the_second_ask(patch, target: Path) -> list:
    """``target`` is the folder its listing held the first time it is asked, and a link from then on, as a folder is
    that was swapped for a link while the walk read its siblings."""
    real, asks = os.lstat, []

    def lstat(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        if not _same(path, target):
            return info
        asks.append(True)
        if len(asks) == 1:
            return info
        return os.stat_result((stat.S_IFLNK | 0o777, *info[1:]))

    patch.setattr(os, "lstat", lstat)
    return asks


#: Each kind of entry a walk can meet that it cannot read as it stands, how this file makes one under ``root`` (returning
#: what shows the walk met it), and whether the walk may still read the tree whole (it found another way to ask) or must
#: refuse.
KINDS = {
    "a folder that cannot be listed": (
        lambda patch, root, error: _fail_once(patch, os, "scandir", error, lambda path=".": _same(path, root / "sub")), False),
    "a folder that cannot be asked": (lambda patch, root, error: _unaskable(patch, root / "sub", error), True),
    "a file that cannot be asked": (lambda patch, root, error: _unaskable(patch, root / "sub" / "inner.py", error), False),
    "a folder gone since its folder was listed": (
        lambda patch, root, error: _after_listing(patch, root, lambda: shutil.rmtree(root / "sub")), False),
    "a file gone since its folder was listed": (
        lambda patch, root, error: _after_listing(patch, root / "sub", lambda: (root / "sub" / "inner.py").unlink()), False),
    "a folder that is a link when the walk comes to go into it": (
        lambda patch, root, error: _a_link_at_the_second_ask(patch, root / "sub"), False),
}
#: The kinds an error's own kind makes no difference to: the file system answers them itself.
OWN_ERROR = ("a folder gone since its folder was listed", "a file gone since its folder was listed",
             "a folder that is a link when the walk comes to go into it")
#: An entry has one answer (the freeze's second follow-up): a folder that is a link by a later ask than the lister's is
#: another entry, so the two loop walkers, which once named it as the link it had become, refuse it or name it as what
#: they could not read, as every walker does.


@pytest.mark.parametrize("kind, error", [(kind, error) for kind in KINDS for error in ERRORS
                                         if kind not in OWN_ERROR or error == "a denial"])
@pytest.mark.parametrize("walker", list(PRODUCERS))
def test_every_walker_reads_every_kind_of_entry_whole_or_not_at_all(tmp_path, walker, kind, error):
    root = _tree(tmp_path / "tree")
    read = PRODUCERS[walker]
    make, may_read = KINDS[kind]
    if walker == "g6.core.vault._files":             # a sealed split is read against its seal
        vault = Vault.seal([root], ledger=None)
        read = lambda root: vault.verify_unchanged()  # noqa: E731 - one producer among the table's
    whole = read(root)
    with pytest.MonkeyPatch.context() as patch:
        met = make(patch, root, ERRORS[error])
        try:
            found = read(root)
        except Unreadable:
            found = Unreadable
    assert met, f"the walk never met {kind}"
    named = walker == "g6.metaharness.loop._links_under" and found is not Unreadable and bool(found[1])
    if may_read:
        assert found is Unreadable or named or found == whole, found
    else:
        assert found is Unreadable or named, f"{walker} read {kind} as {found!r}"


@WINDOWS
def test_a_junction_is_read_as_each_walker_read_it_before(tmp_path):
    import _winapi

    root = _tree(tmp_path / "tree")
    _winapi.CreateJunction(str(root / "sub"), str(root / "junction"))
    names = {path.relative_to(root).as_posix() for path in reading.files_under(root)}
    assert {"junction/inner.py", "junction/deep/leaf.py", "sub/inner.py", "keep.py"} <= names
    links, unlisted = loop_module._links_under([root])
    assert links == [root / "junction"] and not unlisted
    assert "junction" in loop_module._listing(root) and not any(name.startswith("junction/")
                                                                for name in loop_module._listing(root))


def test_a_link_is_never_gone_into_and_one_to_nothing_is_unreadable(tmp_path):
    root = _tree(tmp_path / "tree")
    try:
        os.symlink(root / "sub", root / "to_folder", target_is_directory=True)
        os.symlink(root / "keep.py", root / "to_file")
    except OSError as exc:
        pytest.skip(f"this account may not make a symbolic link ({type(exc).__name__})")
    names = {path.relative_to(root).as_posix() for path in reading.files_under(root)}
    assert "to_file" in names and not any(name.startswith("to_folder") for name in names)
    assert set(loop_module._links_under([root])[0]) == {root / "to_folder", root / "to_file"}
    os.symlink(root / "nowhere", root / "to_nothing")
    with pytest.raises(Unreadable):
        reading.files_under(root)
    assert loop_module._listing(root)["to_nothing"] == "a link"


# Class 2: what a cycle met or made is read where it is kept, on every way out -------------------------------------------

def _calls(function) -> list[str]:
    """Every call ``function`` makes, from its own code, by the dotted name it is called by."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    return sorted({name for node in ast.walk(tree) if isinstance(node, ast.Call)
                   and re.fullmatch(r"[\w.]+", name := ast.unparse(node.func))})


def _owner(loop: ShadowLoop, name: str):
    """What holds the function a call names, and its name there: the handle or a part of it (``self.log.cycle``), the
    runner's loop (``self.loop.fault``), or the loop's module (``_coded``, ``Coordination``); ``None`` for a built-in or
    a method of a value the function made, which is nothing of the loop's to fail."""
    parts = name.split(".")
    if parts[0] == "self":
        parts = parts[2:] if parts[1] == "loop" else parts[1:]
        owner = loop
        for part in parts[:-1]:
            owner = getattr(owner, part, None)
        return (owner, parts[-1]) if owner is not None and callable(getattr(owner, parts[-1], None)) else None
    held = getattr(loop_module, name, None) if len(parts) == 1 else None
    return (loop_module, name) if inspect.isfunction(held) or inspect.isclass(held) else None


#: Every function a tick that raised goes out through, from the runner's hand-off down: each call of each is failed.
WAYS_OUT = (Runner._fault, loop_module._exclusive, ShadowLoop.fault, ShadowLoop._record, ShadowLoop.lost_cycle)
#: What the fault's ways out are failed with: what its handlers name, and an error none of them does.
FAILURES = {"an OSError": OSError, "a LedgerError": LedgerError, "an error never seen": _NeverSeen}
#: Each safety status a cycle can meet (review 2's), and the code its row carries.
SAFETY = {name: code for name, code in fu2.STATUSES.items() if code is not None}
#: The ways out findings 2 and 3 went through, which every status is failed at; a classifier's refusal is failed at all.
FOUND = (("self.mh.refresh", "a LedgerError"), ("self.loop.fault", "an OSError"))


def _raised_after(status: str, folder: Path) -> tuple[ShadowLoop, object, BaseException]:
    """A tick whose cycle met ``status`` and then raised past its own guard, at a settlement that failed."""
    folder.mkdir()
    domain, met, holder = domains.WordProblems(folder), [], {}

    def propose(system, user):
        met.append(True)
        return fu2._meets(status, holder["loop"], domain)

    loop, _ = enrolled(folder, domain=domain, proposer=FakeLLM(propose))
    holder["loop"] = loop
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(loop.meter, "settle", lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("the accounting store is temporarily unavailable")))
        with pytest.raises(OSError) as raised:
            loop.tick()
    assert met
    return loop, domain, raised.value


def _ways_out(loop: ShadowLoop) -> list[str]:
    """Every call the ways out make that is the loop's to fail, each function once whatever names call it."""
    found: dict[tuple[int, str], str] = {}
    for name in sorted({name for function in WAYS_OUT for name in _calls(function)}):
        held = _owner(loop, name)
        if held is not None:
            found.setdefault((id(held[0]), held[1]), name)
    return sorted(found.values())


@pytest.mark.parametrize("status", list(SAFETY))
def test_every_way_out_of_a_tick_that_raised_leaves_a_safety_stop_held_after_a_restart(tmp_path, status):
    reached, wrong = set(), []
    probe, _, _ = _raised_after(status, tmp_path / "probe")      # a handle the names are read against
    names = _ways_out(probe)
    assert {"self.mh.refresh", "_reloaded", "Coordination", "self.loop.fault", "self.loop.lost_cycle", "self._now",
            "self._record", "self.log.cycle"} <= set(names), names
    for failure, error in FAILURES.items():
        for name in names:
            if status != "a classifier's refusal" and (name, failure) not in FOUND:
                continue
            loop, domain, exc = _raised_after(status, tmp_path / re.sub(r"\W", "_", f"{name}_{failure}"))
            owner, attr = _owner(loop, name)
            with pytest.MonkeyPatch.context() as patch:
                failed = _fail_once(patch, owner, attr, error)
                try:
                    Runner._fault(SimpleNamespace(loop=loop), exc)
                except Exception:  # noqa: BLE001 - the runner's own pass notes it and goes on (``Runner.run``)
                    pass
            if not failed:
                continue                              # this way out never makes this call
            reached.add(name)
            got = _held_after_restart(loop, domain)
            if got not in (PAUSED, STOPPED):
                wrong.append(f"{name} failing with {failure}: the restart refused with {got}")
    assert not wrong, wrong
    assert {name for name, _ in FOUND} <= reached, sorted(reached)



def test_a_tick_that_raises_at_any_step_after_its_cycle_is_held_with_no_fault_at_all(tmp_path):
    """The calls ``_run`` makes after its cycle (review 2's inventory), each failing with an error no handler names, and
    nobody calling ``fault``: a caller that is no runner, or a runner that died there."""
    reached, wrong = [], []
    for step in fu2.STEPS:
        folder = tmp_path / re.sub(r"\W", "_", step)
        folder.mkdir()
        domain, met, holder = domains.WordProblems(folder), [], {}

        def propose(system, user):
            met.append(True)
            return fu2._meets("a classifier's refusal", holder["loop"], domain)

        loop, _ = enrolled(folder, domain=domain, proposer=FakeLLM(propose))
        holder["loop"] = loop
        owner, attr = _owner(loop, step) or (None, None)
        if owner is None:
            owner, attr = getattr(loop_module, step.split(".")[0]), step.split(".")[-1]     # a module G6 calls into
        with pytest.MonkeyPatch.context() as patch:
            failed = _fail_once(patch, owner, attr, _NeverSeen, lambda *args: bool(met))
            try:
                loop.tick()
            except Exception:  # noqa: BLE001 - nobody hands it to ``fault``
                pass
        if not failed:
            continue
        reached.append(step)
        got = _held_after_restart(loop, domain)
        if got not in (PAUSED, STOPPED):
            wrong.append(f"{step}: the restart refused with {got}")
    assert not wrong, wrong
    assert fu2.ALWAYS <= set(reached), reached


def test_a_sealed_file_the_guard_counts_only_on_its_way_out_is_kept_too(tmp_path, monkeypatch):
    """The sealed split's guard counts a file still held open as it leaves, after the cycle's work has ended: a tick that
    then raises writes that breach on its way out as well."""
    from contextlib import contextmanager

    loop, domain = enrolled(tmp_path)
    left = []

    @contextmanager
    def counted_on_the_way_out(vault):
        breaches: list[str] = []
        try:
            yield breaches
        finally:
            breaches.append("a sealed file still held open")
            left.append(True)

    monkeypatch.setattr(loop_module, "sealed_closed", counted_on_the_way_out)
    monkeypatch.setattr(loop.meter, "settle", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("held")))
    with pytest.raises(OSError):
        loop.tick()
    assert left and _held_after_restart(loop, domain) == PAUSED


@pytest.mark.parametrize("failure", list(FAILURES))
def test_a_pause_the_log_will_not_take_from_a_tick_on_its_way_out_stops_the_folder(tmp_path, monkeypatch, failure):
    met = []
    loop, domain = enrolled(tmp_path, proposer=_refusing(met))
    error = FAILURES[failure]
    monkeypatch.setattr(loop.log, "pause", lambda reason: (_ for _ in ()).throw(error("the log is held")))
    monkeypatch.setattr(loop.meter, "settle", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("held too")))
    with pytest.raises(OSError):
        loop.tick()
    assert met and stop_in_force(loop.root).code == STOPPED
    assert _held_after_restart(loop, domain) == STOPPED


def test_when_no_store_takes_a_safety_status_this_process_spends_no_more(tmp_path, monkeypatch):
    met = []
    loop, _ = enrolled(tmp_path, proposer=_refusing(met))
    for owner, name in ((loop.log, "pause"), (loop.log, "cycle"), (loop_module, "ask_to_stop")):
        monkeypatch.setattr(owner, name, lambda *args, **kwargs: (_ for _ in ()).throw(OSError("the disk is gone")))
    assert loop.tick()["warning_card"]["code"] == CANNOT_RECORD
    assert met and loop._stop().code == CANNOT_RECORD      # the limit of it: a restart then reads no store at all


def test_a_fault_after_a_row_that_holds_the_safety_stop_writes_no_second_row(tmp_path, monkeypatch):
    met = []
    loop, domain = enrolled(tmp_path, proposer=_refusing(met))
    beat = loop._beat

    def after_the_row(state, code, **said):
        if code == CLASSIFIER_STOP:
            raise OSError("the heartbeat cannot be written")
        return beat(state, code, **said)

    monkeypatch.setattr(loop, "_beat", after_the_row)
    with pytest.raises(OSError) as raised:
        loop.tick()
    rows = len(loop.log.cycles())
    reply = Runner._fault(SimpleNamespace(loop=loop), raised.value)
    assert met and reply["warning_card"]["code"] == PAUSED and len(loop.log.cycles()) == rows == 1
    assert _held_after_restart(loop, domain) == PAUSED


#: What a harness call is cut short with once it has made the generation: an error, and, for the monitor's audit, which
#: sends requests, the two a cycle ends on without a row when nothing is owed (a provider's limit, a stop at a call).
CUT_SHORT = {"an OSError": lambda: OSError("the ticket could not be written"),
             "an error never seen": lambda: _NeverSeen("never seen"),
             "a provider's limit": lambda: _AtLimit(NOW, "HTTP 429: the provider's limit was met"),
             "a stop met at a model call": lambda: _Halted(STOPPED, "a person stopped the loop")}
#: The harness call that makes each generation.
MAKER = {"adoption": "adopt", "rollback": "audit"}


def _says_what_it_made(finished: str, loop: ShadowLoop, rows_before: int) -> str:
    """Why the rows this tick wrote do not say the generation it made, or ``""``: the last row keeps its status, and a
    rollback codes the row and gives the log its generation."""
    rows = loop.log.cycles()[rows_before:]
    if not rows:
        return "the tick wrote no row"
    row, code = rows[-1], ROLLED_BACK if finished == "rollback" else ADOPTED
    codes = [status["code"] for status in row["statuses"]]
    if code not in codes:
        return f"the row kept {codes}"
    if finished == "rollback" and (row["code"] != ROLLED_BACK or loop.log.generation() != loop.mh._head()[0]):
        return f"the row is coded {row['code']} and the log holds generation {loop.log.generation()}"
    return ""


@pytest.mark.parametrize("finished", ["adoption", "rollback"])
def test_a_failure_at_each_step_after_a_generation_is_made_leaves_it_on_the_row(tmp_path, finished):
    reached, wrong = [], []
    steps = [(step, failure) for step in fu2.STEPS
             for failure in (("an OSError", "an error never seen") if step in ("self.meter.settle", "self._record")
                             else ("an OSError",))]
    for step, failure in steps:
        folder = tmp_path / re.sub(r"\W", "_", f"{step}_{failure}")
        folder.mkdir()
        loop, _ = _making(finished, folder)
        before = len(loop.log.cycles())
        owner, attr = _owner(loop, step) or (None, None)
        if owner is None:
            owner, attr = getattr(loop_module, step.split(".")[0]), step.split(".")[-1]
        with pytest.MonkeyPatch.context() as patch:
            failed = _fail_once(patch, owner, attr, FAILURES[failure], lambda *args: _made(finished, loop))
            try:
                loop.tick()
            except Exception as exc:  # noqa: BLE001 - the runner's hand-off
                Runner._fault(SimpleNamespace(loop=loop), exc)
        if not failed:
            continue
        assert _made(finished, loop)
        reached.append(step)
        missing = _says_what_it_made(finished, loop, before)
        if missing:
            wrong.append(f"{step} failing with {failure}: {missing}")
    assert not wrong, wrong
    assert {"self._record", "self.meter.reading"} <= set(reached), reached


@pytest.mark.parametrize("finished, cut", [("adoption", "an OSError"), ("adoption", "an error never seen"),
                                           *(("rollback", cut) for cut in CUT_SHORT)])
def test_a_harness_that_raises_once_it_has_made_a_generation_leaves_it_on_the_row(tmp_path, finished, cut):
    loop, _ = _making(finished, tmp_path)
    before, real = len(loop.log.cycles()), getattr(loop.mh, MAKER[finished])

    def made_then_raised(*args, **kwargs):
        real(*args, **kwargs)
        assert _made(finished, loop)
        raise CUT_SHORT[cut]()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(loop.mh, MAKER[finished], made_then_raised)
        try:
            loop.tick()
        except Exception as exc:  # noqa: BLE001 - the runner's hand-off
            Runner._fault(SimpleNamespace(loop=loop), exc)
    assert _made(finished, loop)
    assert not _says_what_it_made(finished, loop, before), _says_what_it_made(finished, loop, before)


def test_a_generation_one_row_has_said_is_not_said_again_by_a_later_fault(tmp_path, monkeypatch):
    loop, _ = _making("adoption", tmp_path)
    read, failed = loop.meter.reading, []

    def after_adoption(*args, **kwargs):
        if loop.mh.adoption_rows() and not failed:
            failed.append(True)
            raise OSError("the final budget reading failed after adoption")
        return read(*args, **kwargs)

    monkeypatch.setattr(loop.meter, "reading", after_adoption)
    try:
        loop.tick()
    except Exception as exc:  # noqa: BLE001 - the runner's hand-off
        loop.fault(exc)
    assert ADOPTED in [status["code"] for status in loop.log.cycles()[-1]["statuses"]]
    loop.fault(RuntimeError("a later pass broke before its tick began"))      # the handle holds what the last tick held
    assert [status["code"] for status in loop.log.cycles()[-1]["statuses"]] == [CYCLE_FAILED]