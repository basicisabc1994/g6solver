"""Draw the registered M1b roles from a complete, body-free inventory.

Usage: --write INVENTORY POOL ENVIRONMENT MANIFEST,
or --check MANIFEST INVENTORY POOL ENVIRONMENT.
The inventory, pool and environment must come from outside the allocation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import chooser
from .environment import from_json
from ._mine_io import atomic_json, digest, entry_id, local_path, pool_entries, read_json, repository_id

LABEL = "g6-pilot-m1b-allocation-v1"
ROLES = {"full": (("acquisition", 5), ("admission", 5), ("monitor", 3), ("reuse", 2)),
         "reduced": (("acquisition", 4), ("admission", 4), ("monitor", 2))}
CONFIGURATIONS = ("K_0", "K_1", "K_2", "K_3")
ENTRY_KEYS = ("repository", "pr", "merge_commit", "parent")


def allocate(binding: str, repositories: list[str], tier: str) -> dict[str, list[str]]:
    """Fill roles in hash order, retaining sorted, unallocated repositories."""
    if (not isinstance(binding, str) or len(binding) != 64
            or any(c not in "0123456789abcdef" for c in binding)):
        raise ValueError("inventory binding refused")
    if tier not in ROLES or chooser.tier(len(repositories)) != tier:
        raise ValueError("repository count differs from tier")
    if repositories != sorted(set(repositories)) or any(repository_id(r) != r for r in repositories):
        raise ValueError("repository list refused")
    ordered = sorted(repositories, key=lambda repo: hashlib.sha256(f"{LABEL}|{binding}|{repo}".encode()).hexdigest())
    groups, index = {}, 0
    for role, size in ROLES[tier]:
        groups[role] = sorted(ordered[index:index + size])
        index += size
    groups["unused"] = sorted(ordered[index:])
    return groups


def _manifest(binding, configuration, tier, repositories, bugs):
    if configuration not in CONFIGURATIONS:
        raise ValueError("selected configuration refused")
    draw = allocate(binding, repositories, tier)
    if not isinstance(bugs, list) or not bugs:
        raise ValueError("bug list refused")
    ids = []
    for bug in bugs:
        if set(bug) != set(ENTRY_KEYS) or repository_id(bug["repository"]) != bug["repository"]:
            raise ValueError("bug entry refused")
        ids.append(entry_id(bug))
    if ids != sorted(set(ids)) or sorted({bug["repository"] for bug in bugs}) != repositories:
        raise ValueError("bugs differ from repository list")
    inputs = {"binding": binding, "configuration": configuration, "tier": tier,
              "repositories": repositories, "bugs": bugs}
    groups = {role: {"repositories": repos, "bugs": [b for b in bugs if b["repository"] in repos]}
              for role, repos in draw.items()}
    return {"schema": 1, "label": LABEL, **inputs, "inputs_sha256": digest(inputs),
            "roles": {role: groups[role] for role, _ in ROLES[tier]}, "unused": groups["unused"]}


def build_manifest(inventory: dict, pool: dict, environment: dict | None = None) -> dict:
    """Recount qualifying completed summaries, never infer eligibility from records."""
    if environment is None:
        raise ValueError("external environment required")
    entries_list = pool_entries(pool)
    binding = digest({"entries": entries_list, "environment": environment, "version": 1})
    if inventory.get("binding") != binding:
        raise ValueError("inventory binding differs from pool or environment")
    from_json(environment)
    exists = environment.get("interpreter_exists") is True
    prepared = exists and "prepared_environment" in environment
    if prepared:
        from_json(environment["prepared_environment"])
    if inventory.get("complete") is not True or not isinstance(inventory.get("selection"), dict):
        raise ValueError("complete inventory with selection required")
    selection = inventory["selection"]
    prepared_count = inventory["prepared_counts"]["K_3"] if prepared else None
    chosen = chooser.choose(inventory["counts"], interpreter_exists=exists,
                            prepared_count=prepared_count).public()
    if _canonical(selection) != _canonical(chosen):
        raise ValueError("selection differs from chooser")
    configuration, tier = selection["configuration"], selection["tier"]
    if configuration not in CONFIGURATIONS or tier not in ROLES:
        raise ValueError("selection does not permit a draw")
    entries = {entry_id(e): e for e in entries_list}
    completed = inventory["completed"]
    excluded = {repository_id(r) for r in inventory.get("a2_exclusions", [])}
    expected = {key for key, entry in entries.items() if repository_id(entry["repository"]) not in excluded}
    if set(completed) != expected:
        raise ValueError("completed rows differ from pool")
    bugs = []
    for identity in sorted(completed):
        row = completed[identity]
        summary = row["prepared_summary"] if configuration == "K_3" else row["summary"]
        count = summary["counts"][configuration]
        if type(count) is not int or count not in (0, 1):
            raise ValueError("single-entry summary count refused")
        if count:
            entry = entries[identity]
            bugs.append({key: repository_id(entry[key]) if key == "repository" else entry[key] for key in ENTRY_KEYS})
    repositories = sorted({b["repository"] for b in bugs})
    count = len(repositories)
    aggregate = inventory.get("prepared_counts") if configuration == "K_3" else inventory["counts"]
    if (type(selection["count"]) is not int or selection["count"] != count
            or not isinstance(aggregate, dict) or type(aggregate.get(configuration)) is not int
            or aggregate[configuration] != count):
        raise ValueError("selected count differs from completed summaries")
    manifest = _manifest(binding, configuration, tier, repositories, bugs)
    if configuration == "K_3":
        manifest["prepared_interpreter"] = environment.get("prepared_interpreter")
    return manifest


def manifest_from_inventory(inventory: Path, pool: Path, environment: Path) -> dict:
    return build_manifest(read_json(inventory), read_json(pool), read_json(environment))


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def check(manifest: dict, inventory: dict | None = None, pool: dict | None = None,
          environment: dict | None = None) -> list[str]:
    """Rebuild the allocation from external inputs, including the pool's commits."""
    if inventory is None or pool is None or environment is None:
        return ["external inventory, pool and environment required"]
    try:
        if type(manifest["schema"]) is not int or manifest["schema"] != 1:
            return ["allocation schema refused"]
        expected = build_manifest(inventory, pool, environment)
        entries = {entry_id(entry): entry for entry in pool_entries(pool)}
        for bug in manifest["bugs"]:
            entry = entries[entry_id(bug)]
            if any(bug[key] != entry[key] for key in ("parent", "merge_commit")):
                return ["allocation commits differ from pool"]
        if _canonical(manifest) != _canonical(expected):
            return ["allocation differs from external inputs or draw"]
        return []
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["malformed allocation inputs"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", nargs=4, metavar=("INVENTORY", "POOL", "ENVIRONMENT", "MANIFEST"))
    group.add_argument("--check", nargs=4, metavar=("MANIFEST", "INVENTORY", "POOL", "ENVIRONMENT"))
    args = parser.parse_args(argv)
    try:
        if args.write:
            inventory, pool, environment, output = (local_path(Path(p)) for p in args.write)
            if output in {inventory, pool, environment}:
                raise ValueError("allocation paths overlap")
            manifest = manifest_from_inventory(inventory, pool, environment)
            atomic_json(output, manifest)
            print("ok")
            return 0
        errors = check(*(read_json(Path(path)) for path in args.check))
        print("\n".join(errors) or "ok")
        return int(bool(errors))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        parser.exit(2, "M1b allocation input or output refused\n")


if __name__ == "__main__":
    raise SystemExit(main())
