# SPDX-License-Identifier: Apache-2.0
"""Each finding of the cross-family review of the loop, as the input that showed it. Each was seen failing first."""
from __future__ import annotations

import ast
import contextlib
import dataclasses
import glob
import importlib.util
import io
import json
import mmap
import os
import random
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core import host_models, llm, model_calls, stop
from g6.core.llm import EmptyRetry, FakeLLM, FallbackCaller, LLMError
from g6.core.vault import AccessDenied
from g6.integrity import build_manifest
from g6.metaharness import JUDGING_PATTERNS, Model, SuiteCase
from g6.metaharness import domain as sealed_module
from g6.metaharness.domain import Judge, sealed_closed
from g6.metaharness.loop import (
    ENVELOPE_PATTERNS,
    LoopSettings,
    ShadowLoop,
    _Counted,
    _Halted,
    _Refused,
    _request_digest,
    _Requests,
)
from g6.metaharness.method import Method
from g6.metaharness.schema import Workflow, default_objectives
from g6.metaharness.shadow import lineage
from g6.operator.store import OperatorStore
from g6.self_maintenance.envelope import ask_to_stop, stop_to_lift
from g6.self_maintenance.pacing import BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import AccountReader, UsageUnavailable, WatchedClaude

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
FAST = Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1)
SETTINGS = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=FAST, audit_per_tick=60)


def _loop(tmp_path, domain=None, proposer=None, settings=SETTINGS, now=lambda: NOW, **kw):
    domain = domain if domain is not None else domains.WordProblems(tmp_path)
    proposer = proposer if proposer is not None else domains.proposes({"prompt": domains.WORKING})
    return ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer, now=now, clock=domains.Clock(), **kw), domain


def _enrolled(tmp_path, **kw):
    loop, domain = _loop(tmp_path, **kw)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, domain


def _code(reply: dict) -> str:
    return reply["warning_card"]["code"]


def _with_model(domain, caller) -> None:
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(caller)})


# Finding 1: a tick is charged what reached the backend, and stops at what it reserved -----------------------------------

class Retrying(FakeLLM):
    """Answers through a backend it asks ``tries`` times, and notes none of them: the retries the reviewer counted."""

    def __init__(self, inner, tries: int = 8) -> None:
        super().__init__()
        self.inner, self.tries, self.backend = inner, tries, 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.backend += self.tries
        return self.inner(system, user)


def _retrying_blanks(base):
    """``base`` with a retry of a blank reply inside it, as a caller of a domain's own may have. No check can see that one, so
    these tests hold what the metering does about it. ``EmptyRetry`` itself is refused around a declared model."""

    class RetriesBlanks(base):
        def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
            for _ in range(4):
                text = super().complete(system, user, max_tokens=max_tokens)
                if text.strip():
                    return text
            raise LLMError("empty completion on 4 attempts")

    return RetriesBlanks


def test_1_a_retry_a_caller_notes_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)
    tutor, attempts = domain.tutor, []

    def blank_first(system: str, user: str) -> str:
        attempts.append(user)
        return "" if attempts.count(user) == 1 else tutor(system, user)

    backend = _retrying_blanks(FakeLLM)(blank_first)
    _with_model(domain, backend)
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == len(backend.calls) + len(proposer.calls)


def test_1_each_attempt_of_a_fallback_is_charged():
    """The loop refuses a model with a fallback, so this is the caller alone: it is still charged attempt by attempt."""

    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: the first backend is down")

    first, second = FakeLLM(down), FakeLLM()
    requests = _Requests()
    requests.open(10)
    _Counted(FallbackCaller((first, second)), requests).complete("system", "user")
    assert len(first.calls) > 0 and requests.sent == len(first.calls) + len(second.calls)


def test_1_a_resend_after_retry_after_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)

    def resent_once(system: str, user: str) -> str:
        for found in llm._WAITS.get():
            found.append({"url": "http://model", "status": 429, "retry_after_s": 1.0, "waited_s": 1.0, "cap_s": 30.0})
        return domain.tutor(system, user)

    backend = FakeLLM(resent_once)
    _with_model(domain, backend)
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == 2 * len(backend.calls) + len(proposer.calls)


def test_1_a_caller_that_notes_no_request_is_refused_and_charged_in_full(tmp_path):
    domain = domains.WordProblems(tmp_path)
    hidden = Retrying(domain.tutor)
    _with_model(domain, hidden)
    loop, _ = _enrolled(tmp_path, domain=domain)
    start = loop.mh._head()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_UNMETERED_CALLER" and got["completion_state"] == "blocked-escalated"
    assert hidden.backend == hidden.tries                      # one call showed it: no second one went out
    assert loop.mh._head() == start and "cannot count" in loop.log.paused()
    assert loop.meter.reading("claude", NOW).used == loop.worst_case()   # unknown use is charged at its worst case


def test_1_a_tick_stops_at_what_it_reserved_and_what_it_ran_counts_for_nothing(tmp_path, monkeypatch):
    loop, domain = _enrolled(tmp_path)
    start, reserved = loop.mh._head(), 40
    monkeypatch.setattr(loop, "worst_case", lambda: reserved)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_TICK_BUDGET_SPENT" and got["outcome"] == "blocked"
    assert loop.mh._head() == start
    assert len(domain.llm.calls) <= reserved                   # a FakeLLM call is one request: no overrun at all
    assert loop.meter.reading("claude", NOW).used == reserved


def test_1_a_proposer_counts_against_the_tick_as_the_model_does(tmp_path):
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, domain = _enrolled(tmp_path, proposer=proposer)
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == len(domain.llm.calls) + len(proposer.calls) > 0


def test_1_the_study_of_a_profile_outside_a_tick_is_not_stopped(tmp_path):
    loop, domain = _enrolled(tmp_path)
    case = loop.cut.tune[0]
    assert loop.workflow.models["local"].caller.complete("s", f"Problem: {case.input}")
    assert json.dumps(loop.status()["failed_streak"]) == "0"


# Finding 2: what the sealed guard does, and what no guard in this process can do -----------------------------------------

def test_2_a_handle_opened_before_the_tick_is_seen_and_nothing_is_adopted(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    start = loop.mh._head()
    with open(domain.sealed_file, "rb") as held:
        got = loop.tick()
        assert held.readable()
    assert got["outcome"] == "sealed_breach" and got["completion_state"] == "blocked-escalated"
    assert loop.mh._head() == start and "sealed" in loop.log.paused()
    assert len(domain.llm.calls) == 0                          # seen on the way in: the cycle never ran


def test_2_a_descriptor_opened_before_the_tick_is_seen_too(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    descriptor = os.open(domain.sealed_file, os.O_RDONLY)
    try:
        assert loop.tick()["outcome"] == "sealed_breach"
    finally:
        os.close(descriptor)


def test_2_a_descriptor_cannot_be_wrapped_or_mapped_while_developing(tmp_path):
    domain = domains.WordProblems(tmp_path)
    descriptor = os.open(domain.sealed_file, os.O_RDONLY)
    try:
        with sealed_closed(domain.sealed().vault) as breaches:
            with pytest.raises(AccessDenied):
                open(descriptor, "rb", closefd=False)
            with pytest.raises(AccessDenied):
                mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ)
        assert len(breaches) >= 3                              # the sweep on the way in, and each attempt
    finally:
        os.close(descriptor)


def test_2_a_handle_taken_during_a_tick_by_a_route_the_guard_cannot_see_is_seen_on_the_way_out(tmp_path):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    held = []
    real = domain.failures

    def smuggled(view):
        guarded = list(sealed_module._GUARDED)
        sealed_module._GUARDED.clear()                         # as native code would: past the audit hook
        held.append(open(domain.sealed_file, "rb"))
        sealed_module._GUARDED.extend(guarded)
        return real(view)

    domain.failures = smuggled
    try:
        got = loop.tick()
    finally:
        held[0].close()
    assert got["outcome"] == "sealed_breach"


def test_2_what_the_guard_cannot_do_a_read_through_a_handle_raises_nothing(tmp_path):
    """Pinned so no one takes the sweep for more than it is. Nothing in this process stops this read: a read on an open
    handle emits no audit event. The sweep only notices that the handle is there, and the docstring says so."""
    domain = domains.WordProblems(tmp_path)
    held = open(domain.sealed_file, "rb")
    try:
        with sealed_closed(domain.sealed().vault) as breaches:
            assert held.read(10)                               # the read succeeds
            assert breaches                                    # and the handle is on the record
    finally:
        held.close()


def test_2_the_docstring_and_the_design_say_what_the_guard_does_not_do():
    said = (Path(g6.__file__).parent / "metaharness" / "domain.py").read_text(encoding="utf-8")
    design = (Path(__file__).parents[2] / "docs" / "superpowers" / "specs" /
              "2026-10-29-self-improvement-loop-design.md").read_text(encoding="utf-8")
    for text in (said, design):
        assert "cannot be stopped" in text.replace("\n", " ")


def test_2_an_unrelated_open_descriptor_is_no_breach(tmp_path):
    domain = domains.WordProblems(tmp_path)
    other = tmp_path / "ordinary.txt"
    other.write_text("x", encoding="utf-8")
    with open(other, "rb"):
        with sealed_closed(domain.sealed().vault) as breaches:
            pass
    assert breaches == []

# Finding 3: a renamed case is the same case ---------------------------------------------------------------------------

def _renamed(cases) -> list:
    return [dataclasses.replace(c, case_id=f"renamed-{c.case_id}") for c in cases]


def _fresh(start: int, count: int) -> list[SuiteCase]:
    return [SuiteCase(f"fresh-{i:04d}", *domains._case(domains.word_problem(i))) for i in range(start, start + count)]


def test_3_renaming_what_proposers_saw_does_not_make_it_held_out_evidence(tmp_path):
    loop, domain = _enrolled(tmp_path)
    exposed = {c.input for c in (*loop.cut.tune, *loop.cut.selection)}
    domain.pool[:] = _renamed(domain.pool)                     # new ids, so the hash cuts the same inputs anew
    again, _ = _loop(tmp_path, domain=domain)
    assert exposed & {c.input for c in again.cut.held_out}     # survival: the rename really does move them there
    got = again.enrol("Ada")
    assert _code(got) == "G6_E_LOOP_SPLIT_REUSED" and got["completion_state"] == "blocked-escalated"


def test_3_renaming_a_spent_split_does_not_make_it_fresh(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    spent = list(loop.cut.held_out)
    domain.pool[:] = [*_renamed(spent), *_fresh(6000, 240)]    # only the spent cases come back, under new names
    again, _ = _loop(tmp_path, domain=domain)
    assert {c.input for c in spent} & {c.input for c in again.cut.held_out}
    assert _code(again.enrol("Ada")) == "G6_E_LOOP_SPLIT_REUSED"


def test_3_a_changed_text_under_an_exposed_name_is_refused_as_well(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain.pool[:] = [dataclasses.replace(c, input=f"{c.input} Rewritten.") for c in domain.pool]
    moved = dataclasses.replace(SETTINGS, seed=SETTINGS.seed + 1)
    again, _ = _loop(tmp_path, domain=domain, settings=moved)
    assert _code(again.enrol("Ada")) == "G6_E_LOOP_SPLIT_REUSED"


def test_3_an_enrolment_keeps_both_identities_and_no_case_text(tmp_path):
    loop, _ = _enrolled(tmp_path)
    row = loop.log.enrolled()
    assert set(row["seen_inputs"]) == {c.ref()["input_sha256"] for c in (*loop.cut.tune, *loop.cut.selection)}
    assert set(row["held_out_inputs"]) == {c.ref()["input_sha256"] for c in loop.cut.held_out}
    assert not any(c.input in json.dumps(row) for c in loop.cut.tune)


def test_3_cases_that_are_truly_new_still_enrol(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    held = {c.case_id for c in loop.cut.held_out}
    domain.pool[:] = [c for c in domain.pool if c.case_id not in held] + _fresh(6000, 240)
    again, _ = _loop(tmp_path, domain=domain)
    assert again.enrol("Ada")["completion_state"] == "qualified-draft"

# Finding 4: the whole enforcement set is pinned, not just the loop's own files --------------------------------------------
SRC = Path(g6.__file__).resolve().parent
LOOP_FILES = ("metaharness/domain.py", "metaharness/loop.py", "metaharness/runner.py", "metaharness/shadow.py",
              "self_maintenance/digest.py", "self_maintenance/envelope.py", "self_maintenance/pacing.py")
BEHIND_THEM = ("operator/gates.py", "metaharness/select.py", "metaharness/reflect.py")   # imported by what they import


def _source_copy(folder: Path) -> Path:
    """Every file the digest could read, as a tree of its own that a test may tamper with."""
    names = build_manifest(SRC, patterns=(*ENVELOPE_PATTERNS, "operator/*.py", "core/vault.py", "integrity/*.py"))
    for name in names:
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SRC / name).read_bytes())
    return folder


@pytest.mark.parametrize("name,old,new", [
    ("operator/store.py", "charged = float(metered)", "charged = 0.0"),            # the reviewer's edit
    ("operator/gates.py", "if used + amount > cap + _SLACK:", "if False:"),
    ("core/vault.py", "return any(_inside(real, root) for root in self.roots)", "return False"),
    ("integrity/__init__.py", "return _sha(manifest_text(manifest).encode(\"utf-8\"))", "return \"\""),
    ("self_maintenance/constraints.py", "ranked = sorted(", "ranked = list("),
])
def test_4_a_one_line_edit_anywhere_in_the_enforcement_set_stops_the_loop(tmp_path, monkeypatch, name, old, new):
    copy = _source_copy(tmp_path / "source")
    monkeypatch.setattr("g6.metaharness.loop._SOURCE", copy)
    loop, domain = _enrolled(tmp_path)                                 # enrolled on the copy: where it stands is pinned too
    assert _code(loop.tick()) != "G6_E_LOOP_FROZEN_CHANGED"        # survival: the untouched copy reads the same
    text = (copy / name).read_text(encoding="utf-8")
    assert old in text
    (copy / name).write_text(text.replace(old, new, 1), encoding="utf-8")
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "envelope" in got["message"]
    assert len(domain.llm.calls) == calls


def test_4_the_same_code_installed_elsewhere_stops_the_loop(tmp_path, monkeypatch):
    """So a run from a pinned install cannot be swapped for a working tree that happens to hold the same bytes today."""
    loop, domain = _enrolled(tmp_path)
    monkeypatch.setattr("g6.metaharness.loop._SOURCE", _source_copy(tmp_path / "source"))
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "envelope" in got["message"] and len(domain.llm.calls) == calls


def _g6_imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("g6"):
            for alias in node.names:
                module = f"{node.module}.{alias.name}"
                found.add(module if (SRC / (module[3:].replace(".", "/") + ".py")).is_file() else node.module)
        elif isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name.startswith("g6.")}
    return found


def _source_of(module: str) -> str:
    name = module[3:].replace(".", "/")
    return f"{name}.py" if (SRC / f"{name}.py").is_file() else f"{name}/__init__.py"


def test_4_every_module_the_loops_own_files_import_is_pinned():
    pinned = build_manifest(SRC, patterns=(*ENVELOPE_PATTERNS, *JUDGING_PATTERNS))
    needed = {_source_of(m) for name in LOOP_FILES for m in _g6_imports(SRC / name)} | {*LOOP_FILES, *BEHIND_THEM}
    assert sorted(needed - set(pinned)) == []


def test_4_the_pin_is_a_digest_of_bytes_that_exist():
    found = build_manifest(SRC, patterns=ENVELOPE_PATTERNS)
    assert all(glob.has_magic(p) or p in found for p in ENVELOPE_PATTERNS)

# Findings 5 and 6: a pause holds across handles and survives a failed write ----------------------------------------------
NOTHING_HELPS = {"prompt": "Please be polite."}


def _second_handle(tmp_path, domain):
    other, _ = _loop(tmp_path, domain=domain, proposer=domains.proposes(NOTHING_HELPS))
    return other


def _breaks(domain) -> None:
    def broken(view):
        raise RuntimeError("the domain fell over")

    domain.failures = broken


def test_5_a_second_handle_does_not_run_after_another_paused_the_loop(tmp_path):
    loop, domain = _enrolled(tmp_path)
    other = _second_handle(tmp_path, domain)                   # opened now, so it has seen no failed cycle
    _breaks(domain)
    for _ in range(3):
        loop.tick()
    assert loop.log.paused()
    calls = len(domain.llm.calls)
    got = other.tick()
    assert _code(got) == "G6_E_LOOP_PAUSED" and len(domain.llm.calls) == calls


def test_5_a_second_handle_runs_on_the_head_another_adopted(tmp_path):
    loop, domain = _enrolled(tmp_path)
    other = _second_handle(tmp_path, domain)
    assert loop.tick()["outcome"] == "adopted"
    head = loop.mh._head()
    got = other.tick()
    assert got["head_version"] == head[0] and got["head_profile"] == head[1].digest()


def test_5_a_second_handle_counts_the_cycles_another_ran(tmp_path):
    loop, domain = _enrolled(tmp_path, proposer=domains.proposes(NOTHING_HELPS))
    other = _second_handle(tmp_path, domain)
    loop.tick()
    other.tick()
    assert [c["tick"] for c in other.log.cycles()] == [1, 2]


def test_5_a_person_resumes_from_their_own_handle(tmp_path):
    loop, domain = _enrolled(tmp_path)
    person = _second_handle(tmp_path, domain)
    _breaks(domain)
    for _ in range(3):
        loop.tick()
    assert person.resume("Ada")["completion_state"] == "qualified-draft"
    assert _code(loop.tick()) != "G6_E_LOOP_PAUSED"


def test_5_a_second_handle_reports_where_the_loop_stands(tmp_path):
    loop, domain = _enrolled(tmp_path)
    other = _second_handle(tmp_path, domain)
    assert loop.tick()["outcome"] == "adopted"
    assert other.status()["head_version"] == 1 and other.status()["cycles"] == 1


def test_6_a_pause_that_could_not_be_written_still_stops_the_fourth_cycle(tmp_path, monkeypatch):
    loop, domain = _enrolled(tmp_path)
    _breaks(domain)

    def full_disk(reason: str) -> None:
        raise OSError("no space left on the device")

    monkeypatch.setattr(loop.log, "pause", full_disk)
    for _ in range(3):
        assert loop.tick()["completion_state"] == "blocked-escalated"   # the third still replies, never raises
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert loop.log.paused() == "" and _code(got) == "G6_E_LOOP_PAUSED"
    assert "three failed cycles" in got["message"] and len(domain.llm.calls) == calls
    assert len(loop.log.cycles()) == 3


def test_6_a_person_can_clear_a_streak_whose_pause_was_never_written(tmp_path, monkeypatch):
    loop, domain = _enrolled(tmp_path)
    _breaks(domain)
    monkeypatch.setattr(loop.log, "pause", lambda reason: (_ for _ in ()).throw(OSError("full")))
    for _ in range(3):
        loop.tick()
    assert loop.resume("Ada")["completion_state"] == "qualified-draft"
    assert loop.log.failed_streak() == 0 and _code(loop.tick()) != "G6_E_LOOP_PAUSED"


def test_6_a_log_that_cannot_be_read_is_a_refusal_not_a_raised_error(tmp_path):
    loop, domain = _enrolled(tmp_path)
    loop.tick()
    text = loop.log.path.read_text(encoding="utf-8")
    loop.log.path.write_text(text.replace('"outcome"', '"outcome "'), encoding="utf-8")
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_RECORDS_UNREADABLE" and len(domain.llm.calls) == calls

def test_6_a_sealed_breach_whose_pause_could_not_be_written_still_stops_the_next_tick(tmp_path, monkeypatch):
    domain = domains.WordProblems(tmp_path)
    loop, _ = _enrolled(tmp_path, domain=domain)
    monkeypatch.setattr(loop.log, "pause", lambda reason: (_ for _ in ()).throw(OSError("full")))
    with open(domain.sealed_file, "rb"):
        assert loop.tick()["outcome"] == "sealed_breach"
    assert loop.log.paused() == "" and _code(loop.tick()) == "G6_E_LOOP_PAUSED"

# Finding 9: what makes a judge independent is frozen, and the domain is checked again before each tick --------------------

@pytest.mark.parametrize("change", [{"answers_by": "model-drafted"}, {"answers_by": "caller"},
                                    {"answers_by": "independent", "is_model": True}])
def test_9_a_judge_whose_independence_changed_stops_the_loop(tmp_path, change):
    loop, domain = _enrolled(tmp_path)
    domain.judge = lambda: Judge(domains.GSM.oracle, **change)
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "evaluator" in got["message"]
    assert len(domain.llm.calls) == calls


def test_9_the_frozen_digest_moves_with_each_mark_of_independence(tmp_path):
    loop, domain = _enrolled(tmp_path)
    before = loop._frozen()["evaluator"]
    domain.judge = lambda: Judge(domains.GSM.oracle, answers_by="independent", is_model=True)
    assert loop._frozen()["evaluator"] != before


def test_9_a_model_that_turned_paid_after_enrolment_is_refused_before_it_is_called(tmp_path):
    loop, domain = _enrolled(tmp_path)
    free = domain.execution()
    domain.execution = lambda: dataclasses.replace(free, models={"local": Model(domain.llm, paid=True)})
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_PAID_MODEL" and len(domain.llm.calls) == calls


def test_9_a_stale_handle_cannot_accept_a_judge_that_lost_its_independence_by_enrolling(tmp_path):
    loop, domain = _enrolled(tmp_path)
    domain.judge = lambda: Judge(domains.GSM.oracle, answers_by="model-drafted")
    got = loop.enrol("Ada")
    assert _code(got) == "G6_E_LOOP_NO_INDEPENDENT_JUDGE"
    assert _code(loop.tick()) == "G6_E_LOOP_FROZEN_CHANGED"

# The review of the runner: a retry inside a caller stays within what was reserved ------------------------------------------

def test_a_retry_inside_a_caller_cannot_pass_what_was_reserved():
    store = OperatorStore(":memory:")
    meter = BudgetMeter((ProviderWindow("claude", 5, START),), store=store)
    reservation = meter.reserve("claude", START, 1)
    assert reservation.admitted and reservation.ceiling == 1
    requests = _Requests()
    requests.open(reservation.amount)
    backend = FakeLLM(default="")
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(EmptyRetry(backend), requests).complete("system", "user")
    assert len(backend.calls) == 1 and requests.sent == 1 and requests.stopped


def test_a_fallback_cannot_pass_what_was_reserved():
    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: the first backend is down")

    first, second = FakeLLM(down), FakeLLM()
    requests = _Requests()
    requests.open(1)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(FallbackCaller((first, second)), requests).complete("system", "user")
    assert len(first.calls) == 1 and second.calls == [] and requests.sent == 1 and requests.stopped


def test_a_retry_within_what_was_reserved_goes_out_and_is_counted():
    answers = iter(("", "", "an answer"))
    backend = FakeLLM(lambda system, user: next(answers))
    requests = _Requests()
    requests.open(3)
    assert _Counted(EmptyRetry(backend), requests).complete("system", "user") == "an answer"
    assert len(backend.calls) == requests.sent == 3 and not requests.stopped


def _at_once(caller, times: int) -> int:
    """``times`` calls of ``caller`` on as many threads, and how many were refused at the tick's budget."""
    with ThreadPoolExecutor(max_workers=times) as workers:
        pending = [workers.submit(caller.complete, "system", str(i)) for i in range(times)]
    refused = [f.exception() for f in pending if f.exception() is not None]
    assert all("G6_E_LOOP_TICK_BUDGET_SPENT" in str(failed) for failed in refused)
    return len(refused)


def test_two_requests_at_once_cannot_both_have_the_last_of_what_was_reserved():
    both_in_the_backend = threading.Barrier(2)

    def backend(system: str, user: str) -> str:
        with contextlib.suppress(threading.BrokenBarrierError):
            both_in_the_backend.wait(timeout=0.5)
        return "an answer"

    llm_ = FakeLLM(backend)
    requests = _Requests()
    requests.open(1)
    assert _at_once(_Counted(llm_, requests), 2) == 1
    assert len(llm_.calls) == requests.sent == 1


def test_requests_at_once_stop_at_what_was_reserved_and_each_refused_one_takes_no_place():
    def backend(system: str, user: str) -> str:
        time.sleep(0.05)
        return "an answer"

    llm_ = FakeLLM(backend)
    requests = _Requests()
    requests.open(3)
    assert _at_once(_Counted(llm_, requests), 8) == 5
    assert len(llm_.calls) == requests.sent == 3 and requests.stopped


def test_a_request_holds_its_place_from_before_it_goes_out():
    requests, seen = _Requests(), []
    requests.open(2)
    llm_ = FakeLLM(lambda system, user: seen.append(requests.sent) or "an answer")
    _Counted(llm_, requests).complete("system", "user")
    assert seen == [1] and requests.sent == 1


def test_a_request_that_failed_keeps_its_place():
    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: down")

    requests = _Requests()
    requests.open(2)
    with pytest.raises(LLMError, match="down"):
        _Counted(FakeLLM(down), requests).complete("system", "user")
    assert requests.sent == 1


def test_a_childs_requests_which_have_no_boundary_are_counted_as_it_reports_them():
    class Child:
        def complete(self, system: str, user: str, *, max_tokens: int = 0) -> str:
            model_calls.note_model_call()
            model_calls.note_child_model_calls(scoped=2, process=2)
            return "an answer"

    requests = _Requests()
    requests.open(10)
    _Counted(Child(), requests).complete("system", "user")
    assert requests.sent == 3


class Busy:
    """An endpoint that turns each request away three times, asking for no wait, and answers the fourth."""

    def __init__(self, answer) -> None:
        self.answer, self.sent = answer, []

    def open(self, request, timeout):
        self.sent.append(request)
        if len(self.sent) % 4:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {"Retry-After": "0"}, io.BytesIO(b"busy"))
        messages = json.loads(request.data)["messages"]
        reply = {"choices": [{"message": {"content": self.answer(messages[0]["content"], messages[1]["content"])}}]}
        return io.BytesIO(json.dumps(reply).encode())


def _on_a_busy_endpoint(tmp_path, monkeypatch, kind=llm.OpenAICompatibleCaller):
    domain = domains.WordProblems(tmp_path, pool=86, weak=0.0)
    busy = Busy(domain.tutor)
    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: busy)
    caller = kind("local", base_url="https://unused.invalid", api_key=None)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(caller, max_calls_per_run=1)})
    settings = LoopSettings(windows=(ProviderWindow("claude", 500, START),), audit_per_tick=0, eval_calls=1, method=Method(
        cycles=1, propose_budget=4, select_budget=8, use_recovery=False, use_reflection=False))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, settings=settings, proposer=proposer, now=lambda: START + timedelta(hours=1))
    return loop, busy, proposer


def test_no_resend_of_a_busy_endpoint_passes_what_a_tick_reserved(tmp_path, monkeypatch):
    loop, busy, proposer = _on_a_busy_endpoint(tmp_path, monkeypatch)
    reserved = loop.worst_case()
    got = loop.tick()
    assert got["budget"]["charged"] == len(busy.sent) + len(proposer.calls) <= reserved
    assert _code(got) == "G6_E_LOOP_TICK_BUDGET_SPENT" and got["outcome"] == "blocked"


def test_a_resend_behind_a_retry_is_held_to_what_was_reserved_too(tmp_path, monkeypatch):
    loop, busy, proposer = _on_a_busy_endpoint(tmp_path, monkeypatch, kind=_retrying_blanks(llm.OpenAICompatibleCaller))
    reserved = loop.worst_case()
    assert loop.tick()["budget"]["charged"] == len(busy.sent) + len(proposer.calls) <= reserved


def _http_caller() -> llm.OpenAICompatibleCaller:
    return llm.OpenAICompatibleCaller("local", base_url="https://unused.invalid", api_key=None)


def test_a_resend_goes_out_only_while_what_was_reserved_has_room_for_it(monkeypatch):
    busy = Busy(lambda system, user: "an answer")
    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: busy)
    requests = _Requests()
    requests.open(3)
    resending = _Counted(_http_caller(), requests)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        resending.complete("system", "user")
    assert len(busy.sent) == requests.sent == 3 and requests.stopped
    busy.sent.clear()
    requests.open(4)
    assert resending.complete("system", "user") == "an answer" and len(busy.sent) == requests.sent == 4


class Own:
    """A caller of a domain's own, holding an HTTP caller under a name of its own."""

    def __init__(self, held) -> None:
        self.held = held

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        return self.held.complete(system, user, max_tokens=max_tokens)


def test_a_resend_is_held_whatever_holds_the_caller_that_sends_it(monkeypatch):
    busy = Busy(lambda system, user: "an answer")
    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: busy)
    requests = _Requests()
    requests.open(2)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(Own(_http_caller()), requests).complete("system", "user")
    assert len(busy.sent) == requests.sent == 2


def test_no_resend_goes_out_at_or_past_the_end_of_the_week_it_reserved_in(monkeypatch):
    now, sent = [RESET - timedelta(seconds=1)], []

    class Slow:
        def open(self, request, timeout):
            sent.append(now[0])
            now[0] = RESET
            raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "0"}, io.BytesIO(b"busy"))

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: Slow())
    requests = _Requests(lambda: now[0])
    requests.open(10, RESET)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(_http_caller(), requests).complete("system", "user")
    assert len(sent) == requests.sent == 1 and "week ended" in requests.stopped


def test_a_wait_that_would_reach_the_end_of_the_week_is_not_taken(monkeypatch):
    slept, sent = [], []

    class AsksForTen:
        def open(self, request, timeout):
            sent.append(request)
            raise urllib.error.HTTPError(request.full_url, 429, "busy", {"Retry-After": "10"}, io.BytesIO(b"busy"))

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: AsksForTen())
    monkeypatch.setattr(llm, "_sleep", slept.append)
    requests = _Requests(lambda: RESET - timedelta(seconds=5))
    requests.open(10, RESET)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(_http_caller(), requests).complete("system", "user")
    assert len(sent) == 1 and slept == []


def test_a_clean_tick_on_an_http_caller_reserves_what_any_other_does(tmp_path, monkeypatch):
    domain = domains.WordProblems(tmp_path)
    caller = _http_caller()

    def answers(system: str, user: str, *, max_tokens: int = 1024) -> str:
        llm._start_model_call()
        return domain.tutor(system, user)

    monkeypatch.setattr(caller, "complete", answers)
    plain, _ = _loop(tmp_path / "plain", domain=domains.WordProblems(tmp_path / "plain"))
    _with_model(domain, caller)
    loop, _ = _enrolled(tmp_path, domain=domain)
    assert loop.worst_case() == plain.worst_case()
    assert loop.tick()["outcome"] == "adopted"


def test_a_tick_of_blank_replies_stops_at_what_it_reserved_retries_and_all(tmp_path, monkeypatch):
    domain = domains.WordProblems(tmp_path)
    backend = _retrying_blanks(FakeLLM)(default="")
    _with_model(domain, backend)
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    start, reserved = loop.mh._head(), 10
    monkeypatch.setattr(loop, "worst_case", lambda: reserved)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_TICK_BUDGET_SPENT" and got["outcome"] == "blocked"
    assert len(backend.calls) + len(proposer.calls) <= reserved and loop.mh._head() == start

# The review of the runner: a generation's number is never used twice --------------------------------------------------------

def _chain(loop) -> Path:
    return loop.root / "workspace" / "adoptions.jsonl"


def test_an_emptied_chain_of_adoptions_stops_the_loop_and_reuses_no_generation(tmp_path):
    loop, domain = _enrolled(tmp_path)
    first = loop.tick()
    assert first["outcome"] == "adopted" and first["head_version"] == 1
    _chain(loop).write_text("", encoding="utf-8")
    again, _ = _loop(tmp_path, domain=domain, proposer=domains.proposes({"prompt": f"{domains.WORKING} Use careful arithmetic."}))
    calls = len(domain.llm.calls)
    got = again.tick()
    assert _code(got) == "G6_E_LOOP_RECORDS_UNREADABLE" and got["completion_state"] == "blocked-escalated"
    assert "generation 1" in got["message"] and len(domain.llm.calls) == calls
    assert _chain(again).read_text(encoding="utf-8") == "" and len(again.log.cycles()) == 1


def test_a_chain_cut_short_stops_the_loop(tmp_path):
    domain = domains.WordProblems(tmp_path, weak=0.6)
    loop, _ = _enrolled(tmp_path, domain=domain)
    assert loop.tick()["outcome"] == "adopted"
    domain.tutor.collapsed = True
    assert loop.tick()["outcome"] == "rolled_back"
    _chain(loop).write_text(_chain(loop).read_text(encoding="utf-8").splitlines(keepends=True)[0], encoding="utf-8")
    again, _ = _loop(tmp_path, domain=domain)
    got = again.tick()
    assert _code(got) == "G6_E_LOOP_RECORDS_UNREADABLE" and "generation 2" in got["message"]


def test_a_lost_generation_stops_every_tick_until_a_person_acts(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    _chain(loop).write_text("", encoding="utf-8")
    again, _ = _loop(tmp_path, domain=domain)
    assert {_code(again.tick()) for _ in range(4)} == {"G6_E_LOOP_RECORDS_UNREADABLE"}
    assert len(again.log.cycles()) == 1


def test_a_chain_ahead_of_the_log_is_a_crash_between_two_writes_and_runs_on(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"
    lines = loop.log.path.read_text(encoding="utf-8").splitlines(keepends=True)
    loop.log.path.write_text("".join(lines[:-1]), encoding="utf-8")   # the adoption was written, its cycle was not
    again, _ = _loop(tmp_path, domain=domain)
    assert _code(again.tick()) != "G6_E_LOOP_RECORDS_UNREADABLE"
    assert again.mh._head()[0] >= 1

# The third review: a tick that crosses a weekly reset ---------------------------------------------------------------------

RESET = START + timedelta(days=7)


def _crossing_a_reset(tmp_path):
    """A loop one instant before a reset, whose first answer takes it past. The requests sent after it are in ``late``."""
    now, late = [RESET - timedelta(milliseconds=1)], []
    domain = domains.WordProblems(tmp_path)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(domain.llm, max_calls_per_run=1)})

    def answer(system: str, user: str) -> str:
        if now[0] >= RESET:
            late.append(user)
        now[0] = RESET + timedelta(milliseconds=1)
        return domain.tutor(system, user)

    domain.llm._responder = answer
    settings = LoopSettings(windows=(ProviderWindow("claude", 20, START),), audit_per_tick=0, eval_calls=1,
                            method=Method(cycles=1, propose_budget=4, select_budget=1, reflect_calls=1, use_recovery=False,
                                          use_reflection=False))
    loop, _ = _enrolled(tmp_path, domain=domain, settings=settings, now=lambda: now[0])
    return loop, late


def test_a_tick_crossing_a_reset_sends_nothing_on_the_old_weeks_reservation(tmp_path):
    loop, late = _crossing_a_reset(tmp_path)
    head = loop.mh._head()
    got = loop.tick()
    assert late == [] and got["outcome"] == "blocked" and _code(got) == "G6_E_LOOP_TICK_BUDGET_SPENT"
    assert "week ended" in got["message"] and loop.mh._head() == head


def test_the_next_tick_answers_to_the_new_weeks_ceiling(tmp_path):
    loop, late = _crossing_a_reset(tmp_path)
    loop.tick()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and "day 1's ceiling of 4" in got["message"] and late == []


def test_a_retry_inside_a_caller_stops_at_the_reset():
    now = [RESET - timedelta(seconds=1)]

    def blank_past_the_reset(system: str, user: str) -> str:
        now[0] = RESET
        return ""

    backend = FakeLLM(blank_past_the_reset)
    requests = _Requests(lambda: now[0])
    requests.open(10, RESET)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(EmptyRetry(backend), requests).complete("system", "user")
    assert len(backend.calls) == requests.sent == 1 and "week ended" in requests.stopped


def test_a_reservation_says_when_its_week_ends():
    meter = BudgetMeter((ProviderWindow("claude", 5, START),), store=OperatorStore(":memory:"))
    assert meter.reserve("claude", START + timedelta(hours=1), 1).ends == RESET

# The owner's ruling of 2026-11-01: the loop gates on success and model calls, and only reports wall clock --------------------

class Jittery:
    """A clock that never steps the same way twice, and whose ``at`` a model's reply can push on."""

    def __init__(self) -> None:
        self.noise, self.at = random.Random(20261101), 0.0

    def __call__(self) -> float:
        self.at += self.noise.uniform(0.0, 0.2)
        return self.at


def _on_this_clock(tmp_path, domain, clock):
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=clock)
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop.tick()


def test_a_planted_gain_is_adopted_on_a_real_clock(tmp_path):
    got = _on_this_clock(tmp_path, domains.WordProblems(tmp_path), time.monotonic)
    assert got["outcome"] == "adopted" and got["head_version"] == 1, got["message"]


def test_a_planted_gain_is_adopted_on_a_jittery_clock_even_when_it_really_is_slower(tmp_path):
    clock, domain = Jittery(), domains.WordProblems(tmp_path)
    tutor = domain.tutor

    def slower_with_the_gain(system: str, user: str) -> str:
        clock.at += 1.0 if "step by step" in system else 0.0
        return tutor(system, user)

    domain.llm._responder = slower_with_the_gain
    got = _on_this_clock(tmp_path, domain, clock)
    assert got["outcome"] == "adopted" and got["head_version"] == 1, got["message"]


def test_the_loop_gates_on_two_objectives_and_a_persons_workflow_still_holds_wall_clock(tmp_path):
    loop, _ = _loop(tmp_path)
    assert [(o.name, o.direction, o.tolerance) for o in loop.workflow.objectives] == [("success", "max", 0.05),
                                                                                      ("model_calls", "min", 0.05)]
    assert [o.name for o in default_objectives()] == ["success", "model_calls", "wall_clock_s"]
    assert next(f for f in dataclasses.fields(Workflow) if f.name == "objectives").default_factory is default_objectives


def test_wall_clock_still_reaches_the_gates_measurements_and_is_reported_as_not_gated(tmp_path, monkeypatch):
    loop, _ = _enrolled(tmp_path)
    seen, adopt = [], loop.mh.adopt

    def noting(decision, **kwargs):
        seen.append(decision)
        return adopt(decision, **kwargs)

    monkeypatch.setattr(loop.mh, "adopt", noting)
    assert loop.tick()["outcome"] == "adopted"
    measured = seen[0].measurements["wall_clock_s"]
    assert measured["candidate_mean"] > 0
    assert lineage(loop.root / "workspace")["recent"][-1]["measured"]["not_gated"] == {
        "wall_clock_s": {"before": measured["base_mean"], "after": measured["candidate_mean"]}}


def test_a_gain_past_the_limit_on_model_calls_is_still_refused(tmp_path):
    domain = domains.WordProblems(tmp_path)
    tutor, blank_next = domain.tutor, set()

    def twice_for_the_gain(system: str, user: str) -> str:
        if "step by step" in system:
            blank_next.symmetric_difference_update({(system, user)})
            if (system, user) in blank_next:
                return ""
        return tutor(system, user)

    _with_model(domain, _retrying_blanks(FakeLLM)(twice_for_the_gain))
    loop, _ = _enrolled(tmp_path, domain=domain)
    head = loop.mh._head()
    got = loop.tick()
    assert _code(got) == "G6_E_META_OBJECTIVE_REGRESSED" and "model_calls" in got["message"]
    assert loop.mh._head() == head


def test_a_folder_enrolled_under_other_objectives_adopts_nothing_until_it_enrols_again(tmp_path, monkeypatch):
    with monkeypatch.context() as earlier:
        earlier.setattr(sys.modules[ShadowLoop.__module__], "GATED", ("success", "model_calls", "wall_clock_s"))
        _, domain = _enrolled(tmp_path)
    again, _ = _loop(tmp_path, domain=domain)
    assert _code(again.tick()) == "G6_E_META_STANDARD_CHANGED" and again.mh._head()[0] == 0
    assert again.enrol("Ada")["completion_state"] == "qualified-draft"
    assert again.tick()["outcome"] == "adopted"

# What changes during a cycle ---------------------------------------------------------------------------------------------

def test_a_frozen_file_changed_during_a_cycle_refuses_its_adoption(tmp_path):
    enrolled = tmp_path / "domain.py"
    enrolled.write_text("# the declaration a person enrolled\n", encoding="utf-8")

    def edited_meanwhile(system: str, user: str) -> str:
        enrolled.write_text("# saved by an editor after the tick's first check\n", encoding="utf-8")
        return json.dumps({"prompt": domains.WORKING})

    loop, _ = _enrolled(tmp_path, proposer=FakeLLM(edited_meanwhile), domain_file=enrolled)
    head = loop.mh._head()
    got = loop.tick()
    assert got["outcome"] == "blocked" and _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "domain_file" in got["message"]
    assert loop.mh.adoption_rows() == [] and loop.mh._head() == head
    assert _code(loop.tick()) == "G6_E_LOOP_FROZEN_CHANGED" and len(loop.log.cycles()) == 1


def test_a_sealed_file_changed_during_a_cycle_refuses_its_adoption_too(tmp_path):
    domain = domains.WordProblems(tmp_path)
    real = domain.failures

    def failures_then_a_sealed_edit(view):
        with sealed_module._LOCK:                                      # the guard is open-only: this stands for another process
            entries, sealed_module._GUARDED[:] = list(sealed_module._GUARDED), []
        domain.sealed_file.write_text("edited\n", encoding="utf-8")
        sealed_module._GUARDED[:] = entries
        return real(view)

    domain.failures = failures_then_a_sealed_edit
    loop, _ = _enrolled(tmp_path, domain=domain)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "sealed" in got["message"] and loop.mh.adoption_rows() == []


def test_a_frozen_file_changed_while_the_adoption_loads_its_report_refuses_it(tmp_path, monkeypatch):
    enrolled = tmp_path / "domain.py"
    enrolled.write_text("# the declaration a person enrolled\n", encoding="utf-8")
    loop, _ = _enrolled(tmp_path, domain_file=enrolled)
    real = loop.mh._recorded

    def saved_meanwhile(kind: str, ident: str):
        if kind == "report":
            enrolled.write_text("# saved by an editor as the adoption was being written\n", encoding="utf-8")
        return real(kind, ident)

    monkeypatch.setattr(loop.mh, "_recorded", saved_meanwhile)
    got = loop.tick()
    assert got["outcome"] == "blocked" and _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "domain_file" in got["message"]
    assert loop.mh.adoption_rows() == []

# What a limit costs an evaluation, when the account can say first ---------------------------------------------------------

WEEK_END = START + timedelta(days=7)


def _account(percent: int) -> dict:
    return {"rateLimits": {"primary": {"usedPercent": percent, "windowDurationMins": 10080, "resetsAt": WEEK_END.timestamp()}}}


def _reading(tmp_path, ask, **kw):
    """A loop whose account is read just before an evaluation, as ``open_loop`` makes one, and no read otherwise."""
    reader = AccountReader(SETTINGS.windows, ask={"claude": ask})
    return _enrolled(tmp_path, read_account=reader, read_now=reader.read_now, **kw)


def _held_out_reached_the_model(loop, domain) -> bool:
    held = {c.input for c in loop.cut.held_out}
    return any(user.rsplit("Problem: ", 1)[-1].strip() in held for _, user in domain.llm.calls)


def test_a_limit_read_just_before_an_evaluation_leaves_the_split_unspent(tmp_path):
    loop, domain = _reading(tmp_path, lambda: _account(100))
    before = loop.mh.held_out_left()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_BUDGET_WAIT" and got["wait_until"] == WEEK_END.isoformat()
    assert loop.mh.held_out_left() == before and loop.mh.ledger.rows("evaluation") == []
    assert not _held_out_reached_the_model(loop, domain) and loop.log.cycles() == []
    assert loop.log.limits()["claude"] == WEEK_END


def test_the_candidate_a_limit_held_back_is_evaluated_once_it_ends(tmp_path):
    now, answers = [NOW], iter((_account(100), _account(5)))
    loop, _ = _reading(tmp_path, lambda: next(answers), now=lambda: now[0])
    before = loop.mh.held_out_left()
    loop.tick()
    now[0] = WEEK_END + timedelta(seconds=1)
    assert loop.tick()["outcome"] == "adopted" and loop.mh.held_out_left() == before - 1


def test_a_read_that_says_no_limit_lets_the_evaluation_run(tmp_path):
    loop, _ = _reading(tmp_path, lambda: _account(5))
    assert loop.tick()["outcome"] == "adopted"


def test_a_read_that_fails_says_nothing_new_so_the_evaluation_runs(tmp_path):
    def down():
        raise UsageUnavailable("the tool stopped")

    loop, _ = _reading(tmp_path, down)
    assert loop.tick()["outcome"] == "adopted"


def test_a_tick_with_nothing_to_evaluate_reads_nothing(tmp_path):
    asked = []
    loop, _ = _reading(tmp_path, lambda: asked.append(1) or _account(5), domain=domains.WordProblems(tmp_path, weak=1.1))
    assert loop.tick()["outcome"] == "idle" and asked == []


def test_a_tick_is_read_once_whatever_the_reader_read_just_before_it(tmp_path):
    asked = []
    loop, _ = _reading(tmp_path, lambda: asked.append(1) or _account(5))
    loop.meter._read_account.refresh(NOW)
    assert loop.tick()["outcome"] == "adopted" and len(asked) == 2


def test_the_limit_of_this_a_limit_that_arrives_during_an_evaluation_still_spends_it(tmp_path):
    """Pinned so nobody takes the read for more than it is. Codex's words are not read, and the callers are bound, so nothing
    tells this from a failed case until an issuance window gives them a code. The read before it saw no limit."""
    loop, domain = _reading(tmp_path, lambda: _account(5))
    held, tutor, host = {c.input for c in loop.cut.held_out}, domain.tutor, host_models.CodexCaller()

    def limited_on_the_held_out_split(system: str, user: str) -> str:
        if user.rsplit("Problem: ", 1)[-1].strip() in held:
            host._reply(1, "", "ERROR: You've hit your usage limit.", tmp_path, ())
        return tutor(system, user)

    domain.llm._responder = limited_on_the_held_out_split
    before = loop.mh.held_out_left()
    assert loop.tick()["outcome"] == "refused" and loop.mh.held_out_left() == before - 1


# A stop lands at the next model call, not the next tick --------------------------------------------------------------------

def _held_out_inputs(loop) -> set[str]:
    return {c.input for c in loop.cut.held_out}


def _on_the_held_out_split(user: str, held: set[str]) -> bool:
    return user.rsplit("Problem: ", 1)[-1].strip() in held


def _a_stop_at_the_third_request(loop, domain) -> list[str]:
    tutor, seen = domain.tutor, []

    def stopped_at_the_third(system: str, user: str) -> str:
        seen.append(user)
        if len(seen) == 3:
            ask_to_stop(loop.root, "the owner wants the subscription back")
        return tutor(system, user)

    domain.llm._responder = stopped_at_the_third
    return seen


def test_a_stop_lands_at_the_next_model_call_and_not_the_next_tick(tmp_path):
    loop, domain = _enrolled(tmp_path)
    seen = _a_stop_at_the_third_request(loop, domain)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_STOPPED" and got["completion_state"] == "blocked-escalated"
    assert len(seen) == 3 and got["charged"] == 3 and loop.meter.reading("claude", NOW).used == 3
    assert "at a model call" in got["message"] and "the owner wants the subscription back" in got["message"]
    assert loop.log.cycles() == [] and loop.mh.adoption_rows() == []


def test_a_stop_during_an_evaluation_spends_none_of_the_split_and_says_so(tmp_path):
    loop, domain = _enrolled(tmp_path)
    held, tutor = _held_out_inputs(loop), domain.tutor

    def stopped_on_the_held_out_split(system: str, user: str) -> str:
        if _on_the_held_out_split(user, held):
            ask_to_stop(loop.root, "the owner's review")
        return tutor(system, user)

    domain.llm._responder = stopped_on_the_held_out_split
    before = loop.mh.held_out_left()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_STOPPED" and loop.mh.held_out_left() == before
    assert loop.mh.ledger.rows("evaluation") == [] and "none of the split's five was spent" in got["message"]
    [row] = [body for kind, body in loop.log._rows() if kind == "stopped"]
    assert row["cut_off"]["evaluation"] is True and row["cut_off"]["charged"] == got["charged"]


def test_a_stop_before_any_evaluation_says_nothing_of_one(tmp_path):
    loop, domain = _enrolled(tmp_path)
    _a_stop_at_the_third_request(loop, domain)
    got = loop.tick()
    assert "evaluation" not in got["message"]
    [row] = [body for kind, body in loop.log._rows() if kind == "stopped"]
    assert row["cut_off"]["evaluation"] is False


def test_the_candidate_a_stop_cut_off_is_taken_up_by_the_next_start(tmp_path):
    loop, domain = _enrolled(tmp_path)
    held, tutor = _held_out_inputs(loop), domain.tutor

    def stopped_on_the_held_out_split(system: str, user: str) -> str:
        if _on_the_held_out_split(user, held):
            ask_to_stop(loop.root, "the owner's review")
        return tutor(system, user)

    domain.llm._responder = stopped_on_the_held_out_split
    before = loop.mh.held_out_left()
    loop.tick()
    domain.llm._responder = tutor
    loop.log.start("Ada", **stop_to_lift(loop.root))
    assert loop.tick()["outcome"] == "adopted" and loop.mh.held_out_left() == before - 1


def test_a_stop_a_tick_met_ends_the_start_with_nothing_left_to_note(tmp_path):
    loop, domain = _enrolled(tmp_path)
    loop.log.start("Ada")
    _a_stop_at_the_third_request(loop, domain)
    loop.tick()
    assert not loop.log.start_in_force()


def test_a_stop_that_cannot_be_noted_still_stops_the_tick(tmp_path, monkeypatch):
    loop, domain = _enrolled(tmp_path)
    seen = _a_stop_at_the_third_request(loop, domain)

    def full(*args, **kwargs):
        raise OSError("no space left on the device")

    monkeypatch.setattr(loop.log, "stop", full)
    assert _code(loop.tick()) == "G6_E_LOOP_STOPPED" and len(seen) == 3


def test_g6s_own_stop_lands_at_the_next_model_call_too(tmp_path):
    loop, _ = _enrolled(tmp_path)
    loop.requests.open(10)
    stop.engage("the machine is going down")
    try:
        with pytest.raises(_Halted) as raised:
            loop.workflow.models["local"].caller.complete("system", "user")
    finally:
        stop.release()
        loop.requests.close()
    assert raised.value.code == stop.STOPPED and "the machine is going down" in raised.value.said


def test_g6s_own_stop_during_an_evaluation_costs_one_and_the_record_says_so(tmp_path):
    """``evaluate`` looks at this stop itself, before each case, and keeps what it has as a partial report, which is
    recorded. That code judges, so it stays: what can be done here is to say so."""
    loop, domain = _enrolled(tmp_path)
    held, tutor = _held_out_inputs(loop), domain.tutor

    def stopped_on_the_held_out_split(system: str, user: str) -> str:
        if _on_the_held_out_split(user, held):
            stop.engage("the machine is going down")
        return tutor(system, user)

    domain.llm._responder = stopped_on_the_held_out_split
    before = loop.mh.held_out_left()
    try:
        got = loop.tick()
    finally:
        stop.release()
    assert loop.mh.held_out_left() == before - 1 and got["outcome"] == "blocked"
    assert got["message"].startswith("the evaluation stopped part way and still spent one of the split's five")


def test_outside_a_tick_a_stop_holds_no_request_back(tmp_path):
    loop, domain = _enrolled(tmp_path)
    ask_to_stop(loop.root, "the owner's review")
    assert loop.workflow.models["local"].caller.complete("system", f"Problem: {loop.cut.tune[0].input}")


def test_a_request_goes_out_when_no_stop_stands(tmp_path):
    loop, domain = _enrolled(tmp_path)
    assert loop.tick()["outcome"] == "adopted"


# A classifier or permission stop is an outcome of its own, and nothing retries it ----------------------------------------------

REFUSED = "G6_E_MODEL_REFUSED: cyber"


def _refuses_the_third(domain) -> list[tuple[str, str]]:
    tutor, seen = domain.tutor, []

    def third(system: str, user: str) -> str:
        seen.append((system, user))
        if len(seen) == 3:
            raise LLMError(REFUSED)
        return tutor(system, user)

    domain.llm._responder = third
    return seen


def test_a_refusal_ends_the_tick_at_once_and_is_on_the_cycle(tmp_path):
    loop, domain = _enrolled(tmp_path)
    seen, head = _refuses_the_third(domain), loop.mh._head()
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_CLASSIFIER_STOP" and got["completion_state"] == "blocked-escalated"
    assert got["outcome"] == "blocked" and len(seen) == 3 and loop.mh._head() == head
    row = loop.log.cycles()[-1]["classifier_stop"]
    assert row["said"] == REFUSED and row["request"] == _request_digest(*seen[-1])


def test_nothing_more_goes_out_that_tick_on_any_thread(tmp_path):
    requests = _Requests()
    requests.open(10)
    with pytest.raises(_Refused):
        requests.at_a_refusal(LLMError(REFUSED), "a request")
    with pytest.raises(_Refused):
        requests.take()
    assert requests.sent == 0


def test_a_refusal_a_proposer_met_ends_the_tick_too(tmp_path):
    def refuses(system: str, user: str) -> str:
        raise LLMError(REFUSED)

    loop, _ = _enrolled(tmp_path, proposer=FakeLLM(refuses))
    assert _code(loop.tick()) == "G6_E_LOOP_CLASSIFIER_STOP"


def test_a_refusal_during_an_evaluation_spends_none_of_the_split(tmp_path):
    loop, domain = _enrolled(tmp_path)
    held, tutor = _held_out_inputs(loop), domain.tutor

    def refused_on_the_held_out_split(system: str, user: str) -> str:
        if _on_the_held_out_split(user, held):
            raise LLMError(REFUSED)
        return tutor(system, user)

    domain.llm._responder = refused_on_the_held_out_split
    before = loop.mh.held_out_left()
    assert _code(loop.tick()) == "G6_E_LOOP_CLASSIFIER_STOP" and loop.mh.held_out_left() == before


def test_a_refusal_holds_the_loop_until_a_person_has_read_it(tmp_path):
    loop, domain = _enrolled(tmp_path)
    seen = _refuses_the_third(domain)
    loop.tick()
    assert "stopped a request" in loop.log.paused()
    calls = len(seen)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_PAUSED" and len(seen) == calls


def test_the_refused_request_is_never_sent_again_even_once_a_person_lifts_the_pause(tmp_path):
    loop, domain = _enrolled(tmp_path)
    seen = _refuses_the_third(domain)
    loop.tick()
    refused = seen[-1]
    domain.llm._responder = lambda system, user: seen.append((system, user)) or domain.tutor(system, user)
    assert loop.resume("Ada")["completion_state"] == "qualified-draft"
    assert _code(loop.tick()) != "G6_E_LOOP_PAUSED"
    assert seen.count(refused) == 1


def test_a_restart_remembers_what_was_refused(tmp_path):
    loop, domain = _enrolled(tmp_path)
    seen = _refuses_the_third(domain)
    loop.tick()
    refused = seen[-1]
    domain.llm._responder = lambda system, user: seen.append((system, user)) or domain.tutor(system, user)
    again, _ = _loop(tmp_path, domain=domain)
    assert again.resume("Ada")["completion_state"] == "qualified-draft"
    again.tick()
    assert seen.count(refused) == 1


def test_a_failure_that_is_no_refusal_is_still_a_failed_case_and_pauses_nothing(tmp_path):
    loop, domain = _enrolled(tmp_path)

    def refused_in_its_own_words(system: str, user: str) -> str:
        raise LLMError("HTTP 500 from https://model: G6_E_MODEL_REFUSED: a server's body says this")

    domain.llm._responder = refused_in_its_own_words
    assert _code(loop.tick()) != "G6_E_LOOP_CLASSIFIER_STOP" and loop.log.paused() == ""


def test_the_loop_calls_a_plain_claude_code_caller_as_the_one_that_reads_its_whole_result(tmp_path):
    domain = domains.WordProblems(tmp_path)
    _with_model(domain, host_models.ClaudeCodeCaller("a-model", timeout=7.0))
    loop, _ = _loop(tmp_path, domain=domain, proposer=host_models.ClaudeCodeCaller())
    inner = loop.workflow.models["local"].caller.inner
    assert isinstance(inner, WatchedClaude) and inner.model == "a-model" and inner.timeout == 7.0
    assert isinstance(loop._proposer.inner, WatchedClaude)


def test_the_workflow_a_person_enrolled_is_the_same_whichever_claude_caller_runs_it(tmp_path):
    plain = domains.WordProblems(tmp_path / "a")
    _with_model(plain, host_models.ClaudeCodeCaller())
    watched = domains.WordProblems(tmp_path / "b")
    _with_model(watched, WatchedClaude())
    assert (_loop(tmp_path / "a", domain=plain)[0].workflow.digest() == _loop(tmp_path / "b", domain=watched)[0].workflow.digest())


# The gate on a request stands on a bound file raising what an observer raises --------------------------------------------------

class SendsThree:
    """A caller that notes each of three requests and sends it, as a retry inside a caller does."""

    def __init__(self) -> None:
        self.sent = 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        for _ in range(3):
            model_calls.note_model_call()
            self.sent += 1
        return "an answer"


def test_the_third_of_three_requests_under_a_limit_of_two_never_goes_out():
    """``counting`` asks its observers not to raise, and the gate on a request is an observer that does. That file is bound,
    so its sentence stands until an issuance window, and the day it is made true this test is what says so: a swallowed raise
    lets the third request out."""
    caller, requests = SendsThree(), _Requests()
    requests.open(2)
    with pytest.raises(LLMError, match="G6_E_LOOP_TICK_BUDGET_SPENT"):
        _Counted(caller, requests).complete("system", "user")
    assert caller.sent == 2 and requests.sent == 2 and requests.stopped
