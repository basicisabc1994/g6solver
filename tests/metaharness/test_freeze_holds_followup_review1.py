# SPDX-License-Identifier: Apache-2.0
"""Review 1 of claude/freeze-holds-followup: Codex's six findings (its run was stopped by its cybersecurity filter after
writing them, and was not sent again) and a fresh Claude reviewer's five, as they reproduced them, on the freeze's own rig
(its temporary folders aside: pytest's serve here). Some overlap: Codex 2 and Claude 1 are the sealed split unreadable at
a tick's start; Codex 5 and Claude 4 the envelope unreadable, at a tick and at enrolment; Codex 3 and Claude 5 the cache
of a source with a dotted name. FakeLLM is the only model; every lock is a real Windows handle, closed before its test
ends."""
from __future__ import annotations

import ctypes
import importlib.util
import os
import py_compile
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

import g6
from g6.core.llm import FakeLLM, LLMError
from g6.metaharness import loop as loop_module
from g6.metaharness import shadow
from g6.metaharness.domain import Judge
from g6.metaharness.loop import FROZEN_CHANGED, JUDGE_UNPINNED, ShadowLoop, _pinned, _roots
from g6.self_maintenance.usage import MODEL_REFUSED

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the locks, the junction and the named stream are Windows's")
if os.name == "nt":      # elsewhere there is no msvcrt: importing it there stopped the collection of every file run with this one
    import msvcrt


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rig = _load(Path(__file__).with_name("test_freeze_holds_review2.py"), "frzfu1_review_rig")
domains, enrolled, SETTINGS, NOW = rig.domains, rig.enrolled, rig.SETTINGS, rig.NOW
CLASSIFIER_STOP, PAUSED = "G6_E_LOOP_CLASSIFIER_STOP", "G6_E_LOOP_PAUSED"


def _create(path: Path, flags: int):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, flags, None)   # GENERIC_READ, no sharing, existing
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return lambda: kernel.CloseHandle(handle)


def exclusive(path: Path):
    """What another process does when it opens a file and shares nothing (an editor, an indexer, a backup tool)."""
    return _create(path, 0x80)


def held_folder(folder: Path):
    """What another process does when it opens a folder and shares nothing: the folder cannot be listed meanwhile."""
    return _create(folder, 0x02000000)                 # FILE_FLAG_BACKUP_SEMANTICS: a folder


def _words(loop: ShadowLoop, reply: dict) -> tuple[str, str]:
    """The code and the words of what the tick left: its cycle row when one was recorded, else its reply, which is no row
    and carries its words in ``message`` (Codex's tests read ``reason`` off either)."""
    if loop.log.cycles():
        row = loop.log.cycles()[-1]
        return row["code"], row["reason"]
    return reply["code"], reply["message"]


# Codex ----------------------------------------------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="Ruled out under the threat model (honest drift): a named stream is reached only by "
                   "spelling its name, and Windows and sync tools write named streams on files nobody changes; it is in "
                   "the design's list of limits")
def test_codex_1_changed_judging_file_named_stream_refuses_adoption(tmp_path, monkeypatch):
    judge_dir = tmp_path / "judge"
    judge_dir.mkdir()
    rule = judge_dir / "rule.txt"
    rule.write_text("judging policy", encoding="utf-8")
    zone = Path(str(rule) + ":Zone.Identifier")
    zone.write_text("[ZoneTransfer]\nZoneId=3\n", encoding="utf-8")
    source = judge_dir / "judge.py"
    source.write_text(
        "from pathlib import Path\n"
        "from frz2_domains import GSM\n"
        "def verdict(output, expected):\n"
        "    zone = Path(str(Path(__file__).with_name('rule.txt')) + ':Zone.Identifier').read_text()\n"
        "    return 'ZoneId=3' in zone and GSM.oracle(output, expected)\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "followup_stream_judge", None)
    module = _load(source, "followup_stream_judge")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, "independent")
    loop, _ = enrolled(tmp_path, domain=domain)
    sample = domain.pool[0]
    assert module.verdict("Answer: " + sample.expected, sample.expected)
    before = loop._frozen()
    measured, changed = shadow._measured, []

    def save(report):
        zone.write_text("[ZoneTransfer]\nZoneId=4\n", encoding="utf-8")
        changed.append(True)
        return measured(report)

    monkeypatch.setattr(shadow, "_measured", save)
    result = loop.tick()
    assert changed and not module.verdict("Answer: " + sample.expected, sample.expected)
    assert loop._frozen() == before, "precondition: only the named stream changed"
    assert not loop.mh.adoption_rows(), f"adopted after a judging file's named stream changed: {result.get('outcome')}"


def test_codex_2_unreadable_sealed_bytes_are_a_frozen_change(tmp_path):
    loop, domain = enrolled(tmp_path)
    with domain.sealed_file.open("r+b", buffering=0) as stream:
        size = domain.sealed_file.stat().st_size
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, size)
        try:
            try:
                reply = loop.tick()
            except Exception as exc:  # noqa: BLE001 - the real runner routes tick failures through this same method
                reply = loop.fault(exc)
            code, words = _words(loop, reply)
            assert code == FROZEN_CHANGED, f"unreadable sealed bytes were coded {code}"
            # the vault holds its roots as the system reads a name (normcased), which on Windows is the same file
            assert os.path.normcase(str(domain.sealed_file)) in os.path.normcase(words)
            assert not loop.mh.adoption_rows()
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, size)


def test_codex_3_an_interpreter_cache_for_a_dotted_source_does_not_change_freeze(tmp_path, monkeypatch):
    source = tmp_path / "criterion.v1.py"
    source.write_text("def permits():\n    return True\n", encoding="utf-8")
    before = _pinned([tmp_path])
    spec = importlib.util.spec_from_file_location("followup_dotted_source", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)       # Python's own source loader writes its cache here
    monkeypatch.setattr(sys, "pycache_prefix", None)
    spec.loader.exec_module(module)
    assert module.permits()
    assert Path(importlib.util.cache_from_source(str(source))).is_file()
    assert _pinned([tmp_path]) == before, "an interpreter-produced bytecode cache changed the freeze"


def test_codex_4_a_readable_ntfs_filename_cannot_escape_enrolments_refusal(tmp_path):
    domain = domains.WordProblems(tmp_path)
    rules = tmp_path / "rules"
    rules.mkdir()
    source = rules / "rule-\ud800.dat"
    source.write_bytes(b"judging data")
    assert source.read_bytes() == b"judging data"
    domain.roots = (*_roots(domain), rules)
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    escaped = None
    try:
        reply = loop.enrol("Review owner")
    except Exception as exc:  # noqa: BLE001 - what escapes is what this looks for
        escaped = exc
    assert escaped is None, f"readable judging file escaped enrolment: {type(escaped).__name__}"
    assert reply["completion_state"] == "qualified-draft" or reply["warning_card"]["code"] == JUDGE_UNPINNED


def test_codex_5_unreadable_envelope_source_is_a_frozen_change(tmp_path):
    loop, _ = enrolled(tmp_path)
    source = Path(loop_module.__file__)
    with source.open("rb", buffering=0) as stream:              # a read-only handle: no checkout byte changes
        size = source.stat().st_size
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, size)
        try:
            try:
                reply = loop.tick()
            except Exception as exc:  # noqa: BLE001 - as the runner does
                reply = loop.fault(exc)
            code, words = _words(loop, reply)
            assert code == FROZEN_CHANGED, f"unreadable envelope was coded {code}"
            assert str(source) in words
            assert not loop.mh.adoption_rows()
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, size)


def test_codex_6_unlistable_sealed_subdirectory_cannot_hide_added_file_at_adoption(tmp_path, monkeypatch):
    loop, domain = enrolled(tmp_path)
    hidden = domain.sealed_file.parent / "additional-fixtures"
    hidden.mkdir()
    assert domain.sealed().vault.verify_unchanged() == ()
    measured, closing = shadow._measured, []

    def add_and_lock(report):
        (hidden / "new-case.json").write_bytes(b'{"expected": "changed"}')
        closing.append(held_folder(hidden))
        return measured(report)

    monkeypatch.setattr(shadow, "_measured", add_and_lock)
    try:
        reply = loop.tick()
        assert closing, "precondition: edit happened before adoption's own last check"
        assert not loop.mh.adoption_rows(), f"adopted with an unlistable changed sealed subtree: {reply.get('outcome')}"
    finally:
        for release in closing:
            assert release()
        if closing:
            assert domain.sealed().vault.verify_unchanged(), "precondition: the added fixture changes the vault"


# Claude ---------------------------------------------------------------------------------------------------------------

def _unenrolled(tmp_path, domain=None) -> ShadowLoop:
    return ShadowLoop(domain or domains.WordProblems(tmp_path), tmp_path / "loop", SETTINGS,
                      proposer=domains.proposes({"prompt": domains.WORKING}), now=lambda: NOW, clock=domains.Clock())


def test_claude_1_a_tick_whose_start_reading_cannot_read_the_sealed_split_refuses_as_a_frozen_change(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop = _unenrolled(tmp_path, domain)
    assert loop.enrol("Review owner")["completion_state"] == "qualified-draft"
    release = exclusive(domain.sealed_file)
    try:
        with pytest.raises(PermissionError):
            domain.sealed_file.read_bytes()
        try:
            reply = loop.tick()
        except Exception as exc:  # noqa: BLE001 - the defect: the unreadable reading escapes the tick as a raise
            pytest.fail(f"the tick raised {type(exc).__name__} ({exc}) instead of refusing as {FROZEN_CHANGED}")
        assert reply["warning_card"]["code"] == FROZEN_CHANGED, reply["warning_card"]["code"]
        assert not loop.mh.adoption_rows()
    finally:
        release()


def runner_tick(loop: ShadowLoop) -> dict:
    """One tick as ``g6.metaharness.runner.Runner._tick`` and ``_fault`` run it."""
    try:
        return loop.tick()
    except Exception as exc:  # noqa: BLE001 - as the runner does
        try:
            return loop.fault(exc)
        except Exception as second:  # noqa: BLE001 - as the runner does
            return loop.lost_cycle(second)


def test_claude_2_a_classifier_stop_is_kept_when_the_last_reading_cannot_read_the_sealed_split(tmp_path):
    domain = domains.WordProblems(tmp_path)
    held: dict = {}
    asked: list[str] = []

    def propose(system: str, user: str) -> str:
        asked.append(user)
        if len(asked) == 1:
            held["release"] = exclusive(domain.sealed_file)     # another process takes the sealed file mid-cycle
            raise LLMError(f"{MODEL_REFUSED}: permission denied")
        return '{"prompt": "No useful improvement"}'

    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=FakeLLM(propose), now=lambda: NOW,
                      clock=domains.Clock())
    assert loop.enrol("Review owner")["completion_state"] == "qualified-draft"
    try:
        runner_tick(loop)
    finally:
        if "release" in held:
            held.pop("release")()
    assert asked, "the proposer was never asked, so no classifier stop was met"
    kept = [status["code"] for row in loop.log.cycles() for status in (row, *row.get("statuses", ()))]
    assert CLASSIFIER_STOP in kept and FROZEN_CHANGED in kept, kept
    following = runner_tick(loop)                              # the file is readable again
    assert following["warning_card"]["code"] == PAUSED, following["warning_card"]["code"]


def test_claude_3_a_junction_to_the_loops_folder_stops_the_loop_while_a_sibling_folder_cannot_be_listed(tmp_path):
    import _winapi

    judge_dir = tmp_path / "judge"
    (judge_dir / "data").mkdir(parents=True)
    (judge_dir / "data" / "table.json").write_text("{}", encoding="utf-8")
    source = judge_dir / "rv3_judge.py"
    source.write_text("def verdict(output, expected):\n"
                      "    return str(output).strip().endswith(str(expected))\n", encoding="utf-8")
    module = _load(source, "frzfu1_judge3")
    loop_dir = tmp_path / "loop"
    loop_dir.mkdir()
    _winapi.CreateJunction(str(loop_dir), str(judge_dir / "out"))   # a person's shortcut from the judge to the loop
    assert (judge_dir / "out").is_junction()
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, "independent")
    release = held_folder(judge_dir / "data")
    try:
        with pytest.raises(PermissionError):
            list((judge_dir / "data").iterdir())
        try:
            ShadowLoop(domain, loop_dir, SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                       now=lambda: NOW, clock=domains.Clock())
            refused = ""
        except ValueError as exc:
            refused = str(exc)
    finally:
        release()
    written = sorted(path.name for path in loop_dir.iterdir())
    assert refused and not written, f"the loop was built on a folder a junction under the judge's root points at: {written}"


def test_claude_4_an_enrolment_that_cannot_read_a_file_of_the_envelope_refuses_as_unpinned(tmp_path):
    held = Path(g6.__file__).resolve().parent / "integrity" / "tree.py"
    assert held.is_file()
    loop = _unenrolled(tmp_path)
    assert not loop.problems
    release = exclusive(held)
    try:
        with pytest.raises(PermissionError):
            held.read_bytes()
        try:
            reply = loop.enrol("Review owner")
        except Exception as exc:  # noqa: BLE001 - the defect: the unreadable reading escapes the enrolment as a raise
            pytest.fail(f"the enrolment raised {type(exc).__name__} instead of refusing as {JUDGE_UNPINNED}")
    finally:
        release()
    assert reply["warning_card"]["code"] == JUDGE_UNPINNED, reply["warning_card"]["code"]
    assert loop.log.enrolled() is None


def _interpreter_cache(source: Path, monkeypatch, optimization="") -> Path:
    """Write the cache the interpreter writes for ``source``, where it writes it with no cache prefix."""
    monkeypatch.setattr(sys, "pycache_prefix", None)
    target = Path(importlib.util.cache_from_source(str(source), optimization=optimization))
    written = Path(py_compile.compile(str(source), cfile=str(target), doraise=True))
    assert written == target and target.parent.name == "__pycache__"
    return target


def test_claude_5_the_interpreters_own_cache_under_a_root_is_no_frozen_change_whatever_the_source_is_named(tmp_path,
                                                                                                          monkeypatch):
    judge = tmp_path / "judge"
    judge.mkdir()
    plain, dotted = judge / "rules.py", judge / "rules.v2.py"
    for source in (plain, dotted):
        source.write_text("ACCEPTED = 'yes'\n", encoding="utf-8")
    domain = domains.WordProblems(tmp_path)
    domain.roots = (judge, *_roots(domains.WordProblems(tmp_path)))
    loop = _unenrolled(tmp_path, domain)
    assert loop.enrol("Review owner")["completion_state"] == "qualified-draft"
    assert loop._changed() == []
    _interpreter_cache(plain, monkeypatch)
    assert loop._changed() == []
    cache = _interpreter_cache(dotted, monkeypatch)
    assert loop._changed() == [], f"the interpreter's own cache {cache.name} reads as a change to what judges"
