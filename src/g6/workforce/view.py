# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The mission as its owner sees it (Phase E Part 2, E8a): one ``ResultView`` built from the mission's rows.

The view says these things, in this order:
* The answer. For a finished mission it is what its last steps produced: the outputs no other producing step
  consumes. For an unfinished one it is what the mission waits for, and what the owner can do about it.
* The trust state, with the checks that ran in plain words (Design §4.1), from the mechanisms each check recorded
  (E2c).
* What remains uncertain or failed: each stopped step's Design §23 message, from its failure class (E2a).
* Checks that disagree, as interpretations (§24).
* The evidence with its provenance (§25), and what the work took: attempts, machine cost and the owner's time
  (Doctrine §18).

Read-only. Nothing here invents a value or reads a clock, so two processes render one mission identically (U6).
"""
from __future__ import annotations

import json
import re
from typing import Any

from g6.workforce.result_view import (
    LABELS,
    Check,
    Evidence,
    Interpretation,
    Problem,
    ResultView,
    Surface,
    render,
)
from g6.workforce import acceptance, classes, economics
from g6.workforce.contracts import BLOCKED, VERIFIED, Outcome
from g6.workforce.missions import FINISHED
from g6.workforce.runs import outcome_of
from g6.workforce.truth import RECORDER_GAP

# COPY: The frozen test provenance explains the coding result's ceiling.
CALLER_TESTS = "Caller-authored tests cannot independently verify this change."
DERIVED_TESTS = "The caller's tests and task-derived checks are nonindependent evidence."
#: The words that open a step G6 stopped for a person (reconfigure.NO_CHECK, coding.PERSON): it leads the answer.
PERSON = "Human action required"
# COPY: Why a check G6 stopped for a person has no result of its own.
NOT_RUN = "G6 did not run it, because the tool it needs was found unavailable"
# COPY: Why a step G6 stopped for a person after it ran has no failure of its own (the repair loop stopped it).
STOPPED_FOR_PERSON = "G6 stopped it for a person"
# COPY: A later step stopped for the same person's step points to it, so the answer says it once.
SEE_ABOVE = "the step that needs a person, above"
# COPY: What resolves the repair loop's stop for a person.
RESTART = "read what the failing check showed (mission_status with detail=\"full\"), then start the objective again"

#: Plain words for each Ledger §10 mechanism (Design §4.1: the consequence, never the machinery).
SAID = {
    "test_execution": "Tests were run on the result",
    "formal_proof_check": "A solver checked the result against its inputs",
    "deterministic_computation": "A separate check recomputed the result",
    "receipt_validation": "The receipts were checked",
    "human_review": "A person judged a disagreement between checks",
    "independent_model_review": "A separate model reviewed the result",
}
#: How to ask for more on the workforce's MCP surface (Design §5: technical details only on request). Each result is
#: there in full, under "outputs" (fix pass I6).
MORE = 'call mission_status with detail="full" for each result in full ("outputs") and the technical details'
#: What a person reads beside each output of a result that could not be verified (fix pass I7).
REJECTED, UNVERIFIED = "a draft the checks rejected", "a result the mission could not verify"
#: A mission accepted before contracts were kept, at a tier that now asks the owner to confirm (fix pass I2).
COMPILED = ("What counts as done was compiled from this mission's older record: it was accepted before G6 kept one, "
            "so you were not asked to confirm it.")
#: The workforce's own internal words, said plainly in a stopped step's reason (plan ruling P2.21: G6's own wording is
#: translated; a reason that still holds an avoided term moves to technical details).
_PLAIN = (("the oracle expects", "the check expects"), ("oracle", "check"), ("verdict", "finding"),
          ("artifact", "result"), ("verifier", "check"), ("declares", "states"), ("declared", "stated"))
#: Each stored reason as a person reads it (Design §19, §23; the lead's P2.21 ruling): what happened, said once, never
#: a wire state or the machinery's own note. A pattern's ``{x}`` is said the same way in turn; an empty result drops
#: the reason (a policy note, or a raw tool answer, which stays in the technical details). Stored reasons are unchanged.
_SAID_AS = tuple((re.compile(pattern, re.IGNORECASE), said) for pattern, said in (
    ((r"a produced output is a draft until it is judged|written by .*: a draft until it is judged|"
      r"the component's own 'verified' is capped at .*|the oracle answered: .*"), ""),
    (r"(?:the reply declares|declared|component reported) blocked[-_]escalated", "the tool said it could not do this"),
    (r"declared blocked[-_]escalated: (?P<x>.+)", "the tool said it could not do this: {x}"),
    (r"no result: empty", "the tool returned no result"),
    (r"no result: (?P<x>.+)", "the tool returned no result: {x}"),
    (r"(?:the )?solver answered unknown", "the solver could not decide"),
    (r"validity unknown", "the tool could not say whether it holds"),
    (r"component status (?P<x>.+)", "the tool answered with the status {x}"),
    (r"(?:component error|the call failed|reported a failure|reported an error): (?:ok is false|ok=false)|ok=false",
     "the tool reported a failure"),
    (r"(?:component error|the call failed|reported a failure|reported an error): (?P<x>.+)",
     "the tool reported a failure: {x}"),
    (r"degraded: (?P<x>.+)", "the tool's answer was incomplete: {x}"),
    (r"verification failed \([\w ]+\): (?P<x>.+)", "the check failed: {x}"),
    (r"verification not measured \([\w ]+\): (?P<x>.+)", "the check could not decide: {x}"),
    (r"a pass that cannot verify: (?P<x>.+)", "the check passed, but it cannot count as independent: {x}"),
    (r"(?P<field>[\w.]+) is (?P<value>.+), the oracle expects (?P<equals>.+)",
     "its answer had {field} = {value} where {equals} was expected"),
    (r"the oracle's op is not declared model-free", "the check's tool may use a model, so it is not independent"),
    (r"the oracle's answer was degraded", "the check's answer was incomplete"),
    (r"answered on defaults: .*", "the tool read none of the inputs it was given"),
    (r"parameters with no effect: (?P<x>.+)", "some inputs had no effect: {x}"),
    (r"refused at the door: (?P<x>.+)", "{x}"),
    (r"(?P<x>[\w.]+) is sensitive: the generic door denies it",
     "{x} handles sensitive data, so G6 does not call it for a mission"),
    (r"no worker can be formed: (?P<x>.+)", "G6 could not set up this step: {x}"),
    (r"a (?P<x>\w+) unit needs the human rung \(V1\)", "a {x} step needs a person"),
    (r"unit '[^']*' names no call, and only the deterministic rung runs in P0 \(local, pull and human rungs are V1\)",
     "the step names no tool call for G6 to run"),
    (r"no ParamContract declares (?P<x>.+)", "G6 has no reviewed description of {x}, so it does not call it"),
    (r"not an envelope: .*", "the tool's answer could not be read"),
    (r"the reply nests deeper than .*", "the tool's answer was nested too deeply to read"),
))
#: chain.verdict's prefix for a component that gave no answer: what follows is said, or is the tool's own error.
_ANSWERLESS = "the component did not answer: "
#: A wire state left in a tool's own text, said as its label (Design §10).
_WIRE = re.compile(r"blocked[-_]escalated|qualified[-_]draft|verified[-_]ready", re.IGNORECASE)
_WIRE_SAID = {"blocked": "could not verify", "qualified": "use with caution", "verified": "verified"}


def _said(reason: Any) -> str:
    """One stored reason in plain words ("" when a person need not read it)."""
    text = " ".join(str(reason).split())
    answerless = text.lower().startswith(_ANSWERLESS)
    text = text[len(_ANSWERLESS):] if answerless else text
    for pattern, said in _SAID_AS:
        found = pattern.fullmatch(text)
        if found:
            return said.format(**{key: _said(value) for key, value in found.groupdict().items()})
    return f"the tool reported a failure: {text}" if answerless else text


def _why(reasons: Any) -> str:
    """A stopped step's reasons in plain words, each said once (a reason the next one extends is left to it); raw
    tool answers and policy notes left for technical details."""
    said = [text for text in dict.fromkeys(_said(r) for r in reasons) if text]
    text = "; ".join(s for s in said if not any(other != s and other.startswith(s) for other in said))
    for old, new in _PLAIN:
        text = text.replace(old, new)
    return _WIRE.sub(lambda found: _WIRE_SAID[re.split(r"[-_]", found.group(0).lower())[0]], text)


def result(wf: Any, mission_id: str) -> dict:
    """The rendered default ``result`` of a mission return (``wf``: a bound ``service.Workforce``)."""
    return render(build(wf, mission_id), more=MORE)


def build(wf: Any, mission_id: str) -> ResultView:
    """The mission's view. Another owner's mission is ``NOT_FOUND``, as every read is."""
    mission = wf.missions.get(mission_id)
    finished = mission["status"] in FINISHED
    plan = _plan(wf, mission)
    runs = wf.ledger.runs(mission_id)
    events = wf.missions.events(mission_id)
    closed = next((e["detail"] for e in reversed(events) if e["kind"] == "mission.closed"), {})
    evaluation = (acceptance.Evaluation.from_dict(closed["acceptance"])
                  if isinstance(closed.get("acceptance"), dict) else None)
    decisions = wf.runtime.decisions.all(mission_id)
    verdicts = wf.ledger.verdicts(mission_id)
    facts = None
    if plan is not None:
        facts = acceptance.gather(plan=plan, runs=runs,
                                  artifacts={e["evidence_id"]: e["detail"] for e in events if e["kind"] == "artifact"},
                                  closed=[e["detail"] for e in events if e["kind"] == "run.closed"],
                                  decisions=decisions, receipts=[],  # no outbox in the library
                                  verdicts=verdicts, questions=wf.runtime.decisions.requested(mission_id),
                                  effects=wf.caps.side_effect)
    records = (evaluation.records if evaluation is not None
               else tuple(r for unit in (facts.do_units if facts else ()) for r in facts.checks.get(unit, ())))
    goals = {u.key: u.goal for u in plan.units} if plan is not None else {}
    outcome = Outcome.from_record(mission["outcome"]) if finished else None
    readings, unresolved = _disputes(wf, mission_id, goals)
    compiled = (mission["acceptance"] is None and mission["status"] != "eliciting"
                and mission["risk_tier"] in acceptance.CONFIRM_TIERS)
    caller_tests = wf.missions.acceptance(mission_id).metadata.get("coding", {}).get("test_provenance") == "caller"
    derived_tests = wf.missions.acceptance(mission_id).metadata.get("coding", {}).get("test_provenance") == "caller_and_task_derived"
    from g6.workforce import coding

    clarified = bool(coding.clarification_request(wf.missions, mission_id))
    compiled = compiled and not clarified and not coding.reuse_reference(wf.missions, mission_id)
    attention = economics.attention_total(wf.runtime.decisions.interventions(mission_id), hourly_rate=wf.hourly_rate)
    attention_words = ("clarification requested; time in chat is not measured" if clarified and not attention.interventions
                       else _attention(attention) + ("; time in chat is not measured" if clarified else ""))

    warned: tuple[str, ...] = ()  # learned probes (#187) are deferred with the learning layer
    limits = tuple(mission.get("evidence_limits", ()))
    coverage = _coverage(evaluation) if evaluation is not None else ""
    if limits and coverage == "Strong":
        coverage = "Partial"  # the saved evaluation predates the effective evidence limit

    return ResultView(
        title=mission["objective"], answer=_answer(wf, mission, plan, outcome), state=outcome.state if outcome else "",
        checks=_checks(records, goals, evaluation), coverage=coverage,
        agreement=_agreement(records, recorded=evaluation is not None) if finished else "",
        caveats=(*((RECORDER_GAP,) if outcome is not None and outcome.state == VERIFIED  # WORKFORCE-01, never silent
                   and RECORDER_GAP in outcome.reasons else ()),
                 *((COMPILED,) if compiled else ()), *((CALLER_TESTS,) if caller_tests else ()),
                 *((DERIVED_TESTS,) if derived_tests else ()), *warned,
                 *limits, *wf.runtime.unchecked(mission_id),  # the part of the change no check could compare
                 *_caveats(evaluation, facts, decisions, goals)),
        problems=_problems(wf, mission, plan, runs, evaluation),
        interpretations=readings, unresolved=unresolved, evidence=_evidence(wf, mission_id, plan),
        attempts=len(runs), cost=(_cost(wf.ledger.cost_total(mission_id),
                                       economics.total(wf.ledger.pending_reservations(mission_id)))
                                 + ("; intake/preparation cost is unmeasured" if clarified else "")),
        attention=attention_words,
        tests=_tests(records), standard=acceptance.standard_words(wf.missions.acceptance(mission_id)),
        surfaces=_surfaces(evaluation))


def _surfaces(evaluation: Any) -> tuple[Surface, ...]:
    """Each failure surface the result's steps were challenged on, in plain words, each said once at its worst across the
    steps (failed, then not tested, then passed), in the order first met (FE §12; E10c). A mission not finished, or
    finished before surfaces were kept, says none; a stored entry outside the current vocabulary is left out."""
    if evaluation is None:
        return ()
    rank = {"failed": 0, "not_tested": 1, "passed": 2}
    worst: dict[str, str] = {}
    for row in evaluation.surfaces:
        if not isinstance(row, tuple | list) or len(row) != 3:
            continue
        _, name, status = row
        if name not in acceptance.SURFACE_WORDS or status not in rank:
            continue
        worst[name] = status if name not in worst else min(worst[name], status, key=rank.__getitem__)
    return tuple(Surface(acceptance.surface_text(name, status), status) for name, status in worst.items())


def _person(wf: Any, mission_id: str, said: set[str]) -> list[Problem]:
    """The repair loop's stop for a person that no stopped step already carries (E2E Build 6; review M-1)."""
    out = []
    for text in dict.fromkeys(m["text"] for m in wf.missions.messages(mission_id) if m["refs"].get("repair_stop")):
        if text.startswith(PERSON + ": ") and text not in said:
            said.add(text)
            out.append(Problem(PERSON, text.removeprefix(PERSON + ": ").rstrip("."), classes.MATERIAL["UNKNOWN"],
                               RESTART, classes.SURFACE["HUMAN_INPUT_REQUIRED"]))
    return out


def _plan(wf: Any, mission: dict) -> Any:
    try:
        return wf.missions.plan(mission["mission_id"], mission["plan_revision"] or None)[1]
    except LookupError:
        return None  # not planned yet


def _fenced(path: str, text: str) -> str:
    """One file of a patch as a fenced block, its fence longer than any run of backticks in it."""
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{path}:\n{fence}\n{text.rstrip(chr(10))}\n{fence}"


def _text(content: Any) -> str:
    """An output as the person reads it: its text when it is one, a patch (``{"files": {path: text}}``) as one fenced
    block per file (fix pass (c)), else its fields as indented JSON."""
    if isinstance(content, str):
        return content
    files = content.get("files") if isinstance(content, dict) and set(content) == {"files"} else None
    if isinstance(files, dict) and files and all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
        return "\n\n".join(_fenced(path, text) for path, text in files.items())
    if isinstance(content, dict) and len(content) == 1 and isinstance(next(iter(content.values())), str):
        return next(iter(content.values()))
    return json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def _finals(wf: Any, mission_id: str, plan: Any) -> list[tuple[Any, dict]]:
    """The mission's final outputs: each do step's latest output that no other step consumes, in plan order."""
    kinds = {u.key: u.kind for u in plan.units}
    consumed = {i for u in plan.units if u.kind == "do" for i in u.inputs}
    consumed |= {before for before, after in plan.edges if kinds.get(after) == "do"}
    out = []
    for spec in plan.units:
        if spec.kind == "do" and not spec.parent and spec.key not in consumed:
            found = wf.ledger.last_artifact(mission_id, spec.key)
            if found is not None:
                out.append((spec, found))
    return out


def outputs(wf: Any, mission_id: str) -> list[dict]:
    """Each final output of a finished mission in full, for ``mission_status(detail="full")`` (fix pass I6: where a
    cut answer points). In a result that could not be verified each is marked: one no check passed is a draft the
    checks rejected (fix pass I7)."""
    mission = wf.missions.get(mission_id)
    if mission["status"] != "done":
        return []
    blocked = Outcome.from_record(mission["outcome"]).state == BLOCKED
    out = []
    for spec, found in _finals(wf, mission_id, _plan(wf, mission)):
        item = {"step": spec.key, "goal": spec.goal, "content": found.get("content")}
        if blocked:
            passed = any(v["artifact_id"] == found["artifact_id"] and v["result"] == "pass" and v["executed"]
                         and v["state"] != BLOCKED for v in wf.ledger.verdicts(mission_id, spec.key))
            item["note"] = UNVERIFIED if passed else REJECTED
        out.append(item)
    return out


def _answer(wf: Any, mission: dict, plan: Any, outcome: Outcome | None) -> str:
    mid, status = mission["mission_id"], mission["status"]
    if outcome is not None and outcome.state == BLOCKED:
        return ""  # nothing is shown that could not be verified (fix pass I7): what failed says why
    if status == "done":
        parts = [f"{spec.goal}:\n{_text(found.get('content'))}" for spec, found in _finals(wf, mid, plan)]
        from g6.workforce import coding

        if coding.repaired(wf, mid):
            parts.insert(0, coding.REPAIRED)
        return "\n\n".join(parts) if parts else "Nothing was produced."
    if status == "eliciting":
        from g6.workforce import coding

        if coding.reuse_reference(wf.missions, mid):
            return ("Harness Pack setup is incomplete. Repeat its original mission_create request to recover the "
                    "same mission and frozen standard. This does not dispatch checks or transfer earlier approval.")
        pending = coding.clarification_status(wf, mid).get("clarification")
        if pending:
            return pending["problem"] or (pending["question"] + " " + pending["reason"] + " "
                                          + pending["approval"] + " Reply using mission_accept.")
        return "Not started: what counts as done is being agreed in the chat (mission_accept)."
    if status == "awaiting_plan":
        pending = wf.runtime.confirmation_pending(mid)
        nxt = pending[0]["question"] if pending else "the chat submits the plan (mission_plan)."
        return "\n".join(["Accepted. What counts as done:", *acceptance.describe(wf.missions.acceptance(mid)),
                          f"Next: {nxt}"])
    units = wf.graph.units(mid)
    done = sum(1 for u in units if u["status"] == "complete")
    lines = [f"Not finished yet: {done} of {len(units)} steps are complete."]
    lines += [f"Waiting for the chat to write: {spec.goal} (mission_submit, step {spec.key})"
              for spec in wf.runtime.awaiting(mid)]
    for asked in wf.runtime.decisions.pending(mid):
        if asked["kind"] == "gate":
            lines.append(f"Waiting for your approval: {asked['question'][:300]} "
                         "(python -P -m g6.workforce.decide list)")
        else:
            lines.append("Waiting for your decision: the checks disagree about a result (the two interpretations "
                         "are below)")
    for wake in wf.runtime.wakes.armed(mid):
        if wake["kind"] == "timer":
            lines.append(f"Waiting until {wake['at']}")
        elif wake["kind"] == "file":
            lines.append(f"Waiting for the file {wake['name']!r} in the mission's inbox ({wake.get('path', '')})")
    return "\n".join(lines)


def _checks(records: tuple, goals: dict, evaluation: Any) -> tuple[Check, ...]:
    """The checks actually performed (Design §12): each check's mechanism in plain words, and each requirement of
    what counts as done that was measured (one not measured is a caveat, never a check)."""
    out = []
    for record in records:
        # A successful tool invocation can return without performing a check.
        # Explain those gaps in caveats; never count them as checks performed.
        if not _performed(record):
            continue
        measured = record.get("measured") or {}
        tally = f": {measured['passed']} of {measured['total']} passed" if measured else ""
        aside = "; its failure was set aside by a person" if record.get("set_aside") else ""
        passed = {"pass": True, "fail": False}.get(str(record.get("result")))
        out.append(Check(f"{SAID.get(str(record.get('mechanism')), 'A separate check ran')}{tally} "
                         f"({goals.get(record.get('judges', ''), record.get('judges', ''))}){aside}", passed,
                         str(record.get("mechanism", ""))))
    if evaluation is not None:
        for found in (*evaluation.requirements, *evaluation.verification, *evaluation.limits):
            if found.status in (acceptance.MET, acceptance.UNMET):
                out.append(Check(f"What counts as done: {found.name}" + (
                    f" ({found.why})" if found.status == acceptance.UNMET else ""), found.status == acceptance.MET))
    return tuple(out)


def _coverage(evaluation: Any) -> str:
    """Design §11's evidence coverage (plan ruling P2.19): every output and requirement checked objectively or
    strongly is Strong, some is Partial, none is Weak."""
    outputs = [strength for _, strength in evaluation.unit_strengths]
    measured = [*evaluation.requirements, *evaluation.limits]
    good = sum(1 for s in outputs if s in ("objective", "strong")) + sum(1 for m in measured
                                                                         if m.status == acceptance.MET)
    total = len(outputs) + len(measured)
    if total and good == total:
        return "Strong"
    return "Partial" if good else "Weak"


#: The mechanisms that are a reviewer (an independent model's or a person's), the only checks "Reviewer comparison" reads
#: (founder C5, 2026-10-09; C5-1). A test or a solver is evidence and is listed with the checks; it is not a reviewer, and one
#: passing test is not reviewers being consistent.
REVIEWERS = ("independent_model_review", "human_review")


def _agreement(records: tuple, *, recorded: bool) -> str:
    """The reviewer comparison, read against the result (founder C5): a reviewer's failure that stands is Disagreement
    (shown "Conflicting"); one a person set aside is Mixed; reviewer passes only is Agreement ("Consistent"); no
    reviewer at all is "None ran" ("Not evaluated"). Only the reviewer mechanisms count; ``records`` hold each check's
    newest record on the latest outputs. With nothing recorded at all, a mission that closed before records were kept
    says "not recorded" (""), never "None ran" (fix pass I1)."""
    reviews = [r for r in records if r.get("mechanism") in REVIEWERS]
    ran = [r for r in reviews if r.get("executed") and r.get("independent") and r.get("result") in ("pass", "fail")]
    set_aside = any(r.get("set_aside") or r.get("result") == "set_aside" for r in reviews)
    if not ran and not set_aside:
        return "None ran" if recorded else ""
    if any(r["result"] == "fail" and not r.get("set_aside") for r in ran):
        return "Disagreement"
    return "Mixed" if set_aside else "Agreement"


def _caveats(evaluation: Any, facts: Any, decisions: list[dict], goals: dict) -> tuple[str, ...]:
    out = []
    records = (evaluation.records if evaluation is not None else
               tuple(r for checks in facts.checks.values() for r in checks) if facts is not None else ())
    for record in records:
        if not _performed(record):
            why = ("not applicable" if (record.get("probe") or {}).get("status") == "not_applicable"
                   else "not run")
            target = goals.get(record.get('judges', ''), record.get('judges', ''))
            out.append(f"Check {why}: {record.get('check', 'unnamed')} ({target})")
    for unit, texts in (facts.degradations.items() if facts is not None else ()):
        for text in texts:
            out.append("Nothing was sent: sending is off for now, so every message stays a draft."
                       if text.startswith("shadow:") else
                       f"Part of the work ran in a reduced way ({goals.get(unit, unit)}): {text}")
    out += [f"A person set aside a failed check: {d['reason']}" for d in decisions
            if d["kind"] == "adjudication" and d["decision"] == "set_aside"]
    if evaluation is not None:
        for found in (*evaluation.requirements, *evaluation.verification, *evaluation.limits):
            if found.status == acceptance.UNMEASURED:
                out.append(f"Not established: {found.name} ({found.why})")
            elif found.status == acceptance.UNMET:
                out.append(f"Not met: {found.name} ({found.why})")
    return tuple(dict.fromkeys(out))


def _problems(wf: Any, mission: dict, plan: Any, runs: list[dict], evaluation: Any) -> tuple[Problem, ...]:
    """Each stopped step's Design §23 message from its failure class (E2a), and a result short of what counts as done.
    A cancelled mission says so, with its reason (it shows no answer, fix pass I7)."""
    mid = mission["mission_id"]
    if mission["status"] == "cancelled":
        why = "; ".join(r.removeprefix("cancelled: ") for r in Outcome.from_record(mission["outcome"]).reasons)
        return (Problem("This mission was cancelled", why or "no reason was given",
                        "Nothing more will run, and nothing it made is ready to use.",
                        "start a new mission if the work is still needed."),)
    if plan is None:
        return ()
    status = {u["key"]: u for u in wf.graph.units(mid)}
    last: dict[str, dict] = {}
    count: dict[str, int] = {}
    for run in runs:
        last[run["unit_key"]] = run
        count[run["unit_key"]] = count.get(run["unit_key"], 0) + 1
    out, person, said = [], [], set()
    warned: dict = {}  # learned probes (#187) are deferred with the learning layer
    for spec in plan.units:
        unit, run = status.get(spec.key, {}), last.get(spec.key)
        stopped = unit.get("status") == "blocked" or (mission["status"] in FINISHED and unit.get("status") == "failed")
        if not stopped or spec.key in warned:
            continue
        tries = count.get(spec.key, 0)
        what = ("A person did not approve this step" if spec.kind == "gate" else
                f"This step stopped after {tries} attempt{'' if tries == 1 else 's'} ({spec.goal})")
        step = str(unit.get("reason") or "")
        if step.startswith(PERSON + ": "):  # stopped for a person (E2E Build 6; review I-1, M-1): said once, first
            body = step.removeprefix(PERSON + ": ").rstrip(".")
            why = (NOT_RUN if run is None else _why(outcome_of(run).reasons[:1]) if run["failure_code"] else body)
            if step in said:  # the person's step is already the first line: this one points to it
                person.append(Problem(f"This step stopped ({spec.goal})", why, classes.MATERIAL["UNKNOWN"], SEE_ABOVE,
                                      classes.SURFACE["HUMAN_INPUT_REQUIRED"]))
            else:
                person.insert(len(said), Problem(PERSON, why, classes.MATERIAL["UNKNOWN"],
                                                 RESTART if why == body else body,
                                                 classes.SURFACE["HUMAN_INPUT_REQUIRED"]))
            said.add(step)
            continue
        if run is None or not run["failure_code"]:
            out.append(Problem(f"This step stopped ({spec.goal})", _why([unit.get("reason") or ""])
                               or "no reason was recorded", classes.MATERIAL["UNKNOWN"],
                               classes.resolve("unclassified"), classes.surface_code("unclassified")))
            continue
        out.append(classes.problem(run["failure_code"], _why(outcome_of(run).reasons), what=what))
    out = [*person, *_person(wf, mid, said), *out]
    if evaluation is not None and not evaluation.accepted and not out:  # held back by what counts as done alone
        needed = LABELS.get(evaluation.required_completion_state, ("", "Verified"))[1]
        out.append(Problem("The result does not meet what counts as done",
                           f"it needs a result marked {needed}", "The work done is kept, but "
                           "it is not ready to use as done.", "decide whether to use it as a draft, or start the "
                           "mission again with what the checks need.", "G6_E_RESULT_NOT_VERIFIED"))
    return tuple(out)


def _disputes(wf: Any, mission_id: str, goals: dict) -> tuple[tuple[Interpretation, ...], str]:
    """Checks that disagree with no retry left (V4c), as two interpretations until a person decides (Design §24)."""
    asked = [q for q in wf.runtime.decisions.pending(mission_id) if q["kind"] == "adjudication"]
    if not asked:
        return (), ""
    found = asked[0]["detail"]
    target = goals.get(found.get("verifies", ""), found.get("verifies", ""))
    reasons = _why(found.get("reasons") or []).removeprefix("the check failed: ")[:300]
    readings = (Interpretation("The result holds", f"the check(s) {', '.join(found.get('passed') or [])} passed {target}"),
                Interpretation("The result is wrong", f"the check {found.get('failed')} failed it: {reasons}"))
    unresolved = ("the checks disagree and none can be tried again, so a person decides: python -P -m "
                  f"g6.workforce.decide uphold|set-aside {mission_id} {found.get('failed')} --reason \"...\"")
    return readings, unresolved


def _evidence(wf: Any, mission_id: str, plan: Any) -> tuple[Evidence, ...]:
    """Each output with its provenance label and who supplied it (Design §13, §25; plan ruling P2.24)."""
    if plan is None:
        return ()
    out = []
    runs = {r["run_id"]: r for r in wf.ledger.runs(mission_id)}
    for spec in plan.units:
        if spec.kind != "do":
            continue
        found = wf.ledger.last_artifact(mission_id, spec.key)
        if found is None:
            continue
        passed = any(v["artifact_id"] == found["artifact_id"] and v["result"] == "pass" and v["executed"]
                     for v in wf.ledger.verdicts(mission_id, spec.key))
        source = outcome_of(runs[found["run_id"]]).source if found.get("run_id") in runs else ""
        made = (found.get("provenance") or {}).get("call") or {}
        if not passed:
            out.append(Evidence("Unverified claim", f"{spec.goal}: no check passed it", ""))
        elif source == "human":
            out.append(Evidence("Source", f"{spec.goal}: written by you", "you"))
        elif source == "pull":
            out.append(Evidence("Inference", f"{spec.goal}: written by your chat", "your chat"))
        else:
            contract = wf.caps.contract(made.get("component", ""), made.get("op", "")) if made else None
            free = contract is not None and contract.model_free
            out.append(Evidence("Calculation" if free else "Inference",
                                f"{spec.goal}: computed by a tool, with no model" if free
                                else f"{spec.goal}: produced by a tool that may use a model", "G6"))
        for key, used in ((found.get("provenance") or {}).get("inputs") or {}).items():
            if "reused" in used:
                out.append(Evidence("Source", f"a saved result ({key}) reused for {spec.goal}", "retrieved"))
    for statement in wf.missions.acceptance(mission_id).metadata.get("assumptions") or []:
        out.append(Evidence("Assumption", str(statement), ""))
    return tuple(out)


def _cost(found: economics.CostTotal, pending: economics.CostTotal | None = None) -> str:
    known = economics.format_money(found.known_minor_units, found.currency)
    if found.amount_minor_units is None:
        n = found.unknown_entries + found.other_currency_entries
        shown = f"not known ({known} known; {n} cost{'' if n == 1 else 's'} not measured)"
    else:
        shown = f"{known} (estimated)" if found.summary == "estimated" else known
    if pending is not None and pending.entries:
        reserved = economics.format_money(pending.known_minor_units, pending.currency)
        unknown = "; some reservation amounts are unknown" if pending.amount_minor_units is None else ""
        shown += f"; {reserved} reserved for unresolved calls (not a recorded charge{unknown})"
    return shown


def _attention(found: economics.AttentionTotal) -> str:
    if not found.interventions:
        return "nothing was asked of you (your time in the chat is not measured)"
    n = found.interventions
    return (f"you stepped in {n} time{'' if n == 1 else 's'} "
            f"({'time not measured' if found.seconds is None else f'{found.seconds} s'})")


def _performed(record: dict) -> bool:
    """An observed check, as distinct from a dispatch that found nothing applicable."""
    return bool(record.get("executed")) and (record.get("probe") or {}).get("status") != "not_applicable"


def _tests(records: tuple) -> tuple[int, int] | None:
    counted = [r["measured"] for r in records if _performed(r)
               and r.get("mechanism") == "test_execution" and r.get("measured")]
    if not counted:
        return None
    return sum(int(m["passed"]) for m in counted), sum(int(m["total"]) for m in counted)


__all__ = ["COMPILED", "MORE", "REJECTED", "SAID", "UNVERIFIED", "build", "outputs", "result"]
