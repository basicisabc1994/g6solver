# SPDX-License-Identifier: Apache-2.0
"""Progressive discovery: find a workflow, read its call, describe an op, then invoke it.

Default discovery uses the selector's shipped corpus and shared ranker, with a deterministic coverage floor and
optional closed-candidate ordering through the shared semantic reader. Explicit names= searches retain the
component-only source profiles, including blockless libraries. An empty list means no G6 tool fits. Discovery
proposes work; it establishes no check result or reliability state.
"""
from __future__ import annotations

import ast
import importlib.util
import math
import re
from collections import Counter
from typing import Any

from g6.core import vetting
from g6.core.door import THREAT_STATEMENT
from g6.core.failure_modes import FailureModeRegistry, declared
from g6.core.registry import get_registry
from g6.core.skills import front_matter, skill_text

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an and are as at be by can could do does for from how i in is it me my of on or our should that "
                  "the this to we what will with would you your".split())
#: Where a ported component's docstring starts listing what it does NOT do. That list is not what the component offers,
#: so it is not ranked: the solver's "customer scoping (not ported)" once outranked action_gating for a customer email.
_NOT_OFFERED = "Not ported from the archive"


def _tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower().replace("_", " ")) if t not in _STOP]


def ops(name: str) -> list[str]:
    """The ops a component's block lists for itself (via its ``ops`` op), or ``[]``."""
    result = get_registry().invoke(name, "ops")
    if result.__class__.__name__ != "Success":
        return []
    value = result.unwrap()
    listed = value.get("ops") if isinstance(value, dict) else None
    return [str(o) for o in listed] if isinstance(listed, list) else []


def _package(name: str) -> str:
    return get_registry().refs[name].module.split(":", 1)[0]


def _docstring(name: str) -> str:
    """A component's package docstring, read from its source without importing it; ``""`` if it cannot be read."""
    try:
        spec = importlib.util.find_spec(_package(name))
        if spec is None or not (spec.origin or "").endswith(".py"):
            return ""
        with open(spec.origin, encoding="utf-8") as f:
            return ast.get_docstring(ast.parse(f.read())) or ""
    except (ImportError, OSError, SyntaxError, ValueError):
        return ""


def runnable(name: str) -> bool:
    """Whether a component names a block ``g6_invoke`` can run (``__g6_block__`` in its package, read from its source
    without importing it, and a class of that name defined or imported there)."""
    try:
        spec = importlib.util.find_spec(_package(name))
        if spec is None or not (spec.origin or "").endswith(".py"):
            return False
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (ImportError, OSError, SyntaxError, ValueError):
        return False
    block, bound = None, set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == "__g6_block__" for t in targets) \
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            block = node.value.value
        bound |= {t.id for t in targets if isinstance(t, ast.Name)}
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            bound |= {alias.asname or alias.name.split(".")[0] for alias in node.names}
        if isinstance(node, ast.ClassDef):
            bound.add(node.name)
    return bool(block) and block in bound


def _guards(name: str) -> list[str]:
    """The titles of the failure modes a component declares it guards against, read without importing it."""
    codes = declared(_package(name)).get("__g6_mitigates__", ())
    return [FailureModeRegistry.title(c) for c in codes if FailureModeRegistry.is_registered(c)]


def _when(name: str) -> str:
    """The ``description`` of a component's skill (when to use it), read without importing the component."""
    return front_matter(skill_text(name) or "").get("description", "")


def _profile(name: str) -> str:
    offered = _docstring(name).split(_NOT_OFFERED, 1)[0]
    return " ".join([name, name, offered, _when(name), *_guards(name)])


def _modes(codes: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"code": c, "title": FailureModeRegistry.title(c) if FailureModeRegistry.is_registered(c) else ""}
            for c in codes]


def _installed(name: str) -> bool:
    """Whether the top-level package of the import name ``name`` is installed, without importing it."""
    try:
        return importlib.util.find_spec(name.partition(".")[0]) is not None
    except (ImportError, ValueError):
        return False


def _extras(extras: dict[str, tuple[str, ...]]) -> list[dict[str, Any]]:
    return [{"extra": extra, "provides": list(names), "installed": all(_installed(n) for n in names)}
            for extra, names in extras.items()]


def find(query: str, limit: int = 5, *, names: list[str] | None = None) -> list[dict[str, Any]]:
    """Rank the selector's workflows; ``[]`` means no G6 tool fits the task above the lexical coverage floor.

    A workflow carries its call template and the ranker that ran. ``names=`` explicitly opts into the historical
    component-only overlap search, including blockless Python libraries; omitted components never weigh in its
    scores. Discovery proposes work and establishes no reliability state.
    """
    if names is None:
        return _find_workflows(query, limit)
    q = Counter(_tokens(query))
    if not q:
        return []
    docs = {n: Counter(_tokens(_profile(n))) for n in names}
    df = Counter(t for d in docs.values() for t in set(d))
    n_docs = max(1, len(docs))
    scored = []
    for name, d in docs.items():
        score = sum(
            min(q[t], d[t]) * math.log(1 + n_docs / df[t]) for t in q if d.get(t)
        )
        if score > 0:
            matched = sorted(t for t in q if d.get(t))
            scored.append({"component": name, "score": round(score, 3), "matched": matched})
    scored.sort(key=lambda r: (-r["score"], r["component"]))
    for row in scored[:limit]:
        if "via" not in row:
            row["runnable"] = runnable(row["component"])
            row["via"] = "g6_invoke" if row["runnable"] else "python"
    return scored[:limit]


def _find_workflows(query: str, limit: int) -> list[dict[str, Any]]:
    from copy import deepcopy

    from g6.core import semantic
    from g6.core.corpus_ranking import FIND_FLOOR, documents, rank
    from g6.skills import corpus

    if not isinstance(query, str) or not query.strip() or type(limit) is not int or limit <= 0:
        return []
    data = corpus.load()
    entries = {e["id"]: e for e in corpus._expanded(data)}
    with semantic.use_request():
        ranking = rank(query, data, model=True, floor=FIND_FLOOR)
    docs = documents(data, entries)
    hits = []
    for key in ranking.order[:limit]:
        entry = entries[key]
        call = deepcopy(entry["call"])
        tool = call.get("tool", "")
        component = entry.get("component", "")
        if not component and tool == "metaharness_start":
            component = "metaharness"
        row = {"id": key, "kind": entry["kind"], "component": component,
               "score": round(ranking.scores[key], 3), "matched": sorted(set(_tokens(query)) & set(_tokens(docs[key]))),
               "runnable": entry["kind"] in ("tool", "op"), "via": tool or "skill",
               "call": call, "ranked_by": ranking.ranked_by}
        row["tool"] = tool or None
        if ranking.rerank_code:
            row["rerank_code"] = ranking.rerank_code
        hits.append(row)
    return hits


def describe(name: str) -> dict[str, Any]:
    """What a component is, how mature it is, the ops it exposes, the failure modes it guards against and can cause,
    what it needs from outside G6 (``outside``: programs you install, from ``__g6_outside__``; ``extras``: each extra,
    the imports it provides and whether they are installed, from ``__g6_extras__``; ``keys``: each key it needs and the
    key file it goes in, from ``__g6_keys__``; nothing is run to find out), its skill (``None`` when it has none), and,
    in a vetted area, whether it is still awaiting review (``vetting``, from ``__g6_vetting__``; ``None`` outside
    one)."""
    info = get_registry().describe(name)
    if info.get("available"):
        info["ops"] = ops(name)
        mod = get_registry().get_module(name)
        info["requires"] = list(getattr(mod, "__g6_requires__", ()))
        info["mitigates"] = _modes(getattr(mod, "__g6_mitigates__", ()))
        info["introduces"] = _modes(getattr(mod, "__g6_introduces__", ()))
        info["outside"] = [program.describe() for program in getattr(mod, "__g6_outside__", ())]
        info["extras"] = _extras(getattr(mod, "__g6_extras__", {}))
        info["keys"] = [key.describe() for key in getattr(mod, "__g6_keys__", ())]
        from g6.core.agent_guidance import skill_for_agent

        info["skill"] = skill_for_agent(skill_text(name))
        info["summary"] = skill_for_agent(info["summary"])
        info["vetting"] = vetting.summary(_package(name))
        info["certificate"] = vetting.certificate(_package(name))
    return info


HELP = (
    "What to do next: call g6_select with your task, the phase you are in (plan, implement, verify, diagnose or stuck) "
    "and any refusal you got; it returns a few checked calls with their templates, and runs nothing. "
    "Build agents from parts you can check. Use a component alone, connect declared contracts with g6_compose, "
    "or fit an application to a task with Propose -> Test -> Gate -> Approve. For AI building AI: "
    "metaharness_start creates a data-only Python application workflow; metaharness_confirm asks for client consent; "
    "diagnose, propose_code with a hypothesis, evaluate_start, poll metaharness_job, decide, approve, then run. "
    "All meta-harness tool names begin metaharness_. Below 30 held-out cases is exploration only. "
    "Generated-code activation also needs independent host-configured safety checks. g6_review_patch assesses a "
    "Python patch against stated requirements in confined test runs, with host permission patch_review.review: "
    "test_integrity_compromised, defect_detected, insufficient_evidence or supported_within_scope (support on the "
    "stated checks only), and merging always needs separate approval. "
    "g6_review_until_clean runs one step of G6's completion loop, with the same permission and G6_LOOP_CRITIC=codex in the "
    "person's key file: another model family reads the patch and a failing test of its own goes back to you, until a "
    "review reproduces nothing or the stop rule fires. "
    "g6_premortem proposes what done would mean and how a task can fail, with no model call. A coding mission holds a "
    "change to a standard fixed first: g6_mission_create freezes it, g6_mission_confirm asks the person in the client's "
    "own dialog, and g6_mission_submit checks the working tree against it, with host permission workforce.mission. "
    "Only the person confirms: no argument does, and there is no terminal fallback. "
    "G6 discovery, in order: find(query) ranks tools, skill instructions and runnable ops from the selector's corpus; "
    "an empty list means no G6 tool fits; describe(component) gives maturity, ops, dependencies, the failure modes it guards "
    "against and can cause, the programs and extras it needs from outside G6, and the component's skill; "
    "invoke(component, op, params_json), with params_json a JSON object of the op's parameters, runs one op through "
    "the component's typed input; the reply carries ok, result or error, completion_state and a warning_card, and ok "
    "is false whenever the state is blocked-escalated (a blocked result keeps its answer in result). Results carry "
    "the component's reliability label; discovery never upgrades it, and a result that reports no label is a "
    "qualified-draft. Over MCP, g6 invoke and g6.invoke in Python, a component's own "
    "verified stands only when G6's own code re-runs a check at the door, against what you gave it, and reproduces "
    "it; any other is held at qualified-draft (G6_E_PROMOTION_LAW, claimed_completion_state), and a re-check that "
    "finds a mismatch blocks. No component you can reach through a door has such a re-check yet, so nothing that "
    "comes through them is verified today; get_registry().invoke in Python is the raw library call beneath the "
    "doors, the component's own answer unchecked. " + THREAT_STATEMENT + " An op that needs a model "
    "uses the one your user set in their key file (~/.g6/.env, for example G6_LLM_PROVIDER=claude_code); with none it "
    "is refused with a *_NO_MODEL code: then do that step yourself and call the op that checks it. A sensitive op (it "
    "runs a program, reaches the network or changes the machine) needs the person's own permission. "
    "Tell the person; only they can allow it themselves. "
    "To learn which G6 step "
    "fits each point of your work, load the using-g6 skill first; `g6 skills install` copies it into your "
    "agent's skills folder."
)
