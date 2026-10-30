# SPDX-License-Identifier: Apache-2.0
"""The essays' figures (owner, 2026-10-30: "many of the images have text which is hard to see"). They are inline SVGs
drawn for the old dark site, with light-grey labels and white-tinted boxes in their own attributes, so on the light page
the labels all but vanished. Every colour a figure uses is mapped onto the site's theme tokens, or is a tint that reads
in both themes."""
from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
SCOPE = "article.py-24 svg"
# tints and mid-tones that read on both the light and the dark paper
BOTH_THEMES = re.compile(r"none|url\(#[\w-]+\)|rgba\((239,68,68|34,197,94|236,72,153|245,158,11),[\d.]+\)|#ec4899")
_COLOUR = re.compile(r'\b(fill|stroke|stop-color)="([^"]+)"')


def _essays() -> list[Path]:
    return [p for p in sorted((WEB / "templates" / "blog").glob("*.html")) if '<article class="py-24"' in p.read_text("utf-8")]


def _mapped() -> set[tuple[str, str]]:
    css = (WEB / "static" / "css" / "g6.css").read_text("utf-8")
    pairs = set()
    for selectors, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        for attribute, value in re.findall(rf"{re.escape(SCOPE)} \[(fill|stroke|stop-color)=\"([^\"]+)\"\]", selectors):
            if f"{attribute}: var(--" in body:              # the rule sets that same property from a token
                pairs.add((attribute, value))
    return pairs


def test_the_scope_is_the_essays_only():
    templates = [p for p in (WEB / "templates").rglob("*.html") if '<article class="py-24"' in p.read_text("utf-8")]
    assert templates and all(p.parent.name == "blog" for p in templates)
    assert len(_essays()) >= 20


def test_every_colour_in_an_essay_figure_follows_the_theme():
    mapped = _mapped()
    unmapped = set()
    for essay in _essays():
        for svg in re.findall(r"<svg.*?</svg>", essay.read_text("utf-8"), re.S):
            for attribute, value in _COLOUR.findall(svg):
                if not BOTH_THEMES.fullmatch(value) and (attribute, value) not in mapped:
                    unmapped.add((essay.name, attribute, value))
    assert not unmapped, sorted(unmapped)[:20]


def test_figure_labels_use_the_text_tokens():
    css = (WEB / "static" / "css" / "g6.css").read_text("utf-8")
    block = css.split(f'{SCOPE} [fill="#d1d5db"]', 1)[1].split("}", 1)[0]
    assert "var(--ink)" in block
    block = css.split(f'{SCOPE} [fill="#9ca3af"]', 1)[1].split("}", 1)[0]
    assert "var(--graphite)" in block
