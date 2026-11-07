# SPDX-License-Identifier: Apache-2.0
"""Transactional advisory cache; no launch admission and no credential access.

Capture files contain only an allow-listed rate_limits block and its local read
time. The separate normalised cache retains per-window good and attempt times.
All writers order by the time input was observed, rather than worker completion.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from g6.allowance import CLAUDE_VERSION, Reading, Window, profile_token, utc_now
from g6._allowance.readers import capture_block, claude_reading, codex_reading


def _dump(reading: Reading) -> str:
    return json.dumps(asdict(reading), default=lambda value: value.isoformat(), allow_nan=False, separators=(",", ":"))


def _load(body: str) -> Reading:
    data = json.loads(body)
    windows = []
    for row in data.pop("windows"):
        for key in ("reset_at", "observed_at", "attempted_at", "last_good_at"):
            if row.get(key) is not None:
                row[key] = datetime.fromisoformat(row[key])
        windows.append(Window(**row))
    data["attempted_at"] = datetime.fromisoformat(data["attempted_at"])
    return Reading(windows=tuple(windows), **data)


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "readings.sqlite3"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS readings (provider TEXT, profile TEXT, body TEXT NOT NULL, "
                       "PRIMARY KEY(provider, profile))")
            db.execute("CREATE TABLE IF NOT EXISTS refreshes (provider TEXT, profile TEXT, at TEXT NOT NULL, "
                       "PRIMARY KEY(provider, profile))")
            db.execute("CREATE TABLE IF NOT EXISTS notes (name TEXT PRIMARY KEY, digest TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS bindings (provider TEXT PRIMARY KEY, profile TEXT NOT NULL)")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=.1, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def bind(self, provider: str, profile: str) -> None:
        """Owner configuration changed: invalidate old observations, even on switching back."""
        profile_token(profile)
        with self._db() as db:
            old = db.execute("SELECT profile FROM bindings WHERE provider=?", (provider,)).fetchone()
            if old is not None and old[0] != profile:
                db.execute("DELETE FROM readings WHERE provider=?", (provider,))
                db.execute("DELETE FROM refreshes WHERE provider=?", (provider,))
            db.execute("INSERT OR REPLACE INTO bindings VALUES (?,?)", (provider, profile))

    def _save(self, db: sqlite3.Connection, reading: Reading) -> bool:
        binding = db.execute("SELECT profile FROM bindings WHERE provider=?", (reading.provider,)).fetchone()
        if binding is not None and binding[0] != reading.profile:
            return False
        row = db.execute("SELECT body FROM readings WHERE provider=? AND profile=?",
                         (reading.provider, reading.profile)).fetchone()
        old = _load(row[0]) if row else None
        if old is not None and reading.attempted_at < old.attempted_at:
            return False
        tie = old is not None and reading.attempted_at == old.attempted_at and _dump(reading) != _dump(old)
        before = {w.identity: w for w in old.windows} if old else {}
        merged = []
        for w in reading.windows:
            previous = before.pop(w.identity, None)
            # Partial observations may tighten capacity, never relax it. A good
            # timestamp belongs to the last complete observation, independently
            # of a newer valid percentage's own time. Conflicting time ties stay
            # unknown and keep the stronger reported capacity constraint.
            if (w.failure or tie) and previous is not None:
                chosen = (w if w.used_share is not None and
                          (previous.used_share is None or w.used_share >= previous.used_share) else previous)
                reset = chosen.reset_at
                if tie and w.reset_at != previous.reset_at or chosen is previous and w.reset_at != previous.reset_at:
                    reset = None
                w = replace(chosen, reset_at=reset, failure="conflicting observation time" if tie else w.failure,
                            attempted_at=reading.attempted_at, last_good_at=previous.last_good_at)
            elif w.failure:
                w = replace(w, last_good_at=None)
            merged.append(w)
        merged.extend(replace(w, failure="not reported", attempted_at=reading.attempted_at) for w in before.values())
        allowed = reading.ordinary_usage_allowed
        if tie:
            allowed = False if False in (allowed, old.ordinary_usage_allowed) else None
        result = replace(reading, windows=tuple(merged), ordinary_usage_allowed=allowed,
                         failure="conflicting observation time" if tie else reading.failure)
        db.execute("INSERT OR REPLACE INTO readings VALUES (?,?,?)", (result.provider, result.profile, _dump(result)))
        return True

    def save(self, reading: Reading) -> None:
        with self._db() as db:
            self._save(db, reading)

    def load(self, provider: str, profile: str, now: datetime | None = None) -> Reading:
        profile_token(profile)
        try:
            with self._db() as db:
                binding = db.execute("SELECT profile FROM bindings WHERE provider=?", (provider,)).fetchone()
                if binding is not None and binding[0] != profile:
                    return Reading(provider, profile, (), now or utc_now(), failure="profile changed")
                row = db.execute("SELECT body FROM readings WHERE provider=? AND profile=?", (provider, profile)).fetchone()
            if row:
                # Neither persistence nor an owner-selected label establishes sign-in
                # continuity. A cached support claim cannot establish Claude support.
                r = _load(row[0])
                if r.provider != provider or r.profile != profile:
                    raise ValueError("cache binding mismatch")
                return replace(r, continuity_established=False,
                               support_established=r.support_established if provider == "codex" else False)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            return Reading(provider, profile, (), now or utc_now(), failure="cache unavailable")
        return Reading(provider, profile, (), now or utc_now(), failure="no observation")

    def claim_refresh(self, provider: str, profile: str, now: datetime, interval: float) -> bool:
        profile_token(profile)
        with self._db() as db:
            binding = db.execute("SELECT profile FROM bindings WHERE provider=?", (provider,)).fetchone()
            if binding is not None and binding[0] != profile:
                return False
            row = db.execute("SELECT at FROM refreshes WHERE provider=? AND profile=?", (provider, profile)).fetchone()
            if row is not None and (now - datetime.fromisoformat(row[0])).total_seconds() < interval:
                return False
            db.execute("INSERT OR REPLACE INTO refreshes VALUES (?,?,?)", (provider, profile, now.isoformat()))
            return True

    def capture_path(self, profile: str) -> Path:
        digest = hashlib.sha256(profile_token(profile).encode()).hexdigest()
        return self.root / "captures" / digest / ("claude-" + CLAUDE_VERSION + ".json")

    def observe_claude(self, payload: Any, profile: str, now: datetime, version: str) -> None:
        reading = claude_reading(payload, profile, now, version=version)
        block = capture_block(payload) if version == CLAUDE_VERSION else {}
        with self._db() as db:
            if not self._save(db, reading) or not block:
                return
        # Commit the observation before publishing its optional capture. A crash
        # after file replacement must not roll back a reading already captured.
        # Publication has a separate bounded writer transaction, with a binding
        # and ordering recheck, so a late worker cannot publish after a switch.
        with self._db() as db:
            binding = db.execute("SELECT profile FROM bindings WHERE provider=?", ("claude",)).fetchone()
            row = db.execute("SELECT body FROM readings WHERE provider=? AND profile=?", ("claude", profile)).fetchone()
            if binding is not None and binding[0] != profile or row is None:
                return
            if _load(row[0]).attempted_at > now:
                return
            path = self.capture_path(profile)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if datetime.fromisoformat(saved["read_at"]) >= now:
                    return
            temp = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                with temp.open("x", encoding="utf-8", newline="\n") as out:
                    json.dump({"rate_limits": block, "read_at": now.isoformat()}, out, allow_nan=False)
                    out.write("\n")
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)

    def changed_note(self, name: str, digest: str) -> bool:
        with self._db() as db:
            row = db.execute("SELECT digest FROM notes WHERE name=?", (name,)).fetchone()
            if row is not None and row[0] == digest:
                return False
            db.execute("INSERT OR REPLACE INTO notes VALUES (?,?)", (name, digest))
            return True


def refresh_codex(store: Store, profile: str, *, now: Callable[[], datetime] = utc_now,
                  semantics: Mapping[str, str] | None = None) -> None:
    """Only the existing raw reader; never its weekly meter adapter or ledger."""
    from g6.self_maintenance.usage import codex_limits

    try:
        payload = codex_limits()
        reading = codex_reading(payload, profile, now(), semantics=semantics)
    except Exception:
        reading = Reading("codex", profile, (), now(), failure="read failed")
    store.save(reading)


def start_refresh(store: Store, profile: str, now: datetime, interval: float,
                  semantics: Mapping[str, str] | None = None) -> None:
    # The transport has a 30s timeout and joins its I/O thread for at most 5s.
    # An expired lease allows recovery without relying on process-local handles.
    if store.claim_refresh("codex", profile, now, max(35, interval)):
        threading.Thread(target=refresh_codex, args=(store, profile), kwargs={"semantics": semantics},
                         daemon=True, name="g6-allowance-refresh").start()
