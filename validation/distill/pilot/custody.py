"""Evaluator preparation of admission and monitor cases in sealed custody.

The export contains references, receipt digests and role totals only. Oracle
staging uses a separate evaluator workspace, following the production oracle.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import json
import logging
import os
from pathlib import Path
import sys

from g6.core.ledger import canonical, digest
from g6.distill.held_out import oracle
from g6.distill.held_out.case_gate import CaseRecord, Provenance, unit_split
from g6.distill.held_out.registration_gate import strict_json
from validation.worked_examples.selection import is_test

from . import _material, allocation, preflight

ROOT = Path(__file__).resolve().parents[3]
ROLES = {"admission": "acceptance", "monitor": "monitor"}
LABELS = ("positive", "negative", "excluded")


class Refused(ValueError):
    """A stable code without input values or exception details."""


class _Discard:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


@contextmanager
def _quiet():
    previous = logging.root.manager.disable
    logging.disable(sys.maxsize)
    try:
        with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
            yield
    finally:
        logging.disable(previous)


def checked_path(path) -> Path:
    path = Path(path).absolute()
    if any(part.lower().startswith("final-") for part in path.parts):
        raise Refused("custody_final")
    if ".." in path.parts:
        raise Refused("custody_path")
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise Refused("custody_link")
    return path


def _role(manifest, unit):
    try:
        repository = allocation.repository_of(unit)
        for role, group in manifest["roles"].items():
            if repository in group["repositories"]:
                if role not in ROLES:
                    raise Refused(f"custody_role_{role}")
                if unit in group["bugs"]:
                    return role
                break
    except (TypeError, AttributeError, ValueError) as error:
        if isinstance(error, Refused):
            raise
    raise Refused("custody_role_unallocated")


def _allocation(manifest):
    try:
        if allocation.check(manifest) or manifest.get("cohort") != "development":
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise Refused("custody_allocation") from None


def _folder(*, study=None, output=None):
    home = os.environ.get("G6_HOME")
    if not home:
        raise Refused("custody_home_required")
    folder = checked_path(Path(home) / "distill/held_out")
    if folder.is_relative_to(ROOT.absolute()) or (study and folder.is_relative_to(study)):
        raise Refused("custody_location")
    if output is not None and checked_path(output) != folder:
        raise Refused("custody_location")
    return folder


def _write(folder, kind, body):
    if checked_path(folder) != _folder() or kind not in {"cases", "receipts"}:
        raise Refused("custody_location")
    path = checked_path(folder / kind / f"{digest(body)}.json")
    if not path.is_relative_to(folder):
        raise Refused("custody_location")
    path.parent.mkdir(parents=True, exist_ok=True)
    checked_path(path)
    data = canonical(body) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != data:
            raise Refused("custody_existing_differs") from None
    return digest(body)


def _record(bug, runner):
    # Use the acquisition converter's extraction and fixed-side line rules.
    material = _material.extract(bug, runner, include_support=True)
    targets = _material.targets(material)
    if len(targets) != 1:
        raise Refused("custody_target_count")
    target = targets[0]
    tests = [p for p in _material.changed_files(material) if is_test(p)]
    if len(tests) != 1:
        raise Refused("custody_test_count")
    test = tests[0]
    return CaseRecord(unit_id=bug["id"],
        provenance=Provenance(source="maintainer", cohort="development",
            source_ref=f"{bug['oracle_image_id']}:/oracle/fix.patch", source_sha256=digest(material)),
        files={p: s for p, s in material["fixed"].items() if not is_test(p)}, target=target,
        changed_lines=_material.changed_lines(material, target), before_tests=material["base"][test],
        after_tests=material["fixed"][test], known_wrong=(material["base"][target],))


def prepare(manifest, cohort_path, *, seed, policy, workspace, bugs=None, output=None,
            runner=preflight.docker_runner):
    """Return metadata only; refuse roles and destinations before opening bugs."""
    with _quiet():
        try:
            return _prepare(manifest, cohort_path, seed=seed, policy=policy, workspace=workspace,
                            bugs=bugs, output=output, runner=runner)
        except Refused:
            raise
        except Exception:
            raise Refused("custody_preparation_failed") from None


def _prepare(manifest, cohort_path, *, seed, policy, workspace, bugs, output, runner):
    _allocation(manifest)
    units = list(bugs) if bugs is not None else [b for r in ROLES for b in manifest["roles"][r]["bugs"]]
    if not units or len(set(units)) != len(units):
        raise Refused("custody_units")
    roles = {unit: _role(manifest, unit) for unit in units}
    expected = {b for role in ROLES for b in manifest["roles"][role]["bugs"]}
    if set(units) != expected:
        raise Refused("custody_units")
    if any(unit_split(u, "development", seed) != ROLES[r] for u, r in roles.items()):
        raise Refused("custody_split")
    path = checked_path(cohort_path)
    folder = _folder(study=path.parent, output=output)
    workspace = checked_path(workspace)
    if (not workspace.is_dir() or workspace.is_relative_to(folder)
            or workspace.is_relative_to(ROOT.absolute()) or workspace.is_relative_to(path.parent)):
        raise Refused("custody_workspace")
    policy = oracle.OraclePolicy.model_validate(policy.model_dump(mode="json"))
    body = strict_json(path.read_bytes())
    ids = [row["id"] for row in body["bugs"]]
    allocated = [b for group in manifest["roles"].values() for b in group["bugs"]]
    if (body.get("cohort") != "development" or body.get("digest") != manifest["cohort_digest"]
            or len(ids) != len(set(ids)) or set(ids) != set(allocated)):
        raise Refused("custody_cohort")
    result = {"version": 1, "allocation_sha256": digest(manifest),
              "policy_sha256": digest(policy.model_dump(mode="json")), "cases": [], "oracle_receipts": {},
              "label_counts": {r: dict.fromkeys(LABELS, 0) for r in ROLES}, "refusals": []}
    for unit, role in roles.items():
        try:
            bug = next(row for row in body["bugs"] if row["id"] == unit)
            record = _record(bug, runner)
            receipt = oracle.label(record, policy=policy, workspace=workspace)
            receipt = oracle.OracleResult.model_validate(receipt.model_dump(mode="json"))
            if receipt.case_id != record.case_id or receipt.policy_sha256 != result["policy_sha256"]:
                raise Refused("custody_receipt_binding")
            _write(folder, "cases", record.model_dump(mode="json"))
            receipt_digest = _write(folder, "receipts", receipt.model_dump(mode="json"))
            result["label_counts"][role][receipt.label] += 1
            result["cases"].append({"case_id": record.case_id, "unit_id": unit,
                                    "cohort": "development", "split": ROLES[role]})
            result["oracle_receipts"][record.case_id] = {"receipt_sha256": receipt_digest}
        except Exception:
            result["refusals"].append({"unit_id": unit, "reason_code": "custody_unit_failed"})
    return result


def main(argv=None, *, runner=preflight.docker_runner):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--seed", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--bug", action="append")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        paths = [checked_path(p) for p in (args.manifest, args.cohort, args.policy)]
        manifest = strict_json(paths[0].read_bytes())
        policy = oracle.OraclePolicy.model_validate(strict_json(paths[2].read_bytes()))
        result = prepare(manifest, paths[1], seed=args.seed, policy=policy, workspace=args.workspace,
                         bugs=args.bug, output=args.output, runner=runner)
    except Exception as error:
        print(str(error) if isinstance(error, Refused) else "custody_preparation_failed", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 2 if result["refusals"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
