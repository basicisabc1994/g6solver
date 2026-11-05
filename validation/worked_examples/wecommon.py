# SPDX-License-Identifier: Apache-2.0
"""What the worked examples' scripts share (PLAN.md): the archive outside the repository, the prices and the $100 stop,
the key's mask, and the tally of Claude Code's stream.

The archive is ``G6_WE_ARCHIVE`` when set, else the local artifacts folder on the Desktop (``Path.home()`` based, so no
private path is written here). Each run is a write-once folder; ``runs.jsonl`` at the root lists every run started and
ended, with its cost, and the stop reads it.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
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
            status = (ending(self.archive.root, {**record, "run": self.rel}) or {}).get("status") or status
        body = {**record, "status": status, "ended_at": now()}
        self.write_json("run.json", body)
        self.archive.append({"event": "ended", "run": self.rel, "status": status, "at": body["ended_at"],
                             "cost_usd": float(record.get("cost_usd") or 0.0),
                             "agent_started": bool(record.get("agent_started"))})
        return body


class Archive:
    def __init__(self, root: Path, redact: Redactor | None = None) -> None:
        self.root = Path(root)
        self.redact = redact or Redactor()

    @property
    def index(self) -> Path:
        return self.root / NDJSON["index"]

    def append(self, row: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.index, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(dict(row), ensure_ascii=False) + "\n")

    def rows(self) -> list[dict[str, Any]]:
        """Every row of the index, for every reader, framed by ``read_stream``. A torn last line, which a write cut short leaves
        (cut inside a character, or inside a record), is skipped; a line that cannot be read anywhere before it is an error, as
        before. Its bytes are read once: a byte that is not UTF-8 is held as an escape (``surrogateescape``), and a line holding one
        cannot be read, as a line that is not JSON cannot."""
        if not self.index.is_file():
            return []
        lines = [line for line in read_stream(self.index, errors="surrogateescape") if line.strip()]
        rows = []
        for at, line in enumerate(lines):
            try:
                line.encode("utf-8")        # strict: an escaped byte is a UnicodeEncodeError, a ValueError
                rows.append(json.loads(line))
            except ValueError:
                if at == len(lines) - 1:
                    break
                raise
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


def ending(root: Path, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """How the run ``row["run"]`` ended, read from its folder under the archive ``root``: the one source every entry point asks
    whether a run may be scored and whether it was made (``scoreable``, ``on_patch``; ``run.check``, ``run.r3_done``,
    ``loop.one``, ``loop.rescore``, ``compare.saved``). Its ending file when it has one (``ENDING``), its status "error" when a
    failure was recorded after an ending that would be scored (``AFTER``), while one with no verdict to give stays as it is
    ("limited", "refused", and arm K3's "no_first_patch"), so a failure after it never makes it more scoreable. A host
    run with no ending file has no ending (None, never scored): an empty or missing record says nothing. Any other run's ending is
    ``row`` itself, its status as its own record gives it, as before; so is that of a row whose run has no folder."""
    rel = row.get("run")
    folder = Path(root) / str(rel) if rel else None
    if folder is None or not folder.is_dir():
        return dict(row)
    try:
        first = json.loads((folder / ENDING).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None if _host(Path(root), str(rel), folder, row) else dict(row)
    except (OSError, ValueError):
        return None
    status = str(first.get("status") or "") if isinstance(first, dict) else ""
    if not status:
        return None
    return {**first, "status": status if status not in SCORED or not (folder / AFTER).exists() else "error"}


def _host(root: Path, rel: str, folder: Path, row: Mapping[str, Any]) -> bool:
    """Whether a run is a host run: as ``row`` says, else as its saved record says, else as its start in the index says."""
    if "executor" in row:
        return row.get("executor") == "host"
    try:
        return json.loads((folder / "run.json").read_text(encoding="utf-8")).get("executor") == "host"
    except (OSError, ValueError, AttributeError):
        return any(r.get("event") == "started" and r.get("run") == rel and r.get("executor") == "host" for r in Archive(root).rows())


def ending_sha256(root: Path, row: Mapping[str, Any]) -> str | None:
    """The digest of the run's durable ending, the files ``ending`` reads in its folder (``ENDING`` and ``AFTER``), so a failure
    recorded after the ending changes it. None for a run whose folder holds neither: its ending is its own record, as it always
    was for a run not made on the host."""
    rel = row.get("run")
    folder = Path(root) / str(rel) if rel else None
    if folder is None or not folder.is_dir():
        return None
    digest, found = hashlib.sha256(), False
    for name in (ENDING, AFTER):
        try:
            data = (folder / name).read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:          # there, but not readable: an ending of its own, never taken for none
            data = f"unreadable: {type(exc).__name__}".encode()
        found = True
        digest.update(f"{name}\0{len(data)}\0".encode() + data)
    return digest.hexdigest() if found else None


def verdict(root: Path, row: Mapping[str, Any], kept: Mapping[str, Any] | None, *, amendment: str | None = None) -> bool | None:
    """A run's verdict on one score, its ending read first (``ending``): the one rule ``loop.one``, ``loop.rescore`` and
    ``compare.saved`` read a saved verdict by. ``row`` is the run's saved row, and ``kept`` the saved verdict: the row itself for the
    maintainers' tests, or its amended one for the diff whose digest is ``amendment``. None for a run that may not be scored now
    (``scoreable``), and for a verdict that is missing (none, or one saved for another run or diff). A verdict saved under an
    ending the run no longer has (``MADE_UNDER``, ``ending_sha256``) is void, never given: a made run that did not end on its patch
    (``on_patch``) failed, which needs no check; any other is None until it is checked again. Else the verdict as saved, and for
    the maintainers' tests none for a run on its patch with no count of failing tests."""
    end = ending(root, row)
    if (not scoreable(end) or kept is None or kept.get("run") != row.get("run")
            or (amendment is not None and kept.get("amendment_sha256") != amendment)):
        return None
    if kept.get(MADE_UNDER) != ending_sha256(root, row):
        return None if on_patch(end) else False
    if amendment is not None:
        return kept.get("amended_passes")
    return None if on_patch(end) and kept.get("failing") is None else bool(kept.get("passes"))


def holds(root: Path, row: Mapping[str, Any], kept: Mapping[str, Any] | None, *, amendment: str | None = None) -> bool:
    """Whether a saved verdict (``verdict``'s ``kept``) is the run's own and stands: the run may be scored now, and it was saved for
    this run (and diff) under the ending the run has now. A saved verdict that does not hold is made again, or not given."""
    return (kept is not None and scoreable(ending(root, row)) and kept.get("run") == row.get("run")
            and (amendment is None or kept.get("amendment_sha256") == amendment)
            and kept.get(MADE_UNDER) == ending_sha256(root, row))


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
