# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors
"""The mapping table's two rows for the formal path (design 15.3, "before promotion, extend 3.1's mapping")."""
from __future__ import annotations

import dataclasses

from g6.evidence import mapping as m


def test_rows_state_every_part():
    lead, accepted = m.MAPPING["formal_model_result"], m.MAPPING["formal_accepted"]
    for row in (lead, accepted):
        assert row.source == "formalisation"
        for field in dataclasses.fields(m.Row):
            value = getattr(row, field.name)
            assert isinstance(value, bool) or (field.name == "issuer_kind" and row is lead) or str(value).strip()
        assert f"| {row.kind} |" in m.render_markdown()
    assert lead.issuer_kind == "" and "lead" in lead.conflicts          # about the model only: never promoted
    assert accepted.issuer_kind == "formal_checked" and accepted.needs_model and accepted.needs_certificate
    assert "negative control unavailable" in lead.negative_control


def test_the_two_codes_are_listed_once():
    assert m.NO_ACCEPTANCE == "G6_E_EVIDENCE_NO_ACCEPTANCE" and m.NO_CORRESPONDENCE == "G6_E_EVIDENCE_NO_CORRESPONDENCE"
    assert m.CODES.count(m.NO_ACCEPTANCE) == 1 and m.CODES.count(m.NO_CORRESPONDENCE) == 1


def test_the_two_rows_never_give_one_requirement_two_statuses():
    """One result is one record of one kind: the lead is refused, so only the accepted row can reach the issuer."""
    promoted = {kind for kind, row in m.MAPPING.items() if row.source == "formalisation" and row.issuer_kind}
    assert promoted == {"formal_accepted"}