# SPDX-License-Identifier: Apache-2.0
"""Inventory every receipt role and both declared and bound fixture identities."""
from dataclasses import replace
from pathlib import Path
import runpy

import pytest

from g6.research.echa import Ledger, trace_claim
from g6.research.echa.claims import EXECUTABLE
from g6.research.echa.ledger import CONTAMINATING

H = runpy.run_path(str(Path(__file__).with_name("test_echa_kernel_claims.py")))
DIRTY = "e" * 64
ROLES = ("check", "check_control", "calibration", "calibration_control", "scope_mapping", "scope_control")
#: Field-only cases: the receipt's own fixture field names the dirty fixture while its custody binding names the clean
#: one. Since 8765975a custody refuses that disagreement (NO_ENVIRONMENT), so the clean claim is already inconclusive
#: and these cases' first assertion cannot hold. Strict, so the day they pass again the mark must go.
FIELD_ONLY_LIMIT = pytest.mark.xfail(strict=True, reason=(
    "KNOWN_LIMITS limit-echa-field-fixture: a receipt whose fixture field disagrees with its custody binding is "
    "refused, so a field-only fixture identity never supports the clean claim"))


def _ledger(tainted_role, location, category):
    led = Ledger()
    led.add_standard(H["STD"])
    led.add_vault("v1", H["FX"], files=1, roots=1)
    led.add_vault("dirty", DIRTY, files=1, roots=1)

    def record(role, eid, code, **over):
        bound_fixture = DIRTY if role == tainted_role and location == "binding" else H["FX"]
        declared_fixture = (bound_fixture if location == "binding" else
                            DIRTY if role == tainted_role else H["FX"])
        source = H["canonical"](H["CheckBinding"](
            "request", "task", "p-keep", "b" * 64, H["STD"].standard_sha256,
            bound_fixture, code * 64).to_dict())
        return H["ev"](eid, source_ref=source, fixture_version=declared_fixture,
                       oracle_category=category, **over)

    for role, eid, code in (("check_control", "ctl", "a"),
                            ("calibration_control", "cal-ctl", "c"),
                            ("scope_control", "scope-ctl", "d")):
        led.add_evidence(record(role, eid, code, purpose="negative_control", result="fail",
                                negative_control_ref=""))
    check = record("check", "e1", "a", oracle_error_bound=0.02, calibration_ref="cal",
                   scope_mapping_ref="scope", population="pilot task t1", sample_unit="one task",
                   checked_statement="lemma L")
    # General claims require the checked record itself to be formal.
    check = replace(check, oracle_category="FORMAL_CHECK")
    led.add_evidence(check)
    for role, eid, code, ctl in (("calibration", "cal", "c", "cal-ctl"),
                                 ("scope_mapping", "scope", "d", "scope-ctl")):
        led.add_evidence(record(role, eid, code, purpose=role, negative_control_ref=ctl,
                                independence_group="assessor", applies_to_sha256=check.sha256,
                                population="pilot task t1", sample_unit="one task", scope="all inputs",
                                checked_statement="lemma L", oracle_error_bound=0.02,
                                evaluator_lower_bound=0.9))
    return led


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("location", ["binding", pytest.param("field", marks=FIELD_ONLY_LIMIT)])
@pytest.mark.parametrize("category", EXECUTABLE)
@pytest.mark.parametrize("incident", CONTAMINATING)
def test_contamination_voids_every_receipt_role(role, location, category, incident):
    led = _ledger(role, location, category)
    options = dict(subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
                   breadth="general", scope="all inputs")
    assert H["verdict"](led, claim_id="clean", **options)["result"] == "supported"
    led.add_incident(incident, fixture_digest=DIRTY)
    assert led.verify() == ()
    got = H["verdict"](led, **options)
    assert got["result"] == "inconclusive", (role, location, got)
    if role.startswith("calibration"):
        assert got["identification"] == "not_identified"
    if role.startswith("scope"):
        assert got["effective_breadth"] == "covered_cases"
    trace = trace_claim(led, "c1")
    assert trace["verdict"] == got
    assert trace["ledger_ok"] is True
    if role == "check":
        assert trace["evidence"][0]["contaminated"] is True


@pytest.mark.parametrize("declared, refused", [(H["FX"], False), (DIRTY, True)])
def test_declared_fixture_must_match_custody_binding(declared, refused):
    from g6.evidence.mapping import NO_ENVIRONMENT
    from g6.research.echa.custody import about

    source = H["canonical"](H["CheckBinding"](
        "request", "task", "p-keep", "b" * 64, H["STD"].standard_sha256,
        H["FX"], "a" * 64).to_dict())
    rec = H["ev"]("e1", source_ref=source, fixture_version=declared)
    parts, refusals = about(rec)
    assert parts.fixture == H["FX"]
    assert [code for code, _ in refusals] == ([NO_ENVIRONMENT] if refused else [])
