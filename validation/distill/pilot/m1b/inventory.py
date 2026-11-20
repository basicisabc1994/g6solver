"""Resume a body-free static inventory of a pool, then apply counts and chooser.

Usage: python -m validation.distill.pilot.m1b.inventory --pool POOL
    --environment ENVIRONMENT --cache CACHE --output OUTPUT [--exclude IDS]

OUTPUT doubles as an atomic checkpoint. It stores per-bug public fit records and
single-record count summaries, never source or local paths. Resume is bound to the
pool entries and environment. A2 exclusions can change between runs. No sealed
cohort file is accepted by this command. A failed read leaves complete=false and
no selection, with completed rows available for the next invocation.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from . import chooser, counts, fit, gitread
from ._mine_io import (atomic_json, cohort_ids, digest, entry_id, external_path, local_path,
                       pool_entries, pool_ids, read_json, repository_id)


def _public_record(record: fit.FitRecord) -> dict:
    public = record.public()
    return {key: public[key] for key in ("repository", "bug_id", "failed_rules", "flags")}


def _report(completed: dict, entries: list[dict], excluded: set[str], *, complete: bool,
            exists: bool, prepared: bool) -> dict:
    records, summaries, prepared_summaries = [], [], []
    removed = set()
    for entry in entries:
        identity = entry_id(entry)
        name = repository_id(entry["repository"])
        if name in excluded:
            removed.add(name)
            continue
        if identity in completed:
            row = completed[identity]
            records.append(row["record"])
            summaries.append((name, row["summary"]))
            if prepared:
                prepared_summaries.append((name, row["prepared_summary"]))

    def aggregate(summaries):
        repositories = {key: set() for key in counts.CONFIGURATIONS}
        result = {"exclusions": {key: [] for key in ("V1", "V2", "V3")},
                  "configuration_exclusions": {key: [] for key in counts.CONFIGURATIONS}, "flags": []}
        for name, summary in summaries:
            for key, value in summary["counts"].items():
                if value:
                    repositories[key].add(name)
            for category in ("exclusions", "configuration_exclusions"):
                for key, value in summary[category].items():
                    result[category][key].extend(value)
            result["flags"].extend(summary["flags"])
        for category in ("exclusions", "configuration_exclusions"):
            for rows in result[category].values():
                rows.sort(key=lambda row: (row["repository"], row["bug_id"]))
        result["flags"].sort(key=lambda row: (row["repository"], row["bug_id"]))
        result["counts"] = {key: len(value) for key, value in repositories.items()}
        return result

    result = aggregate(summaries)
    result.update(records=sorted(records, key=lambda r: (r["repository"], r["bug_id"])),
                  a2_exclusions=sorted(removed), complete=complete, selection=None)
    prepared_count = None
    if prepared:
        result["prepared_counts"] = aggregate(prepared_summaries)["counts"]
        prepared_count = result["prepared_counts"]["K_3"]
    if complete:
        result["selection"] = chooser.choose(result["counts"], interpreter_exists=exists,
                                               prepared_count=prepared_count).public()
    return result


def run(*, pool: Path, environment: Path, cache: Path, output: Path, exclude: Path | None = None,
        repository_root: Path | None = None) -> dict:
    pool, environment = local_path(pool), local_path(environment)
    output, cache = (external_path(path, repository_root) for path in (output, cache))
    exclude = local_path(exclude) if exclude is not None else None
    inputs = {pool, environment, *([exclude] if exclude else [])}
    if output in inputs or cache == output or any(path.is_relative_to(cache) for path in inputs | {output}):
        raise ValueError("inventory paths overlap")
    pool_data = read_json(pool)
    entries = pool_entries(pool_data)
    env_data = read_json(environment)
    env = fit._environment(env_data)
    exists = env_data.get("interpreter_exists") is True
    prepared = fit._environment(env_data["prepared_environment"]) if exists and "prepared_environment" in env_data else None
    excluded = cohort_ids(read_json(exclude)) if exclude else set()
    a2_exclusions = sorted(excluded & pool_ids(pool_data))
    binding = digest({"entries": entries, "environment": env_data, "version": 1})
    previous = read_json(output) if output.exists() else None
    completed = {}
    if previous is not None:
        if previous["schema"] != 1 or previous["binding"] != binding:
            raise ValueError("inventory checkpoint inputs differ")
        completed = previous["completed"]

    def save(complete):
        report = _report(completed, entries, excluded, complete=complete, exists=exists, prepared=prepared is not None)
        report["a2_exclusions"] = a2_exclusions
        # Excluded candidates are removed from the checkpoint as well as the report.
        kept = {entry_id(e) for e in entries if repository_id(e["repository"]) not in excluded}
        state = {**report, "schema": 1, "binding": binding,
                 "completed": {key: value for key, value in completed.items() if key in kept}}
        atomic_json(output, state)
        return state

    save(False)
    for entry in entries:
        identity = entry_id(entry)
        if repository_id(entry["repository"]) in excluded or identity in completed:
            continue
        candidate = gitread.read_candidate(entry, cache, repository_root=repository_root)
        record = fit.assess(candidate, env)
        row = {"record": _public_record(record), "summary": counts.summarize([record])}
        if prepared is not None:
            row["prepared_summary"] = counts.summarize([record], prepared_environment=prepared)
        completed[identity] = row
        save(False)
    return save(True)


def main(argv=None, *, repository_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pool", "environment", "cache", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--exclude", type=Path)
    args = parser.parse_args(argv)
    try:
        run(**vars(args), repository_root=repository_root)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, subprocess.SubprocessError):
        parser.exit(2, "static inventory input or read refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
