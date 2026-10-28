# SPDX-License-Identifier: Apache-2.0
"""Verification search, part 4: the formal path, the only part of verification search that searches multi-step
behaviour. It never lets a result about a model read as a result about the code.

For each ``state_invariant.v1`` instance (``g6.harness.verification_templates``), on the abstract state model the caller
states (provenance ``stated``; a model a proposer builds is VS-5's):

1. **The obligations first.** ``g6.formal.check_contract`` on the invariants, with the model's ``init`` as their premise:
   an invariant that holds without it (a tautology) is vacuous, invariants no state satisfies together conflict, and a
   check that cannot decide is undecided. Each of these is reported, and the model is never searched.
2. **The search.** ``g6.model_checking``'s ``check_invariant`` (bounded, to the instance's depth) or
   ``prove_invariant`` (k-induction), in G6's process on the model's data only (its closed grammar reads no command).
   G6 restricts the model to the actions the instance maps, and, for a bounded search, adds a step counter and one
   history variable per step (``g6_step``, ``g6_h1`` ...), so each trace's action sequence is part of its last state:
   once a trace is found, its sequence is excluded and the search asked again, until the instance's number of traces or
   no further trace. Every model run is an activity.
3. **The replay.** The traces are compiled, by the template's fixed text, into one replay test: for each trace, from a
   fresh initial state, the operation calls in order, then each observed variable read on the real state and the
   invariants evaluated on those values (with the model's own values for the variables the code does not show). A
   trace whose invariants fail whatever the observed values are (given the model's own values) would say nothing of
   the code, so it is not replayed. The test runs confined like any compiled check (VS-2's executor, VS-3's import
   roots, ``G6_REPO_PYTHON``, ``G6_REPO_ENV`` and the loaded-elsewhere guard): on the patch, and at the base commit for
   an instance whose intent is ``preserve``.
4. **The three-part report** (plan rule 9), in the instance's activity and, for a finding, in its defect:
   ``specification`` (the template, the provenance of the bindings and of the model, and ``check_contract``'s result),
   ``model`` (the operation, verdict, depth or k, the traces, where the model came from) and ``correspondence``:
   ``reproduced`` (a trace breaks the invariants on the real code), ``not_reproduced`` with reason ``model_only`` (every
   trace replayed keeps them: the abstraction was too broad, so nobody is accused), ``not_replayable`` (with the
   reason), ``not_replayed`` (the model checker found no trace: about the model only) or ``not_searched``.

Only a reproduced trace is a finding about the code, and then it is a check G6 compiled from the caller's bindings:
``unconfirmed_counterexample`` (stage ``evidence``, so at most ``insufficient_evidence``), with the replay test as its
reproduction; for a ``preserve`` instance, a trace that breaks the invariants at the base commit too is
``fails_on_base_too``, with no accusation. ``k_inductive`` and a bounded "none found" leave the requirement exactly as it
was, and the requirement says the result is about the model only. A result about a model never passes a requirement
and never gives ``defect_detected``.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass, field

from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt

#: Each formal run's own time limit in seconds, inside the review's deadline (model_checking allows at most 60).
FORMAL_S = 30.0
CONTRACT_S = 10.0
#: The time the replays need after the search: the search stops before it would leave less.
RESERVE_S = 2 * vs.MIN_RUN_S + 1
ABOUT_THE_MODEL = ("about the model only: a model checker's answer on the model the caller stated, restricted to the "
                   "actions the instance maps; it says nothing of the code and leaves the requirement as it was")
MODEL_ONLY = ("every trace the model checker found that G6 could replay keeps the invariants on the real code: the "
              "abstraction was too broad, so nobody is accused")
NOT_A_TRACE = ("the trace is an induction step: its first state need not be reachable from the initial states, so it "
               "is no counterexample, and it is not replayed")
INDEPENDENT = ("the invariants fail on this trace whatever the code shows, given the model's own values for the "
               "variables the code does not show, so its replay would say nothing of the code")


@dataclass
class Formal:
    """What the formal path did: one record per ``state_invariant.v1`` instance, with its three parts."""
    runs: list[dict] = field(default_factory=list)


def _int(value: int) -> str:
    return str(value) if value >= 0 else f"(- {-value})"


def _conjunction(terms: list[str]) -> str:
    return terms[0] if len(terms) == 1 else "(and " + " ".join(terms) + ")"


def _contract(model, deadline: float) -> dict:
    """``check_contract`` on the invariants, ``init`` their premise: vacuous, conflicting or undecided obligations are
    never searched."""
    from g6.formal.contract import check_contract
    from g6.formal.envelope import VACUOUS_PROPERTY

    started = time.monotonic()
    found = check_contract(list(model.invariants.values()), premises=[model.init], sorts=model.variables,
                           timeout=max(0.1, min(CONTRACT_S, deadline - time.monotonic() - RESERVE_S)))
    vacuous = bool(found.vacuous) or found.code == VACUOUS_PROPERTY
    conflicting = found.value == "B"
    return {"value": found.value, "vacuous": vacuous, "conflicting": conflicting,
            "undecided": found.value not in ("T", "F", "B") and not vacuous, "code": found.code,
            "reason": (found.reason or "")[:400], "seconds": round(time.monotonic() - started, 3),
            "checked": "each invariant under init, then without it (a tautology is vacuous), then all together (no "
                       "state satisfying them all is a conflict)"}


def _restriction(action: str, values: list[int]) -> str:
    return _conjunction([f"(= next_{action} {_int(v)})" for v in values]) if len(values) == 1 else (
        "(or " + " ".join(f"(= next_{action} {_int(v)})" for v in values) + ")")


def _model_run(one, operation: str, got, depth: int, actions: list[int] | None = None) -> dict:
    run = {"activity": "model check", "instance": one.id, "template": one.template, "operation": operation,
           "verdict": got.verdict or "refused", "code": got.code, "depth": depth, "explored_depth": got.explored_depth,
           "k": got.k, "engine": got.engine or "z3", "seconds": float(got.elapsed_s), "model_calls": 0}
    if actions is not None:
        run["actions"] = actions
    if not got.verdict:
        run["reason"] = (got.reason or "")[:300]
    return run


def _bounded(one, b, model, deadline: float, activities: list) -> tuple[dict, list[dict]]:
    """``check_invariant`` on the model with G6's history, asked again with each trace found excluded."""
    from g6.model_checking import symbolic

    values = sorted(int(key) for key in b.actions)
    history = [f"g6_h{i}" for i in range(1, b.depth + 1)]
    variables = {**model.variables, "g6_step": "Int", **{name: "Int" for name in history}}
    init = _conjunction([model.init, "(= g6_step 0)", *(f"(= {name} 0)" for name in history)])
    copies = [f"(ite (= g6_step {i - 1}) (= next_{name} next_{b.action}) (= next_{name} {name}))"
              for i, name in enumerate(history, 1)]
    transition = _conjunction([model.transition, _restriction(b.action, values), "(= next_g6_step (+ g6_step 1))",
                               *copies])
    obligations = _conjunction(list(model.invariants.values()))
    excluded, traces, last = [], [], None
    while len(traces) < b.traces:
        left = deadline - time.monotonic() - RESERVE_S
        if left < 1:
            last = {"verdict": "stopped", "reason": vs.OUT_OF_TIME}
            break
        term = "(or " + " ".join([*excluded, obligations]) + ")" if excluded else obligations
        got = symbolic.check_invariant({"variables": variables, "init": init, "transition": transition,
                                        "invariants": {"g6_obligations": term}}, depth=b.depth,
                                       time_limit=min(FORMAL_S, left))
        if got.verdict == "violated" and got.trace:
            states = [step["state"] for step in got.trace]
            actions = [state[b.action] for state in states[1:]]
            activities.append(_model_run(one, "check_invariant", got, b.depth, actions))
            traces.append({"actions": actions, "final": {name: states[-1][name] for name in model.variables}})
            excluded.append(_conjunction([f"(= g6_step {len(actions)})",
                                          *(f"(= g6_h{i} {_int(a)})" for i, a in enumerate(actions, 1))]))
            continue
        activities.append(_model_run(one, "check_invariant", got, b.depth))
        last = {"verdict": got.verdict or "refused", "code": got.code, "reason": (got.reason or "")[:300],
                "explored_depth": got.explored_depth}
        break
    part = {"operation": "check_invariant", "depth": b.depth, "source": "stated", "engine": "z3",
            "verdict": "violated" if traces else (last or {}).get("verdict", "no_violation_within_bound"),
            "traces": [trace["actions"] for trace in traces], "runs": len(traces) + (1 if last else 0),
            "history_variables": 1 + b.depth,
            "restricted_to": f"the actions the instance maps ({', '.join(map(str, values))})"}
    if last:
        part["last_run"] = last
    if not traces:
        part["note"] = ABOUT_THE_MODEL
    return part, traces


def _induction(one, b, model, deadline: float, activities: list) -> tuple[dict, list[dict]]:
    """``prove_invariant`` on the model, restricted to the mapped actions."""
    from g6.model_checking import symbolic

    values = sorted(int(key) for key in b.actions)
    left = deadline - time.monotonic() - RESERVE_S
    part = {"operation": "prove_invariant", "depth": b.depth, "source": "stated", "engine": "z3",
            "restricted_to": f"the actions the instance maps ({', '.join(map(str, values))})"}
    if left < 1:
        return {**part, "verdict": "stopped", "reason": vs.OUT_OF_TIME}, []
    got = symbolic.prove_invariant({"variables": dict(model.variables), "init": model.init,
                                    "transition": _conjunction([model.transition, _restriction(b.action, values)]),
                                    "invariants": dict(model.invariants)}, depth=b.depth,
                                   time_limit=min(FORMAL_S, left))
    part.update(verdict=got.verdict or "refused", k=got.k, code=got.code, explored_depth=got.explored_depth)
    if got.verdict == "k_inductive":
        part["note"] = ABOUT_THE_MODEL
    if got.verdict == "violated" and got.trace:
        states = [step["state"] for step in got.trace]
        actions = [state[b.action] for state in states[1:]]
        activities.append(_model_run(one, "prove_invariant", got, b.depth, actions))
        part["traces"] = [actions]
        return part, [{"actions": actions, "final": {name: states[-1][name] for name in model.variables}}]
    activities.append(_model_run(one, "prove_invariant", got, b.depth))
    if not got.verdict:
        part["reason"] = (got.reason or "")[:300]
    return part, []


def _depends_on_code(model, ghost: dict, observed: list[str], deadline: float) -> bool:
    """Whether some values of the observed variables keep the invariants, given the model's own values for the others:
    else a trace breaks them whatever the code does, and its replay would say nothing of the code."""
    from g6.formal import check_sat
    from g6.model_checking.encoding import literal

    fixed = [f"(= {name} {literal(model.variables[name], value, name)})" for name, value in ghost.items()]
    found = check_sat(_conjunction([*fixed, *model.invariants.values()]), model.variables,
                      timeout=max(0.1, min(CONTRACT_S, deadline - time.monotonic() - RESERVE_S)))
    return found.satisfiable is True


def _outcomes(found: dict, count: int) -> dict[int, dict] | str:
    observed = found.get("observations")
    if not isinstance(observed, list):
        return observed if isinstance(observed, str) else "the replay recorded no observation of its traces"
    outcomes = {item["input"]: item["output"] for item in observed}
    if set(outcomes) != set(range(count)) or not all(isinstance(o, dict) and o.get("outcome") in (
            "broken", "holds", "not_replayable") for o in outcomes.values()):
        return "the replay's observations do not account for each trace once"
    return outcomes


def _judge(count: int, intent: str, on_patch: dict, at_base: dict | None) -> dict:
    """A replay's result from its readings: the traces it says broke the invariants on the real code."""
    def error(where: str, why: str, **correspondence) -> dict:
        return {"result": vs.CHECK_ERROR, "detail": f"{where}: {why}",
                "correspondence": {"result": "not_replayable", "reason": why[:300], **correspondence}}

    if on_patch["result"] == "error":
        return error("on the patch", on_patch["detail"])
    patch = _outcomes(on_patch, count)
    if isinstance(patch, str):
        return error("on the patch", patch)
    broken = sorted(i for i, o in patch.items() if o["outcome"] == "broken")
    held = sorted(i for i, o in patch.items() if o["outcome"] == "holds")
    unplayable = {str(i): str(o.get("why", ""))[:200] for i, o in patch.items() if o["outcome"] == "not_replayable"}
    if (on_patch["result"] == "failed") != bool(broken):
        return error("on the patch", "its result and its observations disagree, so G6 does not read it")
    if not broken:
        if not held:
            return error("on the patch", "no trace could be replayed: " + "; ".join(unplayable.values()),
                         traces_not_replayable=unplayable)
        return {"result": vs.NOT_REFUTED, "detail": f"no trace the model checker found breaks the invariants on the "
                                                    f"real code ({len(held)} of {count} replayed)",
                "correspondence": {"result": "not_reproduced", "reason": "model_only", "detail": MODEL_ONLY,
                                   "traces_held": held, "traces_not_replayable": unplayable}}
    shown = ", ".join(map(str, broken))
    if intent == "change":
        return {"result": vs.COUNTEREXAMPLE, "detail": f"trace(s) {shown} the model checker found break the "
                                                       "invariants on the patch's real code",
                "shape": "a trace the model checker found breaks the invariants on the patch's real code",
                "correspondence": {"result": "reproduced", "traces_reproduced": broken, "on": "the patch"}}
    if at_base is None or at_base["result"] == "error":
        why = at_base["detail"] if at_base else "it did not run"
        return {"result": vs.CHECK_ERROR, "detail": f"at the base commit: {why}",
                "correspondence": {"result": "reproduced", "traces_reproduced": broken, "on": "the patch",
                                   "at_base": f"not established: {why}"[:300]}}
    base = _outcomes(at_base, count)
    if isinstance(base, str):
        return error("at the base commit", base)
    also = [i for i in broken if base[i]["outcome"] == "broken"]
    new = [i for i in broken if i not in also]
    if not new:
        return {"result": vs.FAILS_ON_BASE_TOO, "detail": f"trace(s) {shown} break the invariants on the real code at "
                                                          "the base commit too",
                "correspondence": {"result": "reproduced", "traces_reproduced": broken, "at_base_too": also}}
    return {"result": vs.COUNTEREXAMPLE,
            "detail": f"trace(s) {', '.join(map(str, new))} the model checker found break the invariants on the "
                      "patch's real code and keep them at the base commit's",
            "shape": "a trace the model checker found breaks the invariants on the patch's real code, and keeps them "
                     "at the base commit's",
            "correspondence": {"result": "reproduced", "traces_reproduced": new, "traces_also_at_base": also}}


def _record(one, b) -> dict:
    return {"id": one.id, "requirement": one.requirement_id, "intent": one.intent, "replayed": False,
            "specification": {"template": one.template, "bindings_provenance": one.provenance,
                              "model_provenance": one.provenance, "obligations": None, "contract": None},
            "model": {"operation": "prove_invariant" if b.search == "induction" else "check_invariant",
                      "verdict": "not_searched", "depth": b.depth, "source": one.provenance},
            "correspondence": {"result": "not_searched"}}


def parts(record: dict) -> dict:
    return {key: record[key] for key in ("specification", "model", "correspondence")}


def _standalone(prepared, one, record: dict, result: str, seconds: float, reason: str = "") -> None:
    prepared.activities.append({**vt._listing(one), "result": result, **({"reason": reason} if reason else {}),
                                "seconds": round(seconds, 3), "formal": parts(record)})


def run(prepared, *, deadline: float, skip: str = "") -> Formal:
    """The formal path for each ``state_invariant.v1`` instance that applies (``prepared.formal``): the obligations,
    the search, and each replay compiled and added to ``prepared``'s checks for the executor. ``skip``: none is run,
    with that reason."""
    from g6.model_checking.encoding import read_model

    formal = Formal()
    for item in prepared.formal:
        one, b, applied = item["instance"], item["bindings"], item["applied"]
        record = _record(one, b)
        formal.runs.append(record)
        started = time.monotonic()
        model = read_model(b.model, need_invariants=True)
        record["specification"]["obligations"] = dict(model.invariants)
        if skip:
            record["model"]["reason"] = record["correspondence"]["reason"] = skip
            _standalone(prepared, one, record, "skipped", 0.0, skip)
            continue
        contract = _contract(model, deadline)
        record["specification"]["contract"] = contract
        prepared.activities.append({"activity": "contract check", "instance": one.id, "template": one.template,
                                    "result": contract["value"], "vacuous": contract["vacuous"],
                                    "conflicting": contract["conflicting"], "seconds": contract["seconds"],
                                    "model_calls": 0})
        why = ("the obligations are vacuous: an invariant holds whatever the state, so checking it says nothing"
               if contract["vacuous"] else
               "the obligations are conflicting: no state satisfies the invariants together" if contract["conflicting"]
               else f"the obligations could not be checked ({contract['code'] or 'undecided'})"
               if contract["undecided"] else "")
        if why:
            record["model"]["reason"] = why
            record["correspondence"] = {"result": "not_searched", "reason": why}
            _standalone(prepared, one, record, "not_searched", time.monotonic() - started, why)
            continue
        search = _induction if b.search == "induction" else _bounded
        record["model"], traces = search(one, b, model, deadline, prepared.activities)
        if not traces:
            verdict = record["model"]["verdict"]
            record["correspondence"] = (
                {"result": "not_replayable", "reason": NOT_A_TRACE} if verdict == "not_inductive" else
                {"result": "not_replayed", "reason": ABOUT_THE_MODEL}
                if verdict in ("k_inductive", "no_violation_within_bound") else
                {"result": "not_searched", "reason": record["model"].get("reason") or
                 record["model"].get("last_run", {}).get("reason") or verdict})
            result = "about_the_model_only" if verdict in ("k_inductive", "no_violation_within_bound") else verdict
            _standalone(prepared, one, record, result, time.monotonic() - started)
            continue
        observed = list(b.observe)
        ghosts = [name for name in applied.data["sorts"] if name not in b.observe]
        replayed, dropped = [], {}
        for index, trace in enumerate(traces):
            ghost = {name: trace["final"][name] for name in ghosts}
            if _depends_on_code(model, ghost, observed, deadline):
                replayed.append({"actions": trace["actions"], "ghost": ghost, "model_trace": index})
            else:
                dropped[str(index)] = INDEPENDENT
        record["model"]["traces_not_replayed"] = dropped
        if not replayed:
            record["correspondence"] = {"result": "not_replayable", "reason": INDEPENDENT}
            _standalone(prepared, one, record, "not_replayable", time.monotonic() - started)
            continue
        data = {**applied.data, "traces": [{"actions": t["actions"], "ghost": t["ghost"]} for t in replayed]}
        try:
            check, placed, origin = vt.compiled_check(
                item["root"], one, data, applied.notes, keep_observations=True, patch_only=one.intent == "change",
                judge=functools.partial(_judge, len(replayed)),
                unconfirmed="G6 compiled its replay from the caller's model and bindings, and a compiled check "
                            "decides no requirement")
        except vt.Inapplicable as exc:
            record["correspondence"] = {"result": "not_replayable", "reason": str(exc)}
            _standalone(prepared, one, record, "not_replayable", time.monotonic() - started, str(exc))
            continue
        record["replayed"] = True
        record["replay_traces"] = [t["model_trace"] for t in replayed]
        prepared.checks.append(check)
        prepared.placed[check.id] = placed
        prepared.origins[check.id] = origin
    return formal


def finish(formal: Formal | None, search) -> None:
    """Each replay's correspondence, from its run, into its record; the three parts into its activity and defect."""
    if formal is None:
        return
    for record in formal.runs:
        if not record["replayed"]:
            continue
        activity = next((a for a in (search.activities if search else []) if a.get("check") == record["id"]), None)
        if activity is None:
            continue
        activity.pop("shape", None)
        correspondence = activity.pop("correspondence", None) or {
            "result": "not_replayable", "reason": activity.get("reason") or activity.get("detail") or "not run"}
        correspondence["model_traces"] = record["replay_traces"]
        record["correspondence"] = correspondence
        activity["formal"] = parts(record)
        for defect in search.defects:
            if defect["check"] == record["id"]:
                defect["formal"] = parts(record)


def annotate(evaluated: list[dict], formal: Formal | None) -> None:
    """A requirement a model-only result names says so, and keeps its status."""
    if formal is None:
        return
    for record in formal.runs:
        if record["model"].get("verdict") not in ("k_inductive", "no_violation_within_bound"):
            continue
        for req in evaluated:
            if req["id"] == record["requirement"]:
                req.setdefault("formal", []).append({"check": record["id"], "verdict": record["model"]["verdict"],
                                                     "note": record["model"]["note"]})
