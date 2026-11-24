# SPDX-License-Identifier: Apache-2.0
"""Local dependency metadata and chained import failures; no optional package is imported."""
from __future__ import annotations

import csv
from functools import cache
from importlib import metadata, util
from pathlib import PurePosixPath


def missing_module(exc: BaseException) -> str | None:
    """Diagnostic name beneath visible wrappers; this alone does not establish absence."""
    pending = [(exc, False)]
    seen = set()
    while pending:
        current, visited = pending.pop()
        if visited:
            if isinstance(current, ImportError) and current.name:
                return current.name
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
    return None


def _missing_modules(exc: BaseException, active: set[int]) -> tuple[str, ...] | None:
    """Missing-module leaves of visible chains/groups; None denotes a defect."""
    if id(exc) in active:
        return ()  # A cycle edge supplies no new evidence.
    active.add(id(exc))
    try:
        links = []
        if exc.__cause__ is not None:
            links.append(exc.__cause__)
        if exc.__context__ is not None and not exc.__suppress_context__:
            links.append(exc.__context__)
        modules = ()
        for linked in links:
            branch = _missing_modules(linked, active)
            if branch is None:
                return None
            modules += branch
        if isinstance(exc, BaseExceptionGroup):
            for member in exc.exceptions:
                branch = _missing_modules(member, active)
                if not branch:
                    return None  # Every member needs its own missing-module evidence.
                modules += branch
            return modules
        if modules:
            return modules
        # A named ImportError is only a candidate; local absence is checked below.
        if isinstance(exc, ImportError) and exc.name:
            return (exc.name,)
        return () if links else None
    finally:
        active.remove(id(exc))


def _record_roots(record: str) -> set[str]:
    roots = set()
    for row in csv.reader(record.splitlines()):
        if not row:
            continue
        path = PurePosixPath(row[0].replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            continue
        if any(part.endswith((".dist-info", ".data", ".pth")) or part == "__pycache__" for part in path.parts):
            continue
        root = path.parts[0] if len(path.parts) > 1 else path.stem if path.suffix == ".py" else ""
        if root.isidentifier():
            roots.add(root)
    return roots


def _import_roots() -> dict[str, set[str]]:
    from importlib.metadata import distributions

    from packaging.utils import canonicalize_name

    roots: dict[str, set[str]] = {}
    for module, providers in metadata.packages_distributions().items():
        for distribution in providers:
            roots.setdefault(canonicalize_name(distribution), set()).add(module.split(".", 1)[0])
    for distribution in distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        top_level = distribution.read_text("top_level.txt")
        if top_level and top_level.strip():
            roots[name] = {module.split(".", 1)[0] for module in top_level.split()}
            continue
        record = distribution.read_text("RECORD")
        if record is not None:
            # Some metadata versions do not infer package folders from RECORD.
            # Discard inferred non-import paths as well as filling missing roots.
            roots[name] = _record_roots(record)
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
    roots = set()
    for requirement in _extra_requirements(extras):
        roots.update(_distribution_roots(requirement.name, imports))
    return roots


def extra_import_roots(extras: str) -> frozenset[str]:
    """Import roots declared by an extra, without claiming base modules are optional."""
    return frozenset(_declared_roots(extras, _import_roots()))


@cache
def optional_import_roots(extras: str) -> frozenset[str]:
    """Repair 1's blocked set: declared roots minus the installed g6[mcp] closure."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    imports = _import_roots()
    pending = [requirement for raw in metadata.requires("g6") or []
               if (requirement := Requirement(raw)).marker is None
               or any(requirement.marker.evaluate({"extra": extra}) for extra in ("", "mcp"))]
    seen = set()
    required = set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        key = (name, frozenset(requirement.extras))
        if key in seen:
            continue
        seen.add(key)
        required.add(name)
        # Missing required metadata is an invalid base install, never an empty closure.
        for raw in metadata.requires(requirement.name) or []:
            dependency = Requirement(raw)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in ("", *requirement.extras)
            ):
                pending.append(dependency)
    base = {module for name in required for module in _distribution_roots(name, imports)}
    return frozenset(_declared_roots(extras, imports) - base)


def optional_dependency_missing(server_id: str, exc: BaseException) -> bool:
    from g6.mcp.registrations import EXTRAS

    modules = _missing_modules(exc, set())
    extras = EXTRAS.get(server_id)
    if not modules or not extras:
        return False
    optional = optional_import_roots(extras)
    for module in modules:
        root = module.split(".", 1)[0]
        if root not in optional:
            return False
        try:
            if util.find_spec(root) is not None:
                return False
        except (ImportError, ValueError):
            return False  # An indeterminate lookup is not evidence of absence.
    return True
