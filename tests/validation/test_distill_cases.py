# SPDX-License-Identifier: Apache-2.0
"""Synthetic contract fixtures, never a source for the registered study."""
import json

import pytest
from g6.core.ledger import digest
from pydantic import ValidationError

from g6.distill.held_out.case_gate import CaseRecord, Provenance, cut, training_view
from g6.distill.held_out.registration_gate import freeze, read_registration


def case(unit="bug-1", cohort="development", **changes):
    values = dict(
        unit_id=unit,
        provenance=Provenance(source="round3", cohort=cohort, source_ref="synthetic-planted",
                              source_sha256="a" * 64),
        files={"subject.py": "def answer(x):\n    return x + 1\n"},
        target="subject.py", changed_lines=(2,),
        before_tests="from subject import answer\ndef test_answer():\n    assert answer(1) == 2\n",
        after_tests="from subject import answer\ndef test_answer():\n    assert answer(1) == 2\n",
        known_wrong=("def answer(x):\n    return x\n",),
    )
    return CaseRecord(**(values | changes))


def test_cut_deterministic_order_independent_and_grouped_by_bug():
    cases = [case(f"bug-{i}") for i in range(200)]
    cases += [case(f"bug-{i}", after_tests=case().after_tests + "\n# another case\n") for i in range(20)]
    first = cut(cases, seed="registered-1")
    assert first == cut(list(reversed(cases)), seed="registered-1")
    assert first != cut(cases, seed="registered-2")
    assert set(first.values()) == {"tuning", "acceptance", "monitor", "reserve"}
    for unit in {c.unit_id for c in cases}:
        assert len({first[c.case_id] for c in cases if c.unit_id == unit}) == 1


def test_selection_is_unseen_and_never_in_training_view():
    cases = [case(f"dev-{i}") for i in range(50)] + [case("selection-1", "selection")]
    split = cut(cases, seed="registered")
    assert split[cases[-1].case_id] == "unseen"
    view = training_view(cases, seed="registered")
    assert view
    assert all(split[c.case_id] == "tuning" for c in view)
    assert all(c.provenance.cohort == "development" for c in view)


def test_duplicate_and_conflicting_unit_cohorts_refused():
    with pytest.raises(ValueError, match="duplicate"):
        cut([case(), case()], seed="s")
    with pytest.raises(ValueError, match="cohort"):
        cut([case(), case(cohort="selection")], seed="s")


def test_case_ids_are_canonical_body_digests_and_extra_fields_refused():
    planted = case()
    assert planted.case_id == digest(planted.model_dump(mode="json"))
    with pytest.raises(ValidationError):
        case(label="positive")


@pytest.mark.parametrize("changes", [dict(cohort="final"), dict(source="V7"), dict(amended=True),
                                           dict(source_ref="tests/safety_engine/test_weakening_se005.py")])
def test_excluded_sources_refused(changes):
    values = case().provenance.model_dump() | changes
    with pytest.raises(ValidationError):
        Provenance(**values)


@pytest.mark.parametrize("target", ["../escape.py", "C:/escape.py", "subject.py::bad", "conftest.py"])
def test_case_paths_are_bounded(target):
    with pytest.raises(ValidationError):
        case(target=target, files={target: "pass\n"})


def registration(tmp_path):
    path = tmp_path / "registration.json"
    frozen = freeze(path, [case(f"bug-{i}") for i in range(20)], seed="frozen-seed",
                    operator_digests={"training": "b" * 64, "legitimate": "c" * 64, "held_out": "d" * 64},
                    policy={"min_mutants": 30, "max_mutants": 128},
                    study={"commit": "e" * 40, "model_ids": {}, "prompts": {}, "seeds": [1]})
    return path, frozen


def test_registration_freezes_digest_cut_policy_and_operator_refs(tmp_path):
    path, frozen = registration(tmp_path)
    assert read_registration(path, expected_digest=frozen.registration_id) == frozen
    assert frozen.manifest_sha256 == digest([c.model_dump(mode="json") for c in frozen.cases])
    assert frozen.operator_digests["held_out"] == "d" * 64
    assert "before_tests" not in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        freeze(path, [case()], seed="s", operator_digests=frozen.operator_digests,
               policy={}, study={})
    body = json.loads(path.read_text(encoding="utf-8"))
    body["seed"] = "changed"
    path.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        read_registration(path, expected_digest=frozen.registration_id)


def test_registration_detects_changed_oracle_code(tmp_path, monkeypatch):
    from g6.distill.held_out import registration_gate
    path, frozen = registration(tmp_path)
    monkeypatch.setattr(registration_gate, "oracle_digest", lambda: "f" * 64)
    with pytest.raises(ValueError, match="oracle"):
        read_registration(path, expected_digest=frozen.registration_id)
