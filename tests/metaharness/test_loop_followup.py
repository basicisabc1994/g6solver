# SPDX-License-Identifier: Apache-2.0
"""What batch 1's last review left open, and the race that lost the rehearsal's best candidate, held here once repaired."""
import contextvars
import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import g6
from g6.core.ledger import digest
from g6.core.llm import FakeLLM, LLMError
from g6.integrity import build_manifest
from g6.metaharness import JUDGING_PATTERNS, Model, Profile
from g6.metaharness import select as racing
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop, _Counted, _Refused, _Requests
from g6.metaharness.method import Method, tune
from g6.metaharness.pool import EditSpace, Pool
from g6.metaharness.reflect import EditStrategy, Pooled
from g6.metaharness.reflex import diagnose, funnel, stages
from g6.metaharness.schema import SuiteCase
from g6.metaharness.select import race, race_calls
from g6.operator.store import OperatorStore
from g6.self_maintenance.envelope import CYCLE_LOG, CycleLog, ask_to_stop, heartbeat
from g6.self_maintenance.pacing import BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import WEEK_MINUTES, AccountReader

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)
MONDAY = datetime(2026, 10, 26, tzinfo=UTC)
BASE = Profile(routing={"answer": "local"})
QUIET = BASE.model_copy(update={"prompt": "Note."})
GOOD = BASE.model_copy(update={"prompt": "Check it."})


def test_a_refusal_while_a_stop_is_read_stops_the_request_that_was_reading_it():
    reading, release = threading.Event(), threading.Event()
    reads = []

    def halted():
        reads.append(None)
        if len(reads) == 2:      # the first is the call's own look, the second the request's boundary
            reading.set()
            assert release.wait(5)

    requests = _Requests(halted=halted)
    requests.open(10)
    backend = FakeLLM(default="sent after the refusal")
    ended = []

    def pending():
        try:
            ended.append(_Counted(backend, requests).complete("system", "second request"))
        except _Refused as refused:
            ended.append(refused)

    worker = threading.Thread(target=pending)
    worker.start()
    assert reading.wait(5)
    with pytest.raises(_Refused):
        requests.at_a_refusal(LLMError("G6_E_MODEL_REFUSED: refusal"), "first request")
    release.set()
    worker.join(5)
    assert backend.calls == [] and isinstance(ended[0], _Refused)


def test_a_context_kept_past_its_call_sends_nothing(tmp_path):
    domain = domains.WordProblems(tmp_path)
    release = threading.Event()
    failed = []

    class KeepsTheContext:
        def __init__(self, inner):
            self._inner, self.worker = inner, None

        def complete(self, system, user, *, max_tokens=1024):
            answer = self._inner.complete(system, user, max_tokens=max_tokens)
            if self.worker is None:
                kept = contextvars.copy_context()

                def later():
                    release.wait(10)
                    try:
                        kept.run(self._inner.complete, "system", "sent later")
                    except LLMError as refused:
                        failed.append(refused)

                self.worker = threading.Thread(target=later)
                self.worker.start()
            return answer

    caller = KeepsTheContext(domain.llm)
    run = domain.execution()
    domain.execution = lambda: dataclasses.replace(run, models={"local": Model(caller)})
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, MONDAY),),
                            method=Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1))
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: MONDAY + timedelta(days=2), clock=domains.Clock())
    enrolled = loop.enrol("Ada")
    assert enrolled["completion_state"] == "qualified-draft", enrolled
    try:
        assert loop.tick()["outcome"] == "adopted"
        ask_to_stop(loop.root, "stop all further requests")
        before = len(domain.llm.calls)
    finally:
        release.set()
        caller.worker.join(10)
    assert len(domain.llm.calls) == before and len(failed) == 1


def test_a_restart_in_the_settings_next_week_keeps_what_the_providers_week_reserved(tmp_path):
    reset = MONDAY + timedelta(days=8)     # the settings' week ends on the Monday, the provider's on the Tuesday
    window = ProviderWindow("codex", 1000, MONDAY)
    reply = {"rateLimits": {"secondary": {"usedPercent": 0, "windowDurationMins": WEEK_MINUTES, "resetsAt": int(reset.timestamp())}}}
    path = tmp_path / "operator.db"
    first, later = MONDAY + timedelta(days=2), MONDAY + timedelta(days=7, hours=1)

    def meter(store, now):
        reader = AccountReader((window,), ask={"codex": lambda: reply})
        reader.refresh(now)
        return BudgetMeter((window,), store=store, read_account=reader)

    store = OperatorStore(path)
    try:
        assert meter(store, first).reserve("codex", first, 350).admitted      # a crashed tick leaves it reserved, even if it sent nothing
    finally:
        store.close()
    store = OperatorStore(path)
    try:
        restarted = meter(store, later)
        assert restarted.reading("codex", later).used == 350
        assert not restarted.reserve("codex", later, 800).admitted
    finally:
        store.close()


def test_a_runner_that_cannot_open_says_which_process_it_was(tmp_path):
    root = tmp_path / "loop"
    root.mkdir()
    settings = {"domain_file": str(tmp_path / "missing_domain.py"), "read_account": False,
                "windows": [{"provider": "claude", "allowance": 5000, "week_start": MONDAY.isoformat()}]}
    (root / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    CycleLog(root / CYCLE_LOG).start("Ada", settings=digest(settings), stops=0)
    ended = subprocess.run([sys.executable, "-P", "-m", "g6.metaharness.loop", "run", str(root)], cwd=root, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    record = heartbeat(root)
    assert ended.returncode == 1 and record["code"] == "G6_E_LOOP_CANNOT_OPEN"
    assert record.get("pid") and "in_job" in record


def _case(number, group=None):
    return SuiteCase(f"s{number:02d}", f"case {number}", "right", meta={"group": group} if group else {})


def _raced(monkeypatch, cases, successes, **kw):
    """A race on ``cases``. ``successes``: the cases each profile gets right, by profile. The base gets none right unless named."""
    def arm(workflow, factory, profile, case, name, **_):
        return SimpleNamespace(success=case.case_id in successes.get(profile.digest(), ()), model_calls=1)

    monkeypatch.setattr(racing, "_arm", arm)
    workflow = SimpleNamespace(suite=SimpleNamespace(selection=cases))
    return race(workflow, None, BASE, [QUIET, GOOD], budget=kw.pop("budget", 10_000), **kw)


def test_a_tie_drops_nobody(monkeypatch):
    cases = [_case(n) for n in range(20)]
    result = _raced(monkeypatch, cases, {GOOD.digest(): {c.case_id for c in cases[10:]}}, block=10)
    assert result.winner == GOOD
    assert all(row["dropped_at_block"] is None for row in result.ranking if row["candidate_digest"] == GOOD.digest())


def test_a_strict_loser_is_still_dropped(monkeypatch):
    cases = [_case(n) for n in range(20)]
    result = _raced(monkeypatch, cases, {GOOD.digest(): {c.case_id for c in cases}, BASE.digest(): {"s00"}, QUIET.digest(): set()}, block=10)
    assert result.winner == GOOD and result.blocks == [1, 1]
    assert next(row for row in result.ranking if row["candidate_digest"] == QUIET.digest())["dropped_at_block"] == 1


def test_a_block_is_not_one_group(monkeypatch):
    first, second = [_case(n, "one bug") for n in range(10)], [_case(n, "another bug") for n in range(10, 20)]
    right = {c.case_id for c in second}                      # it loses the one case of the first bug the base gets right
    result = _raced(monkeypatch, first + second, {GOOD.digest(): right, BASE.digest(): {"s00"}, QUIET.digest(): {"s00"}}, block=10)
    assert result.winner == GOOD, "a loss on one bug must not end a candidate before any other bug was seen"


def test_cases_with_no_group_keep_their_order(monkeypatch):
    seen = []

    def arm(workflow, factory, profile, case, name, **_):
        seen.append(case.case_id)
        return SimpleNamespace(success=False, model_calls=1)

    monkeypatch.setattr(racing, "_arm", arm)
    cases = [_case(n) for n in range(4)]
    race(SimpleNamespace(suite=SimpleNamespace(selection=cases)), None, BASE, [QUIET], budget=100, block=2)
    assert seen[::2] == [c.case_id for c in cases]


def test_the_bound_covers_a_race_of_ties(monkeypatch):
    cases = [_case(n) for n in range(30)]
    result = _raced(monkeypatch, cases, {}, block=5)
    assert result.calls_used == race_calls(len(cases), 2) == 90


# The reflexive step's second review -------------------------------------------------------------------------------------
def _loop(tmp_path, method=None, initial_prompt=None, proposer=None):
    method = method or Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, use_recovery=False, use_reflection=False)
    domain = domains.WordProblems(tmp_path, **({"initial_prompt": initial_prompt} if initial_prompt else {}))
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, MONDAY),), method=method, audit_per_tick=60)
    loop = ShadowLoop(domain, tmp_path / "loop", settings, proposer=proposer or domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: MONDAY + timedelta(days=2), clock=domains.Clock())
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop


def test_the_readers_take_a_count_below_zero(tmp_path):
    log = CycleLog(tmp_path / "cycles.jsonl")
    log.cycle({"outcome": "no_winner", "budget": {"waited": -2}})
    log.reload()
    stages(log.cycles())
    diagnose(log.cycles())


def test_a_new_candidate_that_lost_is_not_hidden_by_another_proposers_repeat(tmp_path):
    class RepeatsTheBase:
        name = "repeats_the_base"

        def propose(self, view, runner):
            return [view.base]

    loop = _loop(tmp_path, Method(cycles=1, propose_budget=400, select_budget=400, use_recovery=False, use_reflection=False),
                 "Work through the problem step by step.")
    edit = EditStrategy(EditSpace({"prompt": ["Please be polite."]}), calls=1)
    counts = funnel(tune(loop.mh, loop.settings.method, approve=lambda _: None, extra=(edit, RepeatsTheBase())).cycles)
    assert (edit.measured, counts["offered"], counts["new"], counts["kept"]) == (1, 1, 0, 0)
    row = {"outcome": "no_winner", "tuning": counts}
    found = diagnose([row, row, row])
    assert (found.level, found.constraint) == ("work", "tuning")


def test_a_wrapper_counts_what_it_measures_for_a_strategy_that_keeps_no_count(tmp_path):
    class Offers:
        name = "offers"

        def propose(self, view, runner):
            return [{**view.base, "retries": 1}]

    loop = _loop(tmp_path, Method(cycles=1, propose_budget=400, select_budget=400, use_recovery=False, use_reflection=False))
    run = tune(loop.mh, loop.settings.method, approve=lambda _: None, extra=(Pooled(Offers(), Pool()),))
    assert funnel(run.cycles)["measured"] == 1


def test_a_cycle_that_raises_leaves_the_counts_of_the_cycles_before_it(tmp_path, monkeypatch):
    loop = _loop(tmp_path, Method(cycles=2, propose_budget=400, select_budget=400, race_block=12, use_recovery=False,
                                  use_reflection=False, use_edits=True, reflect_calls=1))
    propose, asked = loop.mh.propose, []

    def fails_in_the_second_cycle(strategy, **kwargs):
        asked.append(strategy.name)
        if len(asked) == 3:
            raise RuntimeError("second tuning cycle failed before proposing")
        return propose(strategy, **kwargs)

    monkeypatch.setattr(loop.mh, "propose", fails_in_the_second_cycle)
    row = loop.tick()
    assert row["outcome"] == "error" and asked == ["edit", "failure_trace", "edit"]
    assert row["tuning"]["measured"] == 1


def test_a_change_anywhere_in_the_package_is_a_change_to_what_runs():
    source = Path(g6.__file__).resolve().parent
    pinned = build_manifest(source, patterns=(*ENVELOPE_PATTERNS, *JUDGING_PATTERNS))
    assert {path.relative_to(source).as_posix() for path in source.rglob("*.py") if "__pycache__" not in path.parts} <= set(pinned)


# The reflexive step's third review -----------------------------------------------------------------------------------------
def test_offered_counts_every_valid_candidate_past_the_cap_on_new_ones(tmp_path):
    class OffersThree:
        name = "three"

        def propose(self, view, runner):
            return [{**view.base, "retries": 1}] * 3

    loop = _loop(tmp_path, Method(cycles=1, candidates_per_cycle=1, propose_budget=400, select_budget=400, use_recovery=False,
                                  use_reflection=False))
    counts = funnel(tune(loop.mh, loop.settings.method, approve=lambda _: None, extra=(OffersThree(),)).cycles)
    assert (counts["new"], counts["offered"]) == (1, 3)


def test_a_tick_cut_off_inside_a_strategy_keeps_what_it_had_measured(tmp_path, monkeypatch):
    from g6.metaharness import reflect

    measured, replies, real = [], [], reflect.run_on_dev

    def observed(*args, **kwargs):
        got = real(*args, **kwargs)
        if got is not None:
            measured.append(got)
        return got

    def answer(system, user):
        replies.append(user)
        if len(replies) == 2:
            raise LLMError("G6_E_MODEL_REFUSED: refusal")
        return json.dumps({"prompt": domains.WORKING})

    monkeypatch.setattr(reflect, "run_on_dev", observed)
    loop = _loop(tmp_path, Method(cycles=1, propose_budget=400, select_budget=400, use_recovery=False, use_reflection=True,
                                  reflect_calls=2), proposer=FakeLLM(answer))
    row = loop.tick()
    assert row["outcome"] == "blocked" and len(measured) == 1
    assert row["tuning"]["measured"] == 1


def test_a_wrapper_counts_the_repeats_its_pool_caught(tmp_path):
    class RepeatsTheBase:
        name = "repeats"

        def propose(self, view, runner):
            return [view.base, view.base]

    loop = _loop(tmp_path, Method(cycles=1, propose_budget=400, select_budget=400, use_recovery=False, use_reflection=False))
    counts = funnel(tune(loop.mh, loop.settings.method, approve=lambda _: None, extra=(Pooled(RepeatsTheBase(), Pool()),)).cycles)
    assert (counts["offered"], counts["new"], counts["measured"], counts["repeated"]) == (2, 0, 0, 2)


def test_diagnosis_takes_a_count_too_large_for_a_float(tmp_path):
    log = CycleLog(tmp_path / "cycles.jsonl")
    log.cycle({"outcome": "no_winner", "budget": {"waited": 10 ** 400}})
    log.reload()
    assert stages(log.cycles())[0].attempted == 10 ** 400 + 1
    diagnose(log.cycles())


def test_two_strategies_of_one_name_are_both_counted(tmp_path):
    class Offers:
        name = "offers"

        def __init__(self, retries):
            self.retries = retries

        def propose(self, view, runner):
            return [{**view.base, "retries": self.retries}]

    loop = _loop(tmp_path, Method(cycles=1, propose_budget=400, select_budget=400, use_recovery=False, use_reflection=False))
    before = len(loop.mh.ledger.rows("proposal"))
    run = tune(loop.mh, loop.settings.method, approve=lambda _: None, extra=(Offers(1), Offers(2)))
    assert funnel(run.cycles)["new"] == len(loop.mh.ledger.rows("proposal")) - before == 2
