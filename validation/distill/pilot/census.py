"""Count the oracle's mutation sample for each acquisition fix without executing suites."""
from __future__ import annotations

import json
import time

from g6.core.ledger import digest
from g6.distill.held_out import oracle

from . import _material, access, allocation, preflight


def sample(fixed: str, lines: tuple[int, ...], policy: oracle.OraclePolicy) -> dict[str, str]:
    candidates = oracle.mutation_check.mutants(fixed, limit=policy.max_mutants, lines=lines,
                                              deadline=time.monotonic() + policy.wall_s)
    return {digest(m.source): m.source for m in candidates if m.source != fixed}


def inspect_bug(manifest, cohort_path, bug_id, *, runner=preflight.docker_runner) -> dict:
    bug = access.load_bug(manifest, cohort_path, bug_id)
    row = {"bug_id": bug_id, "repository": allocation.repository_of(bug_id), "changed_files": [],
           "changed_line_count": 0, "distinct_mutant_count": 0, "reaches_min_mutants": False,
           "reason_code": "ok", "files": []}
    policy = oracle.OraclePolicy()
    try:
        material = _material.extract(bug, runner)
        row["changed_files"] = _material.changed_files(material)
        targets = _material.targets(material)
        if not targets:
            row["reason_code"] = "no_python_fix"
            return row
        for path in targets:
            lines = _material.changed_lines(material, path)
            fixed = material["fixed"].get(path)
            if not lines or fixed is None:
                row["files"].append({"path": path, "changed_lines": list(lines), "distinct_mutant_count": 0,
                                     "reaches_min_mutants": False, "reason_code": "empty_changed_lines"})
                continue
            mutants = sample(fixed, lines, policy)
            row["files"].append({"path": path, "changed_lines": list(lines), "distinct_mutant_count": len(mutants),
                                 "reaches_min_mutants": len(mutants) >= policy.min_mutants, "reason_code": "ok"})
        row["changed_line_count"] = sum(len(f["changed_lines"]) for f in row["files"])
        # A CaseRecord has one target: counts from different files cannot fill one sample.
        row["distinct_mutant_count"] = max((f["distinct_mutant_count"] for f in row["files"]), default=0)
        row["reaches_min_mutants"] = any(f["reaches_min_mutants"] for f in row["files"])
        if not row["changed_line_count"]:
            row["reason_code"] = "empty_changed_lines"
    except _material.MaterialError as exc:
        row["reason_code"] = str(exc)
        row["changed_line_count"] = None
        row["distinct_mutant_count"] = None
    except oracle.mutation_check.Unfinished:
        row["reason_code"] = "mutation_sample_unfinished"
        row["changed_line_count"] = None
        row["distinct_mutant_count"] = None
    return row


def main(argv=None, *, runner=preflight.docker_runner) -> int:
    parser = access.arguments(__doc__)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        ids = access.selected(manifest, args.bug)
        rows = [inspect_bug(manifest, args.cohort, bug, runner=runner) for bug in ids]
        repositories = {}
        for row in rows:
            group = repositories.setdefault(row["repository"], {"bugs": 0, "reaches_min_mutants": 0,
                                                                  "changed_line_count": 0,
                                                                  "distinct_mutant_count": 0, "reason_codes": {}})
            group["bugs"] += 1
            group["reaches_min_mutants"] += int(row["reaches_min_mutants"])
            for key in ("changed_line_count", "distinct_mutant_count"):
                group[key] = group[key] + row[key] if group[key] is not None and row[key] is not None else None
            codes = group["reason_codes"]
            codes[row["reason_code"]] = codes.get(row["reason_code"], 0) + 1
        access.write_json(args.output, {"bugs": rows, "repositories": repositories,
                                       "min_mutants": oracle.OraclePolicy().min_mutants,
                                       "max_mutants": oracle.OraclePolicy().max_mutants,
                                       "count_scope": "largest single Python source target per bug; per-file counts included; "
                                                      "repository counts sum bug samples without pooling eligibility"})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0 if all(row["reason_code"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
