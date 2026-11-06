# SPDX-License-Identifier: Apache-2.0
"""What the worked examples' scripts share (PLAN.md): the archive outside the repository, the prices and the $100 stop,
the key's mask, and the tally of Claude Code's stream.

The archive is ``G6_WE_ARCHIVE`` when set, else the local artifacts folder on the Desktop (``Path.home()`` based, so no
private path is written here). Each run is a write-once folder; ``runs.jsonl`` at the root lists every run started and
ended, with its cost, and the stop reads it.
"""
from __future__ import annotations

import collections
import datetime as dt
import hashlib
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Callable, Container, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

MODEL = "claude-sonnet-5-5"
CAP_USD = 100.00
RUN_CAP_USD = 10.00
RUN_STOP_USD = 10.50          # the runner's own stop: past this, a run is ended
MAX_TURNS = 200
WALL_CLOCK_S = 120 * 60
#: Commits G6 must contain for a paid run (PLAN.md sections 4 and 9): the launch fix pass's Critical commit on the
#: confined run's read paths (5dad0fe), and item L1 (2680cc9: an environment linked to G6's own Python refused up
#: front; G6's own environment made with --copies), both on origin/claude/launch on 2026-10-29. From run 6, also
#: a923002, the merge of the worked examples' launch fixes W1 to W5 (PLAN.md section 9, option B). From round 2, also
#: 9ad134d, with the security review's fix pass F1 to F6 and F8 (PLAN.md section 9, "Round 2").
REQUIRED_G6_COMMITS: tuple[str, ...] = ("5dad0fe7e0b933b274cd44443b449bef3864627a",
                                        "2680cc9d1afc9fa7c873b411573bd70c6134640b",
                                        "a9230022ebf080859411aa5dc0c8ea3c42687d45",
                                        "9ad134d85dc543774057f427425cf543af923aa4")
#: Anthropic's published prices, US dollars per million tokens, read on 2026-10-29 from
#: https://platform.claude.com/docs/en/about-claude/pricing (PLAN.md §6). Haiku 5.5 at its prices for prompts over
#: 100,000 tokens, which err high.
PRICES: dict[str, dict[str, float]] = {
    "claude-sonnet-5-5": {"input": 2.00, "write_5m": 2.50, "write_1h": 4.00, "hit": 0.10, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "write_5m": 1.25, "write_1h": 2.00, "hit": 0.10, "output": 5.00},
    "claude-haiku-5-5": {"input": 0.50, "write_5m": 0.625, "write_1h": 1.00, "hit": 0.05, "output": 2.50},
    "claude-opus-5-5": {"input": 4.00, "write_5m": 5.00, "write_1h": 8.00, "hit": 0.20, "output": 20.00},
}
#: A model the table does not name is charged at the highest price on the page for each kind of token.
UNKNOWN_PRICE = {"input": 10.00, "write_5m": 12.50, "write_1h": 20.00, "hit": 1.00, "output": 50.00}
PRICE_SOURCE = {"url": "https://platform.claude.com/docs/en/about-claude/pricing", "read": "2026-10-29"}


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def archive_root(given: Path | None = None, environ: Mapping[str, str] | None = None) -> Path:
    raw = (os.environ if environ is None else environ).get("G6_WE_ARCHIVE", "")
    if given is not None:
        return Path(given)
    if raw.strip():
        return Path(raw)
    return Path.home() / "Desktop" / "g6-continuation-artifacts" / "worked-examples"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


#: Every exit a command's own run can end with, for a call whose caller reads that exit itself, next (``returncode``): the
#: command's no is an outcome there, as ``git apply``'s "does not apply" is. A status outside it is no exit of the command's.
EXITS = range(256)
#: ``git apply``'s own exits: 0 applied, 1 it does not apply, 128 no patch git can read (an empty or garbled one). A candidate's
#: patch that does not apply is a failed patch; any other status (a failed exec: 125 to 127, a signal's 128 + n) raises.
APPLY_EXITS = (0, 1, 128)


def _text(data: Any) -> str:
    data = data or b""
    return (data if isinstance(data, str) else data.decode("utf-8", errors="replace")).strip()


def completed(cp: Any, what: str, *, ok: Container[int] = (0,)) -> Any:
    """The one reader of every command's result the study reads (a docker exec, run, cp or image call; git, gh, node or the runner
    itself): its status first, and any status ``ok`` does not name, or none, raised as a failure (RuntimeError), never read as
    output (study review 2, findings 6, 7 and 11: a failed exec whose output looked like a success). ``ok`` is a success alone,
    unless the command's own non-zero exit is an outcome its caller reads next (``EXITS``, or the exits named). Every call site is
    listed, from the code, by tests/validation/test_command_results.py."""
    code = getattr(cp, "returncode", None)
    if not isinstance(code, int) or isinstance(code, bool) or code not in ok:
        said = (_text(getattr(cp, "stdout", b"")) + " " + _text(getattr(cp, "stderr", b""))).strip()
        raise RuntimeError(f"{what} failed ({code}): {said[-600:]}")
    return cp


#: The last line a command run in a container with its own report (``reporting``, ``run.Docker.sh(..., own_exit=True)``) prints,
#: naming its own exit. Only the shell that ran the command in the container prints it, after the command ended; the container
#: runtime prints nothing of the kind, and its own failures (a stopped or missing container, a killed exec, the daemon's error) end
#: the docker command with 1 or 125 to 127, exits a command has too (study review 3, findings 1 and 7).
OWN_EXIT = "g6we-own-exit="


def reporting(script: str) -> str:
    """``script`` as a shell runs it with its own report: its exit named on the last line of its output, then the shell exits with it."""
    return f'{script}\ncode=$?; printf "\\n{OWN_EXIT}%s\\n" "$code"; exit "$code"'


def own_exit(cp: Any, what: str, *, ok: Container[int]) -> Any:
    """The one reader of a command's own exit in a container, for a site that reads an exit but 0 as the command's answer (``git
    apply``'s "does not apply", ``cat``'s "no such file"). A 0 is the command's: the runtime ends its own failures with a status
    but 0. Any other status is the command's only when the command's own report (``OWN_EXIT``, its output's last line) names that
    very status; with none, the runtime failed, the command's answer is unknown, and it raises (RuntimeError), so a caller records
    nothing, never a "no". A reported exit ``ok`` does not name raises too. Every site is listed by tests/validation/
    test_command_results.py."""
    code = getattr(cp, "returncode", None)
    if not isinstance(code, int) or isinstance(code, bool):
        return completed(cp, what, ok=ok)
    if code != 0:
        data = getattr(cp, "stdout", b"") or b""
        data = data.encode("utf-8", "surrogateescape") if isinstance(data, str) else data
        if data.rstrip(b"\n").rpartition(b"\n")[2] != f"{OWN_EXIT}{code}".encode():
            said = _text(getattr(cp, "stderr", b""))
            raise RuntimeError(f"{what}: no exit of its own was reported, so the container runtime failed ({code}) and its answer "
                               f"is unknown: {said[-600:]}")
    return completed(cp, what, ok=ok)


def told(cp: Any) -> bytes:
    """A reported command's output (``reporting``) without its report."""
    data = getattr(cp, "stdout", b"") or b""
    data = data.encode("utf-8", "surrogateescape") if isinstance(data, str) else data
    head, _, last = data.rstrip(b"\n").rpartition(b"\n")
    return head if last.startswith(OWN_EXIT.encode()) else data


def price_of(model: str) -> dict[str, float]:
    name = str(model or "")
    for known, price in PRICES.items():
        if name == known or name.startswith(known + "-") or name.startswith(known + "["):
            return price
    return UNKNOWN_PRICE


def usage_cost(usage: Mapping[str, Any], model: str = MODEL) -> float:
    """One response's usage at the published price. A cache write whose duration the usage does not give is charged
    at the 1-hour price (PLAN.md §6)."""
    p = price_of(model)
    writes = int(usage.get("cache_creation_input_tokens") or 0)
    split = usage.get("cache_creation") or {}
    w5 = int(split.get("ephemeral_5m_input_tokens") or 0) if isinstance(split, Mapping) else 0
    w1 = int(split.get("ephemeral_1h_input_tokens") or 0) if isinstance(split, Mapping) else 0
    rest = max(0, writes - w5 - w1)
    dollars = (int(usage.get("input_tokens") or 0) * p["input"] + w5 * p["write_5m"] + (w1 + rest) * p["write_1h"]
               + int(usage.get("cache_read_input_tokens") or 0) * p["hit"]
               + int(usage.get("output_tokens") or 0) * p["output"])
    return dollars / 1e6


def model_usage_cost(model_usage: Mapping[str, Any]) -> float:
    """Claude Code's result record's ``modelUsage`` (per model: inputTokens, outputTokens, cacheReadInputTokens,
    cacheCreationInputTokens) at the published prices, cache writes at the 1-hour price."""
    total = 0.0
    for model, u in (model_usage or {}).items():
        if not isinstance(u, Mapping):
            continue
        total += usage_cost({"input_tokens": u.get("inputTokens"), "output_tokens": u.get("outputTokens"),
                             "cache_read_input_tokens": u.get("cacheReadInputTokens"),
                             "cache_creation_input_tokens": u.get("cacheCreationInputTokens")}, model)
    return total


# --- the key's mask ----------------------------------------------------------------------------------------------------
WITHHELD = "[key withheld]"


class Redactor:
    """Replaces the key's value (and any ``sk-ant-`` token) in text bound for a file or a terminal."""

    PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")

    def __init__(self, *secrets: str) -> None:
        self.secrets = tuple(s for s in secrets if s and len(s) >= 8)

    def __call__(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, WITHHELD)
        return self.PATTERN.sub(WITHHELD, text)

    def leaks(self, text: str) -> bool:
        return any(s in text for s in self.secrets) or bool(self.PATTERN.search(text))


# --- Claude Code's stream ----------------------------------------------------------------------------------------------
#: Every newline-delimited JSON file the runs write, by its kind, named as its writer names it (``{tag}``, ``{n}`` and ``{stem}``
#: filled in by the writer). Every writer and reader in the worked examples takes the name from here, and reads the file only
#: through ``read_stream`` or ``stream_lines`` (tests/validation/test_host_author_followup.py holds both).
NDJSON = {
    "index": "runs.jsonl",                          # every run started and ended (``Archive.append``)
    "ledger": "ledger.jsonl",                       # round 3's spending, a line a run (``run.r3_ledger``)
    "egress": "egress.jsonl",                       # the egress proxy's log, copied out of its container (``run.egress``)
    "stream": "stream.jsonl",                       # a run's Claude Code session, in its container or on the host
    "tagged_stream": "{tag}_stream.jsonl",          # a scout's or a critic's session (``run.run_scout``)
    "numbered_stream": "{tag}_stream_{n}.jsonl",    # a repair's session, or a scout's or a critic's second
    "stderr": "{stem}_stderr.txt",                  # a host session's standard error, beside its stream (``run.host_session``)
    "cases": "cases.jsonl",                         # the reviewer cases ``export_reviewer_cases`` writes
}


def stream_lines(text: str) -> list[str]:
    """Newline-delimited JSON as its lines: every file ``NDJSON`` names, Claude Code's stream and a host session's stderr,
    ``runs.jsonl``, the ledger and the egress log among them. It is split at "\\n" alone, never where ``str.splitlines`` splits too
    (U+2028, U+2029, U+0085 and the like, which a JSON string holds as they are): no part of a record is ever read as a line of
    its own."""
    return text.split("\n")


def read_stream(path: Path, *, errors: str = "strict") -> list[str]:
    """A newline-delimited JSON file's lines (``stream_lines``): its bytes decoded as UTF-8 with no newline translation, so a
    "\\r" is never read as a line's end either. ``errors``: the decoder's, strict unless a reader that skips a torn last line
    asks otherwise (``Archive.rows``); a "\\n" is one byte that no other character's UTF-8 holds, so the lines are the same."""
    return stream_lines(Path(path).read_bytes().decode("utf-8", errors))


@dataclass
class Tally:
    """What Claude Code's stream-json said: model calls (distinct assistant messages, sub-agents included) with their
    model and usage, and the result record."""

    usage_by_message: dict[str, tuple[str, dict]] = field(default_factory=dict)
    result: dict | None = None
    session_id: str = ""
    lines: int = 0
    #: Round 3 (PLAN.md section 9): Claude Code's own ``total_cost_usd`` counts too, so a model the price table does not
    #: name (Fable 5.1) is never counted below what Claude Code itself reports.
    count_reported: bool = False

    def feed(self, obj: Mapping[str, Any]) -> None:
        self.lines += 1
        self.session_id = str(obj.get("session_id") or self.session_id)
        if obj.get("type") == "assistant":
            msg = obj.get("message") or {}
            key = str(msg.get("id") or f"line-{self.lines}")
            model = str(msg.get("model") or MODEL)
            if msg.get("usage"):
                self.usage_by_message[key] = (model, dict(msg["usage"]))
            else:
                self.usage_by_message.setdefault(key, (model, {}))
        elif obj.get("type") == "result":
            self.result = dict(obj)

    @property
    def model_calls(self) -> int:
        return len(self.usage_by_message)

    @property
    def stream_usd(self) -> float:
        return sum(usage_cost(u, m) for m, u in self.usage_by_message.values())

    @property
    def result_usd(self) -> float | None:
        if not self.result or not self.result.get("modelUsage"):
            return None
        return model_usage_cost(self.result["modelUsage"])

    @property
    def reported_usd(self) -> float | None:
        value = (self.result or {}).get("total_cost_usd")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @property
    def cost_usd(self) -> float:
        """The larger of the stream's count and the result's per-model usage (PLAN.md §6); in round 3 also Claude
        Code's reported total."""
        reported = (self.reported_usd or 0.0) if self.count_reported else 0.0
        return round(max(self.stream_usd, self.result_usd or 0.0, reported), 6)

    @property
    def usage(self) -> dict[str, int]:
        keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        return {k: sum(int(u.get(k) or 0) for _, u in self.usage_by_message.values()) for k in keys}


# --- the archive and the stop -----------------------------------------------------------------------------------------
class ArchiveError(RuntimeError):
    """A write the archive refuses: a run folder or a file that already exists."""


@dataclass
class RunFolder:
    archive: Archive
    path: Path
    rel: str
    #: How a status becomes the run's ending, for a run whose end is decided from durable state (arm K3: ``run.ended_as``): every
    #: ending this folder writes (``end``, so ``failed`` and ``finish`` too) goes through it, so an ending written early says what
    #: the run's end would say. None: the status as it is.
    ending_rule: Callable[[str], str] | None = field(default=None, repr=False, compare=False)

    def write(self, name: str, data: str | bytes) -> Path:
        target = self.path / name
        if target.exists():
            raise ArchiveError(f"{self.rel}/{name} exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = self.archive.redact(data).encode("utf-8")
        target.write_bytes(data)
        return target

    def write_json(self, name: str, obj: Any) -> Path:
        return self.write(name, json.dumps(obj, indent=1, ensure_ascii=False, default=str))

    def fresh_path(self, name: str) -> Path:
        target = self.path / name
        if target.exists():
            raise ArchiveError(f"{self.rel}/{name} exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def end(self, status: str, *, why: str = "", agent_started: bool = True) -> None:
        """The run's ending, in a file of its own (``ENDING``), written the moment it is decided and never written again: an ending
        decided later is not written over it, and a failure after it is recorded beside it (``failed``)."""
        if not (self.path / ENDING).exists():
            status = self.ending_rule(status) if self.ending_rule is not None else status
            self.write_json(ENDING, {"status": status, "why": why, "agent_started": agent_started, "at": now()})

    def failed(self, why: str, *, agent_started: bool) -> None:
        """A failure: the run's ending ("error") when none was decided before it, else recorded beside the ending, once
        (``AFTER``), which can only turn a scoreable ending into "error" (``ending``)."""
        if not (self.path / ENDING).exists():
            self.end("error", why=why, agent_started=agent_started)
        elif not (self.path / AFTER).exists():
            self.write_json(AFTER, {"why": why, "at": now()})

    def finish(self, record: Mapping[str, Any], status: str) -> dict[str, Any]:
        """The run's record and its end in the index. A host run's status is its ending (``end``, ``ending``), written first if it
        was not decided before, so a record and an index left unwritten by a stop still have it."""
        if record.get("executor") == "host":
            self.end(status, why=str(record.get("why") or ""), agent_started=bool(record.get("agent_started")))
            status = (ending(self.archive, self.rel) or {}).get("status") or status
        body = {**record, "status": status, "ended_at": now()}
        self.write_json("run.json", body)
        self.archive.append({"event": "ended", "run": self.rel, "status": status, "at": body["ended_at"],
                             "cost_usd": float(record.get("cost_usd") or 0.0),
                             "agent_started": bool(record.get("agent_started"))})
        return body


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """One writer of ``path`` at a time, across processes: an exclusive lock on a sidecar file beside it (``.lock``), as
    ``g6.workforce.receipt_store`` takes one (``msvcrt.locking`` on Windows, ``fcntl.flock`` elsewhere). Waits up to five
    seconds, then raises the lock's own error."""
    with path.with_name(path.name + ".lock").open("a+b") as handle:
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Archive:
    def __init__(self, root: Path, redact: Redactor | None = None) -> None:
        self.root = Path(root)
        self.redact = redact or Redactor()

    @property
    def index(self) -> Path:
        return self.root / NDJSON["index"]

    def append(self, row: Mapping[str, Any]) -> None:
        """One row written whole at the index's end, under its lock (``_locked``: one writer at a time, across processes). A tail
        a write cut short (the bytes after the index's last "\\n") is first moved, exactly, to a side file beside the index
        (``runs.jsonl.torn-<UTC stamp>``, never written over), and the index cut back to its last "\\n": so the row is never
        joined to a torn one, and the torn bytes are kept, not lost. A newline alone would not do: the torn line would then end
        with one and, by ``rows``'s rule, be an error."""
        self.root.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(dict(row), ensure_ascii=False) + "\n").encode("utf-8")
        with _locked(self.index), open(self.index, "a+b") as stream:
            stream.seek(0)
            data = stream.read()
            tail = data[data.rfind(b"\n") + 1:]
            if tail:
                stamp, n = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ"), 0
                while True:
                    side = self.index.with_name(f"{self.index.name}.torn-{stamp}" + (f"-{n}" if n else ""))
                    try:
                        with open(side, "xb") as kept:
                            kept.write(tail)
                        break
                    except FileExistsError:
                        n += 1
                stream.truncate(len(data) - len(tail))
            stream.write(line)
            stream.flush()

    def rows(self) -> list[dict[str, Any]]:
        """Every row of the index, for every reader, framed by ``read_stream``. Only a tail the file does not end with (no "\\n"
        at the very end of its bytes), which a write cut short leaves, is skipped; the next ``append`` moves it aside. Every line
        the index ends, last or not, must be read, and one that cannot is an error. Its bytes are read once: a byte that is not
        UTF-8 is held as an escape (``surrogateescape``), and a line holding one cannot be read, as a line that is not JSON
        cannot. Whether a line is a row is JSON's to say alone (``json.loads``), never Python's own reading of whitespace
        (``str.strip``, which takes U+2028 and the like for blank): a complete line that is not one JSON value, an empty one
        included, is an error (review 4, finding 4)."""
        if not self.index.is_file():
            return []
        lines = read_stream(self.index, errors="surrogateescape")
        lines.pop()                         # what follows the last "\n": "" when the index ends with one, else a torn tail
        rows = []
        for line in lines:
            line.encode("utf-8")            # strict: an escaped byte is a UnicodeEncodeError, a ValueError
            rows.append(json.loads(line))
        return rows

    def start(self, kind: str, task: str, **meta: Any) -> RunFolder:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
        rel, n = f"{kind}/{task}/{stamp}", 0
        while (self.root / rel).exists():          # the clock can repeat itself within a microsecond on Windows
            n += 1
            rel = f"{kind}/{task}/{stamp}-{n}"
        path = self.root / rel
        path.mkdir(parents=True)
        self.append({"event": "started", "run": rel, "kind": kind, "task": task, "at": now(), **meta})
        return RunFolder(self, path, rel)


def api_need(run: Mapping[str, Any]) -> float:
    """What a run may spend of the API's money, the one figure round 3's admission, the run's start record, ``spent`` and its
    sessions' cost stop take: nothing for a host run (``executor`` "host"), whose sessions are the subscription's (PLAN.md,
    2026-11-04); else its own stop (``stop_usd``, 0 included; ``RUN_STOP_USD`` when it gives none)."""
    if run.get("executor") == "host":
        return 0.0
    stop = run.get("stop_usd")
    return RUN_STOP_USD if stop is None else float(stop)


def spent(rows: Iterable[Mapping[str, Any]]) -> float:
    """Dollars spent: every ended run's cost; a run started and never ended counts what it may spend of the API's money
    (``api_need``: its whole stop, or nothing for a host run) until it is closed with its stream's cost."""
    rows = list(rows)
    ended = {r["run"]: float(r.get("cost_usd") or 0.0) for r in rows if r.get("event") == "ended"}
    total = sum(ended.values())
    for r in rows:
        if r.get("event") == "started" and r.get("kind") == "run" and r["run"] not in ended:
            total += api_need(r)
    return round(total, 6)


#: How a run ends with no verdict to give (PLAN.md, 2026-11-04): at the subscription's usage limit, or refused by G6 itself,
#: before or at its session's start. Such a run is never scored, and may be made again. A run a model refusal ended
#: ("model_refused") was made, and is scored (``ON_PATCH``).
UNSCORED = ("limited", "refused")
#: How a made run ends when what is scored is its final patch: it finished, or a model refusal ended it, as Claude Code alone
#: would leave it (PLAN.md, runtime-k ec0f761). Any other made run counts as failed.
ON_PATCH = ("finished", "model_refused")
#: Every ending that has a verdict to give, by name: on the final patch (``ON_PATCH``), else a failure, which needs no check (an
#: error, a cap or the clock reached, or a run its key check, its tree or its arm stopped before its agent, as they always were).
#: Any status not named here, or none, has no verdict (``scoreable``).
SCORED = (*ON_PATCH, "error", "timeout", "cap_turns", "cap_cost", "key_check_failed", "tree_mismatch", "arm_mismatch")
#: A run's ending, in its folder, written once the moment it is decided (``RunFolder.end``); and a failure after it (``AFTER``).
ENDING = "ending.json"
AFTER = "failed_after_ending.json"
#: What every saved verdict records (``loop.one``'s row, ``loop.rescore``'s amended one): the digest of the ending it was made
#: under (``ending_sha256``), so a verdict saved under another ending is void (``verdict``).
MADE_UNDER = "ending_sha256"


#: Arms whose run keeps a Claude Code baseline, its first patch, and is admitted only while it has it (arm K3; ``run``'s
#: ``RULE_STEP_ARMS``). Such a run keeps it in a folder of its own (``BASELINE``), outside ``rounds``, as ``FIRST_PATCH``.
BASELINE_ARMS = ("K3",)
BASELINE = "baseline"
FIRST_PATCH = "round-1.patch"


@dataclass(frozen=True)
class RunFacts:
    """What the archive knows of one run, as ``run_facts`` read it in one place."""
    name: str                            # exactly as its start in the index names it; its folder is opened by this name only
    arm: str
    task: str
    executor: str                        # "host" or "container", as its start says
    ending: Mapping[str, Any] | None     # how it ended (``run_facts``); None: no ending on record
    ending_sha256: str | None            # the digest of its ending's files (``ENDING``, ``AFTER``); None when it has neither
    kept: Mapping[str, str]              # each patch a capture kept (``FIRST_PATCH``: K3's baseline), by the digest recorded then
    folder: Path
    repeat: int | None = None            # as its start names it; None for a start that names none (every round 3 run's names one)
    cost_usd: float | None = None        # what its end in the index says it cost; None while no end is there


def run_facts(archive: Archive | Path, name: str, rows: Iterable[Mapping[str, Any]] | None = None) -> RunFacts | None:
    """The one source of every fact about a run, which every entry point asks by the run's name: wecommon's ``ending``,
    ``ending_sha256``, ``verdict``, ``holds`` and the admission rule (``unscored_why``); and through them ``run.check``,
    ``run.r3_done``, ``compare.saved``, ``loop.one``, ``loop.rescore`` and the reviewer cases' export. A run is found only by a
    start in the index (``rows``, read now when not given) whose name is exactly ``name``: no other name, and no row a caller
    holds, says anything of it, and a name no start has (an alias such as ``a/../a``, or none) gives None, which is never
    admitted. Its ending: its ending file (``ENDING``), "error" when a failure was recorded after an ending that would be scored
    (``AFTER``), while one with no verdict to give stays as it is ("limited", "refused", K3's "no_first_patch"); a host run with
    no ending file has none; any other run's is its own record in its folder (``run.json``), else the index's end of it. The
    ending's files are read once, and their digest is taken from the same bytes."""
    archive = archive if isinstance(archive, Archive) else Archive(Path(archive))
    rows = list(archive.rows() if rows is None else rows)
    name = str(name or "")
    started = next((r for r in rows if r.get("event") == "started" and r.get("run") == name), None) if name else None
    if started is None:
        return None
    folder, executor = archive.root / name, "host" if started.get("executor") == "host" else "container"
    files: dict[str, bytes] = {}
    for part in (ENDING, AFTER):
        try:
            files[part] = (folder / part).read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:          # there, but not readable: an ending of its own, never taken for none
            files[part] = f"unreadable: {type(exc).__name__}".encode()
    digest = hashlib.sha256()
    for part, data in files.items():
        digest.update(f"{part}\0{len(data)}\0".encode() + data)
    kept: dict[str, str] = {}
    for r in rows:
        if r.get("event") == "kept" and r.get("run") == name and r.get("patch") and r.get("sha256"):
            kept.setdefault(str(r["patch"]), str(r["sha256"]))
    repeat = started.get("repeat")
    ended = next((r for r in reversed(rows) if r.get("event") == "ended" and r.get("run") == name), None)
    cost = (ended or {}).get("cost_usd")
    return RunFacts(name=name, arm=str(started.get("arm") or ""), task=str(started.get("task") or ""), executor=executor,
                    ending=_ending_from(files, executor, folder, rows, name),
                    ending_sha256=digest.hexdigest() if files else None, kept=MappingProxyType(kept), folder=folder,
                    repeat=repeat if isinstance(repeat, int) and not isinstance(repeat, bool) else None,
                    cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None)


def _ending_from(files: Mapping[str, bytes], executor: str, folder: Path, rows: list[Mapping[str, Any]],
                 name: str) -> Mapping[str, Any] | None:
    """``run_facts``'s ending, from the bytes of the ending's files it read, else the run's own record or its end in the index."""
    if ENDING in files:
        try:
            first = json.loads(files[ENDING].decode("utf-8"))
        except ValueError:
            return None
        status = str(first.get("status") or "") if isinstance(first, dict) else ""
        if not status:
            return None
        return MappingProxyType({**first, "status": status if status not in SCORED or AFTER not in files else "error"})
    if executor == "host":
        return None
    try:
        record = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if not isinstance(record, dict):
        record = next((dict(r) for r in reversed(rows) if r.get("event") == "ended" and r.get("run") == name), None)
    return MappingProxyType(dict(record)) if record and record.get("status") else None


def ending(root: Path | Archive, name: str) -> Mapping[str, Any] | None:
    """How run ``name`` ended, as ``run_facts`` reads it; None for a name no start in the index has, or a run with no ending."""
    facts = run_facts(root, name)
    return facts.ending if facts is not None else None


def ending_sha256(root: Path | Archive, name: str) -> str | None:
    """The digest of run ``name``'s ending's files, as ``run_facts`` reads them, so a failure recorded after the ending changes it.
    None for a run whose folder holds neither (its ending is its own record), and for a name no start has."""
    facts = run_facts(root, name)
    return facts.ending_sha256 if facts is not None else None


def linked(st: os.stat_result) -> bool:
    """Whether what ``os.lstat`` described is a link: a symbolic link, a junction or any other reparse point."""
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def plain(path: Path, *, folder: bool = False) -> bool:
    """Whether ``path`` is a regular file (a folder, with ``folder``) and no link (``linked``)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(st.st_mode) if folder else stat.S_ISREG(st.st_mode)) and not linked(st)


def baseline_of(folder: Path) -> Path:
    """Where a K3 run's first patch is kept: in a folder of the run's own (``BASELINE``), outside ``rounds``."""
    return folder / BASELINE / FIRST_PATCH


def _baseline(facts: RunFacts | None) -> bytes | None:
    want = facts.kept.get(FIRST_PATCH) if facts is not None else None
    path = baseline_of(facts.folder) if facts is not None else None
    if not want or path is None or not plain(path):
        return None
    data = path.read_bytes()
    return data if hashlib.sha256(data).hexdigest() == want else None


def baseline_bytes(archive: Archive, name: str) -> bytes | None:
    """K3 run ``name``'s Claude Code baseline, read once: the kept file's bytes (in the folder ``run_facts`` names), when they have
    the digest the index recorded at their capture, else None. A reader uses these bytes and never reads the path again, so what
    it uses is what was checked. A digest that does not match means the baseline changed, and the run is never scored."""
    return _baseline(run_facts(archive, name))


def has_baseline(archive: Archive, name: str) -> bool:
    """Whether K3 run ``name`` has its Claude Code baseline (``baseline_bytes``): the one answer, from durable state only."""
    return baseline_bytes(archive, name) is not None


def _why_not(facts: RunFacts | None, name: str) -> str:
    if facts is None:
        return f"{name!r} is no run the archive started: no start in its index has exactly that name"
    if facts.arm in BASELINE_ARMS and _baseline(facts) is None:
        return "its baseline changed since it was kept" if facts.kept.get(FIRST_PATCH) else "it has no Claude Code baseline"
    if not scoreable(facts.ending):
        return (f"it ended {(facts.ending or {}).get('status') or 'with no ending on record'}: it has no verdict to give "
                "(wecommon.run_facts)")
    return ""


def unscored_why(archive: Archive, name: str) -> str:
    """The one admission rule: "" when run ``name`` may be saved as a row and offered to ``run.check``, else why not. Every fact it
    reads comes from ``run_facts``: a name no start in the index has is never admitted; a run of an arm that keeps a baseline
    (``BASELINE_ARMS``, by the arm its start names) only while it has it (``has_baseline``), whatever its ending says; then every
    arm's alike, its ending must have a verdict to give (``scoreable``), so a stopped or failed run is scored on what it left, and
    a K3 run whose baseline was kept is a row on the same endings as K2's. ``verdict`` and ``holds`` ask it first."""
    return _why_not(run_facts(archive, name), str(name or ""))


def scored_run(archive: Archive, name: str) -> bool:
    """Whether the one admission rule (``unscored_why``) admits run ``name``."""
    return not unscored_why(archive, name)


def disagrees(facts: RunFacts, row: Mapping[str, Any]) -> str:
    """"" when every attribution a saved row (or a caller) makes of its run, its arm, its task and its repeat where it names them,
    is the run's own (``run_facts``), else why not. A row that disagrees is refused, never re-labelled: what it says of the run is
    never a fact, and a score is attributed to the arm, task and repeat the run's start names, or to none. A repeat said of a run
    whose start names none disagrees with it: no repeat is ever taken from a row (study review 1, finding 6)."""
    for key in ("arm", "task"):
        if key in row and str(row.get(key) or "") != getattr(facts, key):
            return f"it says {key} {row.get(key)!r} of a run whose start names {key} {getattr(facts, key)!r}"
    said = row.get("repeat")
    if "repeat" in row and (said != facts.repeat or isinstance(said, bool)):
        return f"it says repeat {said!r} of a run whose start names " + (
            f"repeat {facts.repeat!r}" if facts.repeat is not None else "no repeat")
    return ""


# --- the saved rows: one module reads and writes them -------------------------------------------------------------------
#: Where each run's saved row is kept (``loop.one``), and its amended verdict beside it (``loop.rescore``), under one name for both.
#: This module alone names these folders, finds a file in them, reads a row or writes one (``shelf``, ``keep``); every other reader
#: asks ``shelf`` (tests/validation/test_saved_rows.py lists, from every module's own code, each place that names them).
SAVED, AMENDED = "loop", "amended"


def saved_name(arm: str, task: str, repeat: Any) -> str:
    """The name a run's saved row (and its amended one) is kept under, made from its arm, task and repeat as its start names them."""
    return f"{arm}-{task}-{repeat}.json"


def _root(root: Path | Archive) -> Path:
    return root.root if isinstance(root, Archive) else Path(root)


def _same(one: Path, other: Path) -> bool:
    """Whether two paths are one file or folder, as the file system resolves them, junctions and links followed."""
    try:
        return os.path.samefile(one, other)
    except OSError:                     # one that is not there is no other
        return False


def _folders(root: Path | Archive) -> tuple[Path, Path]:
    """The saved rows' folder and the amended verdicts' (``SAVED``, ``AMENDED``), refused (ValueError) when they are one folder by
    identity, as a junction or link from one to the other makes them: a row read or written in one would be the other's (study
    review 3, finding 14). Every reader and writer of the rows (``shelf``, ``keep``) and of a store (``beside``) asks it, at each
    access."""
    rows, amended = _root(root) / SAVED, _root(root) / SAVED / AMENDED
    if _same(rows, amended):
        raise ValueError(f"{SAVED}/{AMENDED} is the saved rows' folder itself, by a junction or a link")
    return rows, amended


def row_file(name: str) -> bool:
    """Whether ``name`` is a saved row's file name, by the one rule the rows' reader (``shelf``) and writer (``keep``) both ask:
    one name, no folder, ending ".json" as the file system compares names (``os.path.normcase``: on Windows "moved.JSON" is one;
    study review 4, finding 9)."""
    return bool(name) and Path(name).name == name and os.path.normcase(name).endswith(".json")


def beside(root: Path | Archive, name: str, *below: str) -> Path:
    """A store kept beside the saved rows, in their folder (``SAVED``), by its one name: a review's or a replay's folder of records
    (``final_review`` and the like), or one of the training's own files (``evolve.json``, ``cegis.json``). Never the rows' folder
    itself, nor the amended verdicts' (``AMENDED``): only ``shelf`` reads a row, and only ``keep`` writes one. Names are compared as
    the file system resolves them (``os.path.normcase``, and on Windows the trailing dots and spaces it drops). At every access the
    store is checked by identity, junctions and links followed: it is refused (ValueError) if it is the rows' folder, the amended
    verdicts', or another entry of either (another store, or a row by a second name), or if it is a file that holds a row of a run
    the archive started, admitted or barring its key, which only ``keep`` may write (study review 3, findings 9, 13 and 14). A
    file kept here that is no run's row is found by ``shelf`` and refused, as it names no run. A path below the store is given as ``below``,
    one name a part, and checked the same way at every part that exists: each resolves inside the store, never through a
    junction or link out of it, and a file there holding any indexed run's row, with a key or not, is refused (study review 4,
    findings 1 and 4)."""
    resolved = os.path.normcase(name.rstrip(". ") if os.name == "nt" else name)
    if not name or Path(name).name != name or resolved in (".", "..", "", os.path.normcase(AMENDED)):
        raise ValueError(f"{name!r} is no store beside the saved rows")
    rows, amended = _folders(root)
    store = rows / name
    if any(not part or Path(part).name != part or part in (".", "..") for part in below):
        raise ValueError(f"{below!r} is no path below a store")
    target = store.joinpath(*below)
    if not os.path.lexists(store):
        return target
    file = store.is_file()             # a folder can be only a folder, and a file with one name only itself
    entries = not file or os.stat(store).st_nlink > 1
    others = [rows, amended, *(Path(e.path) for folder in (rows, amended) if entries and folder.is_dir()
                               for e in os.scandir(folder) if (file or e.is_dir())
                               and not (folder == rows and os.path.normcase(e.name) == resolved))]
    if any(_same(store, other) for other in others):
        raise ValueError(f"{name!r} is the saved rows' folder, the amended verdicts', or another of their entries, by a junction "
                         "or a link")
    inside = os.path.realpath(store)
    for depth in range(1, len(below) + 1):
        part = store.joinpath(*below[:depth])
        if os.path.lexists(part) and os.path.normcase(os.path.realpath(part)) != os.path.normcase(
                os.path.join(inside, *below[:depth])):
            raise ValueError(f"{'/'.join((name, *below[:depth]))!r} leaves its store by a junction or a link")
    if target.is_file():
        archive = root if isinstance(root, Archive) else Archive(Path(root))
        held, _ = _found(archive, SAVED, target, lambda run: run_facts(archive, run) if run else None)
        if held.run and run_facts(archive, held.run) is not None:      # any indexed run's row, admitted or refused
            raise ValueError(f"{name!r} holds the row of {held.run!r}, which only the saved rows' writer writes")
    return target


def _own(facts: RunFacts, kept: Mapping[str, Any] | None, amendment: str | None) -> bool:
    """Whether ``kept`` is a verdict saved for this run (and diff): it names the run exactly, and says nothing of it that its start
    does not (``disagrees``)."""
    return (kept is not None and kept.get("run") == facts.name and not disagrees(facts, kept)
            and (amendment is None or kept.get("amendment_sha256") == amendment))


def judge(facts: RunFacts | None, kept: Mapping[str, Any] | None, *, amendment: str | None = None,
          why: str | None = None) -> bool | None:
    """The one rule a run's saved verdict is read by, on the run's facts as they were read (``run_facts``): ``verdict`` asks it, and
    so do ``shelf`` (and through it every reader of a saved row) and ``loop.one`` on the facts it read again after its check.
    ``kept`` is only the verdict being judged: the run's saved row for the maintainers' tests, or its amended one for the diff whose
    digest is ``amendment``. None for a run the admission rule does not admit now (``unscored_why``), and for a verdict that is
    missing (none, or one saved for another run or diff, or one whose arm, task or repeat is not the run's own). A made run that
    did not end on its patch (``on_patch``: an error, a cap or the clock, a stop before its agent) failed on either score, which
    needs no check, whatever a row says (study review 1, finding 13). A verdict saved under an ending the run no longer has
    (``MADE_UNDER``) is void, None until it is checked again. Else the verdict as saved, when it is one: for the maintainers' tests
    none for a run with no count of failing tests, and a verdict that is not true or false is none, never a pass or a failure.
    ``why``: the admission rule's word on the run, where the caller read it already with these facts (``shelf``)."""
    if facts is None or (_why_not(facts, facts.name) if why is None else why) or not _own(facts, kept, amendment):
        return None
    if not on_patch(facts.ending):
        return False
    if kept.get(MADE_UNDER) != facts.ending_sha256:
        return None
    got = kept.get("amended_passes" if amendment is not None else "passes")
    if amendment is None and kept.get("failing") is None:
        return None
    return got if isinstance(got, bool) else None


def _stands(facts: RunFacts | None, kept: Mapping[str, Any] | None, amendment: str | None = None, why: str | None = None) -> bool:
    return (facts is not None and not (_why_not(facts, facts.name) if why is None else why) and _own(facts, kept, amendment)
            and kept.get(MADE_UNDER) == facts.ending_sha256)


def verdict(root: Path | Archive, name: str, kept: Mapping[str, Any] | None, *, amendment: str | None = None) -> bool | None:
    """Run ``name``'s verdict on one score, by the one rule (``judge``) on the run's facts as ``run_facts`` reads them now."""
    return judge(run_facts(root, name), kept, amendment=amendment)


def holds(root: Path | Archive, name: str, kept: Mapping[str, Any] | None, *, amendment: str | None = None) -> bool:
    """Whether a saved verdict (``verdict``'s ``kept``) is run ``name``'s own and stands: the admission rule admits the run now,
    and the verdict was saved for this run (and diff) under the ending the run has now. One that does not hold is made again, or
    not given. One whose arm, task or repeat is not the run's own (``disagrees``) never holds."""
    return _stands(run_facts(root, name), kept, amendment)


@dataclass(frozen=True)
class Found:
    """One file in the saved rows' folders as ``shelf`` read it: where it is (``folder``: ``SAVED`` for a run's row, ``AMENDED`` for
    its amended verdict; ``file``, its name there), the run it names (``run``, "" for none), that run's key as its start names it
    (``key``: arm, task and repeat; None for a name no start has, or a start that names no repeat), and why it is refused ("" for
    a row that is its run's own). ``torn``: it cannot be read as one JSON object, as a write an older writer cut short leaves it;
    it bars nothing, and the next write of its run replaces it (``keep``)."""
    folder: str
    file: str
    run: str
    key: tuple[str, str, int] | None
    why: str = ""
    torn: bool = False


@dataclass(frozen=True)
class Saved:
    """A saved row that is its run's own, as ``shelf`` read it, in one read: the run's facts, its key (arm, task and repeat, as its
    start names them), the file its row is kept in, and the verdicts that one read of the row and of its amended row gives by the
    one rule (``judge``). It holds no row, so no reader can pair a score with another read of one, or read a field of one."""
    facts: RunFacts
    key: tuple[str, str, int]
    file: str
    admitted: bool                   # whether the one admission rule admits the run (``unscored_why``), as this read found it
    passes: bool | None              # on the maintainers' tests
    holds: bool                      # whether that verdict stands as saved (``holds``)
    failing: int | None              # how many of the maintainers' tests its check found failing, while the verdict stands
    tests: tuple[str, ...]           # which of them (the first twenty), while the verdict stands
    amended: bool | None             # on the amended tests as they stand; the maintainers' verdict where the bug has no amendment
    amended_holds: bool
    amended_file: str | None         # the file its amended verdict is kept in, if any
    amendment_sha256: str | None     # the digest of the bug's amendment it was judged on; None for a bug with none

    @property
    def name(self) -> str:
        """The name its rows are written under when none is kept yet (``saved_name``)."""
        return saved_name(*self.key)

    @property
    def scores(self) -> dict[str, bool | None]:
        return {"maintainers": self.passes, "amended": self.amended}

    def record(self) -> dict[str, Any]:
        """The row as ``loop.one`` keeps it, made from the run's facts and this verdict alone: what the loop and the training read."""
        f, end = self.facts, self.facts.ending or {}
        return {"arm": f.arm, "task": f.task, "repeat": f.repeat, "run": f.name, "status": end.get("status"), "why": end.get("why"),
                "cost_usd": f.cost_usd, "passes": self.passes, "failing": self.failing, "tests": list(self.tests),
                **({MADE_UNDER: f.ending_sha256} if f.ending_sha256 else {})}

    def amended_record(self) -> dict[str, Any]:
        """The amended row as ``loop.rescore`` keeps it, made the same way."""
        f = self.facts
        return {"arm": f.arm, "task": f.task, "repeat": f.repeat, "run": f.name, "passes": self.passes,
                "amended_passes": self.amended, "amendment_sha256": self.amendment_sha256,
                **({MADE_UNDER: f.ending_sha256} if f.ending_sha256 else {})}


@dataclass(frozen=True)
class Shelf:
    """What ``shelf`` read of the saved rows' folders, in one read: every row that is its run's own (``rows``, by file name), and
    every file it found there, refused or not (``found``)."""
    rows: tuple[Saved, ...]
    found: tuple[Found, ...]

    def get(self, key: tuple[str, str, int]) -> Saved | None:
        return next((one for one in self.rows if one.key == key), None)

    def bars(self, key: tuple[str, str, int], *, amended: bool = False) -> str:
        """Why the run of ``key`` has no row (``amended``: no amended verdict), "" when nothing bars it: a file refused for anything
        but being torn that names a run of that key (one saying of its run what its start does not, or one of two files for one run,
        or of two runs for one key). Every reader of this read gives no score for a barred key (``shelf``), and ``loop.one`` and
        ``loop.rescore`` refuse it before any run or check, the same way (study review 2, finding 1)."""
        folder = AMENDED if amended else SAVED
        for f in self.found:
            if f.folder == folder and not f.torn and f.why and f.key == key:
                return f"{SAVED}/{AMENDED + '/' if amended else ''}{f.file}: {f.why}"
        return ""

    def blocks(self, key: tuple[str, str, int], *, amended: bool = False, run: str | None = None, file: str | None = None) -> str:
        """Why a row of the run of ``key`` may not be written to the file it would go to (``file``, else the key's name), "" when it
        may: that file holds a row refused for anything but being torn, or a row of another key's run, or, with ``run`` (the run it is
        written for), of any other run. ``loop.one`` and ``loop.rescore`` ask it before any run or check, and ``keep`` reads the file
        again just before it writes, by the same rule. A name is the file system's: on Windows, one in another case is that file."""
        folder, dest = (AMENDED if amended else SAVED), os.path.normcase(file or saved_name(*key))
        for f in self.found:
            if f.folder != folder or f.torn or os.path.normcase(f.file) != dest:
                continue
            where = f"{SAVED}/{AMENDED + '/' if amended else ''}{f.file}"
            if f.why:
                return f"{where}: {f.why}"
            if f.key != key or (run is not None and f.run != run):
                return f"{where} holds the row of {f.run}, a run of {' '.join(map(str, f.key or ()))}: never written over"
        return ""


def _found(archive: Archive, folder: str, path: Path, facts_of: Callable[[str], RunFacts | None]) -> tuple[Found, dict | None]:
    """One file, read once: what it is (``Found``), and the row it holds when that is its run's own. From a row only the run it
    names is read here, and what it says of that run's arm, task and repeat, to refuse it if any is not the run's own."""
    if not plain(path):
        return Found(folder, path.name, "", None, "it is no plain file (a folder or a link)"), None
    try:
        row = json.loads(path.read_bytes().decode("utf-8"))
    except ValueError:                  # UnicodeDecodeError is a ValueError too
        row = None
    if not isinstance(row, dict):
        return Found(folder, path.name, "", None, "it cannot be read as one JSON object, as a write cut short leaves it",
                     torn=True), None
    name = row.get("run") if isinstance(row.get("run"), str) else ""
    facts = facts_of(name)
    if facts is None:
        return Found(folder, path.name, name, None, f"{name!r} is no run the archive started"), None
    key = (facts.arm, facts.task, facts.repeat) if facts.repeat is not None else None
    wrong = disagrees(facts, row) or ("its run's start names no repeat" if key is None else "")
    if wrong:                           # the admission rule's word first, as every entry point gives it
        return Found(folder, path.name, name, key, "; ".join(x for x in (_why_not(facts, name), wrong) if x)), None
    return Found(folder, path.name, name, key), row


def _unique(found: list[Found]) -> list[Found]:
    """A run whose rows more than one file of a folder holds, or a key more than one run's row holds, has no row: each is refused,
    in the rows' folder and the amended verdicts' alike (study review 2, finding 14)."""
    runs = collections.Counter((f.folder, f.run) for f in found if not f.why)
    keys: dict[Any, set[str]] = collections.defaultdict(set)
    for f in found:
        if not f.why:
            keys[(f.folder, f.key)].add(f.run)
    out = []
    for f in found:
        if not f.why and runs[(f.folder, f.run)] > 1:
            f = replace(f, why=f"{runs[(f.folder, f.run)]} files hold a row of its run")
        elif not f.why and len(keys[(f.folder, f.key)]) > 1:
            f = replace(f, why=f"{len(keys[(f.folder, f.key)])} runs' rows hold its key")
        out.append(f)
    return out


def shelf(root: Path | Archive, amendment: Callable[[str], Path | None]) -> Shelf:
    """The one reader of the saved rows (study review 1: the attribution class met a sixth time, its design now enforced). It finds
    every ``*.json`` in the rows' folder (``SAVED``) and in the amended verdicts' (``AMENDED``), whatever the name, and reads each
    once. From a row it reads only the run it names, exactly, the verdict being judged and the digest of the ending it was made
    under; every attribution (arm, task, repeat, status) is the run's own (``run_facts``, read once a run, with the index read
    once). It refuses a row that names no run the index started, whose run's start names no repeat, that says of its run what the
    run's facts do not (``disagrees``), or whose run or key another file's row holds too (``_unique``): a refused row is never
    re-labelled, takes no key and gives no score. Each row that is its run's own comes back with its run's facts and its verdicts
    together (``Saved``), made from this one read by the one rule (``judge``): on the maintainers' tests; and on the amended tests
    from the amended row that names its run, for a run admitted now on a bug with an amendment (``amendment``: the bug's diff, or
    None, which ``run.amendment`` gives; every caller passes it), else the maintainers' verdict, as ``compare`` reads a bug with
    none. Rows come in the order of their files' names."""
    archive = root if isinstance(root, Archive) else Archive(Path(root))
    index, facts = archive.rows(), {}

    def facts_of(name: str) -> RunFacts | None:
        if name not in facts:
            facts[name] = run_facts(archive, name, index) if name else None
        return facts[name]

    found, read = [], {}
    for folder, where in zip((SAVED, AMENDED), _folders(archive)):      # two folders by identity (study review 3, finding 14)
        for path in sorted(p for p in where.glob("*.json") if row_file(p.name)) if where.is_dir() else ():
            one, row = _found(archive, folder, path, facts_of)
            found.append(one)
            read[(folder, one.file)] = row
    found = _unique(found)
    barred = Shelf(rows=(), found=tuple(found))     # what bars a key, for every reader of this read, as loop.one asks it
    amended = {f.run: (f.file, read[(AMENDED, f.file)]) for f in found if f.folder == AMENDED and not f.why}
    rows = []
    for f in found:
        if f.folder != SAVED or f.why or barred.bars(f.key):     # a key a refused row bars has no row (study review 2, 1)
            continue
        run, row = facts_of(f.run), read[(SAVED, f.file)]
        why = _why_not(run, run.name)            # the admission rule, read once a row, for every verdict below
        passes, stands = judge(run, row, why=why), _stands(run, row, why=why)
        failing, tests = row.get("failing"), row.get("tests")
        a_file, a_row = amended.get(run.name, (None, None))
        if barred.bars(f.key, amended=True):
            a_file, a_row = None, None           # nor an amended verdict where one is barred
        diff = amendment(run.task) if not why else None
        sha = sha256_file(diff) if diff is not None else None
        rows.append(Saved(
            facts=run, key=f.key, file=f.file, admitted=not why, passes=passes, holds=stands,
            failing=failing if stands and isinstance(failing, int) and not isinstance(failing, bool) else None,
            tests=tuple(str(t) for t in tests) if stands and isinstance(tests, list) else (),
            amended=judge(run, a_row, amendment=sha, why=why) if sha else passes,
            amended_holds=bool(sha) and _stands(run, a_row, sha, why), amended_file=a_file, amendment_sha256=sha))
    return Shelf(rows=tuple(rows), found=tuple(found))


def saved_rows(root: Path | Archive, amendment: Callable[[str], Path | None]) -> list[Saved]:
    """Every saved row that is its run's own, with its run's facts and its verdicts (``shelf``)."""
    return list(shelf(root, amendment).rows)


def saved_row(root: Path | Archive, arm: str, task: str, repeat: Any, amendment: Callable[[str], Path | None]) -> Saved | None:
    """The saved row of the run whose start names this arm, task and repeat (``shelf``), or None."""
    return shelf(root, amendment).get((arm, task, repeat))


def durable(path: Path) -> None:
    """``path``'s bytes flushed to the disk (``os.fsync``), before anything that says they were kept is written."""
    with open(path, "r+b") as stream:
        os.fsync(stream.fileno())


def write_whole(where: Callable[[], Path], data: bytes) -> Path:
    """The one writer of every store file (study follow-up review 1, finding 2): a saved row (``keep``) and every store kept beside
    the rows (``beside``: the reviews, the replays' records, the training's own files). ``where`` names the destination and checks
    it (for a store, ``beside``; for a row, ``keep``'s reading of the folder and of the run's ending), and it is asked twice: before
    anything is made, and again just before the replace, so what it checks is what holds when the file is put in place. ``data``
    goes to a temporary file made exclusively in the destination's folder (``tempfile.mkstemp``: ``O_CREAT|O_EXCL``, a name no one
    can predict, so no file made there in advance is written through), flushed and synced to the disk, and is put in place by
    ``os.replace`` only while neither it nor the destination has a second name (a hard link: a file with another name is another
    file's too, and is never put in place or replaced). A stop at any step leaves the destination as it was, or whole, and no
    temporary file behind. Returns the destination."""
    dest = where()
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, part = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.normcase(os.fspath(where())) != os.path.normcase(os.fspath(dest)):
            raise ArchiveError(f"{dest.name}: its destination moved while it was written; nothing is put in place")
        for path, what in ((part, "its temporary file"), (dest, "its destination")):
            if os.path.lexists(path) and os.lstat(path).st_nlink != 1:
                raise ArchiveError(f"{dest.name}: {what} has a second name (a hard link), so it is another file's too: "
                                   "nothing is put in place")
        os.replace(part, dest)
    except BaseException:
        with suppress(OSError):
            os.unlink(part)
        raise
    return dest


def keep(root: Path | Archive, row: Mapping[str, Any], *, amended: bool = False, file: str | None = None) -> Path:
    """The one writer of the saved rows: ``loop.one``'s row, or ``loop.rescore``'s amended one (``amended``), written whole or not at
    all by the one writer of store files (``write_whole``), in the file its run's row is kept in already (``file``), else under the
    name its run's start gives (``saved_name``). It decides as the rows' reader does (``shelf``), at the moment it writes: every
    check is made when ``write_whole`` asks, before it makes anything and again just before the replace. It is refused
    (``ArchiveError``), and nothing is written, for a row that names no run the index started, of a start that names no repeat, or
    that says of its run what the run's facts do not; for a file there that holds another run's row, read by the reader's own rule
    (``_found``), so another run's row is never written over (a torn one is replaced); for a row the reader would refuse beside
    the folder's other files, by its own rule (``_unique``: one file a run, one run a key, so one run is never written under two
    names; study follow-up review 1, finding 7); and, last, for a run whose ending is not the one the verdict was made under
    (``MADE_UNDER``), its digest read after every other check (finding 6)."""
    archive = root if isinstance(root, Archive) else Archive(Path(root))
    name = row.get("run") if isinstance(row.get("run"), str) else ""
    label = AMENDED if amended else SAVED

    def where() -> Path:
        index, read = archive.rows(), {}

        def facts_of(run: str) -> RunFacts | None:
            if run not in read:
                read[run] = run_facts(archive, run, index) if run else None
            return read[run]

        facts = facts_of(name)
        wrong = (f"{name!r} is no run the archive started" if facts is None
                 else disagrees(facts, row) or ("its run's start names no repeat" if facts.repeat is None else ""))
        if wrong:
            raise ArchiveError(f"no row of {name!r} is written: {wrong}")
        folder = _folders(archive)[1 if amended else 0]     # refused when the two are one folder (study review 3, finding 14)
        dest = file or saved_name(facts.arm, facts.task, facts.repeat)
        if not row_file(dest):               # the reader's own rule (study review 4, finding 9)
            raise ValueError(f"{dest!r} is no saved row's name")
        path = folder / dest
        if os.path.lexists(path):            # read by the reader's own rule (``_found``)
            held, _ = _found(archive, label, path, facts_of)
            if not held.torn and (held.why or held.run != facts.name):     # a torn one is replaced; any other is never written over
                raise ArchiveError(f"{path.name} holds no row of {facts.name}'s own: "
                                   + (held.why or f"it is the row of {held.run!r}, another run's") + "; it is never written over")
        others = [_found(archive, label, p, facts_of)[0] for p in sorted(folder.glob("*.json"))
                  if row_file(p.name) and os.path.normcase(p.name) != os.path.normcase(dest)] if folder.is_dir() else []
        mine = Found(label, dest, facts.name, (facts.arm, facts.task, facts.repeat))
        barred = Shelf(rows=(), found=tuple(_unique([*others, mine]))).bars(mine.key, amended=amended)
        if barred:                           # the folder, with this row written, would give its run no row
            raise ArchiveError(f"no row of {name!r} is written: {barred}, as the rows' reader refuses it")
        if row.get(MADE_UNDER) != ending_sha256(archive, name):    # read now, after every check: a change voids the verdict
            raise ArchiveError(f"no row of {name!r} is written: its ending changed since its verdict was made (study review 4, 7)")
        return path

    return write_whole(where, json.dumps(dict(row), indent=1).encode("utf-8"))


def numstat_names(out: bytes) -> list[str]:
    """The files git's own reader of a patch names, from its machine-readable output (``git apply --numstat -z``): each by its name
    after the patch (for a rename, the new one), as git's bytes held as they are (``surrogateescape``, never a replacement character),
    so a name git would quote, or two names that render alike, are read exactly as git keeps them."""
    fields, names = out.split(b"\0"), []
    while fields:
        one = fields.pop(0)
        if not one:
            continue
        _, _, name = one.split(b"\t", 2)
        if not name and len(fields) >= 2:                  # a rename: its old name, then its new one, each a field of its own
            _, name = fields.pop(0), fields.pop(0)
        names.append(name.decode("utf-8", errors="surrogateescape"))
    return names


def scoreable(record: Mapping[str, Any] | None) -> bool:
    """Whether a run's ending (``ending``) may be given a verdict, and whether it was made: only an ending it names (``SCORED``).
    No ending, an empty record, a status it does not know, and "limited" or "refused" (``UNSCORED``) may not."""
    return record is not None and record.get("status") in SCORED


def on_patch(record: Mapping[str, Any] | None) -> bool:
    """Whether a run's verdict is its final patch's independent check (``ON_PATCH``), the one rule ``loop`` and ``compare`` ask."""
    return record is not None and record.get("status") in ON_PATCH


def may_start(spent_usd: float, cap: float = CAP_USD, need: float = RUN_STOP_USD) -> tuple[bool, str]:
    """A run starts only if the money spent so far plus the run's own stop stays within the cap."""
    if spent_usd + need > cap + 1e-9:
        return False, f"${spent_usd:.2f} spent; another run could reach ${spent_usd + need:.2f}, past the ${cap:.0f} stop"
    return True, ""
