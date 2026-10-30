# SPDX-License-Identifier: Apache-2.0
"""Light mode by default (owner, 2026-10-30: "I want to default to light mode please"). Every page opens light whatever
the visitor's system prefers; the dark tokens stay in the stylesheets for a later switch."""
from __future__ import annotations

from pathlib import Path

from pages.routes import public_paths

WEB = Path(__file__).resolve().parents[2] / "web"


def test_every_page_opens_in_light_mode(client):
    for path in public_paths():
        page = client.get(path).content.decode()
        assert '<html lang="en" data-theme="light">' in page, path
        assert 'content="#000000"' not in page, path                 # no dark browser chrome either


def test_the_diagrams_follow_the_page_not_the_system():
    script = (WEB / "static" / "js" / "mermaid-init.js").read_text("utf-8")
    assert "darkMode: scheme.matches," not in script
    assert 'document.documentElement.dataset.theme' in script
