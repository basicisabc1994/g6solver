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
from collections.abc import Iterable, Mapping
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
#: a923002, the merge of the worked examples' launch fixes W1 to W5 (PLAN.md section 9, option B).
REQUIRED_G6_COMMITS: tuple[str, ...] = ("5dad0fe7e0b933b274cd44443b449bef3864627a",
                                        "2680cc9d1afc9fa7c873b411573bd70c6134640b",
                                        "a9230022ebf080859411aa5dc0c8ea3c42687d45")
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
@dataclass
class Tally:
    """What Claude Code's stream-json said: model calls (distinct assistant messages, sub-agents included) with their
    model and usage, and the result record."""

    usage_by_message: dict[str, tuple[str, dict]] = field(default_factory=dict)
    result: dict | None = None
    session_id: str = ""
    lines: int = 0

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
    def cost_usd(self) -> float:
        """The larger of the stream's count and the result's per-model usage (PLAN.md §6)."""
        return round(max(self.stream_usd, self.result_usd or 0.0), 6)

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

    def finish(self, record: Mapping[str, Any], status: str) -> dict[str, Any]:
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
        return self.root / "runs.jsonl"

    def append(self, row: Mapping[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.index, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(dict(row), ensure_ascii=False) + "\n")

    def rows(self) -> list[dict[str, Any]]:
        if not self.index.is_file():
            return []
        return [json.loads(line) for line in self.index.read_text(encoding="utf-8").splitlines() if line.strip()]

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


def spent(rows: Iterable[Mapping[str, Any]]) -> float:
    """Dollars spent: every ended run's cost; a run started and never ended counts the run's whole stop until it is
    closed with its stream's cost."""
    ended = {r["run"]: float(r.get("cost_usd") or 0.0) for r in rows if r.get("event") == "ended"}
    total = sum(ended.values())
    for r in rows:
        if r.get("event") == "started" and r.get("kind") == "run" and r["run"] not in ended:
            total += RUN_STOP_USD
    return round(total, 6)


def may_start(spent_usd: float, cap: float = CAP_USD, need: float = RUN_STOP_USD) -> tuple[bool, str]:
    """A run starts only if the money spent so far plus the run's own stop stays within the cap."""
    if spent_usd + need > cap + 1e-9:
        return False, f"${spent_usd:.2f} spent; another run could reach ${spent_usd + need:.2f}, past the ${cap:.0f} stop"
    return True, ""
