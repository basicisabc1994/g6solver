# SPDX-License-Identifier: Apache-2.0
"""Find includes in config bytes before Git may read their targets.

Only LF ends a line; CRLF is one line ending and a lone CR stays ordinary
content. Values unrelated to includes are never decoded or interpreted.
"""
import re
from collections.abc import Callable, Iterator

_SPACE = b" \t\r\v\f"
_SECTION = re.compile(rb'^\[([A-Za-z0-9.-]+)(?:[ \t]+"(?:[^"\\]|\\.)*")?[ \t]*\]')
_KEY = re.compile(rb'([A-Za-z][A-Za-z0-9-]*)(.*)', re.S)
_ESCAPES = {ord("n"): 10, ord("t"): 9, ord("b"): 8, ord("\\"): 92, ord('"'): 34}


def _lines(data: bytes, check: Callable[[], None]) -> Iterator[bytes]:
    """Join continuations outside comments, checking the deadline even in long lines."""
    line = bytearray()
    quoted = comment = False
    index = 0
    next_check = 0
    data = data.replace(b"\r\n", b"\n")
    while index < len(data):
        if index >= next_check:
            check()
            next_check = index + 1024
        char = data[index]
        index += 1
        if char == 10:
            yield bytes(line)
            line.clear()
            quoted = comment = False
        elif char == 92 and not comment and index < len(data):
            following = data[index]
            index += 1
            if following != 10:
                line.extend((char, following))
        else:
            if not comment:
                if char == 34:
                    quoted = not quoted
                elif not quoted and char in (35, 59):
                    comment = True
            line.append(char)
    if line:
        yield bytes(line)
    check()


def _value(data: bytes, check: Callable[[], None]) -> bytes:
    value: list[tuple[int, bool]] = []
    quoted = False
    index = 0
    next_check = 0
    while index < len(data):
        if index >= next_check:
            check()
            next_check = index + 1024
        char = data[index]
        index += 1
        if not quoted and char in (35, 59):
            break
        if char == 34:
            quoted = not quoted
        elif char == 92:
            if index == len(data) or data[index] not in _ESCAPES:
                raise ValueError("unsupported config escape")
            value.append((_ESCAPES[data[index]], True))
            index += 1
        else:
            value.append((char, quoted))
    if quoted:
        raise ValueError("unterminated config value")
    while value and value[-1][0] in _SPACE and not value[-1][1]:
        value.pop()
    return bytes(char for char, _ in value)


def file_entries(data: bytes, *, check: Callable[[], None] = lambda: None) -> Iterator[tuple[bytes, bytes]]:
    """Repository includes and native tree-file paths; unrelated values stay opaque.

    Include conditions are not evaluated here: even an inactive repository include
    must remain inside the declared roots. Subsections of core/blame do not name
    their ordinary file settings.
    """
    if b"\x00" in data:
        raise ValueError("config contains a null byte")
    section = b""
    plain_section = False
    for line in _lines(data.removeprefix(b"\xef\xbb\xbf"), check):
        check()
        line = line.lstrip(_SPACE)
        if not line or line[0] in (35, 59):
            continue
        if line.startswith(b"["):
            match = _SECTION.match(line)
            if match is None:
                raise ValueError("config section cannot be classified")
            section = match[1].split(b".", 1)[0].lower()
            plain_section = b'"' not in match[0] and b"." not in match[1]
            line = line[match.end():].lstrip(_SPACE)
            if not line or line[0] in (35, 59):
                continue
        match = _KEY.fullmatch(line)
        if match is None or not section:
            raise ValueError("config key cannot be classified")
        key = section + b"." + match[1].lower()
        include = section in {b"include", b"includeif"} and match[1].lower() == b"path"
        native_file = plain_section and key in {b"core.excludesfile", b"core.attributesfile", b"blame.ignorerevsfile"}
        if not include and not native_file:
            continue
        rest = match[2].lstrip(_SPACE)
        if not rest.startswith(b"="):
            raise ValueError("config file entry needs a path")
        yield key, _value(rest[1:].lstrip(_SPACE), check)


def paths(data: bytes, *, check: Callable[[], None] = lambda: None) -> Iterator[bytes]:
    """Every include path, even when its condition is inactive; no value decoding."""
    for key, value in file_entries(data, check=check):
        if key in {b"include.path", b"includeif.path"}:
            yield value
