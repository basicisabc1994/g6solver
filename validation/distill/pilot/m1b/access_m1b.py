"""Validate allocation membership before opening any acquisition material."""
from __future__ import annotations

from . import allocation_m1b
from ._mine_io import entry_id


def acquisition_bug(manifest: dict, bug_id: str) -> dict:
    """Return a copy of acquisition commit metadata, or refuse before source IO."""
    errors = allocation_m1b.check(manifest)
    if errors:
        raise ValueError("invalid allocation: " + "; ".join(errors))
    for role, group in [*manifest["roles"].items(), ("unused", manifest["unused"])]:
        for bug in group["bugs"]:
            if entry_id(bug) == bug_id:
                if role != "acquisition":
                    raise ValueError("acquisition access refused: role=" + role)
                return dict(bug)
    raise ValueError("acquisition access refused: role=unallocated")


def selected(manifest: dict, requested: list[str] | None = None) -> list[str]:
    errors = allocation_m1b.check(manifest)
    if errors:
        raise ValueError("invalid allocation: " + "; ".join(errors))
    ids = list(requested) if requested is not None else [entry_id(b) for b in manifest["roles"]["acquisition"]["bugs"]]
    for bug_id in ids:
        acquisition_bug(manifest, bug_id)
    return ids
