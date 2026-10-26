# SPDX-License-Identifier: Apache-2.0
"""activation — founder brief §25's tracked rates for one owner's finished missions, counted from their own rows.

Off by default (audit 010 §7, §11: kept as a report; not served on any surface). ``report(missions)`` reads each finished
mission through ``trace.gather`` and ``trace.stages`` and writes nothing: open the store with ``MissionStore.reader`` so
that any write is refused. Each metric is ``{value, numerator, denominator, n, sources, status, why}``; ``sources`` are
the missions behind the numbers. A metric with nothing to count, or whose label G6 does not record, is ``unmeasured``
with no value: never 0 and never 1.0. A running mission is counted once it finishes.

The headline ``default_path_coverage`` counts the missions that entered through the objective entry (``ENTRY``); every
metric also carries ``by_class``, the same metric for each task class alone. §25 says the metrics are not optimised
independently: none is combined into a score, and ``selection_recall`` carries the toolset size beside it.

Audit 010 WF-016: the archive's ``selection_precision`` was "selected routes that ran over routes selected", a count of
execution reported under a precision's name. Precision needs to know which route was the right one, and G6 records no
such label, so ``selection_precision`` is measured only over selections a caller labels with the right route
(``route_labels``, an evaluator's ground truth, never G6's own) and is ``unmeasured`` otherwise. The archive's count is
kept beside it as ``selections_executed``: how many selected routes ran, which says nothing about whether they were right.

Not ported: the archive's ``python -m ... activation`` command (the CLI surface is frozen); call ``report``.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from types import SimpleNamespace

from g6.workforce import decisions, economics, trace
from g6.workforce.contracts import QUALIFIED, VERIFIED, Outcome
from g6.workforce.missions import AD_HOC, FINISHED, MissionStore
from g6.workforce.runs import RunLedger

#: §25's names, in its order, as snake_case keys.
METRICS = ("default_path_coverage", "task_standard_activation", "memory_retrieval", "task_local_toolset_size",
           "selection_precision", "selection_recall", "failure_elicitation_activation", "silent_error_detection",
           "recovery_success", "completion_state_precision", "experience_reuse", "promoted_learning_reuse",
           "cost_per_verified_outcome", "human_minutes_per_accepted_task")
MEASURED, UNMEASURED = "measured", "unmeasured"
#: The task classes the objective entry derives: the headline coverage counts only their missions.
ENTRY = ("code_change", "format_json")
#: Templates whose standard is the caller's own words, never derived by G6.
RECORDED = ("verify_task",)
#: The reconfiguration triggers that follow a failure (a cost replan starts no recovery).
RECOVERY_TRIGGERS = ("tool_unavailable", "repeated_failure", "no_progress", "verify_unmeasured")
#: A task is accepted when its mission closed done with a delivered result.
ACCEPTED = (VERIFIED, QUALIFIED)
_KEY = ("repository_identity", "module", "function")

# COPY: Each metric's reason, measured and unmeasured.
WHY = {
    "coverage": ("{n} of {d} finished missions that came in through the objective entry ran every stage that applied "
                 "to them. Each applicable stage that did not run is listed under missed with its reason; each task "
                 "class is shown on its own under by_class."),
    "class_coverage": ("{n} of {d} finished missions of this class ran every stage that applied to them. Each "
                       "applicable stage that did not run is listed under missed with its reason."),
    "no_coverage": "No finished mission had a stage that applied, so there is nothing to cover yet.",
    "stage": "{n} of {d} finished missions this stage applied to ran it.",
    "no_stage": "This stage applied to no finished mission.",
    "standard": ("{n} of {d} finished missions had what counts as done derived and frozen by G6 before work began (a "
                 "verify_task record's standard is the caller's own task, so it never counts)."),
    "no_missions": "No mission in scope has finished yet.",
    "retrieval": "{n} of {d} finished missions that began with an approved learned check for their task retrieved it.",
    "no_retrieval": "No finished mission began with an approved learned check for its task, so nothing was retrieved.",
    "toolset": ("{n} routes were offered across {d} missions' preflight shortlists; the value is the mean per mission. "
                "Read it beside selection recall."),
    "no_toolset": "No finished mission recorded a preflight shortlist of routes.",
    # audit 010 WF-016: precision is measured only against a ground truth of the right route
    "precision": ("{n} of {d} selections that carry a label of the right route selected that route. The labels are the "
                  "caller's ground truth, not G6's; {u} other selection(s) carried no label and are not counted."),
    "no_route_labels": ("Precision needs to know which route was the right one for each selection, and no selection "
                        "here carries such a label, so it is not measured. How many selected routes ran is under "
                        "selections_executed; that counts execution, not precision."),
    "no_selection": "No finished mission recorded a selected route.",
    "executed": ("{n} of {d} routes the scheduler selected ran. This counts execution only: a route that ran may still "
                 "have been the wrong one."),
    "recall": ("{n} of {d} stages that applied to a finished mission ran. Read it beside the task-local toolset size: a "
               "high recall bought with many routes is no gain."),
    "no_recall": "No finished mission had a stage that applied.",
    "elicitation": ("{n} of {d} finished code-change missions (their standard asks for a search for hidden failures) "
                    "ran one on their final output."),
    "no_elicitation": "No finished mission's standard asked for a search for hidden failures.",
    "silent": ("Silent-error detection needs a label from a check run after delivery, which G6 does not record, so it "
               "is not measured. What G6 can count is under caught_on_test_passing_proposals."),
    "silent_proxy": ("Of {d} proposals that passed their agreed tests and were searched for hidden failures, the search "
                     "caught {n}. This is not the share of all hidden failures."),
    "no_silent_proxy": "No proposal both passed its agreed tests and had a search for hidden failures run on it.",
    "recovery": ("{n} of {d} changes of course after a failure (a proposal sent back, or a route changed after a "
                 "failure) led to a later result that passed every check."),
    "no_recovery": "Nothing failed in a way G6 sent back or changed course on, so no recovery was tried.",
    "no_labels": ("G6 does not record a label from a check run after delivery, so how often a delivered result's "
                  "completion state was right cannot be measured. A label typed by a person never counts."),
    "reuse": "{n} of {d} learned checks G6 retrieved ran.",
    "no_reuse": "G6 retrieved no learned check in any finished mission.",
    "promoted": "{n} of {d} approved learned checks that covered a mission's task when it began ran in that mission.",
    "no_promoted": "No approved learned check covered a finished mission's task when it began.",
    "cost": ("The known cost of the {k} finished missions, {n} AUD minor units ({e} of the amounts estimated), over {d} "
             "verified outcomes."),
    "no_verified": "There are no verified outcomes, so there is no cost per verified outcome.",
    "unknown_cost": ("The cost is unknown: {u} recorded amount(s) have no known price, and an unknown cost is never "
                     "counted as zero."),
    "minutes": ("{n} seconds at the terminal across {d} accepted tasks, in minutes per task. Only time at the terminal "
                "is measured; the owner's time in the chat is not."),
    "no_accepted": "No task was accepted: no mission closed done with a verified or qualified-draft result.",
    "untimed": ("Some time a person spent at the terminal on an accepted task was not measured, so the minutes per "
                "accepted task are unknown."),
    "declared": ("People said their decisions on {d} accepted tasks took {n} minutes in all (the value is per accepted "
                 "task). This is each person's own figure, not a measurement: {c} of {k} decisions carried one."),
    "undeclared": ("No decision on an accepted task said how many minutes it took, so the declared minutes per accepted "
                   "task are unknown, never 0."),
    "wait": ("{d} questions to a person on accepted tasks waited {n} minutes in all for their answers (the value is the "
             "mean wait per answer). This is how long a question stayed open, not how long anyone worked."),
    "no_wait": "No question to a person on an accepted task was answered, so there is no wait to report.",
    "present": "{v} capabilities have a reviewed contract G6 can call.",
    "reachable": "{v} of the {d} capabilities with a contract passed their latest probe for this owner.",
    "no_reachable": "No probe of this owner's was recorded, so which capabilities can be planned on here is not known.",
    "invoked": "{v} capabilities were called by the {n} finished missions in scope.",
    "no_invoked": "No mission in scope has finished, so no capability call was counted.",
    "no_effective": ("Whether a capability changed an outcome needs a label from a check run after delivery, which G6 "
                     "does not record. A count of calls is not an effect, and a label typed by a person never counts."),
}

#: A caller's ground truth for ``selection_precision``: mission id -> work unit id -> the id of the right option (a
#: scheduler decision's ``selected_option["id"]``, e.g. ``"check-patch:planned"``).
RouteLabels = Mapping[str, Mapping[str, str]]


def report(missions: MissionStore, venture_id: str | None = None, *, route_labels: RouteLabels | None = None) -> dict:
    """§25's metrics for this owner's finished missions (``venture_id``: that venture's only; another owner's reads
    ``LookupError(NOT_FOUND)`` like an unknown one). ``route_labels``: the right route for each labelled selection
    (WF-016); without them ``selection_precision`` is unmeasured. Reads only."""
    ventures = [venture_id] if venture_id else [v["venture_id"] for v in missions.scope.list_ventures()]
    listed = [m for venture in ventures for m in missions.list(venture)]
    ledger, read = RunLedger.reader(missions), []
    for mission in (m for m in listed if m["status"] in FINISHED):
        rows = trace.gather(missions, mission["mission_id"], ledger=ledger, instances=False)
        created = (rows.get("created") or [{"detail": {}}])[0]["detail"]
        read.append(SimpleNamespace(mid=mission["mission_id"], mission=mission, rows=rows, stages=trace.stages(rows),
                                    cls=str(created.get("template") or AD_HOC)))
    history = _history(missions)  # learned checks are the owner's, across every venture
    for r in read:
        r.covering = _covering(r, history)
    labels = route_labels or {}
    found = _metrics(read, labels, headline=True)
    for cls in sorted({r.cls for r in read}):
        alone = _metrics([r for r in read if r.cls == cls], labels, headline=False)
        for name in METRICS:
            found[name].setdefault("by_class", {})[cls] = alone[name]
    found["selection_recall"]["beside"] = {"task_local_toolset_size": {
        k: found["task_local_toolset_size"][k] for k in ("value", "status")}}
    return {"version": 1, "owner": missions.owner, "venture_id": venture_id,
            "missions": {"finished": len(read), "running": [m["mission_id"] for m in listed if m["status"] not in FINISHED]},
            "metrics": {name: found[name] for name in METRICS}, "funnel": _funnel(missions, read)}


def _metrics(read, labels: RouteLabels, *, headline: bool) -> dict:
    covered = [r for r in read if r.cls in ENTRY] if headline else read
    return {"default_path_coverage": _coverage(covered, "coverage" if headline else "class_coverage"),
            "task_standard_activation": _standard(read), "memory_retrieval": _retrieval(read),
            "task_local_toolset_size": _toolset(read), "selection_precision": _precision(read, labels),
            "selection_recall": _recall(read), "failure_elicitation_activation": _elicitation(read),
            "silent_error_detection": _silent(read), "recovery_success": _recovery(read),
            "completion_state_precision": _unmeasured(None, None, [], WHY["no_labels"]),
            "experience_reuse": _reuse(read), "promoted_learning_reuse": _promoted_reuse(read),
            "cost_per_verified_outcome": _cost(read),
            "human_minutes_per_accepted_task": {**_minutes(read), **_declared(read)}}


# -- counting ----------------------------------------------------------------------------------------------------------
def _rate(numerator, denominator, sources: list[str], why: str, empty: str, **said) -> dict:
    """A rate counted from rows; unmeasured (no value) when its denominator is empty."""
    if not denominator:
        return _unmeasured(numerator, denominator, sources, WHY[empty])
    return {"value": round(numerator / denominator, 4), "numerator": numerator, "denominator": denominator,
            "n": len(sources), "sources": sources, "status": MEASURED,
            "why": WHY[why].format(n=numerator, d=denominator, **said)}


def _unmeasured(numerator, denominator, sources: list[str], why: str) -> dict:
    return {"value": None, "numerator": numerator, "denominator": denominator, "n": len(sources), "sources": sources,
            "status": UNMEASURED, "why": why}


def _state(mission: dict) -> str:
    return Outcome.from_record(mission["outcome"]).state if mission["status"] in FINISHED else ""


def _plan(r):
    return r.rows["plans"][-1]["plan"] if r.rows.get("plans") else None


def _applied(r) -> list[str]:
    return [name for name, stage in r.stages.items() if stage["ran"] != trace.NOT_APPLICABLE]


def _stage_rate(read, name: str, why: str, empty: str) -> dict:
    applied = [r for r in read if r.stages[name]["ran"] != trace.NOT_APPLICABLE]
    return _rate(sum(r.stages[name]["ran"] is True for r in applied), len(applied), [r.mid for r in applied], why, empty)


def _coverage(read, why: str) -> dict:
    applied = [r for r in read if _applied(r)]
    full = [r for r in applied if all(r.stages[name]["ran"] is True for name in _applied(r))]
    out = _rate(len(full), len(applied), [r.mid for r in applied], why, "no_coverage")
    out["by_stage"] = {name: _stage_rate(read, name, "stage", "no_stage") for name in trace.STAGES}
    out["missed"] = [{"mission_id": r.mid, "stage": name, "why": stage["why"]} for r in applied
                     for name, stage in r.stages.items() if stage["ran"] is False]
    return out


def _standard(read) -> dict:
    """Missions whose standard G6 derived and froze itself (the freezing move's actor is code), never a verify_task's."""
    derived = [r for r in read if r.cls not in RECORDED and r.stages["standard"]["ran"] is True
               and any(e["evidence_id"] in r.stages["standard"]["evidence"] and e["detail"].get("actor") == "code"
                       for e in r.rows.get("statuses") or [])]
    return _rate(len(derived), len(read), [r.mid for r in read], "standard", "no_missions")


def _recall(read) -> dict:
    applied = [r for r in read if _applied(r)]
    return _rate(sum(r.stages[n]["ran"] is True for r in applied for n in _applied(r)),
                 sum(len(_applied(r)) for r in applied), [r.mid for r in applied], "recall", "no_recall")


def _elicitation(read) -> dict:
    asks = [r for r in read if r.cls == "code_change"
            or (((r.mission.get("acceptance") or {}).get("metadata") or {}).get("coding"))]
    return _rate(sum(r.stages["elicit"]["ran"] is True for r in asks), len(asks), [r.mid for r in asks], "elicitation",
                 "no_elicitation")


def _toolset(read) -> dict:
    """Routes offered per stage in each mission's preflight shortlist (its "cheapest trustworthy route" answer)."""
    from g6.workforce.objective import QUESTIONS

    shortlists = []
    for r in read:
        for event in r.rows.get("preflight") or []:
            answer = next((a["answer"] for a in event["detail"].get("answers") or [] if a.get("question") == QUESTIONS[6]),
                          None)
            if isinstance(answer, dict) and answer:
                shortlists.append((r.mid, {stage: len(d.get("candidate_options") or []) for stage, d in answer.items()}))
    out = _rate(sum(sum(s.values()) for _, s in shortlists), len(shortlists), [m for m, _ in shortlists], "toolset",
                "no_toolset")
    by_stage: dict[str, list[int]] = {}
    for _, offered in shortlists:
        for stage, routes in offered.items():
            by_stage.setdefault(stage, []).append(routes)
    out["by_stage"] = {stage: {"routes": sum(found), "missions": len(found), "value": round(sum(found) / len(found), 4)}
                       for stage, found in by_stage.items()}
    return out


def _selections(read) -> list[tuple[str, dict, bool]]:
    """Every scheduler decision recorded for each mission (its preflight's and its plan's): (mission, decision, ran)."""
    return [(r.mid, decision, _ran(r, decision)) for r in read for event in r.rows.get("schedule") or []
            for decision in event["detail"].get("decisions") or []]


def _right_route(labels: RouteLabels, mission_id: str, decision: dict) -> str | None:
    found = (labels.get(mission_id) or {}).get(str(decision.get("work_unit_id") or ""))
    return found if isinstance(found, str) and found else None


def _precision(read, labels: RouteLabels) -> dict:
    """audit 010 WF-016: precision is right selections over labelled selections, against the caller's ground truth of
    the right route; with no label it is unmeasured. ``selections_executed`` (the archive's "selected routes that ran
    over routes selected") stays beside it under its own name, never as the metric's value."""
    picks = _selections(read)
    sources = list(dict.fromkeys(m for m, _, _ in picks))
    executed = {"numerator": sum(ran for _, _, ran in picks), "denominator": len(picks), "n": len(sources),
                "sources": sources,
                "why": WHY["executed"].format(n=sum(ran for _, _, ran in picks), d=len(picks)) if picks
                else WHY["no_selection"]}
    labelled = [(m, d, right) for m, d, _ in picks if (right := _right_route(labels, m, d)) is not None]
    if not labelled:
        out = _unmeasured(None, None, [], WHY["no_route_labels"] if picks else WHY["no_selection"])
    else:
        right = sum((d.get("selected_option") or {}).get("id") == route for _, d, route in labelled)
        out = _rate(right, len(labelled), list(dict.fromkeys(m for m, _, _ in labelled)), "precision", "no_selection",
                    u=len(picks) - len(labelled))
        out["labelled_by"] = "the caller's route_labels"
    out["selections_executed"] = executed
    return out


def _ran(r, decision: dict) -> bool:
    """Whether the step or stage a decision chose for shows an executed row (an output, an executed verdict, a decided
    gate, or for the reproduce stage receipts from the route it chose)."""
    plan = _plan(r)
    unit = next((u for u in (plan.units if plan else ()) if u.key == decision.get("work_unit_id")), None)
    if unit is not None:
        if unit.kind == "do":
            return any(x["unit_key"] == unit.key and x["state"] == "closed" and x["artifact_id"] for x in r.rows["runs"])
        if unit.kind == "verify":
            return any(v["unit_key"] == unit.key and v["executed"] for v in r.rows["verdicts"])
        return any(e["detail"].get("unit") == unit.key for e in r.rows.get("decided") or [])
    if (decision.get("metadata") or {}).get("stage") != "reproduce":
        return False
    route = ".".join(decision["selected_option"]["required_capabilities"][0])
    swapped = any(d["stage"] == "reproduce" and d["trigger"] == "tool_unavailable" and d["changed"]["from"] == route
                  and not d.get("refused") for d in (e["detail"] for e in r.rows.get("reconfiguration") or []))
    found = [e["detail"]["refs"]["reproduction"] for e in r.rows.get("messages") or []
             if "reproduction" in (e["detail"].get("refs") or {})]
    receipts = (found[-1].get("receipts") if found else None) or []
    in_place = route.endswith(".run_tests_in_place")  # its receipts carry the runner's verdict; the copy's an exit code
    return bool(receipts) and not swapped and all(x.get("executed") and ("verdict" in x) is in_place for x in receipts)


def _silent(read) -> dict:
    """Unmeasured (it needs a label from a check run after delivery); the count G6 can make is kept apart."""
    caught, searched, sources = 0, 0, []
    for r in read:
        plan = _plan(r)
        if plan is None:
            continue
        searches = {u.key for u in plan.units if trace.elicits(u)}
        tests = {u.key for u in plan.units if u.kind == "verify" and u.key not in searches}
        for artifact in dict.fromkeys(v["artifact_id"] for v in r.rows["verdicts"]):
            on_it = [v for v in r.rows["verdicts"] if v["artifact_id"] == artifact]
            agreed = [v for v in on_it if v["unit_key"] in tests]
            elicited = [v for v in on_it if v["unit_key"] in searches and v["executed"]]
            if agreed and elicited and all(v["executed"] and v["result"] == "pass" for v in agreed):
                searched += 1
                caught += any(v["result"] == "fail" for v in elicited)
                sources.append(r.mid)
    sources = list(dict.fromkeys(sources))
    out = _unmeasured(None, None, [], WHY["silent"])
    out["caught_on_test_passing_proposals"] = {
        "numerator": caught, "denominator": searched, "n": len(sources), "sources": sources,
        "why": WHY["silent_proxy"].format(n=caught, d=searched) if searched else WHY["no_silent_proxy"]}
    return out


def _recovery(read) -> dict:
    """Each change of course after a failure (a send-back, or a route changed on a failure trigger), and whether a
    later result then passed every check (or the reproduction then ran)."""
    tried, recovered, sources = 0, 0, []
    for r in read:
        plan = _plan(r)
        doers = {u.key for u in plan.units if u.kind == "do"} if plan else set()
        judged = {v["run_id"]: v["artifact_id"] for v in r.rows["verdicts"]}
        attempts: dict[tuple[str, str], bool] = {}
        for event in r.rows.get("messages") or []:
            refs = event["detail"].get("refs") or {}
            if "run_id" in refs and not refs.get("repair_stop") and event["detail"].get("to") in doers:
                after = refs.get("artifact_id") or judged.get(refs["run_id"]) or f"run:{refs['run_id']}"
                attempts.setdefault((event["detail"]["to"], after), True)
        for decision in (e["detail"] for e in r.rows.get("reconfiguration") or []):
            if (decision.get("trigger") in RECOVERY_TRIGGERS and not decision.get("refused")
                    and decision["changed"]["to"] not in (None, "person")):
                if decision["unit"] in doers:
                    attempts.setdefault((decision["unit"], decision["observation"].get("artifact_id", "")), True)
                elif decision["stage"] == "reproduce":
                    attempts.setdefault(("reproduce", decision["changed"]["to"]), True)
        for unit, after in attempts:
            tried += 1
            sources.append(r.mid)
            recovered += _recovered(r, plan, unit, after)
    return _rate(recovered, tried, list(dict.fromkeys(sources)), "recovery", "no_recovery")


def _recovered(r, plan, unit: str, after: str) -> bool:
    if unit == "reproduce":
        found = [e["detail"]["refs"]["reproduction"] for e in r.rows.get("messages") or []
                 if "reproduction" in (e["detail"].get("refs") or {})]
        return bool(found) and bool(found[-1].get("reproduced"))
    made = [e["evidence_id"] for e in r.rows.get("artifacts") or [] if e["detail"].get("unit") == unit]
    later = made[made.index(after) + 1:] if after in made else []  # no anchor, no recovery
    checks = {u.key for u in plan.units if u.kind == "verify" and u.verifies == unit}
    return any(checks and checks <= {v["unit_key"] for v in r.rows["verdicts"] if v["artifact_id"] == artifact
                                     and v["executed"] and v["result"] == "pass"} for artifact in later)


def _task_key(r) -> dict | None:
    signature = (((r.mission.get("acceptance") or {}).get("metadata") or {}).get("coding") or {}).get("task_signature")
    return {k: signature.get(k) for k in _KEY} if signature and all(signature.get(k) for k in _KEY) else None


def _history(missions: MissionStore) -> dict:
    """Every learning decision and mission start in all of this owner's ventures, each placed by its time and then its
    row (never the per-venture ``seq``). Read only."""
    owned = (" FROM evidence e JOIN missions m ON m.mission_id = e.ref AND m.venture_id = e.venture_id"
             " JOIN ventures v ON v.venture_id = m.venture_id WHERE v.owner = ?")
    found = []
    for at, row, venture, detail in missions.conn.execute(
            "SELECT e.at, e.rowid, e.venture_id, e.detail_json" + owned + " AND e.kind = 'message'"
            " AND (json_extract(e.detail_json, '$.refs.learning_decision') IS NOT NULL"
            " OR json_extract(e.detail_json, '$.refs.learned_probe_promoted') IS NOT NULL) ORDER BY e.at, e.rowid",
            (missions.owner,)).fetchall():
        refs = json.loads(detail or "{}")["refs"]
        approved = refs.get("learned_probe_promoted") or {}
        decision = refs.get("learning_decision") or {
            "evidence": [{"key": approved.get("key")}],
            "promotion_decision": {"guards": {approved.get("guard_id"): {"verdict": "promoted"}}}}
        promotion = decision.get("promotion_decision") or {}
        found.append({"place": (at, row), "venture": venture,
                      "keys": [e.get("key") for e in decision.get("evidence") or []],
                      "guards": [g for g, entry in (promotion.get("guards") or {}).items()
                                 if entry.get("verdict") == "promoted"],
                      "retired": list(promotion.get("retired") or [])})
    starts = {mission: (at, row) for mission, at, row in missions.conn.execute(
        "SELECT e.ref, e.at, e.rowid" + owned + " AND e.kind = 'mission.created'", (missions.owner,)).fetchall()}
    return {"decisions": found, "starts": starts}


def _covering(r, history: dict) -> set[str]:
    """The approved learned checks that covered this mission's task when it began (decided earlier, in any venture)."""
    key, start = _task_key(r), history["starts"].get(r.mid)
    if key is None or start is None:
        return set()
    live: set[str] = set()
    for decision in (d for d in history["decisions"] if d["place"] < start):
        if key in decision["keys"]:
            live |= set(decision["guards"])
        live -= set(decision["retired"])
    return live


def _retrieved(r) -> list[str]:
    found = [e["detail"]["refs"]["learning_selection"] for e in r.rows.get("messages") or []
             if "learning_selection" in (e["detail"].get("refs") or {})]
    return [g.get("guard_id") for g in (found[-1] if found else []) or []]


def _executed(r) -> set[str]:
    return {(m.get("probe") or {}).get("guard_id") for e in r.rows.get("closed") or []
            for m in e["detail"].get("verification") or [] if m.get("executed")} - {None, ""}


def _retrieval(read) -> dict:
    due = [r for r in read if r.covering]
    hit = [r for r in due if set(_retrieved(r)) & r.covering]
    return _rate(len(hit), len(due), [r.mid for r in due], "retrieval", "no_retrieval")


def _reuse(read) -> dict:
    pairs = [(r.mid, g in _executed(r)) for r in read for g in _retrieved(r)]
    return _rate(sum(ok for _, ok in pairs), len(pairs), list(dict.fromkeys(m for m, _ in pairs)), "reuse", "no_reuse")


def _promoted_reuse(read) -> dict:
    pairs = [(r.mid, g in _executed(r)) for r in read for g in sorted(r.covering)]
    return _rate(sum(ok for _, ok in pairs), len(pairs), list(dict.fromkeys(m for m, _ in pairs)), "promoted",
                 "no_promoted")


def _unpriced(r) -> int:
    """Spending this mission recorded with no price (its preflight's, its coding loop's timing, a learning round's):
    each is an unknown amount, never 0."""
    found = sum("money" in (e["detail"].get("cost") or {}) and e["detail"]["cost"]["money"] is None
                for e in r.rows.get("preflight") or [])
    for event in r.rows.get("messages") or []:
        refs = event["detail"].get("refs") or {}
        timing = refs.get("coding_timing")
        spending = (refs.get("learning_decision") or {}).get("spending") if refs.get("learning_decision") else None
        found += sum(isinstance(record, dict) and "cost" in record and record["cost"] is None
                     for record in (timing, spending))
    return found


def _cost(read) -> dict:
    """The known cost of every finished mission over the verified ones; unknown when any amount is unknown."""
    verified = [r for r in read if _state(r.mission) == VERIFIED]
    sources = [r.mid for r in read]
    if not verified:
        return _unmeasured(None, 0, [], WHY["no_verified"])
    spent = economics.total([c for r in read for c in r.rows.get("costs") or []])
    legacy = sum(x["state"] != "open" and x["cost_source"] == "none" for r in read for x in r.rows["runs"])
    unpriced = sum(_unpriced(r) for r in read)
    if spent.amount_minor_units is None or legacy or unpriced:
        return _unmeasured(None, len(verified), sources, WHY["unknown_cost"].format(
            u=spent.unknown_entries + spent.other_currency_entries + legacy + unpriced))
    return _rate(spent.amount_minor_units, len(verified), sources, "cost", "no_verified", k=len(read),
                 e=spent.estimated_entries)


def _accepted(read) -> list:
    return [r for r in read if r.mission["status"] == "done" and _state(r.mission) in ACCEPTED]


def _minutes(read) -> dict:
    """Measured seconds at the terminal on accepted tasks, in minutes per task; unknown when any was not measured."""
    accepted = _accepted(read)
    if not accepted:
        return _unmeasured(None, 0, [], WHY["no_accepted"])
    sources = [r.mid for r in accepted]
    spent = [e["detail"] for r in accepted for e in r.rows.get("interventions") or []]
    if any(t.get("duration_source") != "measured" or t.get("duration_seconds") is None for t in spent):
        return _unmeasured(None, len(accepted), sources, WHY["untimed"])
    seconds = sum(t["duration_seconds"] for t in spent)
    out = _rate(seconds, len(accepted), sources, "minutes", "no_accepted")
    out["value"] = round(seconds / 60 / len(accepted), 4)
    return out


def _declared(read) -> dict:
    """The minutes people said their decisions on accepted tasks took (never mixed into the measured minutes), and how
    long those questions waited (latency, not effort), each under its own name."""
    accepted = _accepted(read)
    sources = [r.mid for r in accepted]
    said = [m for r in accepted for m in decisions.said_minutes(r.rows.get("decided") or [])]
    made = sum(len(r.rows.get("decided") or []) for r in accepted)
    if not accepted:
        declared = _unmeasured(None, 0, [], WHY["no_accepted"])
    elif not said:
        declared = _unmeasured(None, len(accepted), sources, WHY["undeclared"])
    else:
        declared = _rate(round(sum(said), 4), len(accepted), sources, "declared", "no_accepted", c=len(said), k=made)
    declared.update(human_decisions=made, minutes_coverage=round(len(said) / made, 4) if made else None)
    waits = [(r.mid, s) for r in accepted
             for s in decisions.wait_seconds(r.rows.get("asked") or [], r.rows.get("decided") or [])]
    wait = _rate(round(sum(s for _, s in waits) / 60, 4), len(waits), list(dict.fromkeys(m for m, _ in waits)), "wait",
                 "no_wait")
    return {"declared": declared, "human_wait_minutes": wait}


def _funnel(missions: MissionStore, read) -> dict:
    """How far each capability gets, in four counts kept apart (never one of §25's metrics, never a score): ``present``
    (a reviewed contract ships), ``reachable`` (its latest probe for this owner passed against that contract),
    ``invoked`` (a finished mission called it) and ``effective`` (unmeasured: no label from a check run after delivery)."""
    from g6.workforce import capabilities as caps

    shipped = {f"{component}.{op}": contract.sha256 for (component, op), contract in caps.load_contracts().items()}
    latest: dict[str, tuple[str, bool]] = {}
    conn = missions.conn
    probed = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='preflight_runs'").fetchone() is not None
    if probed:
        for component, op, sha, passed in conn.execute(
                "SELECT component, op, contract_sha, passed FROM preflight_runs WHERE owner=? ORDER BY rowid",
                (missions.owner,)):
            latest[f"{component}.{op}"] = (sha, bool(passed))
    reachable = sum(latest.get(key) == (sha, True) for key, sha in shipped.items())
    called: dict[str, dict] = {}
    for r in read:
        for run in r.rows.get("runs") or []:
            for call in trace._calls(run):
                key = f"{call.get('component')}.{call.get('op')}"
                row = called.setdefault(key, {"calls": 0, "missions": set(), "present": key in shipped,
                                              "reachable": key in shipped and latest.get(key) == (shipped[key], True)})
                row["calls"] += 1
                row["missions"].add(r.mid)
    sources = sorted({m for row in called.values() for m in row["missions"]})
    by_capability = {key: {**row, "missions": len(row["missions"])} for key, row in sorted(called.items())}
    return {"present": {"value": len(shipped), "numerator": None, "denominator": None, "n": 0, "sources": [],
                        "status": MEASURED, "why": WHY["present"].format(v=len(shipped))},
            "reachable": ({"value": reachable, "numerator": reachable, "denominator": len(shipped), "n": 0,
                           "sources": [], "status": MEASURED,
                           "why": WHY["reachable"].format(v=reachable, d=len(shipped))}
                          if probed and latest else _unmeasured(None, len(shipped), [], WHY["no_reachable"])),
            "invoked": ({"value": len(by_capability), "numerator": None, "denominator": None, "n": len(sources),
                         "sources": sources, "status": MEASURED,
                         "why": WHY["invoked"].format(v=len(by_capability), n=len(read))}
                        if read else _unmeasured(None, None, [], WHY["no_invoked"])),
            "effective": _unmeasured(None, None, [], WHY["no_effective"]),
            "by_capability": by_capability}


__all__ = ["ENTRY", "MEASURED", "METRICS", "UNMEASURED", "RouteLabels", "report"]
