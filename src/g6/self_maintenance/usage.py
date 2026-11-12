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

**What a provider says stopped a request** (a refusal, a limit of the account, or its model at capacity) is read from the
provider's own structured output and never from text a model could have written, and it reaches the loop as G6's own code at
the start of what the request raised: ``MODEL_REFUSED`` (``refused_by``), ``G6_E_LLM_RATE_LIMITED`` or ``AT_CAPACITY``
(``limit_ends``). A server's body comes after the start, and its words never decide this: an HTTP 500 that names
``RateLimitError`` is a failed case. Neither do a host's printed words, whatever they say, since a reply can say the same.

* The built-in HTTP callers and the Anthropic SDK are read reliably: ``G6_E_LLM_RATE_LIMITED``, with the wait a server asked
  for, ``HTTP 429`` with none, and the SDK's ``RateLimitError``. ``AnthropicCaller`` raises ``MODEL_REFUSED`` for a refusal.
  A caller of a domain's own is read only if it raises ``LLMError`` with such words.
* ``g6.core.host_models`` is bound, and its callers raise no code of their own for any of these: they keep the last 300
  characters of what the program printed, flattened. So the loop has each host caller read its program's own output whole,
  before it is cut: construction chooses a dormant ``WatchedClaude`` or ``WatchedCodex`` cleanup wrapper; ``read_stops``
  enables its observation without changing a live object's class. What that reads goes to the loop (``telling_stops``)
  before anything else
  sees it, so a wrapper that catches failed requests cannot retry it or hide it. For a watched caller only those structured
  fields decide: the older text pattern (``_LIMIT``) is never applied to what it raises, stderr included.
* **What a host request is.** A refused request is known by one identity, ``request_identity``: the digest of the captured
  arguments and input handed to the launcher, taken by the owned completion path after command hooks return, or by the
  final ``_launch`` for a session such as ``ToolSession``. The loop checks it there, against what was refused and against an
  identical request still out,
  under one lock (``telling_stops``' gate), which hands back the send's one record (``Send``), made once it may leave from the
  bytes as they are after the stop reads and waits. That immutable ``Request`` is held on the record; the wrapper hands
  back built-in text and arguments naming G6's private copies. File identity, rather than pathname, keys capture, so aliases
  share a snapshot and a private copy. A reply releases its record; the invocation's finally releases unanswered sends.
  The canonical encoded manifest is kept with the request. The bound launcher still encodes OS arguments/stdin and opens
  files itself: exact wire encoding and custody through that open need the bound seam at an issuance window.
* Claude Code: its JSON result (the bound ``host_models._result``); ``_reply_events`` also checks whole-stream readability.
  A refusal is ``stop_reason`` ``refusal``, an ``api_error`` of
  ``_STOPPED_BY`` or a ``permission_denials`` entry; on an error result, a limit is ``api_error_status`` 429 or ``api_error``
  ``usage_limit_reached``, and capacity is ``api_error_status`` 529 (``overloaded_error``). Read from the text of Claude Code
  2.1.316 and 2.1.318's program, never seen at a real stop. A model's words sit inside the JSON string ``result``, where every
  quote is escaped, so they cannot read as a field. A version that moves those fields is not read. The plain caller's 300
  characters are still read for the two limit fields (``_LIMIT``), which a Claude Code caller the loop does not watch can show.
* Codex: its JSON events (``codex exec --json``, which codex-cli 0.165.0 documents as "Print events to stdout as JSONL"), read
  by ``codex_said``. Only Codex's own events are read: an ``error`` event, and the ``error`` of a ``turn.failed``. A model's
  words are in ``item`` events, inside JSON strings, so they cannot read as either. An explicit failure of a kind G6 does not
  know (``turn.failed``, or an ``error`` of no known kind) is a failed request, whatever the exit status or the last-message
  file say; a known limit or capacity on the way to a turn that completed with no error is an answer. Codex
  names what it reports in ``codex_error_info`` (``_CODEX_KINDS``), and where an event carries none, its own message begins
  with one of the program's fixed texts (``_CODEX_SAYS``). Read from the installed program's text and from Codex's own
  recorded sessions on this machine, where ``server_overloaded`` came with "Selected model is at capacity." and ``cyber_policy``
  with "This content was flagged for possible cybersecurity risk"; no recorded file held a usage limit, and no recorded file
  held ``codex exec --json`` output, so which of the two an exec event carries is not seen, and both are read. What Codex
  prints without ``--json``, as the plain caller runs it, is text, and is never read.

A refusal is never sent again, a limit or capacity ends the tick as a wait, and either spends no held-out evaluation and
scores no case for either arm. A reply that cannot be read whole in the required mode is unknown: its exact request is
barred before release, using the existing refusal store and failed-write stop. A fully read failed run without a refusal
remains a failed case, including a nonzero exit without a terminal event.
The owned launch checks its final observation options, admits its immutable snapshot, and supplies that same record to its
note for idempotent admission. A host note without that module-owned record authorizes no launch. Instance annotations and
reply dispatch never decide observation on this path. Outside every loop tick of this process an owned launch has no loop
gate, and its sharing check (``cloud_consent.admission``) is its one check; while a tick is open, one with no gate in its
context escaped that tick's gate and is refused (``_note_launch``).
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import weakref
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from g6.core import cloud_consent, component_process, host_models
from g6.core.consent_callers import ConsentClaude, ConsentCodex, _Consent, host_admission, session_admission
from g6.core.llm import RATE_LIMITED, AnthropicCaller, LLMError, OpenAICompatibleCaller
from g6.core.outside import clean_environment, find_program
from g6.core.process_tree import ProcessTreeError
from g6.operator.gates import exact_number, kind_of
from g6.self_maintenance.pacing import (
    REFRESH,
    AccountReading,
    ProviderWindow,
    figure_of,
    standing,
    utc,
    windows_by_provider,
)

MODEL_REFUSED = "G6_E_MODEL_REFUSED"
HOST_UNKNOWN = "G6_E_LOOP_HOST_UNKNOWN"
#: A provider's model at capacity: no answer and no limit of the account, so a wait as a limit with no reset is.
AT_CAPACITY = "G6_E_MODEL_AT_CAPACITY"
#: Claude Code's ``api_error`` values that say something other than the model's own reply stopped the turn.
_STOPPED_BY = ("dlp_request_denied", "safety_monitor_blocked")
#: What each ``codex_error_info`` Codex reports says, of those that stop a request for a reason not the candidate's: the names
#: codex-cli 0.165.0's program holds. A permission stop (``too_many_denials``) is a refusal, as Claude Code's is.
_CODEX_KINDS = {"usage_limit_exceeded": "limit", "rate_limit_exceeded": "limit", "server_overloaded": "capacity",
                "flex_unavailable": "capacity", "cyber_policy": "refusal", "bio_policy": "refusal",
                "misalignment_policy_violation": "refusal", "too_many_denials": "refusal"}
#: How Codex's own message begins for each, for an event with no ``codex_error_info``: the program's fixed texts.
_CODEX_SAYS = (("You've hit your usage limit", "limit"), ("You\u2019ve hit your usage limit", "limit"),
               ("Quota exceeded.", "limit"), ("exceeded retry limit, last status: 429", "limit"),
               ("Selected model is at capacity.", "capacity"), ("Flex capacity unavailable.", "capacity"),
               ("This content was flagged for possible", "refusal"), ("This request has been flagged for possible", "refusal"))


class Gate(Protocol):
    """What ``telling_stops`` puts in front of a watched caller: the loop's one check on a send that is about to leave, which
    reads what will leave once it may (``identify``) and hands back that send's record, or raises to keep it in
    (``sending``); where what the provider said of a send goes (``told``); and the release of a send by its outcome
    (``done``)."""

    def sending(self, identify: Callable[[], Request], sender: Any) -> Send: ...

    def told(self, said: str, record: Send | str) -> None: ...

    def done(self, record: Send) -> None: ...


_TELL: contextvars.ContextVar[Gate | None] = contextvars.ContextVar("g6_loop_stops", default=None)
#: Each send's own record and the gate it went through, by the call's own folder, from the step that sends to the reply that
#: reads its outcome: a call nested inside another, or on another thread, has a folder of its own. Only a send's own outcome
#: takes its entry out (``_forget``); nothing else evicts one.
_SENT: dict[tuple[int, int], tuple[Send, Gate | None]] = {}
_SENT_LOCK = threading.Lock()
#: The folders of the sends a watched call made, so the end of that call releases any whose program never answered.
_OPEN: contextvars.ContextVar[list[tuple[int, int]] | None] = contextvars.ContextVar("g6_open_sends", default=None)
_LAUNCH_RECORD: contextvars.ContextVar[Send | None] = contextvars.ContextVar("g6_launch_record", default=None)
#: How many loop ticks of this process are open (``loop_tick``): process-wide, as a launch that escaped a tick's context
#: carries nothing of it.
_TICKS = 0
_TICKS_LOCK = threading.Lock()
# Construction identity survives replacement of an instance dictionary or class. It selects the owned data path,
# never grants admission. Weak references retain neither a caller nor its dynamically constructed class.
_HOSTS: dict[int, tuple[weakref.ReferenceType[Any], str]] = {}
_HOSTS_LOCK = threading.RLock()                  # allocation under this lock can collect a host and re-enter forget
_NATIVE_HOST_COMPLETE = host_models._HostCaller.complete.__code__
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
# COPY: A provider whose tool reports, before it has been read.
NOT_READ = "its usage has not been read yet"
#: The start of what a request raises at a limit or at capacity, which is G6's own words. A server's body comes after them. Of
#: what a host program prints, only the two JSON fields of a Claude Code error result: see the module docstring for why. The
#: search for them ends at the next caller's failure, whose text a model may have written.
_LIMIT = re.compile(r"(?:every caller failed: \S+: )?"
                    r"(?:G6_E_LLM_RATE_LIMITED\b(?:[^;]*? asks for a wait of (?P<seconds>[0-9.]+(?:e[+-]?[0-9]+)?) s)?"
                    r"|HTTP 429\b|Anthropic request failed: RateLimitError\b|" + AT_CAPACITY + r"\b"
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
    one with no reset is, and that look costs one refused request. A model at capacity is such a limit."""
    now = utc(now)
    limit = _LIMIT.match(said)
    if limit is None:
        return None
    seconds = float(limit["seconds"] or 0)
    return now + (timedelta(minutes=min(seconds / 60, WEEK_MINUTES)) if seconds > 0 else REFRESH)


def refused_by(said: str) -> str:
    """A classifier/permission stop, or the owned launch's unknown-outcome hold, or ``""``. Only G6's own leading codes
    decide; server text and model-authored content supply no such authority."""
    return said[:300] if said.startswith((f"{MODEL_REFUSED}:", f"{HOST_UNKNOWN}:")) else ""


def _stop_in(result: Mapping[str, Any]) -> str:
    """What Claude Code's own result says stopped the turn, or ``""``."""
    if result.get("stop_reason") == "refusal":
        return "refusal"
    if result.get("api_error") in _STOPPED_BY:
        return str(result["api_error"])
    denied = result.get("permission_denials")
    return f"permission denied ({len(denied)})" if isinstance(denied, list) and denied else ""


def _status(value: Any, status: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == status


def claude_said(result: Mapping[str, Any]) -> str:
    """What Claude Code's own JSON result says stopped the turn, in G6's own words, or ``""``: a refusal, then on an error
    result a limit or its model at capacity. Only fixed words of G6's are written, never a field's text."""
    stopped = _stop_in(result)
    if stopped:
        return f"{MODEL_REFUSED}: {stopped}"
    if result.get("is_error") is not True:
        return ""
    if _status(result.get("api_error_status"), 429) or result.get("api_error") == "usage_limit_reached":
        return f"{RATE_LIMITED}: Claude Code reported a limit of the account (api_error_status 429 or usage_limit_reached)"
    if _status(result.get("api_error_status"), 529):
        return f"{AT_CAPACITY}: Claude Code reported its model overloaded (api_error_status 529)"
    return ""


def _codex_kind(error: Mapping[str, Any]) -> str:
    """Which stop an error Codex reported is, or ``""``: from its ``codex_error_info`` when it carries one, which is known only
    when it is exactly one of ``_CODEX_KINDS``, whatever else it holds; and only from how its own message begins when it
    carries none."""
    info = error.get("codex_error_info", error.get("codexErrorInfo"))
    if isinstance(info, str):
        return _CODEX_KINDS.get(info, "")
    if isinstance(info, Mapping):
        return _CODEX_KINDS.get(next(iter(info)), "") if len(info) == 1 and isinstance(next(iter(info)), str) else ""
    if info is not None:
        return ""
    message = error.get("message")
    return next((kind for start, kind in _CODEX_SAYS if isinstance(message, str) and message.startswith(start)), "")


#: G6's own words for each stop Codex reports.
_CODEX_SAID = {"refusal": f"{MODEL_REFUSED}: Codex reported its policy stopped the request",
               "limit": f"{RATE_LIMITED}: Codex reported a limit of the account",
               "capacity": f"{AT_CAPACITY}: Codex reported its model at capacity"}


def _reply_events(printed: str) -> tuple[list[dict[str, Any]], bool]:
    """Total JSON/JSONL read, retaining earlier events when any later read fails."""
    events, readable = [], True
    try:
        text = printed.strip()
        if not text:
            return [], True
        try:
            whole = json.loads(text)
        except json.JSONDecodeError as exc:
            # Only multiple JSONL values explain this expected whole-value parse failure.
            if exc.msg != "Extra data" or "\n" not in text:
                readable = False
        except BaseException:
            readable = False
        else:
            return ([whole], True) if isinstance(whole, dict) else ([], False)
        for line in printed.split("\n"):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except BaseException:
                readable = False
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                readable = False
    except BaseException:
        readable = False
    return events, readable


def _read_reply(provider: str, printed: str) -> tuple[list[dict[str, Any]], bool]:
    """Total native-format read, including the bound Claude reader. Foreign outcomes carry no authority."""
    events = []
    try:
        events, readable = _reply_events(printed)
        if provider == "claude":
            # Keep the producer's format reader; event order and whole-stream readability are checked here too.
            try:
                final = host_models._result(printed)
            except BaseException:
                final, readable = None, False
            def foreign(event: Mapping[str, Any]) -> bool:
                kind = event.get("type")
                return kind in ("turn.completed", "turn.failed", "item.completed", "error")
        elif provider == "codex":
            def foreign(event: Mapping[str, Any]) -> bool:
                return event.get("type") == "result"
        else:
            return [], False
        native = []
        results = [event for event in events if event.get("type") == "result"] if provider == "claude" else []
        for event in events:
            if foreign(event):
                readable = False
            elif provider == "claude" and event.get("type") == "result":
                try:
                    result = final if event is results[-1] and final is not None else host_models._result(json.dumps(event))
                    if result is None:
                        readable = False
                    else:
                        native.append(result)
                except BaseException:
                    readable = False
            else:
                native.append(event)
        return native, readable
    except BaseException:
        return [], False


def _event_outcome(provider: str, event: Mapping[str, Any]) -> tuple[str, str]:
    """Every native result supplies a state; an unrecognised status is never an implicit continuation."""
    kind = event.get("type")
    if provider == "claude":
        if kind != "result":
            return "", "event"
        said = claude_said(event)
        if said:
            return said, "stop"
        if event.get("is_error") is True:
            return "", "failed"
        if event.get("is_error") is False and isinstance(event.get("result"), str):
            return "", "answer"
        return "", "unknown"
    if kind not in ("error", "turn.failed", "turn.completed"):
        return "", "event"
    error = event if kind == "error" else event.get("error") if kind in ("turn.failed", "turn.completed") else None
    stop = _codex_kind(error) if isinstance(error, Mapping) else ""
    if stop:
        return _CODEX_SAID[stop], "stop"
    return "", "answer" if kind == "turn.completed" and error is None else "failed"


def _reply_outcome(provider: str, events: Sequence[Mapping[str, Any]]) -> tuple[str, bool, bool]:
    """One ordered rule for every native event: refusal persists; only a later completion clears a wait."""
    refused, said, answered, failed, unknown = "", "", False, False, False
    for event in events:
        stop, state = _event_outcome(provider, event)
        if stop.startswith(MODEL_REFUSED):
            refused = stop
        elif stop:
            said, answered = stop, False
        if state == "answer":
            said, answered = "", True
        elif state == "failed":
            failed, answered = True, False
        elif state not in {"event", "stop"}:
            unknown, answered = True, False
    unread = f"{HOST_UNKNOWN}: host result has an unrecognised status; these inputs stay barred" if unknown else ""
    return refused or unread or said, failed, answered and not failed and not unknown


def _codex_result(events: Sequence[Mapping[str, Any]]) -> tuple[str, bool, bool]:
    """Compatibility facts use the same ordered rule as the owned classifier."""
    return _reply_outcome("codex", events)


def _codex_turn(printed: str) -> tuple[str, bool]:
    """Compatibility stop facts from the same parser; only the owned observer decides a reply's class."""
    said, failed, _ = _codex_result(_read_reply("codex", printed)[0])
    # This compatibility API projects provider-reported stops, not G6's local unread-result hold. The owned classifier
    # still reads the whole outcome and bars the admitted inputs for that hold.
    return ("" if said.startswith(HOST_UNKNOWN + ":") else said), failed


def codex_said(printed: str) -> str:
    """What Codex's own JSON events say stopped the turn, in G6's own words, or ``""`` (``_codex_turn``)."""
    return _codex_turn(printed)[0]


def plain_text(value: str) -> str:
    """Own the underlying text, bypassing a subclass's rendering and encoding hooks."""
    if not isinstance(value, str):
        raise LLMError("G6_E_LOOP_UNNAMED_REQUEST: a request must be text")
    return str.__str__(value)


def _digest(encoded: bytes) -> str:
    return hashlib.sha256(encoded).hexdigest()


def _text_bytes(system: str, user: str) -> bytes:
    system, user = plain_text(system), plain_text(user)
    try:
        if "\0" not in system and "\0" not in user:
            return (system + "\0" + user).encode("utf-8")
    except UnicodeEncodeError:
        pass
    framed = b"\0\0"
    for part in (system, user):
        data = part.encode("utf-8", "surrogatepass")
        framed += len(data).to_bytes(8, "big") + data
    return framed


def request_digest(system: str, user: str) -> str:
    """What a request to a model is known by once refused, when no host program's input can be seen: no text of it is kept.
    A pair with no NUL and no lone surrogate in it is digested as it always was, its two parts joined by one NUL, so what was
    refused before is still known. Any other pair is digested from its bytes, each part as UTF-8 with its surrogates kept as
    they are and framed by its length, behind two NULs, which no such join can begin with: no two pairs share a digest, and a
    surrogate pair is not the character it would stand for."""
    return _digest(_text_bytes(system, user))


def request_identity(argv: Sequence[str], stdin: str, root: str | Path | None) -> str:
    """What a host request is known by: the digest of the arguments a program is started with, after the program itself, and
    of its input, exactly as given. A path in the call's own folder (``root``) stands for the bytes of the file there, which
    G6 wrote for the program to read, or for its place in that folder where there is no file yet, as where the program
    writes; any other argument stands as it is. So the same bytes are one request, whichever call or rendering made them."""
    captured = argv if isinstance(argv, _HostInput) else _host_input(argv, root, stdin)
    return _digest(captured.encoded)


@dataclass(frozen=True)
class _HostInput(Sequence[str]):
    """The host identity reader accepts a captured input as well as a live command; a captured one opens no file."""

    arguments: tuple[str, ...]
    parts: tuple[tuple[str, ...], ...]
    files: tuple[tuple[tuple[int, int], bytes], ...]
    aliases: tuple[tuple[int, tuple[int, int]], ...]
    encoded: bytes
    root: str
    locations: tuple[tuple[int, str], ...]
    root_file: tuple[int, int] | None

    def __getitem__(self, index: Any) -> Any:
        return self.arguments[index]

    def __len__(self) -> int:
        return len(self.arguments)


@dataclass
class _Delivery:
    folders: list[tuple[Path, tuple[int, int]]] = field(default_factory=list)
    argv: tuple[str, ...] | None = None
    lock: Any = field(default_factory=threading.Lock)
    anchors: ExitStack = field(default_factory=ExitStack)
    directories: set[tuple[int, int]] = field(default_factory=set)
    base: _InputHandle | None = None

    def directory(self, root: Path, relative: Path) -> _InputHandle:
        """Create each copy parent under an opened parent, and pin it before any child is created."""
        parent = self.base
        path = root
        for part in relative.parts:
            path = path / part
            created = False
            try:
                if os.name == "nt":
                    path.mkdir()
                else:
                    os.mkdir(part, dir_fd=parent.fileno())
                created = True
            except FileExistsError:
                pass
            opened = self.anchors.enter_context(_open_input(path, pin=True, parent=parent))
            key = _file_identity(opened)
            if (not created and key not in self.directories) or not _inside(_final_path(opened), _final_path(self.base)):
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: private copy parent changed")
            self.directories.add(key)
            parent = opened
        return parent


@dataclass(frozen=True)
class Request:
    """An admitted immutable input and its canonical encoding; only its digest is persisted.

    Private delivery copies belong to this request and are released with its send. The bound transport's wire encoding
    remains outside this seam, rather than being claimed as this canonical manifest's encoding.
    """

    identity: str
    system: str = ""
    user: str = ""
    argv: tuple[str, ...] = ()
    stdin: str = ""
    files: tuple[tuple[tuple[int, int], bytes], ...] = ()
    aliases: tuple[tuple[int, tuple[int, int]], ...] = ()
    encoded: bytes = b""
    root: str = ""
    locations: tuple[tuple[int, str], ...] = ()
    root_file: tuple[int, int] | None = None
    _delivery: _Delivery = field(default_factory=_Delivery, compare=False, repr=False)

    @classmethod
    def text(cls, system: str, user: str) -> Request:
        system, user = plain_text(system), plain_text(user)
        encoded = _text_bytes(system, user)
        return cls(_digest(encoded), system, user, encoded=encoded)

    @classmethod
    def host(cls, argv: Sequence[str], stdin: str, root: str | Path | None) -> Request:
        arguments, stdin = tuple(plain_text(arg) for arg in argv), plain_text(stdin)
        captured = _host_input(arguments, root, stdin)
        return cls(request_identity(captured, stdin, root), argv=arguments, stdin=stdin, files=captured.files,
                   aliases=captured.aliases, encoded=captured.encoded, root=captured.root, locations=captured.locations,
                   root_file=captured.root_file)

    def handoff(self) -> tuple[list[str], str]:
        """Hand off private copies without writing into any shared input or its aliases.

        The bound launcher encodes argv/stdin itself and reopens paths. Custody through that final open needs issuance.
        """
        with self._delivery.lock:
            if self._delivery.argv is None:
                copies: dict[tuple[int, int], tuple[Path, tuple[int, int]]] = {}
                locations = dict(self.locations)
                data = dict(self.files)
                delivered = list(self.argv)
                folder = None
                if self.files:
                    anchor = self._delivery.anchors.enter_context(_open_input(Path(self.root), pin=True))
                    if _file_identity(anchor) != self.root_file:
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: admitted folder changed before delivery")
                    # The destination is G6's own new folder, never an input's possibly replaced parent.
                    folder = Path(self.root) / (".g6-input-" + uuid4().hex)
                    if os.name == "nt":
                        folder.mkdir()
                    else:
                        os.mkdir(folder.name, dir_fd=anchor.fileno())
                    opened = self._delivery.anchors.enter_context(_open_input(folder, pin=True, parent=anchor))
                    self._delivery.folders.append((folder, _file_identity(opened)))
                    if not _inside(_final_path(opened), self.root):
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: delivery folder left its admitted folder")
                    self._delivery.base = opened
                    self._delivery.directories.add(_file_identity(opened))
                for index, identity in self.aliases:
                    target = folder / locations[index]
                    parent = self._delivery.directory(folder, Path(locations[index]).parent)
                    if identity not in copies:
                        try:
                            with _create_copy(target, parent) as stream:
                                if not _inside(_final_path(stream), self.root):
                                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: private copy left its admitted folder")
                                stream.write(data[identity])
                                copied = _file_identity(stream)
                                if os.name == "nt":
                                    self._delivery.anchors.enter_context(_open_input(target, pin=True))
                        except FileExistsError:
                            raise LLMError("G6_E_LOOP_INPUT_CHANGED: different inputs share a delivery name") from None
                        copies[identity] = target, copied
                    else:
                        source, copied = copies[identity]
                        # The name is only a requested directory entry. If it exists, its opened identity decides.
                        try:
                            if os.name == "nt":
                                os.link(source, target)
                            else:
                                source_parent = self._delivery.directory(folder, source.relative_to(folder).parent)
                                os.link(source.name, target.name, src_dir_fd=source_parent.fileno(),
                                        dst_dir_fd=parent.fileno(), follow_symlinks=False)
                        except FileExistsError:
                            pass
                        opened = self._delivery.anchors.enter_context(_open_input(target, pin=True, parent=parent))
                        if _file_identity(opened) != copied:
                            raise LLMError("G6_E_LOOP_INPUT_CHANGED: different inputs share a delivery name")
                    delivered[index] = str(target)
                self._delivery.argv = tuple(delivered)
            return list(self._delivery.argv), self.stdin

    def close(self) -> None:
        with self._delivery.lock:
            failed = None
            try:
                self._delivery.anchors.close()
            except BaseException as exc:
                failed = exc
            for folder, identity in self._delivery.folders:
                try:
                    with _open_input(folder) as stream:
                        if _file_identity(stream) != identity:
                            raise LLMError("G6_E_LOOP_INPUT_CHANGED: private copy folder changed before cleanup")
                    shutil.rmtree(folder, ignore_errors=True)
                except FileNotFoundError:
                    pass
                except BaseException as exc:
                    failed = failed or exc
            if failed is not None:
                raise failed


# Input roles emitted by the two host command producers: ClaudeCodeCaller and ToolSession.
_INPUT_FLAGS = frozenset({"--system-prompt-file", "--mcp-config", "--settings"})


@dataclass(frozen=True)
class _InputHandle:
    fd: int

    def fileno(self) -> int:
        return self.fd

    def read(self) -> bytes:
        chunks = []
        while data := os.read(self.fd, 1024 * 1024):
            chunks.append(data)
        return b"".join(chunks)


def _windows_files() -> Any:
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFinalPathNameByHandleW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    api.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    api.GetFileInformationByHandleEx.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api


@contextmanager
def _open_input(path: Path, *, pin: bool = False, parent: _InputHandle | None = None,
                follow_directory: bool = False) -> Iterator[_InputHandle]:
    """Open once without following the final link; ancestors are judged by the resulting handle's final path."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        api = _windows_files()
        flags = 0x02000000 if follow_directory else 0x02200000
        handle = api.CreateFileW(str(path), 0x80000000, 3 if pin else 7, None, 3, flags, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # FileAttributeTagInfo: reject all reparse tags, including tags we do not understand.
            info = (ctypes.c_ulong * 2)()
            if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info[0] & 0x400:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input is an unresolved reparse point")
            fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            api.CloseHandle(handle)
            raise
    else:
        if not hasattr(os, "O_NOFOLLOW"):
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input cannot be opened without following links")
        flags = os.O_RDONLY | (os.O_DIRECTORY if follow_directory else os.O_NOFOLLOW)
        fd = os.open(path.name if parent is not None else path, flags,
                     dir_fd=parent.fileno() if parent is not None else None)
    try:
        yield _InputHandle(fd)
    finally:
        os.close(fd)


@contextmanager
def _create_copy(path: Path, parent: _InputHandle) -> Iterator[Any]:
    if os.name == "nt":
        with path.open("xb") as stream:
            yield stream
    else:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                     dir_fd=parent.fileno())
        with os.fdopen(fd, "wb") as stream:
            yield stream


def _final_path(stream: _InputHandle) -> str:
    """The opened object's OS name, never a second resolution of the input's spelling."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        buffer = ctypes.create_unicode_buffer(32768)
        size = _windows_files().GetFinalPathNameByHandleW(msvcrt.get_osfhandle(stream.fileno()), buffer, len(buffer), 0)
        if not size or size >= len(buffer):
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input's final path cannot be resolved")
        name = buffer.value
        if name.startswith("\\\\?\\UNC\\"):
            name = "\\\\" + name[8:]
        elif name.startswith("\\\\?\\"):
            name = name[4:]
    else:
        try:
            name = os.readlink(f"/proc/self/fd/{stream.fileno()}")
        except OSError:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input's final path cannot be resolved") from None
        if name.endswith(" (deleted)"):
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: opened host input was removed")
    return os.path.normcase(os.path.normpath(name))


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _file_identity(stream: Any) -> tuple[int, int]:
    """Identity from the admitted open: Windows volume serial and full file id; device/inode on other platforms."""
    if os.name == "nt":
        import ctypes
        import msvcrt

        class FileIdInfo(ctypes.Structure):
            _fields_ = [("volume", ctypes.c_ulonglong), ("file", ctypes.c_ubyte * 16)]

        native = FileIdInfo()
        if not _windows_files().GetFileInformationByHandleEx(msvcrt.get_osfhandle(stream.fileno()), 18,
                                                            ctypes.byref(native), ctypes.sizeof(native)):
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: opened object's native file identity cannot be read")
        identity = int.from_bytes(bytes(native.file), "little")
        if not identity:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: opened object has no file identity")
        return native.volume, identity
    info = os.fstat(stream.fileno())
    if not info.st_ino:
        raise LLMError("G6_E_LOOP_INPUT_CHANGED: opened object has no file identity")
    return info.st_dev, info.st_ino


def _output_identity(path: Path) -> tuple[Any, ...]:
    """An existing object, or an uncreated slot under its opened parent. Slot labels never identify an existing file."""
    missing = []
    while True:
        try:
            with _open_input(path) as stream:
                identity = _file_identity(stream)
                if not missing:
                    return ("object", *identity)
                if not stat.S_ISDIR(os.fstat(stream.fileno()).st_mode):
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: output parent is not a directory")
                return ("slot", *identity, *reversed(missing))
        except FileNotFoundError:
            missing.append(path.name)
            parent = path.parent
            if parent == path:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: output has no opened parent") from None
            path = parent
        except OSError:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: output cannot be opened safely") from None


def _output_place(path: Path, root: str) -> str:
    """A not-yet-created output belongs where its nearest existing opened parent actually lives."""
    missing = []
    while True:
        try:
            with _open_input(path) as stream:
                resolved = _final_path(stream)
                if not stat.S_ISDIR(os.fstat(stream.fileno()).st_mode):
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: host output parent is not a directory")
                break
        except FileNotFoundError:
            missing.append(path.name)
            parent = path.parent
            if parent == path:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: host output has no resolvable parent") from None
            path = parent
    resolved = os.path.normpath(os.path.join(resolved, *reversed(missing)))
    if not _inside(resolved, root):
        raise LLMError("G6_E_LOOP_INPUT_CHANGED: host output is outside its admitted folder")
    return resolved


def _host_input(argv: Sequence[str], root: str | Path | None, stdin: str = "") -> _HostInput:
    """The existing host identity format, and the exact file bytes it names, read together once."""
    base = None
    root_file = None
    if root is not None:
        with _open_input(Path(root)) as folder:
            base = _final_path(folder)
            info = os.fstat(folder.fileno())
            root_file = _file_identity(folder)
            if not stat.S_ISDIR(info.st_mode) or not info.st_ino:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: admitted folder is not a directory")
    parts: list[list[str]] = []
    files: list[tuple[tuple[int, int], bytes]] = []
    aliases: list[tuple[int, tuple[int, int]]] = []
    locations: list[tuple[int, str]] = []
    by_file: dict[tuple[int, int], tuple[bytes, tuple[int, int, int]]] = {}
    roles: dict[tuple[int, int], bool] = {}
    arguments = tuple(plain_text(arg) for arg in argv)
    # G6's producers emit separate flag/path arguments. Reject alternate file-option syntax rather than passing an
    # unclassified path through as ordinary text (the host program would still open it).
    if any(arg.startswith(flag + "=") for arg in arguments for flag in _INPUT_FLAGS | {"--output-last-message"}):
        raise LLMError("G6_E_LOOP_INPUT_CHANGED: host file option has an unsupported path form")
    required: set[int] = set()                    # argument positions are syntax, never filesystem identities
    outputs: set[int] = set()
    for index, arg in enumerate(arguments):
        if arg in _INPUT_FLAGS or arg == "--output-last-message":
            if index + 1 == len(arguments) or arguments[index + 1].startswith("--"):
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input flag has no path")
            (required if arg in _INPUT_FLAGS else outputs).add(index + 1)
    for index, arg in enumerate(arguments[1:], 1):
        where = os.path.normcase(os.path.abspath(arg)) if os.path.isabs(arg) else ""
        candidate = base and where
        if candidate:
            try:
                opened = _open_input(Path(arg))
                stream = opened.__enter__()
            except FileNotFoundError:
                if index in required:
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: required host input is missing") from None
                if not _inside(where, base):
                    if index in outputs:
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: host output is outside its admitted folder")
                    parts.append(["arg", arg])
                    continue
                resolved = _output_place(Path(arg), base)
                place = os.path.relpath(where if _inside(where, base) else resolved, base).replace(os.sep, "/")
                parts.append(["path", place])
                continue
            except OSError:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input could not be opened safely") from None
            try:
                final = _final_path(stream)
                if not _inside(final, base):
                    if index in required or index in outputs or _inside(where, base):
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input is outside its admitted folder")
                    parts.append(["arg", arg])
                    continue
                # Containment is the opened object's fact. Retain an admitted argument's logical name afterwards so an
                # existing refusal keeps its digest; new spellings (short/extended names) use the handle's canonical name.
                place = os.path.relpath(where if _inside(where, base) else final, base).replace(os.sep, "/")
                place = "/".join(part for part in place.split("/") if not part.startswith(".g6-input-"))
                before = os.fstat(stream.fileno())
                key = _file_identity(stream)
                output = index in outputs
                if key in roles and roles[key] != output:
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: one opened object has conflicting input and output roles")
                roles[key] = output
                if index in outputs:
                    if not stat.S_ISREG(before.st_mode):
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: host output is not a regular file")
                    parts.append(["path", place])
                    continue
                if stat.S_ISDIR(before.st_mode):
                    if index in required:
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: required host input is a directory")
                    parts.append(["path", place])
                    continue
                if not stat.S_ISREG(before.st_mode) or not before.st_ino:
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input has no regular file identity")
                stamp = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                if key not in by_file:
                    data = stream.read()
                    after = os.fstat(stream.fileno())
                    if (_file_identity(stream), after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (key, *stamp):
                        raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input changed during capture")
                    by_file[key] = (data, stamp)
                    files.append((key, data))
                data, captured_stamp = by_file[key]
                if stamp != captured_stamp:
                    raise LLMError("G6_E_LOOP_INPUT_CHANGED: aliased host input changed during capture")
                parts.append(["file", place, _digest(data)])
                aliases.append((index, key))
                locations.append((index, place))
            finally:
                opened.__exit__(None, None, None)
        else:
            if index in required or index in outputs:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: host input has no absolute path")
            parts.append(["arg", arg])
    encoded = json.dumps(["host", parts, plain_text(stdin)]).encode("ascii")
    return _HostInput(arguments, tuple(tuple(part) for part in parts), tuple(files), tuple(aliases), encoded,
                      base or "", tuple(locations), root_file)


@dataclass(frozen=True, eq=False)
class Send:
    """One send, as the loop let it out: made once, after any wait, and never changed. ``identity``: what left, read from its
    bytes at that moment; ``watched``: whether its caller reads its program's output itself, so only that decides for it;
    ``owner``: the innermost metered call it was made in, and ``thread`` where; ``generation``: the tick; ``sender``: the
    caller that sent it; ``places``: what it was charged on each meter as it was let out, set once, so no call charges it
    again; ``charged``: each meter it took those places on, with the tick it took them under, which is where what the
    provider says of it goes."""

    identity: str | None
    watched: bool
    owner: object
    generation: int
    thread: int
    sender: Any = None
    places: int = 0
    charged: tuple[tuple[Any, int], ...] = ()
    request: Request | None = None


@contextmanager
def telling_stops(gate: Gate) -> Iterator[None]:
    """Inside, a watched caller asks ``gate`` before its program starts (``sending``), which hands back the send's record or
    raises to keep it in; a stop it reads goes to ``gate`` under that record before anything else sees it (``told``); and the
    record is released by its own outcome (``done``). What any of these raises is what stops the request, so a wrapper between
    it and the loop cannot take a refusal or a limit for a failed request and send it again. A call nested in another sets its
    own gate here, and the loop's admission behind every gate is one and the same, which lets the send in for every meter
    around it, so the innermost gate stands for them all."""
    token = _TELL.set(gate)
    try:
        yield
    finally:
        _TELL.reset(token)


class ProviderStop(LLMError):
    """What a watched caller raises for a stop its provider reported: G6's own code first, and the record of the send it was
    of (``record``), or what was sent where no record was made."""

    def __init__(self, said: str, record: Send | str) -> None:
        super().__init__(said)
        self.said, self.record = said, record

    @property
    def identity(self) -> str | None:
        return self.record.identity if isinstance(self.record, Send) else self.record


@dataclass(frozen=True)
class _OwnedSend:
    """The reply folder's captured identity, send and gate, held until the launch's finally."""

    key: tuple[int, int]
    send: Send
    gate: Gate | None


def _sending(caller: Any, argv: Sequence[str], stdin: str, files: Any) -> tuple[list[str], str, _OwnedSend]:
    """The last step before a watched caller's program starts: the loop's gate lets it out or keeps it in, and makes the send's
    record from the bytes as they are once it is let out, which stays with this call's own folder until its outcome is read."""
    root = Path(files).parent if files is not None else None
    gate = _TELL.get()
    if gate is None:
        request = Request.host(argv, stdin, root)
        record = Send(request.identity, True, None, 0, threading.get_ident(), caller, request=request)
    else:
        record = gate.sending(lambda: Request.host(argv, stdin, root), caller)
    key = None
    try:
        try:
            anchor = record.request._delivery.anchors.enter_context(_open_input(Path(files), pin=True))
        except OSError:
            raise LLMError("G6_E_LOOP_INPUT_CHANGED: reply folder cannot be opened safely") from None
        key = _file_identity(anchor)
        with _SENT_LOCK:
            if key in _SENT:
                raise LLMError("G6_E_LOOP_INPUT_CHANGED: reply folder already has an outstanding launch")
            _SENT[key] = (record, gate)
        opened = _OPEN.get()
        if opened is not None:
            opened.append(key)
        argv, stdin = record.request.handoff()
        return argv, stdin, _OwnedSend(key, record, gate)
    except BaseException:
        try:
            if key is not None:
                _forget(key, record)
        finally:
            _finish_record(record, gate)
        raise


def _forget(files: Any, expected: Send | None = None) -> tuple[Send | None, Gate | None]:
    """This send's own record, taken out of where it waited for its outcome, and released by that outcome."""
    if isinstance(files, tuple):
        key = files                              # already captured by the same open that registered the send
    else:
        with _open_input(Path(files)) as stream:
            key = _file_identity(stream)
    with _SENT_LOCK:
        if expected is not None and _SENT.get(key, (None, None))[0] is not expected:
            return None, None
        record, gate = _SENT.pop(key, (None, None))
    return record, gate


def _finish_record(record: Send | None, gate: Gate | None) -> None:
    try:
        if record is not None and record.request is not None:
            record.request.close()
    finally:
        if record is not None and gate is not None:
            gate.done(record)


def _received(hide: tuple[str, ...]) -> str:
    """What a reply read with no step that sends before it was given, as the bound caller says: its system text and request,
    or a prompt alone. Such a request was never checked as it left, so this serves the record and nothing else."""
    return request_digest(*hide[:2]) if len(hide) > 1 else request_digest("", hide[0] if hide else "")


def _stopped(said: str, files: Any, hide: tuple[str, ...]) -> None:
    """Tell the loop what the provider said of the send this reply is of, then raise it as the request's failure: before the
    bound caller cuts what the program printed down to its end. The send's record is released only once that is told."""
    owned = isinstance(files, _OwnedSend)
    record, gate = (files.send, files.gate) if owned else (None, None)
    try:
        if not owned and files is not None:
            record, gate = _forget(files)
        if said:
            told = gate if gate is not None else _TELL.get()
            what: Send | str = record if record is not None else _received(hide)
            if told is not None:
                told.told(said, what)
            raise ProviderStop(said, what)
    finally:
        if not owned:
            _finish_record(record, gate)


def _released(files_keys: Sequence[str | tuple[int, int] | _OwnedSend]) -> None:
    """Sends of a call that ended with no reply read: each is released by that outcome, and no other send is."""
    failed = None
    for key in files_keys:
        record, gate = (key.send, key.gate) if isinstance(key, _OwnedSend) else (None, None)
        try:
            try:
                if isinstance(key, _OwnedSend):
                    _forget(key.key, record)
                else:
                    record, gate = _forget(key)
            finally:
                _finish_record(record, gate)
        except BaseException as exc:
            failed = failed or exc
    if failed is not None:
        raise failed


_FINAL_HOST_METHODS = frozenset({"complete", "run", "_invocation", "_launch", "_reply", "__getattribute__",
                                 "__getattr__", "__setattr__", "_observe_stops", "__class__", "__init_subclass__"})


class _Watched:
    """What both watched callers do around the bound call: a send whose program never answers is released as the call ends."""

    _observe_stops = True

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _FINAL_HOST_METHODS:
            raise LLMError("G6_E_LOOP_UNWATCHED_HOST: host instance replaces a final send method")
        object.__setattr__(self, name, value)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        if cls.__module__ != __name__:
            _check_seam(cls)                      # first: a replaced send method is refused as this module's own case
        super().__init_subclass__(**kwargs)

    @contextmanager
    def _invocation(self) -> Iterator[None]:
        token = _OPEN.set([])
        try:
            yield
        finally:
            try:
                _released(_OPEN.get())
            finally:
                _OPEN.reset(token)

    def complete(self, system: str, user: str, **kwargs: Any) -> str:
        _check_seam(type(self))
        with _Watched._invocation(self):
            return _complete_host(self, plain_text(system), plain_text(user), **kwargs)

    def run(self, *args: Any, **kwargs: Any) -> str:
        _check_seam(type(self))
        with _Watched._invocation(self):
            return super().run(*args, **kwargs)


def _complete_host(caller: Any, system: str, user: str, *, max_tokens: int = host_models.DEFAULT_MAX_TOKENS) -> str:
    """The owned completion path. Every caller hook runs before admission; only module functions run after it."""
    provider = _host_kind(caller)
    if not any(base is _Watched for base in type.__getattribute__(type(caller), "__mro__")):
        raise LLMError("G6_E_LOOP_UNWATCHED_HOST: constructed host no longer has its supported preparation hooks")
    read_stops(caller)                            # convenience argv hooks emit structured mode; final data decides
    program = caller.locate()
    with host_models._folders() as (work, files):
        argv, stdin = caller._command(program, system, user, work, files)
        env = host_models._environment(caller.keep_env, files / "tmp")
        seconds, name = float(caller.timeout), provider
        reply = host_models.ClaudeCodeCaller._reply if provider == "claude" else host_models.CodexCaller._reply
        argv, stdin = list(map(plain_text, argv)), plain_text(stdin)
        _check_observation(argv, provider, files)
        argv, stdin, owned = _sending(caller, argv, stdin, files)
        try:
            _check_observation(argv, provider, files)
            # the sharing check of every owned launch: after the loop's own bar, before the program starts
            with cloud_consent.admission(**host_admission(caller, system, user)):
                _note_launch(owned)
                try:
                    code, printed, errors = host_models._run(argv, stdin, work, files, env, seconds, name)
                except BaseException as failed:
                    try:
                        _observe_reply(provider, None, "", owned, (system, user, stdin))
                    finally:
                        raise failed
                _observe_reply(provider, code, printed, owned, (system, user, stdin))
                try:
                    return reply(caller, code, printed, errors, files, (system, user, stdin))
                except BaseException as failed:
                    try:
                        _observe_reply(provider, None, "", owned, (system, user, stdin))
                    finally:
                        raise failed
        finally:
            _released((owned,))


def _host_kind(caller: Any) -> str:
    """Construction identity selects the owned route even after class erasure; ancestry covers older native instances."""
    with _HOSTS_LOCK:
        entry = _HOSTS.get(id(caller))
        if entry is not None and entry[0]() is caller:
            return entry[1]
    ancestry = type.__getattribute__(type(caller), "__mro__")
    return next((provider for (host, _), provider in zip(_WATCHED, ("claude", "codex"))
                 if any(base is host for base in ancestry)), "")


def _remember_host(caller: Any) -> None:
    key, protocol = id(caller), _host_kind(caller)

    def forget(reference: weakref.ReferenceType[Any]) -> None:
        with _HOSTS_LOCK:
            if _HOSTS.get(key, (None, ""))[0] is reference:
                del _HOSTS[key]

    reference = weakref.ref(caller, forget)
    with _HOSTS_LOCK:
        _HOSTS[key] = reference, protocol


def _native_host_note(stop: Any) -> bool:
    """A borrowed native completion still requires owned launch data, whichever object or class invokes it."""
    frame = sys._getframe(1)
    try:
        while frame is not None and frame.f_code is not stop:
            if frame.f_code is _NATIVE_HOST_COMPLETE:
                return True
            frame = frame.f_back
        return False
    finally:
        del frame


def launch_record() -> Send | None:
    """The immutable launch a note is about, supplied only by the owned path."""
    return _LAUNCH_RECORD.get()


@contextmanager
def loop_tick() -> Iterator[None]:
    """A loop tick of this process is open inside (``ShadowLoop``'s tick), on whatever thread or context."""
    global _TICKS
    with _TICKS_LOCK:
        _TICKS += 1
    try:
        yield
    finally:
        with _TICKS_LOCK:
            _TICKS -= 1


def _note_launch(owned: _OwnedSend) -> None:
    """The last step before an owned launch's program starts. Its sharing check has passed (``cloud_consent.admission``).
    Outside every loop tick of this process that check is all an owned launch passes, as any G6 model call: the loop's
    gate is set only inside a tick's metered call. While a tick is open, a launch with no gate in its context escaped
    that tick's gate (a thread started without the tick's context, a caller outside its metered calls): no meter could
    charge it or hold it to what the tick refused, so it is refused."""
    record, gate = owned.send, owned.gate
    if gate is None and _TICKS:
        raise LLMError("G6_E_LOOP_UNWATCHED_HOST: owned host launch has no admission gate while a loop tick is open")
    token = _LAUNCH_RECORD.set(record)
    try:
        host_models._start_model_call()
    finally:
        _LAUNCH_RECORD.reset(token)


# Every option used to observe a completion. Joined, repeated and post-delimiter forms are deliberately refused.
_OBSERVATION_OPTIONS = {"claude": ("-p", "--output-format"),
                        "codex": ("exec", "--json", "--output-last-message")}


def _check_observation(argv: Sequence[str], provider: str, files: Path) -> None:
    def bad() -> None:
        raise LLMError("G6_E_LOOP_UNOBSERVABLE_HOST: final command does not retain its observation protocol")

    if provider not in _OBSERVATION_OPTIONS or not argv:
        bad()
    end = argv.index("--") if "--" in argv else len(argv)
    for option in _OBSERVATION_OPTIONS[provider]:
        if argv.count(option) != 1 or argv.index(option) >= end or any(arg.startswith(option + "=") for arg in argv):
            bad()
    if provider == "claude":
        at = argv.index("--output-format")
        if at + 1 >= end or tuple(argv[1:4]) != ("-p", "--output-format", "json"):
            bad()
    else:
        at = argv.index("--output-last-message")
        if argv.index("exec") != 1 or argv.index("--json") != 2 or at + 1 >= end:
            bad()
        if _output_identity(Path(argv[at + 1])) != _output_identity(files / "last_message.txt"):
            bad()


def _classify_reply(provider: str, code: int | None, printed: str) -> tuple[str, str]:
    """The one reply decision: refusal first, then unread/unfinished replies, stops, failures and answers.

    A returned exit status means the launcher read its streams to the end. A nonzero exit needs no terminal event to
    establish a failed case, but cannot excuse an unparseable stream. ``None`` means launch or reply reading did not finish.
    Only native outcomes carry authority. Any reader failure becomes unknown; no exception grants resend authority.
    """
    try:
        events, readable = _read_reply(provider, printed)
        said, failed, known = (_codex_result(events) if provider == "codex" else _reply_outcome(provider, events))
    except BaseException:
        said, failed, known, readable = "", False, False, False
    if said.startswith(MODEL_REFUSED):
        return "refusal", said
    if code is None or not readable:
        return "unknown", f"{HOST_UNKNOWN}: host reply could not be read whole; these inputs stay barred"
    if said.startswith(HOST_UNKNOWN + ":"):
        return "unknown", said
    if said:
        return "stop", said
    if code != 0 or failed:
        return "failed", f"{host_models.HOST_FAILED}: {provider} reported that its turn failed, of no kind G6 reads as a stop"
    if not known:
        return "unknown", f"{HOST_UNKNOWN}: host reply ended before its result; these inputs stay barred"
    return "answer", ""


def _observe_reply(provider: str, code: int | None, printed: str, files: Any, hide: tuple[str, ...]) -> None:
    """Apply the module's decision before returning to any caller-supplied reply dispatch."""
    kind, said = _classify_reply(provider, code, printed)
    if kind in {"refusal", "unknown", "stop"}:
        _stopped(said, files, hide)
    if kind == "failed":
        raise LLMError(said)


class WatchedClaude(_Watched, ConsentClaude):
    """Claude Code as the loop calls it: as the bound caller, checked as it sends, and a stop in its result raises G6's own
    code."""

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        return super()._command(program, system, user, work, files)

    def _launch(self, argv: list[str], stdin: str, cwd: Path, files: Path, env: dict[str, str], seconds: float) -> str:
        """A session of a caller's own that starts its program itself names that step ``_launch`` (``ToolSession``)."""
        name = "claude"
        reply = host_models.ClaudeCodeCaller._reply
        argv, stdin = list(map(plain_text, argv)), plain_text(stdin)
        env, seconds = dict(env), float(seconds)
        _check_observation(argv, name, files)
        argv, stdin, owned = _sending(self, argv, stdin, files)
        try:
            _check_observation(argv, name, files)
            with cloud_consent.admission(**session_admission(self, stdin)):   # inside its session's admitted run
                _note_launch(owned)
                try:
                    result = host_models._run(argv, stdin, cwd, files, env, seconds, name)
                except BaseException as failed:
                    try:
                        _observe_reply(name, None, "", owned, (stdin,))
                    finally:
                        raise failed
                _observe_reply(name, result[0], result[1], owned, (stdin,))
                try:
                    return reply(self, *result, files, (stdin,))
                except BaseException as failed:
                    try:
                        _observe_reply(name, None, "", owned, (stdin,))
                    finally:
                        raise failed
        finally:
            _released((owned,))

    def _reply(self, code: int, printed: str, errors: str, files: Any, hide: tuple[str, ...]) -> str:
        if watched(self):
            _observe_reply("claude", code, printed, files, hide)
        return super()._reply(code, printed, errors, files, hide)


class WatchedCodex(_Watched, ConsentCodex):
    """Codex as the loop calls it: as the bound caller, checked as it sends, printing its events as JSON, and a stop in them
    raises G6's own code. A turn it reports as failed is a failed request. The reply is still the last message Codex writes."""

    def argv(self, program: str, *, work: str, last_message: str) -> list[str]:
        args = super().argv(program, work=work, last_message=last_message)
        if not watched(self) or "exec" not in args:
            return args
        at = args.index("exec") + 1
        return [*args[:at], "--json", *args[at:]]

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        return super()._command(program, system, user, work, files)

    def _reply(self, code: int, printed: str, errors: str, files: Any, hide: tuple[str, ...]) -> str:
        if not watched(self):
            return super()._reply(code, printed, errors, files, hide)
        _observe_reply("codex", code, printed, files, hide)
        return super()._reply(code, printed, errors, files, hide)


#: Each host caller the loop watches, and what it becomes.
_WATCHED = ((host_models.ClaudeCodeCaller, WatchedClaude), (host_models.CodexCaller, WatchedCodex))


def _check_seam(kind: type) -> None:
    """Command/argv/locate hooks run before admission. Completion, observation and session launch are final.

    ToolSession's existing run and launch are admitted explicitly: its launch is replaced at construction by our final
    module-owned launch, which starts the program directly. A further subclass may customize only pre-admission hooks.
    """
    # consent's guarded hosts are bases of the two watched classes: their completion is shadowed by ``_Watched``'s
    owned = {_Watched, WatchedClaude, WatchedCodex, host_models._HostCaller, _Consent, ConsentClaude, ConsentCodex,
             host_models.ClaudeCodeCaller, host_models.CodexCaller, OpenAICompatibleCaller, AnthropicCaller, object}
    for base in kind.__mro__:
        if base in owned:
            continue
        methods = _FINAL_HOST_METHODS.intersection(base.__dict__)
        session_module = sys.modules.get("g6.harness.completion_loop")
        if session_module is not None and base is vars(session_module).get("ToolSession"):
            methods -= {"run", "_launch"}
        if methods:
            raise LLMError("G6_E_LOOP_UNWATCHED_HOST: host subclass overrides a final send method")


def _new_host(kind: type, *args: Any, **kwargs: Any) -> Any:
    """Choose the cleanup wrapper at construction, before any bound complete can enter.

    Plain callers stay dormant until read_stops enables observation. The bound constructor needs a native hook at issuance.
    """
    _check_seam(kind)
    dormant = not issubclass(kind, _Watched)
    if dormant:
        observer = next(wrapper for host, wrapper in _WATCHED if issubclass(kind, host))
        kind = type(kind.__name__, (observer, kind), {})
    caller = object.__new__(kind)
    object.__setattr__(caller, "_observe_stops", not dormant)
    _remember_host(caller)
    return caller


host_models._HostCaller.__new__ = staticmethod(_new_host)


def read_stops(sender: Any) -> None:
    """Enable the observer selected at construction. A live caller's class and send methods are never changed."""
    if isinstance(sender, tuple(host for host, _ in _WATCHED)):
        if not isinstance(sender, _Watched):
            raise LLMError("G6_E_LOOP_UNWATCHED_HOST: host caller predates the construction wrapper")
        _check_seam(type(sender))
        object.__setattr__(sender, "_observe_stops", True)


def watched(sender: Any) -> bool:
    """Whether ``sender`` reads its program's output itself, so only what it reads from the structured fields decides."""
    return isinstance(sender, WatchedClaude | WatchedCodex) and sender._observe_stops


def _time(value: Any) -> datetime | None:
    """``value``, seconds since 1970, as a moment, or ``None`` when no clock could show it. The number is converted once
    (``exact_number``) before any clock reads it."""
    seconds = exact_number(value)
    try:
        return utc(datetime.fromtimestamp(seconds, UTC)) if seconds is not None else None
    except (OverflowError, OSError, ValueError):
        return None


def _window(raw: Any) -> dict[str, Any] | None:
    """A window of the tool's reply as plain values, each read once and converted where it enters: its percentage
    (``figure_of``, to 100), its length in minutes and when it resets. ``None`` when any of them does not read, so a NaN, a
    subclass that answers its own way and nonsense alike lose the reply, and nothing of the reply is used after this. The
    tool may leave out how long a window is and when it resets, and it may not send nonsense for either."""
    try:
        if not isinstance(raw, Mapping):
            return None
        percent, minutes, reset = raw.get("usedPercent"), raw.get("windowDurationMins"), raw.get("resetsAt")
    except Exception:  # noqa: BLE001 - a window that cannot be read is none
        return None
    used, length, at = figure_of(percent, FULL), None if minutes is None else figure_of(minutes), _time(reset)
    if used is None or (minutes is not None and not length) or (reset is not None and at is None):
        return None
    return {"usedPercent": used, "windowDurationMins": length, "resetsAt": at}


def _reset(window: Mapping[str, Any], now: datetime) -> datetime | None:
    """When a converted window (``_window``) resets, or reset, where that can be believed: no further from ``now`` than
    the window is long (a week where it does not say), ahead or behind. The one rule for a reset in both directions, and
    the one reader of a window's reset: a limit that outlasts its own window cannot be believed, and neither can a week
    said to have ended longer ago than a week lasts. Any other value is no reset: it ends no limit, names no week's end
    and moves no kept week."""
    now = utc(now)
    at = window["resetsAt"]
    longest = (window["windowDurationMins"] or WEEK_MINUTES) * 60
    return at if at is not None and abs((at - now).total_seconds()) <= longest else None


def limits_reading(limits: Mapping[str, Any], window: ProviderWindow, now: datetime) -> AccountReading | None:
    """``limits`` as the meter reads them, or ``None`` when they do not read: use in the meter's own unit from the
    week's percentage, and a limit when a window is full or the provider says ordinary use is not allowed. One field that
    makes no sense loses the whole reply, since a reply that wrong says nothing about the rest. A limit whose reset has
    passed is still a limit, so it is looked at again as one with no reset is. A week whose own reset has passed is another
    matter: a tool that has not caught up is saying last week's use, so that figure is left out and nothing stands for it.
    That reset is kept apart as ``ended_at``: it says where the provider's weeks fall while the store keeps no word of it,
    and it ends no tick. A reading's ``resets_at`` and ``ended_at`` are the provider's own word or nothing, and what can
    be believed of it is one rule in both directions (``_reset``): a reset further from now than a week is long, ahead or
    behind, names no week's end. Its figure is then kept as one with no reset, as the percentage is the provider's own
    whatever its reset says and counting it can only refuse more; the meter puts it in the week the store's own grid says
    (``BudgetMeter.stands``). No reset is made up here: one that was would move the kept week as if the provider had said
    it."""
    now = utc(now)
    try:
        snapshot, allowed = limits.get("rateLimits"), limits.get("ordinaryUsageAllowed")
        if not isinstance(snapshot, Mapping) or not isinstance(allowed, bool | None):
            return None
        raw = [w for w in (snapshot.get("primary"), snapshot.get("secondary")) if w is not None]
    except Exception:  # noqa: BLE001 - a reply that cannot be read is no reading
        return None
    seen = [_window(w) for w in raw]
    if not seen or any(w is None for w in seen):
        return None
    week = next((w for w in seen if w["windowDurationMins"] == WEEK_MINUTES), None)
    full = [w for w in seen if w["usedPercent"] >= FULL]
    resets = [at for w in (full or seen) if (at := _reset(w, now)) is not None and at > now]
    until = (max(resets) if resets else now + REFRESH) if full or allowed is False else None
    if week is None:
        return AccountReading(limited_until=until)
    reset = _reset(week, now)
    if reset is not None and reset <= now:           # its week has ended: last week's figure says nothing of this one
        return AccountReading(limited_until=until, ended_at=reset)
    return AccountReading(used=window.allowance * week["usedPercent"] / FULL, estimated=False, limited_until=until,
                          resets_at=reset)


def _holds(reading: AccountReading | None) -> bool:
    """Whether what ``standing`` gave still says something: a reading, and not an unread one."""
    return reading is not None and not reading.unread


class AccountReader:
    """The ``read_account`` of a ``BudgetMeter``. Calling it only hands back what ``refresh`` last read, which the runner
    calls between ticks, as it stands when asked (``standing``, the one rule for whether a reading holds): once the week
    its figure is of has reset, that figure is last week's and is not handed back, and once nothing it said stands, it is
    unread. A provider whose tool reports nothing has no reading, and ``said`` says why: the meter then stays on its own
    count. A read that fails, a process that does not end cleanly (``_ran``) among them, or a reply that does not read,
    says nothing new, so the last good reading stands while ``standing`` keeps it, and what is charged meanwhile goes on
    top. Past that the account is not known, and nothing is admitted on it until a read succeeds: the owner uses this
    account too, so a broken tool must not make the meter read low. This is held in memory, and every provider whose tool
    is asked starts unread: before its first read, and after a restart or a read that ends in any way without a reading,
    it is not known, and admits nothing. Where a provider's weeks fall is the store's to keep, and is not kept here. The
    meter it reads for binds it (``bind_meter``): from then the store is told when a reading is taken in the step that
    makes it anyone's to use, so another meter or process on that store stamps after it at once; and whether a figure
    still stands is the meter's one rule (``BudgetMeter.stands``), which gives a figure with no reset the week's end the
    store's grid says."""

    def __init__(self, windows: Sequence[ProviderWindow],
                 ask: Mapping[str, Callable[[], Mapping[str, Any] | None]] | None = None) -> None:
        self.windows = windows_by_provider(windows)
        self.ask = {"codex": codex_limits} if ask is None else ask
        asked = set(self.windows) & set(self.ask)
        self.readings: dict[str, AccountReading] = {provider: AccountReading(unread=NOT_READ) for provider in asked}
        self.said = {provider: f"{NOT_READ}, so nothing is reserved on it until it is read" if provider in asked
                     else NOT_REPORTED for provider in self.windows}
        self._asked: datetime | None = None
        self._meter: Any = None

    def bind_meter(self, meter: Any) -> None:
        """Read for ``meter`` (a ``BudgetMeter``, which calls this as it is made; the one made last holds). What is already
        held is told to its store now; a reading whose time the store will not take is no reading."""
        self._meter = meter
        for provider, held in list(self.readings.items()):
            if not self._told(provider, held):
                self.readings[provider] = AccountReading(unread="its reading could not be recorded in the store")

    def _told(self, provider: str, reading: AccountReading) -> bool:
        """Tell the store of the meter this reads for when ``reading`` was taken (``BudgetMeter.tell``), and say whether it
        took it. With no meter there is no store to tell, and no admission to guard."""
        if self._meter is None:
            return True
        try:
            self._meter.tell(provider, reading.read_at)
        except Exception:  # noqa: BLE001 - whatever stopped it, the store does not know of this reading
            return False
        return True

    def _stands(self, provider: str, reading: AccountReading | None, now: datetime) -> AccountReading | None:
        """What ``reading`` still says at ``now``, by the one rule: the meter's (``BudgetMeter.stands``), and ``standing``
        alone before a meter binds this reader."""
        now = utc(now)
        return standing(reading, now) if self._meter is None else self._meter.stands(provider, reading, now)

    def _lapsed(self, now: datetime) -> bool:
        """Whether a reading that held when the tool was last asked no longer does because the reset it named has passed:
        then what it said then says nothing now, and the tool is asked again at once. A figure that names no reset is not
        asked for early: the meter refuses on it once the store's week ends, and the tool is asked at its next refresh."""
        now = utc(now)
        return self._asked is not None and any(_holds(standing(r, self._asked)) and not _holds(standing(r, now))
                                               for r in self.readings.values())

    def refresh(self, now: datetime) -> None:
        now = utc(now)
        if self._asked is not None and now - self._asked < REFRESH and not self._lapsed(now):
            return
        self._asked = now
        for provider in set(self.windows) & set(self.ask):
            try:
                answer = self.ask[provider]()
                found = limits_reading({} if answer is None else answer, self.windows[provider], now)
            except (OSError, UsageUnavailable) as exc:
                self._no_reading(provider, now, f"its usage could not be read ({str(exc)[:120]})")
                continue
            except Exception as exc:  # noqa: BLE001 - a reader that fails any other way says nothing new either
                self._no_reading(provider, now, f"its usage could not be read ({kind_of(exc)})")
                continue
            if found is None:
                self._no_reading(provider, now, "its reply could not be read")
                continue
            known = self.readings.get(provider)
            if found.estimated and known is not None and not known.estimated and self._stands(provider, known, now) is known:
                limits = (at for at in (known.limited_until, found.limited_until) if at is not None)   # no week in it
                self.readings[provider] = replace(known, limited_until=max(limits, default=None))
                self._no_reading(provider, now, "its reply gave no week")
                continue
            taken = replace(found, read_at=now)
            if not self._told(provider, taken):          # the store first: nobody holds a reading it does not know of
                self._no_reading(provider, now, "its reading could not be recorded in the store")
                continue
            self.readings[provider] = taken
            self.said[provider] = f"reported by its own tool at {now.isoformat(timespec='minutes')}"
            if found.resets_at is not None:
                self.said[provider] += f"; its week ends at {found.resets_at.isoformat(timespec='minutes')}"

    def read_now(self, now: datetime) -> None:
        """Ask the tool whenever it was last asked: the one read a tick makes, just before a held-out evaluation."""
        now = utc(now)
        self._asked = None
        self.refresh(now)

    def _no_reading(self, provider: str, now: datetime, why: str) -> None:
        """No reading never replaces the last good one: it stands while ``standing`` keeps it, and ``read_at`` stays as it
        was, so the meter goes on adding what is charged after it. When nothing it said stands, nothing known of the account
        is left: the reading says why (``unread``), and the meter admits nothing on it until a read succeeds, as the ledger
        alone holds only G6's own use and would read low."""
        now = utc(now)
        known = self.readings.get(provider)
        kept = self._stands(provider, known, now)
        if not _holds(kept):
            self.readings[provider] = AccountReading(unread=why)
            self.said[provider] = (f"{why}, and nothing it reported still stands, so nothing is reserved on it until it "
                                   "reads again")
        elif kept.limited_until is not None and kept.limited_until > now:
            self.said[provider] = f"{why}; the limit it last reported stands until {kept.limited_until.isoformat()}"
        elif kept.resets_at is not None:
            self.said[provider] = (f"{why}; the last figure stands until {kept.resets_at.isoformat()}, with the model calls "
                                   "charged since on top")
        else:
            self.said[provider] = f"{why}; what it last reported stands until its week ends or a read replaces it"

    def __call__(self, provider: str, now: datetime) -> AccountReading | None:
        """What was last read of ``provider``, as it stands at ``now`` (``_stands``, the one rule)."""
        now = utc(now)
        return self._stands(provider, self.readings.get(provider), now)

__all__ = ["AT_CAPACITY", "AccountReader", "Gate", "MODEL_REFUSED", "NOT_REPORTED", "ProviderStop", "REFRESH", "Request",
           "UsageUnavailable", "WatchedClaude", "WatchedCodex", "claude_said", "codex_limits", "codex_said", "limit_ends",
           "limits_reading", "plain_text", "read_limits", "read_stops", "refused_by", "request_digest", "request_identity",
           "Send", "telling_stops", "watched"]
