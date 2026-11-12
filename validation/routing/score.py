# SPDX-License-Identifier: Apache-2.0
"""Scoring the routing trials (0.3 design 14.1, the "Routing" row of section 5). Offline and deterministic: it reads
tasks, transcripts and gates, and calls nothing.

A task is one fresh agent session on a synthetic repository with G6's MCP server and the installed skill. A positive
task has calls the agent should make, a negative task has calls it should not make, and a missing-tool task withholds a
tool the skill names. The unit is the task. Every rate keeps its full denominator: a task that was refused, could not
be run or was interrupted stays in it and counts against every gate it belongs to, so neither calling everything nor
refusing everything can pass. Results are per client and never pooled.

``score`` scores the transcripts it is handed. The gate's own reading is ``run.py``'s ``gate_reading``, which hands
it transcripts read again from each session's kept event log and adds the ``evidence`` block; ``release`` reads
``pass`` from no report without that block, without a client named for every row, or scored on bars other than the
pinned ones (``frozen.json``, beside this file).

Standard library only; ``run.py`` and the tests load this file by path.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FORMAT_TASKS = "g6.routing_tasks/1"
FORMAT_GATES = "g6.routing_gates/1"
FORMAT_REPORT = "g6.routing_report/1"
STATES = ("completed", "refused", "unavailable", "interrupted")
POLARITIES = ("positive", "negative", "missing_tool")
COHORTS = ("development", "final")
COMPLETIONS = ("calls_in_order", "final_states")
BARS = ("min_positive_completion", "min_positive_lower_bound", "max_negative_unnecessary", "max_malformed",
        "min_missing_tool_recovery")
#: The tool an agent falls back to when a named tool is absent (the root skill's rule).
FIND = "g6_find"
_ABSENT = re.compile(r"\b(absent|not (?:available|listed|registered|installed|present|there)|unavailable|missing|"
                     r"does(?:n't| not) (?:exist|list|have)|is(?:n't| not) (?:available|listed))\b", re.IGNORECASE)
#: The pinned digests of the bars and of the development task list; ``pinned`` reads them.
PINS = Path(__file__).resolve().parent / "frozen.json"
#: What a client's final reading is called when it is not ``pass``.
READINGS = {"fail": "routing bar not met", "not_run": "not run", "simulated": "simulated sessions only",
            "unattributed": "transcripts do not name the client that produced them",
            "unevidenced": "not read from the sessions' kept event logs",
            "other_bars": "scored on bars that are not the pinned ones"}
#: What a pass does not show, carried in every report.
LIMITS = ("The repositories are synthetic and the tasks are short, so a pass is an upper bound for real work.",
          "A task that was refused, could not be run or was interrupted counts against every gate it belongs to.",
          "Results are per client and are never pooled into one pass.")


class TaskError(ValueError):
    """A task list or a gate file is outside its format."""


@dataclass(frozen=True)
class Transcript:
    task_id: str
    client: str
    state: str                                   # completed | refused | unavailable | interrupted
    calls: tuple[Mapping[str, Any], ...] = ()    # each {"server", "tool", "args", "seconds", "valid", "result_state"}
    questions: int = 0
    seconds: float = 0.0
    cost_usd: float | None = None                # None: the client reported no cost; never estimated
    final_text: str = ""
    repeat: int = 0                              # 0 is the run the gate reads; 1 and 2 are consistency repeats
    code: str = ""                               # the refusal code, when refused
    produced: str = ""                           # "client:<program> <version>", or "stand-in:<what stood in>"
    exit_code: int | None = None                 # the client process's exit code, when one ran
    events_sha256: str = ""                      # the digest of the event log the transcript was read from

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise TaskError(f"transcript state {self.state!r} is not one of {STATES}")

    def to_json(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "client": self.client, "state": self.state,
                "calls": [dict(call) for call in self.calls], "questions": self.questions, "seconds": self.seconds,
                "cost_usd": self.cost_usd, "final_text": self.final_text, "repeat": self.repeat, "code": self.code,
                "produced": self.produced, "exit_code": self.exit_code, "events_sha256": self.events_sha256}

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> "Transcript":
        return cls(task_id=row["task_id"], client=row["client"], state=row["state"],
                   calls=tuple(dict(call) for call in row.get("calls", ())), questions=int(row.get("questions", 0)),
                   seconds=float(row.get("seconds", 0.0)), cost_usd=row.get("cost_usd"),
                   final_text=str(row.get("final_text", "")), repeat=int(row.get("repeat", 0)),
                   code=str(row.get("code", "")), produced=str(row.get("produced", "")),
                   exit_code=row.get("exit_code"), events_sha256=str(row.get("events_sha256", "")))


def _once(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise TaskError("the pin file names a key twice")
    return dict(pairs)


def pinned(path: Path | None = None) -> dict[str, str]:
    """The pinned digests: exactly ``bars_digest`` and ``tasks_digest``. ``TaskError`` for any other file."""
    try:
        found = json.loads(Path(path or PINS).read_text(encoding="utf-8"), object_pairs_hook=_once)
    except TaskError:
        raise
    except (OSError, ValueError) as exc:
        raise TaskError(f"the pin file could not be read: {type(exc).__name__}") from exc
    if not isinstance(found, dict) or set(found) != {"bars_digest", "tasks_digest"} \
            or not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) for v in found.values()):
        raise TaskError("frozen.json holds exactly bars_digest and tasks_digest")
    return found


def named_client(produced: str) -> bool:
    """Whether a transcript names the client program that produced it: ``client:`` and then the program."""
    return produced.startswith("client:") and bool(produced[len("client:"):].strip())


def _need(condition: bool, said: str) -> None:
    if not condition:
        raise TaskError(said)


def _pattern(task_id: str, pattern: Any) -> None:
    _need(isinstance(pattern, dict) and set(pattern) <= {"tool", "server", "args_include"}
          and isinstance(pattern.get("tool"), str) and pattern["tool"].strip() == pattern["tool"] != ""
          and "*" not in pattern["tool"] and isinstance(pattern.get("args_include", {}), dict),
          f"task {task_id}: a call pattern is {{tool, server?, args_include?}} with a literal tool name")


def validate_tasks(data: Any) -> dict[str, Any]:
    """The task list when it is inside its format; ``TaskError`` naming the first fault otherwise."""
    _need(isinstance(data, dict) and set(data) == {"format", "routes", "tasks"}, "a task list is {format, routes, tasks}")
    _need(data["format"] == FORMAT_TASKS, f"format is not {FORMAT_TASKS}")
    _need(isinstance(data["routes"], dict) and all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
                                                    for v in data["routes"].values()),
          "routes maps each route id to the sha256 that binds it")
    seen: set[str] = set()
    for task in data["tasks"]:
        _need(isinstance(task, dict), "a task is an object")
        task_id = task.get("id")
        _need(isinstance(task_id, str) and bool(re.fullmatch(r"[a-z0-9_.-]+", task_id)), f"task id {task_id!r}")
        _need(task_id not in seen, f"task {task_id} is listed twice")
        seen.add(task_id)
        required = {"id", "route", "polarity", "cohort", "prompt", "fixture", "budget"}
        optional = {"servers", "withhold", "expected_calls", "forbidden", "completion", "permissions"}
        _need(required <= set(task) <= required | optional, f"task {task_id}: fields {sorted(task)}")
        _need(task["route"] in data["routes"], f"task {task_id}: route {task['route']!r} has no bound digest")
        _need(task["polarity"] in POLARITIES and task["cohort"] in COHORTS, f"task {task_id}: polarity or cohort")
        _need(isinstance(task["prompt"], str) and len(task["prompt"].strip()) >= 20, f"task {task_id}: prompt")
        _need(isinstance(task["fixture"], str) and bool(re.fullmatch(r"[a-z0-9_]+", task["fixture"])),
              f"task {task_id}: fixture")
        budget = task["budget"]
        _need(isinstance(budget, dict) and set(budget) == {"seconds", "calls", "questions"}
              and all(isinstance(budget[k], int) and not isinstance(budget[k], bool) and budget[k] >= 0 for k in budget)
              and budget["seconds"] > 0, f"task {task_id}: budget is {{seconds, calls, questions}} in whole numbers")
        servers = task.get("servers", ["g6"])
        _need(isinstance(servers, list) and bool(servers) and all(isinstance(s, str) for s in servers),
              f"task {task_id}: servers")
        for pattern in task.get("expected_calls", []):
            _pattern(task_id, pattern)
        forbidden = task.get("forbidden", [])
        _need(forbidden == "any" or isinstance(forbidden, list), f"task {task_id}: forbidden is \"any\" or a list")
        for pattern in ([] if forbidden == "any" else forbidden):
            _pattern(task_id, pattern)
        permissions = task.get("permissions", [])
        _need(isinstance(permissions, list) and all(isinstance(p, str) and re.fullmatch(r"[a-z_]+(\.[a-z_]+)?", p)
                                                    for p in permissions),
              f"task {task_id}: permissions are host permissions, component or component.op")
        withhold = task.get("withhold", [])
        _need(isinstance(withhold, list) and all(isinstance(t, str) and t for t in withhold), f"task {task_id}: withhold")
        if task["polarity"] == "positive":
            completion = task.get("completion")
            _need(bool(task.get("expected_calls")) and isinstance(completion, dict)
                  and completion.get("kind") in COMPLETIONS and not withhold and not forbidden,
                  f"task {task_id}: a positive task has expected_calls and a completion, and withholds nothing")
            if completion["kind"] == "final_states":
                _need(isinstance(completion.get("tool"), str) and isinstance(completion.get("states"), list)
                      and bool(completion["states"]), f"task {task_id}: final_states names a tool and its states")
        elif task["polarity"] == "negative":
            _need(bool(forbidden) and not task.get("expected_calls") and not withhold,
                  f"task {task_id}: a negative task says what must not be called, and expects and withholds nothing")
        else:
            _need(bool(withhold) and not task.get("expected_calls") and not forbidden,
                  f"task {task_id}: a missing-tool task withholds a tool, and expects and forbids nothing")
    return data


def validate_gates(data: Any) -> dict[str, Any]:
    """The gate file when it is inside its format: the four bars and the interval floor, each a share in [0, 1]."""
    fields = {"format", "final_tasks_digest", "interval", "release_rule", "final_cohort_shape", "consistency_repeats",
              *BARS}
    _need(isinstance(data, dict) and set(data) == fields, f"a gate file has exactly {sorted(fields)}")
    _need(data["format"] == FORMAT_GATES, f"format is not {FORMAT_GATES}")
    _need(all(isinstance(data[bar], float) and 0.0 <= data[bar] <= 1.0 for bar in BARS), "each bar is a share")
    _need(data["final_tasks_digest"] is None or bool(re.fullmatch(r"[0-9a-f]{64}", str(data["final_tasks_digest"]))),
          "final_tasks_digest is null until the final cohort is frozen, then its sha256")
    _need(data["interval"] == "wilson95" and data["release_rule"] == "at_least_one_client_passes",
          "interval and release rule")
    return data


def wilson_lower(successes: int, total: int) -> float:
    """The lower end of the Wilson 95% interval for ``successes`` of ``total``; 0.0 for an empty denominator."""
    if total <= 0:
        return 0.0
    z, share = 1.959963984540054, successes / total
    centre = share + z * z / (2 * total)
    half = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total))
    return max(0.0, (centre - half) / (1 + z * z / total))


def matches(call: Mapping[str, Any], pattern: Mapping[str, Any]) -> bool:
    """Whether a recorded call is the one a pattern describes: the same server and tool, and every ``args_include``
    value present (a string as a substring of the argument's text, anything else as an equal value)."""
    if call.get("tool") != pattern["tool"] or call.get("server", "g6") != pattern.get("server", "g6"):
        return False
    args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
    for name, wanted in pattern.get("args_include", {}).items():
        actual = args.get(name)
        if isinstance(wanted, str):
            if not isinstance(actual, str) or wanted not in actual:
                return False
        elif actual != wanted:
            return False
    return True


def malformed(call: Mapping[str, Any], task: Mapping[str, Any], schemas: Mapping[str, Mapping[str, Any]] | None) -> bool:
    """Whether a call was badly formed: the session said so (``valid`` false), or, against the registered schemas, it
    names a tool no server registers, an argument the tool does not take, or lacks a required one. A call to a tool
    this task withholds is not malformed: it is a call to an absent tool, which the recovery count reads."""
    if call.get("tool") in task.get("withhold", ()):
        return False
    if call.get("valid") is False:
        return True
    if schemas is None:
        return False
    schema = schemas.get(f"{call.get('server', 'g6')}/{call.get('tool')}")
    if schema is None:
        return True
    args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
    return bool(set(args) - set(schema.get("properties", ()))) or bool(set(schema.get("required", ())) - set(args))


def judge(task: Mapping[str, Any], transcript: Transcript | None,
          schemas: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """One task's result. ``None`` (no transcript) is an unavailable run, counted like any other."""
    state = transcript.state if transcript is not None else "unavailable"
    calls = transcript.calls if transcript is not None else ()
    budget = task["budget"]
    bad = [malformed(call, task, schemas) for call in calls]
    # a call with no time of its own is as late as its whole session: timely only when the session met the budget
    session = transcript.seconds if transcript is not None else 0.0
    timely = [index < budget["calls"]
              and float(call["seconds"] if call.get("seconds") is not None else session) <= budget["seconds"]
              for index, call in enumerate(calls)]
    out: dict[str, Any] = {
        "task_id": task["id"], "route": task["route"], "polarity": task["polarity"], "state": state,
        "calls": len(calls), "malformed_calls": sum(bad), "questions": transcript.questions if transcript else 0,
        "seconds": transcript.seconds if transcript else 0.0,
        "cost_usd": transcript.cost_usd if transcript else None,
        "over_budget": {"seconds": bool(transcript) and transcript.seconds > budget["seconds"],
                        "calls": len(calls) > budget["calls"],
                        "questions": bool(transcript) and transcript.questions > budget["questions"]},
    }
    done = state == "completed"
    out["well_formed"] = done and not any(bad)
    if task["polarity"] == "positive":
        matched, start = 0, 0
        for pattern in task["expected_calls"]:
            found = next((i for i in range(start, len(calls))
                          if timely[i] and not bad[i] and matches(calls[i], pattern)), None)
            if found is None:
                break
            matched, start = matched + 1, found + 1
        reached = matched == len(task["expected_calls"])
        completion = task["completion"]
        if completion["kind"] == "final_states":
            reached = reached and any(timely[i] and not bad[i] and call.get("tool") == completion["tool"]
                                      and call.get("result_state") in completion["states"]
                                      for i, call in enumerate(calls))
        out.update(expected_calls=len(task["expected_calls"]), timely_valid_calls=matched,
                   useful_completion=done and reached and not out["over_budget"]["questions"])
    elif task["polarity"] == "negative":
        forbidden = task["forbidden"]
        unnecessary = [call for call in calls if forbidden == "any" or any(matches(call, p) for p in forbidden)]
        out.update(unnecessary_calls=len(unnecessary), quiet=done and not unnecessary)
    else:
        withheld = [call for call in calls if call.get("tool") in task["withhold"]]
        text = transcript.final_text if transcript else ""
        said = bool(_ABSENT.search(text)) and any(tool in text for tool in task["withhold"])
        used_find = any(call.get("tool") == FIND and not bad[i] for i, call in enumerate(calls))
        out.update(withheld_calls=len(withheld), said_absent=said, used_find=used_find,
                   recovered=done and len(withheld) <= 1 and (said or used_find))
    return out


def _tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def of(polarity: str) -> list[Mapping[str, Any]]:
        return [row for row in rows if row["polarity"] == polarity]

    positive, negative, missing = of("positive"), of("negative"), of("missing_tool")
    costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    return {
        "scheduled": len(rows), **{state: sum(row["state"] == state for row in rows) for state in STATES},
        "positive": {"tasks": len(positive), "useful_completion": sum(row["useful_completion"] for row in positive),
                     "expected_calls": sum(row["expected_calls"] for row in positive),
                     "timely_valid_calls": sum(row["timely_valid_calls"] for row in positive)},
        "negative": {"tasks": len(negative), "quiet": sum(row["quiet"] for row in negative),
                     "with_unnecessary_calls": sum(row["unnecessary_calls"] > 0 for row in negative),
                     "not_completed": sum(row["state"] != "completed" for row in negative)},
        "missing_tool": {"tasks": len(missing), "recovered": sum(row["recovered"] for row in missing),
                         "repeated_the_call": sum(row["withheld_calls"] > 1 for row in missing)},
        "malformed": {"tasks": len(rows), "well_formed": sum(row["well_formed"] for row in rows),
                      "with_malformed_calls": sum(row["malformed_calls"] > 0 for row in rows)},
        "over_budget": {kind: sum(row["over_budget"][kind] for row in rows) for kind in ("seconds", "calls", "questions")},
        "calls": sum(row["calls"] for row in rows), "questions": sum(row["questions"] for row in rows),
        "seconds": round(sum(row["seconds"] for row in rows), 3),
        "cost": {"usd": round(sum(costs), 6) if costs else None, "measured_tasks": len(costs),
                 "unmeasured_tasks": len(rows) - len(costs)},
    }


def _digest(value: Any) -> str:
    """The canonical digest ``validation/freeze.py`` gives the same value."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def score(tasks: Mapping[str, Any], transcripts: Sequence[Transcript], gates: Mapping[str, Any], *, client: str,
          cohort: str, schemas: Mapping[str, Mapping[str, Any]] | None = None,
          require_client: bool = True) -> dict[str, Any]:
    """The report for one client on one cohort: per route and overall, every count with its denominator, each gate with
    its value, bar and ``passed``, and ``decision`` (``pass``, ``fail``, or ``not_run`` when no task was run at all).

    A transcript of another client or of a task outside the cohort is ignored; a scheduled task with no transcript is
    ``unavailable``. Repeats (``repeat`` 1 and 2) feed only the consistency figure, which has no bar.

    A gate is read from one transcript per task: two transcripts of one task and repeat raise ``TaskError``, so a
    cohort run a second time cannot replace its first reading. ``decision`` is ``pass`` only when every gate passed
    and no gate transcript says a stand-in produced it (``simulated``: a simulated session can fail a gate and never
    passes one), and every gate transcript names the client program that produced it (``unattributed`` otherwise;
    ``require_client=False`` is for a caller that wants the gates' arithmetic alone and reads no decision from it).
    The report names the digests of the task list and the gate file it was scored with. It scores what it is handed:
    whether those transcripts are the kept event logs' own reading is ``run.py``'s ``gate_reading`` to say."""
    validate_tasks(dict(tasks))
    validate_gates(dict(gates))
    if cohort not in COHORTS:
        raise TaskError(f"cohort {cohort!r}")
    scheduled = [task for task in tasks["tasks"] if task["cohort"] == cohort]
    mine = [t for t in transcripts if t.client == client]
    ids = {task["id"] for task in scheduled}
    seen: set[tuple[str, int]] = set()
    for transcript in mine:
        key = (transcript.task_id, transcript.repeat)
        if transcript.task_id in ids and key in seen:
            raise TaskError(f"task {key[0]} has two transcripts for repeat {key[1]}: a gate is read from one run")
        seen.add(key)
    first = {t.task_id: t for t in mine if t.repeat == 0 and t.task_id in ids}
    rows = [judge(task, first.get(task["id"]), schemas) for task in scheduled]
    overall = _tally(rows)
    share = lambda part, whole: (part / whole) if whole else None   # noqa: E731

    pos, neg, mis, mal = overall["positive"], overall["negative"], overall["missing_tool"], overall["malformed"]
    lower = wilson_lower(pos["useful_completion"], pos["tasks"])
    values = {
        "min_positive_completion": (share(pos["useful_completion"], pos["tasks"]), ">="),
        "min_positive_lower_bound": (lower if pos["tasks"] else None, ">="),
        "max_negative_unnecessary": (share(neg["tasks"] - neg["quiet"], neg["tasks"]), "<="),
        "max_malformed": (share(mal["tasks"] - mal["well_formed"], mal["tasks"]), "<="),
        "min_missing_tool_recovery": (share(mis["recovered"], mis["tasks"]), ">="),
    }
    judged = {}
    for bar, (value, direction) in values.items():
        passed = value is not None and (value >= gates[bar] if direction == ">=" else value <= gates[bar])
        judged[bar] = {"value": None if value is None else round(value, 4), "bar": gates[bar], "passed": passed}
    ran = any(row["state"] != "unavailable" for row in rows)
    made = [t.produced for t in first.values()]
    produced = {"client": sum(named_client(p) for p in made),
                "stand_in": sum(p.startswith("stand-in") for p in made),
                "unattributed": sum(not named_client(p) and not p.startswith("stand-in") for p in made)}
    if not ran:
        decision = "not_run"
    elif not all(g["passed"] for g in judged.values()):
        decision = "fail"
    elif produced["stand_in"]:
        decision = "simulated"
    elif require_client and produced["unattributed"]:
        decision = "unattributed"
    else:
        decision = "pass"
    repeats: dict[str, list[bool]] = {}
    by_id = {task["id"]: task for task in scheduled if task["polarity"] == "positive"}
    for transcript in mine:
        if transcript.task_id in by_id:
            repeats.setdefault(transcript.task_id, []).append(
                judge(by_id[transcript.task_id], transcript, schemas)["useful_completion"])
    thrice = {task_id: runs for task_id, runs in repeats.items() if len(runs) >= 3}
    return {
        "format": FORMAT_REPORT, "client": client, "cohort": cohort,
        "tasks_digest": _digest(dict(tasks)), "gates_digest": _digest(dict(gates)),
        "bars_digest": _digest({**gates, "final_tasks_digest": None}), "produced": produced, "overall": overall,
        "routes": {route: _tally([row for row in rows if row["route"] == route])
                   for route in sorted({row["route"] for row in rows})},
        "tasks": rows, "gates": judged,
        "consistency": {"positive_tasks_run_three_times": len(thrice),
                        "passed_all_three": sum(all(runs) for runs in thrice.values()), "bar": None},
        "decision": decision,
        "limits": list(LIMITS),
    }


def reading(report: Mapping[str, Any], bars_digest: str) -> str:
    """What one report's decision counts as at release. ``pass`` only when the report says so, was scored on the
    pinned bars, names a client for every gate row and no stand-in, and carries the gate reading's ``evidence`` block
    with no row left unevidenced. Anything else, a decision this file does not know included, is not a pass."""
    decision = report.get("decision")
    if decision != "pass":
        return decision if decision in READINGS else "fail"
    produced = report.get("produced") if isinstance(report.get("produced"), Mapping) else {}
    evidence = report.get("evidence")
    if report.get("bars_digest") != bars_digest:
        return "other_bars"
    if produced.get("stand_in"):
        return "simulated"
    if produced.get("unattributed") or not produced.get("client"):
        return "unattributed"
    if not isinstance(evidence, Mapping) or evidence.get("unevidenced") != {} or not evidence.get("rederived"):
        return "unevidenced"
    return "pass"


def release(reports: Sequence[Mapping[str, Any]], *, bars_digest: str | None = None) -> dict[str, Any]:
    """The release reading of the final cohort's per-client reports: at least one client meets every bar; a client that
    missed is "routing bar not met" and one that was not run is "not run", each a known limit. Each report is read
    by ``reading``, against the pinned bars (``bars_digest``, or the pin file's own). A report is a file anyone can
    write: ``run.py release`` makes each report again from its folder's kept event logs before it calls this."""
    bars_digest = bars_digest or pinned()["bars_digest"]
    final = [report for report in reports if report["cohort"] == "final"]
    if len({report["client"] for report in final}) != len(final):
        raise TaskError("two final reports for one client: a client's final cohort is read once")
    clients = {report["client"]: reading(report, bars_digest) for report in final}
    limits = {client: READINGS[decision] for client, decision in clients.items() if decision != "pass"}
    return {"decision": "pass" if "pass" in clients.values() else "fail" if clients else "not_run",
            "clients": clients, "known_limits": limits}
