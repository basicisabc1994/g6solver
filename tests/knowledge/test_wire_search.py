# SPDX-License-Identifier: Apache-2.0
import json
from types import SimpleNamespace

from g6.knowledge.ctx_search import web


def test_brave_snippets_use_html_entities_and_attribute_boundaries(monkeypatch):
    payload = {"type": "search", "web": {"results": [{
        "title": "A <span data-x='>'>B</span> &amp; C", "url": "https://example.invalid/page",
        "description": "Visible <script>hidden</script> text"}]}}
    client = SimpleNamespace(max_redirects=0, get=lambda *args, **kwargs: SimpleNamespace(
        body=json.dumps(payload).encode()))
    monkeypatch.setattr(web.keys, "require", lambda key: "dummy-not-a-key")
    found = web.BraveSearcher(client=client, gate=lambda *args: {"decision": "allow"}).search("example", 1)
    assert found.results[0].title == "A B & C"
    assert "hidden" not in found.results[0].text
