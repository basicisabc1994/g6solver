# SPDX-License-Identifier: Apache-2.0
"""coding_learning — opt-in: a probe learned from a regression a coding mission caught and repaired,
which can only ever lower the result of a later mission on the same function of the same repository.

Audit 010 WF-008 (§11 keeps this opt-in). The archive learned a probe (one input of a function the
differential check caught, and the output the baseline gave there), "validated" it on variants it labelled with the
probe's own replay (so a probe that always fires scored as well as one that is right: ``k_new`` was 0 by construction),
and drained its candidates on a daemon thread after every reply, running repository code in the background. Here:

* nothing runs unless the caller calls it: no thread, no drain, no hook in the mission runtime; every call returns
  when its work is done;
* a probe is a ``g6.learning`` ``Theory`` in a ``TheoryStore`` (``store``): ``learn`` and ``propose`` only add
  candidates;
* ``validate`` pairs the decision with and without the probe on variants labelled by a run the probe did not produce
  (an evaluator's, a person's) and lets ``g6.learning.promote`` read the held-out pairs; a label from the probe's own
  replay or from the learner is refused (``SELF_LABELLED``); the best outcome is a recommendation;
* a person accepts that recommendation (``approve``); only an ``active`` probe is selected (``select``);
* ``review`` replays the active probes for a closed coding mission's task on its last patch. A probe that disagrees
  holds the result at ``qualified-draft`` at most and asks a person one question (``answer``: approve says the change
  is intended and retires the probe; reject keeps it); a probe that agrees changes nothing; a probe that could not run
  caps a ``verified`` result at ``qualified-draft``. ``demote`` never raises a state, and the review's own envelope is
  never above ``qualified-draft``.

Limits: the variants and their labels are the caller's, so the evidence is only as independent as the caller made it;
an approver's answer is the caller's own unauthenticated claim (WF-013); a replay runs the repository's code in a
scratch copy as the owner, only for a repository a person allowed (WF-007), and is not an OS sandbox. Not ported: the
plan's ``check-learned-*`` verify units and the runtime's in-mission warning (a probe is applied by ``review`` after
the close), mutant-generated validation variants, prose lessons in a worker's brief, imported learning bundles, the
lease and drain machinery, and the background thread. Nothing here has been shown to catch a regression the frozen
tests miss (ECHA VLE-1 is unmet).
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.core.adaptation_gate import is_automated
from g6.core.reliability_envelope import envelope_fields, warning_card
from g6.learning import HeldOutEvidence, PairedOutcome, PromotionDecision, PromotionPolicy, Theory, TheoryStore, promote
from g6.workforce import bench, coding, patch_checks
from g6.workforce.api import Approver, NotConfirmed
from g6.workforce.contracts import BLOCKED, QUALIFIED, VERIFIED, Outcome
from g6.workforce.decisions import principal
from g6.workforce.missions import FINISHED

#: The failure a probe stands for (the differential check's executed counterexample).
REGRESSION = "VERIFY_REGRESSION"
#: The ``TheoryStore`` scope stem (the owner is appended) and the store's file under the workforce root.
SCOPE, STORE_FILE = "workforce.coding_probe", "coding_learning.db"
#: Seconds one replay may run: one call of one function at one input in a fresh interpreter.
REPLAY_TIMEOUT_S = 20
#: Label sources that are the probe's or the learner's own: never evidence for the probe (WF-008).
SELF_LABELS = frozenset({"probe_replay", "learned_probe", "learner", "g6.coding_learning"})
#: What a probe's replay on a change found.
AGREES, DISAGREES, NOT_RUN = "agrees", "disagrees", "not_run"
#: A learned probe's question to a person on a mission is this unit key, followed by the probe's theory id.
QUESTION_PREFIX = "retire-probe-"
#: Idempotency-key stems of the warning row and the retirement row.
WARNING_KEY, RETIRE_KEY = "coding:learned_probe_warning:", "coding:learned_probe_retired:"

SELF_LABELLED = "G6_E_WORKFORCE_PROBE_SELF_LABELLED"
PROBE_DISAGREES = "G6_E_WORKFORCE_LEARNED_PROBE_DISAGREES"
PROBE_UNMEASURED = "G6_E_WORKFORCE_LEARNED_PROBE_UNMEASURED"
EXPERIMENTAL = "G6_E_WORKFORCE_LEARNING_EXPERIMENTAL"
NOT_CLOSED = "G6_E_WORKFORCE_LEARNING_NOT_CLOSED"

# COPY: The bounded hypothesis a probe states.
HYPOTHESIS = "Keep the output the baseline gave at this input when this function changes."
# COPY: The one question a person answers when an approved learned probe disagrees with a later change.
QUESTION = ("A check G6 learned from an earlier mission expects {function}({input}) to give {expected}; this change "
            "gives {actual}. If the change is intended, approve it and G6 retires that check. If it is not, reject it: "
            "this is the regression the check was learned to catch.")
# COPY: What a person reads before accepting a probe's recommendation.
PROMOTE_LINES = ("Use this learned check on later changes to {module}:{function}? At input {input} it expects {expected}.",
                 "Held-out evidence: {wins} change(s) it caught that the other checks missed, {losses} it flagged "
                 "wrongly, over {pairs} labelled variants (one-sided sign test p={p:.4g}).",
                 "The variants and their labels were supplied to G6; they are only as independent as their source. A "
                 "learned check can only lower a later result, never raise it.")
# COPY: The retirement row a person's approval writes.
RETIRED = ("A person approved this change, so G6 retired its learned check for {function}({input}): it is not used on "
           "later changes to {function}.")
# COPY: Why a replay gave no evidence.
NOT_ALLOWED = "No person allowed G6 to run this repository's code at the mission's revision, so the probe did not run."
MOVED = "The mission's frozen copy of the repository changed, so the replay is not evidence."
NO_VALUE = "The replay gave no value at the probe input: the function raised there, ran too long, or did not run."
NO_PATCH = "The mission kept no patch to replay the probe on."
# COPY: The review's warning-card messages.
SAID = {
    PROBE_DISAGREES: ("{n} approved learned check(s) disagree with this change, so the result is held at qualified-draft "
                      "at most and a person is asked whether the change is intended."),
    PROBE_UNMEASURED: ("{n} approved learned check(s) for this task could not run, so they are unmeasured and the "
                       "result is held at qualified-draft at most."),
    "agree": ("The approved learned checks for this task agree with the change. Their value is unmeasured, "
              "so they add no support: the mission's own result stands."),
    "none": "No approved learned check covers this task, so nothing was replayed; the mission's own result stands.",
}
ACTION = {PROBE_DISAGREES: "Answer the question: approve if the change is intended (the check is retired), reject if not.",
          PROBE_UNMEASURED: "Read the replay's reason; the mission's own checks are unaffected.",
          EXPERIMENTAL: "Read the mission's own result and its evidence; a learned check never vouches for a change."}

_KEY = ("repository_identity", "module", "function")
_PROBE = ("module", "function", "input", "expected")
_RANK = {BLOCKED: 0, QUALIFIED: 1, VERIFIED: 2}
_NOT_COPIED = (".git", ".env*", "__pycache__", ".pytest_cache", "*.pyc")

#: ``replay(probe, files)``: the probe's function called at its input on ``files`` (a change's full new text of its
#: files); returns ``{"executed": bool, "value": ..., "execution_id": str, "reason": str}``.
Replay = Callable[[Mapping[str, Any], Mapping[str, str]], Mapping[str, Any]]


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode("utf-8")).hexdigest()


def files_id(files: Mapping[str, str]) -> str:
    """A change's instance id: each file's syntax tree (its text when it does not parse), so a comment or layout edit of
    a training change is the same instance and never counts as held out."""
    shape = {}
    for path, text in sorted(files.items()):
        try:
            shape[str(path)] = ast.dump(ast.parse(text))
        except (SyntaxError, ValueError, TypeError):
            shape[str(path)] = str(text)
    return "files:" + digest(shape)


def store(wf, *, promotion_policy: PromotionPolicy | None = None) -> TheoryStore:
    """This owner's probe store under the workforce root (close it, or use it as a context manager).
    ``promotion_policy`` is the lowest bar its ``activate`` judges a decision by (default ``PromotionPolicy()``)."""
    return TheoryStore(Path(wf.db_dir) / STORE_FILE, scope=f"{SCOPE}:{wf.missions.owner}",
                       promotion_policy=promotion_policy)


def probe_of(theory: Theory | None) -> dict | None:
    """The key and probe a theory holds, or None when it is not a well-formed coding probe."""
    if theory is None or theory.failure_code != REGRESSION:
        return None
    try:
        key, probe = json.loads(theory.trigger), json.loads(theory.instruction)
    except (TypeError, ValueError):
        return None
    if not (isinstance(key, dict) and set(key) == set(_KEY) and isinstance(probe, dict) and set(probe) == set(_PROBE)
            and (probe["module"], probe["function"]) == (key["module"], key["function"])):
        return None
    return {"theory_id": theory.theory_id, "key": key, "probe": probe, "probe_sha256": digest(probe)}


# -- learning: candidates only --------------------------------------------------------------------------------------------
def propose(probes: TheoryStore, key: Mapping[str, Any], counterexample: Mapping[str, Any], *,
            sources: Sequence[str] = ()) -> Theory | None:
    """A candidate probe from one executed counterexample (``{module, function, input: [x], baseline_output,
    candidate_output}``), keyed to a repository function (``key``: ``repository_identity``, ``module``, ``function``).
    ``sources``: the training instances (``files_id`` of the caught change and of its repair), which held-out evidence
    must avoid. None when the counterexample is not one input with a changed output. The same probe proposed again is
    the stored one, whatever its status: a retired probe is not revived."""
    inputs = counterexample.get("input")
    if not (isinstance(inputs, list) and len(inputs) == 1) or "baseline_output" not in counterexample \
            or _canon(counterexample.get("baseline_output")) == _canon(counterexample.get("candidate_output")):
        return None
    key = {k: str(key[k]) for k in _KEY}
    if (key["module"], key["function"]) != (str(counterexample.get("module")), str(counterexample.get("function"))):
        raise ValueError("the counterexample's function is not the one the key names")
    probe = {"module": key["module"], "function": key["function"], "input": inputs[0],
             "expected": counterexample["baseline_output"]}
    theory_id = "probe_" + digest([key, probe])[:24]
    found = probes.get(theory_id)
    if found is not None:
        return found
    theory = Theory(theory_id=theory_id, failure_code=REGRESSION, instruction=_canon(probe), trigger=_canon(key),
                    rationale=HYPOTHESIS, source_instances=list(dict.fromkeys(sources)))
    return probes.add_candidates([theory])[0]


def learn(wf, mission_id: str, probes: TheoryStore) -> list[Theory]:
    """The candidates one closed coding mission gives (audit 010 WF-008: called by the caller, at once; never by a
    thread): each executed counterexample of a differential check that failed a change the mission then repaired. []
    for a mission that is open, is not a coding mission, or repaired nothing. Runs no repository code."""
    standard = coding.frozen(wf, mission_id)
    if not standard or wf.missions.get(mission_id)["status"] not in FINISHED or not coding.repaired(wf, mission_id):
        return []
    repair = wf.ledger.last_artifact(mission_id, "patch")
    found: dict[str, Theory] = {}
    for check in coding.status(wf, mission_id, standard)["checks"]:
        if check["check"] != "check-regression" or check["result"] != "fail" or not check["executed"]:
            continue
        failed = wf.ledger.artifact(mission_id, check["artifact_id"])
        sources = [files_id(failed["content"]["files"]), files_id(repair["content"]["files"])]
        probe = check.get("probe") or {}
        for counterexample in probe.get("counterexamples") or []:
            ran = next((f for f in probe.get("functions") or [] if (f.get("module"), f.get("function"))
                        == (counterexample.get("module"), counterexample.get("function"))), {})
            if not all((ran.get(side) or {}).get("executed") for side in ("baseline", "candidate")):
                continue
            key = {"repository_identity": standard["task_signature"]["repository_identity"],
                   "module": counterexample["module"], "function": counterexample["function"]}
            theory = propose(probes, key, counterexample, sources=sources)
            if theory is not None:
                found.setdefault(theory.theory_id, theory)
    return list(found.values())


# -- validation: labels from outside the probe ----------------------------------------------------------------------------
@dataclass(frozen=True)
class LabelledVariant:
    """A change to validate a probe on, and its label from a run the probe did not produce.

    ``files``: the change's full new text of its files, on the probe's mission's frozen repository. ``regression``: the
    label (True when the change breaks the behaviour the probe protects). ``label_source``: where the label came from,
    ``{"kind": ..., "run_id": ...}`` (an evaluator's run, a person's review); never the probe's replay or the learner.
    ``others_flagged``: whether the mission's other checks already flag the change (so the probe adds nothing there).
    """

    files: Mapping[str, str]
    regression: bool
    label_source: Mapping[str, str]
    others_flagged: bool = False

    @property
    def instance_id(self) -> str:
        return files_id(self.files)


def _label_problem(source: Mapping[str, Any] | None, produced: set[str]) -> str:
    """Why a label cannot count for a probe ("" when it can): audit 010 WF-008, the archive labelled each variant with
    the probe's own replay, so the trial could not tell a probe that is right from one that always fires."""
    kind = str((source or {}).get("kind") or "").strip()
    run = str((source or {}).get("run_id") or "").strip()
    if not kind or not run:
        return "its label names no run that produced it"
    if kind in SELF_LABELS:
        return f"its label comes from {kind!r}, the probe's or the learner's own"
    if run in produced:
        return "its label is the probe's own replay"
    return ""


def validate(probes: TheoryStore, theory_id: str, variants: Sequence[LabelledVariant], replay: Replay, *,
             policy: PromotionPolicy | None = None, run_id: str = "") -> PromotionDecision:
    """Whether a candidate probe may be recommended, from held-out variants labelled outside it (WF-008).

    Each variant is replayed (``replay``, e.g. ``replayer(wf, mission_id)``) and paired: the decision with the probe
    (flagged when the other checks or the probe flag it) and without it (flagged when the other checks do), each right
    when it matches the label. ``g6.learning.promote`` reads the pairs: held out from the probe's training changes,
    enough of them, a positive sign test. Any variant labelled by the probe or the learner refuses the whole trial
    (``SELF_LABELLED``); a variant that gave no value at the probe input is skipped and counted. Nothing is activated
    here: a person accepts a recommendation with ``approve``."""
    theory = probes.get(theory_id)
    found = probe_of(theory)
    if found is None:
        raise KeyError(f"G6_E_LEARNING_UNKNOWN_THEORY: {theory_id!r} is not a coding probe in this store")
    probe = found["probe"]
    # A label named as the probe's or the learner's own is refused before anything runs; one whose run is a replay of
    # the probe made here is refused once the replays name their runs.
    refused = [f"{v.instance_id}: {why}" for v in variants if (why := _label_problem(v.label_source, set()))]
    pairs, skipped = [], 0
    if not refused:
        replays = [dict(replay(probe, dict(v.files))) for v in variants]
        produced = {str(r.get("execution_id") or "") for r in replays} - {""}
        refused = [f"{v.instance_id}: {why}" for v in variants if (why := _label_problem(v.label_source, produced))]
    if not refused:
        for variant, result in zip(variants, replays, strict=True):
            if not result.get("executed"):
                skipped += 1
                continue
            fires = _canon(result.get("value")) != _canon(probe["expected"])
            truth = bool(variant.regression)
            pairs.append(PairedOutcome(variant.instance_id, with_theory=(variant.others_flagged or fires) == truth,
                                       without_theory=bool(variant.others_flagged) == truth))
    evidence = HeldOutEvidence(theory.theory_id, theory.digest, tuple(pairs), frozenset(theory.source_instances),
                               run_id=run_id)
    decision = promote(theory, evidence, policy)
    if refused:
        return dataclasses.replace(decision, recommendation="refuse", code=SELF_LABELLED, reasons=(
            f"{SELF_LABELLED}: {len(refused)} variant(s) are labelled by the probe or the learner, never by a run the "
            "probe did not produce, so the trial cannot show the probe is right: " + "; ".join(refused[:3]),))
    if skipped:
        return dataclasses.replace(decision, reasons=(
            *decision.reasons, f"{skipped} variant(s) gave no value at the probe input and were not counted"))
    return decision


def approve(probes: TheoryStore, decision: PromotionDecision, approver: Approver) -> Theory:
    """A person accepts a probe's recommendation: ``approver`` reads what the probe checks and the evidence behind it,
    and returns who decides (``NotConfirmed`` when it gives none). Only then is the probe ``active``. A decision that
    does not recommend the probe is refused before anyone is asked."""
    found = probe_of(probes.get(decision.theory_id))
    if found is None:
        raise KeyError(f"G6_E_LEARNING_UNKNOWN_THEORY: {decision.theory_id!r} is not a coding probe in this store")
    if decision.recommendation != "promote":
        raise ValueError(f"G6_E_LEARNING_NOT_RECOMMENDED: {decision.code}: the probe is not recommended, so no person "
                         "is asked")
    probe = found["probe"]
    lines = [line.format(module=probe["module"], function=probe["function"], input=_canon(probe["input"]),
                         expected=_canon(probe["expected"]), wins=decision.wins, losses=decision.losses,
                         pairs=decision.pairs, p=decision.p_one_sided) for line in PROMOTE_LINES]
    who = approver(lines)
    if not isinstance(who, str) or not who.strip():
        raise NotConfirmed("the approver did not accept the learned check; it stays a candidate")
    if is_automated(who):  # checked before the prefix, which would hide an auto: name from the store's own check
        raise NotConfirmed(f"{who.strip()!r} is an automated (auto:) name, not a person; the learned check stays a "
                           "candidate")
    return probes.activate(decision, accepted_by=f"approver:{who.strip()}")


def select(probes: TheoryStore, task_signature: Mapping[str, Any]) -> list[dict]:
    """The active (person-approved) probes for exactly this repository function; a candidate or retired one never."""
    key = {k: str(task_signature.get(k) or "") for k in _KEY}
    found = [probe_of(t) for t in probes.theories("active")]
    return sorted((f for f in found if f is not None and f["key"] == key), key=lambda f: f["theory_id"])


# -- applying an approved probe to a later mission ------------------------------------------------------------------------
def demote(state: str, findings: Sequence[Mapping[str, Any]]) -> str:
    """The mission's state as its learned probes read it (audit 010 WF-008): a probe that disagrees, or could not run,
    holds a ``verified`` result at ``qualified-draft``; nothing else changes. Never higher than ``state``."""
    if state not in _RANK:
        raise ValueError(f"{state!r} is not a completion state")
    lowers = any(f.get("status") in (DISAGREES, NOT_RUN) for f in findings)
    return QUALIFIED if lowers and _RANK[state] > _RANK[QUALIFIED] else state


def _allowed(wf, standard: Mapping[str, Any]) -> bool:
    return any(e["detail"].get("repository") == standard["repository"]
               and e["detail"].get("revision") == standard["baseline_revision"]
               for e in wf.missions.scope.evidence(wf.venture_id, kind=coding.EXECUTION_ALLOWED, limit=100_000))


def _replay(wf, standard: Mapping[str, Any], probe: Mapping[str, Any], files: Mapping[str, str]) -> dict:
    out = {"executed": False, "value": None, "execution_id": "", "reason": ""}
    if not _allowed(wf, standard):
        return {**out, "reason": NOT_ALLOWED}
    try:
        source = bench._repo(standard["snapshot"], wf.venture_id)
        checked = bench._files(dict(files))
    except ValueError as exc:
        return {**out, "reason": str(exc)}
    if source is None or bench._digest(source) != standard["baseline_digest"]:
        return {**out, "reason": MOVED}
    with tempfile.TemporaryDirectory(prefix="g6_learned_probe_") as directory:
        scratch = Path(directory)
        copy = scratch / "repo"
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns(*_NOT_COPIED))
        bench._write(copy, checked)
        values, receipt = patch_checks._observe(copy, probe["module"], probe["function"], [probe["input"]], scratch,
                                                timeout=REPLAY_TIMEOUT_S)
    if bench._digest(source) != standard["baseline_digest"]:
        return {**out, "execution_id": receipt["execution_id"], "reason": MOVED}
    if values is None:
        return {**out, "execution_id": receipt["execution_id"], "reason": NO_VALUE, "receipt": receipt}
    return {"executed": True, "value": values[0], "execution_id": receipt["execution_id"], "reason": "",
            "receipt": receipt}


def replayer(wf, mission_id: str) -> Replay:
    """A replay of a probe on a change to this coding mission's frozen repository copy (runs its code; see limits)."""
    standard = coding.frozen(wf, mission_id)
    if not standard:
        raise ValueError(f"mission {mission_id} has no frozen coding standard to replay a probe on")
    return lambda probe, files: _replay(wf, standard, probe, files)


def review(wf, mission_id: str, probes: TheoryStore, *, replay: Replay | None = None, request_id: str = "") -> dict:
    """Replay the approved probes for a closed coding mission's task on its last patch, and say what they do to its
    result (see the module docstring). A disagreement records a warning on the mission and asks a person one question
    (``awaiting_human`` in its status; ``answer``). Returns an envelope whose ``completion_state`` is never above
    ``qualified-draft``, with ``mission_state`` (as it closed), ``state_with_probes`` (``demote``) and ``lowered``."""
    mission = wf.missions.get(mission_id)
    if mission["status"] not in FINISHED:
        raise ValueError(f"{NOT_CLOSED}: learned probes are reviewed on a closed mission's result; mission "
                         f"{mission_id} is {mission['status']}")
    standard = coding.frozen(wf, mission_id)
    if not standard:
        raise ValueError(f"mission {mission_id} is not a coding mission: no learned probe applies to it")
    state = Outcome.from_record(mission["outcome"]).state
    patch = wf.ledger.last_artifact(mission_id, "patch")
    replay = replay or replayer(wf, mission_id)
    findings = []
    for chosen in select(probes, standard["task_signature"]):
        probe = chosen["probe"]
        result = dict(replay(probe, patch["content"]["files"])) if patch else {"executed": False, "reason": NO_PATCH}
        status = (NOT_RUN if not result.get("executed")
                  else AGREES if _canon(result.get("value")) == _canon(probe["expected"]) else DISAGREES)
        findings.append({"theory_id": chosen["theory_id"], "probe_sha256": chosen["probe_sha256"],
                         **{k: probe[k] for k in _PROBE}, "actual": result.get("value") if result.get("executed") else None,
                         "status": status, "execution_id": str(result.get("execution_id") or ""),
                         "reason": str(result.get("reason") or "")})
    lowered = demote(state, findings)
    questions = [_ask(wf, mission_id, patch, f) for f in findings if f["status"] == DISAGREES]
    disagreeing = sum(f["status"] == DISAGREES for f in findings)
    unrun = sum(f["status"] == NOT_RUN for f in findings)
    if disagreeing:
        code, message = PROBE_DISAGREES, SAID[PROBE_DISAGREES].format(n=disagreeing)
    elif unrun:
        code, message = PROBE_UNMEASURED, SAID[PROBE_UNMEASURED].format(n=unrun)
    else:
        code, message = EXPERIMENTAL, SAID["agree" if findings else "none"]
    card = warning_card(code, message, ACTION[code])
    evidence = [{"kind": "learned_probe_replay", "theory_id": f["theory_id"], "status": f["status"],
                 "execution_id": f["execution_id"], "expected": f["expected"], "actual": f["actual"],
                 "reason": f["reason"]} for f in findings]
    completion = QUALIFIED if lowered == VERIFIED else lowered  # a learned check never vouches for a change
    return {"mission_id": mission_id, "mission_state": state, "state_with_probes": lowered, "lowered": lowered != state,
            "probes": findings, "questions": questions,
            **envelope_fields(reliability_label=completion, warning_card=card, evidence=evidence,  # type: ignore[arg-type]
                              request_id=request_id, run_id=str((patch or {}).get("run_id") or ""),
                              degradation_reason=message)}


def _ask(wf, mission_id: str, patch: Mapping[str, Any], finding: Mapping[str, Any]) -> dict:
    unit = QUESTION_PREFIX + finding["theory_id"]
    message = QUESTION.format(function=finding["function"], input=_canon(finding["input"]),
                              expected=_canon(finding["expected"]), actual=_canon(finding["actual"]))
    bound = {"mission_id": mission_id, "check": unit, "theory_id": finding["theory_id"],
             "probe_sha256": finding["probe_sha256"], "artifact_id": patch["artifact_id"]}
    card = {**finding, "check": unit, "artifact_id": patch["artifact_id"], "message": message}
    wf.missions.record_event(mission_id, "message", actor="code", detail={
        "from": unit, "to": "person", "text": message, "refs": {"learned_probe_warning": card}},
        idem_key=f"{WARNING_KEY}{patch['artifact_id']}:{unit}")
    wf.runtime.decisions.request(mission_id, unit, "gate", question=message, detail={"retires": bound, "card": card},
                                 binding=bound)
    return {"unit": unit, "kind": "gate", "question": message, "answers": ["approve", "reject"]}


def answer(wf, probes: TheoryStore, mission_id: str, unit_key: str, decision: str, approver: Approver, *,
           reason: str = "") -> dict | None:
    """A person's answer to a learned probe's question: ``approve`` (the change is intended: the probe is retired) or
    ``reject`` (it is a regression: the probe stays). Recorded under the identity ``approver`` returns, with its
    principal (the caller's own, unauthenticated claim). Returns the retirement row, or None when nothing was retired.
    The closed mission's result does not change."""
    asked = next((q for q in wf.runtime.decisions.requested(mission_id)
                  if q["unit"] == unit_key and q["detail"].get("retires")), None)
    if asked is None:
        raise ValueError(f"mission {mission_id} asked no learned-probe question {unit_key!r}")
    who = approver([asked["question"]])
    if not isinstance(who, str) or not who.strip():
        raise NotConfirmed("the approver gave no decision; nothing was recorded")
    identity = f"approver:{who.strip()}"
    wf.runtime.decide(mission_id, unit_key, decision, reason=reason or decision, actor="human", decided_by=identity,
                      principal=principal(identity, channel="approver"))
    return retire(wf, probes, mission_id, unit_key)


def retire(wf, probes: TheoryStore, mission_id: str, unit_key: str) -> dict | None:
    """Retire the probe a recorded approval names: only an approval of this mission's question, bound to the exact probe
    it asked about, retires anything; a rejection, a stale or a mismatched answer retires nothing (None)."""
    decided = wf.runtime.decisions.get(mission_id, unit_key, "gate")
    asked = next((q for q in wf.runtime.decisions.requested(mission_id)
                  if q["unit"] == unit_key and q["detail"].get("retires")), None)
    if not decided or decided["decision"] != "approve" or asked is None:
        return None
    bound = asked["detail"]["retires"]
    if (wf.runtime.decisions.binding(mission_id, decided["decision_id"]) != bound
            or (bound.get("mission_id"), bound.get("check")) != (mission_id, unit_key)):
        return None
    theory = probes.get(str(bound.get("theory_id") or ""))
    found = probe_of(theory)
    if found is None or theory.status != "active" or found["probe_sha256"] != bound.get("probe_sha256"):
        return None
    probes.retire(theory.theory_id)
    row = {**bound, "decision_id": decided["decision_id"], "decided_at": decided["decided_at"]}
    probe = found["probe"]
    wf.missions.record_event(mission_id, "message", actor="code", detail={
        "from": "code", "to": "learning", "text": RETIRED.format(function=probe["function"], input=_canon(probe["input"])),
        "refs": {"learned_probe_retired": row}}, idem_key=f"{RETIRE_KEY}{decided['decision_id']}")
    return row


__all__ = ["AGREES", "DISAGREES", "EXPERIMENTAL", "NOT_RUN", "PROBE_DISAGREES", "PROBE_UNMEASURED", "REGRESSION",
           "SELF_LABELLED", "SELF_LABELS", "LabelledVariant", "Replay", "answer", "approve", "demote", "files_id", "learn",
           "probe_of", "propose", "replayer", "retire", "review", "select", "store", "validate"]
