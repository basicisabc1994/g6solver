# SPDX-License-Identifier: Apache-2.0
"""Keys the person gives G6, from the one file they write: ``$G6_HOME/.env`` (default ``~/.g6/.env``).

Putting a key in that file is the person's approval for G6 to use it (owner ruling, 2026-10-21): a paid model, a paid
image service, any account an extension reaches. G6 reads keys from nowhere else. A key set in the process environment
for another program (a coding agent's own ``ANTHROPIC_API_KEY``) is not one the person gave G6, and a project's own
``.env`` belongs to that project. G6 never writes, copies or prints the file, and a key's value never appears in a
result, a log or an error; ``g6 doctor`` shows the names only.

The file is ``NAME=value`` lines. ``#`` starts a comment line, ``export`` before a name is allowed, a value may be
wrapped in single or double quotes, an unquoted value ends at `` #`` (a comment after it), and nothing is expanded. An
empty value is no key. A line of any other form is ignored and reported by :func:`problems` by its number, without
its text. The file is read again on every call, so an edit counts at once.

An extension declares each key it needs in ``__g6_keys__``, a tuple of :class:`Key` (its name, what it is for, where
to get one, whether using it spends money; ``g6_describe`` lists them), and asks for one with :func:`require`, which
returns the value or raises :class:`KeyMissing` (``[G6_E_KEY_MISSING]``, with the file and the line to add):
``blocked-escalated``, never a fallback.
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from g6.core.result import Result
from g6.core.storage import g6_home

FILE_NAME = ".env"
KEY_MISSING = "G6_E_KEY_MISSING"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


@dataclass(frozen=True)
class Key:
    """One key an extension needs, which the person puts in their key file."""

    name: str
    purpose: str
    where: str = ""
    paid: bool = False

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError(f"a key's name is letters, digits and underscores; got {self.name!r}")
        if not self.purpose.strip():
            raise ValueError("a key needs a purpose")

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "purpose": self.purpose, "where": self.where, "paid": self.paid,
                "file": str(path())}


class KeyMissing(Exception):
    """A key the person has not put in their key file. Its text is ``[G6_E_KEY_MISSING] message``."""

    code = KEY_MISSING

    def __init__(self, key: Key | str) -> None:
        name = key.name if isinstance(key, Key) else key
        what = f" ({key.purpose})" if isinstance(key, Key) else ""
        where = f" Get one at {key.where}." if isinstance(key, Key) and key.where else ""
        self.message = (f"{name}{what} is not in {path()}. G6 uses a key only when you put it there; add the line "
                        f"{name}=<your key> to that file.{where}")
        super().__init__(f"[{KEY_MISSING}] {self.message}")

    def as_failure(self) -> Result:
        """The failed ``Result`` a component returns for this key (``blocked-escalated`` with its code)."""
        return Result.fail(str(self))


def path() -> Path:
    """The key file: ``.env`` in the state folder."""
    return g6_home() / FILE_NAME


def _value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
        return raw[1:-1]
    return raw.split(" #", 1)[0].rstrip()


def _parse() -> tuple[dict[str, str], list[str]]:
    file = path()
    if not file.exists():
        return {}, []
    try:
        text = file.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"{file} cannot be read ({type(exc).__name__}); G6 uses no key from it"]
    values: dict[str, str] = {}
    seen: dict[str, int] = {}
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.fullmatch(line)
        if match is None:
            found.append(f"line {number} of {file} is not NAME=value; it is ignored")
            continue
        name, value = match.group(1), _value(match.group(2))
        if name in seen:
            found.append(f"{name} is given on line {seen[name]} and again on line {number} of {file}; line {number} "
                         "is used")
        seen[name] = number
        if value:
            values[name] = value
        else:
            values.pop(name, None)
    if os.name != "nt" and file.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
        found.append(f"others can read {file}; make it yours alone: chmod 600 {file}")
    return values, found


def read() -> dict[str, str]:
    """Every name and value in the key file (empty when there is none)."""
    return _parse()[0]


def get(name: str) -> str | None:
    """The value of ``name`` in the key file, or ``None``."""
    return read().get(name)


def present(name: str) -> bool:
    """Whether the key file gives ``name`` a value."""
    return name in read()


def require(key: Key | str) -> str:
    """The value of ``key`` in the key file; :class:`KeyMissing` when the person has not put it there."""
    value = get(key.name if isinstance(key, Key) else key)
    if value is None:
        raise KeyMissing(key)
    return value


def names() -> list[str]:
    """The names the key file gives a value, sorted; never a value."""
    return sorted(read())


def problems() -> list[str]:
    """What is wrong with the key file, by line number and name, never by value."""
    return _parse()[1]


__all__ = ["FILE_NAME", "KEY_MISSING", "Key", "KeyMissing", "get", "names", "path", "present", "problems", "read",
           "require"]
