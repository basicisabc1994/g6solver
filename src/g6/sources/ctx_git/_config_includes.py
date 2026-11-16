# SPDX-License-Identifier: Apache-2.0
"""Parse include paths without letting git open files to discover them.

This accepts git's quoted values, comments, continuations and both subsection forms.
Malformed or undecodable text is refused by the caller, never silently skipped.
"""
import re
from collections.abc import Iterator

_SECTION = re.compile(r'^\[([A-Za-z0-9.-]+)(?:[ \t]+"(?:[^"\\]|\\.)*")?[ \t]*\]')
_KEY = re.compile(r'([A-Za-z][A-Za-z0-9-]*)(.*)')
_ESCAPES = {"n": "\n", "t": "\t", "b": "\b", "\\": "\\", '"': '"'}


def _lines(text: str) -> Iterator[str]:
    """Drop escaped newlines except inside comments; retain other escapes for value parsing."""
    line = []
    quoted = comment = False
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if char == "\n":
            yield "".join(line)
            line = []
            quoted = comment = False
        elif char == "\\" and not comment and index < len(text):
            following = text[index]
            index += 1
            if following != "\n":
                line.extend((char, following))
        else:
            if not comment:
                if char == '"':
                    quoted = not quoted
                elif not quoted and char in "#;":
                    comment = True
            line.append(char)
    if line:
        yield "".join(line)


def _value(text: str) -> str:
    value: list[tuple[str, bool]] = []
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        index += 1
        if not quoted and char in "#;":
            break
        if char == '"':
            quoted = not quoted
        elif char == "\\":
            if index == len(text) or text[index] not in _ESCAPES:
                raise ValueError("unsupported config escape")
            value.append((_ESCAPES[text[index]], True))
            index += 1
        else:
            value.append((char, quoted))
    if quoted:
        raise ValueError("unterminated config value")
    while value and value[-1][0] in " \t" and not value[-1][1]:
        value.pop()
    return "".join(char for char, _ in value)


def paths(text: str) -> Iterator[str]:
    """All include/includeIf path values, regardless of whether a condition currently matches."""
    if "\x00" in text or "\ufffd" in text:
        raise ValueError("config encoding cannot be classified")
    section = ""
    for line in _lines(text.removeprefix("\ufeff")):
        line = line.lstrip(" \t")
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            match = _SECTION.match(line)
            if match is None:
                raise ValueError("config section cannot be classified")
            section = match[1].split(".", 1)[0].lower()
            line = line[match.end():].lstrip(" \t")
            if not line or line[0] in "#;":
                continue
        match = _KEY.fullmatch(line)
        if match is None or not section:
            raise ValueError("config key cannot be classified")
        rest = match[2].lstrip(" \t")
        if not rest or rest[0] in "#;":
            if section in {"include", "includeif"} and match[1].lower() == "path":
                raise ValueError("config include needs a path")
            continue
        if not rest.startswith("="):
            raise ValueError("config value cannot be classified")
        value = _value(rest[1:].lstrip(" \t"))
        if section in {"include", "includeif"} and match[1].lower() == "path":
            yield value
