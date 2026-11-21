# SPDX-License-Identifier: Apache-2.0
"""Program guidance for agents never teaches them to lift the person's block."""
from __future__ import annotations

import re

PERSON = "This op is sensitive. The person must allow it themselves. Tell the person and stop."
WITHHELD = "Results were withheld from a source not shared with this provider. Tell the person."
_AUTHORITY = re.compile(
    r"allow\s*=|G6_MCP_SENSITIVE|G6_ALLOW_UNCONFINED|allow_execution|\ballow_sensitive\b|"
    r"\b(?:confirm|approve|grant|promote)\s*\(|\bapprover\b|autonomous_low_risk|"
    r"\bg6\s+setup\b|--allow|allow parameter", re.I)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def skill_for_agent(text: str | None) -> str | None:
    """Keep safe prose and examples; remove authority paragraphs and whole matching fenced blocks."""
    if text is None or not _AUTHORITY.search(text):
        return text
    kept, chunk = [], []
    fence = ""

    def finish():
        part = "".join(chunk)
        if not _AUTHORITY.search(part):
            kept.append(part)
        chunk.clear()

    for line in text.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if fence:
            chunk.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) \
                    and not line[marker.end():].strip():
                finish()
                fence = ""
        elif marker:
            finish()
            fence = marker[1]
            chunk.append(line)
        elif not line.strip():
            finish()
            kept.append(line)
        else:
            chunk.append(line)
    finish()
    return "".join(kept)


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
