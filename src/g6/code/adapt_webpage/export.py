# SPDX-License-Identifier: Apache-2.0

"""
export.py -- Token export functions (CSS, Tailwind, JSON DTCG).

Extracted from ui_design_block.py to reduce monolith size.
"""
from __future__ import annotations

import json
from typing import Any

from g6.code.adapt_webpage.color_math import _hex_to_rgb, _rgb_to_hsl, _hsl_to_rgb, _rgb_to_hex


# ---------------------------------------------------------------------------
# Token export
# ---------------------------------------------------------------------------

def _export_css(tokens: list[dict], include_dark_mode: bool = False) -> str:
    """Export tokens as CSS custom properties, optionally with a dark-mode block.

    Token names and values pass through ``sanitise.safe_css_ident`` and
    ``safe_css_value`` so a malicious token cannot break out of the
    ``--…: …;`` declaration to inject ``url(javascript:…)`` or ``@import``.
    """
    from g6.code.adapt_webpage.token_sanitise import safe_css_ident, safe_css_value

    lines = [":root {"]
    for t in tokens:
        name = safe_css_ident(t.get("name", ""))
        value = safe_css_value(t.get("value", ""))
        lines.append(f"  --{name}: {value};")
    lines.append("}")

    if include_dark_mode:
        color_tokens = [t for t in tokens if t.get("category") == "color"]
        if color_tokens:
            dark_lines = ["", "@media (prefers-color-scheme: dark) {", "  :root {"]
            for t in color_tokens:
                name = safe_css_ident(t.get("name", ""))
                raw_value = t.get("value", "")
                try:
                    r, g, b = _hex_to_rgb(raw_value)
                    h, s, lightness = _rgb_to_hsl(r, g, b)
                    dark_lightness = 1.0 - lightness
                    dr, dg, db = _hsl_to_rgb(h, s, dark_lightness)
                    dark_value = _rgb_to_hex(dr, dg, db)
                except Exception:
                    dark_value = safe_css_value(raw_value)
                dark_lines.append(f"    --{name}: {dark_value};")
            dark_lines.append("  }")
            dark_lines.append("}")
            lines.extend(dark_lines)

    return "\n".join(lines)


def _export_tailwind(tokens: list[dict]) -> str:
    """Export tokens as Tailwind config JSON."""
    extend: dict[str, dict[str, str]] = {}
    category_map = {
        "color": "colors",
        "typography": "fontSize",
        "spacing": "spacing",
        "shadow": "boxShadow",
        "radius": "borderRadius",
        "breakpoint": "screens",
    }
    for t in tokens:
        cat = t.get("category", "")
        tw_key = category_map.get(cat, cat)
        if tw_key not in extend:
            extend[tw_key] = {}
        name = t.get("name", "").replace(f"{cat}-", "", 1) if cat else t.get("name", "")
        extend[tw_key][name] = t.get("value", "")

    config = {"theme": {"extend": extend}}
    return json.dumps(config, indent=2)


def _export_json_dtcg(tokens: list[dict]) -> str:
    """Export tokens as W3C Design Token Community Group JSON."""
    out: dict[str, Any] = {}
    for t in tokens:
        name = t.get("name", "")
        out[name] = {
            "$value": t.get("value", ""),
            "$type": t.get("category", ""),
        }
        if t.get("description"):
            out[name]["$description"] = t["description"]
    return json.dumps({"tokens": out}, indent=2)
