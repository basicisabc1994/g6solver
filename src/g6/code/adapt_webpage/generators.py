# SPDX-License-Identifier: Apache-2.0

"""
generators -- g6.code.adapt_webpage.generators

Page and component generation functions extracted from webpage_block.py.
"""
from __future__ import annotations

import html as html_mod
from typing import Any

from g6.code.adapt_webpage.sanitise import safe_style_block
from g6.code.adapt_webpage.patterns import (
    _COMPONENT_MAP,
    _LAYOUT_SECTIONS,
    _PATTERNS,
    _THEMES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _theme_css(theme_name: str) -> str:
    """Return a :root CSS block with the theme's custom properties."""
    props = _THEMES.get(theme_name, _THEMES["modern"])
    lines = ["  " + f"{k}: {v};" for k, v in props.items()]
    return ":root {\n" + "\n".join(lines) + "\n}"


def _build_section(pattern_key: str, title: str = "", description: str = "") -> tuple[str, str]:
    """Return (html, css) for a single section pattern."""
    pattern = _PATTERNS.get(pattern_key)
    if not pattern:
        raise ValueError('unknown section pattern')
    safe_title = html_mod.escape(title or "My Site")
    safe_desc = html_mod.escape(description or "")
    section_html = pattern["html"].replace("{title}", safe_title).replace("{description}", safe_desc)
    css = pattern["css"]
    return section_html, css


def _section_key_from_name(name: str) -> str:
    """Map common section names (hero, features, cta, footer, etc.) to pattern keys."""
    mapping = {
        "hero": "hero-centered",
        "features": "features-grid",
        "pricing": "pricing-cards",
        "testimonials": "testimonials-carousel",
        "cta": "cta-banner",
        "footer": "footer-columns",
        "nav": "nav-sticky",
        "blog": "blog-grid",
        "contact": "contact-form",
        "stats": "stats-counter",
        "gallery": "gallery-masonry",
    }
    return mapping.get(name.lower().strip(), name.lower().strip())


# ---------------------------------------------------------------------------
# Utility CSS
# ---------------------------------------------------------------------------

UTILITY_CSS = """
/* Utility classes */
.flex { display: flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.justify-center { justify-content: center; }
.justify-between { justify-content: space-between; }
.grid { display: grid; }
.grid-cols-2 { grid-template-columns: repeat(2, 1fr); }
.grid-cols-3 { grid-template-columns: repeat(3, 1fr); }
.grid-cols-4 { grid-template-columns: repeat(4, 1fr); }
.gap-1 { gap: 0.25rem; }
.gap-2 { gap: 0.5rem; }
.gap-4 { gap: 1rem; }
.gap-6 { gap: 1.5rem; }
.gap-8 { gap: 2rem; }
.p-1 { padding: 0.25rem; }
.p-2 { padding: 0.5rem; }
.p-4 { padding: 1rem; }
.p-6 { padding: 1.5rem; }
.p-8 { padding: 2rem; }
.m-0 { margin: 0; }
.m-1 { margin: 0.25rem; }
.m-2 { margin: 0.5rem; }
.m-4 { margin: 1rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
.text-center { text-align: center; }
.text-left { text-align: left; }
.text-right { text-align: right; }
.font-bold { font-weight: 700; }
.font-semibold { font-weight: 600; }
.text-sm { font-size: 0.875rem; }
.text-lg { font-size: 1.125rem; }
.text-xl { font-size: 1.25rem; }
.text-2xl { font-size: 1.5rem; }
.text-3xl { font-size: 1.875rem; }
.w-full { width: 100%; }
.max-w-screen-lg { max-width: 1024px; }
.max-w-screen-xl { max-width: 1280px; }
.hidden { display: none; }
.block { display: block; }
.inline-block { display: inline-block; }
.rounded { border-radius: 0.25rem; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-full { border-radius: 9999px; }
.shadow { box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.shadow-lg { box-shadow: 0 10px 15px rgba(0,0,0,0.1); }
.overflow-hidden { overflow: hidden; }
.relative { position: relative; }
.absolute { position: absolute; }
""".strip()


# ---------------------------------------------------------------------------
# Page generation functions
# ---------------------------------------------------------------------------

def generate_page(
    title: str,
    layout: str,
    theme: str,
    sections: list[str] | None,
    custom_css: str,
    description: str,
    og_image: str,
    canonical: str,
) -> dict[str, Any]:
    """Generate a full HTML page. Returns dict with html, css, sections_html, metadata."""
    title = title or "My Website"
    layout = layout or "landing"
    theme = theme or "modern"

    section_keys = list(sections) if sections else []
    if not section_keys:
        layout_sections = _LAYOUT_SECTIONS.get(layout, _LAYOUT_SECTIONS["landing"])
        section_keys = [s for s in layout_sections]

    resolved_keys = [_section_key_from_name(s) for s in section_keys]

    all_html_parts: list[str] = []
    all_css_parts: list[str] = [_theme_css(theme)]
    all_css_parts.append(
        "*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }\n"
        "body { font-family: var(--font-family); color: var(--color-text);"
        " line-height: 1.6; }\n"
        ".sr-only { position: absolute; width: 1px; height: 1px; padding: 0;"
        " margin: -1px; overflow: hidden; clip: rect(0,0,0,0);"
        " white-space: nowrap; border: 0; }"
    )

    for key in resolved_keys:
        html_part, css_part = _build_section(key, title=title, description=description)
        all_html_parts.append(html_part)
        if css_part:
            all_css_parts.append(css_part)

    if custom_css:
        all_css_parts.append(safe_style_block(custom_css))

    body_html = "\n".join(all_html_parts)
    combined_css = "\n".join(all_css_parts)

    # SEO fragments
    seo_lines: list[str] = []
    if description:
        seo_lines.append(f'  <meta name="description" content="{html_mod.escape(description)}">')
        seo_lines.append(f'  <meta property="og:title" content="{html_mod.escape(title)}">')
        seo_lines.append(f'  <meta property="og:description" content="{html_mod.escape(description)}">')
    if og_image:
        seo_lines.append(f'  <meta property="og:image" content="{html_mod.escape(og_image)}">')
    if canonical:
        seo_lines.append(f'  <link rel="canonical" href="{html_mod.escape(canonical)}">')
    seo_block = ("\n".join(seo_lines) + "\n") if seo_lines else ""

    full_html = (
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '  <meta charset="UTF-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'  <title>{html_mod.escape(title)}</title>\n'
        f'{seo_block}'
        f'  <style>\n{combined_css}\n  </style>\n'
        '</head>\n'
        '<body>\n'
        '  <a href="#main-content" class="sr-only">Skip to main content</a>\n'
        '  <main id="main-content">\n'
        f'{body_html}\n'
        '  </main>\n'
        '</body>\n'
        '</html>'
    )

    return {
        "html": full_html,
        "css": combined_css,
        "sections_html": all_html_parts,
        "metadata": {"layout": layout, "theme": theme, "sections": resolved_keys, "title": title},
    }


def generate_component(
    component_type: str,
    title: str,
    theme: str,
) -> dict[str, Any]:
    """Generate a single component. Returns dict with html, css, metadata."""
    comp_type = component_type or "hero"
    pattern_key = _COMPONENT_MAP.get(comp_type, comp_type)
    title = title or "Component"

    html_part, css_part = _build_section(pattern_key, title=title)
    theme_block = _theme_css(theme)
    full_css = theme_block + "\n" + css_part

    return {
        "html": html_part,
        "css": full_css,
        "metadata": {"component_type": comp_type, "pattern_key": pattern_key},
    }


def compose_sections(
    sections: list[str] | None,
    title: str,
    theme: str,
) -> dict[str, Any]:
    """Compose multiple sections. Returns dict with html, css, sections_html, metadata."""
    section_list = list(sections) if sections else ["hero", "features", "footer"]
    title = title or "My Site"
    theme = theme or "modern"

    all_html: list[str] = []
    all_css: list[str] = [_theme_css(theme)]

    for s in section_list:
        key = _section_key_from_name(s)
        html_part, css_part = _build_section(key, title=title)
        all_html.append(html_part)
        if css_part:
            all_css.append(css_part)

    combined_html = "\n".join(all_html)
    combined_css = "\n".join(all_css)

    return {
        "html": combined_html,
        "css": combined_css,
        "sections_html": all_html,
        "metadata": {"sections": section_list},
    }


def generate_css(theme: str, custom_css: str) -> dict[str, Any]:
    """Generate a combined CSS string. Returns dict with css, metadata."""
    theme = theme or "modern"
    theme_block = _theme_css(theme)
    combined = theme_block + "\n" + UTILITY_CSS

    if custom_css:
        combined += "\n" + safe_style_block(custom_css)

    return {
        "css": combined,
        "metadata": {"theme": theme, "utility_count": UTILITY_CSS.count("{")},
    }


def export_html(state: dict[str, Any]) -> dict[str, Any] | None:
    """Export current state as HTML. Returns dict or None if no HTML in state."""
    page_html = state.get("html", "")
    if not page_html:
        return None

    return {
        "html": page_html,
        "css": state.get("css", ""),
        "js": state.get("js", ""),
        "metadata": {"title": state.get("title", ""), "theme": state.get("theme", "modern")},
    }


# Keep build_page as an alias for generate_page for flexibility
build_page = generate_page
