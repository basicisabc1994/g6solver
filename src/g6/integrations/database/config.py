# SPDX-License-Identifier: Apache-2.0
"""Which databases G6 may reach: the connections the person who runs the server declares, and no others.

The operator declares connections as JSON, an object of connection name to settings, in the environment variable
``G6_DATABASE_CONNECTIONS`` or in the file ``database/connections.json`` under the G6 home
(``g6.core.storage.state_path``); the variable wins when both are there. With neither there is one connection,
``scratch``: a SQLite file, ``database/scratch.sqlite`` under the G6 home. A configuration that does not parse is
refused whole, and never falls back to scratch.

Each connection's settings (unknown keys are refused):

* ``backend``: ``sqlite`` (the standard library), ``duckdb`` or ``postgresql`` (the ``db`` extra), or a name given to
  :func:`g6.integrations.database.backends.register_backend`;
* ``path``: the file, for sqlite and duckdb. A relative path is under the G6 home's ``database`` folder; ``:memory:``
  gives DuckDB a fresh in-memory database each call, for reading files in ``data_dir``. A path that starts with ``~``
  is refused, never expanded: G6 does not read the home folder, so give the full path;
* ``url``: for postgresql, ``postgresql://user:password@host:port/database``; ``?sslmode=require``, ``verify-ca`` or
  ``verify-full`` asks for TLS with the server's certificate checked;
* ``data_dir``: for duckdb, the only directory its SQL may read files from (none by default; ``~`` is refused too);
* ``read_only``: ``execute`` is refused;
* ``allow_destructive``: a destructive statement (:mod:`~g6.integrations.database.sql` lists them: DROP, TRUNCATE, MERGE,
  REPLACE and upserts among them) and a DELETE or UPDATE of every row may be dry-run, and committed if the action gate
  allows it;
* ``recoverable``: the operator can restore this database (a backup, point-in-time recovery), or would not miss what is
  in it, so the action gate is told a committed change can be undone;
* ``reach``: how far a change to this database reaches, for the action gate: ``none``, ``local``, ``account``,
  ``organization``, ``external_party`` or ``public``; by default ``local`` for a file and unknown for a server;
* ``max_rows``, ``max_cell_chars``, ``max_result_chars``, ``timeout_seconds``: ceilings on a result and on one call; a
  caller may ask for fewer rows, never more.

An agent names a connection; it never gives a path, a URL or a setting. Credentials live only here, and no reply shows
them, nor a connection's path, URL or host.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from g6.core.storage import state_path

CONFIG_ENV = "G6_DATABASE_CONNECTIONS"
CONFIG_FILE = ("database", "connections.json")
SCRATCH = "scratch"
#: A connection's name: a letter, then letters, digits, hyphens or underscores. A path or URL never matches it.
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")
FILE_BACKENDS = ("sqlite", "duckdb")
Reach = Literal["none", "local", "account", "organization", "external_party", "public"]


class ConfigError(ValueError):
    """The operator's database configuration cannot be used. The message never repeats a value from it."""


class ConnectionSettings(BaseModel):
    """One declared connection; see the module docstring."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1, max_length=64)
    path: str = Field("", max_length=4096)
    url: str = Field("", max_length=4096)
    data_dir: str = Field("", max_length=4096)
    read_only: bool = False
    allow_destructive: bool = False
    recoverable: bool = False
    reach: Reach | None = None
    max_rows: int = Field(1000, ge=1, le=100_000)
    max_cell_chars: int = Field(2000, ge=16, le=1_000_000)
    max_result_chars: int = Field(1_000_000, ge=1_000, le=100_000_000)
    timeout_seconds: float = Field(30.0, gt=0, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def _fits_its_backend(self) -> ConnectionSettings:
        if self.backend in FILE_BACKENDS and not self.path:
            raise ValueError(f"a {self.backend} connection needs a path")
        if self.backend == "postgresql" and not self.url:
            raise ValueError("a postgresql connection needs a url")
        if self.url and self.backend != "postgresql":
            raise ValueError("only a postgresql connection takes a url")
        if self.path and self.backend == "postgresql":
            raise ValueError("a postgresql connection takes a url, not a path")
        if self.data_dir and self.backend != "duckdb":
            raise ValueError("only a duckdb connection takes a data_dir")
        if self.url and urlsplit(self.url).scheme not in ("postgresql", "postgres"):
            raise ValueError("a postgresql url starts postgresql://")
        if self.path.startswith("~") or self.data_dir.startswith("~"):
            raise ValueError("a path that starts with ~ is not expanded, because G6 does not read the home folder: "
                             "give the full path, or one relative to the database folder under the G6 home")
        return self

    @property
    def gate_reach(self) -> str:
        """The reach the action gate is told: the operator's, or ``local`` for a file and ``unknown`` for a server."""
        return self.reach or ("local" if self.backend in FILE_BACKENDS else "unknown")

    def location(self) -> Path | None:
        """The database file (``None`` for ``:memory:`` or a backend with no file)."""
        if not self.path or self.path == ":memory:":
            return None
        path = Path(self.path)
        return path if path.is_absolute() else state_path("database", self.path)

    def data_directory(self) -> Path | None:
        """The directory DuckDB may read files from, or ``None``."""
        return Path(self.data_dir).resolve() if self.data_dir else None

    def hidden(self) -> tuple[list[str], list[str]]:
        """What no reply may show: (strings to remove wherever they appear, names to remove as whole words)."""
        anywhere = [s for s in (self.url, self.path, self.data_dir) if s]
        for path in (self.location(), self.data_directory()):
            if path is not None:
                anywhere += [str(path), path.as_posix()]
        words: list[str] = []
        if self.url:
            parts = urlsplit(self.url)
            anywhere += [s for s in (parts.netloc, parts.password and unquote(parts.password)) if s]
            words += [s for s in (parts.username and unquote(parts.username), parts.hostname) if s]
        return sorted(set(anywhere), key=len, reverse=True), sorted(set(words), key=len, reverse=True)


@dataclass(frozen=True)
class Connections:
    """The declared connections by name, and where they came from (``source``)."""

    settings: Mapping[str, ConnectionSettings]
    source: str

    def names(self) -> list[str]:
        return sorted(self.settings)


def _problems(error: ValidationError) -> str:
    """Where each problem is and what kind, never the value (it may hold a password)."""
    return "; ".join(f"{'.'.join(str(p) for p in e.get('loc') or ()) or 'settings'}: {e.get('msg', '')}"
                     for e in error.errors()[:5])


def from_mapping(mapping: Mapping[str, Any], source: str = "the configuration given in Python") -> Connections:
    """Connections from a mapping of name to settings. Raises :class:`ConfigError`."""
    if not isinstance(mapping, Mapping) or not mapping:
        raise ConfigError(f"{source} must be a JSON object of connection names to settings")
    out: dict[str, ConnectionSettings] = {}
    for name, raw in mapping.items():
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ConfigError(f"{source}: a connection name is a letter then letters, digits, - or _ (one is not)")
        if not isinstance(raw, Mapping):
            raise ConfigError(f"{source}: the settings of {name} must be a JSON object")
        try:
            out[name] = ConnectionSettings.model_validate(dict(raw))
        except ValidationError as exc:
            raise ConfigError(f"{source}: the {name} connection: {_problems(exc)}") from None
    return Connections(out, source)


def parse(text: str, source: str) -> Connections:
    """Connections from JSON text. Raises :class:`ConfigError`."""
    try:
        mapping = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{source} is not JSON ({exc.msg} at line {exc.lineno}, column {exc.colno})") from None
    return from_mapping(mapping, source)


def scratch() -> Connections:
    """The one connection there is with no configuration."""
    return Connections({SCRATCH: ConnectionSettings(backend="sqlite", path="scratch.sqlite")},
                       "no configuration, so the scratch database")


def load(environ: Mapping[str, str] | None = None) -> Connections:
    """The operator's connections: ``G6_DATABASE_CONNECTIONS``, else the file under the G6 home, else scratch."""
    env = os.environ if environ is None else environ
    raw = env.get(CONFIG_ENV, "").strip()
    if raw:
        return parse(raw, CONFIG_ENV)
    file = state_path(*CONFIG_FILE)
    if file.is_file():
        return parse(file.read_text(encoding="utf-8"), "the connections file under the G6 home")
    return scratch()


__all__ = ["CONFIG_ENV", "CONFIG_FILE", "FILE_BACKENDS", "NAME", "SCRATCH", "ConfigError", "ConnectionSettings",
           "Connections", "from_mapping", "load", "parse", "scratch"]
