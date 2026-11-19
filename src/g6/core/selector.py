# SPDX-License-Identifier: Apache-2.0
"""The staged selector behind ``g6_select`` (0.3 design 3.5; plan revision 8, phase B).

G6 needs no model to select: the calling agent is the semantic engine. Given the agent's situation, G6 returns a short
ranked list of next calls, each with its exact call template, what it establishes and what it needs, checked before
the agent makes it; the agent chooses, and the door checks the call it makes. A refusal passed back as
``last_outcome`` reroutes. Its ranking floor reads G6's own corpus (``g6.skills.corpus``) and the agent's
arguments. The shared reader can add safety suggestions and raise reported risk, with fallback and disagreement
recorded in mechanisms. The MCP adapter may supply a private reader of evaluator-admitted operational history,
evaluated only after argument validation. It runs no proposed call and reads no repository. A fact about the host (whether an op is allowed,
whether a grant exists, whether a model is configured, whether a run can be confined) is never reported: it is unknown
until observed, and the refusal that observes it is what the agent passes back.

The stages, each reported in ``mechanisms`` as run or skipped, with why:

1. ``facts``: what the agent supplied; each candidate carries its static needs. Each input is bounded and refused
   past its bound with the envelope, in G6's own words, before it is read: ``task`` past ``MAX_TASK_BYTES``,
   ``last_outcome`` past ``MAX_OUTCOME_BYTES``, ``considering`` past ``MAX_CONSIDERING_BYTES``,
   ``MAX_CONSIDERING_ENTRIES`` entries or ``MAX_DEPTH`` levels (measured by a walk that stops at the first excess).
   Nothing is ranked on a part of an input; so a reply stays within ``MAX_REPLY_BYTES``.
2. ``model_check``: in plan, implement or verify with no stated requirement, ``g6_premortem`` and
   ``g6_confirm_behaviour`` are raised (``RAISE_BONUS``); each ``considering`` entry is validated.
3. ``candidates``: a log-IDF word overlap over the corpus (``discovery._tokens``, the scoring ``g6_find`` uses), as
   a share of a full match, times ``LEXICAL_WEIGHT`` (``ROUTE_WEIGHT`` more for a route), plus the fixed phase priors ``PRIORS`` (and ``PHASE_TAG_BONUS`` for a matched entry
   tagged with the phase) and the fixed intent priors ``INTENTS`` (a safety question raises the action gate; an
   agent that keeps failing, the meta-harness). A safety question is a task holding one of ``SAFETY_WORDS``, fixed
   and reviewed, a verb among them in any of its word forms (pushing, deleted, dropping, rolled back, tore down;
   "safely"): "safe", "should i", "should we", "can i", "can we", "risky", "dangerous", "allowed", "okay to",
   "ok to", "fine to", "is it ok", "production", "prod", "live", "delete", "rm", "overwrite", "reset", "push",
   "commit", "merge", "pay", "migrate", "migration", "drop", "deploy", "send", "publish", "wipe", "erase",
   "truncate", "destroy", "terminate", "remove", "upload", "email", "transfer", "ship", "release", "roll back",
   "rollback", "revoke", "rotate", "tear down", "teardown", "kill", "refund", "charge", "shut down", "shutdown",
   "rewrite history", "force". Every reply, whatever its words: a call that acts outside G6
   (``_acts``: an op its component lists as sensitive, or whose name holds an ``OUTWARD_WORDS`` word) ranks below
   every call that does not, and is never ``next``; ``g6_cloud_grant`` is ``next`` only as the remedy for the
   consent code. Where the rankers disagree it is listed. A semantic rerank (the local ColBERT rung) is
   deferred: it would make the reply depend on what the host has installed and cached, so ``rerank="colbert"`` is
   kept for compatibility and answered lexically (``NOT_OFFERED``).
4. ``next_action``: with open hypotheses in ``considering``, ``verification_controller.next_experiment`` picks the
   call with the most severity-weighted open-hypothesis coverage per cost unit (``COST_UNITS``) within
   ``BUDGET_UNITS``; otherwise the first candidate.
5. ``reroute``: read only from G6's own reply (``_read``: the envelope's code, state, named outcome field and
   ``result`` component and op; in plain text, only a code that leads the first line), never from the content an
   op worked on. Who repairs a refusal is declared where its code is written (``g6.core.refusals``; the corpus
   carries the components' declarations), never in a table here: ``input`` (read the component's contract with
   ``g6_describe``, or fix the field in your own call), ``tool:<name>`` (G6's own next call), ``retry`` (the same
   call once more), or ``person``/``none`` (tell the person; any code its writer does not declare is the person's).
   ``REMEDIES`` holds only what no code says: outcome words (``g6.metaharness.recovery``'s remedies by component
   among them), a declared limit and a refusal with no code. ``g6_cloud_grant`` is offered only for the consent code
   itself, whose dialog the person answers. The op whose outcome ``last_outcome`` reports is never ``next`` again.
6. ``validation``: a tool template's keys are within its recorded schema and hold its required keys; an op template
   carries what the corpus recorded when it was built from shipped code: the door's own typed input check
   (``registry.build_typed_input``) and, for an op that computes over its input alone, a run of the template as
   given through ``g6_invoke``'s path. An op template is ``validated`` only when that run was not refused; any
   other carries ``fill``, the fields to supply, and is ``next`` only with them and ``FILL_HINTS``' or G6's generic
   instruction in ``say``. An op call in ``considering`` is checked against the fields the corpus recorded. No
   component is imported here, so a reply never depends on what the host can import. ``allowed_by_default`` is
   false for a call with a sensitive allowance, which the person grants where the server is registered. Every issue
   is attached to its candidate.

The library route offers only registered, non-sensitive operations whose templates passed the door and operation
checks. Several considered operations may be offered as ``g6.invoke`` calls. A blockless component gets no library
candidate. These rank after MCP calls, take at most ``LIBRARY_SLOTS``, and are never ``next``.
"""
from __future__ import annotations
from g6.core.semantic import scoped

import copy
import itertools
import json
import math
import re
from g6.core import semantic
from g6.core.risk_terms import SAFETY_WORDS, SAFETY_VERBS, SAFETY_IRREGULAR as _IRREGULAR
from collections import Counter
from typing import Any

from g6.core.discovery import _tokens

STAGES = ("facts", "model_check", "candidates", "next_action", "reroute", "validation", "semantic", "semantic_risk")
#: ``lexical`` only ranks; ``colbert`` is kept so a call that names it is not broken, and is answered lexically.
RERANKS = ("lexical", "colbert")
NOT_OFFERED = ("not offered here: a semantic rerank would depend on what this machine has installed and cached, "
               "which this reply never says; ranked lexically")
MAX_CANDIDATES = 8
MAX_CONSIDERING_BYTES = 64 * 1024   # ``considering`` past this is refused, as text or as a value
MAX_DEPTH = 16                      # and past this many nested lists or objects
MAX_CONSIDERING_ENTRIES = 64        # and past this many entries
MAX_TASK_BYTES = 16 * 1024          # ``task`` past this is refused: say what you are doing in fewer words
MAX_OUTCOME_BYTES = 64 * 1024       # ``last_outcome`` past this is refused: pass the refusal's code and message
MAX_REPLY_BYTES = 256 * 1024        # what the bounds above keep a reply within; past it the reply is refused
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
#: docstring; each is matched as whole words in the task, lower-cased, and a verb in ``SAFETY_VERBS`` in its word
#: forms too (``_forms``: pushes, pushed, pushing; deleting; dropping; and "safely").





def _forms(word: str) -> set[str]:
    """``word`` and, for a verb in ``SAFETY_VERBS``, its regular forms: -s, -es, -ed/-d, -ing (a final e dropped, a
    final consonant after a single vowel doubled), with ``_IRREGULAR``'s."""
    out = {word, *_IRREGULAR.get(word, ())}
    if word in SAFETY_VERBS:
        out |= {word + "s", word + "es", word + "ed", word + "ing"}
        if word.endswith("e"):
            out |= {word + "d", word[:-1] + "ing"}
        if re.fullmatch(r"[a-z]*[^aeiou][aeiou][bdgmnprt]", word):
            out |= {word + word[-1] + "ing", word + word[-1] + "ed"}
    return out
#: Fixed, documented intent priors, read by a reviewer: name -> (a pattern over the task's words, {phase, or "*" for
#: every phase: {entry id: bonus}}). ``action_safety`` is the task holding a ``SAFETY_WORDS`` word or phrase: the
#: action gate that decides allow, confirm, review or block is raised first, and the premortem with it while
#: planning. ``workflow_improvement`` is ``g6_find``'s own workflow-intent pattern (an agent that keeps failing on a
#: kind of task): the meta-harness's diagnosis, its start and the self-improvement loop's status. A test keeps every
#: id in the corpus.
INTENTS: dict[str, tuple[re.Pattern[str], dict[str, dict[str, float]]]] = {
    "action_safety": (
        re.compile(r"\b(" + "|".join(sorted((re.escape(f) for w in SAFETY_WORDS for f in _forms(w)), key=len,
                                            reverse=True)) + r")\b"),
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
#: The fixed prefixes G6's own MCP stack writes before a tool's error text, and no others: FastMCP's
#: (``fastmcp.tools.tool_manager``: ``Error calling tool 'name': ``) and the MCP SDK's
#: (``mcp.server.fastmcp.tools.base``: ``Error executing tool name: ``). G6's consent transport writes no code
#: (``g6.mcp.consent_transport``).
TRANSPORT_PREFIXES = (r"Error calling tool '[a-z][a-z0-9_]*': ", r"Error executing tool [a-z][a-z0-9_]*: ")
#: A text reply's code counts only at the very start of its first line (an optional ``[``), or right after one of
#: ``TRANSPORT_PREFIXES``; any other words before it never count, and the code that leads wins. Right after it, the
#: door's own refusal names the component and op (``[NAME_ERROR] op:``) or a sensitive ``component.op``. Nothing
#: later in the text is read.
_LEADING_CODE = re.compile(r"\s*(?:" + "|".join(TRANSPORT_PREFIXES) + r")?\[?(G6_E_[A-Z0-9_]+)\]?")
_DOOR_TAG = re.compile(r"\s*\[([A-Z][A-Z0-9_]*)_ERROR\]\s+([a-z][a-z0-9_]*):")
_LEAD_PAIR = re.compile(r"\s*([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)(?![\w.])")
_WORD_LINE = re.compile(r"(?:(?:ending|outcome|action)\s*:\s*)?([a-z][a-z_]*)")
STATES = ("verified", "qualified-draft", "blocked-escalated")
CONSENT = "G6_E_CLOUD_CONSENT_REQUIRED"
#: The envelope fields that name an outcome: a review's assessment, a mission's outcome, a review loop's ending and
#: action, read in this order.
OUTCOME_FIELDS = (("assessment", "outcome"), ("outcome",), ("ending",), ("action",))


def _remedy(action: str, tool: str = "", arguments: dict[str, Any] | None = None, *, tell: bool = False,
            setup: str = "") -> dict[str, Any]:
    return {"action": action, "tool": tool, "arguments": dict(arguments or {}), "tell_person": tell,
            "setup": setup, "available": tool not in PENDING_TOOLS}


#: Codes that only say what kind of result this is: every g6_review_patch result carries G6_E_PATCH_REVIEW, and
#: the outcome word beside it is what says what to do.
CARRIES_OUTCOME = frozenset({"G6_E_PATCH_REVIEW"})
_ASK = "Ask the person; offer no other way to agree, and never answer for them."
_SHOW = "Show the reply's report to the person as it is."
#: The remedies that are no refusal code's: an outcome word a reply names (a review's outcome, a loop's ending or
#: action, a meta-harness cause), ``component_limit`` (a code a component declares in ``__g6_limits__``) and
#: ``unknown_code`` (a refusal that carries no code). A code's remedy is never written here: it is declared where the
#: code is written (``g6.core.refusals``), and :func:`remedy_for` reads that declaration.
REMEDIES: dict[str, dict[str, Any]] = {
    "unknown_code": _remedy(
        "G6 has no remedy written for this refusal, so it is not known to be one you can fix: tell the person what "
        "it says. Never work around it.", tell=True),
    "component_limit": _remedy(
        "The component refused at a limit it declares. The override kernel can try a substitute for that part, "
        "with the person's agreement, beside the original refusal.", "g6_agentic_run",
        {"component": "<component>", "op": "<op>", "params_json": "<the same parameters>"}),
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
    "withheld": _remedy("The reply was withheld for consent: pass its code back as last_outcome, or ask the person "
                        "whether to share the source. Never grant for the person.", tell=True),
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
#: The outcome words a reply names (a review's outcome, a loop's ending or action, a meta-harness cause).
OUTCOME_WORDS = frozenset(k for k in REMEDIES if re.fullmatch(r"[a-z][a-z_]*", k)) - {
    "component_limit", "unknown_code"}


def input_codes(data: dict[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    """The component codes their writers declare ``input`` (``g6.core.refusals``): code -> the components that write
    it. A view of the declarations, kept for callers that read it as ``INPUT_CODES``; nothing decides by it."""
    found: dict[str, list[str]] = {}
    for name, codes in (data or _corpus())["refusals"].items():
        for code, row in codes.items():
            if row[0] == "input":
                found.setdefault(code, []).append(name)
    return {code: tuple(sorted(names)) for code, names in sorted(found.items())}


def __getattr__(name: str) -> Any:
    if name == "INPUT_CODES":
        return input_codes()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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


def _sensitive(data: dict[str, Any]) -> set[str]:
    """What the person can allow in G6's setup: each op the corpus lists as sensitive, and each tool's allowance."""
    return {e["id"][3:] for e in data["entries"] if e["kind"] == "op" and e["sensitive"]} | {
        e["needs"]["sensitive"] for e in data["entries"] if e["kind"] == "tool" and e["needs"]["sensitive"]}


def _field(value: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _read(outcome: str) -> dict[str, str]:
    """What ``last_outcome`` says, read only from G6's own reply. A reply passed back as JSON: its code is the
    envelope's own (``warning_card.code``, else a top-level or ``error`` code, else the code its ``error`` text
    leads with; ``cloud_consent_code`` when it is the consent code), its state is ``completion_state``, its outcome
    word the first of ``OUTCOME_FIELDS`` that names one, and its call the ``result``'s ``component`` and ``op``; a
    finished job's wrapped reply is read through. Plain text: only a code that leads its first line counts (the
    door's ``[NAME_ERROR] op:`` or a ``component.op`` right after it is read with it), or, with no code, a first
    line that is an outcome word (``defect_detected``, ``ending: open``). The content an op worked on and the rest
    of any text are never searched for codes, ``component.op`` pairs or outcome words."""
    seen = dict.fromkeys(("code", "state", "word", "component", "op", "pair"), "")
    text = (outcome or "").strip()
    try:
        value = json.loads(text) if text.startswith("{") else None
    except (ValueError, RecursionError):
        value = None
    lead = ""
    if isinstance(value, dict):
        if "job_id" in value and isinstance(value.get("result"), dict):
            value = value["result"]          # a finished job (metaharness_job) wraps the reply it ran
        for holder in (value.get("warning_card"), value, value.get("error")):
            code = holder.get("code") if isinstance(holder, dict) else None
            if isinstance(code, str) and _CODE.fullmatch(code):
                seen["code"] = code
                break
        error = value.get("error")
        first = error.split("\n", 1)[0] if isinstance(error, str) else ""
        found = _LEADING_CODE.match(first)
        if found and seen["code"] in ("", found.group(1)):
            seen["code"], lead = found.group(1), first[found.end():]
        if value.get("cloud_consent_code") == CONSENT:
            seen["code"] = CONSENT
        state = value.get("completion_state")
        seen["state"] = state if state in STATES else ""
        result = value.get("result")
        if isinstance(result, dict) and isinstance(result.get("component"), str) and isinstance(result.get("op"), str):
            seen["component"], seen["op"] = result["component"], result["op"]
        seen["word"] = next((w for w in (_field(value, p) for p in OUTCOME_FIELDS)
                             if isinstance(w, str) and w in OUTCOME_WORDS), "")
    else:
        first = text.split("\n", 1)[0]
        found = _LEADING_CODE.match(first)
        if found:
            seen["code"], lead = found.group(1), first[found.end():]
        else:
            said = _WORD_LINE.fullmatch(first.strip().lower())
            seen["word"] = said.group(1) if said and said.group(1) in OUTCOME_WORDS else ""
    tag = _DOOR_TAG.match(lead)
    if tag and not seen["component"]:
        seen["component"], seen["op"] = tag.group(1).lower(), tag.group(2)
    pair = _LEAD_PAIR.match(lead)
    seen["pair"] = pair.group(1) if pair else ""
    return seen


def recovery_remedies(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """``g6.metaharness.recovery``'s remedies for wrong answers, mapped by the component each names: a runnable
    component through ``g6_invoke`` (its ops listed), any other inside a candidate (``metaharness_propose``), and a
    remedy only a person can take to the person. Read from the corpus (which records them when it is built), so the
    meta-harness is not imported here."""
    data = data or _corpus()
    out = []
    for remedy in data["recovery"]:
        ops = data["components"].get(remedy["component"], {}).get("ops", [])
        person = remedy["who"] == "person"
        out.append({"answer_kind": remedy["answer_kind"], "component": remedy["component"],
                    "action": remedy["action"], "how": remedy["how"], "tell_person": person,
                    "tool": "" if person else "g6_invoke" if ops else "metaharness_propose",
                    "ops": [f"{remedy['component']}.{op}" for op in ops]})
    return out


def _template(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The corpus's call template for a tool, or for an op named ``component.op`` (a ``g6_invoke`` call)."""
    key = f"op:{name}" if "." in name else f"tool:{name}"
    entry = next((e for e in data["entries"] if e["id"] == key), None)
    return copy.deepcopy(entry["call"]) if entry else None


def remedy_for(outcome: str, data: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The remedy for a refusal or an outcome, or ``None`` when G6 has none, from what ``_read`` finds in G6's own
    reply. A refusal's code is resolved by its writer's declaration (``g6.core.refusals.resolve``: the component the
    reply names, else the layers, else the components that declare it, when they agree; a code nothing declares is
    the person's). The declaration's class decides: ``input``, read the component's contract with ``g6_describe`` (or,
    with no component named, fix the field in your own call); ``tool:<name>``, that call; ``retry``, the same call once
    more; ``person`` and ``none``, tell the person (the consent code alone opens its dialog, ``g6_cloud_grant``, where
    the person decides). A code in ``CARRIES_OUTCOME`` gives way to the reply's outcome word, a component's declared
    limit to ``component_limit``. A reply that was not refused has no remedy, unless its code's declaration names G6's
    own next call or it names an outcome word."""
    from g6.core import refusals

    data = data or _corpus()
    seen = _read(outcome)
    code, word, state = seen["code"], seen["word"], seen["state"]
    components = data["components"]
    named = seen["component"] if seen["component"] in components else ""
    refused = state not in ("verified", "qualified-draft")      # a bare code passed back counts as a refusal
    key, found = "", None
    if code:
        limits = set(components[named].get("limits", ())) if named else {
            c for comp in components.values() for c in comp.get("limits", ())}
        if code in limits:
            key = "component_limit"
        elif code in CARRIES_OUTCOME and word:
            key = word
        else:
            found = refusals.resolve(code, seen["component"], data["refusals"])
            if found.declared and (refused or found.kind == "tool"):
                key = "component_input" if found.kind == "input" and found.owner.startswith("component:") else code
            elif word:
                key, found = word, None                 # the reply's own outcome beside a code that does not reroute
            elif not refused:
                return None                             # not a refusal: nothing to reroute
            else:
                key, found = "unknown_code", None
    elif word:
        key = word
    elif state == "blocked-escalated":
        key = "unknown_code"
    else:
        return None
    if found is None:
        written = REMEDIES[key]
        action, tool, tell, setup = written["action"], written["tool"], written["tell_person"], written["setup"]
        arguments = dict(written["arguments"])
        if "component" in arguments and named:
            arguments["component"] = named
        fix_by = "person" if tell else f"tool:{tool}" if tool else "none"
    else:
        action, setup, fix_by, tool, tell, arguments = found.words, found.setup, found.fix_by, "", False, {}
        owners = found.owner.removeprefix("component:").split() if found.owner.startswith("component:") else []
        component = named or (owners[0] if len(owners) == 1 and owners[0] in components else "")
        if found.kind == "input":
            tool, arguments = ("g6_describe", {"component": component}) if component else ("", {})
            if state:
                action += f" The outcome beside it: {state}."
        elif found.kind == "tool":
            tool = found.fix_by[5:]
        elif found.kind in ("person", "none"):
            tool, tell = refusals.DIALOG.get(code, ""), code not in refusals.DIALOG
    # the setup step names an op or a tool's allowance (patch_review.review) only when it leads the refusal and the
    # corpus knows it as sensitive; any other text the agent passed stays out of G6's words
    pair = seen["pair"] if seen["pair"] in _sensitive(data) else ""
    if setup.endswith("<component.op>") and pair:
        setup = setup.replace("<component.op>", pair)
    template = _template(data, tool) if tool else None
    available = bool(tool) and tool in _registered(data)
    call = None
    if tool and "." in tool:
        call = template
    elif tool:
        call = {"tool": tool, "arguments": {**(template["arguments"] if template else {}), **arguments}}
    out = {"key": key, "code": code or key, "fix_by": fix_by, "action": action, "tell_person": tell, "tool": tool,
           "call": call, "setup": setup, "available": available if tool else True}
    # the library route: a sensitive op the server refused can run in the person's own Python, by their own allowance
    if code == "G6_E_SENSITIVE_COMPONENT_DENIED" and pair and f"op:{pair}" in {e["id"] for e in data["entries"]}:
        out["library"], out["library_op"] = _PERSONS_OWN, pair
        if _PERSONS_OWN not in out["action"]:
            out["action"] += " " + _PERSONS_OWN
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


class _Refusal(ValueError):
    """A refusal of the agent's input, in G6's own fixed words: no exception's or codec's text is ever passed on."""


_SURROGATE = re.compile("[\ud800-\udfff]")


def _serialisable(value: Any) -> Any:
    """``value`` with every lone surrogate in its text (a legal JSON escape a client may send, which UTF-8 cannot
    write) replaced by U+FFFD, so a reply that echoes agent text can always be sent."""
    if isinstance(value, str):
        return _SURROGATE.sub("�", value)
    if isinstance(value, dict):
        return {_serialisable(k): _serialisable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialisable(v) for v in value]
    return value


def _size(text: str) -> int:
    """``text``'s UTF-8 size; a lone surrogate counts as its three bytes instead of raising."""
    return len(text.encode("utf-8", "surrogatepass"))


_OVER = f"considering is over {MAX_CONSIDERING_BYTES // 1024} KB"
_DEEP = f"considering nests deeper than {MAX_DEPTH} levels"
_MANY = f"considering holds more than {MAX_CONSIDERING_ENTRIES} entries"


def _bounded(value: Any) -> None:
    """Refuse a value over ``MAX_CONSIDERING_BYTES`` (as JSON would write it, near enough) or nested deeper than
    ``MAX_DEPTH``. The walk is lazy and stops at the first excess, so a wide or deep value costs no more than the
    bound to refuse; a value that is not JSON (an object, bytes, a number too long to write) is refused too."""
    budget, end = MAX_CONSIDERING_BYTES, object()
    stack: list[tuple[Any, int]] = [(iter((value,)), 0)]
    while stack:
        items, level = stack[-1]
        item = next(items, end)
        if item is end:
            stack.pop()
            continue
        if isinstance(item, dict):
            budget -= 2
            children: Any = itertools.chain.from_iterable(item.items())
        elif isinstance(item, (list, tuple)):
            budget -= 2
            children = iter(item)
        else:
            if isinstance(item, str):
                if len(item) > budget:
                    raise _Refusal(_OVER)
                budget -= _size(item) + 3
            elif item is None or isinstance(item, (bool, float)):
                budget -= 6
            elif isinstance(item, int):
                if item.bit_length() > 64:
                    raise _Refusal("considering holds a number too long to read")
                budget -= len(str(item)) + 1
            else:
                raise _Refusal("considering holds only JSON values: text, numbers, lists and objects")
            if budget < 0:
                raise _Refusal(_OVER)
            continue
        if level + 1 > MAX_DEPTH:
            raise _Refusal(_DEEP)
        if budget < 0:
            raise _Refusal(_OVER)
        stack.append((children, level + 1))


def _items(considering: Any) -> list[Any]:
    """``considering`` as a list of entries. Raises ``_Refusal`` (G6's own words) when it is over
    ``MAX_CONSIDERING_BYTES``, holds more than ``MAX_CONSIDERING_ENTRIES`` entries, nests deeper than ``MAX_DEPTH``,
    is JSON that does not decode, or is not a JSON value. Each bound is measured before the value is walked."""
    if isinstance(considering, (list, tuple, dict)):
        items = [considering] if isinstance(considering, dict) else considering
        if len(items) > MAX_CONSIDERING_ENTRIES:
            raise _Refusal(_MANY)
        _bounded(considering)
        return list(items)
    if not isinstance(considering, str):
        return []
    if len(considering) > MAX_CONSIDERING_BYTES or _size(considering) > MAX_CONSIDERING_BYTES:
        raise _Refusal(_OVER)
    text = considering.strip()
    if text.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
        except (RecursionError, ValueError, TypeError):
            raise _Refusal("considering starts as JSON but does not decode") from None
        items = parsed if isinstance(parsed, list) else [parsed]
        if len(items) > MAX_CONSIDERING_ENTRIES:
            raise _Refusal(_MANY)
        _bounded(parsed)
        return items
    items = [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]
    if len(items) > MAX_CONSIDERING_ENTRIES:
        raise _Refusal(_MANY)
    return items


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


#: ``verification_controller.next_experiment``'s severity weights.
SEVERITY_WEIGHTS = {"low": 1, "medium": 3, "high": 9}


def _schedule(hypotheses: list[dict[str, Any]], entries: dict[str, dict[str, Any]]) -> tuple[str, str]:
    """The next call by the rule of ``g6.harness.verification_controller.next_experiment`` (greedy: the most
    severity-weighted open-hypothesis coverage per cost unit within the budget, the id breaking a tie), or
    ``("", why)`` when none fits. The rule is applied here over the corpus rather than imported, since importing it
    loads components at request time; a test holds the two to the same pick."""
    named = {h["check"]: h["id"] for h in hypotheses if h["check"]}
    weights = {h["id"]: SEVERITY_WEIGHTS[h["severity"]] for h in hypotheses}
    experiments = []
    for key, entry in sorted(entries.items()):
        if entry["kind"] == "route":
            continue
        covers = sorted((set(entry.get("hypotheses", ())) & set(weights)) | ({named[key]} if key in named else set()))
        if covers:
            experiments.append((key, covers, COST_UNITS[entry["cost"]]))
    possible = [e for e in experiments if e[2] <= BUDGET_UNITS]
    if not possible:
        return "", f"next_experiment found no call within {BUDGET_UNITS} units that addresses an open hypothesis"
    key, covers, _ = min(possible, key=lambda e: (-sum(weights[i] for i in e[1]) / e[2], e[0]))
    return key, (f"next_experiment: the most severity-weighted open-hypothesis coverage per cost unit within "
                 f"{BUDGET_UNITS} units ({', '.join(covers)}; {len(experiments)} calls compared)")


#: What to put in a template whose op refuses it as given, where G6 can say it in one line; any other such template
#: says which fields to fill, from the corpus's record of the refusal.
FILL_HINTS = {
    "op:action_gating.evaluate": 'Fill action with the act you are about to take, as {"action_type": "run_code", '
                                 '"target_resource": "git push origin main"} (action_type is one the gate profiles: '
                                 "write_file, run_code, call_api, send_email, payment, account_deletion, "
                                 "production_infrastructure_change, permission_escalation, plc_write, gcode_execute): "
                                 "the gate decides allow, confirm, review or block before you act.",
}


def _fill_say(candidate: dict[str, Any]) -> str:
    """What ``next`` says about an op's template: for one that ran as given on placeholders or defaults, to fill
    them before relying on its answer; for one not known to run as given, the fields to fill and why."""
    if candidate["kind"] != "op":
        return ""
    if candidate["validated"]:
        fields = candidate["fill"]
        return (f"This call ran as given when G6 was built, but only on placeholders or defaults: fill "
                f"{', '.join(fields)} with your own values before relying on its answer; a field its signature "
                f"names but you leave out (the act a gate decides on, the evidence) can change that answer."
                if fields else "")
    hint = FILL_HINTS.get(candidate["id"], "")
    others = [f for f in candidate["fill"] if not re.search(rf"\b{re.escape(f)}\b", hint)]
    if hint and others:
        hint += (f" Also fill {', '.join(others)} where they apply: a field its signature names but you leave out "
                 "can change the answer.")
    if not hint:
        fields = candidate["fill"]
        hint = (f"Fill {', '.join(fields)} with your own values before you make this call." if fields else
                f"Read g6_describe {candidate['component']} for the fields this call needs before you make it.")
    return hint + (f" {candidate['issues'][-1]}" if candidate["issues"] else "")


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


#: At most this many checked library forms, ranked after MCP calls and never ``next``.
LIBRARY_SLOTS = 2
_PERSONS_OWN = ("This op is sensitive. The person decides whether to allow it themselves. "
                "Tell the person and stop.")


def library_sketch(component: str, function: str, data: dict[str, Any] | None = None) -> str:
    """Only a registered operation may be offered through the checked library door."""
    return invoke_sketch([f"{component}.{function}"], data)


def invoke_sketch(steps: list[str], data: dict[str, Any] | None = None) -> str:
    """``g6.invoke`` calls, one per ``component.op`` the corpus offers, each with its template's parameters; a
    sensitive op or an op the corpus lacks has no agent-facing sketch."""
    data = data or _corpus()
    entries = {e["id"]: e for e in data["entries"] if e["kind"] == "op"}
    lines = ["import g6", ""]
    for index, step in enumerate(steps, 1):
        entry = entries.get(f"op:{step}")
        if entry is None or entry["sensitive"] or not entry["validated"] or entry["issues"]:
            return ""
        component, op = step.split(".", 1)
        params = repr(json.loads(entry["call"]["arguments"]["params_json"]))
        lines.append(f'step{index} = g6.invoke("{component}", "{op}", {params})')
    return "\n".join(lines)


def _library_candidate(key: str, code: str, component: str, *, why: str, by: str, score: float = 0.0,
                       fill: list[str] | None = None) -> dict[str, Any]:
    return {"id": key, "kind": "library", "call": {"python": code}, "component": component,
            "by": "agent", "step": "",
            "establishes": "the component's answer through the checked Python door; nothing executed here",
            "not_established": "nothing ran; " + why,
            "needs": {"sensitive": "", "consent": [], "model": "none", "confined": False, "outside": [],
                      "extras": []},
            "cost": "low", "ranked_by": [by], "score": round(score, 3), "validated": True, "issues": [],
            "fill": list(fill or []), "allowed_by_default": True,
            "acts": "", "runs_over_mcp": False}


def _library_route(task: str, data: dict[str, Any], documents: dict[str, str], remedy: dict[str, Any] | None,
                   calls: list[str]) -> list[dict[str, Any]]:
    """Offer only templates that passed through the door without a person's permission.

    Blockless components have no checked operation, so never get a library candidate.
    """
    out = []
    ops = [k[3:] for k in calls if k.startswith("op:")]
    if len(ops) >= 2:
        code = invoke_sketch(ops, data)
        if code:
            out.append(_library_candidate("library:compose", code, ops[0].split(".", 1)[0], by="considering",
                                          why="the ops you are considering, chained in Python; fill each step's "
                                              "parameters", fill=ops))
    return out[:LIBRARY_SLOTS]


@scoped
def select(task: str, phase: str = "", last_outcome: str = "", considering: Any = "", limit: int = 5,
           rerank: str = "lexical", *, corpus: dict[str, Any] | None = None,
           request_id: str = "", run_id: str = "", _history=None) -> dict[str, Any]:
    """The selection for the agent's situation (module docstring). ``corpus`` stands in for the shipped corpus in
    tests."""
    from g6.core.reliability_envelope import envelope_fields, warning_card
    from g6.skills.corpus import PHASES, _expanded as expanded     # the reply is deep-copied at the end

    if not isinstance(task, str) or not task.strip():
        return _refuse("task says what you are doing", request_id, run_id)
    if len(task) > MAX_TASK_BYTES or _size(task) > MAX_TASK_BYTES:
        return _refuse(f"task is over {MAX_TASK_BYTES // 1024} KB: say what you are doing in fewer words",
                       request_id, run_id)
    if phase not in ("", *PHASES):
        return _refuse(f"phase is one of {', '.join(PHASES)}, or empty", request_id, run_id)
    if rerank not in RERANKS:
        return _refuse("rerank is lexical: a semantic rerank is not offered here (it would depend on what this "
                       "machine has installed)", request_id, run_id)
    if not isinstance(limit, int) or isinstance(limit, bool):
        return _refuse("limit is a whole number", request_id, run_id)
    if not isinstance(last_outcome, str):
        return _refuse("last_outcome is a refusal's text, its G6_E_* code or an outcome word", request_id, run_id)
    if len(last_outcome) > MAX_OUTCOME_BYTES or _size(last_outcome) > MAX_OUTCOME_BYTES:
        return _refuse(f"last_outcome is over {MAX_OUTCOME_BYTES // 1024} KB: pass the refusal's code and message, "
                       "or the outcome word", request_id, run_id)
    limit = max(1, min(limit, MAX_CANDIDATES))
    try:                                            # bounded before anything else is read
        items = _items(considering)
    except _Refusal as refused:
        return _refuse(str(refused), request_id, run_id)
    # The adapter's host-repository reader runs only after all caller argument validation.
    if callable(_history):
        _history = _history()
    data = corpus if corpus is not None else _corpus()
    entries = {e["id"]: e for e in expanded(data)}
    # History controls stay discoverable when asked for, but adding them must not change an ordinary cold
    # selection's IDF population or evidence count. Admission, never caller text, enables history-based ranking.
    history_requested = re.search(r'\b(?:learning_history|history|retained|forget)\b', task, re.IGNORECASE)
    history_considered = any('learning_history' in json.dumps(item) for item in items)
    if not (_history is not None and _history.rows) and not history_requested and not history_considered:
        entries = {key: entry for key, entry in entries.items() if entry.get('component') != 'learning_history'}
    tools = {e["call"]["tool"]: e["id"] for e in data["entries"] if e["kind"] == "tool"}
    mechanisms: list[dict[str, Any]] = []

    # 1. facts
    considered = _considered(items, entries, tools)
    requirements = [c for c in considered if c["kind"] == "requirement" and c["validated"]]
    hypotheses = [c for c in considered if c["kind"] == "hypothesis" and c["validated"]]
    calls = [c["id"] for c in considered if c["kind"] == "call" and c["validated"]]
    seen = _read(last_outcome)
    outcome = seen["code"] or seen["word"] or ("not recognised" if last_outcome.strip() else "")
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
    from g6.core.risk_signals import _floor_classify
    intents = [name for name, (pattern, _) in INTENTS.items() if pattern.search(words)]
    # Ambient model configuration is a host fact. Only an explicit request caller may interpret this task.
    with semantic.use_request(llm=None):
        risk_reading = semantic.decide("risk", task, _floor_classify(task)[0], ("R0", "R1", "R2", "R3"))
        safety = semantic.decide("safety_checks", task,
                                 ["action_safety"] if "action_safety" in intents else [], ("action_safety",))
    if "action_safety" in safety.value and "action_safety" not in intents:
        intents.append("action_safety")
    for name in intents:
        by_phase = INTENTS[name][1]
        for key, value in {**by_phase.get("*", {}), **by_phase.get(phase, {})}.items():
            add(key, value, "intent_prior")
    for key in calls:
        add(key, CONSIDERED_BONUS, "considering")
    # Python-only evaluator input supplied by the MCP adapter, never a tool argument.
    if _history is not None and _history.rows:
        for key, value in _history.candidate_scores(entries, phase).items():
            if key in scores:
                scores[key] += value
                ranked_by.setdefault(key, []).append(_history.note)
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
            first = f"op:{remedy['tool']}" if "." in remedy["tool"] else f"tool:{remedy['tool']}"
        mechanisms.append({"stage": "reroute", "ran": True,
                           "why": f"remedy {remedy['key']} ({remedy['fix_by']}): " + (
                               "tell the person" if remedy["tell_person"] else "call " + remedy["tool"] if first else
                               f"{remedy['tool']} is not registered on this server" if remedy["tool"] else
                               "change your own call")})

    # the remedy first, then the scheduler's pick and the calls you are considering, kept within the limit; the rest
    # by score, and all but the remedy in score order
    # the library route's Python forms take the last slots, after every call that runs now over MCP
    named_ops = list(dict.fromkeys(c["id"] for c in considered if c["kind"] == "call" and c.get("id")))
    library = _library_route(task, data, documents, remedy, named_ops)
    slots = max(1, limit - len(library))
    must = list(dict.fromkeys(k for k in (first, scheduled, *calls) if k))[:slots]
    rest = [k for k in order if k not in must][:slots - len(must)]
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
                     "fill": list(entry.get("fill", ())) if call == entry["call"] and entry["kind"] == "op" else [],
                     "allowed_by_default": not entry["needs"]["sensitive"], "acts": acting[key]}
        if entry["kind"] == "op":
            candidate["component"] = entry["component"]
            if entry["sensitive"]:
                candidate.update(by="person", step=_PERSONS_OWN, call=None,
                                 establishes=_PERSONS_OWN, validated=False)
        candidates.append(candidate)
    library = library[:max(0, limit - len(candidates))]
    candidates += library
    mechanisms.append({"stage": "validation", "ran": bool(candidates),
                       "why": "tool templates against their recorded schemas; op templates by the door's typed input "
                              "check and a run as given through g6_invoke, made when the corpus was built from "
                              "shipped code; a Python form names only what the corpus lists"
                              + (f" ({len(library)} offered, after every call over MCP, never next)" if library
                                 else "")})

    # the op whose outcome last_outcome reports (the reply's own component and op) is never next again
    reported = f"op:{seen['component']}.{seen['op']}" if seen["component"] and seen["op"] else ""
    # g6_cloud_grant asks the person to share data: next only as the remedy for the consent code itself
    open_ = [c["id"] for c in candidates if not c["acts"] and c["id"] != reported and c["kind"] != "library"
             and (c["id"] != "tool:g6_cloud_grant" or c["id"] == first)]
    if remedy is not None and remedy["tell_person"]:
        say = remedy["action"] + (f" The step: {remedy['setup']}." if remedy["setup"] else "")
        nxt = {"id": None, "call": None, "tell_person": True, "say": say, "why": f"remedy {remedy['key']}",
               "fill": []}
    elif remedy is not None and not first and remedy["fix_by"] in ("input", "retry"):
        # fix the field in the call you made, or make it once more: your own call, which G6 does not name here
        nxt = {"id": None, "call": None, "tell_person": False, "say": remedy["action"],
               "why": f"remedy {remedy['key']}", "fill": []}
    elif open_:
        # a call that acts outside G6 is never next: the scheduler's pick only when it does not act
        pick = first or (scheduled if scheduled in open_ else "") or open_[0]
        found = next(c for c in candidates if c["id"] == pick)
        nxt = {"id": pick, "call": found["call"], "tell_person": False, "say": _fill_say(found),
               "validated": found["validated"], "fill": found["fill"],
               "why": "remedy " + remedy["key"] if pick == first else why if pick == scheduled else
               "the first candidate that does not act outside G6"
               + (", and is not the call last_outcome reports" if reported else "")}
    elif candidates:
        nxt = {"id": None, "call": None, "tell_person": True, "why": "every candidate acts outside G6 or was just made",
               "say": "Every call offered here acts outside G6's checks or is the one just made: ask the person "
                      "before any of them.", "fill": []}
    else:
        nxt = {"id": None, "call": None, "tell_person": False, "say": "Nothing matched: describe the task in other "
               "words, or call g6_find.", "why": "no candidate", "fill": []}
    rankers = "fixed rules with semantic safety suggestions" if safety.by != "floor" else "fixed rules"
    activity = "no proposed call ran" if safety.by != "floor" or risk_reading.by != "floor" else "nothing ran"
    card = warning_card("G6_E_QUALIFIED_DRAFT",
                        f"g6_select: a proposal ranked by {rankers} over G6's own text; {activity} and nothing "
                        "about your task is checked",
                        "Choose a call and make it; the door checks it then. Pass a refusal back as last_outcome to "
                        "reroute.")
    if _history is not None and _history.rows:
        card['message'] = 'g6_select: fixed rules and ' + _history.note + '; no check ran on this task'
    for reading, stage in ((safety, "semantic"), (risk_reading, "semantic_risk")):
        mechanism = {**reading.mechanism(), "stage": stage}
        if reading.by == "floor":
            mechanism["reason"] = "deterministic floor; no semantic reading used"
        mechanisms.append(mechanism)
    evidence = [{"kind": "selection", "corpus_entries": len(entries), "corpus_release": data.get("release", ""),
                 "stages_run": [m["stage"] for m in mechanisms if m["ran"]]}]
    # a deep copy: candidates hold the cached corpus's own objects, which a caller filling in a template must not
    # change; and any agent text it echoes made writable as UTF-8
    reply = _serialisable({
        "ok": True, "phase": phase, "candidates": candidates, "next": nxt, "remedy": remedy,
        "considering": considered, "mechanisms": mechanisms, "unknown_until_observed": list(UNKNOWN_UNTIL_OBSERVED),
        **envelope_fields(reliability_label="qualified-draft", warning_card=card, evidence=evidence,
                          request_id=request_id, run_id=run_id)})
    if _history is not None and (_history.rows or _history.rejected):
        reply['learned'] = _history.learned()
        reply['evidence_note'] = _history.note
    if len(json.dumps(reply)) > MAX_REPLY_BYTES:       # the input bounds keep a reply well within it; this says so
        return _refuse(f"the reply would be over {MAX_REPLY_BYTES // 1024} KB: pass fewer or shorter entries in "
                       "considering", request_id, run_id)
    return reply


#: How the prompt hook's hint begins (``hint``).
HINT_PREFIX = "G6 calls that may fit at the start of a task: "


def hint(prompt: str) -> str:
    """The few words the prompt hook adds when a task starts (plan revision 8, A): the first two calls
    ``select(prompt, "plan")`` puts first (by its plan-phase rules and shared words), by name only (an op as
    ``g6_invoke component.op``), then the lead of G6's instructions (``g6.mcp.instructions.LEAD``); at most 60 words.
    Fixed rules, no model; nothing of the prompt is repeated. ``""`` for an empty prompt. A route (a skill section to
    read) is not a call, and a sensitive op is refused until the person allows it, so both are passed over.
    ``select``'s reply is unchanged by it."""
    from g6.mcp.instructions import LEAD

    if not isinstance(prompt, str) or not prompt.strip():
        return ""
    names = []
    for candidate in select(prompt, "plan")["candidates"]:
        call = candidate["call"]
        if "tool" not in call or (candidate["kind"] == "op" and candidate["needs"]["sensitive"]):
            continue
        names.append(call["tool"] + (f" {call['arguments']['component']}.{call['arguments']['op']}"
                                     if candidate["kind"] == "op" else ""))
        if len(names) == 2:
            break
    return f"{HINT_PREFIX}{', '.join(names)}. {LEAD}" if names else LEAD


__all__ = ["BUDGET_UNITS", "COST_UNITS", "HINT_PREFIX", "MAX_CANDIDATES", "PENDING_TOOLS", "PRIORS", "REMEDIES",
           "STAGES", "hint", "recovery_remedies", "remedy_for", "select"]
