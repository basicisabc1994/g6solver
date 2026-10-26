# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.core.reliability_envelope import envelope_fields


def test_envelope_fields_default_egress_notice_empty():
    d = envelope_fields(reliability_label="verified")
    assert d["egress_notice"] == []
    assert d["completion_state"] == "verified"


def test_envelope_fields_carries_egress_notice():
    notice = [{"service": "Brave Search", "host": "api.search.brave.com",
               "data_class": "web search query", "jurisdiction": "US"}]
    d = envelope_fields(reliability_label="verified", egress_notice=notice)
    assert d["egress_notice"] == notice
    # egress notice must NOT change the reliability state
    assert d["completion_state"] == "verified"
    assert d["reliability_label"] == "verified"