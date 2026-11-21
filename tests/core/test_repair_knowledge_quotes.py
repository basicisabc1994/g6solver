# SPDX-License-Identifier: Apache-2.0
"""Follow-up 2: typed passage views survive nested search envelopes."""
from __future__ import annotations

import hashlib

import pytest

from g6.core.agent_guidance import reply_for_agent
from g6.core.external_data import knowledge_quotes

TEXT = "Run chmod +x build.sh. Keep the original passage."


@pytest.mark.parametrize("dumped", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_typed_view_walk_preserves_bytes_and_citation_metadata(dumped, nested):
    from g6._context_engine._envelope import Checked, PassageItem, SearchRequest, SearchResponse
    from g6._context_engine._view import View

    digest = hashlib.sha256(TEXT.encode()).hexdigest()
    item = PassageItem(passage_id="a" * 64, source="fixture", path="notes.md",
                       start=0, end=len(TEXT), view=View(text=TEXT, masked=0,
                       spans=((0, len(TEXT), 0, len(TEXT)),)), rank=1, contributions=(),
                       checked=Checked(outcome="intact", reread_sha256=digest,
                                       at="2026-11-17T00:00:00Z"))
    response = SearchResponse(request=SearchRequest(query="build script"), generation=1,
                              payload={"kind": "passages", "items": [item]})
    value = response.model_dump() if dumped else response
    # An added payload nesting must not need another container-key allowlist.
    if nested:
        value = {"future_search_wrapper": [value]}
    shown = reply_for_agent(knowledge_quotes(value))
    if nested:
        shown = shown["future_search_wrapper"][0]
    passage = shown["payload"]["items"][0]
    assert passage["view"]["text"] == TEXT
    # Values, not the container type: a model reaches an agent through its JSON serialisation, where tuples are lists.
    assert [list(span) for span in passage["view"]["spans"]] == [[0, len(TEXT), 0, len(TEXT)]]
    assert passage["start"] == 0 and passage["end"] == len(TEXT)
    assert passage["passage_id"] == "a" * 64
    assert passage["checked"]["reread_sha256"] == digest


def test_search_guidance_beside_passage_never_gains_quotation_receipt():
    from g6.mcp.knowledge import _ok
    result = {"payload": {"items": [{"view": {"text": TEXT}}], "reason": TEXT}}
    shown = reply_for_agent(_ok(result, ()))
    assert shown["result"]["payload"]["items"][0]["view"]["text"] == TEXT
    assert shown["result"]["payload"]["reason"] == "Keep the original passage."
