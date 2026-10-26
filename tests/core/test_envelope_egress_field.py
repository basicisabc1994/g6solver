# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.core.reliability_envelope import envelope_fields


def test_envelope_fields_default_egress_notice_empty():
    d = envelope_fields(reliability_label="qualified-draft")
    assert d["egress_notice"] == []
    assert d["completion_state"] == "qualified-draft"


def test_envelope_fields_carries_egress_notice():
    notice = [{"service": "Brave Search", "host": "api.search.brave.com",
               "data_class": "web search query", "jurisdiction": "US"}]
    for label in ("qualified-draft", "blocked-escalated"):
        d = envelope_fields(reliability_label=label, egress_notice=notice)
        assert d["egress_notice"] == notice
        # egress notice must NOT change the reliability state
        assert d["completion_state"] == label
        assert d["reliability_label"] == label
