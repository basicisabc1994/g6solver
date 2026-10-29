# SPDX-License-Identifier: Apache-2.0
"""The session launcher V9's and V10's protocols assume: it starts the Claude Code sessions for patch generation
(``patch``), V9's arm 2 (``self_review``: the session that wrote the patch, resumed with arm 2's prompt) and V10's arm
R (``review_r``: a fresh session in R's copy), and writes the session records arm 2, arm R and the cost accounting read.

It reuses V7's runner (``validation/v7_swebench/run_arm.py``) where it fits: each session runs Claude Code headless in
a container on an internal network whose only way out is a proxy that lets through the model's endpoint, so the
commands a session runs have no network; Claude Code's stream is read and tallied as V7 read it. The container has no
saved memory, plugin, skill, hook, tool server or instruction file of its own, and ``--strict-mcp-config`` loads no
tool server; the repository's own files are all it reads.

**The key** is read by the caller through ``g6.core.keys`` and given here only to the container client's environment,
by name (``-e ANTHROPIC_API_KEY``): never in a command, a record or a transcript. A stream line that holds it is written
with it masked (``MASK``).

**The dollar stop** (``Ledger``) is one cap for the whole rehearsal, kept in a file so every step counts against it.
Before a session starts, it reserves the session's budget (``--max-budget-usd``) plus ``MARGIN_USD`` for the one model
message that can complete after Claude Code reaches its budget; a session the cap cannot cover is not started. After
it, the session is charged its usage at ``PRICES`` (a cache write at twice the input price, the one-hour rate, so the
count errs high), and an unsettled reservation stays counted.

**The record** is V9's arm 2 session record (``self_review``: task_id, patch_sha256, answer, ended, elapsed_s, tokens,
model_calls) with the model, the usage by kind, the dollars, the session id and the transcript's file name, which arm R
reads (``bindings.Sessions``, ``arm_r.cost_cents``). ``tokens`` counts every kind of token the usage reports.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
V7_FOLDER = HERE.parent / "v7_swebench"
if str(V7_FOLDER) not in sys.path:
    sys.path.insert(0, str(V7_FOLDER))

import run_arm as v7  # noqa: E402

#: The model V9's sessions use: Claude Code with claude-haiku-4-5, the owner's choice for V9 (2026-10-28), which the
#: feasibility freeze entry names (ruling on item 73). Effort is Claude Code's default for the model: none is set.
MODEL, EFFORT = "claude-haiku-4-5", None
EFFORT_NOTE = "Claude Code's default for the model (none set)"
#: Anthropic's published prices for MODEL, US dollars per million tokens (read 2026-10-29): input 1.00, output 5.00, a
#: cache read a tenth of input, a five-minute cache write 1.25 times input and a one-hour write twice input. The stop
#: counts every cache write at the higher rate (``PRICES``); the accounting prices each at its own (``accounted``).
INPUT, OUTPUT = 1.00, 5.00
PRICES = {MODEL: {"input_tokens": INPUT, "output_tokens": OUTPUT, "cache_read_input_tokens": INPUT / 10,
                  "cache_creation_input_tokens": INPUT * 2}}
MARGIN_USD = 0.50
KINDS = ("patch", "self_review", "review_r")
#: Each kind's caps: turns, wall clock (arm 2 and arm R have V9's arm 2 limit, 30 minutes) and dollars.
CAPS = {"patch": {"max_turns": 40, "wall_clock_s": 1200, "max_usd": 1.50},
        "self_review": {"max_turns": 20, "wall_clock_s": 1800, "max_usd": 0.75},
        "review_r": {"max_turns": 30, "wall_clock_s": 1800, "max_usd": 1.00}}
MASK = "<the key, masked>"
#: How V7's statuses read as V9's session endings.
ENDED = {"finished": "completed", "timeout": "timed_out", "cap_turns": "token_cap", "cap_cost": "token_cap",
         "refused": "refused", "error": "error"}


class Stopped(RuntimeError):
    """The dollar stop: the cap cannot cover what was asked."""


def dollars(usage: dict, model: str = MODEL) -> float:
    """The stop's count: every kind at ``PRICES``, a cache write at the higher rate."""
    price = PRICES[model]
    return sum(int(usage.get(kind) or 0) * rate for kind, rate in price.items()) / 1e6


def accounted(usage: dict) -> float:
    """The cost record's count (ruling on item 76): each cache write at its own published rate where the usage says
    which (five minutes 1.25 times input, one hour twice), else twice."""
    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else None
    writes = (int(split.get("ephemeral_5m_input_tokens") or 0) * INPUT * 1.25
              + int(split.get("ephemeral_1h_input_tokens") or 0) * INPUT * 2) if split else \
        int(usage.get("cache_creation_input_tokens") or 0) * INPUT * 2
    return (int(usage.get("input_tokens") or 0) * INPUT + int(usage.get("output_tokens") or 0) * OUTPUT
            + int(usage.get("cache_read_input_tokens") or 0) * INPUT / 10 + writes) / 1e6


class Ledger:
    """The rehearsal's one dollar stop, in a JSON file (the module docstring)."""

    def __init__(self, path: str | Path, cap: float):
        self.path = Path(path)
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if cap > data["cap"]:
                raise Stopped(f"the cap is {data['cap']} dollars; a later step may lower it, never raise it")
            data["cap"] = min(cap, data["cap"])
        else:
            data = {"cap": float(cap), "spent": 0.0, "open": {}, "entries": []}
        self.data = data
        self._save()

    @property
    def cap(self) -> float:
        return self.data["cap"]

    @property
    def spent(self) -> float:
        return self.data["spent"]

    def open_dollars(self) -> float:
        return sum(item["most"] for item in self.data["open"].values())

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1) + "\n", encoding="utf-8")

    def reserve(self, what: str, most: float) -> str:
        if self.spent + self.open_dollars() + most > self.cap + 1e-9:
            raise Stopped(f"{what} could cost {most:.2f} dollars: with {self.spent:.4f} spent and "
                          f"{self.open_dollars():.2f} reserved, that would pass the cap of {self.cap:.2f}")
        ident = uuid.uuid4().hex[:12]
        self.data["open"][ident] = {"what": what, "most": float(most), "at": time.time()}
        self._save()
        return ident

    def settle(self, ident: str, spent: float, detail: dict) -> None:
        held = self.data["open"].pop(ident)
        self.data["spent"] = round(self.data["spent"] + float(spent), 6)
        self.data["entries"].append({"what": held["what"], "reserved": held["most"], "dollars": round(spent, 6),
                                     **detail})
        self._save()


def claude_args(*, caps: dict, resume: str = "", model: str = MODEL, effort: str | None = EFFORT) -> list[str]:
    """V7's flags, with the model and effort the rehearsal names."""
    args = [v7.CLAUDE_IN, "-p", "--model", model, *(["--effort", effort] if effort else []), "--output-format",
            "stream-json", "--verbose",
            "--max-turns", str(caps["max_turns"]), "--max-budget-usd", f"{caps['max_usd']:.2f}",
            "--dangerously-skip-permissions", "--disallowedTools", "WebFetch", "WebSearch", "--strict-mcp-config"]
    return args + (["--resume", resume] if resume else [])


def _writes(tally) -> dict:
    """Cache writes by lifetime, summed over the session's messages, for arm 2's token cap at the freeze."""
    found = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}
    for usage in tally.usage_by_message.values():
        for kind, count in (usage.get("cache_creation") or {}).items():
            found[kind] = found.get(kind, 0) + int(count or 0)
    return found


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: The rehearsal's patch-generation prompt: the task statement as V9 gives it (its problem, requirements and
#: interface). V9 freezes its own prompt in the entry; this one is the rehearsal's, not registered.
PATCH_PROMPT = """{problem}

Requirements:
{requirements}

Interface:
{interface}

Make the change in this repository's working tree."""
#: Arm R's diff, beside its registered prompt (INTERPRETATIONS.md, item 47), in Claude Code's appended system prompt.
DIFF_BESIDE = "The patch under review, as a unified diff:\n\n{diff}"


def run_session(kind: str, *, prompt: str, docker, box, key: str, ledger: Ledger, record_dir: Path, task_id: str,
                patch: str, caps: dict, resume: str = "", extra: tuple = ()) -> dict:
    """One session in ``box``: the prompt on standard input, the stream tallied and written (the key masked), the
    session charged its usage. Writes ``<task>.jsonl`` (the transcript) and ``<task>.json`` (the record) in
    ``record_dir`` and returns the record."""
    if kind not in KINDS:
        raise ValueError(f"a session is one of {KINDS}")
    ident = ledger.reserve(f"{kind}:{task_id}", caps["max_usd"] + MARGIN_USD)
    record_dir = Path(record_dir)
    record_dir.mkdir(parents=True, exist_ok=True)
    tally, started, stop = v7.Tally(), time.monotonic(), "error"
    try:
        args = claude_args(caps=caps, resume=resume) + list(extra)
        env_names = ["ANTHROPIC_API_KEY", f"HTTPS_PROXY=http://{box.sidecar}:{v7.PROXY_PORT}",
                     f"HTTP_PROXY=http://{box.sidecar}:{v7.PROXY_PORT}", "NO_PROXY=localhost,127.0.0.1",
                     "IS_SANDBOX=1", "DISABLE_AUTOUPDATER=1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
                     "DISABLE_TELEMETRY=1", "DISABLE_ERROR_REPORTING=1"]
        docker_args = ["exec", "-i", "-u", "root", "-w", box.repo, *[a for e in env_names for a in ("-e", e)],
                       box.agent, *args]
        (record_dir / f"{task_id}.command.json").write_text(json.dumps(
            {"kind": kind, "docker": docker_args, "caps": caps, "model": MODEL, "effort": EFFORT, "resume": resume,
             "key": "passed by name; its value only in the container client's environment"}, indent=1) + "\n",
            encoding="utf-8")
        env = {**v7._base_environ(), "ANTHROPIC_API_KEY": key}
        proc = docker.popen(*docker_args, env=env)
        errors: list[bytes] = []
        drain = threading.Thread(target=lambda: errors.append(proc.stderr.read() or b"") if proc.stderr else None,
                                 daemon=True)
        drain.start()
        with open(record_dir / f"{task_id}.jsonl", "w", encoding="utf-8", newline="\n") as transcript:
            try:
                proc.stdin.write(prompt.encode("utf-8"))
                proc.stdin.close()
                stop = v7.read_stream(iter(proc.stdout.readline, b""), tally,
                                      deadline=time.monotonic() + float(caps["wall_clock_s"]),
                                      max_turns=int(caps["max_turns"]), max_usd=float(caps["max_usd"]),
                                      write=lambda line: (transcript.write(line.replace(key, MASK) + "\n"),
                                                          transcript.flush()))
            finally:
                if proc.poll() is None:
                    docker.sh(box.agent, v7.KILL_AGENT, timeout=60)
                    proc.kill()
                proc.wait(timeout=60)
        drain.join(timeout=30)
        (record_dir / f"{task_id}.stderr.txt").write_text(
            b"".join(errors).decode("utf-8", errors="replace").replace(key, MASK), encoding="utf-8")
    finally:
        cost = round(sum(dollars(u) for u in tally.usage_by_message.values()), 6)
        ledger.settle(ident, cost, {"model_calls": tally.model_calls, "usage": tally.usage,
                                    "reported_usd": (tally.result or {}).get("total_cost_usd")})
    status = v7.status_of(stop, tally)
    result = tally.result or {}
    record = {"task_id": task_id, "kind": kind, "patch_sha256": _sha256(patch),
              "answer": str(result.get("result") or ""), "ended": ENDED.get(status, "error"), "status": status,
              "elapsed_s": round(time.monotonic() - started, 3), "tokens": sum(tally.usage.values()),
              "model_calls": tally.model_calls, "model": MODEL, "effort": EFFORT_NOTE,
              "claude_code": getattr(box, "claude_code", None),
              "configuration_empty": getattr(box, "configuration_empty", None), "usage": tally.usage,
              "cache_writes": _writes(tally), "dollars": cost,
              "accounted_dollars": round(sum(accounted(u) for u in tally.usage_by_message.values()), 6),
              "reported_usd": result.get("total_cost_usd"), "num_turns": result.get("num_turns"),
              "session_id": tally.session_id, "transcript": f"{task_id}.jsonl"}
    (record_dir / f"{task_id}.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    return record


class Env:
    """What V7's container functions read of a task's environment: its image and its limits."""

    def __init__(self, image: str, cpus: float = 2.0, memory_mb: int = 4096):
        self.image, self.cpus, self.memory_mb = image, cpus, memory_mb


def open_box(docker, assets, image: str, tag: str):
    """A container of ``image`` on its own internal network, the proxy beside it, Claude Code installed (V7's)."""
    env = Env(image)
    net = v7.start_net(docker, env, assets, tag)
    try:
        box = v7.start_box(docker, env, image, net, f"rehearsal-{tag}")
        box.claude_code = v7.install_claude(docker, box, assets).strip().splitlines()[-1]
        # the record says the configuration folder was empty as checked at the start (ruling on item 46)
        box.configuration_empty = v7.out(docker.sh(box.agent, "ls -A /root/.claude 2>/dev/null | wc -l")) == "0"
    except Exception:
        net.close()
        raise
    return box


def close_box(box) -> None:
    box.close()
    if box.net is not None:
        box.net.close()


def _put(docker, box, folder: Path) -> None:
    v7.checked(docker("cp", f"{folder}/.", f"{box.agent}:{box.repo}", timeout=600), "copy the repository in")
    docker.sh(box.agent, "git config --global --add safe.directory '*'")


def patch_and_self_review(task_id: str, *, statement: dict | str, requirements: list[dict], source: Path, docker,
                          assets, image: str, key: str, ledger: Ledger, out: Path, time_limit_s: int,
                          token_cap: int) -> dict:
    """The patch session on ``source`` (the base commit's tree), the patch captured as V7 captured it (V9's
    ``patches/<task>.diff``), then V9's arm 2 in the same session, resumed with arm 2's prompt (``sessions/<task>.json``,
    with its transcript beside it)."""
    from patch_review_utility import contract, self_review

    said = statement if isinstance(statement, dict) else {"problem_statement": statement}
    out = Path(out)
    box = open_box(docker, assets, image, f"{task_id[:20]}-{uuid.uuid4().hex[:6]}")
    try:
        _put(docker, box, Path(source))
        prompt = PATCH_PROMPT.format(problem=said.get("problem_statement", ""),
                                     requirements=said.get("requirements", "") or contract.render(requirements),
                                     interface=said.get("interface", "") or "No new interface.")
        written = run_session("patch", prompt=prompt, docker=docker, box=box, key=key, ledger=ledger,
                              record_dir=out / "patch-sessions", task_id=task_id, patch="", caps=CAPS["patch"])
        docker.sh(box.agent, v7.CAPTURE, timeout=600)
        target = out / "patches" / f"{task_id}.diff"
        target.parent.mkdir(parents=True, exist_ok=True)
        v7.checked(docker("cp", f"{box.agent}:/tmp/v7_model.patch", str(target), timeout=600), "capture the patch")
        patch = target.read_text(encoding="utf-8")
        prepared = self_review.prepare(task_id, requirements, patch=patch, time_limit_s=time_limit_s,
                                       token_cap=token_cap)
        reviewed = run_session("self_review", prompt=prepared["prompt"], docker=docker, box=box, key=key,
                               ledger=ledger, record_dir=out / "sessions", task_id=task_id, patch=patch,
                               caps=CAPS["self_review"], resume=written["session_id"])
    finally:
        close_box(box)
    return {"patch": written, "self_review": reviewed, "patch_text": patch}


def review_r(task_id: str, *, prepared: dict, copy: Path, docker, assets, image: str, key: str, ledger: Ledger,
             sessions: Path) -> dict:
    """Arm R: a fresh session in R's copy (``arm_r.make_copy``), the registered prompt on standard input and the diff
    beside it; the tests it wrote in its folder are copied back into the copy for ``arm_r.collect``. Writes the record
    and transcript in ``sessions`` (``bindings.Sessions``)."""
    from patch_review_search import arm_r

    box = open_box(docker, assets, image, f"r-{task_id[:20]}-{uuid.uuid4().hex[:6]}")
    try:
        _put(docker, box, Path(copy))
        record = run_session("review_r", prompt=prepared["prompt"], docker=docker, box=box, key=key, ledger=ledger,
                             record_dir=sessions, task_id=task_id, patch=prepared["diff"], caps=CAPS["review_r"],
                             extra=("--append-system-prompt", DIFF_BESIDE.format(diff=prepared["diff"])))
        (Path(copy) / arm_r.FOLDER).mkdir(exist_ok=True)
        docker("cp", f"{box.agent}:{box.repo}/{arm_r.FOLDER}/.", str(Path(copy) / arm_r.FOLDER), timeout=600)
    finally:
        close_box(box)
    return record
