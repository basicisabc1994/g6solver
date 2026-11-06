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
  before it is cut: as a request goes out, the caller really sending it becomes a ``WatchedClaude`` or a ``WatchedCodex``
  (``read_stops``), whatever holds it. What that reads goes straight to the loop (``telling_stops``) before anything else
  sees it, so a wrapper that catches failed requests cannot retry it or hide it. For a watched caller only those structured
  fields decide: the older text pattern (``_LIMIT``) is never applied to what it raises, stderr included.
* **What a host request is.** A refused request is known by one identity, ``request_identity``: the digest of the exact
  arguments and input the program is started with, taken at the last step the bound caller gives a subclass before the
  program starts (``_command``, whose output ``host_models._run`` receives unchanged; ``_launch`` for a session of a caller's
  own, as ``ToolSession`` is). The loop checks it there, against what was refused and against an identical request still out,
  under one lock (``telling_stops``' gate), which hands back the send's one record (``Send``), made once it may leave from the
  bytes as they are then. The record stays with the call's own folder until the reply that reads its outcome, which alone
  releases it. What is left between that step and the start is the building of the program's environment, with no wait in
  it; closing it needs a step in the bound file itself, at an issuance window.
* Claude Code: its JSON result (``host_models._result``). A refusal is ``stop_reason`` ``refusal``, an ``api_error`` of
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
scores no case for either arm. What reads as none of these is a failed case, and the tick runs on as it always has.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from g6.core import component_process, host_models
from g6.core.llm import RATE_LIMITED, LLMError
from g6.core.outside import clean_environment, find_program
from g6.core.process_tree import ProcessTreeError
from g6.self_maintenance.pacing import AccountReading, ProviderWindow

REFRESH = timedelta(minutes=15)
MODEL_REFUSED = "G6_E_MODEL_REFUSED"
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

    def sending(self, identify: Callable[[], str], sender: Any) -> Send: ...

    def told(self, said: str, record: Send | str) -> None: ...

    def done(self, record: Send) -> None: ...


_TELL: contextvars.ContextVar[Gate | None] = contextvars.ContextVar("g6_loop_stops", default=None)
#: Each send's own record and the gate it went through, by the call's own folder, from the step that sends to the reply that
#: reads its outcome: a call nested inside another, or on another thread, has a folder of its own. Only a send's own outcome
#: takes its entry out (``_forget``); nothing else evicts one.
_SENT: dict[str, tuple[Send, Gate | None]] = {}
_SENT_LOCK = threading.Lock()
#: The folders of the sends a watched call made, so the end of that call releases any whose program never answered.
_OPEN: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("g6_open_sends", default=None)
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
    one with no reset is, and that look costs one refused request. A model at capacity is such a limit."""
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


def _codex_turn(printed: str) -> tuple[str, bool]:
    """What Codex's own JSON events (``codex exec --json``) say of the turn: the stop it names, in G6's own words, or ``""``,
    and whether the turn failed explicitly. The stream is split as Codex writes it, one event to a line ended by ``\\n``: a
    line separator inside a JSON string is part of that string. Only its own events are read: ``error``, and the ``error`` of
    ``turn.failed`` (or of a ``turn.completed`` that carries one). A refusal anywhere among them is a refusal, since what was
    refused is never sent again whatever came after it. Otherwise the last limit or capacity named decides, unless the turn
    then completed with no error, since then it answered. A ``turn.failed``, or an ``error`` of no kind G6 knows exactly, is a
    failure whatever follows. A line that is no JSON object, or a field of the wrong kind, says nothing."""
    refused, said, answered, failed = "", "", False, False
    for line in printed.split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        error = event if kind == "error" else event.get("error") if kind in ("turn.failed", "turn.completed") else None
        if kind == "turn.completed" and error is None:
            answered = True
            continue
        failed = failed or kind == "turn.failed"
        stop = _codex_kind(error) if isinstance(error, Mapping) else ""
        if stop == "refusal":
            refused = _CODEX_SAID[stop]
        elif stop:
            said, answered = _CODEX_SAID[stop], False
        elif kind in ("error", "turn.completed"):
            failed = True
    return refused or ("" if answered else said), failed


def codex_said(printed: str) -> str:
    """What Codex's own JSON events say stopped the turn, in G6's own words, or ``""`` (``_codex_turn``)."""
    return _codex_turn(printed)[0]


def request_digest(system: str, user: str) -> str:
    """What a request to a model is known by once refused, when no host program's input can be seen: no text of it is kept.
    A pair with no NUL and no lone surrogate in it is digested as it always was, its two parts joined by one NUL, so what was
    refused before is still known. Any other pair is digested from its bytes, each part as UTF-8 with its surrogates kept as
    they are and framed by its length, behind two NULs, which no such join can begin with: no two pairs share a digest, and a
    surrogate pair is not the character it would stand for."""
    try:
        if "\0" not in system and "\0" not in user:
            return hashlib.sha256(f"{system}\0{user}".encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        pass
    framed = b"\0\0"
    for part in (system, user):
        data = part.encode("utf-8", "surrogatepass")
        framed += len(data).to_bytes(8, "big") + data
    return hashlib.sha256(framed).hexdigest()


def request_identity(argv: Sequence[str], stdin: str, root: str | Path | None) -> str:
    """What a host request is known by: the digest of the arguments a program is started with, after the program itself, and
    of its input, exactly as given. A path in the call's own folder (``root``) stands for the bytes of the file there, which
    G6 wrote for the program to read, or for its place in that folder where there is no file yet, as where the program
    writes; any other argument stands as it is. So the same bytes are one request, whichever call or rendering made them."""
    base = os.path.normcase(os.path.abspath(root)) if root is not None else None
    parts: list[list[str]] = []
    for arg in list(argv)[1:]:
        where = os.path.normcase(os.path.abspath(arg)) if os.path.isabs(arg) else ""
        if base and where and (where == base or where.startswith(base + os.sep)):
            place = os.path.relpath(where, base).replace(os.sep, "/")
            try:
                parts.append(["file", place, hashlib.sha256(Path(arg).read_bytes()).hexdigest()])
            except OSError:
                parts.append(["path", place])
        else:
            parts.append(["arg", arg])
    return hashlib.sha256(json.dumps(["host", parts, stdin]).encode("ascii")).hexdigest()


@dataclass(frozen=True, eq=False)
class Send:
    """One send, as the loop let it out: made once, after any wait, and never changed. ``identity``: what left, read from its
    bytes at that moment; ``watched``: whether its caller reads its program's output itself, so only that decides for it;
    ``owner``: the call that made it, and ``thread`` where; ``generation``: the tick; ``sender``: the caller that sent it;
    ``places``: what it was charged as it was let out, set once, so no call charges it again."""

    identity: str | None
    watched: bool
    owner: object
    generation: int
    thread: int
    sender: Any = None
    places: int = 0


@contextmanager
def telling_stops(gate: Gate) -> Iterator[None]:
    """Inside, a watched caller asks ``gate`` before its program starts (``sending``), which hands back the send's record or
    raises to keep it in; a stop it reads goes to ``gate`` under that record before anything else sees it (``told``); and the
    record is released by its own outcome (``done``). What any of these raises is what stops the request, so a wrapper between
    it and the loop cannot take a refusal or a limit for a failed request and send it again."""
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


def _sending(caller: Any, argv: Sequence[str], stdin: str, files: Any) -> None:
    """The last step before a watched caller's program starts: the loop's gate lets it out or keeps it in, and makes the send's
    record from the bytes as they are once it is let out, which stays with this call's own folder until its outcome is read."""
    root = Path(files).parent if files is not None else None
    gate = _TELL.get()
    if gate is None:
        record = Send(request_identity(argv, stdin, root), True, None, 0, threading.get_ident(), caller)
    else:
        record = gate.sending(lambda: request_identity(argv, stdin, root), caller)
    with _SENT_LOCK:
        _SENT[str(files)] = (record, gate)
    opened = _OPEN.get()
    if opened is not None:
        opened.append(str(files))


def _forget(files: Any) -> tuple[Send | None, Gate | None]:
    """This send's own record, taken out of where it waited for its outcome, and released by that outcome."""
    with _SENT_LOCK:
        record, gate = _SENT.pop(str(files), (None, None))
    return record, gate


def _received(hide: tuple[str, ...]) -> str:
    """What a reply read with no step that sends before it was given, as the bound caller says: its system text and request,
    or a prompt alone. Such a request was never checked as it left, so this serves the record and nothing else."""
    return request_digest(*hide[:2]) if len(hide) > 1 else request_digest("", hide[0] if hide else "")


def _stopped(said: str, files: Any, hide: tuple[str, ...]) -> None:
    """Tell the loop what the provider said of the send this reply is of, then raise it as the request's failure: before the
    bound caller cuts what the program printed down to its end. The send's record is released only once that is told."""
    record, gate = _forget(files) if files is not None else (None, None)
    try:
        if said:
            told = gate if gate is not None else _TELL.get()
            what: Send | str = record if record is not None else _received(hide)
            if told is not None:
                told.told(said, what)
            raise ProviderStop(said, what)
    finally:
        if record is not None and gate is not None:
            gate.done(record)


def _released(files_keys: Sequence[str]) -> None:
    """Sends of a call that ended with no reply read: each is released by that outcome, and no other send is."""
    for key in files_keys:
        record, gate = _forget(key)
        if record is not None and gate is not None:
            gate.done(record)


class _Watched:
    """What both watched callers do around the bound call: a send whose program never answers is released as the call ends."""

    def complete(self, *args: Any, **kwargs: Any) -> str:
        token = _OPEN.set([])
        try:
            return super().complete(*args, **kwargs)
        finally:
            _released(_OPEN.get())
            _OPEN.reset(token)


class WatchedClaude(_Watched, host_models.ClaudeCodeCaller):
    """Claude Code as the loop calls it: as the bound caller, checked as it sends, and a stop in its result raises G6's own
    code."""

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        argv, stdin = super()._command(program, system, user, work, files)
        _sending(self, argv, stdin, files)
        return argv, stdin

    def _launch(self, argv: list[str], stdin: str, cwd: Path, files: Path, env: dict[str, str], seconds: float) -> Any:
        """A session of a caller's own that starts its program itself names that step ``_launch`` (``ToolSession``)."""
        _sending(self, argv, stdin, files)
        try:
            return super()._launch(argv, stdin, cwd, files, env, seconds)
        except BaseException:
            _released((str(files),))
            raise

    def _reply(self, code: int, printed: str, errors: str, files: Any, hide: tuple[str, ...]) -> str:
        _stopped(claude_said(host_models._result(printed) or {}), files, hide)
        return super()._reply(code, printed, errors, files, hide)


class WatchedCodex(_Watched, host_models.CodexCaller):
    """Codex as the loop calls it: as the bound caller, checked as it sends, printing its events as JSON, and a stop in them
    raises G6's own code. A turn it reports as failed is a failed request. The reply is still the last message Codex writes."""

    def argv(self, program: str, *, work: str, last_message: str) -> list[str]:
        args = super().argv(program, work=work, last_message=last_message)
        if "exec" not in args:            # no command of Codex's that prints events: nothing it prints is read
            return args
        at = args.index("exec") + 1
        return [*args[:at], "--json", *args[at:]]

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        argv, stdin = super()._command(program, system, user, work, files)
        _sending(self, argv, stdin, files)
        return argv, stdin

    def _reply(self, code: int, printed: str, errors: str, files: Any, hide: tuple[str, ...]) -> str:
        said, failed = _codex_turn(printed)
        _stopped(said, files, hide)
        if failed:
            raise LLMError(f"{host_models.HOST_FAILED}: codex exited {code} and reported that its turn failed, of no kind "
                           "G6 reads as a stop")
        return super()._reply(code, printed, errors, files, hide)


#: Each host caller the loop watches, and what it becomes.
_WATCHED = ((host_models.ClaudeCodeCaller, WatchedClaude), (host_models.CodexCaller, WatchedCodex))


def read_stops(sender: Any) -> None:
    """Have ``sender``, when it is any kind of Claude Code or Codex caller, read its program's whole output. It is done to the
    caller that is sending, as it sends, so what holds it does not matter. Its class changes and nothing else does: a
    subclass of a domain's own keeps all it has, and reads the output first."""
    kind = type(sender)
    for host, watched_kind in _WATCHED:
        if isinstance(sender, host) and not isinstance(sender, watched_kind):
            sender.__class__ = type(kind.__name__, (watched_kind, kind), {})


def watched(sender: Any) -> bool:
    """Whether ``sender`` reads its program's output itself, so only what it reads from the structured fields decides."""
    return isinstance(sender, WatchedClaude | WatchedCodex)


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


def _reset(window: Mapping[str, Any], now: datetime) -> datetime | None:
    """When a window resets: ahead of ``now`` and no further than the window is long, or a week where it does not say. Any
    other figure is no reset: a limit that outlasts its own window cannot be believed."""
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

    def _reset_since(self, now: datetime) -> bool:
        """Whether a week has ended since the tool was last asked, so what it said then says nothing of this one."""
        return any(r.resets_at is not None and self._asked < r.resets_at <= now for r in self.readings.values())

    def refresh(self, now: datetime) -> None:
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
        self._asked = None
        self.refresh(now)

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


__all__ = ["AT_CAPACITY", "AccountReader", "Gate", "MODEL_REFUSED", "NOT_REPORTED", "ProviderStop", "REFRESH",
           "UsageUnavailable", "WatchedClaude", "WatchedCodex", "claude_said", "codex_limits", "codex_said", "limit_ends",
           "limits_reading", "read_limits", "read_stops", "refused_by", "request_digest", "request_identity",
           "Send", "telling_stops", "watched"]
