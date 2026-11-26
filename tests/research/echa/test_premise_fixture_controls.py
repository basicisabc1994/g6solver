# SPDX-License-Identifier: Apache-2.0
"""Separate sealed fixture agreement from two separately sealed identities."""
from dataclasses import replace
from pathlib import Path
import json
import runpy

import pytest

from g6.evidence.mapping import NO_ENVIRONMENT
from g6.research.echa import EvidenceRecord, Ledger, StandardRef, trace_claim
from g6.research.echa.claims import EXECUTABLE
from g6.research.echa.custody import about
from g6.research.echa.ledger import CONTAMINATING

F = runpy.run_path(str(Path(__file__).with_name("test_echa_fixture_roles.py")))
H = F["H"]
OPTIONS = dict(subject="sha256:" + "b" * 64, estimand="true_correctness", target=0.5,
               breadth="general", scope="all inputs")
ROLE_IDS = {"check": "e1", "check_control": "ctl", "calibration": "cal",
            "calibration_control": "cal-ctl", "scope_mapping": "scope", "scope_control": "scope-ctl"}


def matching_ledger(original, location):
    """Replay the tracked builder's shape with agreement and updated exact-record references.

    Keep the selected identity: copy custody into the repeated field for binding
    cases, or copy the repeated field into custody for field cases. Both identities
    already have seals in the original builder. No stored ledger is edited.
    """
    led = Ledger()
    hashes = {}
    for entry in original.entries():
        body = entry["body"]
        if entry["kind"] == "standard":
            led.add_standard(StandardRef.from_dict(body))
        elif entry["kind"] == "vault":
            led.add_vault(body["vault_id"], body["digest"], files=body["files"], roots=body["roots"])
        else:
            assert entry["kind"] == "evidence"
            rec = EvidenceRecord.from_dict(body)
            binding = json.loads(rec.source_ref)
            if location == "binding":
                matched = replace(rec, fixture_version=binding["fixture_sha256"])
            else:
                binding["fixture_sha256"] = rec.fixture_version
                matched = replace(rec, source_ref=H["canonical"](binding))
            matched = replace(matched, applies_to_sha256=hashes.get(rec.applies_to_sha256, rec.applies_to_sha256))
            hashes[rec.sha256] = matched.sha256
            led.add_evidence(matched)
    return led


@pytest.mark.parametrize("role", F["ROLES"])
@pytest.mark.parametrize("location", ["binding", "field"])
@pytest.mark.parametrize("category", EXECUTABLE)
@pytest.mark.parametrize("incident", CONTAMINATING)
def test_matching_sealed_fixture_supports_every_receipt_role(role, location, category, incident):
    led = matching_ledger(F["_ledger"](role, location, category), location)
    rec = EvidenceRecord.from_dict(led.get("evidence", ROLE_IDS[role]))
    parts, refused = about(rec)
    assert refused == ()
    assert parts.fixture == rec.fixture_version == F["DIRTY"]
    assert led.verify() == ()
    got = H["verdict"](led, claim_id="clean", **OPTIONS)
    assert got["result"] == "supported", (role, location, category, got)
    assert got["identification"] == "identified"
    assert got["effective_breadth"] == "general"
    clean_trace = trace_claim(led, "clean")
    assert clean_trace["verdict"] == got
    assert clean_trace["ledger_ok"] is True

    led.add_incident(incident, fixture_digest=parts.fixture)
    assert led.verify() == ()
    got = H["verdict"](led, **OPTIONS)
    assert got["result"] == "inconclusive", (role, location, category, incident, got)
    if role.startswith("calibration"):
        assert got["identification"] == "not_identified"
    if role.startswith("scope"):
        assert got["effective_breadth"] == "covered_cases"
    trace = trace_claim(led, "c1")
    assert trace["verdict"] == got
    assert trace["ledger_ok"] is True
    if role == "check":
        assert trace["evidence"][0]["contaminated"] is True


@pytest.mark.parametrize("role", F["ROLES"])
@pytest.mark.parametrize("location", ["binding", "field"])
@pytest.mark.parametrize("category", EXECUTABLE)
def test_two_seals_do_not_make_disagreeing_fixture_identities_match(role, location, category):
    led = F["_ledger"](role, location, category)
    rec = EvidenceRecord.from_dict(led.get("evidence", ROLE_IDS[role]))
    parts, refused = about(rec)
    expected = (F["DIRTY"], H["FX"]) if location == "binding" else (H["FX"], F["DIRTY"])
    assert (parts.fixture, rec.fixture_version) == expected
    assert NO_ENVIRONMENT in {code for code, _ in refused}
    got = H["verdict"](led, **OPTIONS)
    assert got["result"] == "inconclusive", (role, location, category, got)
