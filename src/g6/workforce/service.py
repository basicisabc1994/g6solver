# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""What a caller (an agent's chat session, or code) calls to run coding missions (workforce OS plan, V1a).

The caller writes the patch; G6's code freezes the standard, validates and runs the checks (founder decision,
2026-10-01). These are plain functions over one bound ``Workforce`` (``g6.workforce.bind`` is the library facade).
The venture and owner come from the binding and nowhere else (no mission function takes either), so a caller
cannot address another owner's missions.
Every change made here is recorded with the actor ``chat``: the model acting for the owner is never
recorded as the human. Only a person decides a gate or a disagreement between verifiers, through the local
command line (``decide.py``, V4c): nothing here decides one.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from g6.workforce import result_view
from g6.intent_engine.schema import IntentContract
from g6.workforce import acceptance, economics, trace, view
from g6.workforce.bench import ENV_DB_DIR, bind_root, workspace_root
from g6.workforce.calls import prober
from g6.workforce.capabilities import Capabilities, ParamContract
from g6.workforce.contracts import Outcome
from g6.workforce.decisions import human_time
from g6.workforce.graph import WorkGraph
from g6.workforce.missions import FINISHED, RUNNING, LeaseHeld, MissionStore
from g6.workforce.plans import PlanRejected, parse_plan
from g6.workforce.runs import RunLedger, outcome_of
from g6.workforce.runtime import Runtime
from g6.workforce.store import VentureStore

ENV_VENTURE, ENV_OWNER = "G6_WORKFORCE_VENTURE_ID", "G6_WORKFORCE_OWNER"
#: Mission certificates (RVC R6; founder ruling R6-P: with this on, a coding mission's certificate decides its state).
#: Founder, 2026-10-15: both are on by default for every plan; only a plain no turns one off (the operator's rollback).
ENV_CERTIFICATES = "G6_WORKFORCE_CERTIFICATES"
ENV_READINESS = "G6_WORKFORCE_READINESS"
_NO = frozenset({"0", "false", "no", "off"})


def certificates_enabled(env: dict | None = None) -> bool:
    """Whether mission certificates decide coding missions' states: on unless the operator said a plain no."""
    env = os.environ if env is None else env
    return str(env.get(ENV_CERTIFICATES) or "").strip().lower() not in _NO


def readiness_enabled(env: dict | None = None) -> bool:
    """Whether the deterministic readiness checkpoint runs: on unless the operator said a plain no."""
    env = os.environ if env is None else env
    return str(env.get(ENV_READINESS) or "").strip().lower() not in _NO


LOCAL_OWNER = "local-operator"
CHAT = "chat"

#: The plan format the chat model drafts (the planning brief's second half; the first is the catalogue).
#: P-7 words approved by the founder on 2026-10-06 (task #161).
PLAN_FORMAT = (
    'A plan is JSON: {"units": [...], "edges": [[from, to], ...]} where `to` consumes `from`. A unit: '
    '{"key": "lowercase-key", "kind": "do" | "verify" | "gate", "goal": "...", "inputs": [unit keys], '
    '"capabilities": [[component, op], ...], "risk": "R0".."R3", "budget_cents": n, "max_attempts": 1..5, '
    '"priority": n}. Every do unit needs a verify unit ({"kind": "verify", "verifies": "<do key>"}); a consumer '
    "waits for that verify unit, never for the producer. A do unit that code can run names a call: "
    '{"call": {"capability": [component, op], "params": {...}, "bind": {"param": "<input key>.<field path>"}}}. '
    "A do unit with no call is yours to write: G6 lists it under awaiting_chat and takes it by mission_submit "
    '(a patch is {"files": {"path": "full new text"}}). '
    "A verify unit names its oracle: the same plus "
    '"expect": {"field": "<path in the answer>", "equals": value}, "kind": "formal" | "deterministic" | "test" '
    '| "oracle", and must bind at least one parameter from the unit it verifies. A formal oracle may also name '
    '"facts": {"<smt var>": "<input key>.<field path>"}: each number read there is asserted as (= var number); '
    "the var must be declared in its params.variables, and a list element is addressed by its index. "
    'A unit may wait: "not_before": "<ISO time with a zone>" holds it until then, and "await_file": "<name>" '
    "holds it until that file is dropped in the mission's inbox (mission_status shows the path); a mission "
    "with nothing else to do waits at no model cost and wakes on the time, the file or your submission. "
    "A unit listed under awaiting_chat may instead be delegated (mission_delegate): its children are units in this "
    "format (a specialist do unit with its verifier, or extra verifiers of the unit), and they never widen it: a "
    "child names only the unit's capabilities (a child verifier also its verifiers'), at most its risk and "
    "attempts, and budgets carved out of its own; at most 6 children and 3 levels deep. The unit then waits for "
    "its children's verified outputs, which its brief carries. "
    "Only capabilities at "
    "probe_passed may be named; params must fit their contract. A verdict is verified only from a separate "
    "read-only oracle whose op is declared model_free. "
    "For verified results, use a separate read-only check that runs without an AI model and has A4 authority. "
    "Exclude A0 operations. External or irreversible actions require at least A2 and the required human approval. "
    "A unit naming an external op must run behind a gate unit (by input or edge); the person approving it sees the "
    "gate's inputs."
)


class WorkforceUnbound(RuntimeError):
    """This process is not bound to a venture (its environment names no db dir or venture)."""


@dataclass(frozen=True)
class Workforce:
    venture_id: str
    missions: MissionStore
    graph: WorkGraph
    ledger: RunLedger
    caps: Capabilities
    runtime: Runtime
    invoker: Any = None
    #: The store this process is bound to (the inbox, the repositories and the service lock live under it).
    db_dir: str = ""
    #: What an hour of the owner's attention costs, when an operator sets it (Ledger §23); None: unpriced.
    hourly_rate: economics.Money | None = None
    #: Whether mission certificates decide coding missions' states (``ENV_CERTIFICATES``).
    certificates: bool = False
    readiness: bool = False


#: The verification authority of the library's own ops, each bound to the digest of the contract it was read for
#: (an op whose shipped contract changed reads A0). Carried over from the archive's census reading of 2026-10-06
#: (``navigator/census_levels.json``, ``workforce-stdio`` source); it is NOT re-measured by the library.
CENSUS = {
    ("workforce", "run_tests"): ("A4", "2cd3668b53b6bd43ce10426bb7995e70627debffb2d471d32b849946a999f125"),
    ("workforce", "run_tests_in_place"): ("A4", "00f1fd0f5c2358c56fe524aedb3d26b7b58ee9721ee2806c08aa9d28a31bd081"),
}


def _census_authority(component: str, op: str) -> str:
    """The op's class from the static census table, for this exact shipped contract; "" (A0) otherwise."""
    from g6.workforce.capabilities import load_contracts

    found = CENSUS.get((component, op))
    contract = load_contracts().get((component, op))
    return found[0] if found and contract is not None and contract.sha256 == found[1] else ""


def bind(env: dict | None = None, *, registry: Any = None, invoke: Any = None,
         contracts: dict[tuple[str, str], ParamContract] | None = None,
         authority: Callable[[str, str], str] | None = None) -> Workforce:
    """The workforce of the venture this process is bound to: ``$G6_WORKFORCE_DB_DIR`` (default
    ``$G6_HOME/workforce``), ``$G6_WORKFORCE_VENTURE_ID`` and ``$G6_WORKFORCE_OWNER`` (default the local operator).
    ``registry``, ``invoke``, ``contracts`` and ``authority`` are for tests (a scripted world); the library itself has
    no component registry, so only the workforce bench's ops can run."""
    env = os.environ if env is None else env
    db_dir = str(env.get(ENV_DB_DIR) or "").strip() or str(workspace_root())
    venture = str(env.get(ENV_VENTURE) or "").strip()
    owner = str(env.get(ENV_OWNER) or "").strip() or LOCAL_OWNER
    if not venture:
        raise WorkforceUnbound(f"the workforce needs {ENV_VENTURE}")
    if not (Path(db_dir) / "venture.db").exists():
        raise WorkforceUnbound(f"no venture db in {db_dir}")
    vstore = VentureStore(Path(db_dir) / "venture.db")
    scope = vstore.for_owner(owner)
    venture_id = scope.resolve(venture)  # LookupError(NOT_FOUND) for another owner's venture
    bind_root(venture_id, db_dir)        # the bench finds this venture's repositories under this root, not the env's
    missions = MissionStore(scope)
    graph = WorkGraph(missions, Path(db_dir) / "workforce" / "units.db")
    ledger = RunLedger(missions)
    caps = Capabilities(scope, contracts=contracts, registry=registry, authority=authority or _census_authority)
    runtime = Runtime(missions, graph, ledger, caps, invoke=invoke, inbox=Path(db_dir) / "inbox",
                      certificates=certificates_enabled(env), readiness=readiness_enabled(env))
    rate = economics.parse_hourly_rate(env.get(economics.HOURLY_RATE_ENV))
    return Workforce(venture_id, missions, graph, ledger, caps, runtime, invoke, db_dir, rate,
                     certificates_enabled(env), readiness_enabled(env))


def capabilities(wf: Workforce) -> dict:
    """The planning brief: every declared capability with its readiness, and the plan format."""
    out = []
    for (component, op), contract in sorted(wf.caps.contracts().items()):
        found = wf.caps.readiness(component, op)
        out.append({"component": component, "op": op, "level": found.level, "reason": found.reason,
                    "required": contract.required, "optional": contract.optional,
                    "side_effect": contract.side_effect, "model_free": contract.model_free,
                    "answer_field": contract.expect_field, "authority": wf.caps.authority(component, op)})
    return {"capabilities": out, "plan_format": PLAN_FORMAT}


def probe(wf: Workforce, component: str, op: str) -> dict:
    """Run one capability's contract probe through the door; returns its readiness."""
    found = wf.caps.probe(component, op, prober(wf.caps, invoke=wf.invoker).reply, actor=CHAT)
    return {"component": component, "op": op, "level": found.level, "reason": found.reason,
            "probed_at": found.probed_at}


def create(wf: Workforce, *, objective: str, risk_tier: str, budget_cents: int,
           materials: dict | None = None) -> dict:
    """A mission the caller plans itself (the runtime's general path; the coding mission is ``objective.compile``)."""
    mission_id = wf.missions.create(wf.venture_id, objective=objective, risk_tier=risk_tier,
                                    budget_cents=budget_cents, actor=CHAT, materials=materials)
    return status(wf, mission_id)


def accept(wf: Workforce, mission_id: str, contract: dict | None = None) -> dict:
    """Fix the clarified contract (if given), compile what counts as done from it (E2c; E7d), and move the mission
    to awaiting its plan. The contract may carry an ``acceptance`` object (``required_completion_state``,
    ``allowed_degradation``, ``max_latency_seconds``). Everything is checked before anything is written. A coding
    mission's clarification (its replay semantics) is answered here too."""
    mission = wf.missions.get(mission_id)
    from g6.workforce import coding

    if coding.clarification_request(wf.missions, mission_id):
        return coding.clarify(wf, mission_id, contract)
    overrides = None
    clarified = None
    if contract is not None:
        contract = dict(contract)
        overrides = contract.pop("acceptance", None)
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("acceptance is a JSON object")
        clarified = IntentContract.model_validate({"goal": mission["objective"], **contract})
    compiled = acceptance.compile_contract(objective=mission["objective"],
                                           contract=clarified or wf.missions.contract(mission_id),
                                           risk_tier=mission["risk_tier"], budget_cents=mission["budget_cents"],
                                           overrides=overrides)
    wf.missions.accept(mission_id, compiled, actor=CHAT, contract=clarified)  # one transaction
    for need in acceptance.cannot_check(compiled):  # E7d: a requirement G6 cannot check is unmet demand
        wf.missions.record_unmet(mission_id, kind="cannot_check", subject=need.name, why=need.problem)
    return status(wf, mission_id)


def plan(wf: Workforce, mission_id: str, drafted: dict | str, *, actor: str = CHAT, origin: str = "") -> dict:
    """Validate the drafted plan against the mission and the broker, store it, materialize it. A plan whose
    materialization was interrupted is finished by submitting that same plan again (any other is refused)."""
    mission = wf.missions.get(mission_id)
    if mission["status"] == "awaiting_plan" and mission["plan_revision"]:
        revision, stored = wf.missions.plan(mission_id, mission["plan_revision"])
        again = parse_plan(wf.missions.normalized(mission_id, drafted, wf.caps.side_effect),  # E10b: as add_plan stored it
                           risk_tier=mission["risk_tier"], budget_cents=mission["budget_cents"],
                           ready=wf.caps.ready, effects=wf.caps.side_effect, contract=wf.caps.contract,
                           authority=wf.caps.authority)
        if again.sha256 != stored.sha256:
            raise ValueError(f"plan revision {revision} is being materialized; submit that same plan to finish it "
                             "(replanning comes with the supervisor)")
    else:
        missing: list[tuple[str, str, str]] = []

        def ready(component: str, op: str) -> str | None:  # E7d: what the plan needs that is not available here
            why = wf.caps.ready(component, op)
            if why is not None:
                missing.append((component, op, why))
            return why

        try:
            wf.missions.add_plan(mission_id, drafted, actor=actor, ready=ready, effects=wf.caps.side_effect,
                                 reusable=wf.runtime.reusable, contract=wf.caps.contract,
                                 authority=wf.caps.authority, origin=origin)
        except PlanRejected:
            for component, op, why in missing:
                wf.missions.record_unmet(mission_id, kind="not_available", subject=f"{component}.{op}", why=why)
            raise
    wf.graph.materialize(mission_id, actor="code")
    shown = review(wf, mission_id)  # recorded first, so the status below reads its schedule
    out = status(wf, mission_id)
    # Design §34 question 10 (E8e Task 9 review): the person reads the timeline; the reason codes are technical and
    # wait for mission_status(detail="full") ("schedule").
    out["review"] = {"timeline": shown["timeline"]}
    return out


def review(wf: Workforce, mission_id: str) -> dict:
    """The stored plan for the person's approval: Design §8's timeline and each step's SchedulerDecision (E8e's first
    cut, extended by E2d), recorded once per plan revision."""
    from g6.workforce import scheduler, timeline

    revision, stored = wf.missions.plan(mission_id)
    decisions = scheduler.decide(mission_id, stored, wf.caps.contract, wf.caps.side_effect,
                                 now=datetime.now(UTC).isoformat(timespec="seconds"),
                                 acceptance_contract=wf.missions.acceptance(mission_id),
                                 mode=wf.missions.mode(mission_id))
    wf.missions.record_event(mission_id, "schedule", actor="code",
                             detail={"revision": revision, "policy_version": scheduler.POLICY_VERSION,
                                     "decisions": [d.as_dict() for d in decisions]},
                             idem_key=f"schedule:{revision}")
    return {"timeline": timeline.plan_timeline(stored, decisions),
            "decisions": [{"step": d.work_unit_id, "reasons": list(d.reason_codes),
                           "how": d.selected_option["strategy_type"]} for d in decisions]}


def run(wf: Workforce, mission_id: str, *, max_steps: int = 50) -> dict:
    """Step the mission now, unless another runner holds it: then nothing is stepped here and the status's
    ``runner`` names who is running it."""
    try:
        wf.runtime.run(mission_id, max_steps=max_steps)
    except LeaseHeld:
        pass
    return status(wf, mission_id)


def delegate(wf: Workforce, mission_id: str, unit_key: str, children: Any) -> dict:
    """Hand part of a unit G6 is waiting for the chat to write to children (V4a); refused with every problem,
    changing nothing, when any child would widen what the unit had."""
    wf.runtime.delegate(mission_id, unit_key, children, actor=CHAT)
    return status(wf, mission_id)


def submit(wf: Workforce, mission_id: str, unit_key: str, artifact: Any) -> dict:
    """What the chat wrote for a unit G6 is waiting for (``status``'s ``awaiting_chat``); a draft until its
    verifier judges it on the next ``run``."""
    from g6.workforce import coding

    standard = coding.frozen(wf, mission_id)
    wf.runtime.submit(mission_id, unit_key, artifact)
    if standard:
        return coding.advance(wf, mission_id)
    return status(wf, mission_id)


def attention(wf: Workforce, mission_id: str) -> dict:
    """The owner's attention on this mission (Ledger §6, §23; the Phase 2 economics record, plan ruling P2.8): the
    interventions by kind, their time when measured, and its cost only with a configured rate; unknown, never 0. Beside
    them, each under its own name (R5, lead ruling H-1): the person's decisions, the minutes they said those took, the
    share that said one, and how long the questions waited (``decisions.human_time``)."""
    found = economics.attention_total(wf.runtime.decisions.interventions(mission_id), hourly_rate=wf.hourly_rate)
    return {**found.as_dict(), "time": "not measured" if found.seconds is None else f"{found.seconds} s",
            "note": "the owner's time in the chat itself is not measured",
            **human_time(wf.missions.events(mission_id, kind="approval.requested"),
                         wf.missions.events(mission_id, kind="approval.decided"))}


def status(wf: Workforce, mission_id: str, *, stages: bool = False) -> dict:
    """The mission as the chat sees it with ``detail="full"`` (``summary`` trims it). ``stages``: also the stage map,
    built only for ``mission_status(detail="full")`` (E2E Build 7 review I2: no summary or tool return builds it)."""
    from g6.workforce import coding
    from g6.workforce import readiness as readiness_view

    mission = wf.missions.get(mission_id)
    try:
        units = wf.graph.units(mission_id)
    except LookupError as exc:
        if str(exc) != "this mission has no plan":
            raise
        units = []
    found = wf.missions.acceptance(mission_id)
    try:
        shown = view.result(wf, mission_id)
    except Exception as exc:  # noqa: BLE001 - a return is never lost to its own summary (fix pass M9)
        shown = result_view.unbuilt(exc, more=view.MORE)
    return {
        **coding.clarification_status(wf, mission_id),
        **({"coding": coding.status(wf, mission_id, found.metadata["coding"], include_pack=stages)}
           if found.metadata.get("coding") else (
               {"coding": {"readiness": asdict(readiness_view.state(wf.missions, mission_id))}}
               if wf.readiness and stages and coding.clarification_request(wf.missions, mission_id) else {})),
        "mission_id": mission["mission_id"], "objective": mission["objective"], "status": mission["status"],
        "completion_state": Outcome.from_record(mission["outcome"]).state if mission["status"] in FINISHED else "",
        "result": shown,  # answer first: the chat reads it before the technical fields
        "outputs": view.outputs(wf, mission_id),  # each result in full, for detail="full" (fix pass I6)
        "risk_tier": mission["risk_tier"],
        "budget_cents": mission["budget_cents"], "plan_revision": mission["plan_revision"],
        "cost_reservations": [e.as_detail() for e in wf.ledger.pending_reservations(mission_id)],
        "outcome": mission["outcome"],
        "units": [{"key": u["key"], "kind": u["kind"], "parent": u["parent"],
                   "status": u["status"],
                   "attempts": wf.ledger.attempts(mission_id, u["key"])} for u in units],
        "tree": _tree(units),
        "runs": [{"unit": r["unit_key"], "attempt": r["attempt"], "state": r["state"],
                  "completion_state": "" if r["state"] == "open" else outcome_of(r).state, "failure_code": r["failure_code"],
                  "model_calls": r["model_calls"]} for r in wf.ledger.runs(mission_id)],
        "verdicts": [{"unit": v["unit_key"], "verifies": v["verifies_key"], "result": v["result"], "state": v["state"]}
                     for v in wf.ledger.verdicts(mission_id)],
        "awaiting_chat": [_brief(wf, mission_id, spec) for spec in wf.runtime.awaiting(mission_id)],
        "messages": wf.missions.messages(mission_id),
        # What only a person decides (V4c): g6.workforce.confirm / decide, or python -P -m g6.workforce.decide
        "awaiting_human": wf.runtime.decisions.pending(mission_id) + wf.runtime.confirmation_pending(mission_id),
        "decisions": [{"unit": d["unit_key"], "kind": d["kind"], "decision": d["decision"], "reason": d["reason"],
                       "actor": d["actor"]} for d in wf.runtime.decisions.all(mission_id)],
        "schedule": _schedule(wf, mission_id),
        "human_attention": attention(wf, mission_id),
        "acceptance": {**found.as_dict(), "sha256": found.sha256},
        # E2E Build 7 (test J): which stages of the founder's loop ran, with the rows that show it (detail="full" only)
        **({"stages": _stages(wf, mission_id)} if stages else {}),
        "runner": ({"holder": mission["lease_holder"], "until": mission["lease_until"], "alive": None}
                   if mission["status"] in RUNNING and mission["lease_holder"] else {}),
        "wakes": [{"kind": w["kind"], "unit": w["unit_key"], "at": w["at"], "name": w["name"],
                   "path": w.get("path", "")} for w in wf.runtime.wakes.armed(mission_id)],
    }


#: What the chat sees by default from a mission tool (Design §5, §34 question 10; plan ruling P2.23): the result, and
#: what it needs to drive the mission. The rest is technical, on request (mission_status with detail="full").
SUMMARY = ("mission_id", "objective", "status", "completion_state", "result", "risk_tier", "budget_cents",
           "awaiting_chat", "awaiting_human", "wakes", "runner",
           "review", "coding", "interaction_state", "clarification")  # additive basic-host interaction


def trim(full: dict) -> dict:
    """A full status as the default return: the summary fields, and what ``detail="full"`` adds (the stage map, which
    only that detail builds, named too)."""
    out = {key: full[key] for key in SUMMARY if key in full}
    out["more"] = 'mission_status with detail="full" adds: ' + ", ".join(
        dict.fromkeys([*(k for k in full if k not in SUMMARY), "stages"]))
    return out


def _stages(wf: Workforce, mission_id: str) -> dict:
    """The stage map, read through the bound ledger (no schema statement) and without worker instances; a map that
    cannot be built answers why, so the return is never lost to it (as ``view.result``; review I2)."""
    try:
        return trace.stages(trace.gather(wf.missions, mission_id, ledger=wf.ledger, instances=False))
    except Exception as exc:  # noqa: BLE001 - a return is never lost to its own stage map
        # COPY: Why the full status carries no stage map.
        return {"unbuilt": f"G6 could not build the stage map from this mission's records ({type(exc).__name__}: "
                           f"{exc})"[:300]}


def summary(wf: Workforce, mission_id: str) -> dict:
    return trim(status(wf, mission_id))


def unmet_demand(wf: Workforce) -> list[dict]:
    """What this venture's missions asked that G6 cannot check or cannot meet here (E7d), oldest first, for the PMF
    instruments (C3). Read-only; the chat is served none of it."""
    return [row for mission in wf.missions.list(wf.venture_id) for row in wf.missions.unmet(mission["mission_id"])]


def _schedule(wf: Workforce, mission_id: str) -> list[dict]:
    """Each step's scheduler decision for the latest plan revision that recorded one (E8e), as its reason codes and
    strategy type; [] before a plan is stored."""
    recorded = wf.missions.events(mission_id, kind="schedule")
    if not recorded:
        return []
    return [{"step": d["work_unit_id"], "reasons": list(d["reason_codes"]), "how": d["selected_option"]["strategy_type"]}
            for d in recorded[-1]["detail"]["decisions"]]


def _tree(units: list[dict]) -> dict[str, list[str]]:
    """The delegation tree (V4a): each unit that delegated, with its children in the order they were given."""
    tree: dict[str, list[str]] = {}
    for unit in units:
        if unit["parent"]:
            tree.setdefault(unit["parent"], []).append(unit["key"])
    return tree


def _brief(wf: Workforce, mission_id: str, spec) -> dict:
    """What the chat needs to write a pull unit: its goal, the shape asked for, its inputs' outputs, and what its
    verifiers said of its earlier outputs. A saved result it names that may no longer be reused shows as None, with
    ``input_problem`` saying why and what resolves it (E2b; only then, so other briefs keep their shape)."""
    from g6.workforce import coding

    inputs, problem = wf.runtime.brief_inputs(mission_id, spec)
    brief = {"unit": spec.key, "goal": spec.goal, "output_schema": spec.output_schema, "inputs": inputs,
             "attempts_left": spec.max_attempts - wf.ledger.attempts(mission_id, spec.key),
             # What its verifiers said of its earlier outputs (V4b): why it is being asked again. A reproduction's
             # receipts are shown without the route they name (coverage ledger R2): the brief names no capability.
             "messages": [{"from": m["from"], "text": m["text"], "refs": {**m["refs"], "reproduction": coding.unrouted(
                 m["refs"]["reproduction"])} if "reproduction" in m["refs"] else m["refs"]}
                          for m in wf.missions.messages(mission_id, to=spec.key)]}
    standard = coding.frozen(wf, mission_id)
    if standard:
        details = coding.status(wf, mission_id, standard)
        details.pop("intake")
        details["standard"] = {k: v for k, v in details["standard"].items() if k != "intake"}
        details["checks"] = [{k: v for k, v in check.items() if k != "op"} for check in details["checks"]]
        brief["coding"] = details
        brief["instructions"] = coding.OBJECTIVE_INSTRUCTIONS
    if problem:
        brief["input_problem"] = problem
    if spec.call:
        # A code step handed to the chat after its check failed (E2d escalation): the call it was planned to make,
        # so the chat writes the same step rather than re-deriving it (Design §34 review, 2026-10-05, question 2).
        brief["call"] = {"capability": list(spec.call["capability"]), "params": dict(spec.call.get("params") or {}),
                         "bind": dict(spec.call.get("bind") or {})}
    return brief
