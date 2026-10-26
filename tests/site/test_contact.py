# SPDX-License-Identifier: Apache-2.0
"""The footer's Contact: a dialog with the project's address and how mail is answered. The site's script puts the
address together from its two parts when the dialog opens, so no page writes it whole."""
from __future__ import annotations

import html
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"


def _after_footer(page: str) -> str:
    return page[page.index('<footer class="foot">'):]


@pytest.mark.parametrize("path", ["/", "/license/", "/security/"])
def test_every_footer_opens_the_contact_dialog_with_how_mail_is_answered(client, path):
    foot = _after_footer(html.unescape(client.get(path).content.decode()))
    assert "data-contact>Contact</button>" in foot and '<dialog id="contact"' in foot
    assert 'data-user="hello" data-domain="g6solver.com"' in foot
    assert "hello@g6solver.com" not in foot              # put together by the script, never written whole
    assert ("Every email is reviewed by the founder, and we endeavour to respond within 48 hours. "
            "Thank you for your correspondence.") in " ".join(foot.split())


def test_the_security_page_reports_through_the_contact_dialog(client):
    # SITE_PASS item 4: the Security page's plain mailto becomes the dialog the footer opens, so no page writes the
    # address whole. Fails while security.html still holds the mailto link; the Contribute page points there too
    page = client.get("/security/").content.decode()
    report = page.split(">Reporting a vulnerability</h2>", 1)[1].split("</article>", 1)[0]
    assert '<span hidden>' in report and 'data-contact>' in report
    assert "mailto:" not in page and "hello@g6solver.com" not in page
    templates = Path(__file__).resolve().parents[2] / "web" / "templates"
    content = Path(__file__).resolve().parents[2] / "web" / "content"
    assert [p.name for p in [*templates.rglob("*.html"), *content.glob("*.md")]
            if "mailto:" in p.read_text(encoding="utf-8")] == []
    contribute = html.unescape(client.get("/contribute/").content.decode())
    assert "will be listed here before the first release" not in contribute
    assert 'href="/security/"' in contribute.split('id="security"', 1)[1]


def test_the_contact_item_shows_only_when_the_script_can_open_the_dialog(client):
    foot = _after_footer(client.get("/").content.decode())
    assert '<li hidden><button type="button" class="foot__contact" data-contact>' in foot
    js = (STATIC / "js" / "site.js").read_text(encoding="utf-8")
    assert "showModal" in js and 'getAttribute("data-user") + "@" + ' in js and "hidden = false" in js
