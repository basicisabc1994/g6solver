# SPDX-License-Identifier: Apache-2.0
"""Readiness comes from executed checks and paired, reserved case runs."""
import pytest

from g6.harness import Check, Memory, Standard, evaluate_cases, readiness_report, run


def _standard(check=None):
    return Standard("Summarise the refund policy", (check or Check("states the right value", lambda o: o == "42",
                    known_bad=("41",), known_good="42"),), family="readiness")


def test_domain_workflow_02_measures_controls_cases_and_baseline(tmp_path):
    # Removal makes this fail: `memory._remember_evaluation(standard.family_key, frozen, candidate, baseline, stable)` in evaluate_cases.
    path = tmp_path / "harness.db"
    memory = Memory(path)
    try:
        batch = evaluate_cases(_standard(), {"unseen-a": "policy A", "unseen-b": "policy B"},
                               lambda c, case: "42", baseline_executor=lambda c, case: "41", memory=memory)
        assert batch["completion_state"] == "qualified-draft"
    finally:
        memory.close()
    memory = Memory(path)
    try:
        report = readiness_report("readiness", memory=memory)
        assert report["checks_frozen"] and report["known_bad_tested"]
        assert report["held_out_pass_rate"] == 1.0 and report["baseline_pass_rate"] == 0.0
        assert report["baseline_compared"] and report["outperforms_baseline"]
        assert report["blocking_failures_observed"] == 0 and report["tier"] == "publishable"
        assert report["completion_state"] == "qualified-draft"
    finally:
        memory.close()


def test_domain_workflow_01_d1_a_lax_check_cannot_claim_readiness():
    # Removal makes this fail: `all(r["plan"]["known_bad_results"][k]) for k in r["plan"]["checks"])` in readiness_report.
    memory = Memory(":memory:")
    try:
        lax = Check("states the right value", lambda o: True, known_bad=("wrong total",))
        evaluate_cases(_standard(lax), {"new": "policy C"}, lambda c, case: "42", memory=memory)
        report = readiness_report("readiness", memory=memory)
        assert report["known_bad_tested"] is False
        assert report["blocking_failures_observed"] == 1
        assert report["tier"] == "none" and report["completion_state"] != "verified"
        with pytest.raises(TypeError):
            readiness_report("readiness", memory=memory, checks_frozen=True, pass_rate=1.0)
    finally:
        memory.close()


def test_domain_workflow_03_unmeasured_is_not_zero():
    # Removal makes this fail: `blocking_failures_observed=blocked if observed else None` in readiness_report.
    memory = Memory(":memory:")
    try:
        report = readiness_report("readiness", memory=memory)
        assert report["held_out_pass_rate"] is None and report["blocking_failures_observed"] is None
        assert report["baseline_pass_rate"] is None and report["tier"] == "none"
    finally:
        memory.close()


def test_readiness_does_not_reuse_a_batch_after_the_standard_changes():
    # Removal makes this fail: `if batch and batch["standard_sha"] != latest.get("standard_sha"):`.
    memory = Memory(":memory:")
    try:
        evaluate_cases(_standard(), {"case": "policy D"}, lambda c, case: "42", memory=memory)
        changed = Check("has a source", lambda o: "source" in o, known_bad=("no citation",))
        run(_standard(changed), lambda c: "source: policy", memory=memory)
        report = readiness_report("readiness", memory=memory)
        assert report["held_out_pass_rate"] is None and report["baseline_compared"] is False
    finally:
        memory.close()


def test_reserved_cases_cannot_reuse_the_controls():
    # Removal makes this fail: `or len(set(cases.values())) != len(cases) or any(value in controls for value in cases.values())):` in evaluate_cases.
    memory = Memory(":memory:")
    try:
        with pytest.raises(ValueError, match="G6_E_HARNESS_CASES"):
            evaluate_cases(_standard(), {"seen": "41"}, lambda c, case: "42", memory=memory)
        assert memory.runs("readiness") == []
    finally:
        memory.close()


def test_a_later_failed_run_invalidates_earlier_readiness():
    # Removal makes this fail: `if latest.get("run_id") not in batch_ids:` in readiness_report.
    memory = Memory(":memory:")
    try:
        evaluate_cases(_standard(), {"new": "policy E"}, lambda c, case: "42", memory=memory)
        run(_standard(), lambda c: "47", memory=memory)
        report = readiness_report("readiness", memory=memory)
        assert report["held_out_pass_rate"] is None and report["blocking_failures_observed"] == 1
        assert report["tier"] != "publishable"
    finally:
        memory.close()


def test_stopped_evaluation_records_no_case_batch(monkeypatch):
    # Removal makes this fail: `if stop.engaged() is not None:` in evaluate_cases.
    monkeypatch.setenv("G6_STOP", "pause")
    memory = Memory(":memory:")
    report = evaluate_cases(_standard(), {"new": "policy F"}, lambda c, case: "42", memory=memory)
    assert report["completion_state"] == "blocked-escalated" and memory._conn is None
