# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Trace: one mission reconstructed from its own rows (E9a; Moat spec §49 P0, §24; Ledger §3).

``trace`` returns the eleven fields Moat §49 P0 asks of every real mission: objective, plan, workers, capabilities,
attempts, cost, evidence, verdict, failure, recovery and human intervention. They are built from the rows the
workforce already keeps: the mission, its plan revisions, worker instances, attempts, verdicts, cost rows, and its
event history (artifacts, retries, messages, interventions, questions to a person, scheduler decisions). It adds no
store and writes nothing. Like every reader here it is owner-scoped: another owner's mission is ``NOT_FOUND``.

Each field is built only from the rows that record it (``SOURCES``). When they are missing it reads
``{"absent": why}``, never a made-up value. "Nothing happened" is a value only where the rows show it: no failure
when every attempt closed without a failure code, no intervention when the mission's own history (which records each
one with what it records) holds none.

Two more fields are derived, never asked of anyone. The task class is the template the mission was made from
(``ad_hoc`` otherwise). The environment signature is the G6 build recorded when the mission was created, the contract
digest of each call it made, the scheduler's policy version, and the providers and resources of its cost rows.

``stages`` (E2E Build 7, test J) maps the same rows to founder brief §10's loop: which stages ran, with the ids of the
rows that show it, and which the mission's standard left out, with why. It too adds no store and writes nothing.
"""
from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path
from typing import Any

from g6.workforce import classes, economics
from g6.workforce.contracts import BLOCKED, Outcome
from g6.workforce.missions import AD_HOC, MissionStore
from g6.workforce.runs import RunLedger, outcome_of

#: Moat §49 P0, in its order.
P0 = ("objective", "plan", "workers", "capabilities", "attempts", "cost", "evidence", "verdict", "failure", "recovery",
      "human_intervention")
#: The row kinds each field is built from (``gather``'s keys): without them the field reads absent.
SOURCES: dict[str, tuple[str, ...]] = {
    "objective": ("mission",), "plan": ("plans",), "workers": ("instances",),
    "capabilities": ("plans", "instances", "runs"), "attempts": ("runs",), "cost": ("costs",),
    "evidence": ("artifacts", "closed"), "verdict": ("verdicts", "finished"), "failure": ("runs", "finished"),
    "recovery": ("runs",), "human_intervention": ("created",),
}
#: The event kinds ``gather`` keeps, under the row kind each is read as.
_EVENTS = {"created": "mission.created", "schedule": "schedule", "artifacts": "artifact", "closed": "run.closed",
           "retries": "retry", "messages": "message", "asked": "approval.requested", "decided": "approval.decided",
           "interventions": "intervention", "finished": "mission.closed", "preflight": "preflight",
           "escalation": "escalation", "reconfiguration": "reconfiguration",
           # E2E Build 7: the move that froze what counts as done (its acceptance digest) is the standard stage's row
           "statuses": "mission.status"}
#: The kind ``coding_learning.record`` writes its learning candidates (episode rows) under (E2E Build 7 review I8).
LEARNING_KIND = "code_change"
#: The failure code of a check that could not run (``rungs``): unmeasured, never a failure (review I10).
UNRUN = "verify_unmeasured"
_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?\Z")


def trace(missions: MissionStore, mission_id: str) -> dict:
    """One of this owner's missions, reconstructed: its P0 fields, task class and environment signature."""
    return assemble(gather(missions, mission_id))


def missing(found: dict) -> list[str]:
    """The P0 fields a trace could not reconstruct, in P0's order."""
    return [name for name in P0 if "absent" in found["p0"][name]]


def gather(missions: MissionStore, mission_id: str, *, ledger: RunLedger | None = None,
           instances: bool = True) -> dict[str, list]:
    """The rows one mission's trace is built from, by kind (``NOT_FOUND`` for an unknown or another owner's).
    ``ledger``: the caller's own (``RunLedger.reader`` for one that must not write), so no schema statement runs again;
    ``instances=False``: without the worker instances, which the stage map never reads (E2E Build 7)."""
    mission = missions.get(mission_id)
    ledger = RunLedger(missions) if ledger is None else ledger
    events = missions.events(mission_id)
    runs = ledger.runs(mission_id)
    rows: dict[str, list] = {kind: [e for e in events if e["kind"] == name] for kind, name in _EVENTS.items()}
    plans = []
    for event in (e for e in events if e["kind"] == "plan.revision"):
        revision = int(event["detail"].get("revision") or 0)
        plans.append({"revision": revision, "plan_sha": event["detail"].get("plan_sha", ""),
                      "actor": event["detail"].get("actor", ""), "plan": missions.plan(mission_id, revision)[1],
                      "evidence_id": event["evidence_id"]})
    return {**rows, "mission": [mission], "plans": plans, "runs": runs,
            "instances": [ledger.instance(mission_id, r["instance_id"]) for r in runs] if instances else [],
            "verdicts": ledger.verdicts(mission_id), "costs": ledger.cost_entries(mission_id),
            "episodes": _candidates(missions, mission)}


def _candidates(missions: MissionStore, mission: dict) -> list[dict]:
    """The learning candidates G6 recorded for this mission: the episode rows ``coding_learning.record`` writes, matched
    on the mission and the coding-learning kind (never on a failure code). Read only; [] in a store with no episodes."""
    if missions.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='episodes'").fetchone() is None:
        return []
    rows = missions.conn.execute(
        "SELECT episode_id, task_id, task_kind, code, source, detail_json, at FROM episodes"
        " WHERE venture_id=? AND task_id=? AND task_kind=? ORDER BY rowid",
        (mission["venture_id"], mission["mission_id"], LEARNING_KIND)).fetchall()
    return [{"episode_id": r[0], "task_id": r[1], "task_kind": r[2], "code": r[3], "source": r[4],
             "detail": json.loads(r[5] or "{}"), "at": r[6]} for r in rows]


def assemble(rows: dict[str, list]) -> dict:
    """The trace from ``gather``'s rows (a test removes a row kind here to see its fields go absent)."""
    mission = rows["mission"][0] if rows["mission"] else {}
    created = rows["created"][0]["detail"] if rows["created"] else {}
    p0 = {"objective": _objective(rows), "plan": _plan(rows), "workers": _workers(rows),
          "capabilities": _capabilities(rows), "attempts": _attempts(rows), "cost": _cost(rows),
          "evidence": _evidence(rows), "verdict": _verdict(rows), "failure": _failure(rows),
          "recovery": _recovery(rows), "human_intervention": _human(rows)}
    found = {"mission_id": mission.get("mission_id", ""), "status": mission.get("status", ""),
             "task_class": str(created.get("template") or AD_HOC), "environment": _environment(rows, created),
             "p0": p0}
    found["learning_decisions"] = [e["detail"]["refs"]["learning_decision"] for e in rows["messages"]
                                   if "learning_decision" in e["detail"].get("refs", {})]
    found["learning_selection"] = [g for e in rows["messages"]
                                    for g in e["detail"].get("refs", {}).get("learning_selection", [])]
    found["missing"] = missing(found)
    return found


# -- the fields ---------------------------------------------------------------------------------------------------------
def _absent(why: str) -> dict:
    return {"absent": why}


def _closed(rows: dict) -> Outcome | None:
    """The mission's own outcome, once it is finished (its ``mission.closed`` event and its row)."""
    if not (rows["finished"] and rows["mission"]):
        return None
    return Outcome.from_record(rows["mission"][0]["outcome"])


def _calls(run: dict) -> list[dict]:
    return [c for c in json.loads(run.get("calls_json") or "[]") if isinstance(c, dict)]


def _objective(rows: dict) -> dict:
    mission = rows["mission"][0] if rows["mission"] else {}
    if not str(mission.get("objective") or "").strip():
        return _absent("the mission's own row is missing, so its objective cannot be read")
    return {"text": mission["objective"], "risk_tier": mission["risk_tier"], "budget_cents": mission["budget_cents"]}


def _plan(rows: dict) -> dict:
    if not rows["plans"]:
        return _absent("no plan was stored for this mission")
    latest = rows["plans"][-1]["plan"]
    units = [{"key": u.key, "kind": u.kind, "verifies": u.verifies, "parent": u.parent,
              "capabilities": [".".join(c) for c in u.capabilities], "code_step": bool(u.call or u.oracle)}
             for u in latest.units]
    schedule = rows["schedule"][-1]["detail"].get("decisions") or [] if rows["schedule"] else []
    return {"revisions": [{k: p[k] for k in ("revision", "plan_sha", "actor")} for p in rows["plans"]],
            "units": units,
            "schedule": [{"step": d["work_unit_id"], "how": d["selected_option"]["strategy_type"],
                          "reasons": list(d["reason_codes"])} for d in schedule]}


def _workers(rows: dict) -> dict:
    if not rows["instances"]:
        return _absent("no worker was formed: no attempt was opened")
    return {"instances": [{"instance_id": w["instance_id"], "unit": w["unit_key"], "attempt": w["attempt"],
                           "template": w["template"], "rung": w["rung"], "read_only": bool(w["read_only"]),
                           "spec_sha256": w["spec_sha"], "status": w["status"]} for w in rows["instances"]]}


def _capabilities(rows: dict) -> dict:
    planned = sorted({".".join(c) for p in rows["plans"][-1:] for u in p["plan"].units for c in u.capabilities})
    allowed = sorted({".".join(pair) for w in rows["instances"]
                      for pair in json.loads(w["spec_json"] or "{}").get("allowed") or []})
    called = [{"run_id": r["run_id"], "capability": f"{c.get('component')}.{c.get('op')}",
               "contract_sha256": c.get("contract_sha", ""), "refused": bool(c.get("refused"))}
              for r in rows["runs"] for c in _calls(r)]
    if not (planned or allowed or called):
        return _absent("neither a plan, a worker nor a call names a capability")
    return {"planned": planned, "allowed": allowed, "called": called}


def _attempts(rows: dict) -> dict:
    if not rows["runs"]:
        return _absent("no attempt was opened")
    return {"runs": [{"run_id": r["run_id"], "unit": r["unit_key"], "attempt": r["attempt"], "rung": r["rung"],
                      "state": r["state"], "completion_state": "" if r["state"] == "open" else outcome_of(r).state,
                      "failure_code": r["failure_code"], "model_calls": r["model_calls"],
                      "cost_minor_units": r["cost_cents"], "cost_source": r["cost_source"],
                      "started_at": r["started_at"], "finished_at": r["finished_at"]} for r in rows["runs"]]}


def _cost(rows: dict) -> dict:
    if not rows["costs"]:
        return _absent("no cost row was recorded (an attempt closed before G6 kept cost rows has none)")
    found = economics.total(rows["costs"])
    return {"total": {"amount_minor_units": found.amount_minor_units, "known_minor_units": found.known_minor_units,
                      "currency": found.currency, "entries": found.entries, "unknown_entries": found.unknown_entries,
                      "estimated_entries": found.estimated_entries},
            "entries": [{"unit": e.work_unit_id, "attempt_id": e.attempt_id, "category": e.category,
                         "source": e.source, "provider": e.provider, "resource": e.resource_name,
                         "amount_minor_units": e.amount_minor_units, "currency": e.currency, "estimated": e.estimated,
                         "quantity": e.quantity, "quantity_unit": e.quantity_unit, "method": e.method}
                        for e in rows["costs"]]}


def _evidence(rows: dict) -> dict:
    outputs = [{"artifact_id": e["evidence_id"], "unit": e["detail"].get("unit", ""),
                "run_id": e["detail"].get("run_id", ""), "sha256": e["detail"].get("sha256", ""),
                "schema": e["detail"].get("schema", ""), "producer": e["detail"].get("producer", ""),
                "bytes": economics.size_of(e["detail"].get("content"))} for e in rows["artifacts"]]
    checks = [{**m, "run_id": e["detail"].get("run_id", "")} for e in rows["closed"]
              for m in e["detail"].get("verification") or []]
    if not (outputs or checks):
        return _absent("no output was kept and no check recorded what it judged")
    return {"outputs": outputs, "checks": checks}


def _verdict(rows: dict) -> dict:
    checks = [{"unit": v["unit_key"], "verifies": v["verifies_key"], "artifact_id": v["artifact_id"],
               "verifier_kind": v["verifier_kind"], "executed": bool(v["executed"]),
               "independent": bool(v["independent"]), "model_free": bool(v["model_free"]),
               "model_calls": v["model_calls"], "result": v["result"], "state": v["state"],
               "reasons": json.loads(v["reasons_json"] or "[]")} for v in rows["verdicts"]]
    closed = _closed(rows)
    if not (checks or closed):
        return _absent("the mission has not closed and no check has judged an output")
    out: dict[str, Any] = {"checks": checks}
    if closed is not None:
        said = {k: v for k, v in rows["finished"][-1]["detail"].items() if k != "actor"}
        out.update(state=closed.state, reasons=list(closed.reasons), closed=said)
    return out


def _failure(rows: dict) -> dict:
    closed = _closed(rows)
    if not (rows["runs"] or closed):
        return _absent("no attempt was opened and the mission has not closed")
    failed = [{"run_id": r["run_id"], "unit": r["unit_key"], "attempt": r["attempt"], "code": r["failure_code"],
               "class": classes.classify(r["failure_code"]), "reasons": list(outcome_of(r).reasons)}
              for r in rows["runs"] if r["failure_code"]]
    out: dict[str, Any] = {"failed_attempts": failed}
    if closed is not None:
        out["stopped"] = list(closed.reasons) if closed.state == BLOCKED else []
    return out


def _recovery(rows: dict) -> dict:
    if not rows["runs"]:
        return _absent("no attempt was opened, so no recovery could be tried")
    return {"retries": [{k: v for k, v in e["detail"].items() if k != "actor"} for e in rows["retries"]],
            "sent_back": [{"from": e["detail"].get("from", ""), "to": e["detail"].get("to", ""),
                           "run_id": (e["detail"].get("refs") or {}).get("run_id", "")} for e in rows["messages"]],
            "set_aside": [{"unit": e["detail"].get("unit", ""), "decision_id": e["detail"].get("decision_id", "")}
                          for e in rows["decided"] if e["detail"].get("kind") == "adjudication"
                          and e["detail"].get("decision") == "set_aside"],
            "called_for": [{"run_id": r["run_id"], "code": r["failure_code"],
                            "step": classes.recovery(r["failure_code"])} for r in rows["runs"] if r["failure_code"]]}


def _human(rows: dict) -> dict:
    if not rows["created"]:
        return _absent("the mission's event history is missing, so who intervened cannot be read")
    found = [economics.HumanIntervention.from_detail(e["detail"], id=e["evidence_id"], recorded_at=e["at"])
             for e in rows["interventions"]]
    return {"interventions": [{"kind": i.kind, "actor_type": i.actor_type, "decided_by": i.decided_by,
                               "required_by_policy": i.required_by_policy, "outcome": i.outcome,
                               "unit": i.work_unit_id, "duration_seconds": i.duration_seconds,
                               "duration_source": i.duration_source} for i in found],
            "questions": [{"unit": e["detail"].get("unit", ""), "kind": e["detail"].get("kind", "")}
                          for e in rows["asked"]],
            "attention": economics.attention_total(found).as_dict()}


# -- the stage map (E2E Build 7, test J; founder brief §10, §24 test J) ---------------------------------------------------
#: Founder brief §10's canonical loop, in its order: what ``stages`` maps a mission's rows to.
STAGES = ("classify", "standard", "retrieve", "failure_surfaces", "select", "plan", "execute", "observe",
          "verify_progress", "reconfigure", "elicit", "final_verify", "adjudicate", "value", "learn")
NOT_APPLICABLE = "not_applicable"
#: The stages a task class's standard leaves out, with why (§10: "The Task Standard determines which stages can be
#: safely skipped"). Every other stage applies, except that ``reconfigure`` and ``learn`` wait for what calls for them.
SKIPPED = {"format_json": {
    # COPY: Why the light JSON route looks up no learned checks.
    "retrieve": "The light JSON route has no learned checks to look up: its one check compares the exact JSON value.",
    # COPY: Why the light JSON route has no reproduction and no progress check (reproduction is that stage's first step).
    "verify_progress": ("The light JSON route has no reported failure to reproduce and only one step, so its one check "
                        "is the final check."),
    # COPY: Why the light JSON route searches for no hidden failures.
    "elicit": ("The light JSON route's check compares the exact JSON value, so its standard asks for no further search "
               "for hidden failures."),
    # COPY: Why the light JSON route has no learning step.
    "learn": "The light JSON route's standard has no learning step.",
}}
# COPY: The stage map's reasons, one per stage and outcome (shown in mission_status with detail="full").
WHY = {
    "classified": "G6 recorded this mission's task class, {cls}, when it was made.",
    "unclassified": "The chat drafted this mission itself, so G6 recorded no task class for it.",
    "frozen": "What counts as done was frozen before any work began.",
    "confirmed": "What counts as done was frozen before any work began, and a person confirmed it.",
    "unfrozen": "No contract of what counts as done was frozen for this mission.",
    "none_learned": "G6 looked for checks it had learned for this task and found none.",
    "retrieved": "G6 retrieved {n} learned check(s) for this task, and each one ran.",
    "retrieved_unrun": "G6 retrieved {n} learned check(s) for this task, but {k} of them never ran.",
    "not_looked": "G6 did not look for earlier experience for this mission.",
    "surfaces": "G6's preflight recorded an answer to \"What usually goes wrong\".",
    "no_surfaces": "No preflight recorded an answer to \"What usually goes wrong\".",
    "selected": "G6's scheduler recorded {n} route decision(s), each with the routes it weighed.",
    "unselected": "No scheduler decision was recorded.",
    "planned": "G6 checked and stored a plan ({n} revision(s)).",
    "unplanned": "No plan was stored.",
    "executed": "Each step that does the work produced an output.",
    "no_step": "The plan has no step that does the work.",
    "unexecuted": "These steps never produced an output: {keys}.",
    "observed": "G6 kept {n} output(s), each with its digest.",
    "unobserved": "G6 kept no output from this mission.",
    "reproduced": "G6 reproduced the reported failure on the frozen baseline, and a check judged a proposed change.",
    # E2E Build 7 review I9 (lead ruling), the lead's words
    "unjudged_proposal": "G6 reproduced the failure, but no proposed change was judged.",
    "unreproduced": "G6 did not reproduce the reported failure on the frozen baseline.",
    "judged": "Checks judged {n} output(s) before the final one.",
    "unjudged": "No check judged an output before the final one.",
    "final_only": "Each step produced only its final output, so its one check is the final check.",
    "nothing_failed": "Nothing failed, so nothing needed to change course.",
    "changed": "After a failure, G6 recorded a change of course, a retry or a stop.",
    # E2E Build 7 review I7 and I10, the lead's words
    "repriced": "A cost changed, so G6 re-chose the route inside the frozen standard.",
    "unchanged": "A step failed, and G6 recorded no change of course, retry or stop.",
    "unrun": "A check could not run, and G6 recorded no change of course, retry or stop.",
    "searched": "Every check that searches for hidden failures ran on the final output.",
    "no_search": "The plan has no check that searches for hidden failures.",
    "unsearched": "These checks that search for hidden failures did not run on the final output: {keys}.",
    "checked": "Every check in the plan that is not a search for hidden failures ran on the final output.",
    "no_check": "The plan has no check of the final output.",
    "unchecked": "These checks did not run on the final output: {keys}.",
    "no_plan": "No plan was stored, so this stage could not run.",
    "settled": "G6 settled the result as {state} against what counts as done.",
    "unsettled": "The mission has not closed, so its result has not been settled.",
    # #181, review M1: a cancel G6 did not make is not G6 settling the result
    "cancelled": "{who} cancelled the mission, so G6 did not settle its result against what counts as done.",
    "valued": "G6 recorded {n} cost amount(s) for this mission's attempts ({unknown} unknown, never counted as zero).",
    "unvalued": "No cost was recorded for this mission's attempts.",
    "nothing_new": "No search for hidden failures caught a new failure, so there was nothing new to learn.",
    # E2E Build 7 review I8 (lead ruling): the stage is the learning candidate G6 records
    "candidate": "G6 recorded the failure it caught as a learning candidate.",
    "candidate_promoted": ("G6 recorded the failure it caught as a learning candidate, and a check learned from it was "
                           "promoted."),
    "candidate_kept": ("G6 recorded the failure it caught as a learning candidate; the check learned from it was not "
                       "promoted."),
    "no_candidate": "G6 caught a new failure ({checks}) but recorded no learning candidate for it.",
}
# COPY: Who cancelled a mission G6 did not settle (review M1), as the start of WHY["cancelled"].
CANCELLED_BY = {"chat": "The owner's chat", "human": "A person"}


def stages(rows: dict[str, list]) -> dict[str, dict]:
    """Each §10 stage of one mission from ``gather``'s rows and nothing else, as ``{ran, evidence, why}``. ``ran`` is
    True only when an executed row shows it: a run that produced an output, a verdict that executed, a probe's receipt,
    or an event that records a finished action. It is ``"not_applicable"`` when the mission's standard leaves the stage
    out (``SKIPPED``) or nothing called for it, and False otherwise. A stage that was only planned never ran.
    ``evidence`` lists the ids of the rows that show it (event, run, verdict, cost row and plan revision ids). A row
    kind missing from ``rows`` reads as none (a test removes one here)."""
    seen = _Seen(rows)
    left_out = SKIPPED.get(seen.cls, {})
    out = {}
    for name in STAGES:
        if name in left_out:
            out[name] = {"ran": NOT_APPLICABLE, "evidence": [], "why": left_out[name]}
            continue
        ran, evidence, why = _RULES[name](seen)
        out[name] = {"ran": ran, "evidence": list(dict.fromkeys(evidence)), "why": why}
    return out


def elicits(unit) -> bool:
    """A check that searches for hidden failures: its oracle compares old and new behaviour or replays a learned probe
    (``regression`` in its params: Brief 2's differential check, Brief 3's learned checks)."""
    return unit.kind == "verify" and "regression" in ((unit.oracle or {}).get("params") or {})


class _Seen:
    """``gather``'s rows as the stage rules read them."""

    def __init__(self, rows: dict[str, list]) -> None:
        self.rows = rows
        self.mission = (rows.get("mission") or [{}])[0]
        created = self.ev("created")
        self.cls = str((created[0]["detail"].get("template") if created else "") or AD_HOC)
        self.plans = rows.get("plans") or []
        self.plan = self.plans[-1]["plan"] if self.plans else None
        self.runs, self.verdicts, self.costs = rows.get("runs") or [], rows.get("verdicts") or [], rows.get("costs") or []
        self.closed = {e["detail"].get("run_id"): e for e in self.ev("closed")}
        self.latest = {r["unit_key"]: r["artifact_id"] for r in self.runs if r["state"] == "closed" and r["artifact_id"]}
        self.standard = ((self.mission.get("acceptance") or {}).get("metadata") or {}).get("coding") or {}

    def ev(self, kind: str) -> list[dict]:
        return self.rows.get(kind) or []

    def refs(self, key: str) -> list[dict]:
        """The mission's messages whose refs carry ``key``."""
        return [e for e in self.ev("messages") if key in (e["detail"].get("refs") or {})]

    def finals(self) -> set[str]:
        """The mission's final outputs, as the result view reads them: the latest output of each step that does the
        work and that no other such step consumes (every latest output when no plan was stored)."""
        if self.plan is None:
            return set(self.latest.values())
        kinds = {u.key: u.kind for u in self.plan.units}
        consumed = {i for u in self.plan.units if u.kind == "do" for i in u.inputs}
        consumed |= {before for before, after in self.plan.edges if kinds.get(after) == "do"}
        return {self.latest[u.key] for u in self.plan.units if u.kind == "do" and not u.parent
                and u.key not in consumed and u.key in self.latest}

    def on_final(self, units) -> tuple[list[str], list[str]]:
        """The executed verdicts of ``units`` (with their runs' closing events), and the units with none on the latest
        output of the unit each verifies."""
        evidence, missing = [], []
        for unit in units:
            done = [v for v in self.verdicts if v["unit_key"] == unit.key and v["executed"]]
            if not any(v["artifact_id"] == self.latest.get(unit.verifies) for v in done):
                missing.append(unit.key)
            evidence += [i for v in done for i in (v["verdict_id"], self.closed.get(v["run_id"], {}).get("evidence_id"))
                         if i]
        return evidence, missing


def _ids(events: list[dict]) -> list[str]:
    return [e["evidence_id"] for e in events]


def _classify(seen: _Seen) -> tuple:
    if seen.cls == AD_HOC:
        return False, [], WHY["unclassified"]
    return True, _ids(seen.ev("created") + seen.ev("preflight")), WHY["classified"].format(cls=seen.cls)


def _standard(seen: _Seen) -> tuple:
    frozen = [e for e in seen.ev("statuses") if e["detail"].get("acceptance_sha256")]
    if not frozen:
        return False, [], WHY["unfrozen"]
    sha = frozen[-1]["detail"]["acceptance_sha256"]
    confirmed = [e for e in seen.ev("interventions") if e["detail"].get("kind") == "clarification"
                 and e["detail"].get("outcome") == "confirmed"
                 and (e["detail"].get("metadata") or {}).get("acceptance_sha256") == sha]
    return True, _ids(frozen + confirmed), WHY["confirmed" if confirmed else "frozen"]


def _retrieve(seen: _Seen) -> tuple:
    looked = seen.refs("learning_selection")
    if not looked:
        return False, [], WHY["not_looked"]
    selected = looked[-1]["detail"]["refs"]["learning_selection"] or []
    if not selected:
        return True, _ids(looked), WHY["none_learned"]
    evidence, unrun = _ids(looked), 0
    for guard in selected:  # each retrieved check counts only once it ran: its receipt, never its selection
        ran = [(e, m) for e in seen.ev("closed") for m in e["detail"].get("verification") or []
               if m.get("executed") and (m.get("probe") or {}).get("guard_id") == guard.get("guard_id")]
        if not ran:
            unrun += 1
        evidence += [i for e, _ in ran for i in (e["evidence_id"], *(v["verdict_id"] for v in seen.verdicts
                                                                   if v["run_id"] == e["detail"].get("run_id")))]
    if unrun:
        return False, evidence, WHY["retrieved_unrun"].format(n=len(selected), k=unrun)
    return True, evidence, WHY["retrieved"].format(n=len(selected))


def _failure_surfaces(seen: _Seen) -> tuple:
    # lazy: objective imports the modules that read this one
    from g6.workforce.objective import QUESTIONS

    named = [e for e in seen.ev("preflight") if any(a.get("question") == QUESTIONS[3] and a.get("answer")
                                                    for a in e["detail"].get("answers") or [])]
    return (True, _ids(named), WHY["surfaces"]) if named else (False, [], WHY["no_surfaces"])


def _select(seen: _Seen) -> tuple:
    decided = [e for e in seen.ev("schedule") if e["detail"].get("decisions")]
    if not decided:
        return False, [], WHY["unselected"]
    return True, _ids(decided), WHY["selected"].format(n=sum(len(e["detail"]["decisions"]) for e in decided))


def _plan_stage(seen: _Seen) -> tuple:
    if not seen.plans:
        return False, [], WHY["unplanned"]
    return True, [p["evidence_id"] for p in seen.plans if p.get("evidence_id")], WHY["planned"].format(n=len(seen.plans))


def _execute(seen: _Seen) -> tuple:
    if seen.plan is None:
        return False, [], WHY["no_plan"]
    doers = [u.key for u in seen.plan.units if u.kind == "do"]
    made = [r for r in seen.runs if r["unit_key"] in doers and r["state"] == "closed" and r["artifact_id"]]
    missing = [key for key in doers if key not in {r["unit_key"] for r in made}]
    if not doers:
        return False, [], WHY["no_step"]
    if missing:
        return False, [r["run_id"] for r in made], WHY["unexecuted"].format(keys=", ".join(missing))
    return True, [r["run_id"] for r in made], WHY["executed"]


def _observe(seen: _Seen) -> tuple:
    kept = seen.ev("artifacts")
    if not kept:
        return False, [], WHY["unobserved"]
    ended = [seen.closed[e["detail"].get("run_id")] for e in kept if e["detail"].get("run_id") in seen.closed]
    return True, _ids(kept + ended), WHY["observed"].format(n=len(kept))


def _verify_progress(seen: _Seen) -> tuple:
    """Review I9 (lead ruling). A code standard: the reported failure reproduced on the frozen baseline AND a check that
    executed on a proposed change. Otherwise: checks that executed on an output before the final one; not applicable
    when every step produced only its final output (its one check is the final check, never counted twice)."""
    outputs = {e["evidence_id"] for e in seen.ev("artifacts")}
    if seen.standard.get("required_tests"):  # a code standard: progress is measured from the reproduced failure
        found = seen.refs("reproduction")
        reproduction = found[-1]["detail"]["refs"]["reproduction"] if found else {}
        receipts = reproduction.get("receipts") or []
        if not (reproduction.get("reproduced") and receipts and all(r.get("executed") for r in receipts)):
            return False, _ids(found), WHY["unreproduced"]
        judged = [v["verdict_id"] for v in seen.verdicts if v["executed"] and v["artifact_id"] in outputs]
        if not judged:
            return False, _ids(found), WHY["unjudged_proposal"]
        return True, _ids(found) + judged, WHY["reproduced"]
    earlier = outputs - seen.finals()
    judged = [v for v in seen.verdicts if v["executed"] and v["artifact_id"] in earlier]
    if judged:
        return True, [v["verdict_id"] for v in judged], WHY["judged"].format(n=len({v["artifact_id"] for v in judged}))
    return (NOT_APPLICABLE, [], WHY["final_only"]) if not earlier else (False, [], WHY["unjudged"])


def _reconfigure(seen: _Seen) -> tuple:
    failed = [v for v in seen.verdicts if v["executed"] and v["result"] == "fail"]
    broken = [r for r in seen.runs if r["failure_code"]]
    decided = seen.ev("reconfiguration")
    if not (failed or broken or decided):
        return NOT_APPLICABLE, [], WHY["nothing_failed"]
    changed = (decided + seen.ev("escalation") + seen.ev("retries")
               + [e for e in seen.ev("messages") if "run_id" in (e["detail"].get("refs") or {})])
    if not changed:  # review I10: a check that could not run is never told as a failure
        unrun = not failed and all(r["failure_code"] == UNRUN for r in broken)
        return False, [], WHY["unrun" if unrun else "unchanged"]
    # review I7: a route re-chosen for its cost is a change of course, not one after a failure
    repriced = not (failed or broken) and all(d["detail"].get("trigger") == "cost_changed" for d in decided)
    return True, _ids(changed), WHY["repriced" if repriced else "changed"]


def _checks(seen: _Seen, *, elicit: bool) -> tuple:
    if seen.plan is None:
        return False, [], WHY["no_plan"]
    units = [u for u in seen.plan.units if u.kind == "verify" and elicits(u) == elicit]
    if not units:
        return False, [], WHY["no_search" if elicit else "no_check"]
    evidence, missing = seen.on_final(units)
    if missing:
        return False, evidence, WHY["unsearched" if elicit else "unchecked"].format(keys=", ".join(missing))
    return True, evidence, WHY["searched" if elicit else "checked"]


def _adjudicate(seen: _Seen) -> tuple:
    """Review M1: G6 settled the result when the mission closed done, or when G6's own code stopped it; a cancel by the
    owner's chat or a person is not G6's adjudication. Only adjudication decisions are cited, never a gate's approval."""
    if not seen.ev("finished"):
        return False, [], WHY["unsettled"]
    closed = seen.ev("finished")[-1]["detail"]
    if closed.get("status") != "done" and closed.get("actor") != "code":
        # COPY: who cancelled, when the record names no actor G6 knows
        return False, [], WHY["cancelled"].format(who=CANCELLED_BY.get(closed.get("actor"), "Someone other than G6"))
    state = (seen.mission.get("outcome") or {}).get("state") or closed.get("state", "")
    adjudications = [e for e in seen.ev("decided") if e["detail"].get("kind") == "adjudication"]
    return True, _ids(seen.ev("finished") + adjudications), WHY["settled"].format(state=state)


def _value(seen: _Seen) -> tuple:
    if not seen.costs:
        return False, [], WHY["unvalued"]
    found = economics.total(seen.costs)
    return True, [c.id for c in seen.costs], WHY["valued"].format(
        unknown=found.unknown_entries + found.other_currency_entries, n=found.entries)


def _learn(seen: _Seen) -> tuple:
    """Review I8 (lead ruling): §10's stage is the learning CANDIDATE. It ran when G6 recorded one for this mission (its
    episode rows, matched on the mission and the coding-learning kind); a learning round whose replay executed is more
    evidence, and the why says whether it promoted a check. A new failure caught with no candidate recorded is named."""
    caught = [(e, m) for e in seen.ev("closed") for m in e["detail"].get("verification") or []
              if m.get("executed") and m.get("result") == "fail" and (m.get("probe") or {}).get("counterexamples")
              and not (m.get("probe") or {}).get("guard_id")]  # a learned check firing is reuse, not a new failure
    candidates = [e for e in seen.ev("episodes") if e.get("task_kind") == LEARNING_KIND
                  and e.get("task_id") == seen.mission.get("mission_id")]
    if not (caught or candidates):
        return NOT_APPLICABLE, [], WHY["nothing_new"]
    if not candidates:
        return False, _ids([e for e, _ in caught]), WHY["no_candidate"].format(
            checks=", ".join(sorted({m.get("check", "") for _, m in caught})))
    rounds = [e for e in seen.refs("learning_decision") if _replayed(e["detail"]["refs"]["learning_decision"])]
    approvals = seen.refs("learned_probe_promoted")
    evidence = [c["episode_id"] for c in candidates] + _ids([e for e, _ in caught] + rounds + approvals)
    if not rounds:
        return True, evidence, WHY["candidate"]
    promoted = bool(approvals) or any(entry.get("verdict") == "promoted" for e in rounds for entry in (
        (e["detail"]["refs"]["learning_decision"].get("promotion_decision") or {}).get("guards") or {}).values())
    return True, evidence, WHY["candidate_promoted" if promoted else "candidate_kept"]


def _replayed(decision: dict) -> bool:
    """A LearningDecision whose replay executed: each receipt of its fit's replay ran (Brief 3's ``learn``).

    R4a records the receipts whole under ``credit.receipts``, the fit's first (``spending.fit_replay.executions`` of
    them), and ``credit.replay`` as executed ids by guard; a decision from before R4a lists the receipts under
    ``credit.replay``. A decision that does not say how many receipts its fit made is no evidence (it fails closed)."""
    credit = decision.get("credit") or {}
    if isinstance(credit.get("replay"), dict):
        fit = ((decision.get("spending") or {}).get("fit_replay") or {}).get("executions")
        receipts = (credit.get("receipts") or [])[:fit] if isinstance(fit, int) else []
    else:
        receipts = credit.get("replay") or []
    return bool(receipts) and all(isinstance(r, dict) and r.get("executed") for r in receipts)


_RULES = {"classify": _classify, "standard": _standard, "retrieve": _retrieve, "failure_surfaces": _failure_surfaces,
          "select": _select, "plan": _plan_stage, "execute": _execute, "observe": _observe,
          "verify_progress": _verify_progress, "reconfigure": _reconfigure,
          "elicit": lambda seen: _checks(seen, elicit=True), "final_verify": lambda seen: _checks(seen, elicit=False),
          "adjudicate": _adjudicate, "value": _value, "learn": _learn}


# -- the environment signature ------------------------------------------------------------------------------------------
def _environment(rows: dict, created: dict) -> dict:
    build = str(created.get("build") or "")
    contracts = {f"{c.get('component')}.{c.get('op')}": c["contract_sha"] for r in rows["runs"] for c in _calls(r)
                 if c.get("contract_sha")}
    policy = str(rows["schedule"][-1]["detail"].get("policy_version") or "") if rows["schedule"] else ""
    providers = sorted({e.provider for e in rows["costs"] if e.provider})
    resources = sorted({e.resource_name for e in rows["costs"] if e.resource_name})
    return {"g6_build": {"value": build} if build else _absent(
                "no build was recorded when the mission was created (made before E9a, or G6 could not tell)"),
            "contracts": {"value": contracts} if contracts else _absent("no call recorded the contract it was checked "
                                                                        "against"),
            "scheduler_policy": {"value": policy} if policy else _absent("no scheduler decision was recorded"),
            "providers": {"value": providers} if providers else _absent("no cost row names a provider"),
            "resources": {"value": resources} if resources else _absent("no cost row names a resource")}


@functools.lru_cache(maxsize=1)
def g6_build() -> str:
    """The G6 build this process runs, read once: ``git:<commit>`` from a checkout, else ``version:<G6_VERSION>``,
    else "" (unknown, never guessed)."""
    return _build(Path(__file__).resolve().parent, os.environ.get("G6_VERSION", ""))


def _build(start: Path, version: str) -> str:
    sha = _commit(start)
    if sha:
        return f"git:{sha}"
    version = str(version or "").strip()
    return f"version:{version}" if version else ""


def _commit(start: Path) -> str:
    """The commit checked out in the nearest git work tree above ``start`` (a worktree's ``.git`` file is followed to
    its common dir), read from the files, or ""."""
    for folder in (start, *start.parents):
        marker = folder / ".git"
        try:
            if marker.is_file():
                text = marker.read_text(encoding="utf-8").strip()
                if not text.startswith("gitdir:"):
                    return ""
                gitdir = (folder / text.removeprefix("gitdir:").strip()).resolve()
            elif marker.is_dir():
                gitdir = marker
            else:
                continue
            head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref:"):
                return head if _SHA.match(head) else ""
            ref = head.removeprefix("ref:").strip()
            common = gitdir
            if (gitdir / "commondir").is_file():
                common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
            return _resolve(ref, dict.fromkeys((gitdir, common)))
        except OSError:
            return ""
    return ""


def _resolve(ref: str, bases) -> str:
    """A ref's commit, loose or packed, in the first of ``bases`` that holds it."""
    for base in bases:
        loose = base / ref
        if loose.is_file():
            sha = loose.read_text(encoding="utf-8").strip()
            return sha if _SHA.match(sha) else ""
        packed = base / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                sha, _, name = line.partition(" ")
                if name.strip() == ref and _SHA.match(sha):
                    return sha
    return ""


__all__ = ["LEARNING_KIND", "NOT_APPLICABLE", "P0", "SKIPPED", "SOURCES", "STAGES", "UNRUN", "assemble", "elicits",
           "g6_build", "gather", "missing", "stages", "trace"]
