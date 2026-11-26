# SPDX-License-Identifier: Apache-2.0
import hashlib
import json

import pytest

from g6.distill.held_out.sealed_gate import SealedRef, evaluate_sealed, install, pin, verify


def test_pin_and_verify_read_only_digest_and_return_no_content(tmp_path):
    path = tmp_path / "operators.json"
    path.write_bytes(b'{"never": "expose this synthetic payload"}')
    ref = pin(path)
    assert isinstance(ref, SealedRef)
    assert ref.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(ref.model_dump()) == {"sha256"}
    assert verify(path, ref) is None


def test_changed_sealed_file_fails_frozen_digest(tmp_path):
    path = tmp_path / "operators.json"
    path.write_bytes(b"first synthetic bytes")
    ref = pin(path)
    path.write_bytes(b"changed synthetic bytes")
    with pytest.raises(ValueError, match="digest"):
        verify(path, ref)


def test_sealed_evaluation_refuses_caller_before_opening_file(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, "open", lambda *a, **kw: pytest.fail("sealed bytes read by an arm"))
    with pytest.raises(PermissionError, match="G6_E_DISTILL_SEALED_ACCESS"):
        evaluate_sealed(tmp_path / "missing.json", SealedRef(sha256="a" * 64), None,
                        policy=None, workspace=tmp_path, registration=None)


def test_sealed_module_has_no_public_content_reader():
    from g6.distill.held_out import sealed_gate
    assert sealed_gate.__all__ == ["SealedRef", "pin", "verify", "install", "evaluate_sealed"]
    assert "caller" not in __import__("inspect").signature(evaluate_sealed).parameters


def test_internal_bytes_reader_refuses_non_evaluator_callers(tmp_path):
    from g6.distill.held_out import sealed_gate
    with pytest.raises(PermissionError, match="G6_E_DISTILL_SEALED_ACCESS"):
        sealed_gate._bytes(tmp_path / "absent.json")


def test_installed_object_is_content_addressed_and_idempotent(tmp_path):
    source = tmp_path / "source.json"
    source.write_bytes(json.dumps({"synthetic": True}).encode())
    reference = install(source, home=tmp_path / "home")
    assert reference == install(source, home=tmp_path / "home")
    assert verify(tmp_path / "home/distill/held_out/objects" / f"{reference.sha256}.json", reference) is None


def test_sealed_evaluator_returns_only_labels_and_checks_registration(tmp_path, monkeypatch):
    from g6.distill.held_out import sealed_gate
    from g6.distill.held_out.case_gate import CaseRecord, Provenance
    from g6.distill.held_out.oracle import OraclePolicy, OracleResult
    from g6.distill.held_out.registration_gate import freeze

    source = "from subject import answer\ndef test_it():\n    assert answer(1) == 2\n"
    case = CaseRecord(unit_id="selection-planted", provenance=Provenance(source="round3", cohort="selection",
                      source_ref="synthetic", source_sha256="a" * 64),
                      files={"subject.py": "def answer(x):\n    return x + 1\n"}, target="subject.py", changed_lines=(2,),
                      before_tests=source, after_tests=source, known_wrong=("def answer(x):\n    return x\n",))
    payload = {"version": 1, "author_family": "claude", "training_family": "codex", "authorship_sha256": "e" * 64,
               "operators": [{"name": "synthetic", "before": "assert answer(1) == 2", "after": "pass"}]}
    path = tmp_path / "operators.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    ref = pin(path)
    policy = OraclePolicy(min_mutants=1)
    registration_path = tmp_path / "registration.json"
    frozen = freeze(registration_path, [case], seed="frozen",
                    operator_digests={"training": "b" * 64, "legitimate": "c" * 64, "held_out": ref.sha256},
                    policy=policy.model_dump(), study={})
    # Unit seam: real module identity/filename checks are tested separately.
    monkeypatch.setattr(sealed_gate, "_require_evaluator", lambda: None)
    calls = []

    def checked(generated, **kwargs):
        calls.append(generated)
        assert "assert" not in generated.after_tests
        return OracleResult(case_id=generated.case_id, label="positive", reason="synthetic",
                            policy_sha256=frozen.policy_sha256)

    monkeypatch.setattr(sealed_gate, "label", checked)
    results = evaluate_sealed(path, ref, case, policy=policy, workspace=tmp_path,
                              registration=(registration_path, frozen.registration_id))
    assert len(results) == len(calls) == 1
    assert all(isinstance(result, OracleResult) for result in results)
    assert "after_tests" not in results[0].model_dump()
    path.write_text(json.dumps(payload) + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        evaluate_sealed(path, ref, case, policy=policy, workspace=tmp_path,
                        registration=(registration_path, frozen.registration_id))


def test_acceptance_identity_cannot_be_claimed_by_a_role_or_function_name():
    from g6.distill.held_out import sealed_gate
    with pytest.raises(PermissionError, match="G6_E_DISTILL_SEALED_ACCESS"):
        sealed_gate._require_evaluator()
