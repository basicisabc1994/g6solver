"""Run the kind-tagged M1b acquisition funnel with git extraction and host isolation.

Usage: --manifest MANIFEST --cache CACHE --output OUTPUT [--bug OWNER/REPO#PR].
No Docker route is used. Suite execution retains the funnel's confined host runner.
"""
from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path
from types import SimpleNamespace

from .. import _material, funnel
from . import access_m1b, material
from ._mine_io import read_json
from .arm import m1b_variants


def run(manifest, cache, output, *, bugs=None, suite_runner=funnel.suite_observation, clock=time.monotonic):
    """Bind only M1b extraction, membership, construction and scalar-report adapters.

    The original funnel's budget, isolation, control, settling, labels and headline
    stages run unchanged. Bindings are local; no shared module is patched.
    """
    ids = access_m1b.selected(manifest, bugs)
    variants, constructions = [], {}

    def extract(manifest, cache, bug_id, runner, budget):
        budget.check()
        try:
            body = material.material_from_git(manifest, bug_id, cache)
        except _material.MaterialError as exc:
            raise funnel._Stop(str(exc)) from None
        budget.check()
        return body, None

    def shape(manifest, cache, bug_id, arm, body, runner):
        mode = "multi_target" if len(_material.targets(body)) > 1 else "fixed_tests"
        case = funnel._shape(manifest, cache, bug_id, mode, body, runner)
        built = m1b_variants(case)
        constructions.clear()
        constructions.update((entry.variant_id, entry) for entry in built)
        variants[:] = constructions
        return case

    def variant(case, arm, name):
        entry = constructions[name]
        if entry.case is None:
            raise funnel._Stop(entry.reason)
        return entry.case

    def new_row(bug, arm, variant=None):
        row = funnel._bind(funnel._new_row, allocation=SimpleNamespace(repository_of=lambda bug: bug.split("#")[0]))(
            bug, arm, variant)
        row["kind"] = constructions[variant].kind if variant is not None else None
        return row

    def stage(budget, row, name, operation):
        if row["variant"] is not None:
            row["kind"] = constructions[row["variant"]].kind
        return funnel._stage(budget, row, name, operation)

    def write(output, report):
        report["material_source"] = "git"
        report["configuration"] = manifest["configuration"]
        report["m1b_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        funnel._write(output, report)

    bound = funnel._bind(funnel.run_funnel, access=access_m1b, ARMS=("m1b",), VARIANTS=variants,
                         _extract=extract, _shape=shape, _variant=variant, _new_row=new_row,
                         _stage=stage, _write=write)
    return bound(manifest, cache, output, arms=("m1b",), bugs=ids, runner=None,
                 suite_runner=suite_runner, clock=clock)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "cache", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--bug", action="append")
    args = parser.parse_args(argv)
    try:
        run(read_json(args.manifest), args.cache, args.output, bugs=args.bug)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        parser.exit(2, "M1b funnel input or output refused\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
