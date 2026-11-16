# SPDX-License-Identifier: Apache-2.0
"""The staged selector behind ``g6_select`` (0.3 design 3.5; plan revision 8, phase B).

G6 needs no model to select: the calling agent is the semantic engine. Given the agent's situation, G6 returns a short
ranked list of next calls, each with its exact call template, what it establishes and what it needs, checked before
the agent makes it; the agent chooses, and the door checks the call it makes. A refusal passed back as
``last_outcome`` reroutes. Pure and deterministic: it reads G6's own corpus (``g6.skills.corpus``) and the agent's
arguments, runs nothing, calls no model and reads no repository. A fact about the host (whether an op is allowed,
whether a grant exists, whether a model is configured, whether a run can be confined) is never reported: it is unknown
until observed, and the refusal that observes it is what the agent passes back.

The stages, each reported in ``mechanisms`` as run or skipped, with why:

1. ``facts``: what the agent supplied; each candidate carries its static needs.
2. ``model_check``: in plan, implement or verify with no stated requirement, ``g6_premortem`` and
   ``g6_confirm_behaviour`` are raised (``RAISE_BONUS``); each ``considering`` entry is validated.
3. ``candidates``: a log-IDF word overlap over the corpus (``discovery._tokens``, the scoring ``g6_find`` uses), as
   a share of a full match, times ``LEXICAL_WEIGHT`` (``ROUTE_WEIGHT`` more for a route), plus the fixed phase priors ``PRIORS`` (and ``PHASE_TAG_BONUS`` for a matched entry
   tagged with the phase). Where the rankers disagree it is listed. A semantic rerank (the local ColBERT rung) is
   deferred: it would make the reply depend on what the host has installed and cached, so ``rerank="colbert"`` is
   kept for compatibility and answered lexically (``NOT_OFFERED``).
4. ``next_action``: with open hypotheses in ``considering``, ``verification_controller.next_experiment`` picks the
   call with the most severity-weighted open-hypothesis coverage per cost unit (``COST_UNITS``) within
   ``BUDGET_UNITS``; otherwise the first candidate.
5. ``reroute``: ``REMEDIES`` by refusal code or outcome, and ``g6.metaharness.recovery``'s remedies by component.
6. ``validation``: a tool template's keys are within its recorded schema and hold its required keys; an op call
   goes through ``g6.navigator.check_plan``. Every issue is attached to its candidate.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from g6.core.discovery import _tokens

STAGES = ("facts", "model_check", "candidates", "next_action", "reroute", "validation")
#: ``lexical`` only ranks; ``colbert`` is kept so a call that names it is not broken, and is answered lexically.
RERANKS = ("lexical", "colbert")
NOT_OFFERED = ("not offered here: a semantic rerank would depend on what this machine has installed and cached, "
               "which this reply never says; ranked lexically")
MAX_CANDIDATES = 8
LEXICAL_WEIGHT = 6.0        # an entry holding every query word the corpus has scores this much; others in proportion
ROUTE_WEIGHT = 0.5          # a route is an instruction to read, so its words count half beside the calls it names
PHASE_TAG_BONUS = 0.5       # a matched entry tagged with the phase
RAISE_BONUS = 1.0           # stage 2's raise
CONSIDERED_BONUS = 2.0      # a call the agent is considering, once it validates
COST_UNITS = {"low": 1, "medium": 2, "high": 4}
BUDGET_UNITS = 4
RAISED = ("tool:g6_premortem", "tool:g6_confirm_behaviour")
#: Fixed, documented priors by phase: entry id -> bonus. Read by a reviewer; a test keeps every id in the corpus.
PRIORS: dict[str, dict[str, float]] = {
    "plan": {"tool:g6_premortem": 3.0, "tool:g6_confirm_behaviour": 2.5, "tool:g6_mission_create": 1.0,
             "tool:g6_knowledge_context": 1.0, "op:complexity.impact": 1.0},
    "implement": {"tool:g6_confirm_behaviour": 2.0, "tool:g6_describe": 1.5, "tool:g6_knowledge_search": 1.0,
                  "op:swe_diagnostics.code_summarize": 1.0, "op:action_gating.evaluate": 1.0},
    "verify": {"tool:g6_review_patch": 3.0, "tool:g6_review_until_clean": 2.5, "tool:g6_mission_submit": 1.5,
               "op:coding_agent_gate.verify_answer": 1.5, "op:grounding.check_claims": 1.0},
    "diagnose": {"tool:g6_review_patch": 2.0, "op:swe_diagnostics.test_analyze": 2.0, "tool:g6_premortem": 1.5,
                 "op:cognitive_bridge.rank": 1.5, "op:swe_diagnostics.detect_build_env": 1.0,
                 "tool:metaharness_diagnose": 1.0},
    "stuck": {"tool:g6_premortem": 2.0, "tool:g6_confirm_behaviour": 2.0, "tool:g6_review_until_clean": 2.0,
              "tool:g6_knowledge_search": 1.0, "tool:g6_find": 1.0},
}
#: Tools a remedy may name before they are registered (built on another branch): such a remedy is marked
#: ``available: false`` until the corpus holds the tool, and is never offered as a call.
PENDING_TOOLS = {"g6_agentic_run": "the override kernel's tool (plan revision 8, phase D), built on its own branch"}
UNKNOWN_UNTIL_OBSERVED = (
    "whether this server has the sensitive op allowed: only a G6_E_SENSITIVE_COMPONENT_DENIED refusal says it is not",
    "whether a sharing grant exists for a source: only a withheld reply (G6_E_CLOUD_CONSENT_REQUIRED) says it does not",
    "whether a model is configured for G6: only a *_NO_MODEL or G6_E_LLM_DISABLED refusal says it is not",
    "whether this machine can confine a run: only G6_E_ISOLATION_UNAVAILABLE says it cannot",
)
#: Check-plan codes that concern a plan navigator runs on its own, not a direct ``g6_invoke`` the agent makes: kept
#: in a candidate's issues, they do not make it invalid. A sensitive op's allowance is in its needs.
_PLAN_ONLY = frozenset({"G6_E_OP_NOT_CLEARED", "G6_E_OP_ACTS_BELOW_A2", "G6_E_NAV_EFFECT_OVER_BUDGET",
                        "G6_E_SENSITIVE_COMPONENT_DENIED"})
_CODE = re.compile(r"G6_E_[A-Z0-9_]+")
_PAIR = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)(?![\w.])")
_COMPONENT_CODE = re.compile(r"\[([A-Z][A-Z0-9_]*)_ERROR\]")


def _remedy(action: str, tool: str = "", arguments: dict[str, Any] | None = None, *, tell: bool = False,
            setup: str = "") -> dict[str, Any]:
    return {"action": action, "tool": tool, "arguments": dict(arguments or {}), "tell_person": tell,
            "setup": setup, "available": tool not in PENDING_TOOLS}


_ASK = "Ask the person; offer no other way to agree, and never answer for them."
_SHOW = "Show the reply's report to the person as it is."
#: Remedies by refusal code (exact), code family (a key ending ``_``), ``*_NO_MODEL``, outcome word, or
#: ``component_limit`` (a code a component declares in ``__g6_limits__``). Each names a registered tool or op, or
#: tells the person; a test holds every code the MCP surface and the door emit to one.
REMEDIES: dict[str, dict[str, Any]] = {
    # consent and dialogs
    "G6_E_CLOUD_CONSENT_REQUIRED": _remedy(
        "The reply was withheld until the person shares the source with your provider. Call g6_cloud_grant: the "
        "person answers in the client's dialog. Never grant for the person; if they decline, go on with local work.",
        "g6_cloud_grant", {"source": "<the source the refusal names>", "provider": "<your client's provider>"}),
    "G6_E_CLOUD_CONSENT": _remedy("Read which sources are shared with which providers.", "g6_cloud_grants"),
    "G6_E_CLOUD_CONSENT_INPUT": _remedy(
        "The grant's source or provider was not one G6 takes: name the provider as the refusal lists them.",
        "g6_cloud_grant", {"source": "<source>", "provider": "<your client's provider>"}),
    "G6_E_CLOUD_CONSENT_STATE": _remedy(
        "G6 could not read its sharing state, so nothing was shared. Tell the person.", tell=True),
    "G6_E_CONSENT_": _remedy("The person declined or cancelled, gave no name, or the client has no dialog. " + _ASK,
                             tell=True),
    # the door and the registry beneath it
    "G6_E_BLOCKED": _remedy("Read the warning card: it says why and what can be done. Tell the person.", tell=True),
    "G6_E_INPUT_VALIDATION": _remedy(
        "The input did not fit: the refusal names the field. For an op, read g6_describe; for a tool, its "
        "description. Fix the field and call again.", "g6_describe", {"component": "<component>"}),
    "G6_E_INVOKE_FAILED": _remedy("The op failed while it ran: read g6_describe, check the inputs, and say so if it "
                                  "fails again.", "g6_describe", {"component": "<component>"}),
    "G6_E_RESOURCE_LIMIT": _remedy("The input was over a resource limit: read g6_describe and narrow it.",
                                   "g6_describe", {"component": "<component>"}),
    "G6_E_UNKNOWN_COMPONENT": _remedy("No component has that name: find one by the task.", "g6_find",
                                      {"query": "<the task>"}),
    "G6_E_NO_BLOCK": _remedy("That component is a library with no block, so g6_invoke cannot run it: pick a runnable "
                             "call for the task.", "g6_select", {"task": "<the task>"}),
    "G6_E_SCREEN_RAW_FAILED": _remedy("The component's own input screen failed: tell the person; it is G6's fault.",
                                      tell=True),
    "G6_E_PROMOTION_LAW": _remedy("The result is held at qualified-draft: report it so, never as verified.",
                                  tell=True),
    "G6_E_QUALIFIED_DRAFT": _remedy("A proposal or draft: report it with its warning card; nothing in it is checked.",
                                    tell=True),
    "G6_E_SENSITIVE_COMPONENT_DENIED": _remedy(
        "This op runs a program or changes the machine, and this server does not allow it. Only the person can, "
        "where the server is registered: tell them the step, and what code it lets run. Never change it yourself.",
        tell=True, setup="g6 setup --allow <component.op>"),
    "G6_E_ISOLATION_UNAVAILABLE": _remedy(
        "No operating-system boundary can confine the run here. Tell the person: g6 doctor says what this computer "
        "needs. Never allow an unconfined run yourself.", tell=True, setup="g6 doctor"),
    "G6_E_LLM_DISABLED": _remedy(
        "No model is configured for G6. Do the step yourself, then ask for the op that checks it.", "g6_select",
        {"task": "<the step you did yourself>", "phase": "verify"}),
    "*_NO_MODEL": _remedy(
        "No model is configured for this op. Do the step yourself, then ask for the op that checks it.", "g6_select",
        {"task": "<the step you did yourself>", "phase": "verify"}),
    "component_limit": _remedy(
        "The component refused at a limit it declares. The override kernel can try a substitute for that part, "
        "with the person's agreement, beside the original refusal.", "g6_agentic_run",
        {"component": "<component>", "op": "<op>", "params_json": "<the same parameters>"}),
    # missions
    "G6_E_GIT_COMMAND_FAILED": _remedy("git failed on the repository: tell the person what the refusal says.",
                                       tell=True),
    "G6_E_GIT_REPO_DIRTY": _remedy("A mission starts on a clean repository: commit or set aside the changes, then "
                                   "start it again.", "g6_mission_create", {"repo": "<repo>", "objective": "<objective>"}),
    "G6_E_HUMAN_APPROVAL_REQUIRED": _remedy("Only the person can approve this. " + _ASK, tell=True),
    "G6_E_JOB_FAILED": _remedy("The job failed: read the mission's state and what it waits for.",
                               "g6_mission_status", {"mission_id": "<mission_id>"}),
    "G6_E_MISSION_NO_CHANGE": _remedy("Change the one permitted file in the working tree, then submit again.",
                                      "g6_mission_submit", {"mission_id": "<mission_id>", "repo": "<repo>"}),
    "G6_E_MISSION_OUT_OF_SCOPE": _remedy("Change only the permitted file, never a test, then submit again.",
                                         "g6_mission_submit", {"mission_id": "<mission_id>", "repo": "<repo>"}),
    "G6_E_MISSION_REFUSED": _remedy("The mission refused this step: tell the person what it says.", tell=True),
    "G6_E_MISSION_": _remedy("Read the mission's state and what it waits for.", "g6_mission_status",
                             {"mission_id": "<mission_id>"}),
    # the Context Engine
    "G6_E_KNOWLEDGE": _remedy("Read which sources are indexed and shared, then search again.", "g6_knowledge_sources"),
    "G6_E_KNOWLEDGE_": _remedy("Read which sources are indexed and shared, then search again.", "g6_knowledge_sources"),
    # the review and its loop
    "G6_E_PATCH_REVIEW_INTERRUPTED": _remedy("The review was interrupted: start it again.", "g6_review_patch",
                                             {"repo": "<repo>", "task": "<task>"}),
    "G6_E_REVIEW_UNTIL_CLEAN": _remedy(_SHOW, tell=True),
    "G6_E_NO_CLEAN_REVIEW": _remedy("The loop stopped with no clean review. " + _SHOW, tell=True),
    "G6_E_STOPPED": _remedy("The review stopped: " + _SHOW, tell=True),
    "G6_E_LOOP_BUSY": _remedy("Another step of this session is running: wait for its result, then call again.",
                              "metaharness_job", {"job_id": "<job_id>"}),
    # the self-improvement loop
    "G6_E_LOOP_NOT_ENROLLED": _remedy("No domain is enrolled: only the person enrols one, in the client's dialog.",
                                      "g6_improve_enrol"),
    "G6_E_LOOP_PAUSED": _remedy("The loop is paused: the person reads why and may lift it in the dialog.",
                                "g6_improve_resume"),
    "G6_E_LOOP_": _remedy("Read where the self-improvement loop stands and why.", "g6_improve_status"),
    # behaviour questions
    "G6_E_QUESTION_CONFIRMED": _remedy("The person's answer is enforced on each later review of the task: review it.",
                                       "g6_review_patch", {"repo": "<repo>", "task": "<task>"}),
    "G6_E_QUESTION_REVIEW_NOT_BOUND": _remedy("The review job is not of this repository, task and working tree: run a "
                                              "review of it first.", "g6_review_patch",
                                              {"repo": "<repo>", "task": "<task>"}),
    "G6_E_SCENARIO_UNAVAILABLE": _remedy("Give the question a concrete candidate with two competing outcomes.",
                                         "g6_confirm_behaviour", {"repo": "<repo>", "task": "<task>"}),
    "G6_E_QUESTION_": _remedy("The question is held, unanswered or over the person's budget. " + _ASK, tell=True),
    # the meta-harness
    "G6_E_META_SCHEMA": _remedy("The workflow's specification did not fit: fix the field the refusal names.",
                                "metaharness_start", {"specification_json": "<specification_json>"}),
    "G6_E_META_": _remedy("Read the workflow's record and its recovery.", "metaharness_inspect",
                          {"workflow": "<workflow>"}),
    # the workflow record
    "G6_E_WORKFLOW_NOT_ADMITTED": _remedy("G6's workflow record is off or this call is not one it keeps: tell the "
                                          "person, who may set G6_WORKFLOW_RECORD=on.", tell=True),
    "G6_E_WORKFLOW_RECORD": _remedy("Look at what G6 recorded, in a file the person opens.", "g6_workflow_export",
                                    {"repo": "<repo>"}),
    # patch-review outcomes
    "test_integrity_compromised": _remedy(
        "A sign the tests cannot be believed. Do not weaken a test to pass; " + _SHOW, tell=True),
    "defect_detected": _remedy(
        "Repair the cause of what G6 reproduced, add each reproduction as a stated check, and review again; stop "
        "after 3 rounds.", "g6_review_patch", {"repo": "<repo>", "task": "<task>"}),
    "insufficient_evidence": _remedy(
        "State each requirement with a check that fails at the base commit, or ask the person where the task does "
        "not decide, then review again.", "g6_review_patch", {"repo": "<repo>", "task": "<task>"}),
    "supported_within_scope": _remedy(
        "Support on the stated checks only. " + _SHOW + " Merging needs the person's approval.", tell=True),
    # loop endings and actions
    "clean": _remedy("The review reproduced nothing; that is not evidence the work is right. " + _SHOW, tell=True),
    "fixed": _remedy(_SHOW, tell=True),
    "contested": _remedy("You and the reviewer disagree on a finding: " + _SHOW, tell=True),
    "open": _remedy("A finding is still open: " + _SHOW, tell=True),
    "untested": _remedy("Nothing was tested (no grant, no boundary or no critic): " + _SHOW, tell=True),
    "continue": _remedy("Check the finding against the task, then repair the cause and call again, or keep the code "
                        "and call again with a one-line reason.", "g6_review_until_clean",
                        {"repo": "<repo>", "task": "<task>", "session": "<session>"}),
    "wait": _remedy("Another step is running: read its result before calling again.", "metaharness_job",
                    {"job_id": "<job_id>"}),
    "withheld": _remedy("The reply was withheld for consent. Call g6_cloud_grant; never grant for the person.",
                        "g6_cloud_grant", {"source": "<the source the refusal names>",
                                           "provider": "<your client's provider>"}),
    # the meta-harness recovery's causes
    "wrong_answer": _remedy("The workflow's answers are wrong: diagnose, then propose a candidate that uses one of "
                            "the components in recovery.", "metaharness_diagnose", {"workflow": "<workflow>"}),
    "no_sign_of_gain": _remedy("No sign of a gain: diagnose the dev failures.", "metaharness_diagnose",
                               {"workflow": "<workflow>"}),
    "search_exhausted": _remedy("The search proposed nothing new: diagnose, or write a candidate.",
                                "metaharness_diagnose", {"workflow": "<workflow>"}),
    "proposer_failed": _remedy("The proposer failed: diagnose the dev failures.", "metaharness_diagnose",
                               {"workflow": "<workflow>"}),
    "more_compute": _remedy("The gain was the extra compute: propose a cheaper variant.", "metaharness_propose",
                            {"workflow": "<workflow>"}),
    "underpowered": _remedy("Too few held-out cases to confirm a gain: only the person adds fresh ones.", tell=True),
    "suite_cannot_discriminate": _remedy("The checks cannot tell answers apart: only the person fixes them.",
                                         tell=True),
    "held_out_spent": _remedy("The held-out split is spent: only the person freezes a fresh one.", tell=True),
    "needs_a_person": _remedy("Only the person can resolve this. " + _ASK, tell=True),
}


def _corpus() -> dict[str, Any]:
    from g6.skills import corpus

    return corpus.load()


def _registered(data: dict[str, Any]) -> set[str]:
    return {e["call"]["tool"] for e in data["entries"] if e["kind"] == "tool"} | {
        e["id"][3:] for e in data["entries"] if e["kind"] == "op"}


def _code(outcome: str) -> str:
    found = _CODE.search(outcome or "")
    return found.group(0) if found else ""


def _outcome_word(text: str) -> str:
    """The outcome a text without a code names: an outcome with an underscore wherever it stands
    (``defect_detected``); a plain word (``open``, ``wait``) only as the whole text or after ``ending`` or
    ``action``, so a sentence that merely holds the word is not read as it."""
    lowered = (text or "").strip().lower()
    found = next((w for w in re.findall(r"[a-z][a-z_]*", lowered) if "_" in w and w in REMEDIES), "")
    if found:
        return found
    if lowered in REMEDIES:
        return lowered
    said = re.search(r"\b(?:ending|action)\W+([a-z]+)", lowered)
    return said.group(1) if said and said.group(1) in REMEDIES else ""


def recovery_remedies(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """``g6.metaharness.recovery``'s remedies for wrong answers, mapped by the component each names: a runnable
    component through ``g6_invoke`` (its ops listed), any other inside a candidate (``metaharness_propose``), and a
    remedy only a person can take to the person."""
    from g6.metaharness.recovery import WRONG_ANSWER_REMEDIES

    data = data or _corpus()
    out, seen = [], set()
    for kind, remedies in sorted(WRONG_ANSWER_REMEDIES.items()):
        for remedy in remedies:
            if (remedy.component, remedy.action) in seen:
                continue
            seen.add((remedy.component, remedy.action))
            ops = data["components"].get(remedy.component, {}).get("ops", [])
            person = remedy.who == "person"
            out.append({"answer_kind": kind, "component": remedy.component, "action": remedy.action,
                        "how": remedy.how, "tell_person": person,
                        "tool": "" if person else "g6_invoke" if ops else "metaharness_propose",
                        "ops": [f"{remedy.component}.{op}" for op in ops]})
    return out


def remedy_for(outcome: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The remedy for a refusal or an outcome, or ``None`` when G6 has none. A ``G6_E_*`` code is read first: a
    component's declared limit, then the code itself, ``*_NO_MODEL``, then its longest family prefix; otherwise the
    first outcome word that has one. Its call is filled from the refusal's own text where that names it."""
    data = data or _corpus()
    text, code = outcome or "", _code(outcome or "")
    key = ""
    if code:
        limits = {c for component in data["components"].values() for c in component.get("limits", ())}
        if code in limits:
            key = "component_limit"
        elif code in REMEDIES:
            key = code
        elif code.endswith("_NO_MODEL"):
            key = "*_NO_MODEL"
        else:
            key = max((k for k in REMEDIES if k.endswith("_") and code.startswith(k)), key=len, default="")
            if not key:
                key = max((k for k in REMEDIES if k.startswith("G6_E_") and code.startswith(k)), key=len, default="")
    else:
        key = _outcome_word(text)
    if not key:
        return None
    found = dict(REMEDIES[key])
    arguments = dict(found["arguments"])
    pairs = [f"{c}.{o}" for c, o in _PAIR.findall(text)]
    pair = pairs[0] if pairs else ""                 # what the refusal names, an op or an allowance (patch_review.review)
    named = _COMPONENT_CODE.search(text)
    component = named.group(1).lower() if named and named.group(1).lower() in data["components"] else next(
        (p.split(".")[0] for p in pairs if p.split(".")[0] in data["components"]), "")
    if "component" in arguments and component:
        arguments["component"] = component
    if found["setup"].endswith("<component.op>") and pair:
        found["setup"] = found["setup"].replace("<component.op>", pair)
    tool = found["tool"]
    available = bool(tool) and tool in _registered(data)
    entry = next((e for e in data["entries"] if e["kind"] == "tool" and e["call"]["tool"] == tool), None)
    call = ({"tool": tool, "arguments": {**(entry["call"]["arguments"] if entry else {}), **arguments}}
            if tool else None)
    out = {"key": key, "code": code or key, "action": found["action"], "tell_person": found["tell_person"],
           "tool": tool, "call": call, "setup": found["setup"], "available": available if tool else True}
    if key == "wrong_answer":
        out["recovery"] = recovery_remedies(data)
    return out


# -- stage helpers ----------------------------------------------------------------------------------------------
def _refuse(message: str, request_id: str, run_id: str) -> dict[str, Any]:
    from g6.core.door import blocked_reply

    return {**blocked_reply(f"[G6_E_INPUT_VALIDATION] {message}", request_id=request_id), "run_id": run_id}


def _documents(data: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, str]:
    """The words each entry is ranked by: its text, what it establishes and, for an op, its component's text."""
    components = data["components"]
    return {key: " ".join([e["text"], e["establishes"], components.get(e.get("component", ""), {}).get("text", "")])
            for key, e in entries.items()}


def _lexical(task: str, documents: dict[str, str]) -> dict[str, float]:
    """``g6_find``'s scoring over the corpus (sum over the query's words of min(count in query, count in entry) times
    log(1 + entries / entries holding the word)), as a share of what an entry holding every query word the corpus
    has would score: 1.0 matches all of them, and a weak overlap stays weak however poor the best match is."""
    query = Counter(_tokens(task))
    docs = {key: Counter(_tokens(text)) for key, text in documents.items()}
    df = Counter(t for d in docs.values() for t in set(d))
    n = max(1, len(docs))
    ideal = sum(query[t] * math.log(1 + n / df[t]) for t in query if df.get(t))
    out = {}
    for key, d in docs.items():
        score = sum(min(query[t], d[t]) * math.log(1 + n / df[t]) for t in query if d.get(t))
        if score > 0:
            out[key] = score / ideal
    return out


def _items(considering: Any) -> list[Any]:
    if isinstance(considering, list):
        return considering
    if isinstance(considering, dict):
        return [considering]
    text = considering.strip() if isinstance(considering, str) else ""
    if text.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]


def _check_tool(entry: dict[str, Any], arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        return ["arguments is a JSON object of the tool's arguments"]
    schema = entry["schema"]
    issues = [f"{entry['call']['tool']} takes no argument {key}" for key in sorted(set(arguments) - set(schema["properties"]))]
    issues += [f"{entry['call']['tool']} needs the argument {key}" for key in schema["required"] if key not in arguments]
    return issues


def _check_op(entry: dict[str, Any], args: Any) -> tuple[bool, list[str]]:
    """``navigator.check_plan`` on the one call, and its keys against the op's typed input. Structural problems make
    it invalid; a plan-only code is kept as an issue and says so."""
    from g6.navigator.plan import check_plan

    if not isinstance(args, dict):
        return False, ["args is a JSON object of the op's parameters"]
    component, op = entry["id"][3:].split(".", 1)
    report = check_plan([{"component": component, "op": op, "args": args}])
    issues, valid = [], True
    for issue in report.issues:
        if issue.code in _PLAN_ONLY:
            issues.append(f"[{issue.code}] {issue.message} (this concerns a plan navigator runs, not a direct "
                          "g6_invoke; a sensitive op's allowance is in needs)")
        else:
            valid = False
            issues.append(f"[{issue.code}] {issue.message}")
    unknown = sorted(set(args) - set(entry.get("fields", ())))
    if unknown:
        valid = False
        issues.append(f"{component}.{op}'s typed input takes no field {', '.join(unknown)}")
    return valid, issues


def _considered(items: list[Any], entries: dict[str, dict[str, Any]], tools: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for index, item in enumerate(items):
        row: dict[str, Any] = {"index": index, "kind": "unknown", "validated": False, "issues": []}
        if isinstance(item, str):
            name = item.strip()
            key = tools.get(name) or (f"op:{name}" if f"op:{name}" in entries else "")
            row.update(kind="call", id=key, validated=bool(key))
            if not key:
                row["issues"].append("not a tool or op G6 offers: g6_select names those it has")
        elif isinstance(item, dict) and "tool" in item:
            key = tools.get(str(item["tool"]), "")
            row.update(kind="call", id=key)
            if not key:
                row["issues"].append(f"no registered tool is called {item['tool']}")
            else:
                row["issues"] = _check_tool(entries[key], item.get("arguments", {}))
                row["validated"] = not row["issues"]
        elif isinstance(item, dict) and "component" in item and "op" in item:
            key = f"op:{item['component']}.{item['op']}"
            row.update(kind="call", id=key if key in entries else "")
            if key not in entries:
                row["issues"].append(f"{item['component']}.{item['op']} is not a runnable op G6 offers")
            else:
                row["validated"], row["issues"] = _check_op(entries[key], item.get("args", item.get("params", {})))
        elif isinstance(item, dict) and ("hypothesis" in item or "claim" in item):
            claim = str(item.get("hypothesis", item.get("claim", ""))).strip()
            severity = item.get("severity", "medium")
            prediction = item.get("predicts") or item.get("prediction") or item.get("falsifier") or ""
            check = str(item.get("check", "") or "")
            row.update(kind="hypothesis", id=str(item.get("id") or f"H{index + 1}"), claim=claim,
                       severity=severity if severity in ("low", "medium", "high") else "medium",
                       predicts=str(prediction), check=tools.get(check) or (f"op:{check}" if f"op:{check}" in entries
                                                                             else ""))
            row["validated"] = bool(claim)
            if not claim:
                row["issues"].append("a hypothesis says what may be wrong")
            if severity not in ("low", "medium", "high"):
                row["issues"].append("severity is low, medium or high; medium is used")
            if not prediction:
                row["issues"].append("no prediction: say what observed result would refute it (predicts)")
            if check and not row["check"]:
                row["issues"].append(f"its check {check} is not a tool or op G6 offers")
        elif isinstance(item, dict) and ("requirement" in item or "text" in item):
            text = str(item.get("requirement", item.get("text", ""))).strip()
            row.update(kind="requirement", id=str(item.get("id", "")), validated=bool(text))
            if not text:
                row["issues"].append("a requirement says what must hold")
            if not item.get("checks"):
                row["issues"].append("no stated check: a requirement with none is not established by a review")
        else:
            row["issues"].append("an entry is a tool or op name, {tool, arguments}, {component, op, args}, "
                                 "{hypothesis, id, severity, predicts, check} or {id, text, checks}")
        out.append(row)
    return out


def _schedule(hypotheses: list[dict[str, Any]], entries: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """The next call by ``verification_controller.next_experiment``, or ``("", why)`` when none fits."""
    from g6.complexity.ir import Hypothesis
    from g6.harness.verification_controller import Experiment, ExperimentState, next_experiment

    states = [Hypothesis(h["id"], h["claim"], h["severity"], h["predicts"]) for h in hypotheses]
    named = {h["check"]: h["id"] for h in hypotheses if h["check"]}
    ids = {h.id for h in states}
    experiments = []
    for key, entry in sorted(entries.items()):
        if entry["kind"] == "route":
            continue
        covers = sorted((set(entry.get("hypotheses", ())) & ids) | ({named[key]} if key in named else set()))
        if covers:
            experiments.append(Experiment(key, key, tuple(covers), COST_UNITS[entry["cost"]], allowed=True))
    state = ExperimentState("selector", "selector", "selector", tuple(states), BUDGET_UNITS)
    chosen = next_experiment(state, tuple(experiments))
    if chosen is None:
        return "", f"next_experiment found no call within {BUDGET_UNITS} units that addresses an open hypothesis"
    return chosen.id, (f"next_experiment: the most severity-weighted open-hypothesis coverage per cost unit within "
                       f"{BUDGET_UNITS} units ({', '.join(chosen.hypothesis_ids)}; {len(experiments)} calls compared)")


def _validate(entry: dict[str, Any], call: dict[str, Any]) -> tuple[bool, list[str]]:
    if entry["kind"] == "tool":
        issues = _check_tool(entry, call.get("arguments", {}))
        return not issues, issues
    if entry["kind"] == "op":
        try:
            params = json.loads(call["arguments"].get("params_json", "{}"))
        except json.JSONDecodeError:
            return False, ["params_json is not JSON"]
        return _check_op(entry, params)
    from g6.mcp.registrations import package_root
    from g6.skills import routing

    root = (package_root() / routing.ROOT).read_text(encoding="utf-8")
    found = routing.section(root, call["section"]) is not None
    return found, [] if found else [f"the skill has no section {call['section']}"]


def select(task: str, phase: str = "", last_outcome: str = "", considering: Any = "", limit: int = 5,
           rerank: str = "lexical", *, corpus: dict[str, Any] | None = None,
           request_id: str = "", run_id: str = "") -> dict[str, Any]:
    """The selection for the agent's situation (module docstring). ``corpus`` stands in for the shipped corpus in
    tests."""
    from g6.core.reliability_envelope import envelope_fields, warning_card
    from g6.skills.corpus import PHASES, entries as expanded

    if not isinstance(task, str) or not task.strip():
        return _refuse("task says what you are doing", request_id, run_id)
    if phase not in ("", *PHASES):
        return _refuse(f"phase is one of {', '.join(PHASES)}, or empty", request_id, run_id)
    if rerank not in RERANKS:
        return _refuse("rerank is lexical: a semantic rerank is not offered here (it would depend on what this "
                       "machine has installed)", request_id, run_id)
    if not isinstance(limit, int) or isinstance(limit, bool):
        return _refuse("limit is a whole number", request_id, run_id)
    if not isinstance(last_outcome, str):
        return _refuse("last_outcome is a refusal's text, its G6_E_* code or an outcome word", request_id, run_id)
    limit = max(1, min(limit, MAX_CANDIDATES))
    data = corpus if corpus is not None else _corpus()
    entries = {e["id"]: e for e in expanded(data)}
    tools = {e["call"]["tool"]: e["id"] for e in data["entries"] if e["kind"] == "tool"}
    mechanisms: list[dict[str, Any]] = []

    # 1. facts
    considered = _considered(_items(considering), entries, tools)
    requirements = [c for c in considered if c["kind"] == "requirement" and c["validated"]]
    hypotheses = [c for c in considered if c["kind"] == "hypothesis" and c["validated"]]
    calls = [c["id"] for c in considered if c["kind"] == "call" and c["validated"]]
    code = _code(last_outcome)
    outcome = code or _outcome_word(last_outcome) or ("not recognised" if last_outcome.strip() else "")
    facts = {"phase": phase or "unknown", "last_outcome": outcome,
             "stated_requirements": len(requirements), "hypotheses": len(hypotheses), "considered_calls": len(calls)}
    mechanisms.append({"stage": "facts", "ran": True, "facts": facts,
                       "why": "what you supplied; each candidate carries its static needs, and what only the host "
                              "knows is in unknown_until_observed"})

    # 2. the agent's model of the task
    scores: dict[str, float] = {}
    ranked_by: dict[str, list[str]] = {}

    def add(key: str, value: float, by: str) -> None:
        if key in entries and value > 0:
            scores[key] = scores.get(key, 0.0) + value
            if by not in ranked_by.setdefault(key, []):
                ranked_by[key].append(by)

    if phase in ("plan", "implement", "verify") and not requirements:
        for key in RAISED:
            add(key, RAISE_BONUS, "model_check")
        why = "no stated requirements in considering: raised g6_premortem and g6_confirm_behaviour"
        mechanisms.append({"stage": "model_check", "ran": True, "why": why, "considering": len(considered)})
    elif phase in ("plan", "implement", "verify"):
        n = len(requirements)
        mechanisms.append({"stage": "model_check", "ran": True, "considering": len(considered),
                           "why": f"{n} stated requirement{'s' if n != 1 else ''} in considering: nothing raised"})
    else:
        mechanisms.append({"stage": "model_check", "ran": bool(considered), "considering": len(considered),
                           "why": "the raise applies in plan, implement or verify; " + (
                               "considering validated" if considered else "nothing to validate")})

    # 3. candidates
    documents = _documents(data, entries)
    lexical = _lexical(task, documents)
    for key, value in lexical.items():
        add(key, LEXICAL_WEIGHT * value * (ROUTE_WEIGHT if entries[key]["kind"] == "route" else 1.0), "lexical")
        if phase and phase in entries[key]["phases"]:
            add(key, PHASE_TAG_BONUS, "phase_prior")
    for key, value in PRIORS.get(phase, {}).items():
        add(key, value, "phase_prior")
    for key in calls:
        add(key, CONSIDERED_BONUS, "considering")
    order = sorted(scores, key=lambda k: (-scores[k], k))
    lexical_order = sorted(lexical, key=lambda k: (-lexical[k], k))
    stage: dict[str, Any] = {"stage": "candidates", "ran": True, "rankers": ["lexical"] + (["phase_prior"] if phase
                                                                                        else [])}
    stage["rerank"] = NOT_OFFERED if rerank == "colbert" else "not asked (rerank=lexical)"
    top = order[:limit]
    disagreements = []
    for key in sorted(set(top) | set(lexical_order[:limit])):
        ranks = {"lexical": lexical_order.index(key) + 1 if key in lexical_order[:limit] else None,
                 "final": top.index(key) + 1 if key in top else None}
        if len(set(ranks.values())) > 1:
            disagreements.append({"id": key, **ranks})
    stage["disagreements"] = disagreements
    stage["why"] = (f"{len(lexical)} entries share a word with the task; phase priors "
                    + (f"for {phase}" if phase else "skipped: no phase given"))
    mechanisms.append(stage)

    # 4. budgeted next action
    scheduled, why = ("", "no open hypothesis in considering: the next action is the first candidate")
    if hypotheses:
        scheduled, why = _schedule(hypotheses, entries)
        mechanisms.append({"stage": "next_action", "ran": True, "why": why})
    else:
        mechanisms.append({"stage": "next_action", "ran": False, "why": why})

    # 5. reroute
    remedy = remedy_for(last_outcome, data) if last_outcome.strip() else None
    first = ""
    if not last_outcome.strip():
        mechanisms.append({"stage": "reroute", "ran": False, "why": "no last_outcome given"})
    elif remedy is None:
        mechanisms.append({"stage": "reroute", "ran": True, "why": "G6 has no remedy for this outcome"})
    else:
        if remedy["call"] and remedy["available"] and not remedy["tell_person"]:
            first = f"tool:{remedy['tool']}"
        mechanisms.append({"stage": "reroute", "ran": True,
                           "why": f"remedy {remedy['key']}: " + ("tell the person" if remedy["tell_person"] else
                                                                  "call " + remedy["tool"] if first else
                                                                  f"{remedy['tool']} is not registered on this server")})

    # the remedy first, then the scheduler's pick and the calls you are considering, kept within the limit; the rest
    # by score, and all but the remedy in score order
    must = list(dict.fromkeys(k for k in (first, scheduled, *calls) if k))[:limit]
    rest = [k for k in order if k not in must][:limit - len(must)]
    rank = {k: i for i, k in enumerate(order)}
    chosen = ([first] if first else []) + sorted((k for k in must + rest if k != first),
                                                 key=lambda k: (rank.get(k, len(rank)), k))

    # 6. validation
    candidates = []
    for key in chosen:
        entry = entries[key]
        call = remedy["call"] if key == first else entry["call"]
        by = ["remedy"] if key == first else list(ranked_by.get(key, []))
        if key == scheduled and "scheduler" not in by:
            by.append("scheduler")
        valid, issues = _validate(entry, call)
        candidate = {"id": key, "kind": entry["kind"], "call": call, "establishes": entry["establishes"],
                     "not_established": entry["not_established"], "needs": entry["needs"], "cost": entry["cost"],
                     "ranked_by": by, "score": round(scores.get(key, 0.0), 3), "validated": valid, "issues": issues}
        if entry["kind"] == "op":
            candidate["component"] = entry["component"]
        candidates.append(candidate)
    mechanisms.append({"stage": "validation", "ran": bool(candidates),
                       "why": "tool templates against their recorded schemas; op calls through navigator.check_plan"})

    if remedy is not None and remedy["tell_person"]:
        say = remedy["action"] + (f" The step: {remedy['setup']}." if remedy["setup"] else "")
        nxt = {"id": None, "call": None, "tell_person": True, "say": say, "why": f"remedy {remedy['key']}"}
    elif candidates:
        pick = first or scheduled or candidates[0]["id"]
        found = next(c for c in candidates if c["id"] == pick)
        nxt = {"id": pick, "call": found["call"], "tell_person": False, "say": "",
               "why": "remedy " + remedy["key"] if pick == first else why if pick == scheduled else "the first candidate"}
    else:
        nxt = {"id": None, "call": None, "tell_person": False, "say": "Nothing matched: describe the task in other "
               "words, or call g6_find.", "why": "no candidate"}
    card = warning_card("G6_E_QUALIFIED_DRAFT",
                        "g6_select: a proposal ranked by fixed rules over G6's own text; nothing ran and nothing "
                        "about your task is checked",
                        "Choose a call and make it; the door checks it then. Pass a refusal back as last_outcome to "
                        "reroute.")
    evidence = [{"kind": "selection", "corpus_entries": len(entries), "corpus_release": data.get("release", ""),
                 "stages_run": [m["stage"] for m in mechanisms if m["ran"]]}]
    return {"ok": True, "phase": phase, "candidates": candidates, "next": nxt, "remedy": remedy,
            "considering": considered, "mechanisms": mechanisms, "unknown_until_observed": list(UNKNOWN_UNTIL_OBSERVED),
            **envelope_fields(reliability_label="qualified-draft", warning_card=card, evidence=evidence,
                              request_id=request_id, run_id=run_id)}


__all__ = ["BUDGET_UNITS", "COST_UNITS", "MAX_CANDIDATES", "PENDING_TOOLS", "PRIORS", "REMEDIES", "STAGES",
           "recovery_remedies", "remedy_for", "select"]
