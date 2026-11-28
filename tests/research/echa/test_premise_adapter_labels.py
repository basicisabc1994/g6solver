# SPDX-License-Identifier: Apache-2.0
"""A transport adapter's label cannot hide a failure on a sealed fixture."""
from dataclasses import replace
from pathlib import Path
import json
import runpy

import pytest

from g6.evidence.mapping import BINDING_PREDICATE, NO_ENVIRONMENT
from g6.research.echa import LedgerError, trace_claim
from g6.research.echa.custody import CheckBinding, about, checked_evidence

H = runpy.run_path(str(Path(__file__).with_name("test_echa_kernel_claims.py")))


@pytest.mark.parametrize("label,declares_digest", [("unsealed", False), (H["FX"], True)])
def test_bound_nonindependent_seal_requirement_follows_digest_declaration(label, declares_digest):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    binding = CheckBinding(**json.loads(H["_binding"]("a")))
    rec = checked_evidence(json.dumps({**binding.to_dict(), "result": "pass"}).encode(), binding,
                           predicate_id="p-keep")
    rec = replace(rec, fixture_version=label)
    reading = about(rec)
    parts, refused = reading
    assert rec.independent is False
    assert parts.fixture == H["FX"]
    assert refused == ()
    assert reading.declares_fixture_digest is declares_digest
    if declares_digest:
        with pytest.raises(LedgerError, match="fixture_version names no sealed vault"):
            led.add_evidence(rec)
        assert [entry["kind"] for entry in led.entries()] == ["standard"]
    else:
        led.add_evidence(rec)
        assert led.get("evidence", rec.evidence_id) == rec.to_dict()
    assert led.verify() == ()


def test_ledger_refuses_an_independent_unsealed_bound_record_without_a_vault():
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    binding = CheckBinding(**json.loads(H["_binding"]("a")))
    rec = checked_evidence(json.dumps({**binding.to_dict(), "result": "pass"}).encode(), binding,
                           predicate_id="p-keep")
    # Present malformed serialized evidence to the real ledger admission path.
    # Normal construction already refuses this combination; admission must too.
    object.__setattr__(rec, "independent", True)
    object.__setattr__(rec, "independence_group", "author")
    assert rec.fixture_version == "unsealed"
    assert about(rec)[1] == ()
    with pytest.raises(LedgerError, match="independent record must name the sealed fixture"):
        led.add_evidence(rec)
    assert [entry["kind"] for entry in led.entries()] == ["standard"]
    assert led.verify() == ()


@pytest.mark.parametrize("result", ["pass", "fail", "unknown"])
@pytest.mark.parametrize("incident", [None, "access_denied", "fixture_modified"])
def test_unsealed_adapter_without_a_vault_is_retained_without_independent_support(result, incident):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    binding = CheckBinding(**json.loads(H["_binding"]("a")))
    rec = checked_evidence(json.dumps({**binding.to_dict(), "result": result}).encode(), binding,
                           predicate_id="p-keep")
    assert rec.fixture_version == "unsealed"
    assert rec.independent is False and rec.oracle_category == "PARTIAL_FALSIFIER"
    assert about(rec)[0].fixture == H["FX"]
    assert about(rec)[1] == ()
    led.add_evidence(rec)
    assert led.get("evidence", rec.evidence_id) == rec.to_dict()
    with pytest.raises(LedgerError, match="already"):
        led.add_evidence(rec)
    if incident is not None:
        led.add_incident(incident, fixture_digest=H["FX"])
    got = H["verdict"](led, subject="sha256:" + "b" * 64, evidence_ids=(rec.evidence_id,))
    assert got["result"] == "inconclusive"
    assert got["independent_groups"] == 0
    assert got["active_evidence"] == []
    assert got["identification"] == "not_identified"
    assert got["effective_breadth"] == "covered_cases"
    assert ("contaminated_fixture" in got["reasons"]) is (incident is not None)
    assert led.verify() == ()
    assert trace_claim(led, "c1")["verdict"] == got
    with pytest.raises(ValueError, match="independent record must name the sealed fixture"):
        replace(rec, independent=True, independence_group="author")


@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.parametrize("seal", [None, "other", "matching"])
def test_a_declared_fixture_digest_still_requires_its_own_seal(independent, seal):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    binding = CheckBinding(**json.loads(H["_binding"]("a")))
    rec = checked_evidence(json.dumps({**binding.to_dict(), "result": "pass"}).encode(), binding,
                           predicate_id="p-keep")
    rec = replace(rec, fixture_version=H["FX"], independent=independent, independence_group="author")
    if seal is not None:
        led.add_vault("v1", H["FX"] if seal == "matching" else "e" * 64, files=1, roots=1)
    assert about(rec)[1] == ()
    if seal == "matching":
        led.add_evidence(rec)
        assert led.verify() == ()
    else:
        with pytest.raises(LedgerError, match="fixture_version names no sealed vault"):
            led.add_evidence(rec)


@pytest.mark.parametrize("result,want", [("pass", "supported"), ("fail", "unsupported"), ("unknown", "supported")])
@pytest.mark.parametrize("label", ["unsealed", H["FX"]])
def test_adapter_label_cannot_change_a_bound_outcome(result, want, label):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    led.add_evidence(H["control"]("ctl", source_ref=H["_binding"]("a")))
    led.add_evidence(H["ev"]("e1", source_ref=H["_binding"]("a")))
    binding = CheckBinding(**json.loads(H["_binding"]("a")))
    rec = checked_evidence(json.dumps({**binding.to_dict(), "result": result}).encode(), binding,
                           predicate_id="p-keep", negative_control_ref="ctl")
    rec = replace(rec, fixture_version=label)
    led.add_evidence(rec)
    assert about(rec)[1] == ()
    got = H["verdict"](led, subject="sha256:" + "b" * 64, evidence_ids=("e1", rec.evidence_id))
    assert got["result"] == want
    if result in ("pass", "fail"):
        assert rec.evidence_id in got["active_evidence"]
    assert led.verify() == ()
    assert trace_claim(led, "c1")["verdict"] == got


@pytest.mark.parametrize("category", ["FORMAL_CHECK", "INDEPENDENT_EXECUTABLE", "PARTIAL_FALSIFIER"])
@pytest.mark.parametrize("placement,code", [("fixture", NO_ENVIRONMENT), ("predicate", BINDING_PREDICATE),
                                           ("unbound", "evidence_not_bound_to_subject")])
def test_a_failure_with_unresolved_custody_blocks_a_passing_check(category, placement, code):
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    led.add_evidence(H["control"]("ctl", source_ref=H["_binding"]("a")))
    led.add_evidence(H["ev"]("e1", source_ref=H["_binding"]("a")))
    binding = json.loads(H["_binding"]("a"))
    if placement == "fixture":
        binding["fixture_sha256"] = "e" * 64
    elif placement == "predicate":
        binding["predicate_id"] = "other"
    source = "log:unbound" if placement == "unbound" else H["canonical"](binding)
    led.add_evidence(H["control"]("failure-ctl", source_ref=source))
    failure = H["ev"]("failure", result="fail", oracle_category=category, source_ref=source,
                       negative_control_ref="failure-ctl")
    led.add_evidence(failure)
    got = H["verdict"](led, subject="sha256:" + "b" * 64, evidence_ids=("e1", "failure"))
    assert got["result"] == "inconclusive"
    assert code in got["reasons"]
    assert "unexplained_failure" in got["reasons"]
    assert "failure" in got["leads"]
    assert "failure" not in got["active_evidence"]
    assert led.verify() == ()
    assert trace_claim(led, "c1")["verdict"] == got


def test_a_failure_placed_on_other_bytes_does_not_refute_this_subject():
    led = H["Ledger"]()
    led.add_standard(H["STD"])
    led.add_vault("sealed", H["FX"], files=1, roots=1)
    led.add_evidence(H["control"]("ctl", source_ref=H["_binding"]("a")))
    led.add_evidence(H["ev"]("e1", source_ref=H["_binding"]("a")))
    led.add_evidence(H["control"]("other-ctl", source_ref=H["_binding"]("a", "c")))
    led.add_evidence(H["ev"]("other", result="fail", source_ref=H["_binding"]("a", "c"),
                            negative_control_ref="other-ctl"))
    got = H["verdict"](led, subject="sha256:" + "b" * 64, evidence_ids=("e1", "other"))
    assert got["result"] == "supported"
    assert "evidence_for_another_subject" in got["reasons"]
    assert "unexplained_failure" not in got["reasons"]
