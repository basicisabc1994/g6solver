# SPDX-License-Identifier: Apache-2.0
"""Program guidance for agents never teaches them to lift the person's block."""
from __future__ import annotations

from g6.core.markdown import parse

import re

PERSON = "This op is sensitive. The person must allow it themselves. Tell the person and stop."
WITHHELD = "Results were withheld from a source not shared with this provider. Tell the person."
_AUTHORITY = re.compile(
    r"allow\s*=|G6_MCP_SENSITIVE|G6_ALLOW_UNCONFINED|allow_execution|\ballow_sensitive\b|"
    r"\b(?:confirm|approve|grant|promote)\s*\(|\bapprover\b|autonomous_low_risk|"
    r"\bg6\s+setup\b|--allow|allow parameter", re.I)


def skill_for_agent(text: str | None) -> str | None:
    """Remove authority-bearing CommonMark blocks, preserving all other source bytes."""
    if text is None or not _AUTHORITY.search(text):
        return text
    doc = parse(text)
    spans = [doc.span(t) for t in doc.tokens
             if t.type in ("paragraph_open", "heading_open", "fence", "code_block", "html_block")]
    cuts = [(a, b) for a, b in spans if _AUTHORITY.search(text[a:b])]
    # A match spanning blocks also removes every block it intersects.
    cuts += [(a, b) for m in _AUTHORITY.finditer(text) for a, b in spans if a < m.end() and b > m.start()]
    kept, end = [], 0
    for a, b in sorted(set(cuts)):
        if a > end:
            kept.append(text[end:a])
        end = max(end, b)
    kept.append(text[end:])
    filtered = "".join(kept)
    return "" if _AUTHORITY.search(filtered) else filtered


def help_for_agent(component: str, help_text: object = None) -> str:
    """Keep the component's safe help lines beside its declared operation templates."""
    from g6.skills import corpus

    if isinstance(help_text, dict):
        original = [f"{key}: {value}" for key, value in help_text.items()]
    elif isinstance(help_text, (list, tuple)):
        original = [str(line) for line in help_text]
    else:
        original = [str(help_text)] if help_text else []
    text = "\n".join(original)
    matches = [match.span() for match in _AUTHORITY.finditer(text)]
    lines, offset = [], 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        if not any(start < end and stop > offset for start, stop in matches):
            lines.append(line.rstrip("\r\n"))
        offset = end
    for entry in corpus.load()["entries"]:
        if entry["kind"] == "op" and entry["component"] == component:
            template = entry["id"][3:] + ": params_json template " + entry["call"]["arguments"]["params_json"]
            if not _AUTHORITY.search(template):
                lines.append(template + (" " + PERSON if entry["sensitive"] else ""))
    return "\n".join(lines) or "No registered operation is available through the G6 door."
