"""The pilot binds its judging inputs before evidence is opened."""
import ast
from dataclasses import asdict
import hashlib
from importlib.util import resolve_name
from pathlib import Path
from types import SimpleNamespace

import pytest

from g6.core.ledger import digest
from g6.distill import pilot_gate as pilot
from g6.distill.held_out.registration_gate import Registration, oracle_digest
from g6.distill.held_out.case_gate import CaseRef, unit_split
from g6.distill.held_out.oracle import OraclePolicy
from tests.distill import test_pilot_gate as pilot_tests
from tests.distill import test_acceptance_measurements as fixtures

ROOT = Path(__file__).resolve().parents[2]
rig = pilot_tests.rig


def distill_dependencies(root, start="g6.distill.pilot_gate"):
    pending, seen = [start], set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        file = root / "src" / (name.replace(".", "/") + ".py")
        package = name.rpartition(".")[0]
        if not file.is_file():
            file = root / "src" / name.replace(".", "/") / "__init__.py"
            package = name
        if not file.is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(file.read_bytes())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                names = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            pending.extend(n for n in names if n and (n == "g6.distill" or n.startswith("g6.distill.")))
    return {"src/" + n.replace(".", "/") + (".py" if (root / "src" / (n.replace(".", "/") + ".py")).is_file()
            else "/__init__.py") for n in seen}


def test_transitive_distill_imports_are_pinned():
    # No exemptions: even lazy imports and package initializers affect custody or execution.
    exemptions = {}
    assert distill_dependencies(ROOT) <= set(pilot.JUDGING_FILES) | exemptions.keys()
    assert len(pilot.JUDGING_FILES) == len(set(pilot.JUDGING_FILES))


def test_missing_digest_refuses_without_filesystem(monkeypatch):
    _, candidate, _, _ = fixtures.sample.__wrapped__()
    monkeypatch.setattr(pilot, "_study_root", lambda p: p)
    monkeypatch.setattr(pilot, "_pilot_registration", lambda _: SimpleNamespace(study={}), raising=False)
    monkeypatch.setattr(pilot.production, "_load", lambda *a: pytest.fail("evidence opened"))
    result = pilot.decide_pilot(candidate, study_root=ROOT / ".build" / "unopened", allocation={}, registration="a" * 64)
    assert result.code == "PILOT_JUDGING_CHANGED"


def test_oracle_change_keeps_judging_refusal_code(monkeypatch):
    _, candidate, _, _ = fixtures.sample.__wrapped__()
    policy = OraclePolicy()
    ref = CaseRef(case_id="a" * 64, unit_id="synthetic-1", cohort="development",
                  split=unit_split("synthetic-1", "development", "seed"))
    registration = Registration(seed="seed", cases=(ref,), manifest_sha256=digest([ref.model_dump(mode="json")]),
        operator_digests=dict.fromkeys(("training", "legitimate", "held_out"), "b" * 64),
        oracle_sha256="0" * 64, policy=policy, policy_sha256=digest(policy.model_dump(mode="json")),
        study={"pilot_judging_sha256": "0" * 64})
    path = ROOT / ".build" / "synthetic-registration.json"
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: registration.model_dump_json().encode() if p == path else original(p))
    monkeypatch.setattr(pilot.production, "_policy", lambda: (None, {"registrations": {
        registration.registration_id: {"path": "irrelevant"}}}))
    monkeypatch.setattr(pilot.production, "_plain", lambda *args: path)
    monkeypatch.setattr(pilot, "_study_root", lambda p: p)
    monkeypatch.setattr(pilot.production, "_load", lambda *a: pytest.fail("evidence opened"))
    result = pilot.decide_pilot(candidate, study_root=ROOT / ".build" / "unopened", allocation={},
                                registration=registration.registration_id)
    assert result.code == "PILOT_JUDGING_CHANGED"


@pytest.mark.parametrize("field,code", [("allocation_sha256", "PILOT_ALLOCATION"),
                                      ("pilot_policy_sha256", "PILOT_POLICY"),
                                      ("pilot_gate_version", "PILOT_POLICY")])
def test_registered_policy_and_allocation_are_bound(monkeypatch, field, code):
    _, candidate, _, _ = fixtures.sample.__wrapped__()
    study = {"pilot_judging_sha256": pilot.pilot_judging_digest(ROOT),
             "allocation_sha256": digest({}), "pilot_policy_sha256": digest(asdict(pilot.PilotPolicy())),
             "pilot_gate_version": pilot.PILOT_GATE_VERSION}
    study[field] = "changed"
    monkeypatch.setattr(pilot, "_pilot_registration", lambda _: SimpleNamespace(study=study))
    monkeypatch.setattr(pilot, "_study_root", lambda p: p)
    monkeypatch.setattr(pilot.production, "_load", lambda *a: pytest.fail("evidence opened"))
    result = pilot.decide_pilot(candidate, study_root=ROOT / ".build" / "unopened", allocation={}, registration="a" * 64)
    assert result.code == code


def test_digest_pins_order_paths_and_exact_bytes(tmp_path):
    expected = hashlib.sha256()
    for rel in pilot.JUDGING_FILES:
        file = tmp_path / rel
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"one\r\ntwo\n")
        expected.update(rel.encode("utf-8") + b"\0" + file.read_bytes() + b"\0")
    before = pilot.pilot_judging_digest(tmp_path)
    assert before == expected.hexdigest()
    file.write_bytes(b"one\ntwo\n")
    assert pilot.pilot_judging_digest(tmp_path) != before
    file.unlink()
    with pytest.raises(FileNotFoundError):
        pilot.pilot_judging_digest(tmp_path)


@pytest.mark.parametrize("recorded", [None, "0" * 64])
def test_changed_judging_refuses_before_evidence(rig, monkeypatch, recorded):
    study = {} if recorded is None else {"pilot_judging_sha256": recorded}
    monkeypatch.setattr(pilot, "_pilot_registration", lambda _: SimpleNamespace(study=study))
    result = pilot_tests.decide(pilot, rig)
    assert result.code == "PILOT_JUDGING_CHANGED"
    assert rig["loaded"] == []


def test_precheck_uses_owner_digest_anchor(tmp_path, monkeypatch):
    policy = OraclePolicy()
    ref = CaseRef(case_id="a" * 64, unit_id="synthetic-1", cohort="development",
                  split=unit_split("synthetic-1", "development", "seed"))
    registration = Registration(seed="seed", cases=(ref,), manifest_sha256=digest([ref.model_dump(mode="json")]),
        operator_digests=dict.fromkeys(("training", "legitimate", "held_out"), "b" * 64),
        oracle_sha256=oracle_digest(), policy=policy, policy_sha256=digest(policy.model_dump(mode="json")),
        study={"pilot_judging_sha256": pilot.pilot_judging_digest(ROOT)})
    rel = "validation/distill/draft.json"
    path = tmp_path / rel
    path.parent.mkdir(parents=True)
    path.write_text(registration.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(pilot.production, "ROOT", tmp_path)
    monkeypatch.setattr(pilot.production, "_policy", lambda: (None, {"registrations": {
        registration.registration_id: {"path": rel}}}))
    assert pilot._pilot_registration(registration.registration_id) == registration
    with pytest.raises(ValueError):
        pilot._pilot_registration("c" * 64)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        pilot._pilot_registration(registration.registration_id)
