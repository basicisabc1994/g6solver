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

The tool is asked between ticks and never inside one, and at most once per ``REFRESH``: a tick starts no process. Each
reading carries when it was read, so what the loop charges after that goes on top of it until the next one. A reading that
lags the provider's own count leaves the same gap in the other direction, and nothing here can see that.

**A limit met during a tick** can only be read from the ``LLMError`` the request raised, so ``limit_ends`` reads that text.
No caller gives a limit a mark of its own, and ``g6.core.llm`` and ``g6.core.host_models`` are bound, so none can yet.

* The built-in HTTP callers and the Anthropic SDK are read reliably: ``G6_E_LLM_RATE_LIMITED``, with the wait a server asked
  for, ``HTTP 429`` with none, and the SDK's ``RateLimitError``. A caller of a domain's own is read only if it raises
  ``LLMError`` with such words.
* Claude Code (2.1.316) was read from the text of its program, on 2026-11-01, and never seen at a limit. A result with
  ``is_error`` is an ``LLMError``, and the program writes the error fields after its long ``usage`` record, with a few short
  fields behind them: ``api_error_status`` of 429, ``api_error`` of ``usage_limit_reached``, and a ``result`` that says
  "You've hit your limit". So they fit in the 300 characters the caller keeps. That order is no promise: a version that
  writes them earlier loses them. A 429 is also what a passing throttle or an organisation's policy sends, and each of
  those waits as a limit does.
* Codex (0.165.0) was read the same way and never seen at a limit: "You've hit your usage limit", and "exceeded retry limit,
  last status: 429 Too Many Requests". How far from the end of its output those land was not seen.

A limit that does not read as one is a failed case, and the tick runs on as it always has, so three such cycles pause the
loop. The repair is a code of its own from those callers, which waits for an issuance window.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from g6.core import component_process
from g6.core.outside import clean_environment, find_program
from g6.core.process_tree import ProcessTreeError
from g6.self_maintenance.pacing import AccountReading, ProviderWindow

REFRESH = timedelta(minutes=15)
ASK_TIMEOUT = 30.0
WEEK_MINUTES = 7 * 24 * 60
FULL = 100
READ_LIMITS = "account/rateLimits/read"
#: What ``codex app-server`` needs before it answers: the third line is the only question.
_ASKED = ({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "g6-loop", "version": "0"}}},
          {"method": "initialized"},
          {"id": 2, "method": READ_LIMITS, "params": {"excludeResetCreditDetails": True}})
# COPY: Why a provider has no account figure.
NOT_REPORTED = "its tool reports no usage without a model call, so the meter counts model calls"
#: What a request stopped at a limit raises: see the module docstring for where each comes from.
_LIMIT = re.compile(r"G6_E_LLM_RATE_LIMITED|\bHTTP 429\b|\bRateLimitError\b|usage_limit_reached|\"api_error_status\":429"
                    r"|hit your [^\"]{0,40}limit|usage limit reached|rate limit reached|429 Too Many Requests", re.IGNORECASE)
_ASKED_TO_WAIT = re.compile(r"asks for a wait of ([0-9.]+(?:e[+-]?[0-9]+)?) s")


class UsageUnavailable(RuntimeError):
    """The tool gave no reading: it is missing, stopped, took too long, or said something that does not read."""


def read_limits(argv: Sequence[str], *, timeout: float = ASK_TIMEOUT) -> dict[str, Any]:
    """One answer from a program that speaks ``codex app-server``'s protocol. Its input stays open until the answer is in,
    because the server stops at the end of its input, before a read it is waiting on comes back. Its process tree is
    ended either way."""
    into_read, into_write = os.pipe()
    back_read, back_write = os.pipe()
    answers: list[dict[str, Any]] = []

    def talk() -> None:
        try:
            with os.fdopen(into_write, "w", encoding="utf-8") as asking, os.fdopen(back_read, encoding="utf-8",
                                                                                    errors="replace") as told:
                asking.write("".join(json.dumps(line) + "\n" for line in _ASKED))
                asking.flush()
                for line in told:
                    try:
                        found = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(found, dict) and found.get("id") == _ASKED[-1]["id"]:
                        answers.append(found)
                        return
        except OSError:                     # it stopped while being asked: no answer, which is what the caller is told
            return

    talking = threading.Thread(target=talk, daemon=True, name="g6-usage-read")
    talking.start()
    try:
        with tempfile.TemporaryDirectory(prefix="g6-usage-", ignore_cleanup_errors=True) as work, \
                component_process.window(timeout):
            component_process.run_process(list(argv), env=clean_environment(("CODEX_HOME",)), cwd=work, stdin=into_read,
                                          stdout=back_write)
    except (OSError, ProcessTreeError, subprocess.SubprocessError) as exc:   # the window's expiry is an OSError too
        raise UsageUnavailable(f"{type(exc).__name__}: {exc}"[:200]) from None
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
    if not _LIMIT.search(said):
        return None
    asked = _ASKED_TO_WAIT.search(said)
    seconds = float(asked.group(1)) if asked else 0.0
    return now + (timedelta(minutes=min(seconds / 60, WEEK_MINUTES)) if seconds > 0 else REFRESH)


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _time(value: Any) -> datetime | None:
    """``value`` as a moment, or ``None`` when no clock could show it."""
    try:
        return datetime.fromtimestamp(value, UTC) if _number(value) else None
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


def limits_reading(limits: Mapping[str, Any], window: ProviderWindow, now: datetime) -> AccountReading | None:
    """``limits`` as the meter reads them, or ``None`` when they do not read: use in the meter's own unit from the
    week's percentage, and a limit when a window is full or the provider says ordinary use is not allowed. One field that
    makes no sense loses the whole reply, since a reply that wrong says nothing about the rest. A limit whose reset has
    passed is still a limit, so it is looked at again as one with no reset is."""
    snapshot, allowed = limits.get("rateLimits"), limits.get("ordinaryUsageAllowed")
    if not isinstance(snapshot, Mapping) or not isinstance(allowed, bool | None):
        return None
    seen = [w for w in (snapshot.get("primary"), snapshot.get("secondary")) if w is not None]
    if not seen or not all(_reads(w) for w in seen):
        return None
    week = next((w for w in seen if w.get("windowDurationMins") == WEEK_MINUTES), None)
    full = [w for w in seen if w["usedPercent"] >= FULL]
    resets = [at for w in (full or seen) if (at := _time(w.get("resetsAt"))) is not None and at > now]
    until = (max(resets) if resets else now + REFRESH) if full or allowed is False else None
    if week is None:
        return AccountReading(limited_until=until)
    reset = _time(week.get("resetsAt"))
    return AccountReading(used=window.allowance * week["usedPercent"] / FULL, estimated=False, limited_until=until,
                          resets_at=reset if reset is not None and reset > now else window.week_end(now))


def _stands(reading: AccountReading | None, now: datetime) -> bool:
    """Whether what a reading said still holds: its limit is still ahead, or the week its figure is of has not ended."""
    return reading is not None and any(at is not None and at > now for at in (reading.limited_until, reading.resets_at))


class AccountReader:
    """The ``read_account`` of a ``BudgetMeter``. Calling it only hands back what ``refresh`` last read, which the runner
    calls between ticks. A provider whose tool reports nothing has no reading, and ``said`` says why: the meter then stays
    on its own count. A read that fails, or a reply that does not read, says nothing new, so the last good reading stands
    until a good read replaces it or its reset passes, and what is charged meanwhile goes on top. The owner uses this
    account too, so a broken tool must not make the meter read low. This is held in memory, so a restart whose first read
    fails has forgotten it."""

    def __init__(self, windows: Sequence[ProviderWindow],
                 ask: Mapping[str, Callable[[], Mapping[str, Any] | None]] | None = None) -> None:
        self.windows = {w.provider: w for w in windows}
        self.ask = {"codex": codex_limits} if ask is None else ask
        self.readings: dict[str, AccountReading] = {}
        self.said = {provider: NOT_REPORTED for provider in self.windows}
        self._asked: datetime | None = None

    def refresh(self, now: datetime) -> None:
        if self._asked is not None and now - self._asked < REFRESH:
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

    def _no_reading(self, provider: str, now: datetime, why: str) -> None:
        """What the last good reading said stands while it holds. ``read_at`` stays as it was, so the meter goes on adding
        what is charged after it. Past that, the meter is on its own count."""
        known = self.readings.get(provider)
        if not _stands(known, now):
            self.readings.pop(provider, None)
            self.said[provider] = f"{why}, so the meter counts model calls"
        elif known.limited_until is not None and known.limited_until > now:
            self.said[provider] = f"{why}; the limit it last reported stands until {known.limited_until.isoformat()}"
        else:
            self.said[provider] = (f"{why}; the last figure stands until {known.resets_at.isoformat()}, with the model calls "
                                   "charged since on top")

    def __call__(self, provider: str, start: datetime) -> AccountReading | None:
        return self.readings.get(provider)


__all__ = ["AccountReader", "NOT_REPORTED", "REFRESH", "UsageUnavailable", "codex_limits", "limit_ends", "limits_reading",
           "read_limits"]
