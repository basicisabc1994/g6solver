# SPDX-License-Identifier: Apache-2.0
"""What an arm's model calls cost on one patch, counted as each call happens, and the failure an arm reports when its
run broke from infrastructure before its first model call. Imports nothing of G6, so an arm's child process can use it
before anything else loads.

- ``Meter``: in the driver's own process.
- ``FileMeter``: across a process boundary. Each call is one JSON line appended and flushed at once, so a child killed
  at its limit leaves every call it made; a line cut short by the kill is not counted.

Both count calls, characters and cents (``reading``), and the tokens a call reports by kind (``tokens``, in a
reading only once a call has reported some, so a meter that counts none reads as before). VS-5's MeteredCaller will be
such a meter.
"""
from __future__ import annotations

import json
from pathlib import Path

#: A meter's reading before anything ran.
NOTHING = {"calls": 0, "characters": 0, "cents": 0.0}


class InfrastructureFailure(Exception):
    """An arm's run that failed from infrastructure before its first model call."""


def _added(found: dict, calls, characters, cents, tokens=None) -> dict:
    if calls < 0 or characters < 0 or cents < 0 or any(int(n) < 0 for n in (tokens or {}).values()):
        raise ValueError("a meter only counts up")
    added = {"calls": found["calls"] + int(calls), "characters": found["characters"] + int(characters),
             "cents": found["cents"] + float(cents)}
    if tokens is not None or "tokens" in found:
        added["tokens"] = dict(found.get("tokens") or {})
        for kind, count in (tokens or {}).items():
            added["tokens"][str(kind)] = added["tokens"].get(str(kind), 0) + int(count)
    return added


class Meter:
    """A meter in this process."""

    def __init__(self):
        self._found = dict(NOTHING)

    def add(self, *, calls: int = 1, characters: int = 0, cents: float = 0.0, tokens: dict | None = None) -> None:
        self._found = _added(self._found, calls, characters, cents, tokens)

    def reading(self) -> dict:
        return {**self._found, **({"tokens": dict(self._found["tokens"])} if "tokens" in self._found else {})}


class FileMeter:
    """A meter kept in ``path``, one JSON line per call, which another process can read."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def add(self, *, calls: int = 1, characters: int = 0, cents: float = 0.0, tokens: dict | None = None) -> None:
        _added(dict(NOTHING), calls, characters, cents, tokens)
        line = {"calls": int(calls), "characters": int(characters), "cents": float(cents)}
        if tokens is not None:
            line["tokens"] = {str(kind): int(count) for kind, count in tokens.items()}
        with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line) + "\n")
            handle.flush()

    def reading(self) -> dict:
        found = dict(NOTHING)
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return found
        for line in text.split("\n"):
            try:
                one = json.loads(line)
                found = _added(found, one["calls"], one["characters"], one["cents"], one.get("tokens"))
            except (ValueError, KeyError, TypeError):
                continue   # an empty line, or one a kill cut short
        return found
