# SPDX-License-Identifier: Apache-2.0
"""Local dependency metadata and chained import failures; no optional package is imported."""
from __future__ import annotations

import csv
from functools import cache
from importlib import machinery, metadata, util
from pathlib import PurePosixPath


def missing_module(exc: BaseException) -> str | None:
    """Diagnostic name beneath visible wrappers; this alone does not establish absence."""
    pending = [(exc, False)]
    seen = set()
    diagnostic = None
    while pending:
        current, visited = pending.pop()
        if visited:
            if isinstance(current, ModuleNotFoundError) and current.name:
                return current.name
            if diagnostic is None and isinstance(current, ImportError) and current.name:
                diagnostic = current.name
            continue
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.append((current, True))
        if isinstance(current, BaseExceptionGroup):
            pending.extend((member, False) for member in reversed(current.exceptions))
        if current.__context__ is not None and not current.__suppress_context__:
            pending.append((current.__context__, False))
        if current.__cause__ is not None:
            pending.append((current.__cause__, False))
    return diagnostic


def _missing_modules(exc: BaseException) -> tuple[str, ...] | None:
    """Missing-module leaves of visible chains/groups; None denotes a defect."""
    pending = [(exc, None, 0)]
    seen = set()
    results = []
    while pending:
        current, children, link_count = pending.pop()
        if children is not None:
            branches = results[-len(children):] if children else []
            if children:
                del results[-len(children):]
            if isinstance(current, BaseExceptionGroup) and any(not branch for branch in branches[link_count:]):
                return None  # Every group member needs its own missing-module evidence.
            modules = tuple(dict.fromkeys(module for branch in branches for module in branch))
            if not modules and not isinstance(current, BaseExceptionGroup):
                if isinstance(current, ModuleNotFoundError) and current.name:
                    modules = (current.name,)
                elif not children:
                    return None
            seen.remove(id(current))
            results.append(modules)
            continue
        if id(current) in seen:
            # A cycle edge adds no evidence. Revisit shared nodes on independent paths.
            results.append(())
            continue
        seen.add(id(current))
        links = []
        if current.__cause__ is not None:
            links.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            links.append(current.__context__)
        members = list(current.exceptions) if isinstance(current, BaseExceptionGroup) else []
        children = links + members
        pending.append((current, children, len(links)))
        pending.extend((child, None, 0) for child in reversed(children))
    return results[0]


def _record_roots(record: str) -> set[str]:
    roots = set()
    suffixes = sorted((*machinery.SOURCE_SUFFIXES, *machinery.BYTECODE_SUFFIXES,
                       *machinery.EXTENSION_SUFFIXES), key=len, reverse=True)
    for row in csv.reader(record.splitlines()):
        if not row:
            continue
        path = PurePosixPath(row[0].replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            continue
        if any(part.endswith((".dist-info", ".data", ".pth")) or part == "__pycache__" for part in path.parts):
            continue
        root = path.parts[0] if len(path.parts) > 1 else next(
            (path.name[:-len(suffix)] for suffix in suffixes if path.name.endswith(suffix)), "")
        if root.isidentifier():
            roots.add(root)
    return roots


def _import_roots() -> dict[str, set[str]]:
    from importlib.metadata import distributions

    from packaging.utils import canonicalize_name

    roots: dict[str, set[str]] = {}
    for module, providers in metadata.packages_distributions().items():
        root = module.split(".", 1)[0]
        if not root.isidentifier():
            continue
        for distribution in providers:
            roots.setdefault(canonicalize_name(distribution), set()).add(root)
    for distribution in distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        top_level = distribution.read_text("top_level.txt")
        if top_level and top_level.strip():
            roots.setdefault(name, set()).update(
                root for module in top_level.split() if (root := module.split(".", 1)[0]).isidentifier())
            continue  # Declared import paths take precedence over RECORD inference.
        record = distribution.read_text("RECORD")
        if record is not None:
            # Each metadata source adds ownership, even when a recorded file is absent.
            roots.setdefault(name, set()).update(_record_roots(record))
    return roots


def _extra_requirements(extras: str):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    project = [Requirement(raw) for raw in metadata.requires("g6") or []]
    pending = extras.split(",")
    seen = set()
    while pending:
        extra = pending.pop()
        if extra in seen:
            continue
        seen.add(extra)
        for requirement in project:
            marker = requirement.marker
            if marker is None or marker.evaluate({"extra": ""}) or not marker.evaluate({"extra": extra}):
                continue
            if canonicalize_name(requirement.name) == "g6":
                pending.extend(requirement.extras)
            else:
                yield requirement


def _distribution_roots(name: str, imports: dict[str, set[str]]) -> set[str]:
    from packaging.utils import canonicalize_name

    # Distribution/import aliases also cover extras that are not installed.
    aliases = {"pillow": "PIL", "scikit-learn": "sklearn", "z3-solver": "z3",
               "import-linter": "importlinter", "langgraph-checkpoint": "langgraph"}
    name = canonicalize_name(name)
    fallback = aliases.get(name, name.replace("-", "_")).split(".", 1)[0]
    return imports.get(name) or {fallback}


def _declared_roots(extras: str, imports: dict[str, set[str]]) -> set[str]:
    from importlib.metadata import PackageNotFoundError

    def requires(name):
        try:
            return metadata.requires(name)
        except (PackageNotFoundError, KeyError):
            # Optional records may be absent, including in a supplied metadata map.
            # The required walk uses metadata.requires directly and never catches this.
            return ()

    roots = set()
    for name in _requirement_closure(_extra_requirements(extras), requires):
        roots.update(_distribution_roots(name, imports))
    return roots


def extra_import_roots(extras: str) -> frozenset[str]:
    """Import roots in an extra's transitive closure, before required-owner exclusion."""
    return frozenset(_declared_roots(extras, _import_roots()))


def _requirement_closure(requirements, requires):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    pending = list(requirements)
    seen = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, frozenset(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        yield name
        for raw in requires(requirement.name) or []:
            dependency = Requirement(raw)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in ("", *requirement.extras)
            ):
                pending.append(dependency)


@cache
def optional_import_roots(extras: str, required_extras: str = "mcp") -> frozenset[str]:
    """Declared optional closure minus core and the caller's required extra closure."""
    from packaging.requirements import Requirement

    imports = _import_roots()
    pending = [requirement for raw in metadata.requires("g6") or []
               if (requirement := Requirement(raw)).marker is None
               or any(requirement.marker.evaluate({"extra": extra}) for extra in ("", *required_extras.split(",")))]
    # Missing required metadata is an invalid base install, never an empty closure.
    required = _requirement_closure(pending, metadata.requires)
    base = {module for name in required for module in _distribution_roots(name, imports)}
    return frozenset(_declared_roots(extras, imports) - base)


def absent_module_roots(exc: BaseException, *, mcp_startup: bool = False) -> frozenset[str]:
    """Missing-module evidence; startup also accepts missing submodules of installed MCP roots."""
    modules = _missing_modules(exc)
    if not modules:
        return frozenset()
    roots = set()
    for module in modules:
        root = module.split(".", 1)[0]
        if mcp_startup and root in {"mcp", "fastmcp"}:
            # Startup's missing MCP submodule still needs its extra when the root exists.
            roots.add(root)
            continue
        try:
            if util.find_spec(root) is not None:
                return frozenset()
        except (ImportError, ValueError):
            return frozenset()  # An indeterminate lookup is not evidence of absence.
        roots.add(root)
    return frozenset(roots)


def optional_dependency_missing(server_id: str, exc: BaseException) -> bool:
    from g6.mcp.registrations import EXTRAS

    extras = EXTRAS.get(server_id)
    if not extras or not _missing_modules(exc):
        return False
    # A broken required metadata record must surface even if this import root exists.
    optional = optional_import_roots(extras)
    roots = absent_module_roots(exc)
    return bool(roots and roots <= optional)
