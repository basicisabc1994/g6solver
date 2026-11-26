"""A library observation never becomes requirement support."""
from g6.evidence import Artifact
from g6.evidence import mapping as m
from tests.evidence.test_mapping_table import A, facts, rec


def test_library_observations_have_a_nonpromoting_row():
    row = m.MAPPING["library"]
    assert row.source == "patch_review" and row.issuer_kind == ""
    found = m.issue_records(facts(), Artifact(A, "agent", 1), [rec(kind="library")])
    assert found.promoted == ()
    assert found.refused[0][1][0][0] == m.NOT_PROMOTED
    assert found.certificate.verified_predicates == ()
