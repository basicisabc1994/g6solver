# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Change course inside the frozen standard (E2E Build 6; founder brief §15, §26; design §2.3, §3 C, D and I).

A reconfiguration changes how a stage is done: its route (a tool that failed its re-probe, a cost that changed) or the
repair strategy (a proposal repeated, or no progress). It never changes the outer contract: what counts as done, the
tests, the permissions, the proposal limit and the authority floor are digested before and after, and a decision whose
two digests differ is refused. Each one is a ``reconfiguration`` mission event; there is no other store.

``on_settle`` is the runtime's one hook (``Runtime._settle``, coding missions only). A check stage has one census-read
route today (design R-2), so at a check the only change of course is to a person, never to a weaker check.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from g6.workforce import classes
from g6.workforce.contracts import AUTHORITY, BLOCKED, VERIFIER_GRADE

POLICY_VERSION = "reconfigure-v1"
TRIGGERS = ("tool_unavailable", "cost_changed", "repeated_failure", "no_progress", "attempt_limit")
# COPY: Why a change of course that would change the outer contract is refused.
REFUSED = ("A change of course may not change what counts as done, the tests, the permissions, the baseline, the "
           "proposal limit or the authority floor, so this one was refused.")
# COPY: The person's step when a change of course was refused (it fails closed).
REFUSED_STOP = "Human action required: " + REFUSED + " Check what changed, then start the objective again."
# COPY: The worker's feedback when the test process did not start for its proposal while the runner itself starts.
CANDIDATE_STOPPED = ("The test process did not start for this proposal, while the test runner itself starts, so G6 "
                     "counts it as this proposal's failure: check what the change does when it is imported.")
# COPY: The person's step when a check's only tool cannot run; existing words "Human action required" lead.
NO_CHECK = ("Human action required: the tool this step needs did not run here, even when G6 checked it again, and no "
            "other route of the same standing is available, so G6 did not use a weaker one. Make the tool available, "
            "then start the objective again.")


@dataclass(frozen=True)
class ReconfigurationDecision:
    """Founder brief §26: what was observed, why the strategy became inadequate, what changed, what stayed fixed."""

    mission_id: str
    stage: str
    unit: str
    trigger: str
    observation: dict
    inadequate_because: str
    changed: dict
    invariants_before: dict
    invariants_after: dict
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if self.trigger not in TRIGGERS:
            raise ValueError(f"a reconfiguration's trigger is one of {list(TRIGGERS)}, got {self.trigger!r}")
        if set(self.changed) != {"from", "to"}:
            raise ValueError("a reconfiguration says what changed: {'from': ..., 'to': ...}")
        if self.invariants_before != self.invariants_after:
            raise ValueError(REFUSED)

    def as_dict(self) -> dict:
        return asdict(self)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _lower(floor: str, found: str) -> str:
    """The lower of two authority classes (an unknown one is A0, fail closed)."""
    ranked = [c if c in AUTHORITY else "A0" for c in (floor, found)]
    return min(ranked, key=AUTHORITY.index)


def invariants(contract, *, max_attempts: int | None = None, route_authority: str = VERIFIER_GRADE) -> dict:
    """The outer contract as digests, the same for two missions of one task: what counts as done (the acceptance
    contract without its per-mission metadata), the tests, the permissions, the baseline, the proposal limit (the one in
    force when given) and the authority floor a route keeps (``route_authority``: the selected route's class)."""
    body = contract.as_dict()
    standard = body.pop("metadata").get("coding") or {}
    return {"acceptance": _sha(body),
            "tests": _sha({k: standard.get(k) for k in ("required_tests", "test_digests", "test_provenance")}),
            "permissions": _sha(standard.get("permissions")),
            "baseline": _sha({k: standard.get(k) for k in ("baseline_revision", "baseline_digest")}),
            "max_attempts": standard.get("max_attempts") if max_attempts is None else max_attempts,
            "authority_floor": _lower(VERIFIER_GRADE, route_authority)}


def in_force(missions, mission_id: str, *, max_attempts: int | None = None,
             route_authority: str = VERIFIER_GRADE) -> dict:
    """The invariants as they stand now (review I-3): the contract read again, the proposal limit in force (the frozen
    one capped by the mission's resource mode, unless the caller names the plan's), and the selected route's class."""
    from g6.workforce import scheduler

    contract = missions.acceptance(mission_id)
    if max_attempts is None:
        frozen_limit = (contract.metadata.get("coding") or {}).get("max_attempts")
        cap = scheduler.settings(missions.mode(mission_id)).max_attempts_per_work_unit
        max_attempts = frozen_limit if cap is None or frozen_limit is None else min(frozen_limit, cap)
    return invariants(contract, max_attempts=max_attempts, route_authority=route_authority)


def frozen(missions, mission_id: str) -> dict:
    """The invariants recorded when the mission's standard froze (its preflight); computed now for one that has none."""
    found = missions.events(mission_id, kind="preflight")
    if found and "invariants" in found[0]["detail"]:
        return found[0]["detail"]["invariants"]
    return in_force(missions, mission_id)


def record(missions, decision: ReconfigurationDecision, *, idem: str) -> str:
    return missions.record_event(decision.mission_id, "reconfiguration", actor="code", detail=decision.as_dict(),
                                 idem_key=f"reconfiguration:{idem}")


def decide(missions, fields: dict, *, idem: str) -> bool:
    """Record a change of course (True); one that would change the outer contract is refused, recorded as refused and
    never raised (False: the caller stops for a person). Idempotent by ``idem``."""
    try:
        decision = ReconfigurationDecision(**fields)
    except ValueError as exc:
        if str(exc) != REFUSED:
            raise
        missions.record_event(fields["mission_id"], "reconfiguration", actor="code",
                              detail={**fields, "policy_version": POLICY_VERSION, "refused": REFUSED},
                              idem_key=f"reconfiguration:{idem}")
        return False
    record(missions, decision, idem=idem)
    return True


def decided(missions, mission_id: str) -> list[dict]:
    """Every decision, the refused ones included."""
    return [e["detail"] for e in missions.events(mission_id, kind="reconfiguration")]


def changes(missions, mission_id: str) -> list[dict]:
    """The decisions that changed course (never a refused one)."""
    return [d for d in decided(missions, mission_id) if not d.get("refused")]


def unavailable(missions, mission_id: str) -> set[tuple[str, str]]:
    """The routes this mission found unavailable (a failed re-probe), as (component, op)."""
    return {tuple(d["changed"]["from"].split(".", 1)) for d in changes(missions, mission_id)
            if d["trigger"] == "tool_unavailable"}


def strategy(missions, mission_id: str, ladder) -> str:
    """The repair strategy in force: the last change's, else the ladder's first."""
    found = [d for d in changes(missions, mission_id) if d["changed"]["from"] in ladder]
    return found[-1]["changed"]["to"] if found else ladder[0]


def no_check(missions, mission_id: str, units, *, route: tuple[str, str], observation: dict, why: str) -> str:
    """Each check stage in ``units`` whose only route is unavailable: a "no eligible alternative" decision; the words
    for the person (the refusal's, when one was refused)."""
    before, after = frozen(missions, mission_id), in_force(missions, mission_id)
    kept = [decide(missions, {"mission_id": mission_id, "stage": key, "unit": key, "trigger": "tool_unavailable",
                              "observation": observation, "inadequate_because": why,
                              "changed": {"from": ".".join(route), "to": None}, "invariants_before": before,
                              "invariants_after": after}, idem=f"{key}:tool_unavailable") for key in units]
    return NO_CHECK if all(kept) else REFUSED_STOP


def reroute(wf, mission_id: str, stage: str, *, failed: str, standard: dict, observation: dict) -> tuple[str, str]:
    """A route of ``stage`` did not run: re-probe it once. ("", "") when it answers its probe (not a change of course);
    else the stage's other ready route that meets the floor (one with no fresh pass is probed once first), as (op, ""),
    with its decision; or ("", why) for a person when none qualifies or the change was refused."""
    from g6.workforce import objective, scheduler
    from g6.workforce.calls import prober

    door = prober(wf.caps, invoke=wf.invoker).reply
    found = wf.caps.probe("workforce", failed, door, actor="code")
    if found.at_least("probe_passed"):
        return "", ""
    for route in objective.STAGES["code_change"][stage]:  # review M-5: never exclude a route that was not tried
        if route["op"] != failed and not wf.caps.readiness(route["component"], route["op"]).at_least("probe_passed"):
            wf.caps.probe(route["component"], route["op"], door, actor="code")
    seen = {"execution_id": observation.get("execution_id", ""), "test": observation.get("test", ""),
            "returncode": observation.get("returncode"), "probe_id": found.probe_id}
    fields = {"mission_id": mission_id, "stage": stage, "unit": stage, "trigger": "tool_unavailable",
              "inadequate_because": found.reason, "invariants_before": frozen(wf.missions, mission_id)}
    options = [o for o in objective.route_options(wf, stage, standard=standard)
               if o.required_capabilities[0][1] != failed]
    try:  # coverage ledger R2: the replacement must mitigate what the stage requires, as the first choice did
        choice = scheduler.choose(options, requires=objective.REQUIRES["code_change"].get(stage, ()))
    except scheduler.NoRoute:
        kept = decide(wf.missions, {**fields, "observation": seen, "changed": {"from": f"workforce.{failed}", "to": None},
                                    "invariants_after": in_force(wf.missions, mission_id)}, idem=f"{stage}:{failed}")
        return "", NO_CHECK if kept else REFUSED_STOP
    component, op = choice.selected.required_capabilities[0]
    kept = decide(wf.missions, {
        **fields, "observation": {**seen, "chosen_by": choice.by, "rejected": [list(r) for r in choice.rejected]},
        "changed": {"from": f"workforce.{failed}", "to": f"{component}.{op}"},
        "invariants_after": in_force(wf.missions, mission_id, route_authority=wf.caps.authority(component, op))},
        idem=f"{stage}:{failed}")
    return (op, "") if kept else ("", REFUSED_STOP)


def on_settle(runtime, mission_id: str, spec, row: dict, lease=None) -> tuple[str, str] | None:
    """The one runtime hook: the move a change of course makes instead of the usual one ("fail" or "escalate", and
    why), or None. A proposal that repeats an earlier one is never judged again, nor is one a patch review held back;
    a check whose tool answered blocked and then fails its re-probe stops for a person, with every other open check on
    that tool. A re-settle reuses the
    decision already recorded for the unit (review M-12); after the probe the lease is checked again, so a runner that
    lost it moves nothing (review I-5)."""
    from g6.workforce import coding

    if spec.kind == "do":
        return (coding.repeated(runtime._missions, runtime._ledger, mission_id, spec, row)
                or coding.held(runtime._missions, runtime._ledger, mission_id, spec, row))
    if spec.kind != "verify" or runtime._passed(mission_id, spec, row) or classes.retried_by_runtime(
            row["failure_code"]):
        return None
    calls = [c for c in json.loads(row["calls_json"] or "[]") if isinstance(c, dict)]
    unstarted = _never_started(runtime._missions, mission_id, spec, row)  # review M-6: whichever check settles first
    if not unstarted and (not calls or calls[-1].get("state") != BLOCKED or calls[-1].get("refused")):
        return None  # the tool answered (or the door refused before sending): not a tool that is unavailable
    known = [d for d in decided(runtime._missions, mission_id)
             if d["stage"] == spec.key and d["trigger"] == "tool_unavailable"]
    if known:
        return "escalate", REFUSED_STOP if known[-1].get("refused") else NO_CHECK
    from g6.workforce.calls import prober

    route = tuple(spec.oracle["capability"])
    found = runtime._caps.probe(*route, prober(runtime._caps, invoke=runtime._invoke).reply, actor="code")
    runtime._missions.check_lease(lease)
    plan = runtime._plan(runtime._missions.get(mission_id))
    if found.at_least("probe_passed") and unstarted:  # the runner starts: the search's own record stands, as before
        return None
    if found.at_least("probe_passed"):  # lead ruling I-2: the runner starts, so the candidate stopped it: the worker
        producer = plan.unit(spec.verifies)  # is told, as any failure is, while its attempts last
        if runtime._tries(mission_id, producer) < producer.max_attempts:
            runtime._missions.record_event(mission_id, "message", actor="code", detail={
                "from": spec.key, "to": producer.key, "text": CANDIDATE_STOPPED, "refs": {"run_id": row["run_id"]}},
                idem_key=f"message:{row['run_id']}")
            runtime._graph.reopen(mission_id, producer.key, CANDIDATE_STOPPED)
        return "fail", CANDIDATE_STOPPED
    others = [key for key in runtime._graph.claimable(mission_id)
              if key != spec.key and plan.unit(key).kind == "verify" and route in plan.unit(key).capabilities]
    why = no_check(runtime._missions, mission_id, [spec.key, *others], route=route,
                   observation={"run_id": row["run_id"], "probe_id": found.probe_id}, why=found.reason)
    for key in others:  # never dispatched to a tool known to be unavailable
        runtime._graph.claim(mission_id, key, agent="code")
        runtime._graph.escalate(mission_id, key, why, agent="code")
    runtime._missions.record_unmet(mission_id, kind="not_available", subject=".".join(route), why=found.reason,
                                   unit=spec.key)
    return "escalate", why


def _never_started(missions, mission_id: str, spec, row: dict) -> bool:
    """Review M-6: a search for hidden failures (``regression`` in its oracle's params) that answered "not run" with a
    receipt whose runner never started. The bench answers such a search ``ok`` (nothing BLOCKED), so without this the
    missing runner is seen only when the check-patch run settles first."""
    if "regression" not in ((spec.oracle or {}).get("params") or {}):
        return False
    for event in missions.events(mission_id, kind="run.closed"):
        if event["detail"].get("run_id") != row["run_id"]:
            continue
        for record in event["detail"].get("verification") or []:
            probe = record.get("probe") or {}
            if probe.get("status") == "not_run" and any(
                    function.get(side, {}).get("started") is False
                    for function in probe.get("functions") or [] for side in ("baseline", "candidate")):
                return True
    return False


__all__ = ["CANDIDATE_STOPPED", "NO_CHECK", "POLICY_VERSION", "REFUSED", "REFUSED_STOP", "TRIGGERS",
           "ReconfigurationDecision", "changes", "decide", "decided", "frozen", "in_force", "invariants", "no_check",
           "on_settle", "record", "reroute", "strategy", "unavailable"]
