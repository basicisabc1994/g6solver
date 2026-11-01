# SPDX-License-Identifier: Apache-2.0
"""What a provider's own tool reports of the account's week: read between ticks, with no model call and no real tool."""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from g6.core import host_models, llm
from g6.core.llm import LLMError
from g6.operator.store import OperatorStore
from g6.self_maintenance import usage
from g6.self_maintenance.pacing import AccountReading, BudgetMeter, ProviderWindow
from g6.self_maintenance.usage import AccountReader, WatchedClaude, limit_ends, limits_reading, read_limits, refused_by

START = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
NOW = START + timedelta(days=2)
WINDOW = ProviderWindow("codex", 1000, START)
RESET = int((START + timedelta(days=7)).timestamp())


def _limits(percent: int, *, minutes=10080, resets=RESET, allowed=True, secondary=None) -> dict:
    return {"ordinaryUsageAllowed": allowed, "rateLimits": {
        "primary": {"usedPercent": percent, "windowDurationMins": minutes, "resetsAt": resets}, "secondary": secondary}}


# A limit read from what a failed request raised ------------------------------------------------------------------------

def _claude_printed(**result) -> str:
    """Claude Code's JSON result in the key order of 2.1.316's own source: what it reports of the run first, and the result
    with its error fields last. Read from the program's text: no limit was ever reached to see this."""
    return json.dumps({"duration_api_ms": 0, "stop_reason": "stop_sequence", "session_id": "0" * 36, "total_cost_usd": 0,
                       "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                                 "output_tokens": 0, "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
                                 "service_tier": None, "cache_creation": {"ephemeral_1h_input_tokens": 0,
                                                                          "ephemeral_5m_input_tokens": 0}},
                       "modelUsage": {}, "permission_denials": [], "is_error": True, "num_turns": 1, "subtype": "success",
                       **result, "type": "result", "duration_ms": 812, "uuid": "1" * 36}, separators=(",", ":"))


def _raised(call) -> str:
    with pytest.raises(LLMError) as raised:
        call()
    return str(raised.value)


def _http(monkeypatch, status: int, headers: dict, body: bytes = b"refused") -> str:
    class Refuses:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, status, "no", headers, io.BytesIO(body))

    monkeypatch.setattr(llm.urllib.request, "build_opener", lambda *args: Refuses())
    monkeypatch.setattr(llm, "_sleep", lambda seconds: None)
    caller = llm.OpenAICompatibleCaller("test", base_url="https://unused.invalid/v1", api_key=None)
    return _raised(lambda: caller.complete("system", "user"))


def _claude(printed: str, code: int = 1) -> str:
    return _raised(lambda: host_models.ClaudeCodeCaller()._reply(code, printed, "", Path("."), ("system", "user")))


def _codex(errors: str) -> str:
    return _raised(lambda: host_models.CodexCaller()._reply(1, "", errors, Path("."), ("system", "user")))


def test_a_refusal_that_says_how_long_to_wait_is_a_limit_that_long(monkeypatch):
    said = _http(monkeypatch, 429, {"Retry-After": "120"})
    assert llm.RATE_LIMITED in said and limit_ends(said, NOW) == NOW + timedelta(seconds=120)


def test_a_wait_too_long_to_write_as_a_time_is_held_to_a_week(monkeypatch):
    assert limit_ends(_http(monkeypatch, 429, {"Retry-After": "1e40"}), NOW) == NOW + timedelta(days=7)


def test_a_refusal_with_no_hint_is_a_limit_looked_at_again_as_one_with_no_reset(monkeypatch):
    assert limit_ends(_http(monkeypatch, 429, {}), NOW) == NOW + usage.REFRESH


def test_an_anthropic_rate_limit_is_read_from_the_sdks_own_error_name():
    class RateLimitError(Exception):
        pass

    class Refuses:
        class beta:
            class messages:
                @staticmethod
                def create(**kwargs):
                    raise RateLimitError("429 rate_limit_error")

    said = _raised(lambda: llm.AnthropicCaller(client=Refuses()).complete("system", "user"))
    assert limit_ends(said, NOW) == NOW + usage.REFRESH


@pytest.mark.parametrize("fields", [{"api_error_status": 429, "api_error": "usage_limit_reached", "result": "You've hit your limit"},
                                    {"api_error_status": 429, "result": "API Error: rate limited"},
                                    {"api_error": "usage_limit_reached", "result": "You've hit your session limit"}])
def test_a_claude_code_limit_is_read_from_the_fields_it_wrote_at_the_end_of_what_it_printed(fields):
    said = _claude(_claude_printed(**fields))
    assert host_models.HOST_FAILED in said and limit_ends(said, NOW) == NOW + usage.REFRESH


MODEL_PROSE = ("You've hit your usage limit", "429 Too Many Requests", "usage_limit_reached", "Rate limit reached",
               '"api_error_status":429', '"api_error":"usage_limit_reached"', "ERROR: You've hit your usage limit")


@pytest.mark.parametrize("prose", MODEL_PROSE)
def test_what_a_model_wrote_in_a_claude_error_result_never_makes_a_limit(prose):
    assert limit_ends(_claude(_claude_printed(result=f"The reply was: {prose}")), NOW) is None


def test_a_prose_notice_with_no_status_from_claude_code_is_no_limit():
    assert limit_ends(_claude(_claude_printed(api_error_status=None, result="You've hit your session limit")), NOW) is None


@pytest.mark.parametrize("prose", MODEL_PROSE)
def test_a_claude_result_that_is_no_error_never_makes_a_limit(prose):
    printed = json.dumps({"type": "result", "is_error": False, "result": prose})
    said = _raised(lambda: host_models.ClaudeCodeCaller()._reply(1, printed, "cannot persist the session", Path("."), ()))
    assert limit_ends(said, NOW) is None


@pytest.mark.parametrize("prose", MODEL_PROSE)
def test_a_codex_run_that_exited_well_never_makes_a_limit(prose):
    said = _raised(lambda: host_models.CodexCaller()._reply(0, f"assistant: {prose}", "", Path("."), ()))
    assert limit_ends(said, NOW) is None


@pytest.mark.parametrize("prose", MODEL_PROSE)
def test_model_prose_on_a_failed_codex_run_never_makes_a_limit(prose):
    said = _raised(lambda: host_models.CodexCaller()._reply(1, prose, "", Path("."), ()))
    assert limit_ends(said, NOW) is None and limit_ends(_codex(prose), NOW) is None


@pytest.mark.parametrize("prose", MODEL_PROSE)
def test_a_claude_failure_ahead_of_a_codex_failure_lends_its_prefix_to_nothing_the_model_wrote(prose):
    claude = _claude(_claude_printed(result="an ordinary error"))
    assert limit_ends(f"every caller failed: first: {claude}; second: {_codex(prose)}", NOW) is None


def test_a_claude_code_limit_printed_early_is_lost_with_the_text_the_caller_cuts_and_reads_as_a_failure():
    """The limit of this: the caller keeps the last 300 characters, and its file is bound. A later Claude Code that prints
    the error ahead of a long usage record reads as failed cases."""
    printed = json.dumps({"type": "result", "is_error": True, "api_error_status": 429, "result": "You've hit your limit",
                          "usage": {f"counter_{i}": 0 for i in range(40)}, "uuid": "1" * 36})
    assert limit_ends(_claude(printed), NOW) is None


@pytest.mark.parametrize("errors", ["ERROR: You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro).",
                                    "ERROR: You've hit your usage limit. Try again later.",
                                    "ERROR: exceeded retry limit, last status: 429 Too Many Requests",
                                    "ERROR: Rate limit reached for gpt-5.5. Please try again in 11.054s."])
def test_a_codex_limit_is_never_read_from_what_it_printed(errors):
    """Codex prints text, and the caller keeps its flattened end with stdout last, so nothing tells its notice from a reply
    that says the same. Its limit is the one its usage reading reports, between ticks: until then it is a failed case."""
    assert limit_ends(_codex(errors), NOW) is None


def test_a_codex_limit_the_reading_reports_holds_the_next_tick():
    reader = AccountReader((WINDOW,), ask={"codex": lambda: _limits(100)})
    reader.refresh(NOW)
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    assert meter.reserve("codex", NOW, 1).wait_until == datetime.fromtimestamp(RESET, UTC)


def test_a_fallback_of_one_backend_that_met_a_limit_reads_as_one():
    said = "every caller failed: OllamaCaller(gpt-oss:20b): G6_E_LLM_RATE_LIMITED: HTTP 429 from u asks for a wait of 90 s"
    assert limit_ends(said, NOW) == NOW + timedelta(seconds=90)


@pytest.mark.parametrize("body", [b"ImportError: cannot import name 'RateLimitError' from 'openai'", b"You've hit your limit",
                                  b"usage_limit_reached", b'{"api_error_status":429}', b"HTTP 429", b"429 Too Many Requests",
                                  b"Rate limit reached for gpt-5.5", b"G6_E_LLM_RATE_LIMITED", b"asks for a wait of 90 s"])
@pytest.mark.parametrize("status", [400, 500])
def test_what_a_servers_body_says_never_makes_a_limit(monkeypatch, status, body):
    assert limit_ends(_http(monkeypatch, status, {}, body), NOW) is None


def test_a_wait_is_taken_from_gs_own_words_and_not_from_a_servers_body(monkeypatch):
    said = _http(monkeypatch, 429, {"Retry-After": "120"}, b"asks for a wait of 9 s")
    assert limit_ends(said, NOW) == NOW + timedelta(seconds=120)
    assert limit_ends(_http(monkeypatch, 429, {}, b"asks for a wait of 9 s"), NOW) == NOW + usage.REFRESH


def test_an_anthropic_error_is_read_by_its_own_class_and_not_by_what_its_message_says():
    class APIStatusError(Exception):
        pass

    class Fails:
        class beta:
            class messages:
                @staticmethod
                def create(**kwargs):
                    raise APIStatusError("500 RateLimitError: rate limit reached")

    assert limit_ends(_raised(lambda: llm.AnthropicCaller(client=Fails()).complete("system", "user")), NOW) is None


@pytest.mark.parametrize("said", ["G6_E_LLM_TIMEOUT: the reply was still arriving", "HTTP 500 from https://model: broken",
                                  "HTTP 503 from https://model: overloaded", "empty completion on 4 attempts (last max_tokens=8192)",
                                  "G6_E_LLM_HOST_FAILED: claude exited 1 and printed no result: not logged in",
                                  "G6_E_LLM_HOST_FAILED: codex exited 2: unexpected argument", "G6_E_MODEL_REFUSED: refusal",
                                  "G6_E_LOOP_TICK_BUDGET_SPENT: this tick has sent 5 of the 5 it reserved", ""])
def test_any_other_failure_is_no_limit(said):
    assert limit_ends(said, NOW) is None


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


FAR_OFF = int(datetime(2050, 1, 1, tzinfo=UTC).timestamp())


def test_a_reset_past_the_end_of_its_own_window_is_no_reset():
    got = limits_reading(_limits(100, resets=FAR_OFF), WINDOW, NOW)
    assert got.limited_until == NOW + usage.REFRESH and got.resets_at == WINDOW.week_end(NOW)


def test_a_reset_with_no_stated_window_is_held_to_a_week():
    assert limits_reading(_limits(100, minutes=None, resets=FAR_OFF), WINDOW, NOW).limited_until == NOW + usage.REFRESH


def test_a_limit_with_a_reset_past_its_window_holds_no_folder_for_decades():
    reader = AccountReader((WINDOW,), ask={"codex": lambda: _limits(100, resets=FAR_OFF)})
    reader.refresh(NOW)
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    assert meter.reserve("codex", NOW, 1).wait_until == NOW + usage.REFRESH


def test_a_figure_with_a_reset_past_its_window_stands_no_longer_than_the_week():
    reader = AccountReader((WINDOW,), ask={"codex": lambda: _limits(40, resets=FAR_OFF)})
    reader.refresh(NOW)
    assert reader.readings["codex"].resets_at == WINDOW.week_end(NOW)


def test_a_reset_as_long_as_its_window_is_taken():
    at = int((NOW + timedelta(minutes=300)).timestamp())
    full = {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": at}
    assert limits_reading({"rateLimits": {"primary": full, "secondary": None}}, WINDOW, NOW).limited_until == datetime.fromtimestamp(at, UTC)


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


def test_a_weekly_figure_whose_own_reset_has_passed_is_of_a_week_that_ended():
    """A tool that has not caught up with its own reset says last week's use. Its own word on when that ended stands."""
    gone = int((NOW - timedelta(seconds=1)).timestamp())
    got = limits_reading(_limits(90, resets=gone), WINDOW, NOW)
    assert got.used == 0 and got.estimated is True and got.resets_at is None and got.limited_until is None


def test_a_figure_with_a_reset_on_the_moment_it_is_read_has_ended_too():
    assert limits_reading(_limits(90, resets=int(NOW.timestamp())), WINDOW, NOW).used == 0


def test_a_full_week_whose_reset_has_passed_is_looked_at_again_and_carries_no_figure():
    gone = int((NOW - timedelta(hours=1)).timestamp())
    got = limits_reading(_limits(100, resets=gone), WINDOW, NOW)
    assert got.used == 0 and got.limited_until == NOW + usage.REFRESH


def test_a_stale_reply_after_a_reset_does_not_carry_last_weeks_use_into_the_new_one():
    reset = NOW + timedelta(minutes=1)
    stale = _limits(90, resets=int(reset.timestamp()))
    reader = AccountReader((WINDOW,), ask={"codex": Tool(stale)})
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    reader.refresh(NOW)
    assert meter.reading("codex", NOW).used == 900
    after = reset + timedelta(seconds=1)
    reader.refresh(after)
    assert meter.reading("codex", after).used == 0


def test_a_reset_past_the_end_of_its_window_still_keeps_the_figure():
    """That reset is nonsense, which says nothing of the figure. A reset that has passed says the figure is over."""
    assert limits_reading(_limits(40, resets=FAR_OFF), WINDOW, NOW).used == 400


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


def test_reading_now_asks_however_lately_the_tool_was_asked():
    tool = Tool(_limits(25), _limits(30))
    reader = AccountReader((WINDOW,), ask={"codex": tool})
    reader.refresh(NOW)
    reader.read_now(NOW + timedelta(seconds=1))
    assert tool.calls == 2 and reader("codex", START).used == 300.0


OFF_THE_GRID = int((START + timedelta(days=3, hours=5)).timestamp())


def test_the_week_a_tool_reports_reaches_the_meter_and_is_said():
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(25, resets=OFF_THE_GRID))})
    reader.refresh(NOW)
    meter = BudgetMeter((WINDOW,), store=OperatorStore(":memory:"), read_account=reader)
    assert meter.reading("codex", NOW + timedelta(days=1, hours=4)).day == 7
    assert "its week ends at 2026-10-29T13:00" in reader.said["codex"]


def test_a_reply_with_no_reset_stays_on_the_week_the_tool_last_reported():
    """A figure with no reset of its own falls back on the window's week, so that week must be the one the tool reported."""
    reader = AccountReader((WINDOW,), ask={"codex": Tool(_limits(25, resets=OFF_THE_GRID), _limits(30, resets=None))})
    reader.refresh(NOW)
    reader.refresh(NOW + usage.REFRESH)
    assert reader("codex", START).resets_at == datetime.fromtimestamp(OFF_THE_GRID, UTC)


def test_a_reset_passing_is_read_at_once_and_not_up_to_a_refresh_later():
    reset = NOW + timedelta(minutes=1)
    tool = Tool(_limits(90, resets=int(reset.timestamp())), _limits(0))
    reader = AccountReader((WINDOW,), ask={"codex": tool})
    reader.refresh(NOW)
    reader.refresh(reset - timedelta(seconds=1))
    assert tool.calls == 1
    reader.refresh(reset + timedelta(seconds=1))
    assert tool.calls == 2 and reader("codex", START).used == 0


def test_a_reset_gets_one_read_however_many_ticks_look_after_it():
    reset = NOW + timedelta(minutes=1)
    down = usage.UsageUnavailable("down")
    tool = Tool(_at_the_limit(reset), down)
    reader = AccountReader((WINDOW,), ask={"codex": tool})
    reader.refresh(NOW)
    for minutes in (2, 3, 4):
        reader.refresh(NOW + timedelta(minutes=minutes))
    assert tool.calls == 2


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
    assert not meter.reserve("codex", reset - timedelta(seconds=1), 10).admitted    # up to its reset, it stands
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


# A stop a classifier or a permission made, read from what a model cannot write -----------------------------------------------

REFUSAL_NOTICE = ("API Error: Claude can't help with this. Start a new session to continue. Learn more: "
                  "https://www.anthropic.com/legal/aup Request ID: req_0000000000000000000000 Message ID: msg_00000000000000000000")


def test_gs_own_code_for_a_refusal_is_a_stop():
    assert refused_by("G6_E_MODEL_REFUSED: cyber") == "G6_E_MODEL_REFUSED: cyber"


@pytest.mark.parametrize("said", ["HTTP 500 from u: G6_E_MODEL_REFUSED: a server's body", "G6_E_LLM_TIMEOUT: no answer", "",
                                  "G6_E_LLM_HOST_FAILED: codex exited 1: G6_E_MODEL_REFUSED: what it printed",
                                  "G6_E_LLM_RATE_LIMITED: HTTP 429 from u"])
def test_any_other_failure_is_no_such_stop(said):
    assert refused_by(said) == ""


def _watched(printed: str, code: int = 0) -> str:
    return WatchedClaude()._reply(code, printed, "", Path("."), ("system", "user"))


@pytest.mark.parametrize("fields,said", [({"stop_reason": "refusal", "api_error": "invalid_request", "result": REFUSAL_NOTICE}, "refusal"),
                                         ({"api_error": "safety_monitor_blocked", "result": "x"}, "safety_monitor_blocked"),
                                         ({"api_error": "dlp_request_denied", "result": "x"}, "dlp_request_denied"),
                                         ({"permission_denials": [{"tool_name": "Bash"}], "result": "x"}, "permission denied")])
def test_a_stop_claude_code_reports_is_read_from_the_fields_of_its_whole_result(fields, said):
    with pytest.raises(LLMError) as raised:
        _watched(_claude_printed(**fields), code=1)
    assert said in str(raised.value) and refused_by(str(raised.value))


def test_a_refusal_with_no_error_flag_on_it_is_a_stop_all_the_same():
    printed = json.dumps({"type": "result", "is_error": False, "stop_reason": "refusal", "result": ""})
    with pytest.raises(LLMError, match="G6_E_MODEL_REFUSED"):
        _watched(printed)


def test_the_same_refusal_through_the_plain_caller_is_lost_with_the_text_it_cuts():
    """Why a subclass is needed at all. The field is written ahead of the long usage record, and the bound caller keeps only
    the last 300 characters of what was printed, so nothing in the error it raises can say so."""
    said = _claude(_claude_printed(stop_reason="refusal", api_error="invalid_request", result=REFUSAL_NOTICE))
    assert refused_by(said) == "" and "refusal" not in said


@pytest.mark.parametrize("prose", ['"stop_reason":"refusal"', '"permission_denials":[{"tool_name":"Bash"}]',
                                   "safety_monitor_blocked", '"api_error":"safety_monitor_blocked"', "I can't help with that."])
def test_what_a_model_wrote_never_makes_such_a_stop(prose):
    assert _watched(json.dumps({"type": "result", "is_error": False, "stop_reason": "end_turn", "result": prose})) == prose
    with pytest.raises(LLMError) as raised:
        _watched(_claude_printed(result=f"The reply was: {prose}"), code=1)
    assert refused_by(str(raised.value)) == ""


def test_an_ordinary_answer_comes_back_as_it_did():
    printed = json.dumps({"type": "result", "is_error": False, "stop_reason": "end_turn", "permission_denials": [], "result": "4"})
    assert _watched(printed) == "4"


def test_a_codex_refusal_is_not_read_since_it_prints_text():
    """The limit of this: what Codex prints cannot be told from a reply, as with its limits. It waits for an issuance window."""
    assert refused_by(_codex("ERROR: This content may violate our usage policies.")) == ""
