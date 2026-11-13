"""Every own field in both provenance record paths resists caller collisions."""
from dataclasses import replace

import pytest

from g6.evidence import mapping
from tests.evidence.test_an_observed_failure_reaches_the_issuer import DECISIVE, FACTS, FLAKY_FAIL, SUBJECT


@pytest.mark.parametrize("record", [DECISIVE, FLAKY_FAIL], ids=["promoted", "caveat"])
def test_every_written_authoritative_field_survives_a_caller_collision(record):
    baseline = mapping.issue_records(FACTS, SUBJECT, [record])
    ident = baseline.caveats[0][1] if baseline.caveats else record.record_id
    own = baseline.ledger.get(mapping.PROVENANCE_KIND, ident)
    for key in own:
        issued = mapping.issue_records(FACTS, SUBJECT, [record], extra={key: "caller", "note": "kept"})
        assert issued.ledger.get(mapping.PROVENANCE_KIND, ident)[key] == own[key], key
        assert issued.ledger.get(mapping.PROVENANCE_KIND, ident)["note"] == "kept"


def test_binding_is_reserved_even_for_an_unbound_record():
    # A missing binding must not become caller-declared authoritative custody.
    rec = replace(DECISIVE, binding=None)
    issued = mapping.issue_records(FACTS, SUBJECT, [rec], extra={"binding": {"caller": "invented"}})
    assert "binding" not in issued.ledger.get(mapping.PROVENANCE_KIND, rec.record_id)
