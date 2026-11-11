"""Caller annotations cannot erase the reason a refused observation was retained."""
from tests.evidence.test_an_observed_failure_reaches_the_issuer import FACTS, SUBJECT, FLAKY_FAIL
from g6.evidence import mapping


def test_extra_metadata_cannot_overwrite_the_caveat_refusal_reason():
    issued = mapping.issue_records(FACTS, SUBJECT, [FLAKY_FAIL], extra={"refused": []})
    assert issued.refused and issued.caveats
    record_id, caveat_id = issued.caveats[0]
    assert record_id == FLAKY_FAIL.record_id
    expected = [[code, why] for rid, codes in issued.refused for code, why in codes
                if rid == record_id]
    provenance = issued.ledger.get(mapping.PROVENANCE_KIND, caveat_id)
    assert provenance["refused"] == expected, "caller metadata erased the caveat's refusal reasons"
