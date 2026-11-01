# SPDX-License-Identifier: Apache-2.0
"""What a provider's own tool reports of the account's week, with no model call (design 6.6).

What each tool reports was checked by running it, on 2026-11-01:

* **Codex (codex-cli 0.165.0) reports it.** ``codex app-server`` answers ``account/rateLimits/read``, which starts no
  thread and calls no model: each limit window's ``usedPercent``, ``windowDurationMins`` and ``resetsAt``, and whether
  ordinary use is allowed. On the account it was tried on there was one window, of 10080 minutes. Codex signs in for
  itself: G6 reads no sign-in file and hands it nothing, not even its environment beyond ``clean_environment``. The
  protocol is marked experimental, so a reply that does not read is no reading, and the meter stays on its own count.
* **Claude Code (2.1.316) does not.** ``claude --help``, ``claude auth --help`` and ``claude doctor --help`` list no usage
  or limit command. Its ``/usage`` belongs to a session, and starting one could not be shown to call no model, so it was
  not tried. For Claude the meter stays on model calls.

A percentage says the same thing whatever the allowance is: use over allowance is what the provider reported. So the
reading is exact as a share of the week, and only the allowance's unit stays an estimate, which the window says.

The tool is asked between ticks and never inside one, and at most once per ``REFRESH``: a tick starts no process.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
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


def limits_reading(limits: Mapping[str, Any], window: ProviderWindow, now: datetime) -> AccountReading | None:
    """``limits`` as the meter reads them, or ``None`` when they do not read: use in the meter's own unit from the
    week's percentage, and a limit when a window is full or the provider says ordinary use is not allowed."""
    snapshot = limits.get("rateLimits")
    if not isinstance(snapshot, Mapping):
        return None
    seen = [w for w in (snapshot.get("primary"), snapshot.get("secondary")) if isinstance(w, Mapping)]
    if not seen or any(isinstance(w.get("usedPercent"), bool) or not isinstance(w.get("usedPercent"), int | float)
                       for w in seen):
        return None
    week = next((w for w in seen if w.get("windowDurationMins") == WEEK_MINUTES), None)
    full = [w for w in seen if w["usedPercent"] >= FULL]
    held = bool(full) or limits.get("ordinaryUsageAllowed") is False
    resets = [w["resetsAt"] for w in (full or seen) if isinstance(w.get("resetsAt"), int)]
    until = (datetime.fromtimestamp(max(resets), UTC) if resets else now + REFRESH) if held else None
    if week is None:
        return AccountReading(limited_until=until)
    return AccountReading(used=window.allowance * week["usedPercent"] / FULL, estimated=False, limited_until=until)


class AccountReader:
    """The ``read_account`` of a ``BudgetMeter``. Calling it only hands back what ``refresh`` last read, which the runner
    calls between ticks. A provider whose tool reports nothing, or whose read failed, has no reading, and ``said`` says
    why: the meter then stays on its own count."""

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
            self.readings.pop(provider, None)
            try:
                found = limits_reading(self.ask[provider]() or {}, self.windows[provider], now)
            except (OSError, UsageUnavailable) as exc:
                self.said[provider] = f"its usage could not be read ({str(exc)[:120]}), so the meter counts model calls"
                continue
            if found is None:
                self.said[provider] = "its reply could not be read, so the meter counts model calls"
                continue
            self.readings[provider] = found
            self.said[provider] = f"reported by its own tool at {now.isoformat(timespec='minutes')}"

    def __call__(self, provider: str, start: datetime) -> AccountReading | None:
        return self.readings.get(provider)


__all__ = ["AccountReader", "NOT_REPORTED", "REFRESH", "UsageUnavailable", "codex_limits", "limits_reading", "read_limits"]
