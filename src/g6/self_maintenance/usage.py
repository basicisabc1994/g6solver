# SPDX-License-Identifier: Apache-2.0
"""What a provider's own tool reports of the account's week, with no model call (design 6.6).

What each tool reports was checked by running it, on 2026-11-01:

* **Codex (codex-cli 0.165.0) reports it.** ``codex app-server`` answers ``account/rateLimits/read``, which starts no
  thread and calls no model: each limit window's ``usedPercent``, ``windowDurationMins`` and ``resetsAt``, and whether
  ordinary use is allowed. On the account it was tried on there was one window, of 10080 minutes. Codex signs in for
  itself: G6 reads no sign-in file and hands it nothing, not even its environment beyond ``clean_environment``. Codex
  says this protocol may change, so a reply that does not read is no reading, and the meter stays on its own count.
* **Claude Code (2.1.316) does not.** ``claude --help``, ``claude auth --help`` and ``claude doctor --help`` list no usage
  or limit command. Its ``/usage`` belongs to a session, and starting one could not be shown to call no model, so it was
  not tried. For Claude the meter stays on model calls.

A percentage says the same thing whatever the allowance is: use over allowance is what the provider reported. So the
reading is exact as a share of the week, and only the allowance's unit stays an estimate, which the window says.

The tool is asked between ticks, at most once per ``REFRESH`` and once more when a week it reported ends, since what it said
before says nothing of the next. Inside a tick it is asked once, just before a held-out evaluation (``read_now``), and at no
other point. Each reading carries when it was read, so what the loop charges after that goes on top of it until the next one.
A reading that lags the provider's own count leaves the same gap in the other direction, and nothing here can see that.

**A limit met during a tick** can only be read from the ``LLMError`` the request raised, so ``limit_ends`` reads that text.
No caller gives a limit a mark of its own, and ``g6.core.llm`` and ``g6.core.host_models`` are bound, so none can yet. It
reads only what a model cannot write: G6's own code or status at the start of that text, and the fields a host program
writes around a model's words. A server's body comes after the start, and its words never decide this: an HTTP 500 that
names ``RateLimitError`` is a failed case. Neither do a host's words, whatever they say, since a reply can say the same.

* The built-in HTTP callers and the Anthropic SDK are read reliably: ``G6_E_LLM_RATE_LIMITED``, with the wait a server asked
  for, ``HTTP 429`` with none, and the SDK's ``RateLimitError``. A caller of a domain's own is read only if it raises
  ``LLMError`` with such words.
* Claude Code (2.1.316) was read from the text of its program, on 2026-11-01, and never seen at a limit. A result with
  ``is_error`` is an ``LLMError``, and the program writes the error fields after its long ``usage`` record, with a few short
  fields behind them, so they fit in the 300 characters the caller keeps: ``api_error_status`` of 429 and ``api_error`` of
  ``usage_limit_reached``. Those are read as JSON fields, and a model's words sit inside the JSON string ``result``, where
  every quote is escaped, so they cannot read as a field. That order is no promise: a version that writes them earlier
  loses them. A notice with neither field, such as a session limit reported in ``result`` alone, is no limit here. A 429 is
  also what a passing throttle or an organisation's policy sends, and each of those waits as a limit does.
* Codex (0.165.0) prints text. The caller keeps the flattened end of its output with what went to stdout last, so nothing
  there tells its notice from a reply that says the same, and nothing it prints is read. Its limit is the one
  ``account/rateLimits/read`` reports, between ticks.

A limit that does not read as one is a failed case, and the tick runs on as it always has. With every run failing the cycle
has no winner, which is a result and no failure, so it pauses nothing, and the runner's rest outlasts ``REFRESH``, so
Codex's reading holds the next tick. What it costs is the record: the cycle says no candidate won. The account is read just
before a held-out evaluation, so a limit already there leaves the split unspent. A limit that arrives during the evaluation
is not caught by that: it reads as a refusal, and spends one of the split's five on a reason untrue of the candidate. A limit
the reading does not show, such as a passing throttle, goes on like that too. The repair is a code of its own from those
callers, which waits for an issuance window.

**A classifier or a permission stop** is read the same way: ``refused_by`` takes only G6's own code at the start of the text.
``AnthropicCaller`` raises that code. Claude Code writes a refusal's ``stop_reason`` ahead of its long ``usage`` record, past
the 300 characters ``g6.core.host_models`` keeps, so nothing in that error can show one, and that file is bound. So the loop
has any Claude Code caller read the same fields from the whole result as parsed JSON, before it is cut: as a request goes out,
the caller really sending it becomes a ``WatchedClaude`` (``read_stops``), whatever holds it. What that reads goes straight to
the loop (``telling_stops``), so a wrapper that catches failed requests cannot retry a refusal or hide it, with the text that
caller was given, so a wrapper that rewrites a request cannot send what was refused under another. A model's words stay
inside the string ``result``, so they cannot read as a field. This was read from the text of Claude Code 2.1.316 and has not been
seen at a real refusal, and a version that moves those fields is not read. Codex prints text, as above, so a refusal from it
reads as a failed case, and the same request goes out again on a later tick. That too waits for an issuance window.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from g6.core import component_process, host_models
from g6.core.llm import LLMError
from g6.core.outside import clean_environment, find_program
from g6.core.process_tree import ProcessTreeError
from g6.self_maintenance.pacing import REFRESH, AccountReading, ProviderWindow, standing, utc

MODEL_REFUSED = "G6_E_MODEL_REFUSED"
#: Claude Code's ``api_error`` values that say something other than the model's own reply stopped the turn.
_STOPPED_BY = ("dlp_request_denied", "safety_monitor_blocked")
_Tell = Callable[[str, tuple[str, ...]], None]
_TELL: contextvars.ContextVar[_Tell | None] = contextvars.ContextVar("g6_loop_stops", default=None)
ASK_TIMEOUT = 30.0
WEEK_MINUTES = 7 * 24 * 60
WEEK = timedelta(minutes=WEEK_MINUTES)
FULL = 100
READ_LIMITS = "account/rateLimits/read"
#: What ``codex app-server`` needs before it answers: the third line is the only question.
_ASKED = ({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "g6-loop", "version": "0"}}},
          {"method": "initialized"},
          {"id": 2, "method": READ_LIMITS, "params": {"excludeResetCreditDetails": True}})
# COPY: Why a provider has no account figure.
NOT_REPORTED = "its tool reports no usage without a model call, so the meter counts model calls"
#: The start of what a request raises at a limit, which is G6's own words. A server's body comes after them. Of what a host
#: program prints, only the two JSON fields of a Claude Code error result: see the module docstring for why. The search for
#: them ends at the next caller's failure, whose text a model may have written.
_LIMIT = re.compile(r"(?:every caller failed: \S+: )?"
                    r"(?:G6_E_LLM_RATE_LIMITED\b(?:[^;]*? asks for a wait of (?P<seconds>[0-9.]+(?:e[+-]?[0-9]+)?) s)?"
                    r"|HTTP 429\b|Anthropic request failed: RateLimitError\b"
                    r"|G6_E_LLM_HOST_FAILED: claude exited -?\d+ and gave an error result: "
                    r"[^;]*\"(?:api_error_status\": ?429\b|api_error\": ?\"usage_limit_reached\"))")


class UsageUnavailable(RuntimeError):
    """The tool gave no reading: it is missing, stopped, took too long, or said something that does not read."""


def _ran(argv: Sequence[str], *, stdin: int, stdout: int, timeout: float) -> None:
    """Run ``argv`` contained, in a folder of its own and with a clean environment, and read how it ended. This is the one
    place a process result is read here: a clean exit, status 0, is the only success. Any other status, of any value or
    kind, a window that ran out or a tree that could not be ended is ``UsageUnavailable``: no reading, never a figure,
    whatever the process printed before it ended."""
    try:
        with tempfile.TemporaryDirectory(prefix="g6-usage-", ignore_cleanup_errors=True) as work, \
                component_process.window(timeout):
            status = component_process.run_process(list(argv), env=clean_environment(("CODEX_HOME",)), cwd=work,
                                                   stdin=stdin, stdout=stdout)
    except (OSError, ProcessTreeError, subprocess.SubprocessError) as exc:   # the window's expiry is an OSError too
        raise UsageUnavailable(f"{type(exc).__name__}: {exc}"[:200]) from None
    if type(status) is not int or status != 0:
        raise UsageUnavailable(f"the tool ended with status {status!r}, so what it printed is no reading")


def read_limits(argv: Sequence[str], *, timeout: float = ASK_TIMEOUT) -> dict[str, Any]:
    """One answer from a program that speaks ``codex app-server``'s protocol, from a run that ended cleanly (``_ran``). Its
    input stays open until the answer is in, because the server stops at the end of its input, before a read it is waiting
    on comes back. Then its input ends, and what it still prints is read and left, so it can stop as it chooses rather
    than on a pipe closed under it. Its process tree is ended either way."""
    into_read, into_write = os.pipe()
    back_read, back_write = os.pipe()
    answers: list[dict[str, Any]] = []

    def talk() -> None:
        try:
            with os.fdopen(back_read, encoding="utf-8", errors="replace") as told:
                with os.fdopen(into_write, "w", encoding="utf-8") as asking:
                    asking.write("".join(json.dumps(line) + "\n" for line in _ASKED))
                    asking.flush()
                    for line in told:
                        try:
                            found = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(found, dict) and found.get("id") == _ASKED[-1]["id"]:
                            answers.append(found)
                            break
                for _ in told:
                    pass
        except OSError:                     # it stopped while being asked: no answer, which is what the caller is told
            return

    talking = threading.Thread(target=talk, daemon=True, name="g6-usage-read")
    talking.start()
    try:
        _ran(argv, stdin=into_read, stdout=back_write, timeout=timeout)
    finally:
        os.close(into_read)
        os.close(back_write)
        talking.join(5)
    if not answers or not isinstance(answers[0].get("result"), dict):
        raise UsageUnavailable("the tool gave no answer to the read of its limits")
    return answers[0]["result"]


def codex_limits() -> dict[str, Any]:
    """What Codex reports of the account. Its own program, wherever ``G6_CODEX`` or PATH says, never the current folder's."""
    where = find_program(os.environ.get("G6_CODEX", "").strip() or "codex")
    if not where:
        raise UsageUnavailable("no codex program")
    return read_limits([where, "app-server", "--listen", "stdio://"])


def limit_ends(said: str, now: datetime) -> datetime | None:
    """When the limit a failed request ran into ends, from the text it raised, or ``None`` when that text is no limit. A
    wait a server asked for is taken as asked. Anything else says no time G6 can read, so the limit is looked at again, as
    one with no reset is, and that look costs one refused request."""
    now = utc(now)
    limit = _LIMIT.match(said)
    if limit is None:
        return None
    seconds = float(limit["seconds"] or 0)
    return now + (timedelta(minutes=min(seconds / 60, WEEK_MINUTES)) if seconds > 0 else REFRESH)


def refused_by(said: str) -> str:
    """What stopped a failed request, when a classifier or a permission did, or ``""``. It reads only G6's own code at the
    start of the text: anything a server or a program said comes after that."""
    return said[:300] if said.startswith(f"{MODEL_REFUSED}:") else ""


def _stop_in(result: Mapping[str, Any]) -> str:
    """What Claude Code's own result says stopped the turn, or ``""``."""
    if result.get("stop_reason") == "refusal":
        return "refusal"
    if result.get("api_error") in _STOPPED_BY:
        return str(result["api_error"])
    denied = result.get("permission_denials")
    return f"permission denied ({len(denied)})" if isinstance(denied, list) and denied else ""


@contextmanager
def telling_stops(tell: _Tell) -> Iterator[None]:
    """Inside, a stop ``WatchedClaude`` reads goes to ``tell`` before anything else sees it, with what the bound caller says it
    was given: its system text and request first, or for a session of its own, such as ``ToolSession``, a prompt alone. What
    ``tell`` raises is what stops the request, so a wrapper between it and the loop cannot take the refusal for a failed request
    and send it again."""
    token = _TELL.set(tell)
    try:
        yield
    finally:
        _TELL.reset(token)


class WatchedClaude(host_models.ClaudeCodeCaller):
    """Claude Code as the loop calls it: as the bound caller, and a stop in its result raises G6's own code."""

    def _reply(self, code: int, printed: str, errors: str, files: Any, hide: tuple[str, ...]) -> str:
        stopped = _stop_in(host_models._result(printed) or {})
        if stopped:
            tell = _TELL.get()
            if tell is not None:
                tell(f"{MODEL_REFUSED}: {stopped}", hide)
            raise LLMError(f"{MODEL_REFUSED}: {stopped}")
        return super()._reply(code, printed, errors, files, hide)


def read_stops(sender: Any) -> None:
    """Have ``sender``, when it is any kind of Claude Code caller, read its whole result. It is done to the caller that is
    sending, as it sends, so what holds it does not matter. Its class changes and nothing else does: a subclass of a domain's
    own keeps all it has, and reads the result first."""
    kind = type(sender)
    if isinstance(sender, host_models.ClaudeCodeCaller) and not isinstance(sender, WatchedClaude):
        sender.__class__ = type(kind.__name__, (WatchedClaude, kind), {})


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _time(value: Any) -> datetime | None:
    """``value`` as a moment, or ``None`` when no clock could show it."""
    try:
        return utc(datetime.fromtimestamp(value, UTC)) if _number(value) else None
    except (OverflowError, OSError, ValueError):
        return None


def _reads(window: Any) -> bool:
    """Whether a window's figures can be believed. Written so that a NaN fails, as it fails every comparison. The tool may
    leave out how long a window is and when it resets, and it may not send nonsense for either."""
    if not isinstance(window, Mapping):
        return False
    percent, minutes, reset = window.get("usedPercent"), window.get("windowDurationMins"), window.get("resetsAt")
    return (_number(percent) and 0 <= percent <= FULL and (minutes is None or _number(minutes) and minutes > 0)
            and (reset is None or _time(reset) is not None))


def _reset(window: Mapping[str, Any], now: datetime) -> datetime | None:
    """When a window resets: ahead of ``now`` and no further than the window is long, or a week where it does not say. Any
    other figure is no reset: a limit that outlasts its own window cannot be believed."""
    now = utc(now)
    at = _time(window.get("resetsAt"))
    longest = (window.get("windowDurationMins") or WEEK_MINUTES) * 60
    return at if at is not None and 0 < (at - now).total_seconds() <= longest else None


def limits_reading(limits: Mapping[str, Any], window: ProviderWindow, now: datetime) -> AccountReading | None:
    """``limits`` as the meter reads them, or ``None`` when they do not read: use in the meter's own unit from the
    week's percentage, and a limit when a window is full or the provider says ordinary use is not allowed. One field that
    makes no sense loses the whole reply, since a reply that wrong says nothing about the rest. A limit whose reset has
    passed is still a limit, so it is looked at again as one with no reset is. A week whose own reset has passed is another
    matter: a tool that has not caught up is saying last week's use, so that figure is left out and nothing stands for it.
    That reset is kept apart as ``ended_at``: it says where the provider's weeks fall, and it ends no tick."""
    now = utc(now)
    snapshot, allowed = limits.get("rateLimits"), limits.get("ordinaryUsageAllowed")
    if not isinstance(snapshot, Mapping) or not isinstance(allowed, bool | None):
        return None
    seen = [w for w in (snapshot.get("primary"), snapshot.get("secondary")) if w is not None]
    if not seen or not all(_reads(w) for w in seen):
        return None
    week = next((w for w in seen if w.get("windowDurationMins") == WEEK_MINUTES), None)
    full = [w for w in seen if w["usedPercent"] >= FULL]
    resets = [at for w in (full or seen) if (at := _reset(w, now)) is not None]
    until = (max(resets) if resets else now + REFRESH) if full or allowed is False else None
    ended = week is not None and (_time(week.get("resetsAt")) or now + WEEK) <= now
    if ended:
        return AccountReading(limited_until=until, ended_at=_time(week.get("resetsAt")))
    if week is None:
        return AccountReading(limited_until=until)
    return AccountReading(used=window.allowance * week["usedPercent"] / FULL, estimated=False, limited_until=until,
                          resets_at=_reset(week, now) or window.week_end(now))


def _stands(reading: AccountReading | None, now: datetime) -> bool:
    """Whether what a reading said still holds: its limit is still ahead, or the week its figure is of has not ended."""
    now = utc(now)
    return reading is not None and any(at is not None and at > now for at in (reading.limited_until, reading.resets_at))


class AccountReader:
    """The ``read_account`` of a ``BudgetMeter``. Calling it only hands back what ``refresh`` last read, which the runner
    calls between ticks, as it stands when asked: once the week its figure is of has reset, that figure is last week's and
    is not handed back until the tool is read again (``standing``). A provider whose tool reports nothing has no reading, and
    ``said`` says why: the meter then stays on its own count. A read that fails, a process that does not end cleanly
    (``_ran``) among them, or a reply that does not read, says nothing new, so the last good reading stands until a good
    read replaces it or its reset passes, and what is charged meanwhile goes on top. Past that the account is not known,
    and nothing is admitted on it until a read succeeds: the owner uses this account too, so a broken tool must not make
    the meter read low. This is held in memory, so a restart whose first read fails knows nothing, and admits nothing."""

    def __init__(self, windows: Sequence[ProviderWindow],
                 ask: Mapping[str, Callable[[], Mapping[str, Any] | None]] | None = None) -> None:
        self.windows = {w.provider: w for w in windows}
        self.ask = {"codex": codex_limits} if ask is None else ask
        self.readings: dict[str, AccountReading] = {}
        self.said = {provider: NOT_REPORTED for provider in self.windows}
        self._asked: datetime | None = None

    def _reset_since(self, now: datetime) -> bool:
        """Whether a week has ended since the tool was last asked, so what it said then says nothing of this one."""
        now = utc(now)
        return any(r.resets_at is not None and self._asked < r.resets_at <= now for r in self.readings.values())

    def refresh(self, now: datetime) -> None:
        now = utc(now)
        if self._asked is not None and now - self._asked < REFRESH and not self._reset_since(now):
            return
        self._asked = now
        for provider in set(self.windows) & set(self.ask):
            try:
                found = limits_reading(self.ask[provider]() or {}, self.windows[provider], now)
            except (OSError, UsageUnavailable) as exc:
                self._no_reading(provider, now, f"its usage could not be read ({str(exc)[:120]})")
                continue
            if found is None:
                self._no_reading(provider, now, "its reply could not be read")
                continue
            known = self.readings.get(provider)
            if found.estimated and known is not None and not known.estimated and _stands(known, now):
                limits = (at for at in (known.limited_until, found.limited_until) if at is not None)   # no week in it
                self.readings[provider] = replace(known, limited_until=max(limits, default=None))
                self._no_reading(provider, now, "its reply gave no week")
                continue
            self.readings[provider] = replace(found, read_at=now)
            self.said[provider] = f"reported by its own tool at {now.isoformat(timespec='minutes')}"
            if found.resets_at is not None:      # so a reply with no reset falls back on the week the tool last reported
                self.windows[provider] = self.windows[provider].aligned(found.resets_at)
                self.said[provider] += f"; its week ends at {found.resets_at.isoformat(timespec='minutes')}"

    def read_now(self, now: datetime) -> None:
        """Ask the tool whenever it was last asked: the one read a tick makes, just before a held-out evaluation."""
        now = utc(now)
        self._asked = None
        self.refresh(now)

    def _no_reading(self, provider: str, now: datetime, why: str) -> None:
        """No reading never replaces the last good one. What it said stands while it holds (``_stands``, and ``standing``
        for its figure), and ``read_at`` stays as it was, so the meter goes on adding what is charged after it. Past that,
        nothing known of the account is left: the reading says why (``unread``), and the meter admits nothing on it until a
        read succeeds, as the ledger alone holds only G6's own use and would read low."""
        now = utc(now)
        known = self.readings.get(provider)
        if not _stands(known, now):
            self.readings[provider] = AccountReading(unread=why)
            self.said[provider] = (f"{why}, and nothing it reported still stands, so nothing is reserved on it until it "
                                   "reads again")
        elif known.limited_until is not None and known.limited_until > now:
            self.said[provider] = f"{why}; the limit it last reported stands until {known.limited_until.isoformat()}"
        else:
            self.said[provider] = (f"{why}; the last figure stands until {known.resets_at.isoformat()}, with the model calls "
                                   "charged since on top")

    def __call__(self, provider: str, now: datetime) -> AccountReading | None:
        """What was last read of ``provider``, as it stands at ``now``: past its reset, without its figure (``standing``)."""
        now = utc(now)
        return standing(self.readings.get(provider), now)


__all__ = ["AccountReader", "MODEL_REFUSED", "NOT_REPORTED", "REFRESH", "UsageUnavailable", "WatchedClaude", "codex_limits",
           "limit_ends", "limits_reading", "read_limits", "read_stops", "refused_by", "telling_stops"]
