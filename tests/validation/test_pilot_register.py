"""Synthetic registration metadata never opens cohort bug bodies."""
from copy import deepcopy
from dataclasses import asdict
import importlib
import json
from pathlib import Path

import pytest

from g6.core.ledger import digest
from g6.distill import pilot_gate
from g6.distill.held_out.case_gate import unit_split
from g6.distill.held_out.oracle import OraclePolicy
from g6.distill.held_out.registration_gate import read_registration
from validation.distill.pilot import allocation

ROOT = Path(__file__).resolve().parents[2]


def tool():
    return importlib.import_module("validation.distill.pilot.register")


def synthetic():
    seed = "synthetic-fixed-seed"
    roles = allocation.allocate("a" * 64, [f"repo{i}" for i in range(18)])
    manifest = {"cohort": "development", "cohort_digest": "a" * 64, "label": allocation.LABEL, "roles": {}}
    cases, receipts, bugs = [], {}, []
    policy = OraclePolicy().model_dump(mode="json")
    for role, repos in roles.items():
        ids = []
        for repo in repos:
            split = {"acquisition": "tuning", "admission": "acceptance", "monitor": "monitor", "reuse": "reserve"}[role]
            unit = next(f"{repo}-{i}" for i in range(1000) if unit_split(f"{repo}-{i}", "development", seed) == split)
            ids.append(unit)
            bugs.append({"id": unit, "content": "MUST NOT BE COPIED"})
            if role == "reuse":
                continue
            ident = digest([unit, "synthetic case"])
            cases.append({"case_id": ident, "unit_id": unit, "cohort": "development", "split": split})
            receipts[ident] = {"case_id": ident, "label": "positive" if len(ids) % 2 else "negative",
                "policy_sha256": digest(policy), "receipt_sha256": digest([ident, "synthetic receipt"])}
        manifest["roles"][role] = {"repositories": repos, "bugs": ids}
    controls = [next(c["case_id"] for c in cases if c["split"] == "acceptance" and receipts[c["case_id"]]["label"] == label)
                for label in ("positive", "negative")]
    metadata = {"seed": seed, "cases": cases, "oracle_receipts": receipts,
        "operator_digests": dict.fromkeys(("training", "legitimate", "held_out"), "b" * 64), "policy": policy,
        "study": {"family": "test_weakening", "acceptance_controls": controls, "replacement_protocol_sha256": "c" * 64}}
    return manifest, {"cohort": "development", "digest": "a" * 64, "bugs": bugs, "pilot_registration": metadata}


def test_assembler_keeps_production_and_pilot_bindings():
    manifest, cohort = synthetic()
    registration = tool().assemble(manifest, cohort)
    assert registration.study["pilot_judging_sha256"] == pilot_gate.pilot_judging_digest(ROOT)
    assert registration.study["pilot_policy_sha256"] == digest(asdict(pilot_gate.PilotPolicy()))
    assert registration.study["allocation_sha256"] == digest(manifest)
    assert registration.study["exploratory"] is True
    assert {c["id"] for c in registration.study["comparators"]} == {"program_only", "stage1_reasoning_withheld"}
    assert "MUST NOT BE COPIED" not in registration.model_dump_json()


def test_missing_input_names_unit_and_artifact():
    manifest, cohort = synthetic()
    unit = manifest["roles"]["admission"]["bugs"][0]
    cohort["pilot_registration"]["cases"] = [c for c in cohort["pilot_registration"]["cases"] if c["unit_id"] != unit]
    with pytest.raises(ValueError, match=f"CaseRecord digest: {unit}"):
        tool().assemble(manifest, cohort)


def test_missing_receipt_and_split_conflict_stop():
    manifest, cohort = synthetic()
    case = next(c for c in cohort["pilot_registration"]["cases"] if c["split"] == "monitor")
    broken = deepcopy(cohort)
    del broken["pilot_registration"]["oracle_receipts"][case["case_id"]]
    with pytest.raises(ValueError, match=f"oracle receipt: {case['unit_id']}"):
        tool().assemble(manifest, broken)
    case["split"] = "acceptance"
    with pytest.raises(ValueError, match="split"):
        tool().assemble(manifest, cohort)


def test_seed_conflict_keeps_production_split_check():
    manifest, cohort = synthetic()
    case = cohort["pilot_registration"]["cases"][0]
    cohort["pilot_registration"]["seed"] = next(str(i) for i in range(1000)
        if unit_split(case["unit_id"], "development", str(i)) != case["split"])
    with pytest.raises(ValueError, match="cut differs from frozen seed"):
        tool().assemble(manifest, cohort)


def test_absent_metadata_names_every_required_unit():
    manifest, cohort = synthetic()
    del cohort["pilot_registration"]
    with pytest.raises(ValueError) as error:
        tool().assemble(manifest, cohort)
    for role in ("acquisition", "admission", "monitor"):
        for unit in manifest["roles"][role]["bugs"]:
            assert f"CaseRecord digest: {unit}" in str(error.value)
            assert f"oracle receipt: {unit}" in str(error.value)
    assert "study.replacement_protocol_sha256" in str(error.value)


@pytest.mark.parametrize("path", ["final-cohort.json", "FINAL-cohort/data.json", "folder/final-hidden/input.json"])
def test_final_path_is_refused_before_read(path, monkeypatch):
    monkeypatch.setattr(Path, "read_bytes", lambda p: pytest.fail("sealed path read"))
    with pytest.raises(ValueError, match="final-"):
        tool().checked_path(Path(path))


def test_draft_cli_round_trip_and_output_custody(tmp_path, monkeypatch, capsys):
    module = tool()
    manifest, cohort = synthetic()
    manifest_path, cohort_path = tmp_path / "allocation.json", tmp_path / "development.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
    output = tmp_path / "output"
    monkeypatch.setattr(module, "ROOT", tmp_path / "repository")
    policy_path = ROOT / "validation/distill/held_out/policy.json"
    original_policy = policy_path.read_bytes()
    original_open = Path.open
    writes = []

    def opened(path, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            assert path.resolve().is_relative_to(output.resolve())
            writes.append(path)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    assert module.main(["--manifest", str(manifest_path), "--cohort", str(cohort_path), "--output", str(output)]) == 0
    registration = module.assemble(manifest, cohort)
    assert read_registration(output / "registration.json", expected_digest=registration.registration_id) == registration
    assert policy_path.read_bytes() == original_policy
    assert writes == [output / "registration.json"]
    printed = capsys.readouterr().out
    assert registration.registration_id in printed
    assert '"concepts": {}' in printed
    assert "before any Concept" in printed


def test_cli_missing_input_leaves_no_output(tmp_path, monkeypatch, capsys):
    module = tool()
    manifest, cohort = synthetic()
    del cohort["pilot_registration"]
    manifest_path, cohort_path = tmp_path / "allocation.json", tmp_path / "development.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cohort_path.write_text(json.dumps(cohort), encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path / "repository")
    output = tmp_path / "output"
    assert module.main(["--manifest", str(manifest_path), "--cohort", str(cohort_path), "--output", str(output)]) == 2
    assert "seed" in capsys.readouterr().err
    assert not output.exists()
