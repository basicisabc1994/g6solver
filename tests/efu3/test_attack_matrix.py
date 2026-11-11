"""Class-by-class controls; all observations and models are local values."""
import uuid
import pytest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from g6.core.ledger import ChainLedger
from g6.evidence import mapping as m
from g6.harness import patch_report, patch_review as review
from g6.research.echa.view import certificate_view
from g6.workforce import repo_tests
from tests.evidence.test_an_observed_failure_reaches_the_issuer import (
    DECISIVE, FACTS, FLAKY_FAIL, REFUSED, SUBJECT,
)
from tests.harness.efu2_cases import FAILED, NODE, PASSED, assess_runs, report
from tests.harness.test_producer_report_paths import counted_writer
from tests.research.echa.test_echa_view_counts_a_receipt_for_its_own_bytes import (
    COUNTED, PLANTED, seen,
)


@pytest.fixture
def tmp_path():
    folder = Path.cwd() / ".review" / "probes" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    return folder


def test_identity_from_rendering_and_generated_id_boundaries():
    assert m.id_of("a:b", "c") != m.id_of("a", "b:c")
    assert m.id_of("x", 1) != m.id_of("x", "1")
    for field in ("record_id", "check_id"):
        taken = m.id_of("caveat", 0, FLAKY_FAIL.record_id)
        good = replace(DECISIVE, **{field: taken})
        for records in ([good, FLAKY_FAIL], [FLAKY_FAIL, good]):
            got = m.issue_records(FACTS, SUBJECT, records)
            assert len(got.refused) == len(got.caveats) == 1
            assert got.caveats[0][1] != taken
            assert got.certificate.predicates[0].caveats


def test_one_fact_two_rules_for_supported_phase_patterns(monkeypatch):
    for phases, expected in [
        (PASSED, "passed"), (FAILED, "failed"),
        ({"setup": "failed", "teardown": "passed"}, "error"),
        ({**PASSED, "teardown": "failed"}, "error"),
        ({**PASSED, "call": "skipped"}, "skipped"),
    ]:
        raw = report(phases)
        assert repo_tests.read_report(raw).valid
        assert review.body_of(review.phases_of(raw)[NODE]) == expected
        base_result = assess_runs(monkeypatch, raw, report(PASSED))
        assert base_result["requirements"][0]["checked"] is (expected == "failed")
        checks = review._verdicts([NODE], raw)
        assert review._observed([NODE], raw)[NODE] == review.observation(checks[NODE])


def test_own_parser_for_foreign_format_and_phase_order(tmp_path):
    path = tmp_path / "counted.json"
    producer = counted_writer(path)
    item = SimpleNamespace(nodeid=NODE)
    producer.pytest_itemcollected(item)
    producer.pytest_collection_finish(SimpleNamespace(items=[item], config=SimpleNamespace(rootpath="", rootdir="")))
    for phase, outcome in FAILED.items():
        producer.pytest_runtest_logreport(SimpleNamespace(nodeid=NODE, when=phase, outcome=outcome,
                                                        failed=outcome == "failed", longrepr="AssertionError"))
    progress = repo_tests._progress({"passed": 0, "failures": 1, "errors": 0}, path)
    projected = {key: value for key, value in progress.items() if key in repo_tests.REPORT_FIELDS}
    raw = {**projected, "verdict": "failed", "said": "", "output": ""}
    assert repo_tests.read_report(raw).valid
    assert review.body_of(review.phases_of(raw)[NODE]) == "failed"
    raw["test_outcomes"][NODE] = dict(reversed(list(FAILED.items())))
    assert not repo_tests.read_report(raw).valid


def test_failure_statuses_partial_types_and_guard_inventory(monkeypatch):
    for wrong in (None, False, 1, [], "broken", {NODE: {"call": "passed"}},
                  {NODE: {"setup": "passed", "call": "failed"}},
                  {NODE: {"setup": "new", "call": "failed", "teardown": "passed"}}):
        raw = report(FAILED, test_outcomes=wrong)
        assert not repo_tests.read_report(raw).valid
        assert assess_runs(monkeypatch, raw, report(PASSED))["requirements"][0]["checked"] is False
    for root in (None, False, 1, [], {}):
        raw = report(FAILED, rootdir=root)
        assert not repo_tests.read_report(raw).valid
        assert assess_runs(monkeypatch, raw, report(PASSED))["requirements"][0]["checked"] is False
    for field in ("failed_ids", "setup_errors", "collected_ids", "omitted_ids"):
        raw = report(PASSED)
        other = "test_calc.py::test_other"
        raw[field] = {other: "failure"} if field == "setup_errors" else [other]
        assert not repo_tests.read_report(raw).valid
        assert review._observed([NODE], raw) == {NODE: "error"}


def test_check_then_act_snapshot_detaches_mutable_inputs():
    raw = report(FAILED)
    snapshot = repo_tests.read_report(raw)
    raw["test_outcomes"][NODE]["call"] = "passed"
    raw["failed_ids"].clear()
    assert snapshot.phases[NODE].body() == "failed"
    assert snapshot.wire()["test_outcomes"][NODE]["call"] == "failed"


def test_state_not_durable_ledger_round_trip(tmp_path):
    issued = m.issue_records(FACTS, SUBJECT, [DECISIVE, FLAKY_FAIL])
    path = tmp_path / "ledger.jsonl"
    writer = ChainLedger(path)
    for entry in issued.ledger.entries():
        writer.append(entry["kind"], entry["id"], entry["body"])
    restarted = ChainLedger(path, expect_head=writer.head())
    assert restarted.verify() == ()
    caveat_id = issued.caveats[0][1]
    assert restarted.get("provenance", caveat_id)["refused"]
    assert restarted.get("receipt", caveat_id)["result"] == "fail"


def test_untrusted_fields_never_replace_an_unreadable_binding():
    lied = replace(FLAKY_FAIL, binding=m.UnreadableBinding("broken"))
    out = m.issue_records(FACTS, SUBJECT, [DECISIVE, lied])
    assert out.refused and not out.caveats
    assert m.about(lied)[0].artefact == ""
    for binding in ({"broken": "binding"}, "not a binding", False, 1, []):
        roles, _ = seen(COUNTED, provenance={COUNTED.receipt_id: {"binding": binding}})
        assert roles[COUNTED.receipt_id] == ("not counted", "binding_unreadable")


def test_refused_failures_for_every_code_and_no_caveat_lends_authority():
    for code, records in REFUSED.values():
        out = m.issue_records(FACTS, SUBJECT, [DECISIVE, *records])
        assert code in {code for _, codes in out.refused for code, _ in codes}
        assert out.caveats and not out.certificate.predicates[0].verified
        assert not out.certificate.predicates[0].refute
    for over in ({"artefact_sha256": "b" * 64}, {"predicates": ("R2",)}):
        out = m.issue_records(FACTS, SUBJECT, [DECISIVE, replace(FLAKY_FAIL, **over)])
        assert out.refused and not out.caveats
        assert out.certificate.predicates[0].verified
    out = m.issue_records(FACTS, SUBJECT, [DECISIVE, FLAKY_FAIL], not_believed="untrusted run")
    assert len(out.refused) == 2 and not out.promoted
    assert out.certificate.predicates[0].caveats and not out.certificate.predicates[0].support


def test_view_gates_and_status_copy():
    for gate, (receipts, checks, held) in PLANTED.items():
        roles, statement = seen(*receipts, checks=checks, held=held)
        assert roles["planted"] == ("not counted", gate)
    issued = m.issue_records(FACTS, SUBJECT, [DECISIVE, FLAKY_FAIL])
    body = issued.certificate.as_dict()
    view = certificate_view(body, artifact_sha256=SUBJECT.sha256, ledger=issued.ledger)
    expected = {key: value for key, value in body["predicates"][0].items() if key != "predicate"}
    assert view["predicates"][0]["status"] == expected
    caveats = [row for row in view["predicates"][0]["evidence"] if row["role"] == "caveat"]
    assert len(caveats) == 1


def test_report_does_not_claim_a_fixture_error_killed_the_base_control():
    assessment = {"scope": {"checks_run": [{"check": review.AT_BASE,
                    "outcomes": {NODE: "failed"}, "observations": {NODE: "error"}}]},
                  "completion": {"demonstrated": [{"requirement": "R1", "checks": [NODE]}]}}
    assert "fails at the base commit" not in str(patch_report._shown(assessment))
    assert "fail at the base commit" not in str(patch_report._steps(assessment))



def test_boundary_case_numeric_limits_and_empty_inputs():
    import sys
    assert repo_tests.read_report(report(PASSED, seconds=0.0)).valid
    for wrong in (-1.0, float("nan"), float("inf")):
        assert not repo_tests.read_report(report(PASSED, seconds=wrong)).valid
    for field in ("passed", "failed", "total", "deselected", "collected", "started", "finished"):
        for wrong in (-1, sys.maxsize, sys.maxsize + 1):
            assert not repo_tests.read_report(report(PASSED, **{field: wrong})).valid
    assert not repo_tests.read_report({}).valid
    assert m.issue_records(FACTS, SUBJECT, []).certificate.predicates[0].support is False
    assert m.issue_records(FACTS, SUBJECT, [DECISIVE]).certificate.predicates[0].support is True

