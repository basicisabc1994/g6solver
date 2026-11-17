# SPDX-License-Identifier: Apache-2.0
"""Program guidance for agents never teaches them to lift the person's block."""
from __future__ import annotations

import re

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
# These fields describe G6-authored guidance only. Component results have a
# separate ownership boundary below; their field names never confer ownership.
_PROSE_FIELDS = frozenset({
    "step", "reason", "message", "error", "warning", "warnings", "warning_card",
    "action", "next_actions", "degradation_reason", "help", "description", "text",
    "say", "why", "issues", "limitations", "summary", "result", "setup",
    "suggested_action", "next_step", "risks", "note", "known_limitations", "missing_checks",
    "recommended_next_steps", "block_reason", "required_human_decision", "evidence_collected",
    "safe_next_action", "rollback_or_containment", "install",
})
_TEMPLATES = frozenset({"call", "arguments", "args", "params", "params_json", "fill", "next", "fix_by"})
_G6_CODE = re.compile(r"\bG6_E_[A-Z0-9_]+\b")
_ISOLATION = "G6_E_ISOLATION_UNAVAILABLE"
_ISOLATION_ACTION = ("Tell the person G6 cannot run this confined on this machine. "
                     "Setting it up is the person's decision.")
_MAX_GUIDANCE_DEPTH = 64


class _GuidanceLimit(Exception):
    pass


def _g6_refusal(value: dict) -> bool:
    """A failed reply with a G6 code is program guidance, never a successful result.

    Look through mirrored refusal fields too. This walk is bounded independently
    of Python's stack; calls/arguments and successful component data are opaque.
    """
    if value.get("ok") is not False:
        return False
    todo = [(value, 0)]
    visits = 0
    while todo:
        item, depth = todo.pop()
        visits += 1
        if depth > _MAX_GUIDANCE_DEPTH or visits > 50_000:
            raise _GuidanceLimit
        if isinstance(item, str) and _G6_CODE.search(item):
            return True
        if isinstance(item, dict):
            todo.extend((child, depth + 1) for key, child in item.items()
                        if key not in _TEMPLATES and not (key == "result" and item.get("ok") is True))
        elif isinstance(item, (list, tuple)):
            todo.extend((child, depth + 1) for child in item)
    return False


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


def _guidance(value, *, prose: bool = True, depth: int = 0):
    if depth > _MAX_GUIDANCE_DEPTH:
        raise _GuidanceLimit
    if isinstance(value, str):
        return text_for_agent(value) if prose else value
    if isinstance(value, dict):
        # The door's successful result is the person's data, byte for byte,
        # including nested envelopes, warning cards, and authority-shaped keys.
        # A component failure without a G6 refusal code remains its data too.
        result_is_data = value.get("ok") is True or (value.get("ok") is False and not _g6_refusal(value))
        out = {key: child if key in _TEMPLATES or (key == "result" and result_is_data)
               else _guidance(child, prose=key in _PROSE_FIELDS, depth=depth + 1)
               for key, child in value.items()}
        card = out.get("warning_card")
        if isinstance(card, dict) and card.get("code") == _ISOLATION:
            # errors.ISOLATION_ACTION is bound. Replace its agent-facing copy,
            # including either card spelling, without altering the bound source.
            card["suggested_action"] = _ISOLATION_ACTION
            if "action" in card:
                card["action"] = _ISOLATION_ACTION
        return out
    if isinstance(value, (list, tuple)):
        children = [_guidance(child, prose=prose, depth=depth + 1) for child in value]
        return tuple(children) if isinstance(value, tuple) else children
    return value


def reply_for_agent(value, *, _prose: bool = True):
    """Filter G6's guidance, preserving the ownership boundary of component results.

    Overdeep or cyclic guidance is refused as a whole, never partially returned.
    Successful result data and structured call templates are not traversed.
    """
    try:
        return _guidance(value, prose=_prose)
    except _GuidanceLimit:
        return {"ok": False, "completion_state": "blocked-escalated",
                "error": "[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person."}


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
