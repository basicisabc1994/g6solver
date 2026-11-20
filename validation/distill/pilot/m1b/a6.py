"""Compare acquisition-only static fit with saved run-2 funnel observations.

Only identifiers, rule codes, flags, counts and decisions are published. Extraction
uses the funnel's own functions; no suites or candidate imports are performed.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
import time
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath

from validation.distill.pilot import _material, access, allocation, funnel, preflight
from validation.worked_examples.selection import is_test

from . import chooser, counts, environment, fit

REPOSITORY = Path(__file__).resolve().parents[4]
REASONS = frozenset({
    "ok", "unrun_budget", "case_record_invalid", "io_unavailable", "invalid_material",
    "suite_runner_refused", "G6_E_ISOLATION_UNAVAILABLE", "source_not_utf8",
    "operator_not_applicable", "fixed_suite_did_not_pass", "control_base_passes", "control_unsettled",
    "insufficient_mutant_sample", "mutation_sample_unfinished", "missing_module",
    "source_loading_failure", "no_tests_collected",
}) | funnel.EXTRACT_REASONS | funnel.SHAPE_REASONS | frozenset(funnel.EXTRACT_MESSAGES.values())


def _observations(manifest, ids, body):
    """Validate identity before using observations; do not export arbitrary text."""
    if not isinstance(body, dict) or not isinstance(body.get("bugs"), list):
        raise ValueError("invalid_funnel")
    rows, seen = [], set()
    for row in body["bugs"]:
        bug = row["bug_id"]
        access.acquisition_bug(manifest, bug)
        arm, variant, stage = row["arm"], row.get("variant"), row["stage"]
        if bug not in ids or arm not in funnel.ARMS or stage not in (*funnel.STAGES, "unrun"):
            raise ValueError("invalid_funnel")
        if variant is not None and variant not in ("maintainer", *funnel.VARIANTS):
            raise ValueError("invalid_funnel")
        key = bug, arm, variant
        if key in seen:
            raise ValueError("duplicate_funnel_row")
        seen.add(key)
        reason = row["reason"]
        # The saved oracle may add codes beyond the funnel's precheck vocabulary.
        # Preserve bounded code tokens, never free-form diagnostics or bodies.
        code = isinstance(reason, str) and re.fullmatch(r"(?:[a-z][a-z0-9_]{0,95}|G6_E_[A-Z0-9_]{1,80})", reason)
        rows.append({"bug_id": bug, "arm": arm, "variant": variant, "last_stage": stage,
                     "reason": reason if code else "unrecognized_reason"})
    if {(r["bug_id"], r["arm"]) for r in rows} != {(bug, arm) for bug in ids for arm in funnel.ARMS}:
        raise ValueError("incomplete_funnel")
    return sorted(rows, key=lambda r: (ids.index(r["bug_id"]), funnel.ARMS.index(r["arm"]), r["variant"] or ""))


def _safe_path(name):
    path = PurePosixPath(name)
    # Validate before creating anything, including Windows drive and device spellings.
    if (not name or path.as_posix() != name or path.is_absolute() or "\\" in name
            or PureWindowsPath(name).drive or PureWindowsPath(name).is_reserved()
            or any(p in {".", ".."} or p.startswith(".") or ":" in p or p.endswith((" ", "."))
                   or PureWindowsPath(p).is_reserved()
                   for p in path.parts)):
        raise ValueError("invalid_material")
    return path


def _candidate(material, bug, parent):
    changed = _material.changed_files(material)
    for name in {*changed, *material["base"], *material["fixed"]}:
        _safe_path(name)
    program = {p: text for p, text in material["fixed"].items() if p.endswith(".py") and not is_test(p)}
    for name in changed:
        if name.endswith(".py") and not is_test(name):
            if name in material["base"]:
                program[name] = material["base"][name]
            else:
                program.pop(name, None)
    for name, text in program.items():
        path = parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    def side(key, path):
        return material[key][path].encode("utf-8") if path in material[key] else None
    return fit.Candidate(allocation.repository_of(bug), bug, parent,
                         {p: fit.ChangedFile(side("base", p), side("fixed", p)) for p in changed if p.endswith(".py")},
                         {p: s.encode("utf-8") for p, s in material["fixed"].items() if is_test(p)})


def _failed(bug, env, parent, reason):
    """Keep extraction attrition in both the fit ledger and every denominator."""
    candidate = fit.Candidate(allocation.repository_of(bug), bug, parent, {}, {})
    return fit.FitRecord(repository=candidate.repository, bug_id=bug, failed_rules=(reason,), flags=(),
                         mutant_count=0, variants=(), environment=env, candidate=candidate, program={}, tests={},
                         helpers={}, target="", changed_lines=(), known_wrong="", repository_modules=frozenset())


def run_a6(manifest, cohort, funnel_report, env, output, *, runner=preflight.docker_runner,
           repository_root: Path | None = None):
    """Extract acquisition material once per bug, assess statically, and join codes."""
    ids = access.selected(manifest, None)
    for bug in ids:
        access.acquisition_bug(manifest, bug)
    cohort, output = Path(cohort), Path(output).absolute()
    if any(p.name.lower().startswith("final-") for p in (cohort, *cohort.parents)):
        raise ValueError("the final cohort is sealed; only the owner opens it")
    repository = (repository_root or REPOSITORY).resolve()
    if output.resolve().is_relative_to(repository):
        raise ValueError("output_inside_repository")
    if any(p.is_symlink() or p.is_junction() for p in (output, *output.parents)):
        raise ValueError("linked_output_refused")
    if not output.parent.is_dir():
        raise ValueError("output_parent_required")
    observations = _observations(manifest, ids, funnel_report)
    records, extraction_reasons = [], Counter()
    workspace = REPOSITORY / ".build"
    workspace.mkdir(exist_ok=True)
    budget = funnel._Budget(time.monotonic)
    for bug in ids:
        # A fresh ordinary directory contains data only, never imported or executed.
        with tempfile.TemporaryDirectory(prefix="a6-parent-", dir=workspace) as folder:
            parent = Path(folder)
            try:
                material, _ = funnel._extract(manifest, cohort, bug, runner, budget)
                record = fit.assess(_candidate(material, bug, parent), env)
            except (funnel._Stop, OSError, ValueError, KeyError, TypeError) as exc:
                reason = funnel._reason(exc)
                if reason not in REASONS:
                    reason = "invalid_material"
                record = _failed(bug, env, parent, reason)
                extraction_reasons[reason] += 1
            records.append(record)
    report = counts.summarize(records)
    report["selection"] = chooser.choose(report["counts"]).public()
    report["records"] = [r.public() for r in records]
    failures = {r.bug_id: list(r.failed_rules) for r in records}
    report["agreement"] = [{**row, "failed_rules": failures[row["bug_id"]]} for row in observations]
    report["extraction_reasons"] = dict(sorted(extraction_reasons.items()))
    funnel._write(output, report)
    return report


def print_agreement(report):
    print("bug_id\tarm\tvariant\tfailed_codes\tfunnel_stage\tfunnel_reason")
    for row in report["agreement"]:
        print("\t".join((row["bug_id"], row["arm"], row["variant"] or "none",
                         ",".join(row["failed_rules"]) or "ok", row["last_stage"], row["reason"])))


def main(argv=None, *, runner=preflight.docker_runner, repository_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "cohort", "funnel", "environment", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        access.selected(manifest, None)
        body = json.loads(args.funnel.read_text(encoding="utf-8"))
        env = environment.from_json(json.loads(args.environment.read_text(encoding="utf-8")))
        print_agreement(run_a6(manifest, args.cohort, body, env, args.output,
                               runner=runner, repository_root=repository_root))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        parser.exit(2, "a6_input_refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
