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
  here, when the corpus is built from shipped code (``_door_check``): ``validated`` says whether the door takes the
  call's shape, and ``issues`` why not. ``g6_select`` reads that and imports no component, so its reply never
  depends on what the host can import.

The separate abstraction server is registered by the person alone, apart from ``g6-mcp`` (``SERVERS_NOT_SELECTED``).
Nothing here runs an op: ``help`` and ``ops`` are each block's own description of itself.

    python -P -m g6.skills.corpus --write     # after a tool, route or op changes
    python -P -m g6.skills.corpus --check     # exit 1 when the file is stale
"""
from __future__ import annotations

import ast
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
        "what to do next, which G6 step fits now, after a refusal or when stuck",
        "a short ranked list of checked next calls with their templates, and a reroute after a refusal",
        "nothing ran; the ranking is fixed rules over G6's own text, a proposal for you to choose from",
        PHASES),
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
        "before the work: what done would mean, how the task can fail, open questions, ambiguous requirements",
        "an intent contract, a proposed standard of done and competing failure hypotheses, with no model call",
        "a proposal: nothing is frozen, confirmed or checked", ("plan", "stuck", "diagnose")),
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
            "text": data["when"] + ". " + _first_sentence(getattr(tool, "description", "") or ""),
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
        summary = (module.__doc__ or "").strip().splitlines()[0] if module.__doc__ else ""
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
            words = re.findall(r"[a-z]+", op)
            phases = sorted({p for w in words for p in _OP_PHASES.get(w, ())}, key=PHASES.index)
            cost = "high" if has_model else "medium" if (op in sensitive or outside or confined) else "low"
            ops.append({
                "id": f"op:{name}.{op}", "kind": "op", "component": name,
                "call": {"tool": "g6_invoke", "arguments": {"component": name, "op": op,
                                                            "params_json": json.dumps(params, sort_keys=True)}},
                "text": line or op.replace("_", " "), "sensitive": op in sensitive, "cost": cost, "phases": phases,
                "validated": not issues, "issues": issues,
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
                    "establishes": "; ".join(shared["guarantees"]) or entry["text"],
                    "not_established": "; ".join(shared["bad_at"]) or "not stated by the component: read g6_describe",
                    "needs": {"sensitive": f"{component}.{op}" if entry["sensitive"] else "", **shared["needs"]},
                    "hypotheses": shared["mitigates"], "fields": shared["fields"]})
    return out


def build() -> dict[str, Any]:
    """The corpus as the code stands. Imports each built-in component and asks its block for ``ops`` and ``help``;
    runs no other op, no model and no program."""
    from g6.skills import routing

    tools = _tools()
    components, ops = _components_and_ops()
    entries = sorted([*tools, *_routes(tools), *ops], key=lambda e: e["id"])
    for entry in entries:
        entry["text"] = " ".join(entry["text"].split())
    return {"format": FORMAT, "release": routing.load().release, "phases": list(PHASES), "costs": list(COSTS),
            "components": components, "entries": entries}


def dumps(data: dict[str, Any]) -> str:
    """The corpus as its file is written: one entry a line, sorted, so a change shows as the lines it changes."""
    head = {k: v for k, v in data.items() if k not in ("components", "entries")}
    lines = ["{"]
    lines += [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    lines.append('  "components": {')
    lines.append(",\n".join(f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}"
                            for k, v in sorted(data["components"].items())))
    lines.append("  },")
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
