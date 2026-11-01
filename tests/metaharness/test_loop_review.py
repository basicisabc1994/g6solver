# SPDX-License-Identifier: Apache-2.0
"""Each finding of the cross-family review of the loop, as the input that showed it. Each was seen failing first."""
from __future__ import annotations

import ast
import dataclasses
import glob
import importlib.util
import json
import mmap
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import g6
from g6.core import llm
from g6.core.llm import EmptyRetry, FakeLLM, FallbackCaller, LLMError
from g6.core.vault import AccessDenied
from g6.integrity import build_manifest
from g6.metaharness import JUDGING_PATTERNS, Model, SuiteCase
from g6.metaharness import domain as sealed_module
from g6.metaharness.domain import Judge, sealed_closed
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop, _Counted, _Requests
from g6.metaharness.method import Method
from g6.operator.store import OperatorStore
from g6.self_maintenance.pacing import BudgetMeter, ProviderWindow

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

class Retrying:
    """Answers through a backend it asks ``tries`` times, and notes none of them: the retries the reviewer counted."""

    def __init__(self, inner, tries: int = 8) -> None:
        self.inner, self.tries, self.backend = inner, tries, 0

    def complete(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        self.backend += self.tries
        return self.inner(system, user)


def test_1_a_retry_a_caller_notes_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)
    tutor, attempts = domain.tutor, []

    def blank_first(system: str, user: str) -> str:
        attempts.append(user)
        return "" if attempts.count(user) == 1 else tutor(system, user)

    backend = FakeLLM(blank_first)
    _with_model(domain, EmptyRetry(backend))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert loop.meter.reading("claude", NOW).used == len(backend.calls) + len(proposer.calls)


def test_1_each_attempt_of_a_fallback_is_charged(tmp_path):
    domain = domains.WordProblems(tmp_path)

    def down(system: str, user: str) -> str:
        raise LLMError("G6_E_LLM_TIMEOUT: the first backend is down")

    first, second = FakeLLM(down), FakeLLM(domain.tutor)
    _with_model(domain, FallbackCaller((first, second)))
    proposer = domains.proposes({"prompt": domains.WORKING})
    loop, _ = _enrolled(tmp_path, domain=domain, proposer=proposer)
    loop.tick()
    assert len(first.calls) > 0
    assert loop.meter.reading("claude", NOW).used == len(first.calls) + len(second.calls) + len(proposer.calls)


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
    loop, domain = _enrolled(tmp_path, proposer=FallbackCaller((proposer,)))
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
    loop, domain = _enrolled(tmp_path)
    copy = _source_copy(tmp_path / "source")
    monkeypatch.setattr("g6.metaharness.loop._SOURCE", copy)
    assert _code(loop.tick()) != "G6_E_LOOP_FROZEN_CHANGED"        # survival: the untouched copy reads the same
    text = (copy / name).read_text(encoding="utf-8")
    assert old in text
    (copy / name).write_text(text.replace(old, new, 1), encoding="utf-8")
    calls = len(domain.llm.calls)
    got = loop.tick()
    assert _code(got) == "G6_E_LOOP_FROZEN_CHANGED" and "envelope" in got["message"]
    assert len(domain.llm.calls) == calls


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


def test_a_tick_of_blank_replies_stops_at_what_it_reserved_retries_and_all(tmp_path, monkeypatch):
    domain = domains.WordProblems(tmp_path)
    backend = FakeLLM(default="")
    _with_model(domain, EmptyRetry(backend))
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