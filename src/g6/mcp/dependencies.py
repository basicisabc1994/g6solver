# SPDX-License-Identifier: Apache-2.0
"""Local dependency metadata and chained import failures; no optional package is imported."""
from __future__ import annotations

import csv
from functools import cache
from importlib import _bootstrap_external, machinery, metadata, util
import os
from pathlib import PurePosixPath
import sys
from threading import RLock


_EXCEPTION_WORK_LIMIT = 100_000
_ownership_lock = RLock()
_known_paths: dict[str, set[tuple]] = {}
_required_paths: set[tuple] = set()


class _ExceptionWork:
    def __init__(self):
        self.remaining = _EXCEPTION_WORK_LIMIT

    def spend(self, count=1):
        self.remaining -= count
        if self.remaining < 0:
            raise RecursionError("exception dependency walk exceeded work limit")


def missing_module(exc: BaseException) -> str | None:
    """Diagnostic name beneath visible wrappers; this alone does not establish absence."""
    pending = [(exc, False)]
    seen = set()
    diagnostic = None
    work = _ExceptionWork()
    while pending:
        work.spend()
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
            members = current.exceptions
            work.spend(len(members))
            pending.extend((member, False) for member in reversed(members))
        if current.__context__ is not None and not current.__suppress_context__:
            pending.append((current.__context__, False))
        if current.__cause__ is not None:
            pending.append((current.__cause__, False))
    return diagnostic


def _missing_modules(exc: BaseException) -> tuple[str, ...] | None:
    """Member-local missing evidence in visible chains/groups; None denotes a defect."""
    # Collect each object's edges once, with a permanent visited set. Named
    # missing errors seed evidence; a group cannot seed its own cyclic members.
    work = _ExceptionWork()
    pending = [exc]
    seen = set()
    nodes = {}
    parents = {}
    member_counts = {}
    supported = set()
    ready = []
    modules = {}
    while pending:
        work.spend()
        current = pending.pop()
        key = id(current)
        if key in seen:
            continue
        seen.add(key)
        nodes[key] = current
        links = []
        if current.__cause__ is not None:
            links.append(current.__cause__)
        if current.__context__ is not None and not current.__suppress_context__:
            links.append(current.__context__)
        members = current.exceptions if isinstance(current, BaseExceptionGroup) else ()
        work.spend(len(links) + len(members))
        if isinstance(current, BaseExceptionGroup):
            member_counts[key] = len(members)
        for child in links:
            parents.setdefault(id(child), []).append((key, False))
        for child in members:
            parents.setdefault(id(child), []).append((key, True))
        pending.extend(reversed((*links, *members)))
        if isinstance(current, ModuleNotFoundError) and current.name:
            # Keep every missing name, including installed/submodule defects
            # with optional evidence further down their chain or cycle.
            modules[current.name] = None
            supported.add(key)
            ready.append(key)

    # Propagate evidence from seeds, never from an unestablished cycle. A chain
    # wrapper needs a supported child; a group needs every member independently
    # supported before it can supply evidence to anything pointing back to it.
    while ready:
        work.spend()
        incoming = parents.get(ready.pop(), ())
        work.spend(len(incoming))
        for parent, member_edge in incoming:
            if parent in supported:
                continue
            if parent in member_counts:
                if not member_edge:
                    continue
                member_counts[parent] -= 1
                if member_counts[parent]:
                    continue
            supported.add(parent)
            ready.append(parent)
    if len(supported) != len(nodes):
        return None  # A defect anywhere invalidates the entire graph.
    work.spend(len(modules))
    return tuple(modules)


def _import_root(module: str) -> str:
    """Accept dynamic import spelling while excluding relative and filesystem paths."""
    if any(char in module for char in "/\\:\0") or any(not part for part in module.split(".")):
        return ""
    return module.split(".", 1)[0]


def _existing_realpath(path):
    """An unresolved spelling is not positive evidence of a different file."""
    try:
        return os.path.realpath(path, strict=True)
    except FileNotFoundError:
        return None


def _root_identities(root: str, paths: set | None = None,
                     absent_paths: set | None = None) -> tuple[set[tuple], bool]:
    """Retain established aliases and whether the lookup completed, without imports."""
    # Use the interpreter's actual FileFinder matching policy, including
    # PYTHONCASEOK and ignore-environment mode, rather than filesystem casing.
    def name_identity(name):
        return ("name", name.lower() if _bootstrap_external._relax_case() else name)

    identities = set()
    established = True

    def lookup(probe):
        nonlocal established
        try:
            return probe()
        except Exception:
            established = False
            return None

    def path_identity(path, source, kind="file"):
        if not path:
            return
        resolved = lookup(lambda: os.path.realpath(path))
        if resolved is not None:
            identities.add((kind, resolved))

        def inode_identity():
            try:
                return os.stat(path), False
            except FileNotFoundError:
                return None, True  # Absence cannot replace an established identity.

        result = lookup(inode_identity)
        status, absent = result if result is not None else (None, False)
        device = inode = None
        if status is not None:
            device, inode = status.st_dev, status.st_ino
            if status.st_ino:
                identities.add(("inode", status.st_dev, status.st_ino))
        elif resolved is not None:
            # Keep live path matches independent, but never update ownership
            # from non-strict realpath's fallback for a missing/broken path.
            resolved = lookup(lambda: _existing_realpath(path))
        # Each successful probe contributes even when its companion failed.
        records = absent_paths if absent else paths
        if records is not None and (absent or resolved is not None or status is not None):
            records.add((kind, source, os.fspath(path), resolved, device, inode))

    def namespace_identities(locations, source):
        if locations is not None:
            for path in locations:
                path_identity(path, (source, os.fspath(path)), "namespace")

    try:
        lookup(lambda: identities.add(name_identity(root)))
        loaded = sys.modules.get(root)
        if loaded is not None:
            identities.add(("loaded", id(loaded)))
            spec = lookup(lambda: getattr(loaded, "__spec__", None))
        else:
            spec = lookup(lambda: util.find_spec(root))
        resolved_name = lookup(lambda: getattr(spec, "name", None))
        if resolved_name:
            lookup(lambda: identities.add(name_identity(resolved_name)))
        origin = lookup(lambda: getattr(spec, "origin", None) if getattr(spec, "has_location", False) else None)
        # Spec and loaded-module attributes are independent evidence. A failed
        # source must not hide a known alias from any other source.
        for source, path in (("origin", origin), ("loaded-file", lookup(lambda: getattr(loaded, "__file__", None))),
                             ("spec-cache", lookup(lambda: getattr(spec, "cached", None))),
                             ("loaded-cache", lookup(lambda: getattr(loaded, "__cached__", None)))):
            lookup(lambda: path_identity(path, source))
        locations = lookup(lambda: getattr(spec, "submodule_search_locations", None))
        lookup(lambda: namespace_identities(locations, "spec-locations"))
        loaded_locations = lookup(lambda: getattr(loaded, "__path__", None))
        lookup(lambda: namespace_identities(loaded_locations, "loaded-locations"))
    except Exception:
        # Lookup uncertainty is possibly required; keep aliases found before it.
        return identities, False
    return identities, established


def _path_aliases(paths):
    identities = set()
    for kind, _, _, resolved, device, inode in paths:
        if resolved is not None:
            identities.add((kind, resolved))
        if inode:
            identities.add(("inode", device, inode))
    return identities


def _compatible_paths(previous, current):
    """Same source with no conflicting successful identity probe."""
    return previous[:3] == current[:3] and all(
        old is None or new is None or old == new for old, new in zip(previous[3:], current[3:]))


def _refresh_path_ownership() -> set[str]:
    """Retire replaced identities everywhere; missing files retain established facts."""
    stale = set()
    uncertain = set()
    refreshed = {}
    paths = _required_paths.union(*_known_paths.values())
    for record in paths:
        _, _, source, resolved, device, inode = record
        try:
            current_path = os.path.realpath(source)
        except Exception:
            current_path = None
            uncertain.add(record)
        try:
            status = os.stat(source)
        except FileNotFoundError:
            status = None
        except Exception:
            status = None
            uncertain.add(record)
        if status is None and current_path is not None:
            try:
                current_path = _existing_realpath(source)
            except Exception:
                current_path = None
                uncertain.add(record)
        if ((resolved is not None and current_path is not None and current_path != resolved)
                or (device is not None and status is not None
                    and (status.st_dev, status.st_ino) != (device, inode))):
            stale.add(record)
        # Fill previously unavailable fields without losing a known value to
        # absence or uncertainty. Later replacements can retire the fuller fact.
        refreshed[record] = (*record[:3], current_path if current_path is not None else resolved,
                             status.st_dev if status is not None else device,
                             status.st_ino if status is not None else inode)
    retained = {refreshed[record] for record in _required_paths - stale}
    _required_paths.clear()
    _required_paths.update(retained)
    blocked = set()
    for root, history in _known_paths.items():
        if (history - stale) & uncertain:
            blocked.add(root)
        retained = {refreshed[record] for record in history - stale}
        history.clear()
        history.update(retained)
    return blocked


def _without_required_aliases(optional: set[str], required: set[str]) -> frozenset[str]:
    identities = {}
    aliases = {}
    blocked = required | _refresh_path_ownership()
    for root in optional | required:
        observed = set()
        absent = set()
        identities[root], established = _root_identities(root, observed, absent)
        history = _known_paths.setdefault(root, set())
        if observed:
            # Positive evidence of a different target retires the old alias's
            # association, even if an independent source failed. Other failed
            # sources and still-advertised absent locations keep their facts.
            replaced = {record[1] for record in observed}
            history.intersection_update({record for record in history
                                         if (not established and record[1] not in replaced)
                                         or any(_compatible_paths(record, current) for current in observed | absent)})
        for record in observed:
            compatible = {previous for previous in history if _compatible_paths(previous, record)}
            history.difference_update(compatible)
            for previous in compatible:
                record = (*record[:3], *(new if new is not None else old
                                        for old, new in zip(previous[3:], record[3:])))
            history.add(record)
        identities[root].update(_path_aliases(history))
        if root in required:
            _required_paths.update(history)
        if not established:
            blocked.add(root)

    # Only file evidence transfers persistent ownership. Live object/name
    # aliases and uncertain verdicts participate in this discovery alone.
    owned = _path_aliases(_required_paths)
    for root in optional | required:
        for record in _known_paths[root]:
            if _path_aliases((record,)) & owned:
                _required_paths.add(record)
                blocked.add(root)
        for identity in identities[root]:
            aliases.setdefault(identity, []).append(root)
    # Follow equivalence transitively: an optional declaration may connect a
    # required file to another spelling whose own file is already missing.
    pending = list(blocked & identities.keys())
    while pending:
        for identity in identities[pending.pop()]:
            for alias in aliases.pop(identity, ()):
                if alias not in blocked:
                    blocked.add(alias)
                    pending.append(alias)
    return frozenset(optional - blocked)


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
        if root and "." not in root and _import_root(root):
            roots.add(root)
    return roots


def _import_roots() -> dict[str, set[str]]:
    from importlib.metadata import distributions

    from packaging.utils import canonicalize_name

    roots: dict[str, set[str]] = {}
    for module, providers in metadata.packages_distributions().items():
        root = _import_root(module)
        if not root:
            continue
        for distribution in providers:
            roots.setdefault(canonicalize_name(distribution), set()).add(root)
    for distribution in distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        top_level = distribution.read_text("top_level.txt")
        if top_level and top_level.strip():
            roots.setdefault(name, set()).update(
                root for module in top_level.split() if (root := _import_root(module)))
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
def _optional_root_declarations(extras: str, required_extras: str):
    """Cache metadata declarations, never file ownership or a live verdict."""
    from packaging.requirements import Requirement

    imports = _import_roots()
    pending = [requirement for raw in metadata.requires("g6") or []
               if (requirement := Requirement(raw)).marker is None
               or any(requirement.marker.evaluate({"extra": extra}) for extra in ("", *required_extras.split(",")))]
    # Missing required metadata is an invalid base install, never an empty closure.
    required = _requirement_closure(pending, metadata.requires)
    base = {module for name in required for module in _distribution_roots(name, imports)}
    return _declared_roots(extras, imports), base


def optional_import_roots(extras: str, required_extras: str = "mcp") -> frozenset[str]:
    """Declared optional closure minus required ownership, refreshed for each discovery."""
    with _ownership_lock:
        optional, required = _optional_root_declarations(extras, required_extras)
        return _without_required_aliases(optional, required)


def _clear_optional_roots():
    with _ownership_lock:
        _optional_root_declarations.cache_clear()
        _known_paths.clear()
        _required_paths.clear()


# Explicit clearing resets declarations and process-wide file ownership together.
optional_import_roots.cache_clear = _clear_optional_roots


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
        except Exception:
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
