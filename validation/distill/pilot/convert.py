"""Convert an acquisition fix to a bounded case record without labelling it."""
from __future__ import annotations

import json

from g6.core.ledger import digest
from g6.distill.held_out.case_gate import CaseRecord, Provenance
from validation.worked_examples.selection import is_test

from . import _material, access, preflight


def inspect_bug(manifest, cohort_path, bug_id, *, runner=preflight.docker_runner) -> CaseRecord:
    bug = access.load_bug(manifest, cohort_path, bug_id)
    material = _material.extract(bug, runner, include_support=True)
    targets = _material.targets(material)
    if not targets:
        raise ValueError("no_python_fix")
    if len(targets) != 1:
        raise ValueError("multiple_python_targets")
    target = targets[0]
    lines = _material.changed_lines(material, target)
    if not lines:
        raise ValueError("empty_changed_lines")
    if target not in material["base"] or target not in material["fixed"]:
        raise ValueError("missing_target_pair")
    tests = [p for p in _material.changed_files(material) if is_test(p)]
    if len(tests) != 1:
        raise ValueError("single_test_module_required")
    test = tests[0]
    if test not in material["base"] or test not in material["fixed"]:
        raise ValueError("missing_test_pair")
    record = CaseRecord(unit_id=bug_id,
                        provenance=Provenance(source="maintainer", cohort="development",
                                              source_ref=f"{bug['oracle_image_id']}:/oracle/fix.patch",
                                              source_sha256=digest(material)),
                        files={p: s for p, s in material["fixed"].items() if not is_test(p)},
                        target=target, changed_lines=lines, before_tests=material["base"][test],
                        after_tests=material["fixed"][test], known_wrong=(material["base"][target],))
    return CaseRecord.model_validate_json(record.model_dump_json())


def main(argv=None, *, runner=preflight.docker_runner) -> int:
    parser = access.arguments(__doc__, convert=True)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        ids = access.selected(manifest, args.bug)
        records = [inspect_bug(manifest, args.cohort, bug, runner=runner) for bug in ids]
        output = args.output or args.cohort.parent / "pilot-cases"
        for record in records:
            access.write_json(output / f"{record.case_id}.json", record.model_dump(mode="json"))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
