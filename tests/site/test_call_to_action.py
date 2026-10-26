# SPDX-License-Identifier: Apache-2.0
"""The Get started call to action. Owner, 2026-10-23: "make the get started button have a flashing visually attractive
call to action so people can see and click on this straight away. Also when users click on this can you do a tasteful
confetti animation" (feedback: the call to action was hard to see in the hero and on the title bar).

It is the most visible thing in the title bar and the hero, it moves slowly and only for a while, it does not move at
all under reduced motion, and the confetti comes from a pinned copy of the library the site serves itself, fetched on
the first click only."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from pages.routes import public_paths

WEB = Path(__file__).resolve().parents[2] / "web"
STATIC = WEB / "static"
VENDOR = STATIC / "vendor" / "tsparticles-confetti"
BUNDLE = VENDOR / "tsparticles.confetti.bundle.min.js"
VERSION = "3.8.1"
SOURCE = f"https://registry.npmjs.org/@tsparticles/confetti/-/confetti-{VERSION}.tgz"
CTA = re.compile(r'<a href="/get-started/" class="btn(?: btn--small)? cta">Get started</a>')
_SCRIPT_SRC = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)""", re.IGNORECASE)


def _page(client, path: str = "/") -> str:
    return client.get(path).content.decode()


def _between(page: str, start: str, end: str) -> str:
    return page.split(start, 1)[1].split(end, 1)[0]


def _foreign_scripts(page: str) -> list[str]:
    """Each ``<script src>`` that is not a path on this site (another origin, or a protocol-relative ``//`` URL)."""
    return [src for src in _SCRIPT_SRC.findall(page) if not src.startswith("/") or src.startswith("//")]


@pytest.mark.parametrize("path", public_paths())
def test_every_title_bar_carries_the_call_to_action(client, path):
    page = _page(client, path)
    assert CTA.search(_between(page, 'class="nav__end"', "</div>")), path
    # removing class="... cta" from the Get started link in templates/components/navbar.html (.nav__end) fails this


def test_the_drawer_and_the_hero_carry_it_and_get_started_leads_the_hero(client):
    page = _page(client)
    assert CTA.search(_between(page, 'id="mobile-menu"', "</div>")), "the phone menu"
    actions = _between(_between(page, 'id="hero-title"', "</header>"), 'class="actions"', "</div>")
    links = re.findall(r"<a [^>]*>[^<]*</a>", actions)
    assert CTA.fullmatch(links[0]) and links[1:] == ['<a href="#loop" class="btn">See how it works</a>'], links
    # the other Get started links keep their look: the one at the foot of the home page is the plain solid button
    assert '<a href="/get-started/" class="btn btn--solid">Get started</a>' in _between(page, 'id="start"', "</section>")
    # removing class="btn cta" from the hero's Get started (templates/pages/landing.html), or putting "See how it works"
    # back first, fails this


def test_it_is_the_one_signal_colour_with_readable_text_and_a_visible_focus():
    css = (STATIC / "css" / "g6.css").read_text(encoding="utf-8")
    ochre = re.findall(r"--ochre:\s*(#[0-9A-Fa-f]{6})", css)
    rule = re.search(r"\n\.cta \{([^}]*)\}", css).group(1)
    assert ochre and "background: var(--ochre)" in rule and "color: #000" in rule

    def luminance(hex_colour: str) -> float:
        def channel(c: int) -> float:
            c = c / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)

    for colour in ochre:                                   # black text on the ochre, in every theme that sets it
        assert (luminance(colour) + 0.05) / (0 + 0.05) >= 4.5, colour
    focus = re.search(r"body\.g6 \.cta:focus-visible \{([^}]*)\}", css).group(1)
    assert "outline: 3px solid var(--ink)" in focus
    # deleting the .cta rule's background or color line, or the focus rule, in static/css/g6.css fails this


def test_it_moves_slowly_then_rests_and_not_at_all_under_reduced_motion():
    css = (STATIC / "css" / "g6.css").read_text(encoding="utf-8")
    animations = re.findall(r"\.cta[^{}]*\{[^}]*?\banimation:\s*([^;]+);", css)
    moving = [a for a in animations if not a.strip().startswith("none")]
    assert len(moving) == 2, animations                    # the pulse on load and the occasional sheen
    for value in moving:
        # each cycle lasts a second or more: far below three flashes a second (WCAG 2.3.1), and never a hard blink
        assert float(re.search(r"(\d*\.?\d+)s\b", value).group(1)) >= 1, value
        assert "steps(" not in value and "step-" not in value, value
    pulse = next(a for a in moving if "cta-pulse" in a)
    assert re.search(r"\s3\s", f" {pulse} "), pulse     # a few times on load, then it stops
    sheen = next(a for a in moving if "cta-sheen" in a)
    assert float(re.search(r"(\d*\.?\d+)s\b", sheen).group(1)) >= 8, sheen   # then rarely
    block = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    block = block[:block.index("\n}\n")]
    assert ".cta, .cta::after { animation: none !important; }" in block
    # removing the .cta line from g6.css's reduced-motion block, or speeding a cycle under a second, fails this


def test_the_confetti_library_is_vendored_pinned_and_licensed():
    readme = (VENDOR / "README.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    assert f"@tsparticles/confetti {VERSION}" in readme and SOURCE in readme and digest in readme
    bundle = BUNDLE.read_text(encoding="utf-8")
    assert bundle.startswith("/*! For license information please see tsparticles.confetti.bundle.min.js.LICENSE.txt */")
    assert f'.version="{VERSION}"' in bundle and bundle.count("\n") == 1          # the minified bundle, as published
    banner = (VENDOR / "tsparticles.confetti.bundle.min.js.LICENSE.txt").read_text(encoding="utf-8")
    assert f"tsParticles Confetti v{VERSION} by Matteo Bruni" in banner
    licence = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "Copyright (c) 2020 Matteo Bruni" in licence
    assert "Permission is hereby granted, free of charge" in licence
    # git keeps the bytes the hash names: a Windows checkout must not rewrite the bundle's line ending
    assert "static/vendor/** -text" in (WEB / ".gitattributes").read_text(encoding="utf-8")
    # deleting any vendored file, or editing the bundle without its README's SHA-256, fails this


def test_the_check_finds_a_script_from_another_origin():
    page = ('<script src="https://cdn.jsdelivr.net/npm/x.js"></script><script defer src="/static/js/site.js"></script>'
            '<script src="//cdn.example/y.js"></script><script type="application/json" id="d">{}</script>')
    assert _foreign_scripts(page) == ["https://cdn.jsdelivr.net/npm/x.js", "//cdn.example/y.js"]


@pytest.mark.parametrize("path", public_paths())
def test_no_page_loads_a_script_from_another_origin_or_the_confetti_up_front(client, path):
    page = _page(client, path)
    assert _foreign_scripts(page) == [], path
    assert "tsparticles" not in page, path                # fetched on the first click, never with the page
    # a <script src="https://..."> in any template, or the confetti bundle loaded in base.html, fails this


def test_the_burst_loads_the_vendored_copy_on_the_first_click_and_never_holds_the_link():
    js = (STATIC / "js" / "site.js").read_text(encoding="utf-8")
    burst = js[js.index("// The call to action"):]
    assert '"../vendor/tsparticles-confetti/tsparticles.confetti.bundle.min.js"' in burst
    assert "http" not in burst and "import(" not in burst
    # an ordinary click only: a modified or middle click, or one something else handled, follows the link at once
    for guard in ("e.button !== 0", "e.ctrlKey", "e.metaKey", "e.shiftKey", "e.altKey", "e.defaultPrevented"):
        assert guard in burst, guard
    # reduced motion is checked before the page is held or any script is made
    assert burst.index("prefers-reduced-motion: reduce") < burst.index("preventDefault")
    assert burst.index("prefers-reduced-motion: reduce") < burst.index('createElement("script")')
    assert 600 <= int(re.search(r"BURST_MS = (\d+);", burst).group(1)) <= 800
    # a library that fails to load, or loads too slowly, sends the visitor on at once
    assert "onerror = go" in burst and re.search(r"setTimeout\(go, WAIT_MS\)", burst)
    for banned in ("localStorage", "sessionStorage", "document.cookie"):
        assert banned not in burst, banned
    # removing the reduced-motion test, a modifier guard or the onerror line from static/js/site.js fails this


def test_the_burst_never_blocks_a_click():
    # the library leaves its full-window canvas (in <div id="confetti">) on the page after the burst, and the browser's
    # back button can bring that page back from its cache: the canvas must let every click through
    css = (STATIC / "css" / "g6.css").read_text(encoding="utf-8")
    assert "#confetti canvas { pointer-events: none !important; }" in css
    bundle = BUNDLE.read_text(encoding="utf-8")
    assert '??"confetti"' in bundle                       # the library's default container id, which the rule names
    # removing the #confetti canvas rule from static/css/g6.css fails this
