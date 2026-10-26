# SPDX-License-Identifier: Apache-2.0

"""
color_math.py -- Color conversion helpers and WCAG 2.1 accessibility math.

Extracted from ui_design_block.py to reduce monolith size.
"""
from __future__ import annotations

from g6.code.adapt_webpage.schema import DesignToken


# ---------------------------------------------------------------------------
# Color conversion helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert '#RRGGBB' or 'RRGGBB' to (R, G, B) ints."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Invalid hex color: {hex_color}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert RGB [0-255] to HSL [0-360, 0-1, 0-1]."""
    r_ = r / 255.0
    g_ = g / 255.0
    b_ = b / 255.0
    c_max = max(r_, g_, b_)
    c_min = min(r_, g_, b_)
    delta = c_max - c_min

    # Lightness
    light = (c_max + c_min) / 2.0

    if delta == 0:
        return (0.0, 0.0, light)

    # Saturation
    if light <= 0.5:
        sat = delta / (c_max + c_min)
    else:
        sat = delta / (2.0 - c_max - c_min)

    # Hue
    if c_max == r_:
        hue = ((g_ - b_) / delta) % 6
    elif c_max == g_:
        hue = ((b_ - r_) / delta) + 2
    else:
        hue = ((r_ - g_) / delta) + 4
    hue *= 60.0
    if hue < 0:
        hue += 360.0

    return (hue, sat, light)


def _hsl_to_rgb(h: float, s: float, lightness: float) -> tuple[int, int, int]:
    """Convert HSL [0-360, 0-1, 0-1] to RGB [0-255]."""
    if s == 0:
        v = int(round(lightness * 255))
        return (v, v, v)

    def _hue_to_rgb(p: float, q: float, t: float) -> float:
        if t < 0:
            t += 1
        if t > 1:
            t -= 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    q = lightness * (1 + s) if lightness < 0.5 else lightness + s - lightness * s
    p = 2 * lightness - q
    h_norm = h / 360.0

    r = _hue_to_rgb(p, q, h_norm + 1 / 3)
    g = _hue_to_rgb(p, q, h_norm)
    b = _hue_to_rgb(p, q, h_norm - 1 / 3)

    return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


# ---------------------------------------------------------------------------
# Shade generator
# ---------------------------------------------------------------------------

_SHADE_LEVELS: dict[int, float] = {
    50: 0.95, 100: 0.90, 200: 0.80, 300: 0.70, 400: 0.60,
    500: 0.50, 600: 0.40, 700: 0.30, 800: 0.20, 900: 0.10, 950: 0.05,
}


def _generate_shades(hex_color: str, prefix: str = "color-primary") -> list[DesignToken]:
    """Generate 11-step shade scale from a base hex color."""
    r, g, b = _hex_to_rgb(hex_color)
    h, s, _l = _rgb_to_hsl(r, g, b)
    tokens: list[DesignToken] = []
    for level, lightness in _SHADE_LEVELS.items():
        rgb = _hsl_to_rgb(h, s, lightness)
        tokens.append(DesignToken(
            name=f"{prefix}-{level}",
            value=_rgb_to_hex(*rgb),
            category="color",
            description=f"Shade {level} of {hex_color}",
        ))
    return tokens


# ---------------------------------------------------------------------------
# Accessibility helpers (WCAG 2.1)
# ---------------------------------------------------------------------------

def _srgb_linearize(c: float) -> float:
    """Linearize an sRGB channel value [0-1]."""
    if c <= 0.03928:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.1 relative luminance of a hex color."""
    r, g, b = _hex_to_rgb(hex_color)
    r_lin = _srgb_linearize(r / 255.0)
    g_lin = _srgb_linearize(g / 255.0)
    b_lin = _srgb_linearize(b / 255.0)
    return 0.2126 * r_lin + 0.7152 * g_lin + 0.0722 * b_lin


def _contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two hex colors."""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)
