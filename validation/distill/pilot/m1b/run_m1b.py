"""Run the kind-tagged M1b acquisition funnel with git extraction and host isolation.

Usage: --manifest MANIFEST --inventory INVENTORY --pool POOL --environment ENVIRONMENT
    --cache CACHE --output OUTPUT [--bug OWNER/REPO#PR].
No Docker route is used. Suite execution retains the funnel's confined host runner.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from .. import _material, funnel
from validation.worked_examples.selection import is_test

from . import access_m1b, counts, fit, gitread, material
from ._mine_io import local_path, read_json
from .arm import m1b_variants

PREPARED_REQUIRED = "prepared_interpreter_required"


def _staged(body, bug_id, configuration, environment):
    """Use the static filter's source adapter and complete case constructor."""
    changes = {path: fit.ChangedFile(body["base"][path].encode() if path in body["base"] else None,
                                     body["fixed"][path].encode() if path in body["fixed"] else None)
               for path in _material.changed_files(body)}
    tree = {path: path for path in body["fixed"]}
    objects = SimpleNamespace(blob=lambda path: body["fixed"][path].encode())
    helpers = gitread._reachable_tests(tree, changes, objects)
    candidate = fit.Candidate(bug_id.split("#")[0], bug_id, Path("."), changes, helpers)
    record = fit._assess_sources(candidate, fit._environment(environment),
                                 {path: text for path, text in body["fixed"].items() if not is_test(path)})
    cases = counts.staged_cases(record, configuration)
    return list(zip(record.tests, cases, strict=True))


def run(manifest, cache, output, *, inventory=None, pool=None, environment=None, bugs=None,
        suite_runner=funnel.suite_observation, clock=time.monotonic):
    """Bind only M1b extraction, membership, construction and scalar-report adapters.

    The original funnel's budget, isolation, control, settling, labels and headline
    stages run unchanged. Bindings are local; no shared module is patched.
    """
    inputs = {"inventory": inventory, "pool": pool, "environment": environment}
    ids = access_m1b.selected(manifest, bugs, **inputs)
    configuration = manifest["configuration"]
    python = None
    if configuration == "K_3":
        recorded = manifest.get("prepared_interpreter")
        if not isinstance(recorded, str) or not recorded or not Path(recorded).is_absolute():
            raise ValueError(PREPARED_REQUIRED)
        python = str(local_path(Path(recorded)))
        if not Path(python).is_file():
            raise ValueError(PREPARED_REQUIRED)
    variants, constructions = [], {}
    pending = []
    cached, active_case, test_module = None, None, None

    def tasks(manifest, requested):
        # Yield each staged module through the same funnel loop and its one budget.
        nonlocal cached, active_case, test_module
        for bug_id in ids:
            cached, active_case, test_module = None, None, None
            pending.clear()
            yield bug_id
            while pending:
                test_module, active_case = pending.pop(0)
                yield bug_id

    def extract(manifest, cache, bug_id, runner, budget):
        nonlocal cached
        budget.check()
        try:
            if cached is None:
                cached = material.material_from_git(manifest, bug_id, cache, **inputs)
        except _material.MaterialError as exc:
            raise funnel._Stop(str(exc)) from None
        budget.check()
        return cached, None

    def shape(manifest, cache, bug_id, arm, body, runner):
        nonlocal active_case, test_module
        if configuration == "K_0":
            mode = "multi_target" if len(_material.targets(body)) > 1 else "fixed_tests"
            case = funnel._shape(manifest, cache, bug_id, mode, body, runner)
        else:
            if active_case is None:
                env = environment["prepared_environment"] if configuration == "K_3" else environment
                try:
                    pending.extend(_staged(body, bug_id, configuration, env))
                except ValueError:
                    raise funnel._Stop("case_record_invalid") from None
                test_module, active_case = pending.pop(0)
            case = active_case
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
        if configuration == "K_2":
            row["test_module"] = test_module
        return row

    def stage(budget, row, name, operation):
        if row["variant"] is not None:
            row["kind"] = constructions[row["variant"]].kind
        try:
            if name != "headline":
                return funnel._stage(budget, row, name, operation)
            # The label is settled before the headline step, which runs extra suites for some labels only. A stop
            # there must not drop the label (attrition must not depend on it): keep it and record the reason apart.
            try:
                return funnel._stage(budget, row, name, operation)
            except funnel._BudgetStop:
                raise
            except funnel._Stop as exc:
                row["headline_unavailable"] = exc.reason
                return "headline_unavailable"
        finally:
            if configuration == "K_2":
                row["test_module"] = test_module

    def write(output, report):
        report["material_source"] = "git"
        report["configuration"] = manifest["configuration"]
        report["m1b_code_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        funnel._write(output, report)

    def suite(*args, **kwargs):
        if python is not None:
            kwargs["python"] = python
        return suite_runner(*args, **kwargs)

    bound = funnel._bind(funnel.run_funnel, access=SimpleNamespace(selected=tasks),
                         sys=SimpleNamespace(executable=python or sys.executable),
                         ARMS=("m1b",), VARIANTS=variants,
                         _extract=extract, _shape=shape, _variant=variant, _new_row=new_row,
                         _stage=stage, _write=write)
    return bound(manifest, cache, output, arms=("m1b",), bugs=ids, runner=None,
                 suite_runner=suite, clock=clock)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "inventory", "pool", "environment", "cache", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--bug", action="append")
    args = parser.parse_args(argv)
    try:
        run(read_json(args.manifest), args.cache, args.output, inventory=read_json(args.inventory),
            pool=read_json(args.pool), environment=read_json(args.environment), bugs=args.bug)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        reason = PREPARED_REQUIRED if str(exc) == PREPARED_REQUIRED else "M1b funnel input or output refused"
        parser.exit(2, reason + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
