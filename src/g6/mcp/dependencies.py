# SPDX-License-Identifier: Apache-2.0
"""Local dependency metadata and chained import failures; no optional package is imported."""
from __future__ import annotations

import csv
from functools import cache
from importlib import _bootstrap_external, machinery, metadata, util
from itertools import count
import os
from pathlib import PurePosixPath
import sys
from threading import RLock


_EXCEPTION_WORK_LIMIT = 100_000
_ownership_lock = RLock()
_known_paths: dict[str, set[tuple]] = {}
_required_paths: set[tuple] = set()
_ownership_generation = 0
_declaration_epoch = 0
_evidence_order = count()
# Fixed evidence slots per source: latest event, direct stat, secondary
# confirmation and path-only observation. Every slot keeps the original event.
_ownership_observations: dict[str, dict[str, tuple]] = {}
# Completion tokens only: direct calls always refresh live ownership. Families
# uses these to bind its describe-only shortcut to this boundary's own commits.
_loaded_required_recordings: dict[str, tuple] = {}
_loaded_optional_recordings: dict[tuple[str, str], tuple] = {}


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


def _plain_text(value):
    """Copy string storage without dispatching a subclass's conversion hooks."""
    if not isinstance(value, str):
        raise TypeError("expected a string")
    value = str.__str__(value)
    if type(value) is not str:
        raise TypeError("expected an exact string")
    return value


def _location_text(value):
    value = os.fspath(value)
    if isinstance(value, bytes):
        value = os.fsdecode(bytes.__bytes__(value))
    return _plain_text(value)


def _stat_identity(path):
    status = os.stat(path)
    device, inode = status.st_dev, status.st_ino
    if type(device) is not int or type(inode) is not int:
        raise TypeError("expected exact integer stat identity")
    return device, inode


def _root_identities(root: str, paths: set | None = None,
                     absent_paths: set | None = None, *, locations: tuple | None = None,
                     live_paths: set | None = None, ordered_paths: list | None = None) -> tuple[set[tuple], bool]:
    """Retain established aliases and whether the lookup completed, without imports."""
    # Use the interpreter's actual FileFinder matching policy, including
    # PYTHONCASEOK and ignore-environment mode, rather than filesystem casing.
    def name_identity(name):
        name = _plain_text(name)
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
        if path is None:
            return
        path = _location_text(path)
        if not path or (source == "origin" and path in {"built-in", "frozen"}):
            return
        if kind == "namespace":
            source = (source, path)
        resolved = lookup(lambda: _plain_text(os.path.realpath(path)))
        if resolved is not None:
            identities.add((kind, resolved))

        def inode_identity():
            try:
                return _stat_identity(path), False
            except FileNotFoundError:
                return None, True  # Absence cannot replace an established identity.

        order = next(_evidence_order)
        result = lookup(inode_identity)
        status, absent = result if result is not None else (None, False)
        device = inode = None
        if status is not None:
            device, inode = status
            if inode:
                identities.add(("inode", device, inode))
        if live_paths is not None and (resolved is not None or status is not None):
            live_paths.add((kind, source, path, resolved, device, inode))
        if status is None and resolved is not None:
            # Keep live path matches independent, but never update ownership
            # from non-strict realpath's fallback for a missing/broken path.
            resolved = lookup(lambda: _plain_text(value) if (value := _existing_realpath(path)) is not None else None)
        # Each successful probe contributes even when its companion failed.
        records = absent_paths if absent else paths
        if records is not None and (absent or resolved is not None or status is not None):
            record = (kind, source, path, resolved, device, inode)
            records.add(record)
            if ordered_paths is not None:
                ordered_paths.append((order, record))

    def namespace_identities(locations, source):
        if locations is not None:
            for path in locations:
                lookup(lambda: path_identity(path, source, "namespace"))

    if locations is not None:
        # Startup supplies copied locations, never root names to rediscover or
        # history to promote. An empty snapshot contributes no ownership.
        for path, source, kind in locations:
            lookup(lambda: path_identity(path, source, kind))
        return identities, established

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


def _paths_match(left, right):
    """Stat identity decides a match; a spelling cannot contradict that evidence."""
    if left[4] is not None and right[4] is not None:
        if left[4:] != right[4:]:
            return False
        if left[5]:
            return True
    return left[0] == right[0] and left[3] is not None and left[3] == right[3]


def _compatible_paths(previous, current):
    """Same source with no conflicting successful identity probe."""
    return previous[:3] == current[:3] and all(
        old is None or new is None or old == new for old, new in zip(previous[3:], current[3:]))


def _path_refresh_observations(paths):
    """Probe a versioned copy of stored paths without holding the store lock."""
    probes = {}
    for record in paths:
        source = record[2]
        uncertain = False
        try:
            current_path = _plain_text(os.path.realpath(source))
        except Exception:
            current_path = None
            uncertain = True
        order = next(_evidence_order)
        absent = False
        direct = True
        try:
            status = _stat_identity(source)
        except FileNotFoundError:
            status = None
            absent = True
        except Exception:
            status = None
            uncertain = True
        if status is None and current_path is not None:
            try:
                current_path = _existing_realpath(source)
                if current_path is not None:
                    current_path = _plain_text(current_path)
            except Exception:
                current_path = None
                uncertain = True
        events = [(current_path, status, uncertain, order, direct, absent)]
        if record[4] is not None and status is None and current_path is not None:
            # The secondary probe has its own ticket and provenance. In
            # particular, never attach the direct probe's absence to its stat.
            order = next(_evidence_order)
            direct, absent, uncertain = False, False, False
            try:
                secondary = _stat_identity(current_path)
                after = _existing_realpath(source)
                if after is None or _plain_text(after) != current_path:
                    current_path = status = None
                    uncertain = True
                elif secondary == record[4:]:
                    # A secondary observation may confirm an unreadable source,
                    # but cannot retire it, including after direct not-found.
                    status = secondary
            except FileNotFoundError:
                status = None
            except Exception:
                status = None
                uncertain = True
            events.append((current_path, status, uncertain, order, direct, absent))
        probes[record] = events
    return probes


def _observe_current_sources(refresh, observations):
    """Recheck the recorded source when discovery contradicts its current resolution."""
    discovered = {record[2]: record[4:] for values in observations.values()
                  for _, record in values[5] if record[4] is not None}
    recheck = {record for record, events in refresh.items()
               for resolved, status, _, _, _, _ in events[-1:]
               if record[2] != resolved and resolved in discovered and discovered[resolved] != status}
    # A discovery through another spelling cannot retire this record. Obtain
    # direct evidence instead, outside the lock, from the recorded source itself.
    if recheck:
        for record, events in _path_refresh_observations(recheck).items():
            # Keep earlier facts under their own tickets, even if this recheck
            # is inconclusive. Its failure never re-dates a prior success.
            refresh[record].extend(events)


def _refresh_path_ownership(probes) -> set[str]:
    """Apply current-generation probes; missing files retain established facts."""
    stale = set()
    uncertain = set()
    refreshed = {}
    for record, (current_path, status, failed) in probes.items():
        _, _, source, resolved, device, inode = record
        if failed:
            uncertain.add(record)
        # A comparable stat identity decides whether this is still the same
        # file. Its resolved spelling may change, for example via a junction
        # retargeted to a hardlink. Otherwise keep the existing-path fallback.
        if device is not None:
            replaced = status is not None and status != (device, inode)
        else:
            replaced = resolved is not None and current_path is not None and current_path != resolved
        if replaced:
            stale.add(record)
        # Fill previously unavailable fields without losing a known value to
        # absence or uncertainty. Later replacements can retire the fuller fact.
        refreshed[record] = (*record[:3], current_path if current_path is not None else resolved,
                             status[0] if status is not None else device,
                             status[1] if status is not None else inode)
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


def _root_observations(roots: set[str]) -> dict:
    """Probe live import objects before taking the ownership-store lock."""
    observations = {}
    for root in roots:
        observed, absent, live = set(), set(), set()
        ordered = []
        identities, established = _root_identities(root, observed, absent, live_paths=live, ordered_paths=ordered)
        observations[root] = identities, established, observed, absent, live, ordered
    return observations


def _reconcile_observations(observations: dict, refresh: dict, *, loaded: bool = False, previous=()):
    """Retain accepted evidence and return ownership inputs in observation order."""
    # Attempts are snapshots, not replacement evidence. Keep every original
    # event, including its absence flag, before combining root histories. Only
    # the latest attempt supplies live object/name aliases and lookup certainty.
    batches = (*previous, (observations, refresh))
    refreshed = [(order, record[2], resolved, status, direct, absent)
                 for _, probes in batches for record, events in probes.items()
                 for resolved, status, _, order, direct, absent in events]
    observed = [(order, record[2], record[3], record[4:] if record[4] is not None else None,
                 True, record in values[3])
                for roots, _ in batches for values in roots.values() for order, record in values[5]]
    roots, probes = {}, {}
    for attempt, refresh in batches:
        for root, (identity, established, paths, absent, live, _) in attempt.items():
            old = roots.get(root)
            roots[root] = (identity, established, paths | old[2] if old else paths,
                           absent | old[3] if old else absent, live)
        for record, events in refresh.items():
            probes.setdefault(record, []).extend(events)
    # Keep bounded, independent evidence slots. A later failure/absence belongs
    # only in the latest-event slot; an older, uncommitted success can still
    # update the stat slot. Copy whole original events, never re-stamp a fact.
    for event in sorted((*observed, *refreshed), key=lambda value: value[0]):
        order, source, resolved, status, direct, absent = event
        history = _ownership_observations.setdefault(source, {})
        slots = ["latest"]
        if status is not None:
            slots.append("direct" if direct else "confirmation")
        elif direct and not absent and resolved is not None:
            slots.append("spelling")
        for slot in slots:
            previous = history.get(slot)
            if previous is None or previous[0] < order:
                history[slot] = event

    def decision(record):
        history = _ownership_observations.get(record[2], {})
        # Only another successful direct stat can supersede a direct stat.
        # Confirmation through another record's resolution has no such power.
        event = history.get("direct")
        if event is None:
            candidates = [event for slot, event in history.items()
                          if (slot == "confirmation" and event[3] == record[4:])
                          or (slot == "spelling" and record[4] is None)]
            event = max(candidates, key=lambda event: event[0],
                        default=(None, None, None, None, True, False))
        _, _, resolved, status, _, _ = event
        return resolved, status

    def current(record):
        resolved, status = decision(record)
        if status is not None:
            if record[4] is not None:
                if record[4:] != status:
                    return None
            elif record[3] is not None and resolved is not None and record[3] != resolved:
                return None
        elif record[4] is None and record[3] is not None and resolved is not None and record[3] != resolved:
            return None
        return (*record[:3], resolved if resolved is not None else record[3],
                *(status if status is not None else record[4:]))

    # A later live stat also refreshes an earlier retained owner at that spelling.
    # Conversely, a later refresh can exclude an obsolete startup observation.
    # Fallback may commit after another transaction added records that were not
    # in any snapshot. Project those current records through the same evidence;
    # never probe them or reacquire import objects while holding the lock.
    records = _required_paths.union(*_known_paths.values(), probes)
    combined = {record: (*decision(record), any(event[2] for event in probes.get(record, ())))
                for record in records}
    reconciled = {}
    for root, (identity, established, paths, absent, live) in roots.items():
        paths, absent, live = ({value for record in records if (value := current(record)) is not None}
                               for records in (paths, absent, live))
        identity = {value for value in identity if value[0] not in {"file", "namespace", "inode"}}
        identity.update(_path_aliases(live))
        reconciled[root] = identity, established, paths, absent, live
    return reconciled, combined


def _without_required_aliases(optional: set[str], required: set[str] | dict[str, set[tuple]]) -> frozenset[str]:
    if isinstance(required, dict):
        # Startup supplies only file records probed from its copied locations,
        # never root-name history or objects that can execute import code.
        for observed in required.values():
            _required_paths.update(observed)
        return frozenset()

    return frozenset(optional - required)


def _without_observed_aliases(optional: set[str] | frozenset[str], required: set[str],
                             observations: dict, refresh: dict, *, previous=()) -> frozenset[str]:
    """Merge already-probed discovery evidence while the ownership lock is held."""
    identities = {}
    live_paths = {}
    aliases = {}
    observations, refresh = _reconcile_observations(observations, refresh, previous=previous)
    blocked = required | _refresh_path_ownership(refresh)
    for root, (identity, established, observed, absent, live) in observations.items():
        identities[root] = identity
        live_paths[root] = live
        history = _known_paths.setdefault(root, set())
        if observed:
            # Positive evidence of a different target retires the old alias's
            # association, even if an independent source failed. A source
            # omitted from the live paths is absence, not a replacement.
            replaced = {record[1] for record in observed}
            history.intersection_update({record for record in history
                                         if record[1] not in replaced
                                         or any(_compatible_paths(record, current) for current in observed | absent)})
        for record in observed:
            compatible = {previous for previous in history if _compatible_paths(previous, record)}
            history.difference_update(compatible)
            for previous in compatible:
                record = (*record[:3], *(new if new is not None else old
                                        for old, new in zip(previous[3:], record[3:])))
            history.add(record)
        identities[root].update(_path_aliases(history))
        live_paths[root].update(history)
        if root in required:
            _required_paths.update(history)
        if not established:
            blocked.add(root)

    # Only file evidence transfers persistent ownership. Live object/name
    # aliases and uncertain verdicts participate in this discovery alone.
    owned = tuple(_required_paths)
    for root in optional | required:
        for record in _known_paths[root]:
            if any(_paths_match(record, owner) for owner in owned):
                _required_paths.add(record)
                blocked.add(root)
        for identity in identities[root]:
            aliases.setdefault(identity, []).append(root)
    # Follow equivalence transitively: an optional declaration may connect a
    # required file to another spelling whose own file is already missing.
    pending = list(blocked & identities.keys())
    while pending:
        root = pending.pop()
        for identity in identities[root]:
            file_alias = identity[0] in {"file", "namespace", "inode"}
            # A rejected path match must remain available to a different
            # required file with a compatible stat identity.
            candidates = aliases.get(identity, ()) if file_alias else aliases.pop(identity, ())
            for alias in candidates:
                if alias not in blocked and (not file_alias or any(
                    _paths_match(left, right) for left in live_paths[root] for right in live_paths[alias]
                )):
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
def _optional_root_declarations(extras: str, required_extras: str, epoch: int = 0):
    """Cache metadata declarations, never file ownership or a live verdict."""
    # The epoch key isolates an old computation that finishes after cache_clear.
    from packaging.requirements import Requirement

    imports = _import_roots()
    pending = [requirement for raw in metadata.requires("g6") or []
               if (requirement := Requirement(raw)).marker is None
               or any(requirement.marker.evaluate({"extra": extra}) for extra in ("", *required_extras.split(",")))]
    # Missing required metadata is an invalid base install, never an empty closure.
    required = _requirement_closure(pending, metadata.requires)
    base = {module for name in required for module in _distribution_roots(name, imports)}
    return ({_plain_text(root) for root in _declared_roots(extras, imports)},
            {_plain_text(root) for root in base})


def _ownership_snapshot():
    with _ownership_lock:
        return (_ownership_generation, _declaration_epoch,
                _required_paths.union(*_known_paths.values()))


def _ownership_epoch() -> int:
    """Read the explicit-clear epoch without running any probes under the lock."""
    with _ownership_lock:
        return _declaration_epoch


def _ownership_state():
    return (_required_paths.copy(), {root: history.copy() for root, history in _known_paths.items()},
            {source: {slot: event[2:] for slot, event in history.items()}
             for source, history in _ownership_observations.items()})


def _advance_ownership_generation(before):
    global _ownership_generation
    # One atomic merge may add, retire and transfer ownership. Any observable
    # store change requires a fresh snapshot, without discarding observations.
    # Advancing an unchanged observation's ticket alone is not an ownership
    # transaction. Reconciliation still reads that latest ticket under the lock.
    if before != _ownership_state():
        _ownership_generation += 1


def optional_import_roots(extras: str, required_extras: str = "mcp", *,
                          _recording_key: tuple | None = None) -> frozenset[str]:
    """Refresh optional ownership on every call, optionally tracking its completion."""
    # Metadata, finders and module/spec attributes can execute import code.
    # None may run while another importer waits for our ownership store.
    extras, required_extras = _plain_text(extras), _plain_text(required_extras)
    with _ownership_lock:
        # A rejected declaration or a contended pass cannot reuse an earlier
        # completion token, even when no ownership state ultimately changed.
        _loaded_optional_recordings.pop((extras, required_extras), None)
    attempts = []
    attempt_epoch = None
    for _ in range(2):
        generation, epoch, paths = _ownership_snapshot()
        if epoch != attempt_epoch:
            attempts.clear()  # Only an explicit clear supersedes gathered facts.
            attempt_epoch = epoch
        optional, required = _optional_root_declarations(extras, required_extras, epoch)
        try:
            optional = {_plain_text(root) for root in optional}
            required = {_plain_text(root) for root in required}
        except Exception:
            return frozenset()  # An unusable declaration cannot establish optionality.
        refresh = _path_refresh_observations(paths)
        observations = _root_observations(optional | required)
        _observe_current_sources(refresh, observations)
        attempts.append((observations, refresh))
        with _ownership_lock:
            if generation != _ownership_generation:
                continue  # Retry once, including declarations invalidated by a clear.
            before = _ownership_state()
            # Only startup recording enters the seam with an empty optional side.
            if optional:
                optional = _without_required_aliases(optional, required)
            result = _without_observed_aliases(optional, required, observations, refresh, previous=attempts[:-1])
            _advance_ownership_generation(before)
            if (_recording_key is not None and len(attempts) == 1
                    and _recording_key[:2] == (epoch, generation)
                    and _loaded_required_recordings.get(required_extras) == _recording_key):
                # This boundary's own optional merge may advance the generation.
                # Bind completion to that committed generation, but never adopt
                # a competing transaction's generation after a retry.
                key = (epoch, _ownership_generation, _recording_key[2])
                _loaded_required_recordings[required_extras] = key
                _loaded_optional_recordings[extras, required_extras] = key
            return result
    # Sustained contention supplies no safe optional verdict. The observations
    # already taken still join current evidence unless an explicit clear won.
    with _ownership_lock:
        if epoch == _declaration_epoch:
            before = _ownership_state()
            if optional:
                optional = _without_required_aliases(optional, required)
            _without_observed_aliases(optional, required, observations, refresh, previous=attempts[:-1])
            _advance_ownership_generation(before)
    return frozenset()


def _loaded_required_modules(required: set[str]) -> tuple[dict, frozenset | None]:
    """Copy names, identities and file values without filesystem or metadata probes."""
    prefixes = tuple(root + "." for root in required)
    loaded, fingerprint = {}, set()
    cacheable = True
    for name, module in sys.modules.copy().items():
        if not isinstance(name, str):
            continue
        root = _plain_text(name)
        if root not in required and not root.startswith(prefixes):
            continue
        if module is not None:
            loaded[root] = module
        try:
            file = getattr(module, "__file__", None)
            # Avoid equality/string hooks under the ownership lock. Exotic or
            # unreadable file values retain full recording on every call.
            if file is not None and type(file) is not str:
                cacheable = False
                continue
            fingerprint.add((root, id(module), file))
        except Exception:
            cacheable = False
    return loaded, frozenset(fingerprint) if cacheable else None


def _remember_loaded_recording(required_extras, epoch, fingerprint):
    """Publish only after reconciliation, while holding the ownership lock."""
    if fingerprint is not None:
        key = epoch, _ownership_generation, fingerprint
        _loaded_required_recordings[required_extras] = key
        return key
    _loaded_required_recordings.pop(required_extras, None)
    return None


def _loaded_required_recording_key(required_extras: str = "mcp") -> tuple | None:
    """Cheap candidate key for the validated-describe boundary; never a live verdict."""
    required_extras = _plain_text(required_extras)
    with _ownership_lock:
        generation, epoch = _ownership_generation, _declaration_epoch
    _, required = _optional_root_declarations("", required_extras, epoch)
    try:
        required = {_plain_text(root) for root in required}
    except Exception:
        return None
    _, fingerprint = _loaded_required_modules(required)
    with _ownership_lock:
        if (fingerprint is not None and epoch == _declaration_epoch
                and generation == _ownership_generation):
            return epoch, generation, fingerprint
    return None


def record_loaded_required_imports(required_extras: str = "mcp") -> tuple | None:
    """Fully refresh a load's required modules and descendants on every direct call."""
    required_extras = _plain_text(required_extras)
    with _ownership_lock:
        epoch = _declaration_epoch
    _, required = _optional_root_declarations("", required_extras, epoch)
    try:
        required = {_plain_text(root) for root in required}
    except Exception:
        return
    loaded, fingerprint = _loaded_required_modules(required)
    generation, current_epoch, paths = _ownership_snapshot()
    if epoch != current_epoch:
        return  # An explicit clear supersedes the copied declaration/modules.
    locations = {root: _loaded_locations(module) for root, module in loaded.items()}
    observations = {}
    for root, copied in locations.items():
        observed, absent, live, ordered = set(), set(), set(), []
        identity, established = _root_identities(root, observed, absent, locations=copied,
                                                live_paths=live, ordered_paths=ordered)
        observations[root] = identity, established, observed, absent, live, ordered
    attempts = []
    for _ in range(2):
        refresh = _path_refresh_observations(paths)
        with _ownership_lock:
            if epoch != _declaration_epoch:
                return  # Only an explicit clear supersedes startup's copied evidence.
            if generation == _ownership_generation:
                before = _ownership_state()
                reconciled, refresh = _reconcile_observations(observations, refresh, loaded=True, previous=attempts)
                _refresh_path_ownership(refresh)
                _without_required_aliases(set(), {root: values[2] for root, values in reconciled.items()})
                _advance_ownership_generation(before)
                return _remember_loaded_recording(required_extras, epoch, fingerprint)
        attempts.append(({}, refresh))
        generation, current_epoch, paths = _ownership_snapshot()
        if epoch != current_epoch:
            return
    # Under sustained contention, reconcile loaded and refresh evidence with
    # current observations, keeping original tickets and copied locations.
    with _ownership_lock:
        if epoch == _declaration_epoch:
            before = _ownership_state()
            reconciled, refresh = _reconcile_observations(observations, {}, loaded=True, previous=attempts)
            _refresh_path_ownership(refresh)
            _without_required_aliases(set(), {root: values[2] for root, values in reconciled.items()})
            _advance_ownership_generation(before)
            # A fallback retained evidence but did not obtain a stable recording.
            # Leave the next successful-load boundary eligible for a full pass.
            _loaded_required_recordings.pop(required_extras, None)


def _loaded_locations(module) -> tuple:
    """Copy a loaded object's own file and namespace locations before probing."""
    def attribute(obj, name):
        try:
            return getattr(obj, name, None)
        except Exception:
            return None

    locations = []

    def location(path, source, kind):
        try:
            spelling = _location_text(path)
            if spelling and not (source == "origin" and spelling in {"built-in", "frozen"}):
                locations.append((spelling, source, kind))
        except Exception:
            pass  # One rejected location must not suppress independent evidence.

    spec = attribute(module, "__spec__")
    for source, path in (("loaded-file", attribute(module, "__file__")),
                         ("origin", attribute(spec, "origin"))):
        location(path, source, "file")
    for source, paths in (("loaded-locations", attribute(module, "__path__")),
                          ("spec-locations", attribute(spec, "submodule_search_locations"))):
        try:
            if paths is not None:
                for path in paths:
                    location(path, source, "namespace")
        except Exception:
            pass
    return tuple(locations)


def _clear_optional_roots():
    global _ownership_generation, _declaration_epoch
    with _ownership_lock:
        _ownership_generation += 1
        _declaration_epoch += 1
        _optional_root_declarations.cache_clear()
        _known_paths.clear()
        _required_paths.clear()
        _ownership_observations.clear()
        _loaded_required_recordings.clear()
        _loaded_optional_recordings.clear()


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
