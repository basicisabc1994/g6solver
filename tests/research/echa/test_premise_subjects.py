# SPDX-License-Identifier: Apache-2.0
"""Every premise and control takes its fixture identity from custody."""
from dataclasses import replace
from pathlib import Path
import json
import runpy

import pytest

from g6.evidence.mapping import NO_ENVIRONMENT
from g6.research.echa import Ledger, trace_claim
from g6.research.echa.custody import about

H = runpy.run_path(str(Path(__file__).with_name("test_echa_kernel_claims.py")))
ROLES = ("check", "check_control", "calibration", "calibration_control", "scope_mapping", "scope_control")


def receipts(role=None, fixture="e" * 64):
    led = Ledger()
    led.add_standard(H["STD"])
    led.add_vault("sealed-B", H["FX"], files=1, roots=1)

    def record(which, eid, code, **over):
        binding = json.loads(H["_binding"](code))
        if which == role:
            binding["fixture_sha256"] = fixture
        return H["ev"](eid, source_ref=H["canonical"](binding), **over)

    for which, eid, code in (("check_control", "ctl", "a"),
                             ("calibration_control", "cal-ctl", "c"),
                             ("scope_control", "scope-ctl", "d")):
        led.add_evidence(record(which, eid, code, purpose="negative_control", result="fail", negative_control_ref=""))
    check = record("check", "e1", "a", oracle_category="FORMAL_CHECK", negative_control_ref="ctl",
                   oracle_error_bound=0.02, calibration_ref="cal", scope_mapping_ref="scope",
                   population="pilot task t1", sample_unit="one task", checked_statement="lemma L")
    led.add_evidence(check)
    for which, eid, code, ctl in (("calibration", "cal", "c", "cal-ctl"),
                                  ("scope_mapping", "scope", "d", "scope-ctl")):
        led.add_evidence(record(which, eid, code, purpose=which, negative_control_ref=ctl,
                                independence_group="assessor", applies_to_sha256=check.sha256,
                                population=check.population, sample_unit=check.sample_unit,
                                scope="all inputs", checked_statement="lemma L", oracle_error_bound=0.02,
                                evaluator_lower_bound=0.9))
    return led


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("fixture", ["e" * 64, "f" * 64])
@pytest.mark.parametrize("seal_A", [False, True])
def test_no_receipt_role_can_borrow_fixture_Bs_seal(role, fixture, seal_A):
    led = receipts(role, fixture)
    if seal_A:
        led.add_vault("sealed-A", fixture, files=1, roots=1)
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                       breadth="general", scope="all inputs")
    assert got["result"] == "inconclusive", (role, fixture, seal_A, got)
    if role.startswith("calibration") or role.startswith("check"):
        assert got["identification"] == "not_identified"
    if role.startswith("scope"):
        assert got["effective_breadth"] == "covered_cases"
    assert trace_claim(led, "c1")["verdict"] == got


def test_fixture_disagreement_has_the_existing_environment_refusal_code():
    rec = H["ev"]("receipt", source_ref=H["_binding"]())
    binding = json.loads(rec.source_ref)
    binding["fixture_sha256"] = "e" * 64
    parts, refused = about(replace(rec, source_ref=H["canonical"](binding)))
    assert parts.fixture == "e" * 64
    assert NO_ENVIRONMENT in {code for code, _ in refused}


def test_matching_custody_and_seal_preserve_bounded_support():
    led = receipts()
    got = H["verdict"](led, subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                       breadth="general", scope="all inputs")
    assert got["result"] == "supported"
    assert got["identification"] == "identified"
    assert got["effective_breadth"] == "general"
    assert got["corrected_lower_bound"] <= 0.88
    assert led.verify() == ()


def test_matching_but_unregistered_custody_fixture_cannot_enter_as_independent():
    from g6.research.echa import LedgerError

    led = receipts()
    binding = json.loads(H["_binding"]())
    binding["fixture_sha256"] = "e" * 64
    rec = H["control"]("unsealed-control", fixture_version="e" * 64, source_ref=H["canonical"](binding))
    with pytest.raises(LedgerError, match="sealed vault"):
        led.add_evidence(rec)
