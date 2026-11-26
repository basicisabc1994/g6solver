# SPDX-License-Identifier: Apache-2.0
"""Pure boundary checks, separate from filesystem/runner integration tests."""
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from g6.distill.held_out.case_gate import CaseRecord, Provenance
from g6.distill.held_out.sealed_gate import _Payload


def test_sealed_payload_carries_patterns_instead_of_public_operator_names():
    payload = _Payload.model_validate({
        "version": 1, "author_family": "claude", "training_family": "codex", "authorship_sha256": "a" * 64,
        "operators": [{"name": "synthetic-example", "before": "assert answer(1) == 2", "after": "pass",
                       "occurrence": -1}],
    })
    assert payload.operators[0].before == "assert answer(1) == 2"


@pytest.mark.parametrize("change", [{"author_family": "codex"}, {"authorship_sha256": "bad"},
                                    {"operators": [{"name": "bad", "before": "", "after": "pass"}]}])
def test_sealed_payload_rejects_same_family_missing_authorship_and_empty_pattern(change):
    data = {"version": 1, "author_family": "claude", "training_family": "codex", "authorship_sha256": "a" * 64,
            "operators": [{"name": "synthetic-example", "before": "assert x", "after": "pass", "occurrence": -1}]}
    with pytest.raises(ValidationError):
        _Payload.model_validate(data | change)


def test_case_file_prefix_collision_is_rejected_before_staging():
    with pytest.raises(ValidationError, match="conflict"):
        CaseRecord(unit_id="synthetic", provenance=Provenance(source="round3", cohort="development",
                   source_ref="synthetic", source_sha256="a" * 64),
                   files={"a.py": "pass\n", "a.py/b.py": "pass\n"}, target="a.py", changed_lines=(1,),
                   before_tests="def test_it():\n    assert True\n", after_tests="def test_it():\n    assert True\n",
                   known_wrong=("pass\n",))


def test_public_package_and_operators_do_not_import_judging_or_sealed_data():
    root = Path(__file__).resolve().parents[2] / "src/g6/distill"
    for name in ("__init__.py", "operators.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "held_out" not in (node.module or "")
            elif isinstance(node, ast.Import):
                assert all("held_out" not in alias.name for alias in node.names)


def test_all_new_judging_paths_are_protected_by_existing_gate():
    from g6.core.adaptation_gate import protected_hits
    files = ["src/g6/distill/held_out/" + name for name in
             ("oracle.py", "case_gate.py", "registration_gate.py", "sealed_gate.py")]
    assert set(protected_hits(files)) == set(files)


def test_real_evaluator_module_identity_reaches_registration_before_payload(monkeypatch):
    import sys
    from types import ModuleType
    from g6.distill.held_out import sealed_gate

    class RegistrationReached(Exception):
        pass

    def registration(*args, **kwargs):
        raise RegistrationReached

    module = ModuleType("g6.distill.acceptance_gate")
    module.__dict__["evaluate_sealed"] = sealed_gate.evaluate_sealed
    code = compile("def evaluate():\n    return evaluate_sealed(None, None, None, policy=None, "
                   "workspace=None, registration=(None, None))\n",
                   str(sealed_gate._EVALUATORS[module.__name__]), "exec")
    # Only trusted synthetic test scaffolding executes in process; no operator
    # source, repository tests, model output or corpus code is executed here.
    exec(code, vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sealed_gate, "read_registration", registration)
    with pytest.raises(RegistrationReached):
        module.evaluate()


def test_wrong_filename_cannot_impersonate_an_evaluator(monkeypatch):
    import sys
    from types import ModuleType
    from g6.distill.held_out import sealed_gate

    module = ModuleType("g6.distill.acceptance_gate")
    module.__dict__["evaluate_sealed"] = sealed_gate.evaluate_sealed
    exec("def evaluate():\n    return evaluate_sealed(None, None, None, policy=None, "
         "workspace=None, registration=(None, None))\n", vars(module))
    monkeypatch.setitem(sys.modules, module.__name__, module)
    with pytest.raises(PermissionError, match="G6_E_DISTILL_SEALED_ACCESS"):
        module.evaluate()
