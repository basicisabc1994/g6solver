# SPDX-License-Identifier: Apache-2.0

"""
generators.py -- Typography, spacing, shadows, radii, and breakpoints generators.

Extracted from ui_design_block.py to reduce monolith size.
"""
from __future__ import annotations

from g6.code.adapt_webpage.schema import DesignToken


# ---------------------------------------------------------------------------
# Typography
# ---------------------------------------------------------------------------

_SCALE_NAMES = ["xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl", "5xl", "6xl"]


def _generate_typography(base_font_size: int = 16, scale_ratio: float = 1.25) -> list[DesignToken]:
    tokens: list[DesignToken] = []
    for i, name in enumerate(_SCALE_NAMES):
        exponent = i - 2  # xs=-2, sm=-1, base=0, lg=1, ...
        size = round(base_font_size * (scale_ratio ** exponent), 2)
        tokens.append(DesignToken(
            name=f"font-size-{name}",
            value=f"{size}px",
            category="typography",
            description=f"Typography scale step {name}",
        ))
    return tokens


# ---------------------------------------------------------------------------
# Spacing (Tailwind-compatible 4px grid)
# ---------------------------------------------------------------------------

_SPACING_MULTIPLIERS = [
    0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 9, 10, 11, 12,
    14, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 72, 80, 96,
]


def _generate_spacing() -> list[DesignToken]:
    tokens: list[DesignToken] = []
    for mult in _SPACING_MULTIPLIERS:
        px = int(mult * 4) if mult == int(mult) else mult * 4
        label = str(mult) if mult != int(mult) else str(int(mult))
        tokens.append(DesignToken(
            name=f"spacing-{label}",
            value=f"{px}px" if isinstance(px, int) else f"{px}px",
            category="spacing",
            description=f"Spacing {label} ({px}px)",
        ))
    return tokens


# ---------------------------------------------------------------------------
# Shadows
# ---------------------------------------------------------------------------

_SHADOWS = {
    "sm": "0 1px 2px 0 rgba(0,0,0,0.05)",
    "DEFAULT": "0 1px 3px 0 rgba(0,0,0,0.1), 0 1px 2px -1px rgba(0,0,0,0.1)",
    "md": "0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -2px rgba(0,0,0,0.1)",
    "lg": "0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -4px rgba(0,0,0,0.1)",
    "xl": "0 20px 25px -5px rgba(0,0,0,0.1), 0 8px 10px -6px rgba(0,0,0,0.1)",
}


def _generate_shadows() -> list[DesignToken]:
    return [
        DesignToken(name=f"shadow-{k}", value=v, category="shadow", description=f"Shadow {k}")
        for k, v in _SHADOWS.items()
    ]


# ---------------------------------------------------------------------------
# Radii
# ---------------------------------------------------------------------------

_RADII = {
    "none": "0", "sm": "2px", "DEFAULT": "4px", "md": "6px",
    "lg": "8px", "xl": "12px", "2xl": "16px", "full": "9999px",
}


def _generate_radii() -> list[DesignToken]:
    return [
        DesignToken(name=f"radius-{k}", value=v, category="radius", description=f"Border radius {k}")
        for k, v in _RADII.items()
    ]


# ---------------------------------------------------------------------------
# Breakpoints
# ---------------------------------------------------------------------------

_BREAKPOINTS = {"sm": "640px", "md": "768px", "lg": "1024px", "xl": "1280px", "2xl": "1536px"}


def _generate_breakpoints() -> list[DesignToken]:
    return [
        DesignToken(name=f"breakpoint-{k}", value=v, category="breakpoint", description=f"Breakpoint {k}")
        for k, v in _BREAKPOINTS.items()
    ]
