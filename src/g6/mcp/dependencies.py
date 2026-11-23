# SPDX-License-Identifier: Apache-2.0
"""Local dependency metadata and chained import failures; no optional package is imported."""
from __future__ import annotations

from functools import cache
from importlib import metadata


def missing_module(exc: BaseException) -> str | None:
    """Innermost named import failure, cause before context, with cycles visited once."""
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
        if current.__context__ is not None:
            pending.append((current.__context__, False))
        if current.__cause__ is not None:
            pending.append((current.__cause__, False))
    return None


def _import_roots() -> dict[str, set[str]]:
    from packaging.utils import canonicalize_name

    roots: dict[str, set[str]] = {}
    for module, distributions in metadata.packages_distributions().items():
        for distribution in distributions:
            roots.setdefault(canonicalize_name(distribution), set()).add(module.split(".", 1)[0])
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


def _declared_roots(extras: str, imports: dict[str, set[str]]) -> set[str]:
    from packaging.utils import canonicalize_name

    # Distribution/import aliases also cover extras that are not installed.
    aliases = {"pillow": "PIL", "scikit-learn": "sklearn", "z3-solver": "z3",
               "import-linter": "importlinter", "langgraph-checkpoint": "langgraph"}
    roots = set()
    for requirement in _extra_requirements(extras):
        name = canonicalize_name(requirement.name)
        fallback = aliases.get(name, name.replace("-", "_")).split(".", 1)[0]
        roots.update(imports.get(name, {fallback}))
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
    base = {module for name in required for module in imports.get(name, ())}
    return frozenset(_declared_roots(extras, imports) - base)


def optional_dependency_missing(server_id: str, exc: BaseException) -> bool:
    from g6.mcp.registrations import EXTRAS

    module = missing_module(exc)
    extras = EXTRAS.get(server_id)
    return bool(module and extras and module.split(".", 1)[0] in optional_import_roots(extras))
