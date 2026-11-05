# SPDX-License-Identifier: Apache-2.0
"""What the third review of batch 1 and the first review of the reflexive step reproduced, held here once repaired."""
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.integrity import build_manifest
from g6.mcp import improve
from g6.metaharness import JUDGING_PATTERNS
from g6.metaharness.loop import ENVELOPE_PATTERNS, LoopSettings, ShadowLoop
from g6.metaharness.method import Method, tune
from g6.metaharness.pool import EditSpace, Pool
from g6.metaharness.reflect import EditStrategy, Pooled
from g6.metaharness.reflex import diagnose, funnel, refusals, stages
from g6.metaharness.shadow import ADOPTIONS
from g6.operator.store import OperatorStore
from g6.self_maintenance.envelope import CycleLog, runner_lock, stop_reason
from g6.self_maintenance.pacing import AccountReading, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import WEEK_MINUTES, AccountReader

_spec = importlib.util.spec_from_file_location("loop_domains", Path(__file__).with_name("_loop_domains.py"))
domains = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(domains, "WordProblems"):
    _spec.loader.exec_module(domains)
START = datetime(2026, 10, 26, tzinfo=UTC)
NOW = START + timedelta(days=2)
SRC = Path(__file__).resolve().parents[2] / "src" / "g6"


def _enrolled(tmp_path, method=None, proposal=None):
    method = method or Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, use_recovery=False, use_reflection=False)
    settings = LoopSettings(windows=(ProviderWindow("claude", 1_000_000, START),), method=method, audit_per_tick=60)
    domain = domains.WordProblems(tmp_path)

    def opened():
        return ShadowLoop(domain, tmp_path / "loop", settings, proposer=domains.proposes(proposal or {"prompt": domains.WORKING}),
                          now=lambda: NOW, clock=domains.Clock())

    loop = opened()
    assert loop.enrol("Ada")["completion_state"] == "qualified-draft"
    return loop, opened


def test_a_restart_keeps_a_reservation_made_on_the_providers_week(tmp_path):
    reset = START + timedelta(days=2)
    window = ProviderWindow("codex", 1000, START)
    current = [AccountReading(used=0, read_at=START, resets_at=reset)]
    store = OperatorStore(tmp_path / "operator.db")
    try:
        before = BudgetMeter((window,), store=store, read_account=lambda *_: current[0])
        before.reading("codex", reset - timedelta(hours=1))
        now = reset + timedelta(hours=1)
        current[0] = AccountReading(used=0, read_at=now, resets_at=reset + timedelta(days=7))
        assert before.reserve("codex", now, 150).admitted
        after = BudgetMeter((window,), store=store, read_account=lambda *_: current[0])
        assert after.reading("codex", now).used >= 150
        assert not after.reserve("codex", now, 150).admitted
    finally:
        store.close()


def test_a_first_reading_with_no_earlier_book_stays_on_the_week_still_open(tmp_path):
    reset = START + timedelta(days=2)
    window = ProviderWindow("codex", 1000, START)
    store = OperatorStore(tmp_path / "operator.db")
    try:
        meter = BudgetMeter((window,), store=store)
        assert meter.reserve("codex", START + timedelta(days=1), 100).admitted
        told = BudgetMeter((window,), store=store, read_account=lambda *_: AccountReading(used=0, read_at=START, resets_at=reset))
        assert told.reading("codex", START + timedelta(days=1)).used >= 100
    finally:
        store.close()


def test_an_ended_week_still_says_where_the_providers_weeks_fall():
    reset = START + timedelta(days=3)
    now = reset + timedelta(hours=1)
    window = ProviderWindow("codex", 1000, START)
    reply = {"rateLimits": {"secondary": {"usedPercent": 90, "windowDurationMins": WEEK_MINUTES, "resetsAt": int(reset.timestamp())}}}
    reader = AccountReader((window,), ask={"codex": lambda: reply})
    reader.refresh(now)
    store = OperatorStore(":memory:")
    try:
        meter = BudgetMeter((window,), store=store, read_account=reader)
        seen = meter.reading("codex", now)
        assert (seen.used, seen.day) == (0, 1) and seen.ends > now, "a tick begun in the new week must not read as past its end"
        assert not meter.reserve("codex", now, 300).admitted
    finally:
        store.close()


def test_a_stop_with_no_folder_reaches_a_runner_in_the_earlier_default(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("G6_HOME", str(home))
    earlier = home / "improve"
    earlier.mkdir(parents=True)
    (earlier / "settings.json").write_text("{}", encoding="utf-8")
    with runner_lock(earlier):
        improve.stop("", "stop the existing runner")
        assert stop_reason(earlier)


def test_the_default_folder_stands_once_a_loop_is_there(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("G6_HOME", str(home))
    for folder in (home / "improve", home.with_name("home-improve")):
        folder.mkdir(parents=True)
        (folder / "settings.json").write_text("{}", encoding="utf-8")
    assert improve._root("") == home.with_name("home-improve")


def test_a_demotion_refuses_a_chain_behind_the_log(tmp_path):
    loop, opened = _enrolled(tmp_path, Method(cycles=1, propose_budget=120, select_budget=400, race_block=12, reflect_calls=1))
    assert loop.tick()["outcome"] == "adopted"
    chain = loop.root / "workspace" / ADOPTIONS
    at_one = chain.read_bytes()
    assert loop.demote(1, "Ada")["head_version"] == 2
    chain.write_bytes(at_one)
    assert opened().demote(1, "Bo")["completion_state"] == "blocked-escalated"
    assert chain.read_bytes() == at_one


@pytest.mark.parametrize("reader", [stages, refusals, diagnose])
@pytest.mark.parametrize("fields", [{"tuning": {"offered": 1}}, {"tuning": ["odd"]}, {"tuning": {"offered": "many", "measured": 1, "kept": "x"}},
                                    {"audit": "none", "decision": ["no"]}, {"budget": {"waited": "soon"}}, {"code": ["G6_E_X"], "outcome": "blocked"},
                                    {"decision": {"code": {"a": 1}, "reasons": {"a": 1}, "verdict": "refuse"}}, {"constraint": ["x"], "outcome": "adopted"}])
def test_the_readers_take_any_row_the_log_can_hold(tmp_path, reader, fields):
    log = CycleLog(tmp_path / "cycles.jsonl")
    log.cycle(json.loads(json.dumps({"outcome": "no_winner", "code": "G6_E_LOOP_CYCLE", **fields})))
    log.reload()
    reader(log.cycles())


def test_a_wrapper_keeps_the_counts_of_the_strategy_inside(tmp_path):
    loop, _ = _enrolled(tmp_path)
    inner = EditStrategy(EditSpace({"retries": [0, 1]}), calls=1)
    method = Method(cycles=2, propose_budget=200, select_budget=400, race_block=12, use_recovery=False, use_reflection=False)
    run = tune(loop.mh, method, approve=lambda _: None, extra=(Pooled(inner, Pool()),))
    got = funnel(run.cycles)
    assert (got["measured"], got["repeated"], got["kept"]) == (inner.measured, inner.duplicates, 1) == (1, 1, 1)


def test_offered_counts_valid_candidates_and_nothing_past_the_limit(tmp_path):
    class InvalidTail:
        name = "tail"

        def propose(self, view, runner):
            return [{**view.base, "prompt": "Please be polite."}, {"not_a_profile_field": True}]

    loop, _ = _enrolled(tmp_path)
    method = Method(cycles=1, candidates_per_cycle=1, propose_budget=120, select_budget=400, use_recovery=False, use_reflection=False)
    counts = funnel(tune(loop.mh, method, approve=lambda _: None, extra=(InvalidTail(),)).cycles)
    assert (counts["new"], counts["offered"]) == (1, 1)


def test_a_cycle_that_raises_keeps_what_it_had_made(tmp_path, monkeypatch):
    loop, _ = _enrolled(tmp_path)
    before = len(loop.mh.ledger.rows("proposal"))

    def crash(*args, **kwargs):
        raise RuntimeError("race failed after proposing")

    monkeypatch.setattr(loop.mh, "select", crash)
    loop.tick()
    row = loop.log.cycles()[-1]
    assert row["outcome"] == "error" and row["tuning"]["new"] == len(loop.mh.ledger.rows("proposal")) - before == 1


def test_each_package_file_that_runs_on_import_is_pinned():
    pinned = build_manifest(SRC, patterns=(*ENVELOPE_PATTERNS, *JUDGING_PATTERNS))
    assert {"__init__.py", "metaharness/__init__.py", "core/__init__.py", "self_maintenance/__init__.py"} <= set(pinned)
