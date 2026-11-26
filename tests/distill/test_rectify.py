# SPDX-License-Identifier: Apache-2.0
"""Class removal is evidence-backed, append-only, and invalidated by recurrence."""
import importlib
import json

import pytest

from g6.core.ledger import LedgerCorrupt, LedgerError, digest
from g6.distill.ledger import Stores
from g6.distill.records import CapabilityExtension, FailureMode, FailureRecord, Rectification
from . import test_intake
from .test_intake import context, event

local_home = test_intake.local_home


D = digest("gate-receipt")


def workflow():
    return importlib.import_module("g6.distill.rectify_gate").Rectifier()


def failures(count=2):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    return [event(intake, "refusal", context(f"e{i}", round=i + 1)) for i in range(count)]


def rect(rows, **changes):
    return Rectification(**(dict(failure_mode="boundary_case", instance_or_class="class",
        change=dict(kind="code", branch="fix", commit="fixed", pr=None),
        prediction=dict(prediction_id="remove-class", measure="recurrences", before=2, expected=0),
        gate_decision_sha256=D, class_removed=True, outcome="held", rollback_ref=None,
        removal="Use one shared bounds check for every caller",
        pinning=dict(test_ref="tests/test_bounds.py::test_all_bounds", replayed=[r.record_id for r in rows],
                     fixed_passed=True, receipt_sha256=digest("replay-run"))) | changes))


def test_met_before_refuses_an_instance_patch_even_for_the_first_record(local_home):
    rows = failures()
    with pytest.raises(LedgerError, match="class"):
        workflow().submit(rows[-1].record_id, rect(rows, instance_or_class="instance"))
    with pytest.raises(LedgerError, match="class"):
        workflow().submit(rows[0].record_id, rect(rows, instance_or_class="instance"))
    assert Stores().for_model(Rectification).list() == []


@pytest.mark.parametrize("change", [dict(pinning=None), dict(removal=""),
    dict(pinning=dict(test_ref="", replayed=[], fixed_passed=True, receipt_sha256=D))])
def test_rectification_without_a_named_test_or_removal_is_refused(local_home, change):
    rows = failures()
    with pytest.raises(ValueError, match="pinning|test|removal|length|pattern"):
        workflow().submit(rows[-1].record_id, rect(rows, **change))
    assert Stores().for_model(Rectification).list() == []


def test_incomplete_replay_or_failed_fixed_code_cannot_close_class(local_home):
    rows = failures()
    for proof in (dict(test_ref="tests/test_bounds.py", replayed=[rows[0].record_id], fixed_passed=True,
                       receipt_sha256=D),
                  dict(test_ref="tests/test_bounds.py", replayed=[r.record_id for r in rows], fixed_passed=False,
                       receipt_sha256=D)):
        result = workflow().submit(rows[-1].record_id, rect(rows, pinning=proof))
        assert result.outcome == "open" and result.class_removed is False
        assert workflow().mode("boundary_case").removing_check is None


def test_recurrence_reopens_after_restart_and_logs_removing_check_failure(local_home):
    rows = failures()
    result = workflow().submit(rows[-1].record_id, rect(rows))
    assert result.class_removed and result.outcome == "held"
    assert workflow().mode("boundary_case").removing_check == result.rect_id
    mode_store = Stores().for_model(FailureMode)
    rect_store = Stores().for_model(Rectification)
    before_modes, before_rects = mode_store.path.read_bytes(), rect_store.path.read_bytes()
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    event(intake, "refusal", context("recurrence", round=3))
    assert mode_store.path.read_bytes().startswith(before_modes)
    assert rect_store.path.read_bytes().startswith(before_rects)
    mode = workflow().mode("boundary_case")
    assert mode.occurrences == 3 and mode.met_before and mode.removing_check is None
    assert mode.recurrences_after_removal == 1
    latest = rect_store.list()[-1]
    assert (latest.outcome, latest.class_removed) == ("failed", False)
    assert latest.pinning == result.pinning  # names the check invalidated by the recurrence
    event(intake, "refusal", context("recurrence", round=3))
    assert len(rect_store.list()) == 2


def test_no_write_on_wrong_mode_or_unknown_replay_reference(local_home):
    rows = failures()
    with pytest.raises(LedgerError, match="class|mode"):
        workflow().submit(rows[-1].record_id, rect(rows, failure_mode="state_not_durable"))
    with pytest.raises(LedgerError, match="replay"):
        workflow().submit(rows[-1].record_id, rect(rows, pinning=dict(test_ref="tests/test_bounds.py",
            replayed=[digest("missing")], fixed_passed=True, receipt_sha256=D)))
    assert Stores().for_model(Rectification).list() == []


def test_capability_extensions_use_records_store_and_need_a_gap(local_home):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    row = FailureRecord(kind="capability_gap", family="g6_self", source="person", observed_by="self",
        case_ref=D, split="monitor", evidence=[], finding_class="boundary_case", met_before=[],
        g6_commit="abc", observed_at="2026-11-18", provenance={})
    gap = intake.record(row, context("gap"))
    extension = CapabilityExtension(kind="component_op", requested_by="person", gap_record=gap.record_id,
        summary="check a new file format", artefact_ref="proposal", evidence_ref=D, certificate_ref=D,
        status="open", scope="local")
    assert workflow().extend(extension) == extension
    assert workflow().extend(extension) == extension
    assert Stores().for_model(CapabilityExtension).list() == [extension]
    wrong = failures(1)[0]
    with pytest.raises(LedgerError, match="gap|request"):
        workflow().extend(CapabilityExtension(**(extension.model_dump(exclude={"ext_id"}) |
                                               {"gap_record": wrong.record_id})))


@pytest.mark.parametrize("model", [FailureRecord, FailureMode, Rectification, CapabilityExtension])
def test_forged_rows_refuse_read_and_append(local_home, model):
    rows = failures()
    workflow().submit(rows[-1].record_id, rect(rows))
    if model is CapabilityExtension:
        # Populate this log through its own API using a real gap record.
        test_capability_extensions_use_records_store_and_need_a_gap(local_home)
    store = Stores().for_model(model)
    lines = store.path.read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["body"]["forged"] = True
    lines[0] = json.dumps(forged)
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(LedgerCorrupt):
        store.list()
    with pytest.raises(LedgerCorrupt):
        workflow().reconcile()


def test_interrupted_mode_append_heals_on_event_replay(local_home, monkeypatch):
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    from g6.distill.ledger import RecordStore
    put = RecordStore.put
    def interrupted(store, row):
        if type(row) is FailureMode:
            raise RuntimeError("simulated interruption")
        return put(store, row)
    with monkeypatch.context() as patch:
        patch.setattr(RecordStore, "put", interrupted)
        with pytest.raises(RuntimeError, match="interruption"):
            event(intake, "refusal", context())
    assert len(Stores().for_model(FailureRecord).list()) == 1
    event(intake, "refusal", context())
    assert workflow().mode("boundary_case").occurrences == 1
    assert len(Stores().for_model(FailureMode).list()) == 1


def test_claimed_removal_without_pinning_is_refused_by_the_records_store(local_home):
    rows = failures()
    forged = rect(rows).model_copy(update={"rect_id": None, "pinning": None, "removal": None})
    with pytest.raises(LedgerError, match="pinning"):
        Stores().for_model(Rectification).put(forged)


def test_successive_removals_retain_the_total_recurrence_count(local_home):
    rows = failures()
    workflow().submit(rows[-1].record_id, rect(rows))
    intake = importlib.import_module("g6.distill.intake").FailureIntake()
    rows.append(event(intake, "refusal", context("recurrence-1", round=3)))
    workflow().submit(rows[-1].record_id, rect(rows))
    assert workflow().mode("boundary_case").recurrences_after_removal == 1
    assert workflow().mode("boundary_case").removing_check is not None
    rows.append(event(intake, "refusal", context("recurrence-2", round=4)))
    assert workflow().mode("boundary_case").recurrences_after_removal == 2
    assert workflow().mode("boundary_case").removing_check is None
    assert Stores().for_model(Rectification).list()[-1].outcome == "failed"


def library_check():
    from g6.distill.records import Concept, LibraryEntry, MethodContract, VerifierCandidate
    from .test_records import bodies
    failure = FailureRecord(**bodies()[0][1])
    concept = Concept(**bodies()[1][1])
    contract = MethodContract(**(bodies()[2][1] | {"family": "g6_self"}))
    candidate = VerifierCandidate(**(bodies()[3][1] | {"contract": contract}))
    entry = LibraryEntry(**(bodies()[4][1] | {"family": "g6_self", "candidate_id": candidate.candidate_id,
                                           "contract_sha256": contract.content_id}))
    for value in (failure, concept, contract, candidate, entry):
        Stores().for_model(type(value)).put(value)
    return entry


def test_recurrence_demotes_a_library_removing_check(local_home):
    from g6.distill.library import Library
    entry = library_check()
    rows = failures()
    # Include the independently stored fixture failure of the same class in the replay.
    rows = Stores().for_model(FailureRecord).list()
    proof = rect(rows).pinning.model_dump() | {"test_ref": f"library:{entry.entry_id}"}
    result = workflow().submit(rows[-1].record_id, rect(rows, pinning=proof))
    assert result.class_removed
    event(importlib.import_module("g6.distill.intake").FailureIntake(), "refusal", context("again", round=3))
    latest, = Library().list()
    assert latest.status == "held" and latest.authority == "A0"
    assert latest.version == entry.version + 1
    assert workflow().mode("boundary_case").removing_check is None
    assert workflow().mode("boundary_case").recurrences_after_removal == 1
    assert Library().by_status("admitted") == []
    from g6.distill.acceptance_gate import _active
    assert _active(Library().list()) == []


def test_foreign_class_replay_is_rejected_by_records_store(local_home):
    rows = failures()
    foreign = event(importlib.import_module("g6.distill.intake").FailureIntake(), "refusal",
                    context("foreign", finding_class="state_not_durable"))
    proof = rect(rows).pinning.model_dump() | {"replayed": [foreign.record_id]}
    with pytest.raises(LedgerError, match="class|mode|replay"):
        Stores().for_model(Rectification).put(rect(rows, pinning=proof))


@pytest.mark.parametrize("attempt", ["instance", "incomplete", "fixed_failed"])
def test_records_store_cannot_bypass_class_rule_or_complete_replay(local_home, attempt):
    rows = failures()
    store = Stores().for_model(Rectification)
    changes = {"instance": dict(instance_or_class="instance", class_removed=False, outcome="open"),
               "incomplete": dict(pinning=rect(rows).pinning.model_dump() | {"replayed": [rows[0].record_id]}),
               "fixed_failed": dict(pinning=rect(rows).pinning.model_dump() | {"fixed_passed": False})}
    with pytest.raises(LedgerError, match="class|fixed|replay"):
        store.put(rect(rows, **changes[attempt]))


def test_mode_snapshot_cannot_reference_an_unknown_removal(local_home):
    with pytest.raises(LedgerError, match="reference"):
        Stores().for_model(FailureMode).put(FailureMode(mode_code="boundary_case", first_seen="now",
            occurrences=1, met_before=False, removing_check=D, recurrences_after_removal=0))


def test_even_an_open_new_rectification_needs_a_pinning_test(local_home):
    rows = failures()
    unpinned = rect(rows, class_removed=False, outcome="open", removal=None, pinning=None)
    with pytest.raises(LedgerError, match="pinning"):
        Stores().for_model(Rectification).put(unpinned)


def test_legacy_open_rows_remain_readable_with_original_identity(local_home):
    from g6.core.ledger import canonical
    from .test_records import bodies
    body = bodies()[-1][1].copy()
    body.pop("removal", None)
    body.pop("pinning", None)
    legacy = Rectification(**body)
    assert legacy.rect_id == digest(body)
    store = Stores().for_model(Rectification)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    obj = store.path.parent / "objects" / f"{legacy.rect_id}.json"
    obj.parent.mkdir()
    obj.write_text(canonical(legacy.model_dump(mode="json")) + "\n", encoding="utf-8", newline="\n")
    store.chain.append("rectification", legacy.rect_id, legacy.model_dump(mode="json"))
    assert store.list() == [legacy]


def test_missing_library_pin_is_a_ledger_refusal(local_home):
    rows = failures()
    proof = rect(rows).pinning.model_dump() | {"test_ref": f"library:{D}"}
    with pytest.raises(LedgerError, match="reference"):
        workflow().submit(rows[-1].record_id, rect(rows, pinning=proof))
