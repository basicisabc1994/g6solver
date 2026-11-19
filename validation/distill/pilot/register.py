"""Assemble a draft from development-manifest metadata; the owner anchors it.

Usage: python -m validation.distill.pilot.register --manifest allocation.json
       --cohort DEVELOPMENT_MANIFEST --output OUTPUT_FOLDER

The manifest's pilot_registration section supplies seed, cases (CaseRef objects,
with case_id exported from CaseRecord), operator_digests, policy, study and
oracle_receipts. Each receipt entry names case_id, label, policy_sha256 and
receipt_sha256. These are evaluator-exported metadata, never case bodies.
Study supplies family, acceptance_controls and replacement_protocol_sha256.
The optional --custody-metadata export supplies admission and monitor references
and receipt digests; matching receipts are read only from sealed custody.
No source paths, CaseRecords or cohort bug payloads are opened.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys

from g6.core.ledger import canonical, digest
from g6.distill import acceptance_gate as production
from g6.distill.pilot_gate import PILOT_GATE_VERSION, PilotPolicy, pilot_judging_digest
from g6.distill.held_out.case_gate import CaseRef, SHA256
from g6.distill.held_out.oracle import OraclePolicy
from g6.distill.held_out.registration_gate import Registration, oracle_digest, read_registration, strict_json, study_metadata

from . import allocation, custody

ROOT = Path(__file__).resolve().parents[3]
COMPARATORS = (
    {"id": "program_only", "description": "Program-only comparator"},
    {"id": "stage1_reasoning_withheld", "description": "Stage-1 reasoning withheld ablation"},
)


def checked_path(path: Path) -> Path:
    """Refuse sealed names and aliases before any file is read or created."""
    path = Path(path).absolute()
    if any(part.lower().startswith("final-") for part in path.parts):
        raise ValueError("final-* path refused")
    if ".." in path.parts:
        raise ValueError("parent traversal refused")
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError("linked path refused")
    return path


def _sha(value, name):
    if not isinstance(value, str) or not re.fullmatch(SHA256, value):
        raise ValueError(f"missing or invalid {name}")
    return value


def _custody_inputs(manifest, metadata, exported):
    """Resolve digest-bound receipts inside custody for the draft's oracle checks."""
    try:
        if (set(exported) != {"version", "allocation_sha256", "policy_sha256", "cases", "oracle_receipts",
                              "label_counts", "refusals"}
                or exported["version"] != 1 or exported["allocation_sha256"] != digest(manifest)
                or exported["refusals"] or not isinstance(exported["cases"], list)):
            raise ValueError
        policy = OraclePolicy.model_validate(metadata["policy"])
        policy_sha = digest(policy.model_dump(mode="json"))
        if exported["policy_sha256"] != policy_sha:
            raise ValueError
        refs = [CaseRef.model_validate(c) for c in exported["cases"]]
        wanted = {u: r for r in custody.ROLES for u in manifest["roles"][r]["bugs"]}
        if (len(refs) != len(wanted) or {c.unit_id for c in refs} != set(wanted)
                or len({c.case_id for c in refs}) != len(refs)
                or set(exported["oracle_receipts"]) != {c.case_id for c in refs}):
            raise ValueError
        folder = custody._folder()
        counts = {r: dict.fromkeys(custody.LABELS, 0) for r in custody.ROLES}
        receipts = {}
        for ref in refs:
            role = wanted[ref.unit_id]
            if ref.cohort != "development" or ref.split != custody.ROLES[role]:
                raise ValueError
            raw = exported["oracle_receipts"][ref.case_id]
            if set(raw) != {"receipt_sha256"}:
                raise ValueError
            ident = _sha(raw["receipt_sha256"], "receipt digest")
            path = custody.checked_path(folder / "receipts" / f"{ident}.json")
            if path.stat().st_size > 65536:
                raise ValueError
            body = strict_json(path.read_bytes())
            if digest(body) != ident:
                raise ValueError
            receipt = custody.oracle.OracleResult.model_validate(body)
            if (receipt.case_id != ref.case_id or receipt.policy_sha256 != policy_sha
                    or receipt.label not in {"positive", "negative"}):
                raise ValueError
            counts[role][receipt.label] += 1
            receipts[ref.case_id] = {"case_id": ref.case_id, "label": receipt.label,
                                     "policy_sha256": policy_sha, "receipt_sha256": ident}
        if exported["label_counts"] != counts:
            raise ValueError
        raw_cases = metadata.get("cases", [])
        raw_receipts = metadata.get("oracle_receipts", {})
        if ({c["unit_id"] for c in raw_cases} & set(wanted) or set(raw_receipts) & set(receipts)):
            raise ValueError
        return dict(metadata, cases=[*raw_cases, *exported["cases"]], oracle_receipts=dict(raw_receipts, **receipts))
    except Exception:
        raise ValueError("custody_metadata_invalid") from None


def assemble(manifest: dict, cohort: dict, *, custody_metadata: dict | None = None) -> Registration:
    """Consume only envelope IDs/digests and explicit evaluator metadata."""
    errors = allocation.check(manifest)
    if errors:
        raise ValueError("invalid allocation: " + "; ".join(errors))
    if (manifest.get("cohort") != "development" or cohort.get("cohort") != "development"
            or cohort.get("digest") != manifest["cohort_digest"]):
        raise ValueError("development cohort differs from allocation")
    _sha(manifest["cohort_digest"], "cohort digest")
    ids = [b["id"] for b in cohort["bugs"]]
    roles = manifest["roles"]
    allocated = [b for group in roles.values() for b in group["bugs"]]
    if len(ids) != len(set(ids)) or set(ids) != set(allocated):
        raise ValueError("cohort bug IDs differ from allocation")
    metadata = cohort.get("pilot_registration", {})
    if not isinstance(metadata, dict):
        raise ValueError("pilot_registration metadata must be an object")
    if custody_metadata is not None:
        metadata = _custody_inputs(manifest, metadata, custody_metadata)
    wanted = {unit: role for role in ("acquisition", "admission", "monitor") for unit in roles[role]["bugs"]}
    raw_cases = metadata.get("cases", [])
    units = {c["unit_id"] for c in raw_cases}
    receipts = metadata.get("oracle_receipts", {})
    missing = [name for name in ("seed", "operator_digests", "policy", "study") if not metadata.get(name)]
    for unit in sorted(wanted):
        if unit not in units:
            missing.append(f"CaseRecord digest: {unit}")
        refs = [c for c in raw_cases if c["unit_id"] == unit]
        if not refs or any(c.get("case_id") not in receipts for c in refs):
            missing.append(f"oracle receipt: {unit}")
    for name in ("family", "acceptance_controls", "replacement_protocol_sha256"):
        if not metadata.get("study", {}).get(name):
            missing.append(f"study.{name}")
    if missing:
        raise ValueError("missing inputs:\n" + "\n".join(missing))
    if units != set(wanted):
        raise ValueError("case units must be acquisition, admission or monitor only")
    cases = tuple(sorted((CaseRef.model_validate(c) for c in raw_cases), key=lambda c: c.case_id))
    policy = OraclePolicy.model_validate(metadata["policy"])
    policy_sha = digest(policy.model_dump(mode="json"))
    labels = {}
    for case in cases:
        expected = {"acquisition": "tuning", "admission": "acceptance", "monitor": "monitor"}[wanted[case.unit_id]]
        if case.cohort != "development" or case.split != expected:
            raise ValueError(f"allocation split conflict: {case.unit_id}")
        receipt = receipts[case.case_id]
        _sha(receipt.get("receipt_sha256"), f"oracle receipt digest: {case.unit_id}")
        if (set(receipt) != {"case_id", "label", "policy_sha256", "receipt_sha256"}
                or receipt["case_id"] != case.case_id or receipt["policy_sha256"] != policy_sha
                or receipt["label"] not in {"positive", "negative"}):
            raise ValueError(f"oracle receipt metadata differs: {case.unit_id}")
        labels[case.case_id] = receipt["label"]
    study = metadata["study"]
    if set(study) != {"family", "acceptance_controls", "replacement_protocol_sha256"}:
        raise ValueError("study metadata must name only family, acceptance_controls and replacement_protocol_sha256")
    if not isinstance(study["family"], str) or not study["family"].strip():
        raise ValueError("study.family required")
    _sha(study["replacement_protocol_sha256"], "study.replacement_protocol_sha256")
    controls = study["acceptance_controls"]
    admission = {c.case_id for c in cases if c.split == "acceptance"}
    if (not isinstance(controls, list) or len(controls) != 2 or len(set(controls)) != 2
            or not set(controls) <= admission or [labels[c] for c in controls] != ["positive", "negative"]):
        raise ValueError("study.acceptance_controls requires positive and negative admission case IDs")
    for split in ("acceptance", "monitor"):
        if {labels[c.case_id] for c in cases if c.split == split} != {"positive", "negative"}:
            raise ValueError(f"both oracle classes required: {split}")
    pilot_policy = asdict(PilotPolicy())
    study = study_metadata(dict(study,
        acceptance_policy_sha256=production._policy_digest(), acceptance_code_sha256=production._judging_digest(),
        pilot_gate_version=PILOT_GATE_VERSION, pilot_policy_sha256=digest(pilot_policy), pilot_policy=pilot_policy,
        pilot_judging_sha256=pilot_judging_digest(production.ROOT), allocation_sha256=digest(manifest),
        exploratory=True, comparators=list(COMPARATORS),
        oracle_receipts={c.case_id: receipts[c.case_id] for c in cases},
        per_unit_rules={"accuracy_unit": "bug", "abstain_is_incorrect": True,
            "minimum_accuracy_delta": 0, "maximum_teacher_correct_regressions": 0,
            "maximum_added_false_acceptances": 0, "maximum_added_admission_false_positives": 0,
            "maximum_monitor_false_positives": 0},
        cost_rules={"horizon": 1000, "maximum_cost_point": 0., "validation": "all candidate attempts cumulatively",
            "publication": "reserve publication and terminal append", "bounds": "registered finite task_saving_bounds"}))
    return Registration(seed=metadata["seed"], cases=cases,
        manifest_sha256=digest([c.model_dump(mode="json") for c in cases]), operator_digests=metadata["operator_digests"],
        oracle_sha256=oracle_digest(), policy=policy, policy_sha256=policy_sha, study=study)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--custody-metadata", type=Path)
    args = parser.parse_args(argv)
    try:
        # Check all paths before the first read, including the output name.
        manifest_path, cohort_path, output = map(checked_path, (args.manifest, args.cohort, args.output))
        custody_path = checked_path(args.custody_metadata) if args.custody_metadata is not None else None
        if custody_path is not None:
            custody._folder(study=cohort_path.parent)
        if cohort_path.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError("development cohort manifest must be outside the repository")
        path = checked_path(output / "registration.json")
        if path.exists() or path in {manifest_path, cohort_path, production.POLICY_PATH.absolute()}:
            raise ValueError("draft cannot replace an existing file or input")
        exported = strict_json(custody_path.read_bytes()) if custody_path is not None else None
        registration = assemble(strict_json(manifest_path.read_bytes()), strict_json(cohort_path.read_bytes()),
                                custody_metadata=exported)
        output.mkdir(exist_ok=True)
        path = checked_path(path)
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(registration.model_dump(mode="json")) + "\n")
        read_registration(path, expected_digest=registration.registration_id)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    ident = registration.registration_id
    destination = f"validation/distill/pilot/registrations/{ident}.json"
    anchor = {ident: {"path": destination, "commit": "OWNER_REGISTRATION_COMMIT_REQUIRED", "concepts": {}}}
    print(f"Registration digest: {ident}")
    print(f"Owner: copy registration.json to {destination}; review and commit it before any Concept exists.")
    print("Then replace OWNER_REGISTRATION_COMMIT_REQUIRED with that commit's 40-hex ID and add this entry to policy.json registrations:")
    print(json.dumps(anchor, indent=2))
    print("Commit the owner anchor separately. After a Concept is created later, anchor its exact path and commit under concepts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
