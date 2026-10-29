# SPDX-License-Identifier: Apache-2.0
"""MCP server: the reflective abstraction harness, production surface.

Tools for the outer-loop agent:

  load_dataset / describe_domain / list_combinators
  propose_abstraction     [[spec]] with validated denotation + soundness gate
  evaluate_abstraction    gate + replay + bootstrap permutations + MDL ->
                          robust (BLP) score, status in
                          {checked, provisional, degenerate, rejected}
  find_counterexamples / refine_abstraction (CEGAR)
  leaderboard             ranked, status-first
  advise_pruning          GUARDED: status, staleness, and support-distance
                          guards can force prune=False with stated reasons
  record_evaluation       active-mode trace append (axiom A0)
  revalidate              re-evaluate an entry's spec on the current trace
  lift_abstraction        Lift functor to the meta-domain
  diagnose                self-debugging audit: the harness examines its own
                          verification decisions and returns findings with
                          machine-actionable remediations
  save_state / load_state persistence (also autosaves when
                          ABSTRACTION_MCP_STATE is set)

The file tools (load_dataset, save_state, load_state) read or write a file on
this machine, so they are refused unless the person who runs the server lists
them (``abstraction.load_dataset``, ...) or ``abstraction`` in
``G6_MCP_SENSITIVE``, as g6-mcp's sensitive ops are, and every path they take,
and ``ABSTRACTION_MCP_STATE``, must resolve inside the directory that person
names in ``ABSTRACTION_MCP_DIR``. The agent can set neither: the server reads
both from its own environment.

Run: python -P -m g6.abstraction.server  (stdio transport; -P keeps the folder it starts in, a project's, off the
import path, so the project's own packages never replace G6's)
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import server as _sdk

from .abstractions import COMBINATORS, SpecError, from_spec
from .cegar import (find_counterexamples as _find_cexs, refine_until,
                    tighten_until)
from .diagnostics import diagnose as _diagnose
from .domain import ConcreteDomain
from .gate import run_gate
from .library import Library, STALE_WARN
from .pruning import advise_pruning as _advise

# The mcp SDK's FastMCP builds its settings with env_file=".env", read from the folder the server starts in, which is
# the project's, whatever FASTMCP_ENV_FILE says (the security review of W1-W5, F3): this server reads no such file.
# FASTMCP_* variables in the person's own environment are read as before.
_sdk.Settings.model_config["env_file"] = None

mcp = FastMCP(
    "abstraction-harness",
    instructions=(
        "A reflective abstraction-engineering harness with self-debugging. "
        "Workflow: load_dataset -> describe_domain -> list_combinators -> "
        "propose_abstraction (iterate on gate feedback) -> "
        "evaluate_abstraction -> refine_abstraction (CEGAR) -> leaderboard. "
        "Run diagnose regularly: it audits the harness's own verification "
        "decisions (inert mechanisms, staleness, winner's curse, "
        "miscalibration) and returns remediations as concrete next tool "
        "calls. Use advise_pruning (guarded) on fresh candidates, "
        "record_evaluation to feed results back, revalidate when entries go "
        "stale, and lift_abstraction to reuse trace-generic mechanisms on "
        "the search over abstractions itself."),
)

_domains: dict[str, ConcreteDomain] = {}
_library = Library()

#: The directory the person who runs the server declares; every file path is read inside it.
DIR_ENV = "ABSTRACTION_MCP_DIR"
#: The state file loaded at start and saved as tools change the state, inside ``DIR_ENV``.
STATE_ENV = "ABSTRACTION_MCP_STATE"
FILE_TOOLS = ("load_dataset", "save_state", "load_state")
DENIED = "G6_E_SENSITIVE_COMPONENT_DENIED"
PATH_REFUSED = "G6_E_ABSTRACTION_PATH_REFUSED"
#: The setting g6-mcp reads (``g6.mcp.SENSITIVE_ENV``). A component may not import g6.mcp, the surface above the
#: components, so it is parsed here by the same rule; a test keeps the two in step.
SENSITIVE_ENV = "G6_MCP_SENSITIVE"


def _allowed_sensitive() -> frozenset[str]:
    """The names listed in ``G6_MCP_SENSITIVE``, as ``g6.mcp.allowed_sensitive`` reads them: comma-separated
    components or ``component.op``, no wildcard, read from the server's own environment."""
    raw = os.environ.get(SENSITIVE_ENV, "")
    return frozenset(name.strip() for name in raw.split(",") if name.strip())


class FileRefused(ValueError):
    """A file tool or path the person running the server has not allowed, with its ``G6_E_*`` code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"[{code}] {reason}")
        self.code = code


def _inside_dir(path: str) -> str:
    """``path`` resolved inside the declared directory (a relative path is read from it), or ``FileRefused``."""
    root = os.environ.get(DIR_ENV, "").strip()
    if not root:
        raise FileRefused(PATH_REFUSED, f"no state directory is declared: the person who runs this server names "
                                        f"one in {DIR_ENV}, and every file path must lie inside it")
    base = Path(root).resolve()
    target = (base / path).resolve()  # an absolute path replaces base; symlinks are followed
    if not target.is_relative_to(base):
        raise FileRefused(PATH_REFUSED, f"{path!r} is outside the state directory {DIR_ENV} declares; give a path "
                                        "inside it")
    return str(target)


def _file_path(tool: str, path: str) -> str:
    """The path a file tool may use: the tool allowed by the person who runs the server, the path inside the dir."""
    allow = _allowed_sensitive()
    if "abstraction" not in allow and f"abstraction.{tool}" not in allow:
        raise FileRefused(DENIED, f"{tool} reads or writes a file on this machine. Only the person who runs this "
                                  f"server can allow it, by adding abstraction.{tool} (or abstraction) to "
                                  f"{SENSITIVE_ENV} where the server is registered.")
    return _inside_dir(path)


def _state_path() -> str | None:
    """``ABSTRACTION_MCP_STATE`` inside the declared directory, ``None`` when it is unset, or ``FileRefused``."""
    name = os.environ.get(STATE_ENV, "").strip()
    return _inside_dir(name) if name else None


def _refused(exc: FileRefused) -> str:
    return _j({"error": str(exc), "code": exc.code, "completion_state": "blocked-escalated"})


def _autoload() -> None:
    """Load the state file at start. A state file outside the declared directory stops the server starting."""
    global _library, _domains
    path = _state_path()
    if path and os.path.exists(path):
        _library, _domains = Library.load(path)


def _autosave() -> None:
    path = _state_path()
    if path:
        _library.save(path, _domains)


def _dom(name: str) -> ConcreteDomain:
    if name not in _domains:
        raise ValueError(f"unknown domain {name!r}; loaded: {list(_domains)}")
    return _domains[name]


def _j(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def _err(exc: Exception) -> str:
    return _j({"error": f"{type(exc).__name__}: {exc}"})


# ---------------------------------------------------------------------- #
@mcp.tool()
def load_dataset(path: str, objective: str, maximise: bool = True,
                 name: str = "domain", fmt: str = "auto") -> str:
    """Register a concrete domain from a dataset file.

    Refused unless the person who runs the server allows it (abstraction or
    abstraction.load_dataset in G6_MCP_SENSITIVE); the path is read inside
    the directory they name in ABSTRACTION_MCP_DIR.

    Args:
        path: CSV or JSON, inside ABSTRACTION_MCP_DIR (a relative path is read
            from it). CSV: one row per evaluated configuration, columns
            = parameters plus the objective column. JSON: a list of row
            objects or {"rows": [...]}. Row order = evaluation trace order.
        objective: name of the objective column (axiom A0: the only source
            of objective values, ever).
        maximise: True if higher objective is better.
        name: handle for later calls.
        fmt: "csv" | "json" | "auto" (by extension).

    The loader validates the dataset (finite objectives, non-constant
    objective, duplicate share) and reports diagnostics with the summary.
    """
    try:
        path = _file_path("load_dataset", path)
    except FileRefused as exc:
        return _refused(exc)
    try:
        kind = fmt if fmt != "auto" else ("json" if path.endswith(".json") else "csv")
        dom = (ConcreteDomain.from_json if kind == "json"
               else ConcreteDomain.from_csv)(path, objective,
                                             maximise=maximise, name=name)
        issues: list[str] = []
        scores = [dom.score(r.objective) for r in dom.records]
        if any(math.isnan(s) or math.isinf(s) for s in scores):
            return _j({"error": "dataset contains NaN/inf objective values; "
                                "clean them before loading (axiom A0 demands "
                                "trustworthy measurements)"})
        if max(scores) - min(scores) < 1e-12:
            issues.append("objective is constant: no abstraction can add "
                          "value; check the objective column")
        keys = [tuple(sorted(r.config.items())) for r in dom.records]
        dup = 1 - len(set(keys)) / max(1, len(keys))
        if dup > 0.5:
            issues.append(f"{dup:.0%} duplicate configurations: replay "
                          f"value estimates will be optimistic")
        _domains[name] = dom
        _autosave()
        return _j({"loaded": name, "issues": issues, **dom.describe()})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def describe_domain(name: str = "domain") -> str:
    """Describe a loaded domain: parameters, trace length, score range."""
    try:
        return _j(_dom(name).describe())
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def list_combinators() -> str:
    """List the abstraction DSL combinators, their validated parameter
    ranges, and example specs. Compose via 'product' nesting (depth <= 4);
    stacks are sound by construction when components are, and every
    candidate is still empirically gated."""
    return _j({"combinators": COMBINATORS,
               "spec_examples": [
                   {"kind": "quotient", "radius": 0.15, "lipschitz": 2.0},
                   {"kind": "project", "dims": ["lr", "batch"], "bins": 4,
                    "lipschitz": 1.0},
                   {"kind": "sufficient", "dim": "lr", "arms": 4},
                   {"kind": "product",
                    "left": {"kind": "quotient", "radius": 0.2},
                    "right": {"kind": "sufficient", "dim": "lr"}}]})


@mcp.tool()
def propose_abstraction(spec_json: str, domain: str = "domain") -> str:
    """Denote a candidate abstraction (validated: resource limits on radius,
    bins, arms, nesting depth) and run the verification gate (extensivity,
    monotonicity, prospective bound soundness, calibration).

    A failed gate is feedback, not a dead end: counterexamples say WHERE the
    spec over-claims (lipschitz too small -> bound violations; radius too
    large -> regret risk). Iterate, or evaluate and CEGAR-refine."""
    try:
        abstraction = from_spec(json.loads(spec_json))
        report = run_gate(abstraction, _dom(domain))
        return _j({"spec": abstraction.spec(), "gate": report.to_dict()})
    except (SpecError, ValueError, KeyError) as exc:
        return _err(exc)


@mcp.tool()
def evaluate_abstraction(spec_json: str, domain: str = "domain",
                         budget: int | None = None, notes: str = "") -> str:
    """Full robust evaluation, recorded in the library.

    Pipeline: gate -> recorded-order replay -> bootstrap replay over
    permuted trace orderings (soundness must hold on EVERY ordering; the
    'valuable' claim uses a selection-adjusted lower confidence bound that
    tightens with each candidate tried on this domain) -> MDL -> status:

      checked      robustly sound AND valuable at the adjusted level (the
                   module's own status, never a G6 verified)
      provisional  robustly sound, value not yet demonstrated
      degenerate   sound but structurally trivial (single cell)
      rejected     gate failure or any regret on any ordering

    Unsound or degenerate entries have blp_score clamped to <= 0."""
    try:
        abstraction = from_spec(json.loads(spec_json))
        entry = _library.evaluate(abstraction, _dom(domain),
                                  budget=budget, notes=notes)
        _autosave()
        return _j(entry.to_dict())
    except (SpecError, ValueError, KeyError) as exc:
        return _err(exc)


@mcp.tool()
def find_counterexamples(entry_id: str, budget: int | None = None) -> str:
    """CEGAR input for a library entry: soundness counterexamples (bound
    violations, replay regrets) and precision counterexamples (sound but
    uselessly coarse cells)."""
    try:
        entry = _library.get(entry_id)
        rep = _find_cexs(_library.instantiate(entry_id),
                         _dom(entry.domain_name), budget=budget)
        return _j({"entry_id": entry_id, **rep.to_dict()})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def refine_abstraction(entry_id: str, max_iterations: int = 5,
                       budget: int | None = None) -> str:
    """CEGAR: apply the adaptive split operator on the worst soundness
    counterexample until none remain or the cap hits. The refined mechanism
    is re-evaluated as a new entry with parent=entry_id, so refinement's MDL
    trade-off is visible on the leaderboard."""
    try:
        entry = _library.get(entry_id)
        dom = _dom(entry.domain_name)
        result = refine_until(_library.instantiate(entry_id), dom,
                              max_iterations=max_iterations, budget=budget)
        new_entry = _library.evaluate(from_spec(result.spec_after), dom,
                                      budget=budget, parent=entry_id,
                                      notes="CEGAR-refined",
                                      supersedes=entry_id)
        _autosave()
        return _j({"refinement": result.to_dict(),
                   "new_entry": new_entry.to_dict()})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def leaderboard(domain: str | None = None,
                include_superseded: bool = False) -> str:
    """Library ranked status-first (checked > provisional > degenerate >
    rejected), then by robust (BLP) score. By default shows only the
    FRONTIER (entries not superseded by a refinement/tightening/
    revalidation); pass include_superseded=True for full provenance. Also
    lists combinator kinds checked on >= 2 domains (promotion rule) and
    per-domain selection pressure (the Bonferroni k)."""
    return _j({"leaderboard": _library.leaderboard(
                   domain, include_superseded=include_superseded),
               "promotable_kinds": _library.promotable(),
               "selection_pressure": _library.selection_pressure()})


@mcp.tool()
def advise_pruning(entry_id: str, candidates_json: str,
                   incumbent: float | None = None) -> str:
    """Apply a library mechanism to fresh candidate configs — GUARDED.

    Guards (each forces prune=False with a stated reason rather than
    silently advising):
      status guard     only checked entries evaluated in this session may
                       prune; provisional entries advise with a caveat;
                       rejected/degenerate never prune
      staleness guard  trace growth since evaluation > 100% refuses pruning
                       (revalidate first); > 25% adds a caveat
      support guard    candidates farther than 0.25 (normalised L-inf) from
                       ALL data never get pruned — bounds are only
                       empirically calibrated on the visited region, so
                       extrapolated pruning is where the bounds are untested

    Args:
        entry_id: a library entry.
        candidates_json: JSON list of config objects (all domain params).
        incumbent: current best score (higher-better); defaults to trace best.
    """
    try:
        entry = _library.get(entry_id)
        result = _advise(_library, entry_id, _dom(entry.domain_name),
                         json.loads(candidates_json), incumbent)
        result.pop("may_prune", None)
        return _j(result)
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def record_evaluation(domain: str, config_json: str, objective: float) -> str:
    """Append a newly evaluated point to the domain trace (active mode).
    Axiom A0 in code: objectives enter only as caller-supplied data; no tool
    computes or adjusts them, so no abstraction can influence its own
    measurement. Staleness of existing entries is reported so the agent
    knows when revalidation is due."""
    try:
        dom = _dom(domain)
        rec = dom.append(json.loads(config_json), objective)
        _autosave()
        stale = [e.entry_id for e in _library.entries.values()
                 if e.domain_name == domain and e.staleness(dom) > STALE_WARN]
        return _j({"recorded_index": rec.index,
                   "n_records": len(dom.records),
                   "entries_now_stale": stale})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def revalidate(entry_id: str, budget: int | None = None) -> str:
    """Re-evaluate an entry's spec on the CURRENT trace (remediation for
    staleness and for self-consistency findings). Produces a fresh entry
    with parent=entry_id; the old entry remains for provenance."""
    try:
        entry = _library.get(entry_id)
        fresh = _library.evaluate(from_spec(entry.spec),
                                  _dom(entry.domain_name), budget=budget,
                                  parent=entry_id, notes="revalidation",
                                  force=True, supersedes=entry_id)
        _autosave()
        return _j({"revalidated": entry_id, "new_entry": fresh.to_dict()})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def lift_abstraction(entry_id: str, domain: str | None = None) -> str:
    """Lift functor: reuse a trace-generic mechanism on the meta-domain
    (configs = featurised abstraction specs, objective = recorded robust
    BLP scores). E.g. a lifted quotient is meta-Tabu: don't propose an
    abstraction similar to one already evaluated. Fails, by design, for
    domain-specific mechanisms. The tower stays grounded: meta objectives
    are recorded level-0 scores (Proposition 4)."""
    try:
        from .lift import lift as _lift, liftable
        base = _library.instantiate(entry_id)
        if not liftable(base):
            return _j({"error": "mechanism is domain-specific; Lift undefined"})
        lifted, meta = _lift(base, _library, domain)
        _domains[meta.name] = meta
        entry = _library.evaluate(from_spec(lifted.spec()), meta,
                                  parent=entry_id,
                                  notes="lifted to meta-domain")
        _autosave()
        return _j({"meta_domain": meta.describe(),
                   "lifted_entry": entry.to_dict()})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def tighten_abstraction(entry_id: str, budget: int | None = None,
                        iterations: int = 6) -> str:
    """The dual of refine (remediation for INERT mechanisms). CEGAR's
    refine loosens toward soundness; tighten binary-searches the smallest
    uniform scale on the mechanism's lipschitz fields that STILL passes the
    gate with zero regret on every bootstrap ordering — turning
    unused slack into pruning power under the same soundness standard as
    evaluation. The tightened mechanism is evaluated as a new entry that
    supersedes entry_id. No-op (with explanation) for guidance-only
    mechanisms and a redirect to refine for unsound ones."""
    try:
        entry = _library.get(entry_id)
        dom = _dom(entry.domain_name)
        result = tighten_until(_library.instantiate(entry_id), dom,
                               budget=budget, iterations=iterations,
                               k_permutations=_library.k_permutations)
        if not result.tightened:
            return _j({"tightening": result.to_dict(),
                       "new_entry": None})
        new_entry = _library.evaluate(from_spec(result.spec_after), dom,
                                      budget=budget, parent=entry_id,
                                      notes="tightened", force=True,
                                      supersedes=entry_id)
        _autosave()
        return _j({"tightening": result.to_dict(),
                   "new_entry": new_entry.to_dict()})
    except Exception as exc:
        return _err(exc)


_SELF_DEBUG_SAFE = ("refine_abstraction", "tighten_abstraction", "revalidate")


@mcp.tool()
def self_debug(max_rounds: int = 3, budget: int | None = None) -> str:
    """Closed-loop self-debugging: diagnose -> execute remediations ->
    re-diagnose, bounded and convergence-checked.

    Each round runs the diagnostic audit, then executes every
    machine-actionable remediation whose tool is one of
    refine_abstraction / tighten_abstraction / revalidate — the
    re-analysis operators. Tools that create data (record_evaluation,
    load_dataset) are NEVER auto-executed: axiom A0 extends to the debugger
    itself — the harness may re-examine its reasoning, not manufacture
    evidence for it.

    Loop discipline (remediation for open-loop debugging):
      remediation-tabu   each (tool, entry) pair is executed at most once
                         across the whole run — the harness's own quotient
                         idea applied to its debugging actions
      convergence check  stops early when the finding fingerprint is
                         unchanged and no executable remediation remains;
                         outcome is 'healthy', 'converged' (stable findings,
                         nothing left to execute) or 'budget_exhausted'

    Returns the round-by-round transcript: findings before, actions taken
    with their outcomes, and the final diagnosis summary."""
    try:
        executed: set[tuple[str, str]] = set()
        rounds: list[dict[str, Any]] = []
        prev_fp: tuple | None = None
        outcome = "budget_exhausted"
        for rnd in range(max_rounds):
            diag = _diagnose(_library, _domains).to_dict()
            fp = tuple(sorted((f["code"], f["subject"])
                              for f in diag["findings"]))
            actions: list[dict[str, Any]] = []
            for f in diag["findings"]:
                rem = f.get("remediation") or {}
                tool, args = rem.get("tool"), rem.get("args") or {}
                eid = args.get("entry_id")
                if tool not in _SELF_DEBUG_SAFE or not eid:
                    continue
                if eid in _library.entries and                         _library.entries[eid].superseded_by:
                    continue  # already remediated via lineage
                key = (tool, eid)
                if key in executed:
                    continue
                executed.add(key)
                fn = {"refine_abstraction": refine_abstraction,
                      "tighten_abstraction": tighten_abstraction,
                      "revalidate": revalidate}[tool]
                res = json.loads(getattr(fn, "fn", fn)(
                    entry_id=eid, budget=budget))
                new = res.get("new_entry") or {}
                actions.append({
                    "finding": f["code"], "tool": tool, "entry_id": eid,
                    "outcome": ({"new_entry": new.get("entry_id"),
                                 "status": new.get("status"),
                                 "blp_score": new.get("blp_score")}
                                if new else
                                {"note": (res.get("tightening", {})
                                          .get("note") or
                                          res.get("error") or "no-op")})})
            rounds.append({"round": rnd,
                           "healthy_before": diag["summary"]["healthy"],
                           "n_findings": diag["n_findings"],
                           "findings": [(f["severity"], f["code"],
                                         f["subject"])
                                        for f in diag["findings"]],
                           "actions": actions})
            if diag["summary"]["healthy"] and not actions:
                outcome = "healthy"
                break
            if not actions and fp == prev_fp:
                outcome = "converged"
                break
            if not actions:
                outcome = "converged"
                break
            prev_fp = fp
        final = _diagnose(_library, _domains).to_dict()
        _autosave()
        return _j({"outcome": outcome, "rounds": rounds,
                   "n_actions": sum(len(r["actions"]) for r in rounds),
                   "final": {"summary": final["summary"],
                             "findings": [(f["severity"], f["code"],
                                           f["subject"])
                                          for f in final["findings"]]}})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def diagnose() -> str:
    """Self-debugging audit: the harness examines its own verification
    decisions and returns findings ordered by severity, each with a
    machine-actionable remediation (the exact next tool call to issue).

    Detects: inert mechanisms (checked vacuously — bounds never bind),
    bound miscalibration (the checker disagreeing with itself),
    stale entries, winner's-curse risk under selection pressure, degenerate
    proposals, domains with no checked mechanism (with the CEGAR target to
    try), underpowered meta-domains, and low-data domains. summary.healthy
    is False whenever any 'act'-severity finding exists."""
    try:
        return _j(_diagnose(_library, _domains).to_dict())
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def save_state(path: str) -> str:
    """Persist the library and all domains to a JSON state file inside
    ABSTRACTION_MCP_DIR. Refused unless the person who runs the server
    allows it (abstraction or abstraction.save_state in G6_MCP_SENSITIVE)."""
    try:
        target = _file_path("save_state", path)
    except FileRefused as exc:
        return _refused(exc)
    try:
        _library.save(target, _domains)
        return _j({"saved": path, "n_entries": len(_library.entries),
                   "n_domains": len(_domains)})
    except Exception as exc:
        return _err(exc)


@mcp.tool()
def load_state(path: str) -> str:
    """Restore the library and domains from a state file inside
    ABSTRACTION_MCP_DIR (replaces current in-memory state; loaded entries
    never authorise pruning until revalidated). Refused unless the person who
    runs the server allows it (abstraction or abstraction.load_state in
    G6_MCP_SENSITIVE)."""
    global _library, _domains
    try:
        source = _file_path("load_state", path)
    except FileRefused as exc:
        return _refused(exc)
    try:
        _library, _domains = Library.load(source)
        return _j({"loaded": path, "n_entries": len(_library.entries),
                   "domains": list(_domains)})
    except Exception as exc:
        return _err(exc)


def main() -> None:
    _autoload()  # raises FileRefused, so the server does not start, when the state file is outside the directory
    mcp.run()


if __name__ == "__main__":
    main()
