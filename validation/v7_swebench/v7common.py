# SPDX-License-Identifier: Apache-2.0
"""What every V7 script shares: the frozen settings, the spend rules, the key redactor and the archive.

Nothing here starts a container or calls a model. The settings below are the ones PROTOCOL.md and the registry entry
name; a change to any of them re-registers the study.

**The archive** (``Archive``): every command that runs anything writes there, outside every worktree (``V7_ARCHIVE``,
default ``g6-validation/V7`` in the home folder). One folder per run, ``<phase>/<task id>/<arm>/<UTC timestamp>/``,
created once; every file in it is created once and never overwritten or deleted, except that the key scan may replace
a leaked key by ``<withheld>``. ``run.json``, written last, indexes the folder (each file's size and SHA-256) and says
how the run ended. ``runs.jsonl`` at the root is append-only: a ``started`` line before each run, an ``ended`` line
after it. The one file outside the run folders that changes is the shadow lessons' working store
(``shadow/<phase>.db``, ``shadow.py``); each lesson's ``theories.json`` is its write-once record. The spend rules and
the analysis read only the archive.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STUDY = "V7"
SEED = 20261023
DEV_SIZE, EVAL_SIZE, SCORED_SIZE, PILOT_SIZE = 10, 20, 60, 4

DATASET = {
    "source": "https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro",
    "revision": "2d52cb3df914a3fcf80c7f66738b3a88ae37fc50",
    "config": "default (SWE-bench Pro V2, version 2.0.0, 642 tasks)",
    "file": "data/default/test-00000-of-00001.parquet",
    "local_name": "test-00000-of-00001.parquet",
    "sha256": "1067f72c1f43bbc578276888625db3b836cebb535546c9eaf824c5d5dd104a7f",
    "bytes": 6511689,
}
HARNESS = {
    "source": "https://github.com/scaleapi/SWE-bench_Pro-os",
    "commit": "66f92766bba642462d4bbe5479e83f91f9211862",
    "tarball_sha256": "a459a929729c95d15cb7b4c5c599d36ea4e9a067bedc9d018cee6032b577e6cc",
    "v2_sha256sums_sha256": "9d84f8507c89241d42d8b3ef911600a1ec75dbbb32687ce9b45b93318502c0bd",
    "licence": "MIT (LICENSE, Scale AI 2026); task content under the 11 upstream repositories' licences",
}
CLAUDE_CODE = {"version": "2.1.309", "platform": "linux-x64",
               "sha256": "4d704a8aecbe038affa3000f498ae17c1b4f753add8facb8764120affc99b658",
               "source": "https://downloads.claude.ai/claude-code-releases/2.1.309/linux-x64/claude",
               "manifest_sha256": "8504633c7837099cefd2a995a433502717c360c155287b1ba92e6ec4d230093b"}
PYTHON = {"file": "cpython-3.12.15+20261021-x86_64-unknown-linux-gnu-install_only.tar.gz",
          "sha256": "5ff9f0602ce964a529ed1e8555d3abab6341926d724402ba9a15e9e877191124",
          "source": "https://github.com/astral-sh/python-build-standalone/releases/tag/20261021"}
# G6's wheel is built at run time from a named git ref (``run_arm.py build-wheel --ref``); every run records the commit.

MODEL = "claude-sonnet-5-5"
EFFORT = "high"            # the setting the official locked-protocol example passes
# Stage 1 (arm A): Claude Code alone solves the task.
CAPS = {"max_turns": 100, "wall_clock_s": 3000, "max_usd_per_run": 4.0}
# Stage 2, the same for A2 and G: a resumed pass from stage 1's finished container and session.
CAPS_STAGE2 = {"max_turns": 30, "wall_clock_s": 900, "max_usd_per_run": 1.50}
# A2's words, the same for every task (lead, 2026-10-23).
SELF_CHECK = "Check your work: reproduce the issue, run the tests you can, and fix anything that is wrong."
# USD per million tokens (Sonnet 5.5, as the owner's brief gives them; cache writes at 1.25 x input, an assumption).
PRICES = {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_creation": 2.50}

MAX_USD_CEILING = 180.0        # the key holds $200; G6_BENCH_MAX_USD may lower the cap, never raise it
# Each run not yet finished is counted at its per-run cap plus $0.50 for the one model message that can complete after
# Claude Code reaches its budget (it checks between messages), so runs in flight can never take the total past the cap.
RESERVE_PER_RUN = 4.50                 # stage 1 ($4 cap)
RESERVE_STAGE2 = 2.00                  # each stage-2 pass ($1.50 cap)
RESERVE_LESSON = 0.10                  # the shadow lesson (shadow.py): its input and output are cut to fit under this
LESSON_ESTIMATE_USD = 0.02             # what a lesson is expected to cost (planning only, like FIRST_RUN_ESTIMATE_USD)
TASK_RESERVE = RESERVE_PER_RUN + 2 * RESERVE_STAGE2 + RESERVE_LESSON   # a task starts only when all its runs fit
SHARES: dict[str, float] = {}         # no phase has a share of its own; the cap covers the pilot and the scored runs
FIRST_RUN_ESTIMATE_USD = 3.0           # the planning figure before any run has finished (reported, never a decision)
ARMS = ("A", "A2", "G")
DEFAULT_ARCHIVE = Path.home() / "g6-validation" / "V7"     # outside every worktree (lead, 2026-10-23)
WITHHELD = "<withheld>"


# --- spend ------------------------------------------------------------------------------------------------------------
def cap_usd(environ: Mapping[str, str] | None = None) -> float:
    """``G6_BENCH_MAX_USD`` (default and ceiling 180). A value that is not a positive number gives 0: nothing runs."""
    raw = (os.environ if environ is None else environ).get("G6_BENCH_MAX_USD", "")
    if not raw.strip():
        return MAX_USD_CEILING
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return min(value, MAX_USD_CEILING) if math.isfinite(value) and value > 0 else 0.0


def usage_cost(usage: Mapping[str, Any]) -> float:
    """USD for one usage record at PRICES (input, output, cache read and cache creation tokens)."""
    return (PRICES["input"] * int(usage.get("input_tokens") or 0)
            + PRICES["output"] * int(usage.get("output_tokens") or 0)
            + PRICES["cache_read"] * int(usage.get("cache_read_input_tokens") or 0)
            + PRICES["cache_creation"] * int(usage.get("cache_creation_input_tokens") or 0)) / 1e6


@dataclass(frozen=True)
class Spend:
    """What the runs so far cost, read from the archive's ledger (so a restart sees the same totals)."""

    total: float
    by_phase: dict[str, float]
    finished_costs: tuple[float, ...]
    in_flight: dict[str, int]           # runs started and not ended, by phase

    @property
    def expected_next(self) -> float:
        """The mean cost of the finished runs, or FIRST_RUN_ESTIMATE_USD before any (for reports, not decisions)."""
        return (sum(self.finished_costs) / len(self.finished_costs) if self.finished_costs
                else FIRST_RUN_ESTIMATE_USD)


def spend(records: Iterable[Mapping[str, Any]]) -> Spend:
    total, phases, costs, flying = 0.0, {}, [], {}
    for r in records:
        cost = float(r.get("cost_usd") or 0.0)
        phase = r.get("phase", "eval")
        total += cost
        phases[phase] = phases.get(phase, 0.0) + cost
        if r.get("status") == "running":
            flying[phase] = flying.get(phase, 0) + 1
        elif r.get("agent_started"):
            costs.append(cost)
    return Spend(round(total, 6), {k: round(v, 6) for k, v in phases.items()}, tuple(costs), flying)


def may_start(s: Spend, phase: str, *, need: float = RESERVE_PER_RUN, reserved: float | None = None,
              environ: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """Whether work reserved at ``need`` may start: the spend so far, the runs in flight (``reserved``, the caller's
    own; by default the ledger's runs still running, each at RESERVE_PER_RUN) and ``need`` must stay within the cap,
    and within the phase's share when it has one."""
    cap = cap_usd(environ)
    flying = (sum(s.in_flight.values()) * RESERVE_PER_RUN) if reserved is None else reserved
    if cap <= 0:
        return False, "G6_BENCH_MAX_USD is not a positive number: nothing runs"
    if s.total + flying + need > cap + 1e-9:
        return False, (f"spent ${s.total:.2f} + ${flying:.2f} in flight + ${need:.2f} reserved for the next would pass "
                       f"the cap ${cap:.2f}")
    share = SHARES.get(phase)
    if share is not None and s.by_phase.get(phase, 0.0) + need > share + 1e-9:
        return False, f"{phase} spent ${s.by_phase.get(phase, 0.0):.2f} + ${need:.2f} would pass its share ${share:.2f}"
    return True, ""


# --- the key never reaches a file -----------------------------------------------------------------------------------
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


# --- the archive ------------------------------------------------------------------------------------------------------
class ArchiveError(RuntimeError):
    """A write the archive refuses: a run folder or a file that already exists."""


def archive_root(environ: Mapping[str, str] | None = None) -> Path:
    raw = (os.environ if environ is None else environ).get("V7_ARCHIVE", "")
    return Path(raw) if raw.strip() else DEFAULT_ARCHIVE


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


_LEDGER_LOCK = threading.Lock()


class Archive:
    def __init__(self, root: Path, redactor: Redactor | None = None) -> None:
        self.root, self.redact = Path(root), redactor or Redactor()

    @property
    def ledger_path(self) -> Path:
        return self.root / "runs.jsonl"

    def _append(self, entry: Mapping[str, Any]) -> None:
        line = self.redact(json.dumps(dict(entry), sort_keys=True, default=str)) + "\n"
        with _LEDGER_LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())

    def start(self, phase: str, task: str, arm: str, **meta: Any) -> RunFolder:
        """A new run folder (refused if it exists: write-once) and its ``started`` line, before the run starts."""
        for name in (phase, task, arm):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,200}", name or ""):
                raise ArchiveError(f"not a safe folder name: {name!r}")
        rel = f"{phase}/{task}/{arm}/{_stamp()}"
        folder = self.root / rel
        folder.parent.mkdir(parents=True, exist_ok=True)
        try:
            folder.mkdir()
        except FileExistsError:
            raise ArchiveError(f"write-once: the run folder {rel} already exists") from None
        self._append({"event": "started", "run": rel, "phase": phase, "task": task, "arm": arm, "at": now(), **meta})
        return RunFolder(self, folder, rel, phase, task, arm)

    def ledger(self) -> list[dict[str, Any]]:
        if not self.ledger_path.is_file():
            return []
        return [json.loads(line) for line in self.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def runs(self) -> list[dict[str, Any]]:
        """Every run started, with how it ended (``status`` "running" when it has no ``ended`` line)."""
        started: dict[str, dict] = {}
        for line in self.ledger():
            if line.get("event") == "started":
                started[line["run"]] = {**line, "status": "running", "cost_usd": 0.0, "agent_started": False}
            elif line.get("event") == "ended" and line.get("run") in started:
                started[line["run"]].update({k: v for k, v in line.items() if k not in ("event", "at")},
                                            ended_at=line.get("at"))
        return list(started.values())

    def records(self) -> list[dict[str, Any]]:
        """What the spend rules read: one row per run, with its phase, cost and status."""
        return self.runs()

    def find(self, phase: str, task: str, arm: str) -> list[dict[str, Any]]:
        """The runs of one phase, task and arm, oldest first, each with its ``run.json`` when it has one."""
        out = []
        for run in self.runs():
            if (run.get("phase"), run.get("task"), run.get("arm")) == (phase, task, arm):
                index = self.root / run["run"] / "run.json"
                out.append({**run, "record": json.loads(index.read_text(encoding="utf-8")) if index.is_file() else None,
                            "path": self.root / run["run"]})
        return sorted(out, key=lambda r: r["run"])

    def latest(self, phase: str, task: str, arm: str) -> dict[str, Any] | None:
        found = [r for r in self.find(phase, task, arm) if r["record"] is not None]
        return found[-1] if found else None


@dataclass
class RunFolder:
    archive: Archive
    path: Path
    rel: str
    phase: str
    task: str
    arm: str
    redactions: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False

    def _target(self, name: str) -> Path:
        target = (self.path / name).resolve()
        if not target.is_relative_to(self.path.resolve()) or name == "run.json":
            raise ArchiveError(f"{name!r} is not a file this run may write")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write(self, name: str, content: str | bytes) -> Path:
        """Create one file (refused if it exists). Text is redacted before it is written."""
        target = self._target(name)
        data = self.archive.redact(content).encode("utf-8") if isinstance(content, str) else content
        try:
            with target.open("xb") as fh:
                fh.write(data)
        except FileExistsError:
            raise ArchiveError(f"write-once: {self.rel}/{name} already exists") from None
        return target

    def write_json(self, name: str, data: Any) -> Path:
        return self.write(name, json.dumps(data, indent=1, sort_keys=True, default=str) + "\n")

    def stream(self, name: str):
        """A new file opened for this run's own appends (Claude Code's stream, as it arrives)."""
        target = self._target(name)
        try:
            return target.open("x", encoding="utf-8", newline="\n")
        except FileExistsError:
            raise ArchiveError(f"write-once: {self.rel}/{name} already exists") from None

    def fresh_path(self, name: str) -> Path:
        """A path for an outside writer (``docker cp``): it must not exist yet."""
        target = self._target(name)
        if target.exists():
            raise ArchiveError(f"write-once: {self.rel}/{name} already exists")
        return target

    def scan(self) -> list[dict[str, Any]]:
        """The key's value and ``sk-ant-`` strings in any file of the folder, replaced by ``<withheld>`` in place: the
        one change the archive allows. Each replacement is listed."""
        found = []
        for path in sorted(p for p in self.path.rglob("*") if p.is_file()):
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="surrogateescape")
            if not self.archive.redact.leaks(text):
                continue
            cleaned = self.archive.redact(text)
            path.write_bytes(cleaned.encode("utf-8", errors="surrogateescape"))
            found.append({"file": path.relative_to(self.path).as_posix(),
                          "replaced": cleaned.count(WITHHELD) - text.count(WITHHELD)})
        return found

    def index(self) -> dict[str, dict[str, Any]]:
        return {p.relative_to(self.path).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
                for p in sorted(self.path.rglob("*")) if p.is_file() and p.name != "run.json"}

    def finish(self, record: Mapping[str, Any], status: str) -> dict[str, Any]:
        """The key scan, then ``run.json`` (written once, last), then the ``ended`` line. A run that failed is finished
        like any other, so it is kept and reported."""
        if self.finished:
            raise ArchiveError(f"{self.rel} is already finished")
        self.redactions = self.scan()
        body = {**dict(record), "run": self.rel, "phase": self.phase, "task": self.task, "arm": self.arm,
                "status": status, "ended_at": now(), "redactions": self.redactions,
                "flagged": bool(self.redactions) or bool(record.get("flagged")), "files": self.index()}
        text = self.archive.redact(json.dumps(body, indent=1, sort_keys=True, default=str) + "\n")
        try:
            with (self.path / "run.json").open("x", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        except FileExistsError:
            raise ArchiveError(f"write-once: {self.rel}/run.json already exists") from None
        self.finished = True
        self.archive._append({"event": "ended", "run": self.rel, "status": status, "at": now(),
                              "cost_usd": float(record.get("cost_usd") or 0.0),
                              "estimated_usd": record.get("estimated_usd"), "reported_usd": record.get("reported_usd"),
                              "agent_started": bool(record.get("agent_started")), "flagged": body["flagged"]})
        return json.loads(text)


def manifest(root: Path) -> dict[str, Any]:
    """Every archived file's path (relative to the archive root), size and SHA-256."""
    files = {p.relative_to(root).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
             for p in sorted(root.rglob("*")) if p.is_file()}
    return {"study": STUDY, "made_at": now(), "files": files, "count": len(files)}


# --- small helpers ----------------------------------------------------------------------------------------------------
def write_json(path: Path, data: Any, redactor: Redactor | None = None) -> None:
    """A summary for the repository (results/V7), written whole: a temporary file, then a rename."""
    text = json.dumps(data, indent=1, sort_keys=True, default=str) + "\n"
    if redactor is not None:
        text = redactor(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{id(data)}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    for attempt in range(50):          # Windows refuses the rename while another thread has the file open to read
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text_lf(path: Path) -> str:
    """A code or text file's SHA-256 with LF line endings (how the registry hashes them)."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_splits(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
