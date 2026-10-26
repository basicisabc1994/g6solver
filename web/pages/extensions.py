# SPDX-License-Identifier: Apache-2.0
"""G6's extensions as the site lists them: each one in the library from its own package, and, while ``SHOW_PLANNED``
is on, the planned ones from ``data/component_catalogue.json``.

The catalogue was made once from the extension tracker (``data/from_tracker.py``): every row has its family and the
package its code lands in, and none says whether it is in the library. An entry is "In the library" only when
``g6.core.registry`` finds a component of that name in that package; it is then described from the package itself (the
first line of its docstring, the titles of the failure modes in ``__g6_mitigates__``, and what ``__g6_outside__`` and
``__g6_extras__`` say it needs), never from the catalogue. A row in a vetted area (its ``vetting`` kinds) is built but
not in the library until its package's certificate is current (``g6.core.vetting``); it is then shown as awaiting its
certificate. No qualified person's review is awaited (owner, 2026-10-23, DECISIONS #39), and every vetted entry says
so. A row that joins another package is merged once that package is in the library (``_merged``), and is shown as part
of it. Every other entry is planned, in the catalogue's words.

``SHOW_PLANNED`` is the site's one switch for planned rows (owner, 2026-10-23: "comment out any planned extensions
leaving only the live completed extensions on the website"). Off, no page lists a planned row or speaks of
extensions to come: the templates wrap each planned section in ``{% if show_planned %}``, the Markdown pages wrap
theirs in ``<!-- planned -->`` and ``<!-- /planned -->`` (``views.render_markdown``), and
``tools/gen_failure_map.py`` reads the switch for the generated failure-mode page. Nothing is deleted: the catalogue
keeps every row, and turning the switch on lists them again.

A row whose package is named after it is an extension of its own, listed under its family. A row that joins another
package is listed under what it joins: another extension, its family's shared code, or a package of the core harness.
A dropped row is not in the catalogue, and a hidden one is never listed. The core harness itself is listed from the
registry (``pages.catalogue``).
"""
from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path

#: Whether the site lists planned rows (and says that more are to come). Off since 2026-10-23; see the docstring.
SHOW_PLANNED = False

CATALOGUE = Path(__file__).resolve().parents[1] / "data" / "component_catalogue.json"
# The 15 extension families (DECISIONS #37; ``g6.core.registry.FAMILIES``), in the page's order.
FAMILIES = {
    "media": "Media", "ml": "Machine learning", "agents": "Agent runtimes", "knowledge": "Knowledge and retrieval",
    "memory": "Memory", "sources": "Sources", "deploy": "Deployment", "workspace": "Workspaces", "code": "Code",
    "embodied": "Embodied and simulation", "cogarch": "Cognitive architectures", "provers": "Provers",
    "domains": "Domain standards", "jobs": "Professional workflows", "integrations": "Integrations",
}
#: The family of a row planned into a package of the core harness.
CORE = "core"


# The capability extension harness (owner, 2026-10-20): what each kind of extension adds to a language model, and what
# that lets an agent achieve. ``library`` names packages in the open-source library now; ``extensions`` names listed
# catalogue rows. tests/site/test_components_page.py checks both, so a name cannot drift.
CAPABILITIES = (
    {"key": "modalities", "title": "Senses and media",
     "gap": "A language model reads and writes text, and nothing else.",
     "achieve": "Read a screenshot, a scanned form or a recording, and make the images, audio, video and 3D scenes "
                "a job needs, from the agent you already use.",
     "library": (), "extensions": ("ctx_vision", "knowledge_converter", "adapt_voice", "adapt_image", "adapt_comfyui",
                                   "adapt_ffmpeg", "adapt_blender", "multimodal")},
    {"key": "memory", "title": "Memory and persistent work",
     "gap": "It forgets whatever falls out of its context, and stops when its reply ends.",
     "achieve": "Run a job against a fixed standard of done, or an operation in budgeted steps over days, and keep "
                "what it learns in a memory it can search.",
     "library": ("workforce", "operator", "context"), "extensions": ("ctx_ace", "ctx_cognee", "agent_langgraph",
                                                                    "adapt_rq", "agent_nanoclaw")},
    {"key": "knowledge", "title": "Current, cited knowledge",
     "gap": "It knows only what it was trained on, and cannot say where a fact came from.",
     "achieve": "Answer from today's sources, with each claim checked against the passage it cites.",
     "library": ("grounding", "epistemic_integrity"), "extensions": ("ctx_search", "ctx_scrapling",
                                                                     "ctx_research_scanner", "knowledge_indexer",
                                                                     "knowledge_manifest", "document_stc")},
    {"key": "action", "title": "Action in the real world",
     "gap": "It can describe an action, but cannot take one or undo it.",
     "achieve": "Query databases, commit code, hand work to other agents and deploy services. Outward git actions "
                "(fetch, pull, push, clone) are checked by G6's action gate first and the risky ones sent to a "
                "person; local ones (commit, merge, branch, tag, stash, add, reset) are sensitive ops you allow.",
     "library": ("action_gating", "coding_agent_gate"), "extensions": ("database", "ctx_git", "agent_claude",
                                                                       "agent_codex", "deploy_docker", "deploy_k8s")},
    {"key": "exact", "title": "Exact computation and proof",
     "gap": "It guesses at arithmetic and logic by predicting likely text.",
     "achieve": "Analyse data, find the best plan under constraints and settle logic with solvers and provers, "
                "where a plausible answer is not enough.",
     "library": ("formal", "cegis", "csf"), "extensions": ("adapt_pandas", "adapt_sklearn", "adapt_bayesian",
                                                           "adapt_optimisation", "lean_prover")},
    {"key": "embodied", "title": "Bodies and simulation",
     "gap": "It has no body, no senses of its own and no physics.",
     "achieve": "Plan and test physical actions in simulation, with sensors and touch, and coordinate people, agents "
                "and robots on one task.",
     "library": (), "extensions": ("embodiment", "physics_prediction", "sensory_fusion", "tactile_fusion",
                                   "hat_orchestrator")},
    {"key": "growth", "title": "New capabilities on demand",
     "gap": "It cannot give itself a tool it does not have.",
     "achieve": "Match a request to the components that exist, or turn a Python library into a new component with "
                "tests; the meta-harness gate decides whether a change stays.",
     "library": ("metaharness",), "extensions": ("auto_component_integrator", "component_creator", "llm_extension")},
)


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def _own(row: dict) -> bool:
    """A row whose package is named after it (``g6.<family>.<name>``, or ``g6.llm_router``): an extension of its own."""
    return row["family"] in FAMILIES and row["package"].rsplit(".", 1)[-1] == row["name"]


def _from_package(name: str) -> dict:
    """What a package in the library says about itself: its summary, the failure modes it guards against, what it needs
    from outside G6, and whether it has an MCP block."""
    from g6.core.failure_modes import FailureModeRegistry, declared
    from g6.core.registry import get_registry

    reg = get_registry()
    package, mod = reg.refs[name].module, reg.get_module(name)
    lines = (mod.__doc__ or "").strip().splitlines()
    summary = lines[0].strip() if lines else ""
    if summary.startswith(package):                    # "g6.llm_router — pick a language model ..."
        summary = summary.split("—", 1)[-1].strip()
    codes = declared(package).get("__g6_mitigates__", ())
    guards = [FailureModeRegistry.title(c) for c in codes if FailureModeRegistry.is_registered(c)]
    needs = [p.name for p in getattr(mod, "__g6_outside__", ())]
    needs += [f"the optional {extra} extra" for extra in getattr(mod, "__g6_extras__", {})]
    return {"summary": summary[:1].upper() + summary[1:], "failure_mode": "; ".join(guards), "needs": needs,
            "mcp": reg.get_class(name) is not None}


def _awaiting(package: str, kinds: list[str]) -> str:
    """What a built package in a vetted area waits for, in the page's words; ``""`` once its certificate is current.
    No kind waits for a qualified person's review (DECISIONS #39)."""
    from g6.core import vetting

    return "its certificate" if vetting.awaiting_review(package, required=kinds) else ""


def _merged(row: dict, library: set[str]) -> bool:
    """Whether a row that joins another package is part of the library already: the extension it joins is in the
    library (``library``), or the core package it joins can be imported. A family's shared code never makes a row
    merged (it exists as soon as any member does), and a row in a vetted area waits for its package's certificate like
    any other (``g6.core.vetting``)."""
    from g6.core import vetting

    package = row["package"]
    if row["family"] in FAMILIES:
        if package not in library:                     # its family's shared code, or an extension not in the library
            return False
    else:
        try:
            if importlib.util.find_spec(package) is None:
                return False
        except (ImportError, ValueError):
            return False
    return not (row["vetting"] and vetting.awaiting_review(package, required=list(row["vetting"])))


def vetting_required(name: str) -> list[str]:
    """The areas a catalogue row works in whose review it must wait for (the tracker's ``vetting_required``)."""
    return next((list(r["vetting"]) for r in _data()["components"] if r["name"] == name), [])


def _status(row: dict) -> dict:
    """The row as the page shows it: ``in_library`` when the registry finds it in its package, described from the
    package; ``awaiting_review`` instead when it is built in a vetted area whose certificate is not current
    (``g6.core.vetting``); ``vetted`` when its package declares a vetted area; otherwise planned, in the catalogue's
    words (``merged`` is decided once every extension's status is known: ``_all``)."""
    from g6.core import vetting
    from g6.core.registry import get_registry

    ref = get_registry().refs.get(row["name"])
    shown = dict(row, needs=list(row["needs"]), in_library=False, awaiting_review="", mcp=False, vetted=False,
                 merged=False)
    if _own(row) and ref is not None and ref.module == row["package"]:
        waiting = _awaiting(row["package"], list(row["vetting"]))
        shown.update(_from_package(row["name"]), in_library=not waiting, awaiting_review=waiting,
                     vetted=vetting.declared(row["package"]) is not None)
    return shown


def _all() -> list[dict]:
    """Every listed row, planned ones included, each a fresh copy with its status and ``planned``: neither in the
    library, nor built and awaiting its certificate, nor merged into a package in the library."""
    out = [_status(r) for r in _data()["components"] if not r["hidden"]]
    library = {r["package"] for r in out if r["in_library"] and _own(r)}
    for r in out:
        r["merged"] = not _own(r) and _merged(r, library)
        r["planned"] = not (r["in_library"] or r["awaiting_review"] or r["merged"])
    return out


def _show(planned: bool | None) -> bool:
    return SHOW_PLANNED if planned is None else planned


def rows(planned: bool | None = None) -> list[dict]:
    """Every row the page lists, by name: the extensions, and the rows that join one of them, a family's shared code or
    the core harness. A planned row is among them only while ``SHOW_PLANNED`` is on (or ``planned=True``)."""
    show = _show(planned)
    return [r for r in _all() if show or not r["planned"]]


def _order(row: dict) -> tuple:
    keys = list(FAMILIES)
    return (keys.index(row["family"]) if row["family"] in keys else len(keys), row["family"], row["name"])


def extensions(planned: bool | None = None) -> list[dict]:
    """Every extension of its own the page lists, by family then name, each with ``merged_in``: the rows merged into
    it, and ``joined``: the rows planned to join it."""
    listed = rows(planned)
    own = sorted((r for r in listed if _own(r)), key=_order)
    by_package = {r["package"]: r for r in own}
    for r in own:
        r["merged_in"], r["joined"] = [], []
    for r in listed:
        if not _own(r) and r["package"] in by_package:
            by_package[r["package"]]["merged_in" if r["merged"] else "joined"].append(r)
    return own


def families(planned: bool | None = None) -> list[dict]:
    """The extensions grouped by family: ``{"key", "label", "package", "items", "shared"}`` in the page's order, where
    ``shared`` holds the rows planned into the family package's own shared code."""
    groups: dict[str, dict] = {}
    for row in extensions(planned):
        key = row["family"]
        groups.setdefault(key, {"key": key, "label": FAMILIES.get(key, key.replace("_", " ").capitalize()),
                                "package": f"g6.{key}", "items": [], "shared": []})["items"].append(row)
    for row in rows(planned):
        if row["family"] in groups and row["package"] == groups[row["family"]]["package"]:
            groups[row["family"]]["shared"].append(row)
    return list(groups.values())


def into_core(planned: bool | None = None) -> list[dict]:
    """The rows merged into, or planned for, the core harness, by the core package they join: ``{"package", "items"}``,
    by package."""
    groups: dict[str, list[dict]] = {}
    for row in rows(planned):
        if row["family"] == CORE:
            groups.setdefault("g6." + row["package"].split(".")[1], []).append(row)
    return [{"package": package, "name": package.split(".", 1)[1], "items": items}
            for package, items in sorted(groups.items())]


def named(*names: str) -> list[dict]:
    """These catalogue rows, in this order, planned ones included; a name the catalogue does not list (dropped, hidden
    or unknown) raises, so an example cannot drift."""
    listed = {r["name"]: r for r in rows(planned=True)}
    missing = [n for n in names if n not in listed]
    if missing:
        raise LookupError(f"not listed on the components page: {missing}")
    return [listed[n] for n in names]


def capabilities(planned: bool | None = None) -> tuple[list[dict], dict]:
    """The capability groups with the rows the page lists (each marked ``own`` or not: a row that joins another package
    is shown as joining it, or as merged into it), and how many of the extensions of their own they name are in the
    library."""
    show = _show(planned)
    groups = [dict(c, rows=[dict(r, own=_own(r)) for r in named(*c["extensions"]) if show or not r["planned"]])
              for c in CAPABILITIES]
    shown = {row["name"]: row for g in groups for row in g["rows"] if row["own"]}
    return groups, {"total": len(shown), "ready": sum(1 for row in shown.values() if row["in_library"])}


def counts(planned: bool | None = None) -> dict:
    """How many extensions the page lists and how many are in the library (``awaiting``: built, waiting for their
    certificate); how many rows join something else (``joining``), how many of those are merged into a package in the
    library (``merged``), and how many join the core harness (``into_core``)."""
    own, listed = extensions(planned), rows(planned)
    return {"extensions": len(own), "in_library": sum(1 for r in own if r["in_library"]),
            "awaiting": sum(1 for r in own if r["awaiting_review"]), "joining": len(listed) - len(own),
            "merged": sum(1 for r in listed if r["merged"]), "into_core": sum(1 for r in listed if r["family"] == CORE)}
