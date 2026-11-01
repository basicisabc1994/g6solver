# SPDX-License-Identifier: Apache-2.0
"""What a provider's own tool reports of the account's week: read between ticks, with no model call and no real tool."""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.operator.store import OperatorStore
from g6.self_maintenance import usage
from g6.self_maintenance.pacing import AccountReading, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import AccountReader, limits_reading, read_limits

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
WINDOW = ProviderWindow("codex", 1000, START)
RESET = int((START + timedelta(days=7)).timestamp())


def _limits(percent: int, *, minutes=10080, resets=RESET, allowed=True, secondary=None) -> dict:
    return {"ordinaryUsageAllowed": allowed, "rateLimits": {
        "primary": {"usedPercent": percent, "windowDurationMins": minutes, "resetsAt": resets}, "secondary": secondary}}


# Reading what the tool says ----------------------------------------------------------------------------------------

def test_the_weeks_percentage_becomes_use_in_the_meters_own_unit():
    got = limits_reading(_limits(25), WINDOW, NOW)
    assert got == AccountReading(used=250.0, estimated=False, limited_until=None)


def test_the_week_is_found_by_its_length_whichever_slot_it_is_in():
    five_hours = {"usedPercent": 90, "windowDurationMins": 300, "resetsAt": RESET}
    week = {"usedPercent": 10, "windowDurationMins": 10080, "resetsAt": RESET}
    got = limits_reading({"rateLimits": {"primary": five_hours, "secondary": week}}, WINDOW, NOW)
    assert got.used == 100.0 and got.limited_until is None


def test_a_full_session_window_is_a_limit_until_it_resets():
    soon = int((NOW + timedelta(hours=2)).timestamp())
    full = {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": soon}
    week = {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": RESET}
    got = limits_reading({"rateLimits": {"primary": full, "secondary": week}}, WINDOW, NOW)
    assert got.used == 400.0 and got.limited_until == datetime.fromtimestamp(soon, UTC)


def test_a_full_week_is_a_limit_until_the_week_resets():
    assert limits_reading(_limits(100), WINDOW, NOW).limited_until == datetime.fromtimestamp(RESET, UTC)


def test_when_the_provider_says_no_ordinary_use_is_allowed_that_is_a_limit_whatever_the_percentages():
    got = limits_reading(_limits(40, allowed=False), WINDOW, NOW)
    assert got.limited_until == datetime.fromtimestamp(RESET, UTC)


def test_a_limit_with_no_reset_time_is_looked_at_again_later():
    got = limits_reading(_limits(40, allowed=False, resets=None), WINDOW, NOW)
    assert got.limited_until == NOW + usage.REFRESH


def test_with_no_weekly_window_there_is_a_limit_or_nothing_but_never_a_figure():
    got = limits_reading(_limits(40, minutes=300), WINDOW, NOW)
    assert got.used == 0 and got.estimated is True and got.limited_until is None


@pytest.mark.parametrize("bad", [{}, {"rateLimits": None}, {"rateLimits": {}}, {"rateLimits": {"primary": {}}},
                                 {"rateLimits": {"primary": {"usedPercent": "lots", "windowDurationMins": 10080}}}])
def test_a_reply_that_does_not_read_is_no_reading(bad):
    assert limits_reading(bad, WINDOW, NOW) is None


@pytest.mark.parametrize("percent", [float("nan"), float("inf"), -float("inf"), -1, 100.5, True, None])
def test_a_percentage_off_its_scale_makes_the_whole_reply_unreadable(percent):
    assert limits_reading(_limits(percent), WINDOW, NOW) is None


@pytest.mark.parametrize("fields", [{"resets": True}, {"resets": "soon"}, {"resets": 10 ** 30}, {"resets": float("nan")},
                                    {"minutes": "a week"}, {"minutes": float("nan")}, {"minutes": 0}, {"minutes": True},
                                    {"allowed": "no"}, {"allowed": 0}, {"secondary": 7}])
def test_any_other_field_that_makes_no_sense_makes_the_reply_unreadable(fields):
    assert limits_reading(_limits(25, **fields), WINDOW, NOW) is None


def test_a_limit_whose_reset_has_passed_is_looked_at_again_later():
    gone = int((NOW - timedelta(hours=1)).timestamp())
    assert limits_reading(_limits(40, allowed=False, resets=gone), WINDOW, NOW).limited_until == NOW + usage.REFRESH


# The reader ---------------------------------------------------------------------------------------------------------

class Tool:
    def __init__(self, *answers) -> None:
        self.answers, self.calls = list(answers), 0

    def __call__(self) -> dict | None:
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_only_a_provider_whose_tool_reports_is_read():
    tool = Tool(_limits(25))
    reader = AccountReader((WINDOW, ProviderWindow("claude", 1000, START)), ask={"codex": tool})
    reader.refresh(NOW)
    assert reader("codex", START).used == 250.0 and reader("claude", START) is None
    assert "claude" in reader.said and "model calls" in reader.said["claude"]


def test_asking_the_reader_never_runs_the_tool_so_a_tick_starts_no_process():
    tool = Tool(_limits(25))
    reader = AccountReader((WINDOW,), ask={"codex": tool})
    assert reader("codex", START) is None and tool.calls == 0


def test_the_tool_is_asked_at_most_once_in_a_while():
    tool = Tool(_limits(25), _limits(30))
    reader = AccountReader((WINDOW,), ask={"codex": tool})
    reader.refresh(NOW)
    reader.refresh(NOW + timedelta(minutes=1))
    assert tool.calls == 1 and reader("codex", START).used == 250.0
    reader.refresh(NOW + usage.REFRESH)
    assert tool.calls == 2 and reader("codex", START).used == 300.0


@pytest.mark.parametrize("failure", [OSError("no such program"), usage.UsageUnavailable("it stopped"), None, {}])
def test_a_read_that_fails_keeps_the_last_figure_and_says_why(failure):
    tool = Tool(_limits(25), failure)
    reader = AccountReader((WINDOW,), ask={"codex": tool})
    reader.refresh(NOW)
    reader.refresh(NOW + usage.REFRESH)
    assert reader("codex", START).used == 250.0 and "model calls" in reader.said["codex"]


def test_a_figure_kept_through_a_failure_rises_with_what_is_charged_meanwhile():
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(25), usage.UsageUnavailable("down"))})
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    reader.refresh(NOW)
    assert meter.reading("codex", NOW).used == 250.0
    meter.settle(meter.reserve("codex", NOW, 30), 30)
    later = NOW + usage.REFRESH
    reader.refresh(later)
    assert meter.reading("codex", later).used == 280.0


def test_a_figure_kept_through_a_failure_goes_when_its_own_reset_passes():
    reset = NOW + usage.REFRESH + timedelta(minutes=5)
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(25, resets=int(reset.timestamp())),
                                                         usage.UsageUnavailable("down"))})
    reader.refresh(NOW)
    reader.refresh(NOW + usage.REFRESH)
    assert reader("codex", START).used == 250.0
    reader.refresh(reset + usage.REFRESH)
    assert reader("codex", START) is None and "model calls" in reader.said["codex"]


def test_a_figure_read_with_no_reset_goes_when_the_providers_week_ends():
    week_end = START + timedelta(days=7)
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(25, resets=None), usage.UsageUnavailable("down"))})
    reader.refresh(NOW)
    reader.refresh(week_end - timedelta(minutes=1))
    assert reader("codex", START).used == 250.0
    reader.refresh(week_end + usage.REFRESH)
    assert reader("codex", START) is None


def test_a_reply_with_no_week_in_it_leaves_the_figure_and_adds_its_limit():
    soon = NOW + timedelta(hours=1)
    session = {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": int(soon.timestamp())}
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(55), {"rateLimits": {"primary": session}})})
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    reader.refresh(NOW)
    meter.settle(meter.reserve("codex", NOW, 20), 20)
    later = NOW + usage.REFRESH
    reader.refresh(later)
    got = reader("codex", START)
    assert got.used == 550.0 and got.limited_until == soon and meter.reading("codex", later).used == 570.0


def test_a_reply_that_is_not_a_number_cannot_lift_a_reported_limit():
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(100), _limits(float("nan")))})
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    reader.refresh(NOW)
    assert not meter.reserve("codex", NOW, 1).admitted
    later = NOW + usage.REFRESH
    reader.refresh(later)
    assert not meter.reserve("codex", later, 1).admitted


def _at_the_limit(until: datetime) -> dict:
    return _limits(100, resets=int(until.timestamp()), allowed=False)


@pytest.mark.parametrize("failure", [usage.UsageUnavailable("the transport failed"), OSError("no such program"), {}])
def test_a_read_that_fails_keeps_a_limit_still_ahead(failure):
    reset = START + timedelta(days=7)
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_at_the_limit(reset), failure)})
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    reader.refresh(NOW)
    before = meter.reserve("codex", NOW, 10)
    assert not before.admitted and before.wait_until == reset
    later = NOW + usage.REFRESH
    reader.refresh(later)
    after = meter.reserve("codex", later, 10)
    assert not after.admitted and after.wait_until == reset
    assert "stands until" in reader.said["codex"] and "model calls" not in reader.said["codex"]


def test_a_limit_kept_through_a_failure_lifts_at_the_first_read_past_its_reset():
    reset = NOW + usage.REFRESH + timedelta(minutes=5)
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_at_the_limit(reset), usage.UsageUnavailable("down"))})
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    reader.refresh(NOW)
    reader.refresh(NOW + usage.REFRESH)
    assert reader("codex", START) is not None
    assert not meter.reserve("codex", reset, 10).admitted                # its figure still stands until a read replaces it
    past = reset + usage.REFRESH
    reader.refresh(past)
    assert meter.reserve("codex", past, 10).admitted


def test_a_failed_read_past_the_limits_reset_forgets_it():
    reset = NOW + timedelta(minutes=1)
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_at_the_limit(reset), usage.UsageUnavailable("down"))})
    reader.refresh(NOW)
    reader.refresh(NOW + usage.REFRESH)
    assert reader("codex", START) is None and "model calls" in reader.said["codex"]


def test_a_good_read_lifts_a_limit_kept_through_a_failure():
    reset = START + timedelta(days=7)
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_at_the_limit(reset), usage.UsageUnavailable("down"), _limits(10))})
    reader.refresh(NOW)
    reader.refresh(NOW + usage.REFRESH)
    reader.refresh(NOW + usage.REFRESH * 2)
    assert reader("codex", START).limited_until is None and reader("codex", START).used == 100.0


def test_a_reading_reaches_the_meter_and_the_loop_gives_way():
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(55))})
    reader.refresh(NOW)
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    assert meter.reading("codex", NOW).used == 550.0
    assert not meter.reserve("codex", NOW, 51).admitted                # 550 + 51 is over day three's 600


def test_a_call_settled_since_the_reading_uses_up_the_room_it_left():
    now = START
    window = ProviderWindow("codex", 1000, now)
    reader = AccountReader((window,), ask={"codex": Tool(_limits(19.9, resets=int((now + timedelta(days=7)).timestamp())))})
    reader.refresh(now)
    meter = BudgetMeter((window,), store=OperatorStore(":memory:"), read_account=reader)
    assert meter.reading("codex", now).used == 199
    first = meter.reserve("codex", now, 1)
    assert first.admitted and first.ceiling == 200
    meter.settle(first, 1)
    second = meter.reserve("codex", now + timedelta(seconds=1), 1)
    assert not second.admitted and "ceiling" in second.reason
    assert meter.reading("codex", now).used == 200


def test_an_estimated_allowance_stays_marked_whatever_the_reading_says():
    window = ProviderWindow("codex", 1000, START, estimated=True)
    reader = AccountReader((window,), ask={"codex": Tool(_limits(55))})
    reader.refresh(NOW)
    meter = BudgetMeter((window,), store=OperatorStore(":memory:"), read_account=reader)
    assert meter.reading("codex", NOW).estimated


# Talking to the tool: a stand-in that behaves as ``codex app-server`` was seen to --------------------------------------

SERVER = """
import json, sys
for line in sys.stdin:
    asked = json.loads(line)
    if "id" in asked:
        answer = {"id": asked["id"], "result": {"method": asked["method"]}}
        print(json.dumps(answer), flush=True)
"""
SILENT = "import sys\nsys.stdin.read()\n"
EXITS = "print('not json')\n"


def _program(tmp_path: Path, text: str) -> list[str]:
    path = tmp_path / "server.py"
    path.write_text(text, encoding="utf-8")
    return [sys.executable, "-I", str(path)]


def test_the_question_stays_open_until_the_answer_is_in_and_the_server_then_stops(tmp_path):
    assert read_limits(_program(tmp_path, SERVER), timeout=30) == {"method": "account/rateLimits/read"}


def test_a_server_that_never_answers_is_ended_and_gives_no_reading(tmp_path):
    with pytest.raises(usage.UsageUnavailable):
        read_limits(_program(tmp_path, SILENT), timeout=2)


def test_a_server_that_stops_with_no_answer_gives_no_reading(tmp_path):
    with pytest.raises(usage.UsageUnavailable):
        read_limits(_program(tmp_path, EXITS), timeout=30)


def test_the_tool_is_given_a_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key-the-tool-must-not-see")
    seen = tmp_path / "seen.txt"
    server = f"""
import json, os, sys
open({str(seen)!r}, "w").write(str(sorted(os.environ)))
for line in sys.stdin:
    asked = json.loads(line)
    if "id" in asked:
        print(json.dumps({{"id": asked["id"], "result": {{}}}}), flush=True)
"""
    read_limits(_program(tmp_path, server), timeout=30)
    assert "ANTHROPIC_API_KEY" not in seen.read_text(encoding="utf-8")
