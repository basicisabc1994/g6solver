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
    #: The session's steps in the order the log holds them: ``call`` (a ``calls`` entry and the ``G6_E_*`` code its
    #: reply carried), ``edit`` (a path), ``test`` (a test command and whether it failed), ``text`` and ``final``.
    timeline: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.state not in STATES:
            raise TaskError(f"transcript state {self.state!r} is not one of {STATES}")

    def to_json(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "client": self.client, "state": self.state,
                "calls": [dict(call) for call in self.calls], "questions": self.questions, "seconds": self.seconds,
                "cost_usd": self.cost_usd, "final_text": self.final_text, "repeat": self.repeat, "code": self.code,
                "produced": self.produced, "exit_code": self.exit_code, "events_sha256": self.events_sha256,
                "timeline": [dict(step) for step in self.timeline]}

    @classmethod
    def from_json(cls, row: Mapping[str, Any]) -> "Transcript":
        return cls(task_id=row["task_id"], client=row["client"], state=row["state"],
                   calls=tuple(dict(call) for call in row.get("calls", ())), questions=int(row.get("questions", 0)),
                   seconds=float(row.get("seconds", 0.0)), cost_usd=row.get("cost_usd"),
                   final_text=str(row.get("final_text", "")), repeat=int(row.get("repeat", 0)),
                   code=str(row.get("code", "")), produced=str(row.get("produced", "")),
                   exit_code=row.get("exit_code"), events_sha256=str(row.get("events_sha256", "")),
                   timeline=tuple(dict(step) for step in row.get("timeline", ())))


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
    present = pattern.get("args_present", []) if isinstance(pattern, dict) else None
    _need(isinstance(pattern, dict) and set(pattern) <= {"tool", "server", "args_include", "args_present"}
          and isinstance(pattern.get("tool"), str) and pattern["tool"].strip() == pattern["tool"] != ""
          and "*" not in pattern["tool"] and isinstance(pattern.get("args_include", {}), dict)
          and isinstance(present, list) and all(isinstance(name, str) and name for name in present),
          f"task {task_id}: a call pattern is {{tool, server?, args_include?, args_present?}} with a literal tool name")


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
    """Whether a recorded call is the one a pattern describes: the same server and tool, every ``args_include``
    value present (a string as a substring of the argument's text, anything else as an equal value), and every
    argument ``args_present`` names given a value that is not empty."""
    if call.get("tool") != pattern["tool"] or call.get("server", "g6") != pattern.get("server", "g6"):
        return False
    args = call.get("args") if isinstance(call.get("args"), Mapping) else {}
    if any(args.get(name) in (None, "", [], {}) for name in pattern.get("args_present", ())):
        return False
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


# --- The moment-of-need cohort --------------------------------------------------------------------------------------
# Unprompted tasks: no prompt names G6, its server or a tool, so a hit is G6 reaching the agent at the moment it needs
# it, not an agent doing what it was told. The task is still the unit and every denominator stays full.

FORMAT_MOMENT_TASKS = "g6.routing_moment_tasks/1"
FORMAT_MOMENT_GATES = "g6.routing_moment_gates/1"
FORMAT_MOMENT_REPORT = "g6.routing_moment_report/1"
MOMENT = "moment"
#: Each kind of moment task: its polarity and, for a positive kind, its completion kind.
MOMENT_KINDS = {"before_done": ("positive", "call_before_final"), "stuck": ("positive", "stuck_call"),
                "after_refusal": ("positive", "call_after_refusal"), "negative": ("negative", None),
                "missing_tool": ("missing_tool", None)}
MOMENT_BARS = ("min_positive_moment_hits", "min_positive_lower_bound", "max_negative_unnecessary", "max_malformed",
               "min_missing_tool_recovery")
#: What a session may start with: the client's own environment, the fixture and its workspace folder. Nothing else.
GRANTS = ("agent_environment", "fixture", "workspace")
#: Words a moment prompt may not hold: naming G6, its server or a tool, or asking for a review or a check, would turn
#: an unprompted moment into an instruction.
NAMES_A_TOOL = re.compile(r"\bG6\b|\bMCP\b|\btools?\b|\breview|\bcheck|\bverif|\bg6_", re.IGNORECASE)
_CODE = re.compile(r"G6_E_[A-Z0-9_]+")
#: What a moment pass does not show, carried in every moment report.
MOMENT_LIMITS = (*LIMITS,
                 "A hit is a well-formed call at the moment the task's rule names, read from the order of the "
                 "session's steps; what the call returned is not part of the hit.",
                 "A positive task whose moment never came (no Python change, fewer failed test runs than the rule "
                 "needs, no refusal) is a miss, so a strong agent that never got stuck still counts against the bar.")


def _text(value: Any, minimum: int = 20) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum


def validate_moment_tasks(data: Any) -> dict[str, Any]:
    """The moment task list when it is inside its format; ``TaskError`` naming the first fault otherwise."""
    _need(isinstance(data, dict) and set(data) == {"format", "cohort", "planned_tools", "ungranted", "tasks"},
          "a moment task list is {format, cohort, planned_tools, ungranted, tasks}")
    _need(data["format"] == FORMAT_MOMENT_TASKS and data["cohort"] == MOMENT, f"format is not {FORMAT_MOMENT_TASKS}")
    for name in ("planned_tools", "ungranted"):
        _need(isinstance(data[name], list) and all(isinstance(v, str) and re.fullmatch(r"[a-z0-9_]+", v)
                                                   for v in data[name]), f"{name} is a list of names")
    _need(isinstance(data["tasks"], list) and bool(data["tasks"]), "a moment list has tasks")
    seen: set[str] = set()
    for task in data["tasks"]:
        _need(isinstance(task, dict), "a task is an object")
        task_id = task.get("id")
        _need(isinstance(task_id, str) and bool(re.fullmatch(r"[a-z0-9_.-]+", task_id)), f"task id {task_id!r}")
        _need(task_id not in seen, f"task {task_id} is listed twice")
        seen.add(task_id)
        required = {"id", "kind", "polarity", "cohort", "fixture", "prompt", "budget", "permissions", "grants",
                    "moment", "fair"}
        _need(required <= set(task) <= required | {"expected_calls", "completion", "forbidden", "withhold"},
              f"task {task_id}: fields {sorted(task)}")
        _need(task["kind"] in MOMENT_KINDS and task["polarity"] == MOMENT_KINDS[task["kind"]][0]
              and task["cohort"] == MOMENT, f"task {task_id}: kind, polarity or cohort")
        _need(_text(task["prompt"]) and not NAMES_A_TOOL.search(task["prompt"]),
              f"task {task_id}: a moment prompt names no G6, server or tool and asks for no review or check")
        _need(_text(task["moment"]) and _text(task["fair"]), f"task {task_id}: the moment rule and why it is fair")
        _need(isinstance(task["fixture"], str) and bool(re.fullmatch(r"[a-z0-9_]+", task["fixture"])),
              f"task {task_id}: fixture")
        budget = task["budget"]
        _need(isinstance(budget, dict) and set(budget) == {"seconds", "calls", "questions"}
              and all(isinstance(budget[k], int) and not isinstance(budget[k], bool) and budget[k] >= 0 for k in budget)
              and budget["seconds"] > 0, f"task {task_id}: budget is {{seconds, calls, questions}} in whole numbers")
        _need(isinstance(task["permissions"], list) and all(isinstance(p, str) and re.fullmatch(r"[a-z_]+(\.[a-z_]+)?", p)
                                                            for p in task["permissions"]),
              f"task {task_id}: permissions are host permissions, component or component.op")
        _need(isinstance(task["grants"], list) and set(task["grants"]) <= set(GRANTS),
              f"task {task_id}: a session starts with no grant but {GRANTS}")
        for pattern in task.get("expected_calls", []):
            _pattern(task_id, pattern)
        forbidden, withhold = task.get("forbidden", []), task.get("withhold", [])
        _need(forbidden == "any" or isinstance(forbidden, list), f"task {task_id}: forbidden is \"any\" or a list")
        for pattern in ([] if forbidden == "any" else forbidden):
            _pattern(task_id, pattern)
        _need(isinstance(withhold, list) and all(isinstance(t, str) and t for t in withhold), f"task {task_id}: withhold")
        completion = task.get("completion")
        if task["polarity"] == "positive":
            kind = MOMENT_KINDS[task["kind"]][1]
            _need(isinstance(completion, dict) and completion.get("kind") == kind and not forbidden and not withhold
                  and isinstance(task.get("expected_calls"), list),
                  f"task {task_id}: a {task['kind']} task's completion is {kind}, and it forbids and withholds nothing")
            if kind == "stuck_call":
                runs = completion.get("failed_test_runs")
                _need(set(completion) == {"kind", "failed_test_runs"} and type(runs) is int and runs >= 1,
                      f"task {task_id}: stuck_call names how many failed test runs come first")
            if kind == "call_after_refusal":
                refusal, tell = completion.get("refusal"), completion.get("tell_any")
                _need(set(completion) == {"kind", "refusal", "tell_any"} and isinstance(refusal, dict)
                      and set(refusal) == {"tools", "codes"} and bool(refusal["tools"]) and bool(refusal["codes"])
                      and all(isinstance(t, str) and t for t in refusal["tools"])
                      and all(isinstance(c, str) and _CODE.fullmatch(c) for c in refusal["codes"])
                      and isinstance(tell, list) and all(isinstance(t, str) and t for t in tell)
                      and bool(tell or task["expected_calls"]),
                      f"task {task_id}: call_after_refusal names the refusal (tools, codes) and its remedy calls "
                      "or what the person must be told")
            else:
                _need(set(completion) <= {"kind", "failed_test_runs"} and bool(task["expected_calls"]),
                      f"task {task_id}: a {task['kind']} task expects at least one call")
        elif task["polarity"] == "negative":
            _need(bool(forbidden) and not task.get("expected_calls") and not withhold and completion is None,
                  f"task {task_id}: a negative task says what must not be called, and expects and withholds nothing")
        else:
            _need(bool(withhold) and not task.get("expected_calls") and not forbidden and completion is None,
                  f"task {task_id}: a missing-tool task withholds a tool, and expects and forbids nothing")
    return data


def validate_moment_gates(data: Any) -> dict[str, Any]:
    """The moment bars when they are inside their format: each bar a share in [0, 1]."""
    fields = {"format", "cohort", "interval", "release_rule", "comparison", *MOMENT_BARS}
    _need(isinstance(data, dict) and set(data) == fields, f"a moment gate file has exactly {sorted(fields)}")
    _need(data["format"] == FORMAT_MOMENT_GATES and data["cohort"] == MOMENT, f"format is not {FORMAT_MOMENT_GATES}")
    _need(all(isinstance(data[bar], float) and 0.0 <= data[bar] <= 1.0 for bar in MOMENT_BARS), "each bar is a share")
    _need(data["interval"] == "wilson95" and data["release_rule"] == "at_least_one_client_passes"
          and data["comparison"] == "paired_exact_mcnemar_no_claim", "interval, release rule and comparison")
    return data


def _moment(task: Mapping[str, Any], steps: Sequence[Mapping[str, Any]],
            schemas: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any]:
    """Whether a positive task's moment came, and whether a well-formed call met it, from the order of its steps.
    Everything after the final message is outside the session's reply and counts for nothing."""
    completion = task["completion"]
    finals = [i for i, step in enumerate(steps) if step.get("kind") == "final"]
    end = finals[-1] if finals else len(steps)
    calls = [i for i in range(end) if steps[i].get("kind") == "call"]
    failed = [i for i in range(end) if steps[i].get("kind") == "test" and steps[i].get("failed") is True]

    def met(index: int) -> bool:
        step = steps[index]
        return not malformed(step, task, schemas) and any(matches(step, p) for p in task["expected_calls"])

    start, hit = None, False
    if completion["kind"] == "call_before_final":
        edits = [i for i in range(end) if steps[i].get("kind") == "edit" and str(steps[i].get("path", "")).endswith(".py")]
        start = edits[-1] if edits else None
        hit = start is not None and any(met(i) for i in calls if i > start)
    elif completion["kind"] == "stuck_call":
        need = completion["failed_test_runs"]
        start = failed[need - 1] if len(failed) >= need else None
        hit = start is not None and any(met(i) for i in calls if i > start)
    else:
        refusal = completion["refusal"]
        start = next((i for i in calls if steps[i].get("tool") in refusal["tools"]
                      and steps[i].get("code") in refusal["codes"]), None)
        if start is not None:
            later = [i for i in calls if i > start]
            retried = any((steps[i].get("tool"), steps[i].get("args")) == (steps[start].get("tool"),
                                                                          steps[start].get("args")) for i in later)
            said = str(steps[end].get("text", "")).lower() if end < len(steps) else ""
            told = any(term.lower() in said for term in completion["tell_any"])
            hit = bool(later and met(later[0])) or (told and not retried)
    return {"moment_reached": start is not None, "moment_hit": hit, "failed_test_runs": len(failed)}


def judge_moment(task: Mapping[str, Any], transcript: Transcript | None,
                 schemas: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """One moment task's result. A negative or missing-tool task is read as ``judge`` reads one; a positive task's
    hit is a well-formed expected call at the moment its completion kind names, in a completed session that kept to
    its question budget."""
    base = judge({**task, "route": task["kind"], "expected_calls": task.get("expected_calls") or [{"tool": "-"}],
                  "completion": {"kind": "calls_in_order"}}, transcript, schemas)
    base["kind"] = task["kind"]
    if task["polarity"] != "positive":
        return base
    for name in ("expected_calls", "timely_valid_calls", "useful_completion"):
        base.pop(name, None)
    found = _moment(task, transcript.timeline if transcript is not None else (), schemas)
    found["moment_hit"] = found["moment_hit"] and base["state"] == "completed" and not base["over_budget"]["questions"]
    return {**base, **found}


def _moment_tally(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    positive = [row for row in rows if row["polarity"] == "positive"]
    tally = _tally([{**row, "useful_completion": False, "expected_calls": 0, "timely_valid_calls": 0}
                    if row["polarity"] == "positive" else row for row in rows])
    tally["positive"] = {"tasks": len(positive), "moment_reached": sum(row["moment_reached"] for row in positive),
                         "moment_hits": sum(row["moment_hit"] for row in positive)}
    return tally


def score_moment(tasks: Mapping[str, Any], transcripts: Sequence[Transcript], gates: Mapping[str, Any], *,
                 client: str, schemas: Mapping[str, Mapping[str, Any]] | None = None,
                 require_client: bool = True) -> dict[str, Any]:
    """The report for one client on the moment cohort: per kind and overall, every count with its denominator, each
    bar with its value and ``passed``, and ``decision`` as ``score`` reads it. One transcript per task is read (two
    for one task raise ``TaskError``); a scheduled task with no transcript is ``unavailable`` and counts against every
    bar it belongs to."""
    validate_moment_tasks(dict(tasks))
    validate_moment_gates(dict(gates))
    scheduled = list(tasks["tasks"])
    ids = {task["id"] for task in scheduled}
    mine = [t for t in transcripts if t.client == client and t.task_id in ids]
    seen: set[tuple[str, int]] = set()
    for transcript in mine:
        key = (transcript.task_id, transcript.repeat)
        if key in seen:
            raise TaskError(f"task {key[0]} has two transcripts for repeat {key[1]}: a bar is read from one run")
        seen.add(key)
    first = {t.task_id: t for t in mine if t.repeat == 0}
    rows = [judge_moment(task, first.get(task["id"]), schemas) for task in scheduled]
    overall = _moment_tally(rows)
    pos, neg, mis, mal = overall["positive"], overall["negative"], overall["missing_tool"], overall["malformed"]
    share = lambda part, whole: (part / whole) if whole else None   # noqa: E731
    values = {
        "min_positive_moment_hits": (share(pos["moment_hits"], pos["tasks"]), ">="),
        "min_positive_lower_bound": (wilson_lower(pos["moment_hits"], pos["tasks"]) if pos["tasks"] else None, ">="),
        "max_negative_unnecessary": (share(neg["tasks"] - neg["quiet"], neg["tasks"]), "<="),
        "max_malformed": (share(mal["tasks"] - mal["well_formed"], mal["tasks"]), "<="),
        "min_missing_tool_recovery": (share(mis["recovered"], mis["tasks"]), ">="),
    }
    judged = {bar: {"value": None if value is None else round(value, 4), "bar": gates[bar],
                    "passed": value is not None and (value >= gates[bar] if way == ">=" else value <= gates[bar])}
              for bar, (value, way) in values.items()}
    made = [t.produced for t in first.values()]
    produced = {"client": sum(named_client(p) for p in made), "stand_in": sum(p.startswith("stand-in") for p in made),
                "unattributed": sum(not named_client(p) and not p.startswith("stand-in") for p in made)}
    if not any(row["state"] != "unavailable" for row in rows):
        decision = "not_run"
    elif not all(gate["passed"] for gate in judged.values()):
        decision = "fail"
    elif produced["stand_in"]:
        decision = "simulated"
    elif require_client and produced["unattributed"]:
        decision = "unattributed"
    else:
        decision = "pass"
    return {"format": FORMAT_MOMENT_REPORT, "client": client, "cohort": MOMENT,
            "tasks_digest": _digest(dict(tasks)), "gates_digest": _digest(dict(gates)),
            "bars_digest": _digest(dict(gates)), "produced": produced, "overall": overall,
            "kinds": {kind: _moment_tally([row for row in rows if row["kind"] == kind])
                      for kind in MOMENT_KINDS if any(row["kind"] == kind for row in rows)},
            "tasks": rows, "gates": judged, "decision": decision, "limits": list(MOMENT_LIMITS)}


def mcnemar_exact(before_only: int, after_only: int) -> float:
    """The two-sided exact McNemar test of paired outcomes: a binomial test of the discordant pairs at one half."""
    n, k = before_only + after_only, min(before_only, after_only)
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def _ok(row: Mapping[str, Any]) -> bool:
    return bool(row["moment_hit"] if row["polarity"] == "positive" else
                row["quiet"] if row["polarity"] == "negative" else row["recovered"])


def compare(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """The before and after arms of one client, paired by task: each task's outcome in each arm (a positive task's
    hit, a negative task's quiet, a missing-tool task's recovery), and per kind and polarity the four cells and the
    exact McNemar p-value. It reports and claims nothing (``claim`` is ``None``): the cohort is small and was not
    sized to decide a difference."""
    for name in ("client", "tasks_digest", "bars_digest"):
        _need(before.get(name) == after.get(name), f"the two arms differ in {name}: they are not a pair")
    after_rows = {row["task_id"]: row for row in after["tasks"]}
    pairs = [{"task_id": row["task_id"], "kind": row["kind"], "before": _ok(row), "after": _ok(after_rows[row["task_id"]])}
             for row in before["tasks"] if row["task_id"] in after_rows]
    polarity = {row["task_id"]: row["polarity"] for row in before["tasks"]}

    def cells(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        b = sum(p["before"] and not p["after"] for p in group)
        c = sum(p["after"] and not p["before"] for p in group)
        return {"pairs": len(group), "both": sum(p["before"] and p["after"] for p in group), "before_only": b,
                "after_only": c, "neither": sum(not p["before"] and not p["after"] for p in group),
                "p_exact": mcnemar_exact(b, c)}

    return {"client": before["client"], "tasks_digest": before["tasks_digest"], "pairs": pairs,
            "by_kind": {kind: cells([p for p in pairs if p["kind"] == kind]) for kind in MOMENT_KINDS
                        if any(p["kind"] == kind for p in pairs)},
            "by_polarity": {name: cells([p for p in pairs if polarity[p["task_id"]] == name]) for name in POLARITIES
                            if any(polarity[p["task_id"]] == name for p in pairs)},
            "claim": None}


def release_moment(reports: Sequence[Mapping[str, Any]], *, bars_digest: str) -> dict[str, Any]:
    """The moment cohort's release reading: at least one client meets every bar, each report read by ``reading``
    against the pinned moment bars."""
    moment = [report for report in reports if report.get("cohort") == MOMENT]
    if len({report["client"] for report in moment}) != len(moment):
        raise TaskError("two moment reports for one client: an arm is read once per client")
    clients = {report["client"]: reading(report, bars_digest) for report in moment}
    return {"decision": "pass" if "pass" in clients.values() else "fail" if clients else "not_run",
            "clients": clients,
            "known_limits": {client: READINGS[found] for client, found in clients.items() if found != "pass"}}