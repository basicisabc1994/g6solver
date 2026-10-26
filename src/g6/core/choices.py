# SPDX-License-Identifier: Apache-2.0
"""The person's choices for this computer, made once and kept in ``$G6_HOME/choices.json`` (DECISIONS #40, ruling 10).

``g6 setup`` asks the person; an app (bundle) that runs code asks too, on its first-run page, and keeps the click. An
agent can read a choice (:func:`choice`) but cannot make one: :func:`record` is called only by ``g6.cli.setup`` and
``g6.bundles.server`` (a test pins this), and no MCP tool or component op reaches either. The variable
``G6_ALLOW_UNCONFINED=1`` stays as an override where the server or the command is started.

One choice so far, :data:`UNCONFINED`: whether code G6 runs for the person (a patch, a generated script, a repository's
tests) may run where no operating-system boundary can confine it, with the person's own rights: ``"allow"`` or
``"refuse"``. Each is kept with ``recorded_at`` and ``how`` (``"g6 setup"`` or ``"bundle first-run page"``). A missing
or unreadable file, or an entry that does not read as one of these, is no choice at all.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

FILE = "choices.json"
#: Whether untrusted code may run where no operating-system boundary can confine it.
UNCONFINED = "unconfined_code"
ALLOW, REFUSE = "allow", "refuse"
#: How a choice was made: the command, or a click on an app's first-run page.
SETUP, BUNDLE_PAGE = "g6 setup", "bundle first-run page"
_VALUES = {UNCONFINED: (ALLOW, REFUSE)}
_HOWS = (SETUP, BUNDLE_PAGE)


def path() -> Path:
    """Where the choices are kept: ``$G6_HOME/choices.json``."""
    from g6.core.storage import state_path

    return state_path(FILE)


def _reads(name: str, entry: object) -> bool:
    return (name in _VALUES and isinstance(entry, dict) and entry.get("value") in _VALUES[name]
            and entry.get("how") in _HOWS and isinstance(entry.get("recorded_at"), str))


def read() -> dict[str, dict[str, str]]:
    """Every choice that reads, by name; {} when there is no file or it cannot be read."""
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {name: {key: str(entry[key]) for key in ("value", "recorded_at", "how")}
            for name, entry in data.items() if _reads(name, entry)}


def choice(name: str) -> dict[str, str] | None:
    """The choice ``name`` (``value``, ``recorded_at``, ``how``), or None when none is recorded."""
    return read().get(name)


def record(name: str, value: str, how: str) -> dict[str, str]:
    """Keep the person's answer ``value`` to ``name``, made through ``how``; the other choices stay. The one writer:
    only ``g6 setup`` and an app's first-run page call it."""
    if name not in _VALUES or value not in _VALUES[name]:
        raise ValueError(f"{name!r} takes one of {', '.join(_VALUES.get(name, ()))}, not {value!r}")
    if how not in _HOWS:
        raise ValueError(f"a choice is made through {' or '.join(_HOWS)}, not {how!r}")
    entry = {"value": value, "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"), "how": how}
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = {**read(), name: entry}
    fd, staged = tempfile.mkstemp(prefix=".choices-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
        os.chmod(staged, 0o600)
        os.replace(staged, target)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise
    return entry


__all__ = ["ALLOW", "BUNDLE_PAGE", "FILE", "REFUSE", "SETUP", "UNCONFINED", "choice", "path", "read", "record"]
