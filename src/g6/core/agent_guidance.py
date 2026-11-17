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
_MAX_GUIDANCE_DEPTH = 64
_ISOLATION_ACTION = ("Tell the person G6 cannot run this confined on this machine. "
                     "Setting it up is the person's decision.")


class _ExternalText(str):
    """Process-private ingress receipt. JSON cannot construct one."""

    def __str__(self):
        return self


class _MixedText(str):
    """A host prefix and external quotation retain separate private receipts."""

    def __new__(cls, parts):
        value = super().__new__(cls, "".join(parts))
        value.parts = tuple(parts)
        return value

    def __str__(self):
        return self

    def __getnewargs__(self):
        return (self.parts,)


def is_external(value) -> bool:
    return isinstance(value, _ExternalText)


# Machine-readable payloads are opaque regardless of who constructs the envelope.
_STRUCTURED = frozenset({
    "id", "ids", "call", "calls", "arguments", "args", "params", "params_json",
    "template", "templates", "code", "codes", "fill", "next", "fix_by",
    "request_id", "run_id", "job_id", "task_id", "artifact_digest", "digest",
    "sha256", "component", "op", "operation", "tool", "inputs",
})


def _structured(key):
    return isinstance(key, str) and (key in _STRUCTURED or key.endswith(("_id", "_ids", "_sha256", "_digest")))


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
    """Filter every unmarked text value; only external ingress is exempt.

    Receipts remain private string subtypes until wire serialization. Keeping them
    internally makes repeated filtering and nested envelopes safe. No JSON key,
    success flag or diagnostic code confers an exemption.
    """
    results, active = {}, set()
    todo = [(value, False, False, 0)]
    while todo:
        item, opaque, done, depth = todo.pop()
        if depth > _MAX_GUIDANCE_DEPTH:
            return {"ok": False, "completion_state": "blocked-escalated",
                    "error": "[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person."}
        ident = (id(item), opaque)
        if isinstance(item, _MixedText):
            results[ident] = _MixedText(tuple(part if is_external(part) else text_for_agent(part)
                                             for part in item.parts))
        elif isinstance(item, str):
            results[ident] = item if opaque or is_external(item) else text_for_agent(item)
        elif not isinstance(item, (dict, list, tuple)):
            results[ident] = item
        elif done:
            if isinstance(item, dict):
                out = {key: results[(id(child), opaque or _structured(key))] for key, child in item.items()}
                if not opaque and item.get("code") == "G6_E_ISOLATION_UNAVAILABLE" \
                        and not is_external(item.get("code")):
                    out.update(action=_ISOLATION_ACTION, suggested_action=_ISOLATION_ACTION)
                results[ident] = out
            else:
                children = [results[(id(child), opaque)] for child in item]
                results[ident] = tuple(children) if isinstance(item, tuple) else children
            active.remove(ident)
        elif ident in active:
            return {"ok": False, "completion_state": "blocked-escalated",
                    "error": "[G6_E_RESOURCE_LIMIT] G6 could not safely read the reply guidance. Tell the person."}
        elif ident not in results:
            active.add(ident)
            todo.append((item, opaque, True, depth))
            if isinstance(item, dict):
                todo.extend((child, opaque or _structured(key), False, depth + 1) for key, child in item.items())
            else:
                todo.extend((child, opaque, False, depth + 1) for child in item)
    return results[(id(value), False)]


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
