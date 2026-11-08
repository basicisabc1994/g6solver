# SPDX-License-Identifier: Apache-2.0
"""Allow-listed adapters; provider text never becomes instructions or permission.

Claude's documented shape is tested with synthetic fixtures only. Its support
declaration stays false pending a checked real capture, regardless of a payload's
own claims. Semantics are declarations from owner settings, never inferred from
duration or a rendered label. Codex transport is reused in store.refresh_codex.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from g6.allowance import CLAUDE_SUPPORT, CLAUDE_VERSION, SEMANTICS, Reading, Window, number


def _label_digest(value: Any) -> str:
    """Digest foreign labels without Unicode normalisation or strict encoding."""
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="surrogatepass")
    else:
        try:
            text = repr(value)
        except Exception:
            text = "invalid label"
        # FF cannot occur in UTF-8, including surrogatepass. Wrong-typed labels
        # therefore cannot alias a valid string such as "int:42".
        raw = b"\xff" + (type(value).__name__ + ":" + text).encode("utf-8", errors="backslashreplace")
    return hashlib.sha256(raw).hexdigest()[:24]


def _ambiguous(window: Window, identity: str) -> Window:
    marker = "ambiguous window identity"
    failure = window.failure if marker in window.failure else "; ".join(filter(None, (window.failure, marker)))
    return replace(window, identity=identity, failure=failure, last_good_at=None)


def _append_window(windows: list[Window], identities: dict[str, int], window: Window) -> None:
    """Even an ambiguous malformed label retains its own quota constraint."""
    original = window.identity
    previous = identities.get(original)
    if previous is not None:
        windows[previous] = _ambiguous(windows[previous], original)
        index = len(windows)
        identity = original[:128] + ".duplicate-" + str(index)
        while identity in identities:
            index += 1
            identity = original[:128] + ".duplicate-" + str(index)
        window = _ambiguous(window, identity)
    identities[window.identity] = len(windows)
    windows.append(window)


def instant(value: Any) -> datetime | None:
    try:
        if number(value):
            return datetime.fromtimestamp(value, UTC)
        if isinstance(value, str) and len(value) <= 64:
            at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return at.astimezone(UTC) if at.tzinfo is not None and at.utcoffset() is not None else None
    except (ValueError, OverflowError, OSError):
        pass
    return None


def _window(provider: str, profile: str, identity: str, raw: Any, now: datetime, *,
            version: str, semantics: Mapping[str, str], claude: bool = False) -> Window:
    share = None
    try:
        fields = raw if isinstance(raw, Mapping) else {}
        percent = fields.get("used_percentage" if claude else "usedPercent")
        share = percent / 100 if number(percent) and 0 <= percent <= 100 else None
        return _normalise_window(provider, profile, identity, fields, now, share=share,
                                 version=version, semantics=semantics, claude=claude)
    except Exception:
        # A failed field belongs to this window. Retain a known quota constraint
        # and continue with its siblings; failure never supplies a good time.
        return Window(provider, profile, identity, used_share=share,
                      observed_at=now if share is not None else None, source_version=version,
                      failure="invalid window", attempted_at=now)


def _normalise_window(provider: str, profile: str, identity: str, fields: Mapping, now: datetime, *,
                      share: float | None, version: str, semantics: Mapping[str, str], claude: bool) -> Window:
    minutes = fields.get("windowDurationMins")
    duration = ({"five_hour": 18000, "seven_day": 604800}.get(identity) if claude else
                minutes * 60 if number(minutes) and minutes > 0 and number(minutes * 60) else None)
    reset = instant(fields.get("resets_at" if claude else "resetsAt"))
    errors = []
    if not fields:
        errors.append("missing window")
    if share is None:
        errors.append("missing or invalid share")
    if duration is None:
        errors.append("missing or invalid duration")
    if reset is None:
        errors.append("missing or invalid reset")
    declared = semantics.get(identity, "unknown")
    if not isinstance(declared, str) or declared not in SEMANTICS:
        declared = "unknown"
        errors.append("invalid semantics declaration")
    return Window(provider, profile, identity, duration, share, reset, declared,
                  now if share is not None else None, version, "; ".join(errors), now,
                  last_good_at=now if not errors else None)


def codex_reading(payload: Any, profile: str, now: datetime, *,
                  semantics: Mapping[str, str] | None = None) -> Reading:
    """All reported sets/windows; neither the weekly meter nor its ledger types."""
    data = payload if isinstance(payload, Mapping) else {}
    declared = semantics if isinstance(semantics, Mapping) else {}
    windows = []
    identities: dict[str, int] = {}

    def add(prefix: str, snapshot: Any, *, default_slots: bool = True) -> None:
        fields = snapshot if isinstance(snapshot, Mapping) else {}
        metadata = {"credits", "planType", "limitId", "limitName"}
        for name in dict.fromkeys([*(["primary", "secondary"] if default_slots else []),
                                   *(key for key in fields if key not in metadata)]):
            identity = (prefix + "." + name if name in {"primary", "secondary"} else
                        prefix + ".window-" + _label_digest(name))
            _append_window(windows, identities, _window("codex", profile, identity, fields.get(name), now,
                                                       version="codex/account-rateLimits-read/v1", semantics=declared))

    add("default", data.get("rateLimits"))
    extra = data.get("rateLimitsByLimitId")
    if isinstance(extra, Mapping):
        for label, snapshot in extra.items():
            prefix = "limit-" + _label_digest(label)
            # Additional sets report only the windows actually present, rather than
            # inventing a second absent slot for every limit set.
            before = len(windows)
            add(prefix, snapshot, default_slots=False)
            if len(windows) == before:
                _append_window(windows, identities, _window("codex", profile, prefix + ".missing", None, now,
                                                           version="codex/account-rateLimits-read/v1", semantics=declared))
    permission = data.get("ordinaryUsageAllowed")
    allowed = permission if type(permission) is bool else None
    failure = "invalid additional window sets" if extra is not None and not isinstance(extra, Mapping) else ""
    return Reading("codex", profile, tuple(windows), now, allowed, True, failure)


def claude_reading(payload: Any, profile: str, now: datetime, *, version: str,
                   semantics: Mapping[str, str] | None = None) -> Reading:
    data = payload if isinstance(payload, Mapping) else {}
    block = data.get("rate_limits")
    fields = block if isinstance(block, Mapping) else {}
    supported_shape = version == CLAUDE_VERSION
    declared = semantics if isinstance(semantics, Mapping) else {}
    windows = [_window("claude", profile, name, fields.get(name) if supported_shape else None, now,
                            version="claude-statusline/" + (CLAUDE_VERSION if supported_shape else "unsupported"),
                            semantics=declared, claude=True) for name in ("five_hour", "seven_day")]
    identities = {window.identity: index for index, window in enumerate(windows)}
    for name, value in fields.items():
        if name not in {"five_hour", "seven_day"}:
            identity = "window-" + _label_digest(name)
            _append_window(windows, identities, _window("claude", profile, identity,
                value if supported_shape else None, now,
                version="claude-statusline/" + (CLAUDE_VERSION if supported_shape else "unsupported"),
                semantics=declared, claude=True))
    return Reading("claude", profile, tuple(windows), now, None, False, CLAUDE_SUPPORT)


def capture_block(payload: Any) -> dict[str, dict[str, Any]]:
    """Only quota numbers and valid reset values, suitable for a sanitised capture."""
    block = payload.get("rate_limits") if isinstance(payload, Mapping) else None
    if not isinstance(block, Mapping):
        return {}
    result = {}
    for name in ("five_hour", "seven_day"):
        value = block.get(name)
        if not isinstance(value, Mapping):
            continue
        row = {}
        percent = value.get("used_percentage")
        if number(percent) and 0 <= percent <= 100:
            row["used_percentage"] = percent
        reset = value.get("resets_at")
        if instant(reset) is not None:
            row["resets_at"] = reset
        if row:
            result[name] = row
    return result
