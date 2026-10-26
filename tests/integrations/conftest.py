# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for the integrations family's tests: a small SQLite shop database the operator has declared, and a
way to read it back without going through G6."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

ITEMS = [(1, "apple", 3), (2, "pear", 0), (3, "plum", 7)]


@dataclass
class Shop:
    """A SQLite file with one table, ``items``, and the operator's connection to it named ``shop``."""

    path: Path

    def settings(self, **extra: object) -> dict:
        return {"shop": {"backend": "sqlite", "path": str(self.path), **extra}}

    def conns(self, **extra: object):  # noqa: ANN201 - a g6.integrations.database.config.Connections
        from g6.integrations.database import config

        return config.from_mapping(self.settings(**extra))

    def rows(self, sql: str = "SELECT id, name, qty FROM items ORDER BY id") -> list[tuple]:
        con = sqlite3.connect(self.path)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def tables(self) -> list[str]:
        return [r[0] for r in self.rows("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]


@pytest.fixture
def shop(tmp_path: Path) -> Shop:
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, qty INTEGER)")
    con.executemany("INSERT INTO items (id, name, qty) VALUES (?, ?, ?)", ITEMS)
    con.commit()
    con.close()
    return Shop(path)


@pytest.fixture
def scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A G6 home with no database configuration, so the one scratch database is all there is."""
    from g6.integrations.database import config

    home = tmp_path / "home"
    monkeypatch.setenv("G6_HOME", str(home))
    monkeypatch.delenv(config.CONFIG_ENV, raising=False)
    return home
