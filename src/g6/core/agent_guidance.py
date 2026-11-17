# SPDX-License-Identifier: Apache-2.0
"""Program guidance for agents never teaches them to lift the person's block."""
from __future__ import annotations

import re
from functools import wraps

PERSON = "This op is sensitive. The person must allow it themselves. Tell the person and stop."
WITHHELD = "Results were withheld from a source not shared with this provider. Tell the person."
_AUTHORITY = re.compile(
    r"allow\s*=|G6_MCP_SENSITIVE|G6_ALLOW_UNCONFINED|allow_execution|\ballow_sensitive\b|"
    r"\b(?:confirm|approve|grant|promote)\s*\(|\bapprover\b|autonomous_low_risk|"
    r"\bg6\s+setup\b|--allow|allow parameter|\bicacls\b|/grant\b|\bchmod\b|\bsetfacl\b|"
    r"\b(?:sudo|doas|runas|sysctl|apparmor_parser|aa-complain|setenforce)\b|\bsu\s+-|/etc/", re.I)
_SETTING = re.compile(r"\bG6_(?!E_)[A-Z][A-Z0-9_]*\b|"
                      r"(?i:\bG6_(?!E_)[A-Z][A-Z0-9_]*\s*=|\$env:[A-Z][A-Z0-9_]*|~[/\\]\.g6[/\\]\.env)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MAX_GUIDANCE_DEPTH = 64
_ISOLATION_ACTION = ("Tell the person G6 cannot run this confined on this machine. "
                     "Setting it up is the person's decision.")


class _GuidanceLimit(Exception):
    pass


class _AuthoredText(str):
    """Private construction receipt, with no additional serialized fields."""


def is_authored(value) -> bool:
    return isinstance(value, _AuthoredText)


def _authored(value, depth=0):
    if depth > _MAX_GUIDANCE_DEPTH:
        raise _GuidanceLimit
    if isinstance(value, str):
        # Filter at construction as well as egress: intermediate JSON snapshots,
        # digests and copies cannot lose a receipt and resurrect unsafe prose.
        return _AuthoredText(text_for_agent(value))
    if isinstance(value, dict):
        return {key: _authored(child, depth + 1) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_authored(child, depth + 1) for child in value]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def authored_fields(value: dict, *fields: str) -> dict:
    """A producer explicitly declares the fields it just wrote, never its data.

    The selected fields contain only prose owned by that producer. Callers must
    split mixed records first; passing a component result here is not permitted.
    Status, codes and field names in arbitrary replies establish no authorship.
    """
    out = dict(value)
    try:
        for field in fields:
            if field in out:
                out[field] = _authored(out[field])
    except _GuidanceLimit:
        return {"ok": False, "completion_state": "blocked-escalated",
                "error": _AuthoredText("[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person.")}
    return out


def authored_builder(*fields: str):
    """Declare a G6 prose factory's fields beside its definition."""
    def decorate(build):
        @wraps(build)
        def marked(*args, **kwargs):
            value = build(*args, **kwargs)
            return authored_fields(value, *fields) if isinstance(value, dict) else value
        marked.__g6_authored_fields__ = fields
        return marked
    return decorate


def _instruction(text: str) -> bool:
    return bool(_AUTHORITY.search(text) or _SETTING.search(text))


def _without_split_instructions(text: str) -> str:
    """Drop just the lines touched by a command or assignment split across lines."""
    while True:
        spans = [m.span() for m in _AUTHORITY.finditer(text) if "\n" in m[0]]
        if not spans:
            return text
        kept, offset = [], 0
        for line in text.splitlines(keepends=True):
            end = offset + len(line)
            if not any(start < end and stop > offset for start, stop in spans):
                kept.append(line)
            offset = end
        text = "".join(kept)


def text_for_agent(text: str) -> str:
    """Remove authority sentences, retaining refusal codes and the person's decision."""
    if not _instruction(text):
        return text
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _instruction(sentence):
            kept.extend(re.findall(r"\[G6_E_[A-Z0-9_]+\]", sentence))
        else:
            kept.append(sentence)
    return " ".join(kept)


def reply_for_agent(value, *, _prose: bool = True):
    """Strip private text receipts at any depth, without classifying any data.

    Text was filtered when its producer marked it, before any intermediate hash
    or JSON snapshot. Iteration also preserves deeply nested component values.
    """
    results, active = {}, set()
    todo = [(value, False)]
    while todo:
        item, done = todo.pop()
        ident = id(item)
        if isinstance(item, _AuthoredText):
            results[ident] = str(item)
        elif not isinstance(item, (dict, list, tuple)):
            results[ident] = item
        elif done:
            if isinstance(item, dict):
                results[ident] = {key: results[id(child)] for key, child in item.items()}
            else:
                children = [results[id(child)] for child in item]
                results[ident] = tuple(children) if isinstance(item, tuple) else children
            active.remove(ident)
        elif ident in active:
            return {"ok": False, "completion_state": "blocked-escalated",
                    "error": "[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person."}
        elif ident not in results:
            active.add(ident)
            todo.append((item, True))
            children = item.values() if isinstance(item, dict) else item
            todo.extend((child, False) for child in children)
    return results[id(value)]


def skill_for_agent(text: str | None) -> str | None:
    """Keep safe prose/list lines and examples; remove whole matching fenced blocks."""
    if text is None or not _instruction(text):
        return text
    split_spans = [m.span() for m in _AUTHORITY.finditer(text) if "\n" in m[0]]
    kept, chunk = [], []
    fence = ""
    offset = 0

    def finish():
        part = "".join(chunk)
        if not _instruction(part):
            kept.append(part)
        chunk.clear()

    for line in text.splitlines(keepends=True):
        end = offset + len(line)
        split = any(start < end and stop > offset for start, stop in split_spans)
        offset = end
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
        elif not split:
            # Blank-line-free lists and wrapped lines are independent units.
            clean = text_for_agent(line)
            if clean:
                kept.append(clean + ("\n" if line.endswith("\n") and not clean.endswith("\n") else ""))
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
    text = _without_split_instructions("\n".join(original))
    # Authority often sits in a trailing parenthesis after the op's signature.
    # Remove that clause, then individual semicolon clauses / sentences.
    text = re.sub(r"\([^()]*\)", lambda m: "" if _instruction(m[0]) else m[0], text)
    lines = []
    for line in text.splitlines():
        cleaned = "; ".join(filter(None, (text_for_agent(part).strip() for part in line.split(";"))))
        if cleaned:
            lines.append(cleaned)
    # A command split over lines must not survive clause-level filtering.
    if _AUTHORITY.search("\n".join(lines)):
        lines = text_for_agent("\n".join(lines)).splitlines()
    for entry in corpus.load()["entries"]:
        if entry["kind"] == "op" and entry["component"] == component:
            template = entry["id"][3:] + ": params_json template " + entry["call"]["arguments"]["params_json"]
            if not _instruction(template):
                lines.append(template + (" " + PERSON if entry["sensitive"] else ""))
    return "\n".join(lines) or "No registered operation is available through the G6 door."
