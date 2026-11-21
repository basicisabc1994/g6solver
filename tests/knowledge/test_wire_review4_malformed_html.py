# SPDX-License-Identifier: Apache-2.0
"""Review 4, finding 4: markup the standard library's parser refuses (a bogus marked section) never raises out of the
HTML reader or a search: the text before it is kept, and what was not read is said."""
import json
from types import SimpleNamespace

from g6.knowledge import _html
from g6.knowledge.ctx_search import web


def test_the_reader_keeps_what_it_read_and_says_what_it_did_not():
    title, text, unread = _html.read_html("<p>before</p><div><![bogus]>after</div>")
    assert text == "before" and "after" not in text
    assert len(unread) == 1 and "not read" in unread[0]
    assert _html.html_to_text("<![ ]>x") == ("", "")


def test_malformed_brave_markup_returns_a_search_result(monkeypatch):
    payload = {"type": "search", "web": {"results": [
        {"title": "A result", "url": "https://example.invalid/a", "description": "<div><![bogus]>snippet</div>"},
        {"title": "<![bogus]>B", "url": "https://example.invalid/b"},
        {"title": "C", "url": "https://example.invalid/c", "description": "plain <strong>text</strong>"},
    ]}}
    client = SimpleNamespace(max_redirects=0, get=lambda *args, **kwargs: SimpleNamespace(
        body=json.dumps(payload).encode()))
    monkeypatch.setattr(web.keys, "require", lambda key: "dummy-not-a-key")
    found = web.BraveSearcher(client=client, gate=lambda *args: {"decision": "allow"}).search("example", 3)
    assert isinstance(found, web.SearchResult) and found.outcome == "ok"
    first, second, third = found.results
    assert first.text == "A result" and first.unread          # the snippet's unread part is said, not raised
    assert second.text == "" and second.unread and second.locator == "https://example.invalid/b"
    assert third.text == "C\n\nplain text" and third.unread == ()
