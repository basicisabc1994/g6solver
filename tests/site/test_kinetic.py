# SPDX-License-Identifier: Apache-2.0
"""Kinetic type (owner, 2026-10-19): the headings of the content pages set themselves as they scroll into view, "in a
tasteful manner ... without affecting readability". Only those pages load it; nothing is hidden unless the script runs;
reduced motion gets no motion."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from pages.routes import public_paths

STATIC = Path(__file__).resolve().parents[2] / "web" / "static"
FORMS = ["/metaharness/", "/components/", "/research/", "/docs/", "/guide/", "/guide/view/", "/faq/", "/essays/",
         *(p for p in public_paths() if p.startswith("/essays/") and p != "/essays/")]
# owner, 2026-10-20: "extend animations/typography to the remaining landing page"; the lattice stays its one picture,
# so the home page sets its headings in motion without the Bauhaus forms
KINETIC = [*FORMS, "/"]
STILL = [p for p in public_paths() if p not in KINETIC]
_BODY = re.compile(r"<body\b[^>]*>")


def _page(client, path: str) -> str:
    return client.get(path).content.decode()


def test_every_essay_and_the_home_page_are_accounted_for():
    assert len(KINETIC) == 8 + 21 + 1 and "/" in KINETIC and "/about/" in STILL and "/docs/full/" in STILL


@pytest.mark.parametrize("path", KINETIC)
def test_the_content_pages_opt_in(client, path):
    page = _page(client, path)
    assert "data-kinetic" in _BODY.search(page).group(0), path
    assert 'src="/static/js/kinetic.js"' in page and 'href="/static/css/kinetic.css"' in page, path
    # and the Bauhaus forms behind them (owner, 2026-10-19: "bauhaus inspired minor background animatory elements")
    forms = 'src="/static/js/bauhaus.js"' in page and 'href="/static/css/bauhaus.css"' in page
    assert forms is (path in FORMS) and page.count('class="bh"') == (1 if path in FORMS else 0), path


def test_the_home_page_sets_its_headings_and_statements_but_not_its_scene_titles(client):
    page = _page(client, "/")
    js = (STATIC / "js" / "kinetic.js").read_text(encoding="utf-8")
    assert '"h1,h2,.statement"' in js and 'class="statement"' in page
    # the two display titles in the scene keep their own setting: kinetic.js moves plain headings only, and these break
    # four short lines, so no line runs into the lattice beside it at desktop width
    assert "Now you can<br>make AI agents<br>check their<br>own work." in page and "G6<br>engineers<br>itself." in page


@pytest.mark.parametrize("path", STILL)
def test_no_other_page_moves(client, path):
    page = _page(client, path)
    assert "data-kinetic" not in _BODY.search(page).group(0), path
    assert "kinetic.js" not in page and "kinetic.css" not in page, path
    assert "bauhaus.js" not in page and "bauhaus.css" not in page and 'class="bh"' not in page, path


def test_the_forms_are_decoration_only(client):
    layer = re.search(r'<div class="bh"[^>]*>(.*?)</div>', _page(client, "/faq/"), re.DOTALL)
    assert 'aria-hidden="true"' in layer.group(0) and not re.search(r"[A-Za-z]{2,}", re.sub(r"<[^>]+>", "", layer.group(1)))
    css = (STATIC / "css" / "bauhaus.css").read_text(encoding="utf-8")
    rule = re.search(r"\.bh\s*\{([^}]*)\}", css).group(1)
    assert "pointer-events: none" in rule and "position: fixed" in rule and "z-index: -1" in rule
    # transform and opacity only: nothing that moves can lay the page out again
    for value in re.findall(r"transition:\s*([^;]+);", css):
        moved = {part.split()[0] for part in re.sub(r"\([^)]*\)", "", value).split(",")}
        assert moved <= {"transform", "opacity", "none"}, value
    assert not re.search(r"@keyframes[^{]*\{[^}]*\{(?![^}]*\btransform\b)[^}]*\}", css)   # keyframes move transform
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert "animation: none !important" in block and "transition: none !important" in block
    js = (STATIC / "js" / "bauhaus.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion: reduce" in js and "visibilitychange" in js
    for banned in ("http", "localStorage", "sessionStorage", "document.cookie", "import "):
        assert banned not in js, banned


def test_reduced_motion_gets_no_motion():
    css = (STATIC / "css" / "kinetic.css").read_text(encoding="utf-8")
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert "opacity: 1 !important" in block and "animation: none !important" in block
    assert "transform: none !important" in block
    js = (STATIC / "js" / "kinetic.js").read_text(encoding="utf-8")
    # the script checks before it changes anything: the reduced-motion test comes before the first class it adds
    assert js.index("prefers-reduced-motion: reduce") < js.index("classList.add")


def test_nothing_is_hidden_unless_the_script_ran():
    css = (STATIC / "css" / "kinetic.css").read_text(encoding="utf-8")
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if re.search(r"opacity:\s*0\b|visibility:\s*hidden", body):
            assert ".k-wait" in selector, selector.strip()      # a class only kinetic.js adds
    templates = Path(__file__).resolve().parents[2] / "web" / "templates"
    assert not any("k-wait" in t.read_text(encoding="utf-8") for t in templates.rglob("*.html"))


def test_the_script_is_small_local_and_stores_nothing():
    js = (STATIC / "js" / "kinetic.js").read_text(encoding="utf-8")
    assert len(js.encode("utf-8")) < 4096
    for banned in ("http", "localStorage", "sessionStorage", "document.cookie", "import "):
        assert banned not in js, banned
    # split headings keep their words for screen readers, and each heading moves once
    assert "aria-label" in js and "aria-hidden" in js and "unobserve" in js
