# SPDX-License-Identifier: Apache-2.0
"""Run section 17's local deterministic contracts (design 17.5): each fixture's actual output against its frozen
expected output. Every case must match, with zero unsupported definite claims and zero unauthorised disclosures.

A fixture is ``{"contract": 1..5, "function": <a name in RUNNERS>, "events": [...], "inputs": {...},
"expected": {...}}``, synthetic only. Matching admits only the tested behaviour; it is never a developer-value result.

    python validation/workflow_value/run_contracts.py [folder]      exit 1 when any case does not match

Everything runs locally under a scratch ``G6_HOME``: no model, no network. A grant a case needs is made through the
same dialog path a client uses, with a session that accepts, in that scratch folder only.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "workflow" / "fixtures" / "contracts"
T0 = datetime(2026, 11, 1, 9, 0, 0, tzinfo=UTC)
PROVIDER = "openai:codex"
#: Wording no rendered text may hold: a definite constraint, a cause, a saving, or an unqualified checked claim.
DEFINITE = ("the constraint is", "the bottleneck is", "binding constraint", "root cause", "verified", "saves you",
            "minutes saved", "this caused", "the cause is")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROJECT, TASK = sha("contract project"), sha("contract task")


def _at(seconds: float = 0, days: float = 0) -> str:
    from g6.harness.workflow.record import TIME_FORMAT

    return (T0 + timedelta(seconds=seconds, days=days)).strftime(TIME_FORMAT)


def _event(row: dict):
    """A fixture's event row as a record event. ``run`` is an alias; ids and digests are derived from the aliases."""
    from g6.harness.workflow import record as rec

    run = sha("run " + str(row.get("run", "r1")))[:32]
    kind = row["kind"]
    data = dict(row.get("data") or {})
    if kind == "run_started":
        data = {"origin_kind": "loop_session", "origin_sha256": sha(run), **data}
    refs = tuple(rec.Ref(ref[0], sha(ref[1]), sha(ref[1])) for ref in row.get("refs") or [])
    return rec.Event(project=PROJECT, task=TASK, run=run, event_id=rec.event_id(run, kind, str(row.get("native", ""))),
                     kind=kind, at=_at(row.get("seconds", 0), row.get("days", 0)),
                     provenance=row.get("provenance", "host_observed"), sources=(rec.workflow_source(PROJECT),),
                     baseline_sha256="", artifact_sha256="", refs=refs, missing=tuple(row.get("missing") or ()),
                     data=data)


def _events(fixture: dict) -> tuple:
    return tuple(_event(row) for row in fixture.get("events") or [])


@contextmanager
def _home():
    """A scratch G6_HOME with the workflow record on, removed afterwards; the caller's environment is restored."""
    kept = {name: os.environ.get(name) for name in ("G6_HOME", "G6_WORKFLOW_RECORD", "G6_WORKFLOW_VIEW")}
    with tempfile.TemporaryDirectory(prefix="g6-contracts-") as folder:
        os.environ.update(G6_HOME=str(Path(folder) / "home"), G6_WORKFLOW_RECORD="on")
        os.environ.pop("G6_WORKFLOW_VIEW", None)
        try:
            yield Path(folder) / "home"
        finally:
            for name, value in kept.items():
                os.environ.pop(name, None) if value is None else os.environ.__setitem__(name, value)


def _grant(source: str) -> None:
    """Share ``source`` with the fixture's provider through the dialog path, in the scratch home."""
    from g6.mcp.cloud_consent import request_grant

    class Accepting:
        async def elicit_form(self, **_kwargs):
            return SimpleNamespace(action="accept", content={"value": "Fixture person"})

    result = asyncio.run(request_grant(SimpleNamespace(session=Accepting(), request_id="fixture"), source, PROVIDER))
    assert result["status"] == "granted", result


def _store(events) -> dict[str, str]:
    """Write run events into the scratch record; each fixture run alias's real run id."""
    from g6.harness.workflow import record as rec

    record, runs = rec.ProjectRecord(PROJECT), {}
    for event in events:
        if event.kind == "run_started":
            runs[event.run] = record.start_run(TASK, event.data["origin_kind"], event.run, baseline_sha256="", at=event.at)
        elif not event.kind.startswith("question_"):
            run = runs[event.run]
            record.append(rec.Event(**{**{f: getattr(event, f) for f in (
                "project", "task", "kind", "at", "provenance", "sources", "baseline_sha256", "artifact_sha256", "refs",
                "observed", "missing", "data")}, "run": run,
                "event_id": rec.event_id(run, event.kind, event.event_id)}))
    return runs


# -- the functions a fixture may name ---------------------------------------------------------------------------
def _obstacles(fixture: dict) -> dict:
    from g6.harness.workflow import obstacle

    inputs = fixture.get("inputs") or {}
    found = obstacle.obstacles(inputs.get("assessment"), inputs.get("loop"), _events(fixture), job_id="job-1")
    check = obstacle.next_check(found, inputs.get("assessment"))
    return {"obstacles": [[one.kind, one.scope, one.requirement] for one in found],
            "next_check": check["kind"] if check else None}


def _friction(fixture: dict) -> dict:
    from g6.harness.workflow.friction import friction

    found = friction(_events(fixture)).to_dict()
    return {name: found[name] for name in fixture["expected"]}


def _identities(fixture: dict) -> dict:
    from g6.harness.workflow import record as rec

    with _home():
        record, runs, appended = rec.ProjectRecord(PROJECT), [], []
        for step in fixture["inputs"]["operations"]:
            if step["op"] == "restart":
                record = rec.ProjectRecord(PROJECT)
            elif step["op"] == "start":
                run = record.start_run(TASK, step.get("kind", "loop_session"), step["origin"], baseline_sha256="",
                                       at=_at(step.get("seconds", 0)))
                runs.append(run) if run not in runs else None
                appended.append(f"run-{runs.index(run) + 1}")
            else:
                run = runs[step["run"] - 1]
                appended.append(record.append(rec.Event(
                    project=PROJECT, task=TASK, run=run, kind=step["kind"], at=_at(step.get("seconds", 0)),
                    event_id=rec.event_id(run, step["kind"], step["native"]), provenance="host_observed",
                    sources=(rec.workflow_source(PROJECT),), baseline_sha256="", artifact_sha256="")))
        fresh = rec.ProjectRecord(PROJECT)
        return {"steps": appended, "runs": len(fresh.runs(TASK)), "tasks": len(fresh.tasks()),
                "events": [sum(e.run == run for e in fresh.events()) for run in runs]}


def _view(fixture: dict) -> dict:
    from g6.harness.workflow import record as rec
    from g6.harness.workflow import view

    inputs = fixture.get("inputs") or {}
    with _home():
        _store(_events(fixture))
        if inputs.get("granted", True):
            _grant(rec.workflow_source(PROJECT))
        if inputs.get("level"):
            os.environ["G6_WORKFLOW_VIEW"] = inputs["level"]
        built = view.build(PROJECT, TASK, assessment=inputs.get("assessment"), loop=inputs.get("loop"),
                           intent=inputs.get("intent"), provider=PROVIDER, job_id="job-1",
                           request=inputs.get("request", ""))
        actual = {"block": view.block(built)}
        for name in fixture["expected"]:
            if name != "block":
                actual[name] = built.get(name)
        return actual


def _report_order(fixture: dict) -> dict:
    from g6.harness.workflow import view

    inputs = fixture["inputs"]
    result = view.with_block(inputs["report"], inputs.get("view"))
    return {"starts_with_the_report": result.startswith(inputs["report"]),
            "added": result[len(inputs["report"]):].splitlines()}


def _lifecycle(fixture: dict) -> dict:
    from g6.harness.workflow import lifecycle, policy
    from g6.harness.workflow import record as rec

    with _home() as home:
        runs = _store(_events(fixture))
        names = {real: f"run-{n}" for n, real in enumerate(runs.values(), 1)}
        folder, done = rec.ProjectRecord(PROJECT).root, []
        for step in fixture["inputs"]["operations"]:
            now = T0 + timedelta(days=step.get("days", 0))
            if step["op"] == "export":
                done.append(lifecycle.export(PROJECT, now=now).name)
            elif step["op"] == "correct":
                target = rec.ProjectRecord(PROJECT).events()[step["target"]].event_id
                folder.joinpath(policy.CORRECTIONS).write_text(json.dumps({
                    "schema": lifecycle.CORRECTIONS_SCHEMA, "corrections": [
                        {"target": target, "action": step["action"], "note": step["note"], "by": "Fixture person"}]}),
                    encoding="utf-8")
                done.append(lifecycle.apply_corrections(PROJECT, now=now)["status"])
            elif step["op"] == "delete":
                ident = list(runs.values())[step["run"] - 1] if step["scope"] == "run" else ""
                done.append(lifecycle.delete(PROJECT, scope=step["scope"], ident=ident, by="Fixture person",
                                             now=now)["status"])
            elif step["op"] == "expire":
                done.append(lifecycle.expire(PROJECT, now=now)["status"])
        files = sorted(str(p.relative_to(folder).as_posix()) for p in folder.rglob("*")
                       if p.is_file() and not p.name.endswith(policy.SIDECARS))
        for real, alias in names.items():
            files = [name.replace(real, alias) for name in files]
        receipts = [[r["scope"], r["removed"], r["reason"]] for r in lifecycle.receipts(PROJECT)]
        elsewhere = sorted(p.name for p in home.iterdir() if p.name not in ("workflow", "cloud_consent"))
        return {"steps": done, "files": [name.replace(TASK, "task-1") for name in files], "receipts": receipts,
                "written_outside_the_record": elsewhere}


def _diagnose(fixture: dict) -> dict:
    from g6.harness.workflow import diagnose, obstacle

    inputs, events = fixture.get("inputs") or {}, _events(fixture)
    goal = inputs.get("goal")
    outcome = obstacle.Outcome(sha(goal), goal, "attributed_label") if goal else None
    found = diagnose.diagnose(events, assessment=inputs.get("assessment"), loop=inputs.get("loop"), outcome=outcome,
                              job_id="job-1")
    return {"status": found.status, "rules": [h.why_code for h in found.hypotheses],
            "obstacles": [one.kind for one in found.obstacles], "overlapping": diagnose.overlapping(events),
            "capacity_calculated": found.capacity is not None, "lines": diagnose.lines(found.to_dict())}


def _decisions(fixture: dict) -> dict:
    """Decisions kept across shows: each ``show`` step gives what would be offered then, and why anything came back."""
    from g6.harness.workflow import diagnose, obstacle, suggest
    from g6.harness.workflow.decisions import Decisions

    with _home():
        _store(_events(fixture))
        shows = []
        for step in fixture["inputs"]["operations"]:
            now = T0 + timedelta(days=step.get("days", 0))
            if step["op"] in ("dismissed", "reopened", "adopted"):
                one = suggest.Intervention(step["kind"], step["target"], "task")
                expires = (now + timedelta(days=step["expires_days"])).strftime("%Y-%m-%dT%H:%M:%SZ") if step.get(
                    "expires_days") else None
                Decisions(PROJECT).record(one.id, step["op"], task=TASK, scope=step.get("scope", "task"),
                                          by="Fixture person", at=_at(days=step.get("days", 0)), expires_at=expires,
                                          reopen_rule=step.get("rule", "never"),
                                          outcome_sha256=sha(step["goal"]) if step.get("goal") else "")
                continue
            goal = step.get("goal", "add returns the sum")
            outcome = obstacle.Outcome(sha(goal), goal, "attributed_label")
            found = diagnose.diagnose(_events(fixture), assessment=step.get("assessment"), outcome=outcome,
                                      job_id=step.get("job", "job-1"))
            task = sha(step["task"]) if step.get("task") else TASK
            shown, again = suggest.ranked(suggest.candidates(found, step.get("assessment")), Decisions(PROJECT), task=task,
                                          now=now, outcome_sha256=outcome.text_sha256)
            kinds = {one.intervention.id: one.intervention.kind for one in shown}
            shows.append({"shown": [one.intervention.kind for one in shown],
                          "shown_again": [[kinds[row["intervention_id"]], row["reason"]] for row in again],
                          "findings": [one.kind for one in found.obstacles]})
        return {"shows": shows}


def _feedback(fixture: dict) -> dict:
    """The optional question's rules over a clock: each step's outcome, in order."""
    from g6.harness import question_store as qs
    from g6.harness.workflow import feedback, view

    inputs, done = fixture["inputs"], []
    admitted = view.ADMITTED_LEVEL
    with _home():
        kept = os.environ.get(feedback.OPT_IN_ENV)
        os.environ.pop(feedback.OPT_IN_ENV, None)
        if inputs.get("opt_in_env"):
            os.environ[feedback.OPT_IN_ENV] = inputs["opt_in_env"]
        view.ADMITTED_LEVEL = inputs.get("admitted", "suggestions")       # the level the recorded gates would admit
        try:
            store, invitations = feedback.FeedbackStore(PROJECT), []
            for step in inputs["operations"]:
                now = T0 + timedelta(days=step.get("days", 0))
                if step["op"] == "may_invite":
                    state = qs.State(default=step.get("budget", 1), reserved=tuple({} for _ in range(step.get("reserved", 0))))
                    done.append(list(feedback.may_invite(PROJECT, TASK, questions=state, now=now,
                                                         report_reached=step.get("report", True),
                                                         dialog=step.get("dialog", True))))
                elif step["op"] == "invite":
                    try:
                        invitations.append(store.reserve_invitation(TASK, now=now))
                        done.append("invited")
                    except feedback.InvitationRefused as refused:
                        done.append(str(refused))
                elif step["op"] == "close":
                    store.close(invitations[-1], step["result"], option=step.get("option", ""), shown_at=_at(days=step.get("days", 0)),
                                back_at=_at(1, days=step.get("days", 0)), seconds=1.0)
                    done.append("closed")
                elif step["op"] == "opt":
                    store.opt(step["choice"], by="Fixture person", at=_at(days=step.get("days", 0)), via="corrections")
                    done.append("opted " + step["choice"])
            kinds = [entry["kind"] for entry in store.entries()]
        finally:
            view.ADMITTED_LEVEL = admitted
            os.environ.pop(feedback.OPT_IN_ENV, None) if kept is None else os.environ.__setitem__(feedback.OPT_IN_ENV, kept)
    return {"steps": done, "invitations": kinds.count("invited"), "closed": kinds.count("closed")}


RUNNERS = {"decisions": _decisions, "diagnose": _diagnose, "feedback": _feedback, "obstacles": _obstacles, "friction": _friction, "identities": _identities, "view": _view,
           "report_order": _report_order, "lifecycle": _lifecycle}


def _texts(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for one in value.values() for text in _texts(one)]
    if isinstance(value, list):
        return [text for one in value for text in _texts(one)]
    return []


def run(folder: Path = FIXTURES) -> dict:
    """Every fixture under ``folder`` through the function it names: counts, each mismatch with what was expected
    and what came out, and the zero-tolerance counters."""
    cases, mismatches, definite, disclosures = 0, [], 0, 0
    for path in sorted(Path(folder).rglob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        cases += 1
        try:
            actual = RUNNERS[fixture["function"]](fixture)
        except Exception as exc:  # noqa: BLE001 - a case that cannot run is a mismatch, said plainly
            actual = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        actual = json.loads(json.dumps(actual))
        if actual != fixture["expected"]:
            mismatches.append({"case": path.relative_to(folder).as_posix(), "expected": fixture["expected"],
                               "actual": actual})
        said = " ".join(_texts(actual)).lower()
        definite += sum(word in said for word in DEFINITE)
        if fixture["function"] == "view" and not (fixture.get("inputs") or {}).get("granted", True):
            from g6.harness.workflow import view

            disclosures += int(actual.get("block") != [view.WITHHELD])
    return {"cases": cases, "matched": cases - len(mismatches), "mismatches": mismatches,
            "unsupported_definite_claims": definite, "unauthorised_disclosures": disclosures,
            "note": "Fixture success admits only the tested behaviour; it is not a developer-value result."}


def simulate(sessions: int = 20) -> dict:
    """Simulated sessions for a gate's mechanical bars: synthetic records put through the view, measuring what the
    view adds (its lines, the time to build it, any question it reserved). A simulated run can fail a bar. It says
    nothing about usefulness, and ``manifest.decide`` never admits a row on it."""
    import statistics
    import time

    from g6.harness.workflow import record as rec
    from g6.harness.workflow import view

    seconds, lines = [], []
    failing = {"report": "REPORT", "reasons": [], "requirements": [{"id": "R1", "status": "failed", "provenance": "stated", "checks": [
        {"id": "tests/test_calc.py::test_add", "verdict": "failed", "provenance": "stated"}]}]}
    with _home() as home:
        _grant(rec.workflow_source(PROJECT))
        for number in range(sessions):
            task, record = sha(f"simulated task {number}"), rec.ProjectRecord(PROJECT)
            run = record.start_run(task, "loop_session", f"session-{number}", baseline_sha256="", at=_at(number))
            for kind, native in [*(("loop_round", str(k)) for k in range(1 + number % 4)), ("report_returned", "end")]:
                record.append(rec.Event(project=PROJECT, task=task, run=run, kind=kind, at=_at(number),
                                        event_id=rec.event_id(run, kind, native), provenance="host_observed",
                                        sources=(rec.workflow_source(PROJECT),), baseline_sha256="",
                                        artifact_sha256="", data={"run_seconds": 1.0} if kind == "report_returned" else {}))
            began = time.perf_counter()
            built = view.build(PROJECT, task, assessment=failing if number % 2 else {"report": "R", "requirements": []},
                               provider=PROVIDER, job_id=f"job-{number}", request="a simulated request")
            lines.append(len(view.block(built)))
            seconds.append(time.perf_counter() - began)
        questions = sum(1 for _ in (home / "questions").rglob("*")) if (home / "questions").exists() else 0
    return {"simulated": True, "sessions": sessions, "measures": {
        "added_wall_seconds_median": {"value": round(statistics.median(seconds), 3), "n": sessions},
        "added_wall_seconds_worst": {"value": round(max(seconds), 3), "n": sessions},
        "added_block_lines": {"value": max(lines), "n": sessions},
        "added_questions": {"value": questions, "n": sessions}}}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    result = run(Path(args[0]) if args else FIXTURES)
    print(json.dumps(result, indent=1))
    return 0 if not result["mismatches"] and not result["unsupported_definite_claims"] and not result[
        "unauthorised_disclosures"] and result["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
