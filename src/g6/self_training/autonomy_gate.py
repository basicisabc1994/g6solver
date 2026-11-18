# SPDX-License-Identifier: Apache-2.0
"""Owner authority and durable, single-writer merge pacing. Never changes grants."""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict

from g6.core.ledger import ChainLedger, digest


class Autonomy(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True)
    level: Literal['off', 'draft_pr', 'auto_merge'] = 'off'
    research: bool = False
    push: bool = False
    draft_pr: bool = False
    merge: bool = False
    container_signin: bool = False
    digest: bool = False

    def permits(self, action: str) -> bool:
        if self.level == 'off':
            return False
        if action == 'merge':
            return (self.level == 'auto_merge' and self.merge and self.push and self.draft_pr
                    and self.container_signin)
        return action in {'research', 'push', 'draft_pr', 'digest'} and getattr(self, action)


def _pairs(items):
    if len(dict(items)) != len(items):
        raise ValueError('duplicate configuration key')
    return dict(items)


def authority(config: Path | Autonomy) -> Autonomy:
    if isinstance(config, Autonomy):
        return Autonomy.model_validate_json(config.model_dump_json())
    try:
        raw = json.loads(Path(config).read_text(encoding='utf-8'), object_pairs_hook=_pairs)
        return Autonomy.model_validate(raw)
    except FileNotFoundError:
        return Autonomy()
    except ValueError:
        return Autonomy()  # malformed authority never grants anything


def clock(now: float) -> float:
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError('nonnegative finite host clock required')
    return float(now)


class RateState:
    """Reservations precede effects and remain charged on crash or uncertain result.

    Host service owns this state outside mission mounts. Lock files are never
    stolen after crashes: the owner must reconcile them and pending reservations.
    Retain an independent expect_head to detect full history replacement.
    """
    def __init__(self, path=None, *, expect_head=None):
        self.path = Path(path) if path is not None else None
        self.chain = ChainLedger(self.path, expect_head=expect_head, validate=self._validate)
        # The supplied external anchor authenticates reopening. Prefix checking
        # protects this live instance as the service appends subsequent entries.
        self.chain.expect_head = None
        self._lock = threading.Lock()

    @staticmethod
    def _validate(kind, ident, body, seen):
        try:
            if kind not in {'reserve', 'merged', 'rollback', 'revert_pending', 'revert_pr', 'demote', 'attempt'}:
                raise ValueError('unknown merge state event')
            clock(body['at'])
            if ident != digest(body):
                raise ValueError('merge state identity mismatch')
            if seen and body['at'] < max(row['at'] for row in seen.values()):
                raise ValueError('merge state clock moved backwards')
        except (KeyError, ValueError, TypeError) as exc:
            return [str(exc)]
        return []

    @contextmanager
    def exclusive(self):
        with self._lock:
            handle, lock = None, None
            try:
                if self.path is not None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    lock = self.path.with_suffix('.lock')
                    handle = lock.open('x', encoding='utf-8')
                self.chain.reload()
                yield
            finally:
                if handle is not None:
                    handle.close()
                    lock.unlink()

    def rows(self):
        self.chain.reload()
        return self.chain.entries()

    def append(self, kind, *, now, **fields):
        body = dict(at=clock(now), **fields)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.chain.append(kind, digest(body), body)

    def demoted(self):
        return any(row['kind'] == 'demote' for row in self.rows())

    def room(self, *, now, rollback=False):
        clock(now)
        rows = self.rows()
        if rows and now < max(row['body']['at'] for row in rows):
            return False
        if self.demoted():
            return False
        reserves = [r['body'] for r in rows if r['kind'] == 'reserve']
        completed = {r['body']['reservation'] for r in rows if r['kind'] == 'merged'}
        if any(r['reservation'] not in completed for r in reserves):
            return False
        if rollback:
            return True
        if any(0 <= now - r['at'] < 86400 for r in reserves):
            return False
        if sum(0 <= now - r['at'] < 7 * 86400 for r in reserves) >= 4:
            return False
        return not any(r['kind'] in ('rollback', 'revert_pending') and now - r['body']['at'] < 72 * 3600
                       for r in rows)
