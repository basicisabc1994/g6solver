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
   tagged with the phase) and the fixed intent priors ``INTENTS`` (a safety question raises the action gate; an
   agent that keeps failing, the meta-harness). A safety question is a task holding one of ``SAFETY_WORDS``, fixed
   and reviewed: "safe", "should i", "risky", "dangerous", "allowed", "okay to", "fine to", "is it ok",
   "production", "prod", "live", "delete", "rm", "overwrite", "reset", "push", "commit", "merge", "pay", "migrate",
   "drop", "deploy", "send", "publish". Every reply, whatever its words: a call that acts outside G6
   (``_acts``: an op its component lists as sensitive, or whose name holds an ``OUTWARD_WORDS`` word) ranks below
   every call that does not, and is never ``next``. Where the rankers disagree it is listed. A semantic rerank (the local ColBERT rung) is
   deferred: it would make the reply depend on what the host has installed and cached, so ``rerank="colbert"`` is
   kept for compatibility and answered lexically (``NOT_OFFERED``).
4. ``next_action``: with open hypotheses in ``considering``, ``verification_controller.next_experiment`` picks the
   call with the most severity-weighted open-hypothesis coverage per cost unit (``COST_UNITS``) within
   ``BUDGET_UNITS``; otherwise the first candidate.
5. ``reroute``: ``REMEDIES`` by refusal code or outcome, and ``g6.metaharness.recovery``'s remedies by component.
6. ``validation``: a tool template's keys are within its recorded schema and hold its required keys; an op template
   carries the door's own typed input check (``registry.build_typed_input``), made when the corpus was built from
   shipped code, and an op call in ``considering`` is checked against the fields the corpus recorded. No component
   is imported here, so a reply never depends on what the host can import. ``validated`` says only that the door
   takes the call's shape; ``allowed_by_default`` is false for a call with a sensitive allowance, which the person
   grants where the server is registered. Every issue is attached to its candidate.
"""
from __future__ import annotations

import copy
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
MAX_CONSIDERING_BYTES = 64 * 1024   # ``considering`` past this is refused, as text or as a value
MAX_DEPTH = 16                      # and past this many nested lists or objects
LEXICAL_WEIGHT = 6.0        # an entry holding every query word the corpus has scores this much; others in proportion
ROUTE_WEIGHT = 0.5          # a route is an instruction to read, so its words count half beside the calls it names
PHASE_TAG_BONUS = 0.5       # a matched entry tagged with the phase
RAISE_BONUS = 1.0           # stage 2's raise
CONSIDERED_BONUS = 2.0      # a call the agent is considering, once it validates
GATE_BONUS = LEXICAL_WEIGHT + PHASE_TAG_BONUS + 1.0   # a safety question: the action gate above any word match
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
#: The words and phrases that make a task a safety question (``action_safety``): it asks whether an action is safe,
#: should be done or is risky, or names an outward or destructive act. Fixed, reviewed data, listed in the module
#: docstring; each is matched as whole words in the task, lower-cased.
SAFETY_WORDS = ("safe", "should i", "risky", "dangerous", "allowed", "okay to", "fine to", "is it ok", "production",
                "prod", "live", "delete", "rm", "overwrite", "reset", "push", "commit", "merge", "pay", "migrate",
                "drop", "deploy", "send", "publish")
#: Fixed, documented intent priors, read by a reviewer: name -> (a pattern over the task's words, {phase, or "*" for
#: every phase: {entry id: bonus}}). ``action_safety`` is the task holding a ``SAFETY_WORDS`` word or phrase: the
#: action gate that decides allow, confirm, review or block is raised first, and the premortem with it while
#: planning. ``workflow_improvement`` is ``g6_find``'s own workflow-intent pattern (an agent that keeps failing on a
#: kind of task): the meta-harness's diagnosis, its start and the self-improvement loop's status. A test keeps every
#: id in the corpus.
INTENTS: dict[str, tuple[re.Pattern[str], dict[str, dict[str, float]]]] = {
    "action_safety": (
        re.compile(r"\b(" + "|".join(re.escape(w) for w in SAFETY_WORDS) + r")\b"),
        {"*": {"op:action_gating.evaluate": GATE_BONUS}, "plan": {"tool:g6_premortem": 2.0}}),
    "workflow_improvement": (
        re.compile(r"\b(tune|improve|keeps failing|make my agent better)\b"),
        {"*": {"tool:metaharness_diagnose": 4.0, "tool:metaharness_start": 3.5, "tool:g6_improve_status": 2.0}}),
}
#: Words in an op's name that say it acts outside G6 (publishes, sends, pays, deletes, deploys, writes, removes),
#: so the op acts even when its component does not list it among its sensitive ops.
OUTWARD_WORDS = frozenset({"push", "send", "publish", "pay", "delete", "deploy", "drop", "remove", "write"})
#: Tools a remedy may name before they are registered (built on another branch): such a remedy is marked
#: ``available: false`` until the corpus holds the tool, and is never offered as a call.
PENDING_TOOLS = {"g6_agentic_run": "the override kernel's tool (plan revision 8, phase D), built on its own branch"}
UNKNOWN_UNTIL_OBSERVED = (
    "whether this server has the sensitive op allowed: only a G6_E_SENSITIVE_COMPONENT_DENIED refusal says it is not",
    "whether a sharing grant exists for a source: only a withheld reply (G6_E_CLOUD_CONSENT_REQUIRED) says it does not",
    "whether a model is configured for G6: only a *_NO_MODEL or G6_E_LLM_DISABLED refusal says it is not",
    "whether this machine can confine a run: only G6_E_ISOLATION_UNAVAILABLE says it cannot",
)
_CODE = re.compile(r"G6_E_[A-Z0-9_]+")
_PAIR = re.compile(r"(?<![\w.])([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)(?![\w.])")
_COMPONENT_CODE = re.compile(r"\[([A-Z][A-Z0-9_]*)_ERROR\]")


def _remedy(action: str, tool: str = "", arguments: dict[str, Any] | None = None, *, tell: bool = False,
            setup: str = "") -> dict[str, Any]:
    return {"action": action, "tool": tool, "arguments": dict(arguments or {}), "tell_person": tell,
            "setup": setup, "available": tool not in PENDING_TOOLS}


#: Codes that only say what kind of result this is: every g6_review_patch result carries G6_E_PATCH_REVIEW, and
#: the outcome word beside it is what says what to do.
CARRIES_OUTCOME = frozenset({"G6_E_PATCH_REVIEW"})
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
    "G6_E_POLICY_BLOCKED": _remedy("A policy refused this step: only the person can change it. Tell them what the "
                                   "refusal says; never change the policy yourself.", tell=True),
    "G6_E_PENDING_APPROVAL": _remedy("The step waits for the person's approval. " + _ASK, tell=True),
    "G6_E_PIPELINE_FEASIBILITY_RISKY_CONFIRMATION_REQUIRED": _remedy(
        "The mission looks risky to carry out as asked and needs the person's confirmation. " + _ASK, tell=True),
    "G6_E_MISSING_PARAM": _remedy("An input is missing: the refusal names it. Fill it in and call again; for an op, "
                                  "g6_describe lists its fields.", "g6_describe", {"component": "<component>"}),
    "G6_E_INVALID_PARAM": _remedy("An input has a value G6 does not take: the refusal names it. Fix it and call "
                                  "again; for an op, g6_describe lists its fields.", "g6_describe",
                                  {"component": "<component>"}),
    "G6_E_CAPABILITY_UNAVAILABLE": _remedy("The step needs a capability this server does not offer: pick another "
                                           "call for the task.", "g6_select", {"task": "<the task>"}),
    "G6_E_DEPENDENCY_UNAVAILABLE": _remedy("A step this one depends on has no result yet: read the mission's state.",
                                           "g6_mission_status", {"mission_id": "<mission_id>"}),
    "G6_E_TASK_CONTRACT_UNVERIFIABLE": _remedy(
        "The task's standard of done cannot be checked as it is stated: say what done means and how each part is "
        "checked.", "g6_premortem", {"task": "<task>"}),
    "G6_E_RESULT_NOT_VERIFIED": _remedy("The result did not pass its check: report it so, never as done. " + _SHOW,
                                        tell=True),
    "G6_E_WORK_LOOP_NO_PROGRESS": _remedy("The work loop made no progress. " + _SHOW, tell=True),
    "G6_E_CONFIG_INVALID": _remedy("G6's configuration or the plan is not valid: tell the person what the refusal "
                                   "says; g6 doctor checks the setup.", tell=True, setup="g6 doctor"),
    "G6_E_COST_GATE_": _remedy("The step is over its cost budget or has no price: only the person raises a budget. "
                               "Tell them.", tell=True),
    "G6_E_TIMEOUT": _remedy("The step ran out of time: tell the person; a narrower step may fit.", tell=True),
    "G6_E_RETRY": _remedy("A tool failed for a passing reason: call the same step once more; if it fails again, "
                          "tell the person.", tell=True),
    "G6_E_INTERNAL": _remedy("G6 failed inside: tell the person; it is G6's fault.", tell=True),
    "G6_E_DELIVERABLE_WITHHELD": _remedy("The result is withheld until the person resolves what it names: act on no "
                                         "part of it. " + _SHOW, tell=True),
    "G6_E_TESTS_DID_NOT_RUN": _remedy("The repository's tests did not run, so nothing was checked. " + _SHOW,
                                      tell=True),
    "G6_E_TEST_REPORT_UNKNOWN": _remedy("G6 could not read the test runner's report: tell the person; it is G6's "
                                        "fault.", tell=True),
    "G6_E_EXECUTION_ARTIFACT_UNAVAILABLE": _remedy(
        "The copy G6 runs the tests in held something it cannot run with (a cache or an input it does not take). "
        + _SHOW, tell=True),
    "G6_E_LEARNING_": _remedy("The mission's learning step refused: " + _SHOW, tell=True),
    "G6_E_WORKFORCE_": _remedy("The mission's own check refused: " + _SHOW, tell=True),
    # the repository and this machine
    "G6_E_GIT_MISSING_BINARY": _remedy("git is not installed where G6 runs: tell the person; g6 doctor says what this "
                                       "computer needs.", tell=True, setup="g6 doctor"),
    "G6_E_GIT_REPO_INVALID": _remedy("Check that repo is the top folder of a git repository; if it is, tell the "
                                     "person what the refusal says.", tell=True),
    "G6_E_GIT_SUBMODULE_REFUSED": _remedy("The repository holds a submodule that was set up, which G6 does not run "
                                          "tests in: tell the person.", tell=True),
    "G6_E_REPO_PYTHON_INVALID": _remedy("The Python G6 is set to run the repository's tests with is not usable: only "
                                        "the person changes that setting; g6 doctor checks it.", tell=True,
                                        setup="g6 doctor"),
    "G6_E_REPO_ENV_INVALID": _remedy("The environment G6 is set to run the repository's tests with is not valid: "
                                     "only the person changes that setting; g6 doctor checks it.", tell=True,
                                     setup="g6 doctor"),
    # the Context Engine
    "G6_E_KNOWLEDGE": _remedy("Read which sources are indexed and shared, then search again.", "g6_knowledge_sources"),
    "G6_E_KNOWLEDGE_": _remedy("Read which sources are indexed and shared, then search again.", "g6_knowledge_sources"),
    # the review and its loop
    "G6_E_PATCH_REVIEW": _remedy(
        "The code every review result carries: the outcome beside it (test_integrity_compromised, defect_detected, "
        "insufficient_evidence or supported_within_scope) says what to do. Pass that outcome back as last_outcome.",
        "g6_select", {"task": "<the task>", "last_outcome": "<the outcome beside the code>"}),
    "G6_E_PATCH_STALE": _remedy(
        "The working tree changed since the digest you passed: review it again as it stands now, without the old "
        "expected_artifact_digest.", "g6_review_patch", {"repo": "<repo>", "task": "<task>"}),
    "G6_E_PATCH_REVIEW_INTERRUPTED": _remedy("The review was interrupted: start it again.", "g6_review_patch",
                                             {"repo": "<repo>", "task": "<task>"}),
    "G6_E_NO_PYTHON_CHANGED": _remedy(
        "No Python file changed against the base, so nothing was tested and no loop began: make the change, or "
        "name the right base, then call again.", "g6_review_until_clean",
        {"repo": "<repo>", "task": "<task>", "session": "<session>"}),
    "G6_E_DEFECT_NOT_REPRODUCED": _remedy(
        "The loop stopped on a defect the reviewer named and no test could show. " + _SHOW, tell=True),
    "G6_E_LOOP_WAITING_ON_PERSON": _remedy(
        "G6's review loop waits for the person's answer to the question that was asked. " + _ASK, tell=True),
    "G6_E_REVIEW_UNTIL_CLEAN": _remedy(_SHOW, tell=True),
    "G6_E_NO_CLEAN_REVIEW": _remedy("The loop stopped with no clean review. " + _SHOW, tell=True),
    "G6_E_STOPPED": _remedy("The review stopped: " + _SHOW, tell=True),
    "G6_E_LOOP_BUSY": _remedy("Another step of this session is running: wait for its result, then call again.",
                              "metaharness_job", {"job_id": "<job_id>"}),
    "G6_E_JOB_LIMIT": _remedy("Sixteen jobs are already pending: read one's result before starting another.",
                              "metaharness_job", {"job_id": "<job_id>"}),
    "G6_E_JOB_UNKNOWN": _remedy("No job is kept under that id (the server may have restarted): start the step again.",
                                "g6_select", {"task": "<the step to start again>"}),
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
    # the meta-harness: a wait on the person tells the person; any other refusal reads the workflow's record
    "G6_E_META_APPROVAL_REQUIRED": _remedy(
        "No person approved, confirmed or asked for this: a name comes only from the client's dialog. " + _ASK,
        tell=True),
    "G6_E_META_APPROVER_IS_PROPOSER": _remedy(
        "The person who accepts a change cannot be the one who proposed it: another person decides. " + _ASK,
        tell=True),
    "G6_E_META_NOT_CONFIRMED": _remedy(
        "No person has confirmed this workflow, so nothing of it runs: only the person confirms it. " + _ASK,
        tell=True),
    "G6_E_META_STANDARD_CHANGED": _remedy(
        "The workflow changed since the person confirmed it, so every earlier comparison is void: only the person "
        "confirms the new one. " + _ASK, tell=True),
    "G6_E_META_PROMOTION_REQUIRED": _remedy(
        "Each step up needs its own held-out report and the person's approval. " + _ASK, tell=True),
    "G6_E_META_PROMOTION_DISABLED": _remedy(
        "No promotion policy was confirmed: only the person confirms one. " + _ASK, tell=True),
    "G6_E_META_SCHEMA": _remedy("The workflow's specification did not fit: fix the field the refusal names.",
                                "metaharness_start", {"specification_json": "<specification_json>"}),
    "G6_E_META_": _remedy("Read the workflow's record and its recovery.", "metaharness_inspect",
                          {"workflow": "<workflow>"}),
    "G6_E_HARNESS_EXECUTOR_FAILED": _remedy("The workflow's version failed while it ran: read its record and recovery.",
                                            "metaharness_inspect", {"workflow": "<workflow>"}),
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


def _acts(entry: dict[str, Any]) -> str:
    """Why an op acts outside G6's own checks, or "" when it does not: its component lists it as sensitive (it runs a
    program, reaches the network or changes the machine), or its name holds an ``OUTWARD_WORDS`` word. A G6 tool
    never acts in this sense: one that names an allowance runs G6's own confined check, or asks the person in the
    client's dialog before anything happens."""
    if entry["kind"] != "op":
        return ""
    if entry["needs"]["sensitive"]:
        return ("sensitive: it runs a program, reaches the network or changes the machine; only the person allows "
                "it, and the action gate decides first")
    said = sorted(set(re.findall(r"[a-z]+", entry["id"].split(".", 1)[1])) & OUTWARD_WORDS)
    return f"its name says it acts outside G6 ({', '.join(said)}); the action gate decides first" if said else ""


def _registered(data: dict[str, Any]) -> set[str]:
    return {e["call"]["tool"] for e in data["entries"] if e["kind"] == "tool"} | {
        e["id"][3:] for e in data["entries"] if e["kind"] == "op"}


def _code(outcome: str) -> str:
    found = _CODE.search(outcome or "")
    return found.group(0) if found else ""


def _outcome_word(text: str) -> str:
    """The outcome a text names: an outcome with an underscore wherever it stands (``defect_detected``); a plain
    word (``open``, ``wait``) only as the whole text or after ``ending``, ``action`` or ``outcome``, so a sentence
    that merely holds the word is not read as it."""
    lowered = (text or "").strip().lower()
    found = next((w for w in re.findall(r"[a-z][a-z_]*", lowered) if "_" in w and w in REMEDIES), "")
    if found:
        return found
    if lowered in REMEDIES:
        return lowered
    said = re.search(r"\b(?:ending|action|outcome)\W+([a-z]+)", lowered)
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
    component's declared limit; a code in ``CARRIES_OUTCOME`` gives way to the outcome word beside it; then the code
    itself, ``*_NO_MODEL``, the outcome word beside a code G6 has no remedy for, then the code's longest family
    prefix. With no code, the outcome word. Its call is filled from the refusal's own text where that names it."""
    data = data or _corpus()
    text, code = outcome or "", _code(outcome or "")
    word = _outcome_word(text)
    key = ""
    if code:
        limits = {c for component in data["components"].values() for c in component.get("limits", ())}
        if code in limits:
            key = "component_limit"
        elif code in CARRIES_OUTCOME and word:
            key = word
        elif code in REMEDIES:
            key = code
        elif code.endswith("_NO_MODEL"):
            key = "*_NO_MODEL"
        elif word:
            key = word                                  # the outcome beside a code G6 has no remedy for
        else:
            key = max((k for k in REMEDIES if k.endswith("_") and code.startswith(k)), key=len, default="")
            if not key:
                key = max((k for k in REMEDIES if k.startswith("G6_E_") and code.startswith(k)), key=len, default="")
    else:
        key = word
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
    return copy.deepcopy(out)                       # nothing a caller changes reaches the corpus or REMEDIES


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


def _depth(value: Any) -> int:
    """How deeply lists and objects nest in ``value``, walked without recursion; past ``MAX_DEPTH`` it stops."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, (list, dict)):
            deepest = max(deepest, level)
            if deepest > MAX_DEPTH:
                return deepest
            stack.extend((child, level + 1) for child in (item.values() if isinstance(item, dict) else item))
    return deepest


def _items(considering: Any) -> list[Any]:
    """``considering`` as a list of entries. Raises ``ValueError`` (G6's own words) when it is over
    ``MAX_CONSIDERING_BYTES``, nests deeper than ``MAX_DEPTH``, or is JSON that does not decode."""
    if isinstance(considering, (list, dict)):
        if _depth(considering) > MAX_DEPTH:
            raise ValueError(f"considering nests deeper than {MAX_DEPTH} levels")
        try:
            size = len(json.dumps(considering, default=str).encode("utf-8"))
        except (RecursionError, ValueError, TypeError):
            raise ValueError("considering is not a JSON value") from None
        if size > MAX_CONSIDERING_BYTES:
            raise ValueError(f"considering is over {MAX_CONSIDERING_BYTES // 1024} KB")
        return considering if isinstance(considering, list) else [considering]
    text = considering.strip() if isinstance(considering, str) else ""
    if len(text.encode("utf-8")) > MAX_CONSIDERING_BYTES:
        raise ValueError(f"considering is over {MAX_CONSIDERING_BYTES // 1024} KB")
    if text.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
        except (RecursionError, ValueError, TypeError):
            raise ValueError("considering starts as JSON but does not decode") from None
        if _depth(parsed) > MAX_DEPTH:
            raise ValueError(f"considering nests deeper than {MAX_DEPTH} levels")
        return parsed if isinstance(parsed, list) else [parsed]
    return [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]


def _check_tool(entry: dict[str, Any], arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        return ["arguments is a JSON object of the tool's arguments"]
    schema = entry["schema"]
    issues = [f"{entry['call']['tool']} takes no argument {key}" for key in sorted(set(arguments) - set(schema["properties"]))]
    issues += [f"{entry['call']['tool']} needs the argument {key}" for key in schema["required"] if key not in arguments]
    return issues


def _check_op(entry: dict[str, Any], args: Any) -> tuple[bool, list[str]]:
    """An op call the agent wrote, against what the corpus recorded of the op's typed input: no field it does not
    take, and each field its template holds. G6's own words; no component is imported."""
    if not isinstance(args, dict):
        return False, ["args is a JSON object of the op's parameters"]
    component, op = entry["id"][3:].split(".", 1)
    issues = []
    unknown = sorted(set(args) - set(entry.get("fields", ())))
    if unknown:
        issues.append(f"{component}.{op}'s typed input takes no field {', '.join(unknown)}")
    missing = sorted(set(json.loads(entry["call"]["arguments"]["params_json"])) - set(args))
    if missing:
        issues.append(f"{component}.{op} needs the field {', '.join(missing)}")
    return not issues, issues


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
        if call == entry["call"]:                   # the template: the door's own check, made when the corpus was built
            return bool(entry["validated"]), list(entry["issues"])
        try:
            params = json.loads(call["arguments"].get("params_json", "{}"))
        except (ValueError, RecursionError, TypeError):
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
    from g6.skills.corpus import PHASES, _expanded as expanded     # the reply is deep-copied at the end

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
    try:
        items = _items(considering)
    except ValueError as refused:
        return _refuse(str(refused), request_id, run_id)
    considered = _considered(items, entries, tools)
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
    words = " ".join(task.lower().split())
    intents = [name for name, (pattern, _) in INTENTS.items() if pattern.search(words)]
    for name in intents:
        by_phase = INTENTS[name][1]
        for key, value in {**by_phase.get("*", {}), **by_phase.get(phase, {})}.items():
            add(key, value, "intent_prior")
    for key in calls:
        add(key, CONSIDERED_BONUS, "considering")
    # a call that acts outside G6 ranks below every call that does not, whatever its score
    acting = {key: _acts(entries[key]) for key in entries}
    order = sorted(scores, key=lambda k: (bool(acting[k]), -scores[k], k))
    lexical_order = sorted(lexical, key=lambda k: (-lexical[k], k))
    stage: dict[str, Any] = {"stage": "candidates", "ran": True, "intents": intents,
                             "rankers": ["lexical"] + (["phase_prior"] if phase else [])
                             + (["intent_prior"] if intents else [])}
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
                    + (f"for {phase}" if phase else "skipped: no phase given") + "; intent priors "
                    + (f"for {', '.join(intents)}" if intents else "matched none")
                    + "; a call that acts outside G6 ranks below every other and is never next")
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
                                                 key=lambda k: (bool(acting[k]), rank.get(k, len(rank)), k))

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
                     "ranked_by": by, "score": round(scores.get(key, 0.0), 3), "validated": valid, "issues": issues,
                     "allowed_by_default": not entry["needs"]["sensitive"], "acts": acting[key]}
        if entry["kind"] == "op":
            candidate["component"] = entry["component"]
        candidates.append(candidate)
    mechanisms.append({"stage": "validation", "ran": bool(candidates),
                       "why": "tool templates against their recorded schemas; op templates by the door's typed input "
                              "check, made when the corpus was built from shipped code"})

    if remedy is not None and remedy["tell_person"]:
        say = remedy["action"] + (f" The step: {remedy['setup']}." if remedy["setup"] else "")
        nxt = {"id": None, "call": None, "tell_person": True, "say": say, "why": f"remedy {remedy['key']}"}
    elif any(not c["acts"] for c in candidates):
        # a call that acts outside G6 is never next: the scheduler's pick only when it does not act
        pick = first or (scheduled if scheduled and not acting[scheduled] else "") or next(
            c["id"] for c in candidates if not c["acts"])
        found = next(c for c in candidates if c["id"] == pick)
        nxt = {"id": pick, "call": found["call"], "tell_person": False, "say": "",
               "why": "remedy " + remedy["key"] if pick == first else why if pick == scheduled else
               "the first candidate that does not act outside G6"}
    elif candidates:
        nxt = {"id": None, "call": None, "tell_person": True, "why": "every candidate acts outside G6",
               "say": "Every call offered here acts outside G6's checks: ask the person before any of them."}
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
    # a deep copy: candidates hold the cached corpus's own objects, which a caller filling in a template must not change
    return copy.deepcopy({
        "ok": True, "phase": phase, "candidates": candidates, "next": nxt, "remedy": remedy,
        "considering": considered, "mechanisms": mechanisms, "unknown_until_observed": list(UNKNOWN_UNTIL_OBSERVED),
        **envelope_fields(reliability_label="qualified-draft", warning_card=card, evidence=evidence,
                          request_id=request_id, run_id=run_id)})


__all__ = ["BUDGET_UNITS", "COST_UNITS", "MAX_CANDIDATES", "PENDING_TOOLS", "PRIORS", "REMEDIES", "STAGES",
           "recovery_remedies", "remedy_for", "select"]
