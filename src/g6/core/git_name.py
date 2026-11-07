# SPDX-License-Identifier: Apache-2.0
"""A path as git names it, as a type that cannot become text by accident, and the one rule by which it does.

A name git gives is untrusted: any byte but NUL and ``/`` may be in it, a line break or a byte that is not UTF-8 too. Every
place G6 put one into text by its own spelling was a place two names could read as one, or a name could write a line of
its own (review-maskfu1 to review-maskfu3). So a listed name is a ``GitName``, made only from git's own bytes by a reader of
git's listings (``g6.workforce.repo_tests``), and it crosses into text one way only:

* for a person or a model, ``shown_name(name)``: git's C-style quoting (``core.quotePath``'s, with UTF-8 kept), so a name is
  always one line and two names with different bytes are always two texts; ``unquoted_name`` reads it back, its exact
  inverse;
* for git, ``name.raw``, its bytes, by an explicit call;
* for the system, ``repo_tests._on_disk``, the bytes as git's producer encodes a name there.

``str()``, ``format()`` (an f-string, ``str.format``), ``%`` and ``repr()`` of a ``GitName`` raise ``GitNameAsText``.
It is a ``str`` with the name's own value (git's bytes, each that is not UTF-8 a lone surrogate, as the listing's reader
has always read them), so it compares, sorts, hashes, joins a path and is written to JSON as the name it always was, and
a persisted field holds what it held. ``str.join`` and ``+`` have no hook, so every one is read from the code instead
(``tests/harness/test_git_names.py``'s inventory, by the flow of this type). A caller's or a model's path is a plain
``str``, never a ``GitName``: ``shown`` writes a ``GitName`` by the rule and any other path as it is.
"""
from __future__ import annotations

import re


class GitNameAsText(Exception):
    """A git name turned into text other than by ``shown_name`` (for a person or a model) or ``GitName.raw`` (for git).
    Not a ``TypeError`` or a ``ValueError``, which code that reads untrusted input catches as a refusal: this is G6's own
    mistake, and nothing should read it as anything else."""


class GitName(str):
    """A path as git named it, made from git's own bytes (``GitName(raw)``) by a reader of git's listings."""

    __slots__ = ()

    def __new__(cls, raw: bytes) -> GitName:
        if not isinstance(raw, bytes):
            raise TypeError("a git name is made from git's own bytes")
        return super().__new__(cls, raw.decode("utf-8", "surrogateescape"))

    @property
    def raw(self) -> bytes:
        """The name's bytes as git gave them: what git is given back, by an explicit call."""
        return self.encode("utf-8", "surrogateescape")

    def __getnewargs__(self) -> tuple[bytes]:
        return (self.raw,)

    def _as_text(self, *args: object) -> str:
        raise GitNameAsText("a git name is put into text only by shown_name, and given to git only as its raw bytes")

    __str__ = __format__ = __repr__ = __mod__ = __rmod__ = _as_text


#: How git's C-style quoting (``quote_c_style``) writes each character it names; ``_octal`` writes the others it escapes.
_ESCAPED = {"\a": "\\a", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\v": "\\v", "\f": "\\f", "\r": "\\r", '"': '\\"', "\\": "\\\\"}


def _octal(ch: str) -> str:
    """``ch`` written as an octal escape for each of its bytes, where git's quoting writes it so, or a line break would cut a
    name: a control character (C0, DEL or C1), U+2028 or U+2029 (``str.splitlines`` ends a line at each), and a byte that is
    not UTF-8 (a lone surrogate as ``_listing`` and ``_paths`` read it); else ``ch`` itself."""
    code = ord(ch)
    if not (code < 0x20 or 0x7F <= code <= 0x9F or code in (0x2028, 0x2029) or 0xD800 <= code <= 0xDFFF):
        return ch
    raw = ch.encode("utf-8", "surrogateescape" if 0xDC80 <= code <= 0xDCFF else "surrogatepass")
    return "".join(f"\\{byte:03o}" for byte in raw)


def shown_name(rel: str, prefix: str = "") -> str:
    """A path git gave, as text: the one rule by which a name is put into what the patch is known by and into what a reader
    is shown. It is git's C-style quoting (``core.quotePath``'s, with UTF-8 kept as it is): a name holding a quote, a
    backslash, a control character, a line break or a byte that is not UTF-8 is written in double quotes, each of those
    escaped as git escapes it (``\\n``, ``\\"``, ``\\\\``, an octal escape for each byte of the others: ``_octal``); any
    other name as it is. ``prefix`` (``a/``, ``b/``) goes inside the quotes, as in git's own patch. So a name is always one
    line, two names with different bytes are always different text, and no name can make a header, a hunk or a G6 line.
    The text is a plain ``str`` (``str``'s own ``+`` makes one), never a ``GitName``."""
    path = prefix + rel
    shown = "".join(_ESCAPED.get(ch) or _octal(ch) for ch in path)
    return path if shown == path else f'"{shown}"'


def shown(path: str) -> str:
    """A path as text for a person or a model: a ``GitName`` by the one rule (``shown_name``), any other path (a caller's, a
    model's, pytest's, the system's) as it is."""
    return shown_name(path) if isinstance(path, GitName) else path


#: Each character ``shown_name`` writes as a letter's escape, by that letter.
_UNESCAPED = {escaped[1]: ch for ch, escaped in _ESCAPED.items()}


def unquoted_name(shown: str) -> str:
    """A name as ``shown_name`` writes it, read back: the one reader of the one quoting rule, its exact inverse, so that
    ``unquoted_name(shown_name(rel, prefix)) == prefix + rel`` for every name a listing gives (repair 1's open item B). A
    bare name is itself; a quoted one has each escape read back (a letter to its character, an octal escape to its byte),
    and its bytes are then read as a listing's are (UTF-8, each byte that is not a lone surrogate). Text the rule never
    writes, such as an escape it has no use for, a quote or a control character left bare, or a quote left open, raises
    ``ValueError``: what it would name is not known."""
    name = shown
    if len(shown) >= 2 and shown[0] == shown[-1] == '"':
        body, out, at = shown[1:-1], bytearray(), 0
        while at < len(body):
            if body[at] != "\\":
                out += body[at].encode("utf-8")       # a lone surrogate here raises (UnicodeEncodeError is a ValueError)
                at += 1
            elif body[at + 1:at + 2] in _UNESCAPED:
                out += _UNESCAPED[body[at + 1]].encode("utf-8")
                at += 2
            elif re.fullmatch("[0-3][0-7]{2}", body[at + 1:at + 4]):
                out.append(int(body[at + 1:at + 4], 8))
                at += 4
            else:
                raise ValueError(f"an escape the name rule never writes: {body[at:at + 4]!r}")
        name = bytes(out).decode("utf-8", "surrogateescape")
    if shown_name(name) != shown:
        raise ValueError(f"text the name rule never writes: {shown[:80]!r}")
    return name
