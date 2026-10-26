# SPDX-License-Identifier: Apache-2.0
"""Host-supplied path globs and time-limited exemptions; no file loader or approval input."""
from __future__ import annotations

from datetime import UTC, datetime
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _normal_path(value: str) -> str:
    if (not isinstance(value, str) or not value.strip() or value.startswith(("/", "\\"))
            or ":" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("paths must be relative repository paths")
    parts = value.replace("\\", "/").split("/")
    if ".." in parts:
        raise ValueError("parent traversal is ambiguous")
    normal = "/".join(p for p in parts if p not in {"", "."}).casefold()
    if not normal:
        raise ValueError("empty path")
    return normal


def _matches(path: str, pattern: str) -> bool:
    pattern = _normal_path(pattern)
    if pattern.endswith("/**") and not any(c in pattern[:-3] for c in "*?["):
        base = pattern[:-3]
        return path == base or path.startswith(base + "/")
    if not any(c in pattern for c in "*?["):
        return path == pattern or path.startswith(pattern + "/")
    return fnmatchcase(path, pattern) or (pattern.startswith("**/") and fnmatchcase(path, pattern[3:]))


class PathExemption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    pattern: str = Field(min_length=1, max_length=2000)
    expires_at: datetime

    @field_validator("pattern")
    @classmethod
    def valid_pattern(cls, value: str) -> str:
        return _normal_path(value)

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("exemption expiry needs an explicit timezone")
        return value.astimezone(UTC)


class ProtectedPathsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    protected: tuple[str, ...] = Field(max_length=1000)
    exemptions: tuple[PathExemption, ...] = Field(default=(), max_length=1000)

    @field_validator("protected")
    @classmethod
    def valid_patterns(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_normal_path(p) for p in values)


def classify_paths(paths, policy: ProtectedPathsPolicy, *, now: datetime | None = None) -> dict[str, str]:
    """Lexical repository-relative matching only; no filesystem access or symlink resolution."""
    if not isinstance(policy, ProtectedPathsPolicy):
        raise ValueError("explicit ProtectedPathsPolicy required")
    if isinstance(paths, (str, bytes)) or paths is None:
        raise ValueError("a non-empty path list is required")
    paths = tuple(paths)
    if not paths:
        raise ValueError("a non-empty path list is required")
    now = now or datetime.now(UTC)
    classified = {}
    for raw in paths:
        path = _normal_path(raw)
        protected = any(_matches(path, pattern) for pattern in policy.protected)
        exempt = protected and any(e.expires_at > now and _matches(path, e.pattern) for e in policy.exemptions)
        classified[path] = "exempt" if exempt else "protected" if protected else "unclassified"
    return classified
