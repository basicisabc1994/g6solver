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
from g6.core.consent_callers import CallerWrapper
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
from g6.self_maintenance.pacing import AccountReading, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import WEEK_MINUTES, AccountReader

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)
MONDAY = datetime(2026, 10, 26, tzinfo=UTC)
BASE = Profile(routing={"answer": "local"})
QUIET = BASE.model_copy(update={"prompt": "Note."})
GOOD = BASE.model_copy(update={"prompt": "Check it."})

pytestmark = pytest.mark.usefixtures('consented_provider_fakes')


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

    class KeepsTheContext(CallerWrapper):
        def __init__(self, inner):
            super().__init__(inner)
            self.worker = None

        def complete(self, system, user, *, max_tokens=1024):
            answer = self.inner.complete(system, user, max_tokens=max_tokens)
            if self.worker is None:
                kept = contextvars.copy_context()

                def later():
                    release.wait(10)
                    try:
                        kept.run(self.inner.complete, "system", "sent later")
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


# --- this branch's first review --------------------------------------------------------------------------------------

def _meter(store, start=MONDAY, resets_at=None):
    read = (lambda *_: AccountReading(resets_at=resets_at)) if resets_at is not None else None
    return BudgetMeter((ProviderWindow("codex", 1000, start),), store=store, read_account=read)


def test_a_restart_in_the_providers_next_week_starts_a_book_of_its_own(tmp_path):
    sunday, path = MONDAY + timedelta(days=6), tmp_path / "operator.db"
    store = OperatorStore(path)
    try:
        meter = _meter(store, resets_at=sunday)                   # the provider's weeks end on Sundays
        admission = meter.reserve("codex", MONDAY + timedelta(days=2), 300)
        meter.settle(admission, 300)
    finally:
        store.close()
    store = OperatorStore(path)
    try:
        later = _meter(store, resets_at=sunday + timedelta(days=7))
        assert later.reading("codex", sunday + timedelta(hours=1)).used == 0      # last week's 300 were last week's
    finally:
        store.close()


def test_the_same_week_written_in_another_zone_is_the_same_book(tmp_path):
    from datetime import timezone

    now, path = MONDAY + timedelta(days=2), tmp_path / "operator.db"
    store = OperatorStore(path)
    try:
        assert _meter(store).reserve("codex", now, 350).admitted
    finally:
        store.close()
    store = OperatorStore(path)
    try:
        brisbane = _meter(store, start=MONDAY.astimezone(timezone(timedelta(hours=10))))
        assert brisbane.reading("codex", now).used == 350
        assert not brisbane.reserve("codex", now, 350).admitted        # 700 against day 3's 600
    finally:
        store.close()


@pytest.mark.parametrize("first", ["moved", "aligned"])
def test_a_week_is_booked_once_whichever_meter_resolves_it_first(tmp_path, first):
    now, store = MONDAY + timedelta(days=2), OperatorStore(tmp_path / "operator.db")
    try:
        moved = _meter(store, resets_at=MONDAY + timedelta(days=8))     # a restart learning the provider's Tuesdays
        aligned = _meter(store, start=MONDAY + timedelta(days=1))       # one already on them
        order = [moved, aligned] if first == "moved" else [aligned, moved]
        assert order[0].reserve("codex", now, 350).admitted
        assert not order[1].reserve("codex", now, 350).admitted         # 700 against day 2's 400 of one week
        assert order[0].reading("codex", now).venture == order[1].reading("codex", now).venture
    finally:
        store.close()


def test_a_week_resolved_by_another_process_while_this_one_checks_is_booked_once(tmp_path, monkeypatch):
    """The first review's interleaving: a restarted meter checks for the provider week's own venture, and before it books
    under the settings' week another process reserves under the provider's."""
    path, now = tmp_path / "operator.db", MONDAY + timedelta(days=2)
    store = OperatorStore(path)
    restarted = _meter(store, resets_at=MONDAY + timedelta(days=8))
    other = OperatorStore(path)
    aligned = _meter(other, start=MONDAY + timedelta(days=1))
    real = restarted.scope.conn.execute
    raced: list = []

    class Interleaved:
        def __getattr__(self, name):
            return getattr(real.__self__, name)

        def __enter__(self):
            return real.__self__.__enter__()

        def __exit__(self, *exc):
            return real.__self__.__exit__(*exc)

        def execute(self, sql, *args):
            cursor = real(sql, *args)              # a read outside a lock has its first row once it is executed
            if not raced and "ventures" in sql and not real.__self__.in_transaction:
                raced.append(aligned.reserve("codex", now, 350).admitted)
            return cursor

    monkeypatch.setattr(restarted.scope, "conn", Interleaved())
    try:
        mine = restarted.reserve("codex", now, 350)
        assert sum([mine.admitted, *raced]) <= 1
    finally:
        store.close()
        other.close()


def test_a_request_waiting_on_the_stop_when_its_tick_closes_sends_nothing():
    reading, release, failures = threading.Event(), threading.Event(), []
    backend = FakeLLM(default="answer")

    def halted():
        if threading.current_thread().name == "late":
            reading.set()
            assert release.wait(5)

    requests = _Requests(halted=halted)
    requests.open(2)

    class SendsLater(CallerWrapper):
        def complete(self, system, user, *, max_tokens=1024):
            answer, kept = self.inner.complete(system, user, max_tokens=max_tokens), contextvars.copy_context()

            def later():
                try:
                    kept.run(self.inner.complete, "system", "sent later")
                except BaseException as exc:
                    failures.append(exc)

            self.worker = threading.Thread(target=later, name="late")
            self.worker.start()
            assert reading.wait(5)
            return answer

    caller = SendsLater(backend)
    try:
        assert _Counted(caller, requests).complete("system", "asked") == "answer"
        requests.close()                     # the tick ends while the late request reads the stop
    finally:
        release.set()
        caller.worker.join(10)
    assert backend.calls == [("system", "asked")] and len(failures) == 1 and isinstance(failures[0], LLMError)


def _install(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    source = tmp_path / "g6" / "metaharness" / "select.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(Path(racing.__file__).read_bytes())
    monkeypatch.setattr(loop_module, "_SOURCE", tmp_path / "g6")
    return source, loop_module.envelope_sha256


@pytest.mark.parametrize("name", ["stray.pyc", "select.pyd", "select.so"])
def test_a_file_python_would_import_beside_the_source_changes_the_envelope(tmp_path, monkeypatch, name):
    import importlib.machinery

    source, envelope = _install(tmp_path, monkeypatch)
    if not any(suffix.endswith(Path(name).suffix) for suffix in importlib.machinery.all_suffixes()):
        pytest.skip(f"this platform's Python imports no {Path(name).suffix} file")
    enrolled = envelope()
    (source.parent / name).write_bytes(b"planted")
    assert envelope() != enrolled


def test_a_cache_a_first_import_writes_leaves_the_envelope_as_it_was(tmp_path, monkeypatch):
    import py_compile

    source, envelope = _install(tmp_path, monkeypatch)
    enrolled = envelope()
    py_compile.compile(str(source), doraise=True)                      # into __pycache__, as an import writes it
    assert envelope() == enrolled


def test_the_runner_is_started_reading_and_writing_no_cached_bytecode(tmp_path, monkeypatch):
    from g6.mcp import improve

    seen: dict = {}

    def popen(argv, **kwargs):
        seen.update(argv=argv)
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(subprocess, "Popen", popen)
    root = tmp_path / "loop"
    root.mkdir()
    improve._spawn(root, {})
    argv = seen["argv"]
    assert "-B" in argv and argv[argv.index("-X") + 1].startswith("pycache_prefix=")
    prefix = Path(argv[argv.index("-X") + 1].removeprefix("pycache_prefix="))
    assert prefix.is_dir() and not any(prefix.iterdir())
    assert argv[-4:] == ["-m", "g6.metaharness.loop", "run", str(root)]


def test_the_command_line_runner_ticks_only_when_no_cached_bytecode_can_run(tmp_path, monkeypatch):
    from g6.metaharness import runner

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    assert runner._launch_risk()
    empty = tmp_path / "prefix"
    empty.mkdir()
    monkeypatch.setattr(sys, "pycache_prefix", str(empty))
    assert runner._launch_risk() == ""
    (empty / "planted.pyc").write_bytes(b"planted")
    assert runner._launch_risk()
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    (empty / "planted.pyc").unlink()
    assert runner._launch_risk()


def test_a_command_line_run_that_could_read_cached_bytecode_ticks_nothing(tmp_path, monkeypatch):
    from g6.metaharness import runner
    from tests.metaharness.test_loop_runner import _enrolled_folder

    root, _ = _enrolled_folder(tmp_path)
    ticked: list = []
    monkeypatch.setattr(runner.Runner, "run", lambda self: ticked.append(self) or 0)     # a tick would start here
    monkeypatch.setattr(sys, "pycache_prefix", None)
    assert runner.main(["run", str(root)]) == 1 and ticked == []
    assert heartbeat(root)["code"] == runner.CACHED_BYTECODE


# --- this branch's second review --------------------------------------------------------------------------------------

def test_a_restart_with_no_reading_stays_on_the_providers_week(tmp_path):
    reset, path = MONDAY + timedelta(days=8), tmp_path / "operator.db"
    store = OperatorStore(path)
    try:
        meter = _meter(store, resets_at=reset)                         # the provider's weeks run Tuesday to Tuesday
        assert meter.reserve("codex", MONDAY + timedelta(days=2), 350).admitted
        assert meter.reserve("codex", MONDAY + timedelta(days=7), 650).admitted
    finally:
        store.close()
    store = OperatorStore(path)
    try:
        blind = _meter(store)                                          # the first reading after a restart can fail
        now = MONDAY + timedelta(days=7, hours=1)                      # past the settings' Monday, inside the provider's week
        assert blind.reading("codex", now).used == 1000 and not blind.reserve("codex", now, 100).admitted
    finally:
        store.close()


def test_a_provider_that_moves_its_weeks_again_moves_the_kept_grid(tmp_path):
    store = OperatorStore(tmp_path / "operator.db")
    try:
        _meter(store, resets_at=MONDAY + timedelta(days=8)).reading("codex", MONDAY + timedelta(days=2))
        _meter(store, resets_at=MONDAY + timedelta(days=9)).reading("codex", MONDAY + timedelta(days=3))
        later = _meter(store)
        assert later.reading("codex", MONDAY + timedelta(days=10)).day == 2      # Wednesday weeks now: day 2 is the Thursday
    finally:
        store.close()


def test_a_charge_after_a_dated_reading_counts_on_top_of_it_for_every_meter(tmp_path):
    now, path = MONDAY + timedelta(days=3), tmp_path / "operator.db"
    account = AccountReading(used=500, read_at=now, resets_at=MONDAY + timedelta(days=7), estimated=False)
    first_store, second_store = OperatorStore(path), OperatorStore(path)
    try:
        first = BudgetMeter((ProviderWindow("codex", 1000, MONDAY),), store=first_store, read_account=lambda *_: account)
        spend = first.reserve("codex", now, 200)
        first.settle(spend, 200)
        second = BudgetMeter((ProviderWindow("codex", 1000, MONDAY),), store=second_store, read_account=lambda *_: account)
        later = now + timedelta(seconds=2)
        assert second.reading("codex", later).used == 700 and not second.reserve("codex", later, 200).admitted
    finally:
        first_store.close()
        second_store.close()


def test_a_group_named_like_a_case_takes_no_case_out_of_its_turn():
    cases = [SuiteCase("g1", "q", "a", meta={"group": "first"}), SuiteCase("g2", "q", "a", meta={"group": "first"}),
             SuiteCase("first", "q", "a"), SuiteCase("second", "q", "a")]
    assert [c.case_id for c in racing._in_turns(cases) if not c.meta.get("group")] == ["first", "second"]


def test_a_restart_that_learns_the_weeks_moved_again_keeps_the_book_of_the_week_in_progress(tmp_path):
    path, now = tmp_path / "operator.db", MONDAY + timedelta(days=2)
    store = OperatorStore(path)
    try:
        aligned = _meter(store, start=MONDAY + timedelta(days=1))           # on the provider's Tuesdays, its own book
        aligned.reading("codex", now)
        _meter(store, resets_at=MONDAY + timedelta(days=8)).reading("codex", now)     # the grid is kept: Tuesdays
        assert aligned.reserve("codex", now, 350).admitted
    finally:
        store.close()
    store = OperatorStore(path)
    try:
        moved = _meter(store, resets_at=MONDAY + timedelta(days=9))           # now it says Wednesdays
        assert moved.reading("codex", now).used == 350 and not moved.reserve("codex", now, 150).admitted
    finally:
        store.close()


@pytest.mark.parametrize("name", ["planted.pyc", "planted.py", "__init__.py", "extra-code.py"])
def test_a_module_python_would_import_from_a_cache_folder_changes_the_envelope(tmp_path, monkeypatch, name):
    source, envelope = _install(tmp_path, monkeypatch)
    enrolled = envelope()
    cache = source.parent / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / name).write_bytes(b"planted")                         # importable as g6.metaharness.__pycache__.planted
    assert envelope() != enrolled


def test_a_cache_file_named_for_its_interpreter_is_no_module_and_leaves_the_envelope(tmp_path, monkeypatch):
    source, envelope = _install(tmp_path, monkeypatch)
    enrolled = envelope()
    cache = source.parent / "__pycache__"
    cache.mkdir(exist_ok=True)
    (cache / f"select.{sys.implementation.cache_tag}.pyc").write_bytes(b"a first import writes this")
    (cache / f"select.{sys.implementation.cache_tag}.opt-1.pyc").write_bytes(b"and this, under -O")
    assert envelope() == enrolled


@pytest.mark.parametrize("hours", [10, -5])
def test_a_reservation_booked_under_a_week_named_in_another_zone_still_counts(tmp_path, hours):
    from datetime import timezone

    from g6.self_maintenance.pacing import OWNER

    start = datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=hours)))
    store = OperatorStore(tmp_path / "operator.db")
    try:
        scope = store.for_owner(OWNER)            # as a folder enrolled before weeks were named in UTC booked it
        venture = scope.venture_named(f"codex@{start.isoformat()}", caps={"model": 1000, "cash": 0})
        scope.reserve(venture, month=start.isoformat(), account="model", amount=350)
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store)
        now = (start + timedelta(days=2)).astimezone(UTC)
        assert meter.reading("codex", now).used == 350
        assert not meter.reserve("codex", now, 350).admitted        # 700 is past the third day's ceiling of 600
        assert meter.reading("codex", now).used == 350
    finally:
        store.close()


def _zone_named(store, start, amount):
    from g6.self_maintenance.pacing import OWNER

    scope = store.for_owner(OWNER)                # booked as a folder enrolled before weeks were named in UTC did
    venture = scope.venture_named(f"codex@{start.isoformat()}", caps={"model": 1000, "cash": 0})
    scope.reserve(venture, month=start.isoformat(), account="model", amount=amount)


def test_a_reservation_booked_in_another_zone_survives_two_moves_of_its_week_and_a_restart(tmp_path):
    from datetime import timezone

    start = datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=10)))
    window, now, path = ProviderWindow("codex", 1000, start), (start + timedelta(days=3, hours=1)).astimezone(UTC), tmp_path / "op.db"
    store = OperatorStore(path)
    try:
        _zone_named(store, start, 350)
        reading = [None]
        meter = BudgetMeter((window,), store=store, read_account=lambda *_: reading[0])
        assert meter.reading("codex", now).used == 350
        reading[0] = AccountReading(resets_at=start + timedelta(days=8))       # the week's end moves once
        assert meter.reading("codex", now).used == 350
    finally:
        store.close()
    store = OperatorStore(path)
    try:                                                                         # and again, after a restart
        meter = BudgetMeter((window,), store=store, read_account=lambda *_: AccountReading(resets_at=start + timedelta(days=9)))
        assert meter.reading("codex", now).used == 350
        assert not meter.reserve("codex", now, 150).admitted                    # 500 is past this day's ceiling of 400
    finally:
        store.close()


def test_a_week_with_a_venture_of_its_own_in_another_zone_keeps_its_own_book_when_the_grid_moves(tmp_path):
    from datetime import timezone

    settings = datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=10)))
    provider = settings + timedelta(days=1)
    store = OperatorStore(tmp_path / "op.db")
    try:
        _zone_named(store, settings, 100)
        _zone_named(store, provider, 350)
        meter = BudgetMeter((ProviderWindow("codex", 1000, settings),), store=store,
                            read_account=lambda *_: AccountReading(resets_at=provider + timedelta(days=7)))
        assert meter.reading("codex", (provider + timedelta(days=2, hours=1)).astimezone(UTC)).used == 350
    finally:
        store.close()


@pytest.mark.parametrize("zone", [10, 0])
def test_a_week_whose_end_moves_three_times_across_restarts_keeps_its_book(tmp_path, zone):
    from datetime import timezone

    start = datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=zone)))
    window, now, path = ProviderWindow("codex", 1000, start), (start + timedelta(days=4, hours=1)).astimezone(UTC), tmp_path / "op.db"
    store = OperatorStore(path)
    try:
        _zone_named(store, start, 350)
    finally:
        store.close()
    seen = []
    for days in (8, 9, 10, 11):
        store = OperatorStore(path)
        try:
            meter = BudgetMeter((window,), store=store,
                                read_account=lambda *_, reset=start + timedelta(days=days): AccountReading(resets_at=reset))
            seen.append(meter.reading("codex", now).used)
        finally:
            store.close()
    assert seen == [350, 350, 350, 350]


def test_a_new_week_still_cannot_take_a_book_another_week_moved_into(tmp_path):
    from g6.self_maintenance.pacing import OWNER

    store = OperatorStore(tmp_path / "op.db")
    try:
        scope = store.for_owner(OWNER)
        caps = {"model": 1000, "cash": 0}
        first, _ = scope.week_ledger("codex", "2026-10-26T00:00:00+00:00", caps=caps)
        moved, book = scope.week_ledger("codex", "2026-10-27T00:00:00+00:00", caps=caps, moved_from="2026-10-26T00:00:00+00:00")
        assert (moved, book) == (first, "2026-10-26T00:00:00+00:00")
        other, other_book = scope.week_ledger("codex", "2026-10-28T00:00:00+00:00", caps=caps,
                                              moved_from="2026-10-26T00:00:00+00:00")       # not the week the book moved to
        assert other != first and other_book == "2026-10-28T00:00:00+00:00"
    finally:
        store.close()


def test_a_week_that_returns_to_its_own_grid_and_moves_on_keeps_its_book(tmp_path):
    window, now, path = ProviderWindow("codex", 1000, MONDAY), MONDAY + timedelta(days=4, hours=1), tmp_path / "op.db"
    store = OperatorStore(path)
    try:
        assert BudgetMeter((window,), store=store).reserve("codex", now, 350).admitted
    finally:
        store.close()
    seen = []
    for days in (8, 7, 9, 7, 10):           # Monday to Tuesday, back, to Wednesday, back, to Thursday, a restart each time
        store = OperatorStore(path)
        try:
            meter = BudgetMeter((window,), store=store,
                                read_account=lambda *_, reset=MONDAY + timedelta(days=days): AccountReading(resets_at=reset))
            seen.append(meter.reading("codex", now).used)
        finally:
            store.close()
    assert seen == [350] * 5


def test_a_week_that_moved_before_weeks_were_named_in_utc_keeps_its_book_after_the_upgrade(tmp_path):
    from datetime import timezone

    from g6.self_maintenance.pacing import OWNER

    start = datetime(2026, 10, 26, tzinfo=timezone(timedelta(hours=10)))
    moved, caps, path = start + timedelta(days=1), {"model": 1000, "cash": 0}, tmp_path / "op.db"
    store = OperatorStore(path)
    try:                                    # both weeks named in the settings' zone, as before the upgrade
        scope = store.for_owner(OWNER)
        venture, book = scope.week_ledger("codex", start.isoformat(), caps=caps)
        scope.reserve(venture, month=book, account="model", amount=350)
        assert scope.week_ledger("codex", moved.isoformat(), caps=caps, moved_from=start.isoformat(),
                                 grid=(moved + timedelta(days=7)).isoformat()) == (venture, book)
    finally:
        store.close()
    store = OperatorStore(path)
    try:
        meter = BudgetMeter((ProviderWindow("codex", 1000, start),), store=store)
        assert meter.reading("codex", (start + timedelta(days=4, hours=1)).astimezone(UTC)).used == 350
    finally:
        store.close()


def _late_refusal(requests: _Requests, release: threading.Event, failures: list):
    """A caller that answers, then has its result read on a thread of its own once ``release`` is set, in the call's context."""
    from g6.self_maintenance.usage import WatchedClaude

    class AnswersFirst(CallerWrapper):
        workers: list = []

        def complete(self, system, user, *, max_tokens=1024):
            answer = self.inner.complete(system, user)
            context = contextvars.copy_context()

            def late():
                try:
                    release.wait(5)
                    context.run(WatchedClaude()._reply, 0, '{"type":"result","stop_reason":"refusal"}', "", None,
                                (system, user))
                except BaseException as exc:
                    failures.append(exc)

            self.workers.append(threading.Thread(target=late))
            self.workers[-1].start()
            return answer

    return AnswersFirst(FakeLLM(default="answer"))


@pytest.mark.parametrize("next_tick", [True, False])
def test_a_refusal_a_closed_ticks_request_met_late_leaves_the_next_tick_clear(next_tick):
    from g6.metaharness.loop import _request_digest

    requests, release, failures = _Requests(), threading.Event(), []
    requests.open(10)
    late = _late_refusal(requests, release, failures)
    assert _Counted(late, requests).complete("system", "old question") == "answer"
    requests.close()
    if next_tick:
        requests.open(10)
    release.set()
    for worker in late.workers:
        worker.join(10)
    assert failures and requests.refusal is None
    if next_tick:                           # the refused request is still never sent again
        assert _request_digest("system", "old question") in requests.refused
        fresh = FakeLLM(default="fresh")
        assert _Counted(fresh, requests).complete("system", "new question") == "fresh" and len(fresh.calls) == 1


def test_a_place_a_request_took_is_not_given_back_as_its_call_returns(monkeypatch):
    from g6.core import model_calls

    backend, counting, failures = FakeLLM(default="answer"), threading.Event(), []
    requests = _Requests()
    requests.open(2)

    class SeenLock:
        """``model_calls``'s lock, which shows when the child has taken its place and waits to be counted."""

        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if threading.current_thread().name == "child":
                counting.set()
            self.lock.acquire()

        def __exit__(self, *exc):
            self.lock.release()

    seen = SeenLock()
    monkeypatch.setattr(model_calls, "_lock", seen)

    class ReturnsWhileItsChildIsCounted(CallerWrapper):
        child = None

        def complete(self, system, user, *, max_tokens=1024):
            answer = self.inner.complete(system, user)
            context = contextvars.copy_context()
            seen.lock.acquire()

            def child():
                try:
                    context.run(self.inner.complete, system, "second")
                except BaseException as exc:
                    failures.append(exc)

            self.child = threading.Thread(target=child, name="child")
            self.child.start()
            assert counting.wait(5)
            return answer

    caller = ReturnsWhileItsChildIsCounted(backend)
    try:
        assert _Counted(caller, requests).complete("system", "first") == "answer"
    finally:
        seen.lock.release()
        caller.child.join(10)
    assert not failures and len(backend.calls) == 2 and requests.sent == 2
    with pytest.raises(LLMError, match="TICK_BUDGET_SPENT"):
        _Counted(backend, requests).complete("system", "third")
    assert len(backend.calls) == 2


def test_a_limit_a_resend_was_told_of_ends_the_tick_before_it_is_resent(monkeypatch):
    import io
    import urllib.error

    from g6.core import llm
    from g6.metaharness.loop import _AtLimit

    sent = []

    class Transport:
        def open(self, request, timeout):
            sent.append(request)
            if len(sent) == 1:
                raise urllib.error.HTTPError(request.full_url, 429, "rate limited", {"Retry-After": "0"}, io.BytesIO(b"limit"))
            return io.BytesIO(b'{"choices":[{"message":{"content":"resent"}}]}')

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *_: Transport())
    requests = _Requests()
    requests.open(10)
    caller = _Counted(llm.OpenAICompatibleCaller("local", base_url="https://unused.invalid", api_key=None), requests)
    with pytest.raises(_AtLimit):
        caller.complete("system", "question")
    assert len(sent) == 1
    with pytest.raises(_AtLimit):                     # and nothing more of that tick goes out
        caller.complete("system", "another")
    assert len(sent) == 1


def test_a_limit_a_closed_ticks_request_met_late_leaves_the_next_tick_clear():
    from g6.metaharness.loop import _AtLimit

    requests = _Requests()
    requests.open(10)
    began = requests.generation
    requests.close()
    requests.open(10)
    failed = LLMError("G6_E_LLM_RATE_LIMITED: HTTP 429 from provider")
    requests.at_a_limit(failed, began)                # a call of the closed tick, answering now
    assert requests.limited is None
    with pytest.raises(_AtLimit):                     # one of this tick's own still ends it
        requests.at_a_limit(failed, requests.generation)


@pytest.mark.skipif(os.name != "nt", reason="only Windows imports a module whatever the case of its suffix")
def test_a_module_in_a_cache_folder_with_a_suffix_in_other_case_changes_the_envelope(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    package = tmp_path / "g6"
    (package / "__pycache__").mkdir(parents=True)
    (package / "mod.py").write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(loop_module, "_SOURCE", package)
    enrolled = loop_module.envelope_sha256()
    (package / "__pycache__" / "planted.PY").write_text("value = 2\n", encoding="utf-8")
    assert loop_module.envelope_sha256() != enrolled


def test_a_limit_one_request_meets_stops_the_ticks_other_requests():
    from g6.metaharness.loop import _AtLimit

    def respond(system, user):
        if user == "first":
            raise LLMError("G6_E_LLM_RATE_LIMITED: HTTP 429 from provider")
        return "sent after the limit"

    backend, requests = FakeLLM(responder=respond), _Requests()
    requests.open(10, datetime.now(UTC) + timedelta(days=7))
    caller = _Counted(backend, requests)
    with pytest.raises(_AtLimit):
        caller.complete("system", "first")
    done = []

    def other():                                   # another thread of the same tick, still unwinding
        try:
            caller.complete("system", "second")
        except (_AtLimit, LLMError) as stopped:
            done.append(type(stopped).__name__)

    worker = threading.Thread(target=other)
    worker.start()
    worker.join(10)
    assert backend.calls == [("system", "first")] and done == ["_AtLimit"]
    requests.open(10, datetime.now(UTC) + timedelta(days=7))        # the next tick starts clear
    assert caller.complete("system", "third") == "sent after the limit"


@pytest.mark.skipif(os.name != "nt", reason="only Windows reads a folder's name without regard to case")
def test_a_cache_folder_named_in_other_case_is_a_cache_folder_on_windows(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    package = tmp_path / "g6"
    package.mkdir()
    (package / "mod.py").write_text("value = 1\n", encoding="utf-8")
    (package / "__PYCACHE__").mkdir()
    monkeypatch.setattr(loop_module, "_SOURCE", package)
    enrolled = loop_module.envelope_sha256()
    (package / "__PYCACHE__" / f"mod.{sys.implementation.cache_tag}.pyc").write_bytes(b"a first import writes this")
    assert loop_module.envelope_sha256() == enrolled
    (package / "__PYCACHE__" / "planted.pyc").write_bytes(b"importable as g6.__pycache__.planted")
    assert loop_module.envelope_sha256() != enrolled


def test_an_install_under_a_folder_named_pycache_still_pins_its_source(tmp_path, monkeypatch):
    from g6.metaharness import loop as loop_module

    package = tmp_path / "__pycache__" / "g6"
    package.mkdir(parents=True)
    (package / "mod.py").write_text("value = 1\n", encoding="utf-8")
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / f"mod.{sys.implementation.cache_tag}.pyc").write_bytes(b"a first import writes this")
    assert list(build_manifest(package)) == ["mod.py"]          # the cache inside the package still stays out
    monkeypatch.setattr(loop_module, "_SOURCE", package)
    enrolled = loop_module.envelope_sha256()
    (package / "mod.py").write_text("value = 2\n", encoding="utf-8")
    assert loop_module.envelope_sha256() != enrolled
