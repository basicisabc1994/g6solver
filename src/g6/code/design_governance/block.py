# SPDX-License-Identifier: Apache-2.0
"""DesignGovernanceBlock: the registry and MCP door (``g6_invoke``) to design_governance.

Every op reads only the file map it is given (path to source), parses it with ``ast`` and runs none of it; nothing
walks the server's disk, so no op is sensitive. The refusal rules the archive's scorecard used carry over: a check
that found nothing to check is refused, never read as clean (no import between the package's modules, a contract
that matches no module), and a file that does not parse is listed and keeps a result from being clean. A clean
result is a ``qualified-draft``: structure only, and dynamic imports are not seen.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.code._types import CodeRefused, answer, refuse
from g6.code.design_governance import graph as g
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

COMPONENT = "design_governance"
INPUT = "G6_E_DESIGN_INPUT"
NO_MODULES = "G6_E_DESIGN_NO_MODULES"
INCOMPLETE = "G6_E_DESIGN_INCOMPLETE"
READ = "G6_E_DESIGN_READ"
FOUND = "G6_E_DESIGN_FINDINGS"
CLEAN = "G6_E_DESIGN_NONE_FOUND"
MAX_FILES = 2000
MAX_FILE_CHARS = 500_000
MAX_TOTAL_CHARS = 20_000_000
SENSITIVE_OPS: tuple[str, ...] = ()
_OPS: dict[str, str] = {
    "import_graph": "import_graph(files, package, depth): the imports between the package's modules, grouped by their "
                    "first depth dotted parts, each with an example file and line; external imports counted",
    "cycles": "cycles(files, package, depth): import cycles (Tarjan's strongly connected components), each with one "
              "cycle through it and the imports that close it",
    "layer_violations": "layer_violations(files, package, contract): imports from a lower layer into a higher one, "
                        "and imports a forbidden pair bans; a contract that matches no module is refused",
    "pass_through": "pass_through(files): functions whose one statement forwards their parameters unchanged to "
                    "another call",
    "diff_cycles": "diff_cycles(before, after, package, depth): the import cycles a change introduced or removed, and "
                   "the new imports inside the introduced ones",
}
_ACTIONS = {
    INPUT: "Give the fields the op needs (the help op lists them).",
    NO_MODULES: "Name the package the files hold (the first part of their paths), or give its files.",
    g.NO_EDGES: "No import between the package's modules was found: check package and the paths before trusting any "
                "clean result.",
    g.CONTRACT_UNMATCHED: "Correct the contract: each layer and pair must name modules of the package.",
    g.BAD_PATH: "Give paths relative to the folder holding the package, such as shop/cart/model.py.",
    INCOMPLETE: "Fix or remove the files that do not parse; until they parse, nothing found is not a clean result.",
    READ: "Structure only: dynamic imports and complexity inside a function are not seen.",
    FOUND: "Read each finding with its file and line; a cycle or a breach is a design decision to make, or undo.",
    CLEAN: "None found, in structure only: dynamic imports (importlib, __import__) are not seen.",
}


class Forbid(BaseModel):
    """A forbidden pair: modules under ``source`` must not import modules under ``forbidden``."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=200)
    forbidden: str = Field(min_length=1, max_length=200)


class Contract(BaseModel):
    """Layers, highest first (each a dotted module prefix), and forbidden pairs."""

    model_config = ConfigDict(extra="forbid")

    layers: tuple[str, ...] = Field((), max_length=20)
    forbidden: tuple[Forbid, ...] = Field((), max_length=50)


def _bounded(value: dict[str, str]) -> dict[str, str]:
    if len(value) > MAX_FILES:
        raise ValueError(f"at most {MAX_FILES} files")
    if any(len(text) > MAX_FILE_CHARS for text in value.values()):
        raise ValueError(f"each file at most {MAX_FILE_CHARS} characters")
    if sum(len(text) for text in value.values()) > MAX_TOTAL_CHARS:
        raise ValueError(f"at most {MAX_TOTAL_CHARS} characters in all")
    return value


class DesignGovernanceInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["import_graph", "cycles", "layer_violations", "pass_through", "diff_cycles", "ops", "help"] = "help"
    files: dict[str, str] = Field(default_factory=dict)
    before: dict[str, str] = Field(default_factory=dict)
    after: dict[str, str] = Field(default_factory=dict)
    package: str = Field("", max_length=200)
    depth: int = Field(2, ge=1, le=6)
    contract: Contract | None = None
    include_lazy: bool = True
    include_type_checking: bool = True
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("files", "before", "after")
    @classmethod
    def _bounded_maps(cls, value: dict[str, str]) -> dict[str, str]:
        return _bounded(value)


def _ids(d: DesignGovernanceInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _graph(files: dict[str, str], d: DesignGovernanceInput, *, what: str = "files") -> g.Graph:
    if not d.package.strip() or not all(p.isidentifier() for p in d.package.split(".")):
        raise CodeRefused(INPUT, "give package: the dotted name of the package the files hold, such as shop")
    if not files:
        raise CodeRefused(INPUT, f"give {what}: a map of path (relative, such as shop/cart.py) to source")
    found = g.build(files, d.package)
    if not found.modules:
        raise CodeRefused(NO_MODULES, f"no file in {what} is a module of {d.package}")
    # the imports the request counts: those inside functions and under TYPE_CHECKING only when asked
    found.edges = [e for e in found.edges if (d.include_lazy or not e.lazy)
                   and (d.include_type_checking or not e.type_checking)]
    return found


def _read(found: g.Graph) -> dict[str, Any]:
    return {"modules": len(found.modules), "imports_inside": len(found.edges), "external_imports": found.external,
            "unparsed": found.unparsed, "not_python": found.skipped[:50]}


def _needs_edges(found: g.Graph, op: str) -> None:
    if not found.edges:
        raise CodeRefused(g.NO_EDGES, f"no import between {found.package}'s {len(found.modules)} module(s) was found, "
                                      f"so {op} would be clean about nothing", unparsed=found.unparsed)


def _evidence(found: g.Graph, d: DesignGovernanceInput) -> list[dict[str, Any]]:
    return [{"kind": "static_read", "by": "ast", "ran_code": False, "package": found.package, "depth": d.depth,
             "lazy_imports_counted": d.include_lazy, "type_checking_imports_counted": d.include_type_checking,
             **_read(found)}]


def _findings(op: str, d: DesignGovernanceInput, found: g.Graph, items: list, noun: str,
              **fields: Any) -> dict[str, Any]:
    if items:
        return answer(COMPONENT, op, FOUND, f"{len(items)} {noun} in {found.package}.", action=_ACTIONS[FOUND],
                      severity="red", evidence=_evidence(found, d), **_ids(d), clean=False, **fields, **_read(found))
    if found.unparsed:
        raise CodeRefused(INCOMPLETE, f"no {noun} found, but {len(found.unparsed)} file(s) did not parse (first "
                                      f"{found.unparsed[0]['file']}), so this is not a clean result",
                          _evidence(found, d), **_read(found))
    return answer(COMPONENT, op, CLEAN, f"No {noun} in {found.package}'s {len(found.modules)} module(s) and "
                  f"{len(found.edges)} import(s); in structure only.", action=_ACTIONS[CLEAN],
                  evidence=_evidence(found, d), **_ids(d), clean=True, **fields, **_read(found))


def import_graph(d: DesignGovernanceInput) -> dict[str, Any]:
    found = _graph(d.files, d)
    grouped = found.grouped(d.depth, lazy=d.include_lazy, type_checking=d.include_type_checking)
    edges = [{"from": a, "to": b, "imports": len(es), "example": {"file": es[0].file, "line": es[0].line}}
             for a, targets in sorted(grouped.items()) for b, es in sorted(targets.items())]
    nodes = {}
    for module in found.modules:
        nodes[g.group(module, d.depth)] = nodes.get(g.group(module, d.depth), 0) + 1
    said = f"{len(nodes)} node(s) and {len(edges)} edge(s) between them, from {len(found.modules)} module(s)."
    if not found.edges:
        said += " No import between the package's modules was found: check package and the paths."
    if found.unparsed:
        said += f" {len(found.unparsed)} file(s) did not parse and are left out."
    return answer(COMPONENT, "import_graph", READ, said, action=_ACTIONS[READ],
                  severity="red" if not found.edges or found.unparsed else "amber", evidence=_evidence(found, d),
                  **_ids(d), nodes=dict(sorted(nodes.items())), edges=edges, **_read(found))


def cycles(d: DesignGovernanceInput) -> dict[str, Any]:
    found = _graph(d.files, d)
    _needs_edges(found, "cycles")
    found_cycles = g.cycles(found, d.depth, lazy=d.include_lazy, type_checking=d.include_type_checking)
    return _findings("cycles", d, found, found_cycles, "import cycle(s)", cycles=found_cycles)


def layer_violations(d: DesignGovernanceInput) -> dict[str, Any]:
    if d.contract is None or not (d.contract.layers or d.contract.forbidden):
        raise CodeRefused(INPUT, "give contract: layers (highest first) and/or forbidden pairs")
    found = _graph(d.files, d)
    _needs_edges(found, "layer_violations")
    pairs = tuple((f.source, f.forbidden) for f in d.contract.forbidden)
    violations = g.layer_violations(found, d.contract.layers, pairs)
    return _findings("layer_violations", d, found, violations, "layer breach(es)", violations=violations,
                     contract=d.contract.model_dump())


def pass_through(d: DesignGovernanceInput) -> dict[str, Any]:
    if not d.files:
        raise CodeRefused(INPUT, "give files: a map of path to source")
    found, unparsed = g.pass_through(d.files)
    said = f"{len(found)} pass-through function(s) in {len(d.files)} file(s)."
    if unparsed:
        said += f" {len(unparsed)} file(s) did not parse and were not read."
    return answer(COMPONENT, "pass_through", READ, said, action=_ACTIONS[READ],
                  evidence=[{"kind": "static_read", "by": "ast", "ran_code": False, "files": len(d.files)}],
                  **_ids(d), functions=found, unparsed=unparsed)


def diff_cycles(d: DesignGovernanceInput) -> dict[str, Any]:
    before = _graph(d.before, d, what="before")
    after = _graph(d.after, d, what="after")
    _needs_edges(after, "diff_cycles")
    if before.unparsed or after.unparsed:
        raise CodeRefused(INCOMPLETE, f"{len(before.unparsed) + len(after.unparsed)} file(s) did not parse, so the "
                                      "cycles before and after cannot be compared", unparsed_before=before.unparsed,
                          unparsed_after=after.unparsed)
    out = g.diff_cycles(before, after, d.depth)
    said = (f"{len(out['introduced'])} cycle(s) introduced and {len(out['removed'])} removed "
            f"({out['before']} before, {out['after']} after).")
    return answer(COMPONENT, "diff_cycles", FOUND if out["introduced"] else READ, said,
                  action=_ACTIONS[FOUND] if out["introduced"] else _ACTIONS[READ],
                  severity="red" if out["introduced"] else "amber",
                  evidence=_evidence(before, d) + _evidence(after, d), **_ids(d), **out)


_HANDLERS = {"import_graph": import_graph, "cycles": cycles, "layer_violations": layer_violations,
             "pass_through": pass_through, "diff_cycles": diff_cycles}


@block_contract(
    purpose="check a codebase's structure, as a supplied file map, for import cycles, layer breaches and "
            "pass-through wrappers, and say which cycles a change introduced, so code does not grow more complex "
            "than anyone can safely change",
    mitigates_failure_modes=("unmanaged_complexity",),
    verification_method="ast reads of the files given; Tarjan's strongly connected components; a contract or a "
                        "graph that matches nothing is refused, and a file that does not parse keeps a result from "
                        "being clean",
)
@component_maturity("beta", reason="the archive's Tarjan, pass-through detector and refusal rules over any package "
                                   "given as a file map, tested on packages that are not the archive's")
class DesignGovernanceBlock(AIBlock[DesignGovernanceInput, dict, dict]):
    """Check a package's import structure. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: DesignGovernanceInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            return Result.ok(_HANDLERS[data.op](data))
        except (RecursionError, MemoryError) as exc:
            return Result.ok(refuse(COMPONENT, data.op, "G6_E_CODE_RESOURCE_LIMIT",
                                    f"input exceeds syntax processing limits: {type(exc).__name__}",
                                    action="Supply a smaller, less deeply nested source.", **_ids(data)))
        except CodeRefused as exc:
            return Result.ok(refuse(COMPONENT, data.op, exc.code, exc.reason, action=_ACTIONS.get(
                exc.code, "Correct the request as the reason says."), evidence=exc.evidence, **_ids(data),
                **exc.fields))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "Contract", "DesignGovernanceBlock", "DesignGovernanceInput", "Forbid"]
