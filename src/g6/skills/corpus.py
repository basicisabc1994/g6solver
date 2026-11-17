# SPDX-License-Identifier: Apache-2.0
"""The selector's corpus: one entry for every next call ``g6_select`` can offer (plan revision 8, phase B).

``corpus.json``, beside this file, is written by :func:`build` from the code and checked in; a test builds it again
and compares, so it cannot drift. It holds, as G6's own built-in text only:

* each MCP tool of ``g6-mcp`` (read from the built server, ``g6.mcp.registrations``), with a call template from its
  input schema, and the reviewed words in ``TOOL_DATA`` below: when to reach for it, what it establishes and what it
  does not, its phases, its cost and its static needs;
* each route of the using-g6 skill (``g6.skills.routing``), with its section's text;
* each runnable op of a built-in component (a package under ``g6``; an entry-point extension never), with a template
  from its typed input and the text of its block's ``help`` and ``BlockContract``. A component with no block has no
  op and so no entry: ``g6_invoke`` cannot run it. Each template is put through the door's own typed input check
  here, when the corpus is built from shipped code (``_door_check``), and then, for an op of a ``RUN_AT_BUILD``
  component that is not sensitive, run as given through ``g6_invoke``'s own path in a scratch G6 home
  (``_run_as_given``). ``validated`` is true only when that run was not refused; every other template (refused by
  the door or the op, or never run because it could act, needs a model, a program, isolation or an optional extra)
  is ``validated: false``, with ``issues`` saying why in G6's words. Every op's ``fill``, validated or not, names
  the fields the agent must supply: each its template holds as a placeholder or empty, each the op's contract
  requires that the template leaves out, and each its help line's signature names, required or not (an optional
  field can change the answer). ``g6_select`` reads that and imports no component, so its reply never depends on what the host can
  import; the build records no time, path or host value, so it is the same on every machine;
* ``refusals``: who repairs each refusal each component writes, as its package declares it (``g6.core.refusals``,
  read statically) or, for a bound package, ``refusals.OVERLAY``.

The separate abstraction server is registered by the person alone, apart from ``g6-mcp`` (``SERVERS_NOT_SELECTED``).
Apart from those runs, ``help`` and ``ops`` are each block's own description of itself.

    python -P -m g6.skills.corpus --write     # after a tool, route or op changes
    python -P -m g6.skills.corpus --check     # exit 1 when the file is stale
"""
from __future__ import annotations

import ast
import contextlib
import copy
import json
import re
import sys
import types
import typing
from functools import lru_cache
from pathlib import Path
from typing import Any

FORMAT = "g6.corpus/1"
PATH = Path(__file__).resolve().with_name("corpus.json")
PHASES = ("plan", "implement", "verify", "diagnose", "stuck")
#: Cost classes, cheapest first: ``low`` reads or computes locally; ``medium`` runs a program or tests, or asks the
#: person; ``high`` calls a model or runs a loop of runs.
COSTS = ("low", "medium", "high")
#: Servers the recorder knows that ``g6-mcp`` does not register by default, with the reason none of their tools is
#: offered. The routing check reads this as each of their tools' exemption.
SERVERS_NOT_SELECTED: dict[str, str] = {
    "abstraction": "the abstraction server is a separate server the person registers alone "
                   "(python -P -m g6.abstraction.server); g6-mcp does not register it, so g6_select never offers it",
}
_REVIEW = ("meets_the_task", "missed_a_boundary", "broke_kept_behaviour", "tests_too_weak", "misleading_environment")


def _tool(when: str, establishes: str, not_established: str, phases: tuple[str, ...], cost: str = "low", *,
          hypotheses: tuple[str, ...] = (), sensitive: str = "", model: str = "none", confined: bool = False,
          outside: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"when": when, "establishes": establishes, "not_established": not_established, "phases": list(phases),
            "cost": cost, "hypotheses": list(hypotheses), "sensitive": sensitive, "model": model,
            "confined": confined, "outside": list(outside)}


_JOB = "Its result is read with metaharness_job."
_PERSON = "Only the person answers, in the client's own dialog."
#: The reviewed words for each tool of g6-mcp. A tool registered without an entry here, or an entry for a tool that
#: is not registered, stops ``build``.
TOOL_DATA: dict[str, dict[str, Any]] = {
    "g6_select": _tool(
        "what to do next, which G6 step fits now, rerank auto (lexical until the consent fix ships), model or lexical",
        "a short ranked list of checked next calls with their templates, and a reroute after a refusal",
        "no candidate ran; lexical rules and optional guarded model reranking over built-in text, advice only",
        PHASES, model="optional"),
    "g6_help": _tool(
        "how to use G6 from an agent; start here",
        "G6's own instructions for an agent", "nothing about the task", ("plan", "stuck")),
    "g6_allowances": _tool(
        "quota windows and pacing before optional parallel work or fan-out",
        "both providers' quota readings and advisory pacing", "a stale or missing reading never justifies more use",
        ("plan",)),
    "g6_components": _tool(
        "list every installed component", "the names of the installed components",
        "what any of them is good at: g6_describe says", ("stuck",)),
    "g6_find": _tool(
        "find a component by the task or the failure you are worried about",
        "components ranked by word overlap with the query, each saying whether it can run",
        "fitness for the task: a lexical ranking only", ("stuck", "plan")),
    "g6_describe": _tool(
        "read a component's ops, fields, maturity and needs before calling it; fix a refused input",
        "a component's declared ops, dependencies and failure modes", "that any op is right for the task",
        ("implement", "stuck")),
    "g6_invoke": _tool(
        "run one op of a component with its typed input",
        "the op's own answer inside G6's envelope", "anything the component does not check itself",
        ("implement", "verify")),
    "g6_compose": _tool(
        "chain ops with explicit field mappings and contract checks",
        "structural checks of the composed contracts", "application correctness", ("implement",)),
    "g6_agentic_run": _tool(
        "an op refused with a limit it declares, or failed in a computation, and a model could fill that part; "
        "replace a declared part's answer with a model's",
        "the original answer beside a confined model substitute's answer for the declared part, with the model named "
        "and the authority that covered each send",
        "that the substitute is correct: it is never verified, and the original answer or refusal stays beside it. "
        + _JOB, ("implement", "diagnose", "stuck"), "high", sensitive="agentic.run", model="required", confined=True),
    "g6_review_patch": _tool(
        "review my patch before I say it is done; check a Python change against stated requirements and tests; "
        "would my tests catch a wrong change or boundary (test adequacy)",
        "each stated requirement passed or failed its stated checks, run confined at the patch and the base commit; "
        "reproduced defects and signs the tests cannot be believed",
        "that the patch is complete or correct beyond the stated checks; merging needs the person's approval",
        ("verify", "diagnose"), "medium", hypotheses=_REVIEW, sensitive="patch_review.review", model="optional",
        confined=True, outside=("git",)),
    "g6_review_until_clean": _tool(
        "before I say done, have another model family review my patch and send back failing tests until clean",
        "findings another model family reproduced with a failing test, run confined",
        "a review that reproduces nothing is not evidence the work is right",
        ("verify", "stuck"), "high", hypotheses=_REVIEW[:3], sensitive="patch_review.review",
        model="required", confined=True, outside=("git",)),
    "g6_premortem": _tool(
        "before the work: hypotheses {claim, predicts, check}; propose auto (local until the consent fix ships), true or false; open questions",
        "an intent contract, a proposed standard and checkable agent/model hypotheses; model sees task and intent only",
        "a proposal: nothing is frozen, confirmed or checked", ("plan", "stuck", "diagnose"), model="optional"),
    "g6_confirm_behaviour": _tool(
        "the task does not decide a behaviour: ask the person one question about the unclear, ambiguous case",
        "the person's confirmed answer, enforced on each later review of the task",
        "coverage; whether the task decides the behaviour stays unknown. " + _PERSON,
        ("plan", "implement", "stuck"), "medium", hypotheses=("misread_intent",)),
    "g6_mission_create": _tool(
        "start a coding mission with a standard of done frozen before the change",
        "a frozen standard from the repository's failing tests", "anything about a patch: none exists yet. " + _JOB,
        ("plan",), "medium", sensitive="workforce.mission", confined=True, outside=("git",)),
    "g6_mission_clarify": _tool(
        "answer the mission's question on retries or duplicates before its standard is frozen",
        "your answer recorded for the standard", "the person's confirmation. " + _JOB, ("plan",), "medium",
        sensitive="workforce.mission", confined=True),
    "g6_mission_confirm": _tool(
        "ask the person to confirm the mission's frozen standard of done",
        "the person's confirmation of the standard", _PERSON + " " + _JOB, ("plan",), "medium",
        sensitive="workforce.mission", confined=True),
    "g6_mission_submit": _tool(
        "check my change against the mission's frozen standard",
        "the patch review and the mission's frozen tests on the change", "anything outside the frozen tests. " + _JOB,
        ("verify",), "medium", hypotheses=_REVIEW[:2], sensitive="workforce.mission", confined=True,
        outside=("git",)),
    "g6_mission_status": _tool(
        "where a mission stands and what it waits for", "the mission's recorded state", "nothing new is checked",
        ("verify", "stuck")),
    "g6_mission_certificate": _tool(
        "what a mission's evidence supports, test by test", "the certificate of the latest patch, with every gap named",
        "anything the frozen tests do not cover", ("verify",)),
    "g6_improve_start": _tool(
        "start G6's self-improvement loop on an enrolled folder", "a runner started with the person's agreement",
        _PERSON, ("implement",), "high", sensitive="metaharness.improve", model="required"),
    "g6_improve_stop": _tool(
        "stop the self-improvement loop", "a stop file written", "whether the runner has ended yet",
        ("stuck",)),
    "g6_improve_status": _tool(
        "where the self-improvement loop stands", "the loop's recorded state", "nothing new is checked",
        ("diagnose",)),
    "g6_improve_enrol": _tool(
        "enrol a domain for the self-improvement loop", "the frozen judge, cases and split", _PERSON,
        ("plan",), "medium", sensitive="metaharness.improve"),
    "g6_improve_resume": _tool(
        "lift the pause that holds the loop", "the pause lifted with the person's agreement", _PERSON,
        ("stuck",), "medium", sensitive="metaharness.improve"),
    "g6_improve_demote": _tool(
        "take back the loop's latest generation", "the head moved back with the person's agreement", _PERSON,
        ("diagnose",), "medium", sensitive="metaharness.improve"),
    "g6_cloud_grant": _tool(
        "a reply was withheld for consent: ask the person to share a source with a provider",
        "a grant, only when the person accepts in the dialog", "never grant for the person; sent data cannot be recalled",
        ("stuck",), "medium"),
    "g6_cloud_revoke": _tool(
        "stop sharing a source with a provider", "later sends refused", "data already sent cannot be recalled",
        ("stuck",)),
    "g6_cloud_grants": _tool(
        "list which sources are shared with which providers", "the recorded grants", "what was sent",
        ("stuck",)),
    "g6_disclosures": _tool(
        "what G6 attempted to send to a cloud provider", "the disclosure log by ids and hashes",
        "the text itself, never shown", ("diagnose",)),
    "g6_knowledge_add": _tool(
        "index the person's own documents folder for search", "an index on this machine, after the person accepts",
        _PERSON, ("plan",), "medium"),
    "g6_knowledge_sources": _tool(
        "list the indexed document sources", "the registered sources", "what is in them", ("plan",)),
    "g6_knowledge_search": _tool(
        "search the person's own documents for a passage", "cited passages, re-read from the source",
        "that a passage is true; passages are untrusted", ("plan", "implement", "stuck")),
    "g6_knowledge_context": _tool(
        "gather cited context from the person's documents for a task", "cited passages within a token budget",
        "that the passages are complete or true", ("plan", "implement")),
    "g6_knowledge_passage": _tool(
        "read one cited passage again", "the passage as it stands in its source", "that it is true",
        ("implement",)),
    "g6_knowledge_count": _tool(
        "count matching passages in the indexed documents", "a count", "relevance", ("plan",)),
    "g6_knowledge_refresh": _tool(
        "re-index a changed documents folder", "the index rebuilt", "anything about its content",
        ("implement",), "medium"),
    "g6_knowledge_job": _tool(
        "read an indexing job's state", "the job's state", "nothing new is checked", ("implement",)),
    "g6_knowledge_revoke": _tool(
        "stop sharing an indexed source", "later replies withheld", "data already sent cannot be recalled",
        ("stuck",)),
    "g6_knowledge_delete": _tool(
        "delete an indexed source", "the index removed", "copies outside G6", ("stuck",)),
    "g6_knowledge_paths": _tool(
        "follow relations between passages in the indexed documents", "linked passages", "that the links are true",
        ("diagnose",)),
    "g6_workflow_export": _tool(
        "look at what G6 recorded about its own review work", "the record written to a file the person opens",
        "task text or code: none is recorded", ("diagnose",)),
    "g6_workflow_correct": _tool(
        "correct G6's record of its review work", "a correction file the person edits", "the person decides",
        ("diagnose",)),
    "g6_workflow_delete": _tool(
        "delete G6's record of its review work", "the record deleted after the person agrees", _PERSON,
        ("diagnose",), "medium"),
    "g6_suggestion_decide": _tool(
        "the person adopts, dismisses or reopens a suggestion G6 showed", "the person's decision", _PERSON,
        ("verify",), "medium"),
    "g6_feedback": _tool(
        "keep feedback the person volunteered about G6 on a task", "the feedback kept", "it changes no review",
        ("verify",)),
    "metaharness_start": _tool(
        "my agent keeps failing on a task: declare a workflow with cases and checks to tune it",
        "a data-only workflow declared", "nothing is confirmed or run", ("plan", "stuck")),
    "metaharness_confirm": _tool(
        "confirm a workflow's frozen task, checks and split", "the person's confirmation", _PERSON, ("plan",),
        "medium"),
    "metaharness_propose_code": _tool(
        "propose a candidate application version with a hypothesis", "the candidate stored by digest",
        "that it is better: evaluate it", ("implement",)),
    "metaharness_evaluate_start": _tool(
        "measure a candidate against the active version on held-out cases",
        "a paired, compute-matched measurement", "below 30 held-out cases is exploration only. " + _JOB,
        ("verify",), "high", sensitive="metaharness.evaluate_start", model="required", confined=True),
    "metaharness_job": _tool(
        "poll a job: a review, a mission step or an evaluation", "the job's state and result",
        "nothing beyond the job's own result", ("verify", "implement")),
    "metaharness_run": _tool(
        "run the approved application version on new input", "the approved version's answer",
        "that the answer is right", ("implement",), "high", sensitive="metaharness.run", model="required",
        confined=True),
    "metaharness_inspect": _tool(
        "a workflow's versions, confirmations, evaluations and decisions", "the workflow's record",
        "nothing new is checked", ("diagnose",)),
    "metaharness_propose": _tool(
        "propose a candidate profile for a workflow", "candidate profiles from the declared search space",
        "that any is better", ("implement", "stuck")),
    "metaharness_evaluate": _tool(
        "measure a candidate now, without a job", "a paired, compute-matched measurement",
        "below 30 held-out cases is exploration only", ("verify",), "high", sensitive="metaharness.evaluate",
        model="required", confined=True),
    "metaharness_decide": _tool(
        "the gate's decision on a measured candidate", "recommend, refuse or block, with recovery",
        "approval: only the person approves", ("verify",)),
    "metaharness_diagnose": _tool(
        "why a workflow's active version fails and what to try next", "causes and remedies from dev failures",
        "held-out cases are never read", ("diagnose", "stuck")),
    "metaharness_approve": _tool(
        "the person approves a recommended version", "the person's approval", _PERSON, ("verify",), "medium"),
    "metaharness_rollback": _tool(
        "move a workflow back to a version the person approved", "the rollback after the person agrees", _PERSON,
        ("diagnose",), "medium"),
}

#: How an op's phases are read from its name: a word in the op's name puts it in each phase listed. An op none of
#: these words names is in no phase and is ranked by its words alone.
_OP_PHASES: dict[str, tuple[str, ...]] = {
    "check": ("verify",), "verify": ("verify",), "prove": ("verify",), "validate": ("verify",),
    "coverage": ("verify",), "evaluate": ("verify",), "adjudicate": ("verify",), "audit": ("verify",),
    "analyze": ("diagnose",), "analyse": ("diagnose",), "diagnose": ("diagnose",), "explain": ("diagnose",),
    "trace": ("diagnose",), "cycles": ("diagnose",), "impact": ("plan", "diagnose"), "plan": ("plan",),
    "preflight": ("plan",), "design": ("plan",), "map": ("plan",), "atlas": ("plan",), "estimate": ("plan",),
    "rank": ("stuck", "diagnose"), "search": ("stuck",), "summarize": ("implement",), "query": ("implement",),
}


def _placeholder(name: str, kind: str) -> Any:
    """A typed placeholder: a template whose values have the right types fits the schema it was read from."""
    return {"array": [], "object": {}, "integer": 0, "number": 0, "boolean": False}.get(kind, f"<{name}>")


def _json_kind(schema: dict[str, Any]) -> str:
    kinds = [schema.get("type")] if "type" in schema else [s.get("type") for s in schema.get("anyOf", ())]
    kinds = [k for k in kinds if k and k != "null"]
    return kinds[0] if kinds else "string"


def _annotation_kind(annotation: Any) -> str:
    origin = typing.get_origin(annotation) or annotation
    if origin in (typing.Union, types.UnionType):
        parts = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _annotation_kind(parts[0]) if parts else "string"
    if origin is typing.Annotated:
        return _annotation_kind(typing.get_args(annotation)[0])
    if origin is typing.Literal:
        return _annotation_kind(type(typing.get_args(annotation)[0]))
    return {list: "array", tuple: "array", set: "array", dict: "object", int: "integer", float: "number",
            bool: "boolean", str: "string"}.get(origin, "object")


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    return re.split(r"(?<=[.!?])\s", flat, maxsplit=1)[0] if flat else ""


def _for_agent(text: str) -> str:
    """G6's own declaration text for the corpus, or ``""`` when it names a way to lift a person's block."""
    from g6.core.agent_guidance import _AUTHORITY

    return "" if _AUTHORITY.search(text) else text


def _tools() -> list[dict[str, Any]]:
    from g6.core.consent_declarations import TOOLS
    from g6.mcp import registrations

    built = dict(registrations._built({"g6": registrations.SERVERS["g6"]}))["g6"]
    listed = dict(registrations._listed(built))
    if set(listed) != set(TOOL_DATA):
        raise ValueError(f"TOOL_DATA and the registered tools differ: registered and not described "
                         f"{sorted(set(listed) - set(TOOL_DATA))}, described and not registered "
                         f"{sorted(set(TOOL_DATA) - set(listed))}")
    out = []
    for name, tool in sorted(listed.items()):
        data = TOOL_DATA[name]
        schema = dict(getattr(tool, "parameters", None) or {})
        properties, required = schema.get("properties", {}), list(schema.get("required", []))
        arguments = {key: _placeholder(key, _json_kind(properties.get(key, {}))) for key in required}
        declared = TOOLS[name].classes if name in TOOLS else ()
        out.append({
            "id": f"tool:{name}", "kind": "tool",
            "call": {"tool": name, "arguments": arguments},
            "text": " ".join(filter(None, [data["when"] + ".", _for_agent(
                _first_sentence(getattr(tool, "description", "") or ""))])),
            "establishes": data["establishes"], "not_established": data["not_established"],
            "needs": {"sensitive": data["sensitive"], "consent": [c for c in declared if c != "g6_operational"],
                      "model": data["model"], "confined": data["confined"], "outside": data["outside"],
                      "extras": ["mcp"]},
            "cost": data["cost"], "phases": data["phases"], "hypotheses": data["hypotheses"],
            "schema": {"properties": sorted(properties), "required": sorted(required)},
        })
    return out


def _routes(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from g6.mcp.registrations import package_root
    from g6.skills import routing

    manifest = routing.load()
    root = (package_root() / routing.ROOT).read_text(encoding="utf-8")
    phases = {entry["call"]["tool"]: entry["phases"] for entry in tools}
    out = []
    for route_id, route in sorted(manifest.routes.items()):
        section = routing.section(root, route.anchor) or ""
        members = sorted(key.split("/", 1)[1] for key, entry in manifest.entries.items()
                         if entry.route == route_id and key.startswith("mcp:g6/"))
        out.append({
            "id": f"route:{route_id}", "kind": "route",
            "call": {"skill": "using-g6", "section": route.anchor},
            "text": " ".join(section.split()),
            "establishes": "the using-g6 skill's instruction for this point of the work",
            "not_established": "nothing ran; read the section, then call the tool it names",
            "needs": {"sensitive": "", "consent": [], "model": "none", "confined": False, "outside": [], "extras": []},
            "cost": "low", "phases": sorted({p for tool in members for p in phases.get(tool, ())},
                                            key=PHASES.index),
            "hypotheses": [], "tools": members,
        })
    return out


def _help_lines(registry: Any, name: str) -> list[str]:
    result = registry.invoke(name, "help")
    value = result.unwrap() if result.__class__.__name__ == "Success" else {}
    said = value.get("help") if isinstance(value, dict) else None
    if isinstance(said, dict):
        return [f"{key}: {text}" for key, text in said.items()]
    return [str(line) for line in said] if isinstance(said, list) else ([str(said)] if said else [])


def _confines(package: str) -> bool:
    """Whether the component's code can start a confined run: a module of it imports G6's boundary."""
    import importlib.util

    spec = importlib.util.find_spec(package)
    folder = Path(spec.origin).parent if spec and spec.origin else None
    if folder is None:
        return False
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else (
                [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            if any(n in ("g6.core.confine", "g6.core.component_process") for n in names) or (
                    isinstance(node, ast.ImportFrom) and node.module == "g6.core"
                    and any(a.name in ("confine", "component_process") for a in node.names)):
                return True
    return False


#: How many times a refused template gains the fields its refusal names before it is recorded as refused.
FILL_ROUNDS = 4
_PATHLIKE = re.compile(r"[A-Za-z]:[\\/]|\\\\|/(?:home|Users|tmp|var|private)/")


def _door_check(instance: Any, name: str, op: str, params: dict[str, Any],
                fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The template as the door's own typed input check (``registry.build_typed_input``, what ``g6_invoke`` runs
    before the op) takes it: a refused template gains a typed placeholder for each field its refusal names and is
    checked again, up to ``FILL_ROUNDS`` times. Returns the template and, when the door still refuses it, why, in
    the door's words (a refusal that holds anything like a path is put in G6's fixed words instead)."""
    from g6.core.registry import build_typed_input

    code = f"[{name.upper()}_ERROR]"
    error: str | None = None
    for _ in range(FILL_ROUNDS):
        _, error = build_typed_input(instance, {**params, "op": op}, code=code, op=op)
        if error is None:
            return params, []
        said = error.split(f"{op}: ", 1)[-1]
        named = sorted(f for f in fields if f not in params and f not in ("op", "operation")
                       and re.search(rf"\b{re.escape(f)}\b", said))
        if not named:
            break
        params = {**params, **{f: _placeholder(f, _annotation_kind(fields[f].annotation)) for f in named}}
    said = (error or "").split(f"{op}: ", 1)[-1]
    if _PATHLIKE.search(said):
        said = "a field holds a value the door does not take"
    return params, [f"[G6_E_INPUT_VALIDATION] {code} {op}: the door refuses this template as it stands ({said}); "
                    f"fill it in from g6_describe {name}"]


#: The components whose ops are run as given when the corpus is built, reviewed by hand: each computes over its
#: typed input alone, with no program, network, model, isolation boundary or optional extra, and reads no file or
#: source of the person's (what it keeps goes under G6's state folder, a scratch one during the build). Any other op
#: is never run here and stays ``validated: false``, and so does a sensitive op of these. A test holds each to its
#: component's static needs. The build records only whether a run was refused, its code and the fields to fill;
#: what decides that must not read the clock, the environment, a file of the person's or chance. Checked by hand
#: (review 3): duty_of_care is left out (its directory says how many days old its numbers are). In the others, a
#: clock read is a timestamp, an id or a monotonic deadline in a reply (action_gating, algorithm_design,
#: coding_agent_gate, graph_reasoning, knowledge_chunker) or a source-age finding reached only past the input check
#: (adapt_physical_ai, whose templates are refused at their input); every draw is from a seeded random.Random
#: (adapt_physical_ai, decision_making); files read are G6's own package data or its scratch state folder; the
#: environment reads (action_gating's store path, coding_agent_gate's intake setting) are off the path of the
#: templates run here. action_logic, complexity, design_governance and epistemic_integrity read none of these.
RUN_AT_BUILD = frozenset({
    "action_gating", "action_logic", "adapt_physical_ai", "algorithm_design", "coding_agent_gate", "complexity",
    "decision_making", "design_governance", "epistemic_integrity", "graph_reasoning", "knowledge_chunker"})
_G6_CODE = re.compile(r"G6_E_[A-Z0-9_]+")


def _fill(said: str, params: dict[str, Any], fields: dict[str, Any]) -> list[str]:
    """The fields the agent must supply: those a refusal names (as a word, or a plural field by its singular), else
    the template's own placeholders."""
    named = sorted(f for f in fields if f not in ("op", "operation") and (
        re.search(rf"\b{re.escape(f)}\b", said)
        or (len(f) > 3 and f.endswith("s") and re.search(rf"\b{re.escape(f[:-1])}\b", said))))
    return named or sorted(params)


def _unfilled(value: Any) -> bool:
    """Whether a template value is a typed placeholder or empty (``_placeholder``'s values, or "")."""
    return (value in ([], {}, "") or value is False or (type(value) in (int, float) and value == 0)
            or (isinstance(value, str) and re.fullmatch(r"<[^<>]*>", value) is not None))


def _declared(op: str, line: str, required: set[str], fields: dict[str, Any]) -> set[str]:
    """The fields the agent must supply for the op: those its contract requires, and every field of its typed input
    its own help line names in its signature (``preflight(task, proposed_action, evidence, ...): ...``), required or
    not, since an optional one can change the answer (the act a gate decides on, the evidence)."""
    said = re.match(rf"\s*{re.escape(op)}\s*\(([^)]*)\)", line)
    named = {m.group(1) for part in (said.group(1).split(",") if said else ())
             if (m := re.match(r"\s*\**([a-z_][a-z0-9_]*)", part))}
    return (set(required) | (named & set(fields))) - {"op", "operation"}


def _complete_fill(fill: list[str], params: dict[str, Any], declared: set[str], fields: dict[str, Any]) -> list[str]:
    """``fill`` with every field the template holds as a placeholder or empty, and every declared field it leaves
    out: all the agent must supply before relying on the call's answer (kept within the typed input's fields)."""
    return sorted((set(fill) | {k for k, v in params.items() if _unfilled(v)} | (declared - set(params)))
                  & set(fields))


def _not_run(module: Any, op: str, name: str, confined: bool) -> str:
    """Why an op is not run when the corpus is built, or "" when it is."""
    if op in set(getattr(module, "__g6_sensitive_ops__", ())):
        return "it acts outside G6, so only the person allows it"
    if getattr(module, "__g6_model__", None):
        return "it calls a model"
    if confined:
        return "it runs inside an isolation boundary"
    outside = sorted(str(getattr(p, "name", p)) for p in getattr(module, "__g6_outside__", ()))
    if outside:
        return f"it runs {', '.join(outside)}"
    extras = sorted(getattr(module, "__g6_extras__", {}))
    if extras:
        return f"it needs the optional extra {', '.join(extras)}, which a machine may lack"
    if name not in RUN_AT_BUILD:
        return "its component is not on the reviewed list of those that compute over their input alone"
    return ""


def _run_as_given(name: str, op: str, params: dict[str, Any],
                  fields: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """Run the template as given through ``g6_invoke``'s own path (``invoke_reply``), with no sensitive op allowed:
    ``(validated, issues, fill)``. Not refused (any state but ``blocked-escalated``) is validated; a refusal is put
    in G6's fixed words, its code and the fields to fill only (none of the op's own text)."""
    from g6.mcp import invoke_reply

    ran = invoke_reply(name, op, json.dumps(params, sort_keys=True), allow_sensitive=frozenset())
    if ran.get("completion_state") != "blocked-escalated":
        return True, [], []
    card = ran.get("warning_card") or {}
    said = " ".join(str(card.get(k, "")) for k in ("message", "why")) + " " + str(ran.get("error", ""))
    fill = _fill(said, params, fields)
    code = card.get("code", "") if _G6_CODE.fullmatch(str(card.get("code", ""))) else "G6_E_BLOCKED"
    what = f"fill {', '.join(fill)} with your own values" if fill else "fill the fields the op needs"
    return False, [f"[{code}] {name}.{op}: the op refuses this template as given; {what} "
                   f"(g6_describe {name} lists them)"], fill


@contextlib.contextmanager
def _scratch_home() -> Any:
    """``G6_HOME`` set to a scratch folder while templates run, so a run reads and keeps nothing of the person's."""
    import os
    import tempfile

    before = os.environ.get("G6_HOME")
    with tempfile.TemporaryDirectory(prefix="g6-corpus-", ignore_cleanup_errors=True) as home:
        os.environ["G6_HOME"] = home
        try:
            yield
        finally:
            if before is None:
                os.environ.pop("G6_HOME", None)
            else:
                os.environ["G6_HOME"] = before


def _components_and_ops() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from g6.core import discovery
    from g6.core.consent_declarations import TOOLS
    from g6.core.failure_modes import FailureModeRegistry
    from g6.core.registry import get_registry
    from g6.mcp.registrations import component_ops
    from g6.skills import routing

    registry, manifest = get_registry(), routing.load()
    consent = [c for c in TOOLS["g6_invoke"].classes if c != "g6_operational"]
    components: dict[str, Any] = {}
    ops: list[dict[str, Any]] = []
    for name, listed in sorted(component_ops().items()):
        ref = registry.refs[name]
        if ref.source != "builtin" or not listed:
            continue                                        # an extension, or no block: never offered
        module, block = registry.get_module(name), registry.get_class(name)
        instance = block()
        contract = instance.contract
        hints = typing.get_type_hints(block.infer)
        model = next((v for k, v in hints.items() if k != "return"), None)
        fields = getattr(model, "model_fields", {})
        mitigates = [str(c) for c in getattr(module, "__g6_mitigates__", ())]
        sensitive = set(getattr(module, "__g6_sensitive_ops__", ()))
        package = ref.module.split(":", 1)[0]
        outside = sorted(str(getattr(p, "name", p)) for p in getattr(module, "__g6_outside__", ()))
        extras = sorted(getattr(module, "__g6_extras__", {}))
        has_model = bool(getattr(module, "__g6_model__", None))
        confined = _confines(package)
        summary = _for_agent((module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else "")
        # what every op of the component shares is kept once, here; ``entries`` gives each op its full entry
        components[name] = {
            "ops": list(listed),
            "text": " ".join([name.replace("_", " "), summary, contract.purpose, discovery._when(name),
                              *(FailureModeRegistry.title(c) for c in mitigates if FailureModeRegistry.is_registered(c))]),
            "limits": sorted(str(c) for c in getattr(module, "__g6_limits__", ())),
            "guarantees": list(contract.guarantees), "bad_at": list(contract.bad_at), "mitigates": mitigates,
            "fields": sorted(fields),
            "needs": {"consent": consent, "model": "required" if has_model else "none", "confined": confined,
                      "outside": outside, "extras": extras},
        }
        lines = _help_lines(registry, name)
        for op in listed:
            if f"op:{name}/{op}" in manifest.entries and not manifest.entries[f"op:{name}/{op}"].agent_facing:
                continue                                    # exempt from agent use
            line = next((ln for ln in lines if re.match(rf"\s*{re.escape(op)}\b", ln)), "")
            required = set(contract.inputs_required_per_op.get(op, contract.inputs_required))
            required |= {k for k, f in fields.items() if f.is_required() and k not in ("op", "operation")}
            params = {k: _placeholder(k, _annotation_kind(fields[k].annotation) if k in fields else "string")
                      for k in sorted(required)}
            params, issues = _door_check(instance, name, op, params, fields)
            why = _not_run(module, op, name, confined)
            if issues or why:
                validated, fill = False, _fill(issues[0], params, fields) if issues else sorted(params)
                if why:
                    what = (f"fill {', '.join(fill)} with your own values first" if fill
                            else f"read g6_describe {name} for the fields it needs")
                    issues = [*issues, f"{name}.{op}: not run when the corpus was built ({why}), so whether it runs "
                                       f"as given is unknown; {what}"]
            else:
                validated, issues, fill = _run_as_given(name, op, params, fields)
            fill = _complete_fill(fill, params, _declared(op, line, required, fields), fields)
            words = re.findall(r"[a-z]+", op)
            phases = sorted({p for w in words for p in _OP_PHASES.get(w, ())}, key=PHASES.index)
            cost = "high" if has_model else "medium" if (op in sensitive or outside or confined) else "low"
            ops.append({
                "id": f"op:{name}.{op}", "kind": "op", "component": name,
                "call": {"tool": "g6_invoke", "arguments": {"component": name, "op": op,
                                                            "params_json": json.dumps(params, sort_keys=True)}},
                "text": (_for_agent(line) or f"{name}.{op}: {contract.purpose}") + (
                    " This op is sensitive. The person must allow it themselves; tell the person and stop."
                    if op in sensitive else ""),
                "sensitive": op in sensitive, "cost": cost, "phases": phases,
                "validated": validated, "issues": issues, "fill": fill,
            })
    return components, ops


def entries(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Every entry in full: an op's entry joined with what its component shares (what it establishes, from the
    contract's guarantees or its help line; what it does not, from ``bad_at``; its needs, the failure modes it
    addresses as hypothesis ids, and its typed input's fields). Deep copies: a caller that fills in a template it
    read here changes nothing a later call returns."""
    return copy.deepcopy(_expanded(load() if data is None else data))


def _expanded(data: dict[str, Any]) -> list[dict[str, Any]]:
    """``entries`` without the copy, for a caller that copies its own reply (``g6.core.selector.select``); it holds
    the cached corpus's own objects, so nothing may change what it returns."""
    out = []
    for entry in data["entries"]:
        if entry["kind"] != "op":
            out.append(entry)
            continue
        shared = data["components"][entry["component"]]
        component, op = entry["id"][3:].split(".", 1)
        out.append({**entry,
                    "establishes": ("This op is sensitive. The person must allow it themselves; tell the person."
                                    if entry["sensitive"] else "; ".join(shared["guarantees"]) or entry["text"]),
                    "not_established": "; ".join(shared["bad_at"]) or "not stated by the component: read g6_describe",
                    "needs": {"sensitive": f"{component}.{op}" if entry["sensitive"] else "", **shared["needs"]},
                    "hypotheses": shared["mitigates"], "fields": shared["fields"]})
    return out


def build() -> dict[str, Any]:
    """The corpus as the code stands. Imports each built-in component and asks its block for ``ops`` and ``help``,
    and runs each ``RUN_AT_BUILD`` op's template as given in a scratch G6 home; runs no other op, no model and no
    program."""
    from g6.core import refusals
    from g6.skills import routing

    tools = _tools()
    with _scratch_home():
        components, ops = _components_and_ops()
    entries = sorted([*tools, *_routes(tools), *ops], key=lambda e: e["id"])
    for entry in entries:
        entry["text"] = " ".join(entry["text"].split())
    return {"format": FORMAT, "release": routing.load().release, "phases": list(PHASES), "costs": list(COSTS),
            "components": components, "refusals": refusals.as_data(refusals.component_declarations()),
            "recovery": _recovery(), "library": _library(set(components)), "entries": entries}


def _library(runnable: set[str]) -> dict[str, Any]:
    """Free functions have no door contract, so none is offered to the agent."""
    return {}


def _recovery() -> list[dict[str, str]]:
    """``g6.metaharness.recovery``'s remedies for wrong answers, once each by (component, action), as data, so the
    selector names them without importing the meta-harness at request time."""
    from g6.metaharness.recovery import WRONG_ANSWER_REMEDIES

    out, seen = [], set()
    for kind, remedies in sorted(WRONG_ANSWER_REMEDIES.items()):
        for remedy in remedies:
            if (remedy.component, remedy.action) not in seen:
                seen.add((remedy.component, remedy.action))
                out.append({"answer_kind": kind, "component": remedy.component, "action": remedy.action,
                            "how": remedy.how, "who": remedy.who})
    return out


def dumps(data: dict[str, Any]) -> str:
    """The corpus as its file is written: one entry a line, sorted, so a change shows as the lines it changes."""
    head = {k: v for k, v in data.items() if k not in ("components", "refusals", "library", "recovery", "entries")}
    lines = ["{"]
    lines += [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    for section in ("components", "refusals", "library"):
        lines.append(f'  "{section}": {{')
        lines.append(",\n".join(f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}"
                                for k, v in sorted(data[section].items())))
        lines.append("  },")
    lines.append('  "recovery": [')
    lines.append(",\n".join(f"    {json.dumps(r, ensure_ascii=False, sort_keys=True)}" for r in data["recovery"]))
    lines.append("  ],")
    lines.append('  "entries": [')
    lines.append(",\n".join(f"    {json.dumps(e, ensure_ascii=False, sort_keys=True)}" for e in data["entries"]))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


@lru_cache(maxsize=1)
def _loaded(text: str) -> dict[str, Any]:
    return json.loads(text)


def load() -> dict[str, Any]:
    """The checked-in corpus (read once per change of the file's text). Callers must not change what it returns."""
    return _loaded(PATH.read_text(encoding="utf-8"))


def selectable(registrations: dict[str, Any] | None = None) -> dict[str, str]:
    """Member key (``mcp:<server>/<tool>``, ``op:<component>/<op>``) -> ``"corpus"`` for each member the corpus offers
    with text, or the reason it is not offered. ``g6.skills.routing.check`` reads it: an agent-facing member that is
    neither is a problem."""
    out: dict[str, str] = {}
    for entry in load()["entries"]:
        if not entry["text"].strip():
            continue
        if entry["kind"] == "tool":
            out[f"mcp:g6/{entry['call']['tool']}"] = "corpus"
        elif entry["kind"] == "op":
            component, op = entry["id"][3:].split(".", 1)
            out[f"op:{component}/{op}"] = "corpus"
    for server, reason in SERVERS_NOT_SELECTED.items():
        for tool in (registrations or {}).get(server, ()):
            out[f"mcp:{server}/{tool}"] = reason
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = dumps(build())
    if argv == ["--write"]:
        PATH.write_text(text, encoding="utf-8", newline="\n")
        return 0
    if argv == ["--check"]:
        current = PATH.read_text(encoding="utf-8") if PATH.exists() else ""
        if current != text:
            print("corpus.json is stale: run python -P -m g6.skills.corpus --write", file=sys.stderr)
            return 1
        return 0
    print("usage: python -P -m g6.skills.corpus --write | --check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
