# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Compile supported objectives using the workforce's existing stores and scheduler.

Library cut: only the ``code_change`` class is compiled (the archive's ``format_json`` class and local plan generation
are not ported; a mission without a repository is one the caller plans itself). The preflight's learning answers (learned guards, earlier lessons, imported lessons, and the routes'
derived operational history) are deferred with the learning layer: they are recorded as empty, never invented.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from datetime import UTC, datetime
from fractions import Fraction

from g6.core.model_calls import counting
from g6.workforce import (
    acceptance,
    bench,
    coding,
    reconfigure,
    repo_tests,
    scheduler,
    truth,
)
from g6.workforce.calls import prober
from g6.workforce.economics import BUDGET_CURRENCY, Money

POLICY_VERSION = "objective-v1"
#: What each route mitigates is its contract's claim now (coverage ledger R2: ``operational`` in its ParamContract).
STAGES = {"code_change": {
    "reproduce": (
        {"component": "workforce", "op": "run_tests", "floor": "A4"},
        {"component": "workforce", "op": "run_tests_in_place", "floor": "A4"}),
}}
#: Coverage ledger R2: the failure modes each stage's route must mitigate (``scheduler.choose(requires=)``).
REQUIRES = {"code_change": {"reproduce": ("unreproduced_failure",)}}
# COPY: What the preflight says while the learning layer is not part of the library (no guard is selected).
NO_LEARNING_LAYER = "none: the learning layer is not part of the g6 library ({signature})"
#: Coverage ledger R2: a route's derived operational fields when no observed history is read (the archive's
#: ``operational.views`` reads closed missions through the credit and trace layers, deferred with learning).
NOT_ESTIMATED = "not estimated"
#: The classes whose baseline is frozen before work begins: a route that writes anything but its scratch is excluded.
FROZEN_BASELINE = ("code_change",)
# COPY: The preflight explains the class's known failure surfaces.
FAILURE_SURFACES = {"code_change": [
    {"id": "unreproduced_failure", "reason": "The reported failure may not reproduce on the frozen baseline."},
    # COPY: Caller tests do not supply independent evidence.
    {"id": "caller_oracle", "reason": "Caller-authored tests cannot independently establish correctness."},
    # COPY: The regression check addresses unintended changes beyond the named function.
    {"id": "outside_scope", "reason": "A proposal may change behavior outside the intended function."},
    # COPY: Coverage ledger R2: what the copy route introduces (its contract names it; bench.py's residual).
    {"id": "runner_in_process", "reason": ("The code under test runs inside the test process with the owner's "
                                           "permissions; this is not an operating-system sandbox.")},
    # COPY: Coverage ledger R2: what the in-place route introduces (its contract names it).
    {"id": "live_working_tree", "reason": ("The tests run in the repository itself, so a change made there during "
                                           "the run can reach them; G6 runs them there only when the repository is "
                                           "clean at the frozen revision.")},
]}
# COPY: The seven questions in the mission preflight.
QUESTIONS = ("What is this", "What counts as success", "What standard", "What usually goes wrong",
             "What G6 learned before", "What machinery is available", "Cheapest trustworthy route")
# COPY: Missing prior learning is an explicit answer tied to the task signature.
NO_LEARNING = "none for {signature}"
# COPY: Route exclusion reasons, kept in scheduler evidence.
BELOW_FLOOR = "This route has no census reading at the required authority."
# COPY: In-place reproduction cannot use a moving or changed repository.
IN_PLACE_UNSAFE = "In-place reproduction needs a clean repository at the frozen revision with matching tests."
# COPY: Coverage ledger R2: a route whose contract says it writes more than its own scratch space is not used on a
# frozen baseline.
STATE_WRITES = "This route's contract says it writes more than its own scratch space, which a frozen baseline forbids."



def classify(objective: str, repository: dict | None) -> str | None:
    return "code_change" if repository is not None else None


def compile(wf, *, objective: str, repository: dict | None, declared_risk: str, budget_cents: int,
            materials: dict | None = None) -> dict:
    """A coding mission when a repository is named; otherwise a mission the caller plans itself (``service.create``;
    the archive's local plan generation and ``format_json`` class are not ported)."""
    from g6.workforce import service

    if materials is not None and repository is not None:
        raise ValueError("Use repo_json for coding or materials_json for general planning, not both.")
    if classify(objective, repository) == "code_change":
        return coding.start(wf, objective=objective, repository=repository,
                            declared_risk=declared_risk, budget_cents=budget_cents)
    return service.create(wf, objective=objective, risk_tier=declared_risk, budget_cents=budget_cents,
                          materials=materials)


def in_place_eligible(wf, standard: dict) -> bool:
    try:
        source = bench._repo(standard["repository"], wf.venture_id)
        return (not repo_tests._git(source, "status", "--porcelain")
                and repo_tests._git(source, "rev-parse", "HEAD").decode().strip() == standard["baseline_revision"]
                and all(hashlib.sha256((source / rel).read_bytes()).hexdigest() == digest
                        for rel, digest in standard["test_digests"].items()))
    except (OSError, ValueError):
        return False


def route_options(wf, stage: str, *, standard: dict, cls: str = "code_change",
                  history: dict | None = None, observed: dict | None = None) -> list[scheduler.ExecutionOption]:
    """Each route of ``stage`` as an option: its declared cost (None when its contract declares none), and its measured
    chance of executing from this owner's earlier probes (``history``: (passed, all) per route, read before this
    decision probed anything; None when it never ran, never made up).

    Coverage ledger R2: what the route mitigates and introduces is its contract's ``operational`` claim for its default
    mode (lead ruling L-2); a route that writes more than its scratch is excluded on a frozen baseline (L-8). With
    ``observed`` (``operational.views``), its derived latency is shown (never ranked) and its derived cost replaces the
    declared one only when every observed amount is known (F-1: today none is)."""
    options = []
    for route in STAGES[cls][stage]:
        component, op = route["component"], route["op"]
        contract = wf.caps.contract(component, op)
        claims = (contract.operational if contract is not None else None) or {}
        passed, total = (history or {}).get((component, op)) or wf.caps.history(component, op)
        ready = wf.caps.readiness(component, op)
        meets_floor = wf.caps.authority(component, op) >= route["floor"]
        reason = "" if meets_floor else BELOW_FLOOR
        if not ready.at_least("probe_passed"):
            meets_floor, reason = False, ready.reason
        if op == bench.IN_PLACE and not in_place_eligible(wf, standard):
            meets_floor, reason = False, IN_PLACE_UNSAFE
        if cls in FROZEN_BASELINE and not set(claims.get("state_writes") or ()) <= {"scratch"}:
            meets_floor, reason = False, STATE_WRITES
        option = scheduler.ExecutionOption(
            id=f"{stage}:{op}", work_unit_id=stage, strategy_type="deterministic", resource_tier="T1_DETERMINISTIC",
            required_capabilities=((component, op),), mechanism="test_execution",
            risk_coverage=tuple(c["failure_mode"] for c in claims.get("mitigates_failure_modes") or ()
                                if c["when"] == ""),
            degradation_implications=tuple(claims.get("introduces_failure_modes") or ()),
            estimated_cost=contract.cost_ceiling if contract is not None else None,
            acceptance_probability=Fraction(passed, total) if passed else None, probability_n=total,
            meets_floor=meets_floor, rationale=reason)
        seen = (observed or {}).get((component, op))
        if seen is not None:
            derived = seen["cost"]["minor_units"]
            option = replace(option, estimated_latency=seen["latency"]["text"], estimated_cost=(
                option.estimated_cost if derived is None else Money(derived, seen["cost"]["currency"])))
        options.append(option)
    return options


def _replanned(wf, mission_id: str, stage: str, contract, choice, options) -> dict:
    """Test D (cross-run): the earlier mission of this task chose another route for ``stage`` and a cost changed since,
    with the standard unchanged. The SchedulerDecision's metadata, after a ReconfigurationDecision is recorded; {} when
    there is no such earlier choice, or this one was not ranked by measured cost and chance."""
    if choice.by != "expected_cost_per_accepted_outcome":
        return {}
    signature = contract.metadata["coding"]["task_signature"]["sha256"]
    earlier = [m for m in wf.missions.earlier(wf.venture_id, template="code_change", task_sha256=signature)
               if m != mission_id and wf.missions.events(m, kind="preflight")]
    if not earlier:
        return {}
    previous = wf.missions.events(earlier[-1], kind="preflight")[0]["detail"]["answers"][6]["answer"].get(stage)
    if not previous or previous["selected_option"]["id"] == choice.selected.id:
        return {}
    before = {o["id"]: o["estimated_cost"] for o in previous["candidate_options"]}
    after = {o.id: o.estimated_cost.as_dict() if o.estimated_cost is not None else None for o in options}
    old = reconfigure.frozen(wf.missions, earlier[-1])  # as the earlier mission froze it
    component, op = choice.selected.required_capabilities[0]
    new = reconfigure.in_force(wf.missions, mission_id, route_authority=wf.caps.authority(component, op))
    if before == after or old != reconfigure.in_force(wf.missions, mission_id):
        return {}  # nothing cost more or less, or another standard: a fresh choice, not a replan
    if not reconfigure.decide(wf.missions, {
            "mission_id": mission_id, "stage": stage, "unit": stage, "trigger": "cost_changed",
            "observation": {"mission_id": earlier[-1], "before": before, "after": after},
            # COPY: Why the earlier route became inadequate.
            "inadequate_because": f"its declared cost changed, so {previous['selected_option']['id']} no longer has "
                                  "the least expected cost per accepted outcome",
            "changed": {"from": ".".join(previous["selected_option"]["required_capabilities"][0]),
                        "to": f"{component}.{op}"}, "invariants_before": old, "invariants_after": new},
            idem=f"{stage}:cost_changed"):
        return {}  # refused (the selected route is below the floor): recorded, and no replan is claimed
    return {"replanned_from": {"mission_id": earlier[-1], "option": previous["selected_option"]["id"]},
            "because": "cost changed", "before": before, "after": after}


def _operational(wf, component: str, op: str, observed: dict) -> dict:
    """What a decision records of one route's operational fields: the declared block and its digest (L-1), and the
    derived view it was shown with."""
    contract = wf.caps.contract(component, op)
    seen = observed.get((component, op)) or {}
    return {"sha256": contract.operational_sha256 if contract is not None else "",
            "declared": contract.operational if contract is not None else None,
            **{k: seen.get(k) for k in ("task_classes", "history", "failure_classes", "latency", "cost", "composition",
                                        "missions")}}


def _unobserved(pair: tuple[str, str]) -> dict:
    """A route's derived operational fields with no observed history: every amount unknown, never zero."""
    routed = sorted({cls for cls, by_stage in STAGES.items() for routes in by_stage.values()
                     for route in routes if (route["component"], route["op"]) == pair})
    stages = sorted({stage for by_stage in STAGES.values() for stage, routes in by_stage.items()
                     for route in routes if (route["component"], route["op"]) == pair})
    return {"op": ".".join(pair), "task_classes": routed,
            "history": {"n": 0, "answered": 0, "executed": 0, "sources": []}, "failure_classes": {},
            "latency": {"n": 0, "mean_seconds": None, "resolution_seconds": 1, "text": NOT_ESTIMATED},
            "cost": {"n": 0, "known": 0, "minor_units": None, "currency": BUDGET_CURRENCY},
            "composition": stages, "missions": {"read": 0, "open": [], "limit": 0}}


def _route(decision: dict) -> str:
    return ".".join(decision["selected_option"]["required_capabilities"][0])


def preflight(wf, mission_id: str, cls: str) -> dict:
    existing = wf.missions.events(mission_id, kind="preflight")
    if existing:
        return existing[0]["detail"]
    contract = wf.missions.acceptance(mission_id)
    standard = contract.metadata["coding"]
    selected: list[dict] = []  # no learned guard is selected: the learning layer is deferred
    reason = NO_LEARNING_LAYER.format(signature=NO_LEARNING.format(signature=standard["task_signature"]["sha256"]))
    selection_id = wf.missions.record_event(mission_id, "message", actor="code", detail={
        "from": "code", "to": "learning", "text": reason, "refs": {"learning_selection": selected}},
        idem_key="coding:learning_selection")
    learned: list = []  # no earlier lessons are read: the learning layer is deferred
    pairs = list(dict.fromkeys((r["component"], r["op"]) for routes in STAGES[cls].values() for r in routes))
    readiness, probe_calls = [], []
    door = prober(wf.caps, invoke=wf.invoker)

    def probe_reply(component, op, params):
        truth.LAST_RUN.clear()
        with counting() as model_count:
            reply = door.reply(component, op, params)
        result = reply.get("result", {})
        if op == bench.IN_PLACE:
            executed = (result.get("verdict") in (repo_tests.PASSED, repo_tests.FAILED)
                        and result.get("counts", {}).get("total") == 1)
        else:  # audit 010 WF-003: the runner's record of one test, never the printed tally
            found = truth.LAST_RUN.get("tally") or {}
            executed = (truth.LAST_RUN.get("rc") in (0, 1)
                        and found.get("passed", 0) + found.get("failed", 0) == 1)
        probe_calls.append({"executed": executed, "model_calls": model_count()})
        return reply

    history = {pair: wf.caps.history(*pair) for pair in pairs}  # before this decision probes anything
    for component, op in pairs:
        ready = wf.caps.readiness(component, op)
        if not ready.at_least("probe_passed"):
            ready = wf.caps.probe(component, op, probe_reply, actor="code")
        readiness.append(asdict(ready))
    # Coverage ledger R2: each route's derived fields, from this owner's closed missions (read only; runs nothing)
    observed = {pair: _unobserved(pair) for pair in pairs}
    for row in readiness:
        found_contract = wf.caps.contract(row["component"], row["op"])
        claims = (found_contract.operational if found_contract is not None else None) or {}
        seen = observed[(row["component"], row["op"])]
        row["operational"] = {"state_reads": list(claims.get("state_reads") or []),
                              "state_writes": list(claims.get("state_writes") or []),
                              "task_classes": seen["task_classes"], "history_n": seen["history"]["n"]}
    decisions = {}
    for stage in STAGES[cls]:
        options = route_options(wf, stage, standard=standard, cls=cls, history=history, observed=observed)
        requires = REQUIRES.get(cls, {}).get(stage, ())
        choice = scheduler.choose(options, requires=requires)
        decision = scheduler.SchedulerDecision(
            mission_id=mission_id, work_unit_id=stage, candidate_options=tuple(o.as_dict() for o in options),
            selected_option=choice.selected.as_dict(), reason_codes=("DETERMINISTIC_METHOD_AVAILABLE",),
            budget_before=Money(standard["budget_cents"], BUDGET_CURRENCY).as_dict(), risk_tier=contract.risk_tier,
            acceptance_contract_reference=contract.sha256, timestamp=datetime.now(UTC).isoformat(timespec="seconds"),
            metadata={"stage": stage, "requirement": [r.as_dict() for r in contract.requirements],
                      "evidence": {"probe_ids": [r["probe_id"] for r in readiness if r["probe_id"]]},
                      "chosen_by": choice.by, "rejected": [list(r) for r in choice.rejected],
                      "requires": list(requires),
                      # lead ruling L-1: the claims each route was judged by, with their own digest
                      "operational": {".".join(o.required_capabilities[0]): _operational(
                          wf, *o.required_capabilities[0], observed) for o in options},
                      **_replanned(wf, mission_id, stage, contract, choice, options)})
        decisions[stage] = decision.as_dict()
    values: dict = {}  # R4b: no guard was selected, so there is no guard value to show
    schedule_id = wf.missions.record_event(mission_id, "schedule", actor="code", detail={
        "revision": 0, "policy_version": scheduler.POLICY_VERSION, "decisions": list(decisions.values())},
        idem_key="objective:schedule:1")
    inherited = coding.reuse_reference(wf.missions, mission_id)
    answers = [
        ({"task_class": cls, "components_used": standard["intake"]["components_used"],
          **({"intake_source_mission_id": inherited["construction_mission_id"]} if inherited else {})}, [contract.sha256]),
        ([r.as_dict() for r in contract.requirements], [contract.sha256]),
        ({"derivation": standard["derivation"], "required_state": contract.required_completion_state,
          "verification": list(contract.verification_requirements),
          "approvals": list(contract.human_approval_requirements),
          "confirmation_required": contract.risk_tier in acceptance.CONFIRM_TIERS}, [contract.sha256]),
        ({"failure_surfaces": FAILURE_SURFACES[cls], "lessons": [asdict(row) for row in learned],
          **({"task_specific_hypotheses": standard["harness_intelligence"]["hypotheses"]}
             if standard.get("harness_intelligence") else {}),
          "introduced": {s: d["selected_option"]["degradation_implications"] for s, d in decisions.items()}},
         [f"{POLICY_VERSION}:{cls}", f"episodes:{wf.missions.owner}:{wf.venture_id}:{cls}",
          *(f"operational:{d['metadata']['operational'][_route(d)]['sha256']}" for d in decisions.values())]),
        ({"guards": selected, "reason": reason, "value": values}, [selection_id]),
        (readiness, [r["probe_id"] or f"readiness:{r['component']}.{r['op']}" for r in readiness]),
        (decisions, [schedule_id]),
    ]
    found = {"policy_version": POLICY_VERSION, "task_class": cls,
             "answers": [{"question": question, "answer": answer, "sources": sources}
                         for question, (answer, sources) in zip(QUESTIONS, answers)],
             "toolset_size": {"by_stage": {s: len(r) for s, r in STAGES[cls].items()},
                              "total": sum(len(r) for r in STAGES[cls].values())},
             "cost": {"model_calls": sum(r["model_calls"] for r in probe_calls),
                      "executions": sum(r["executed"] for r in probe_calls) + (0 if inherited else sum(
                 r["executed"] for r in standard.get("scope", {}).get("receipts", []))),
                      # The ids of docs/architecture/store_map.json (tests/contract/test_store_map.py, T7)
                      "stores_read": ["missions", "preflight_runs", "census", "run_ledger"], "money": None},
             # E2E Build 6 review I-3: what a later change of course must keep, as the standard froze it
             "invariants": reconfigure.in_force(wf.missions, mission_id)}
    wf.missions.record_event(mission_id, "preflight", actor="code", detail=found, idem_key="preflight:1")
    return found
