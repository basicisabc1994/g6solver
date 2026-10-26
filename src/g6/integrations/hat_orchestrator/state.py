# SPDX-License-Identifier: Apache-2.0
"""Bounded typed state with atomic replacement and a fail-closed cross-process writer lock."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from g6.core.resources import RESOURCE_LIMIT
from g6.core.storage import state_path
from g6.integrations._boundary import Refusal, json_value

Name = Annotated[str, Field(min_length=1, max_length=100)]
Rank = Annotated[int, Field(strict=True, ge=0, le=1000)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Agent(Record):
    agent_id: Name
    capabilities: list[Name] = Field(max_length=64)
    available: bool
    basis: str = "caller_declared"


class Task(Record):
    task_id: Name
    assigned_agent_id: Name
    required_capabilities: list[Name] = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=4000)
    basis: str = "declared_capabilities_and_availability"
    status: str = "allocated"
    reporter_claim: str = Field("", max_length=100)
    result_claim: Any = None

    @field_validator("result_claim", mode="before")
    @classmethod
    def _json(cls, value: Any) -> Any:
        return json_value(value, 16_384)


class Sync(Record):
    sync_point_id: Name
    required_agents: list[Name] = Field(min_length=1, max_length=64)
    ready_agents: list[Name] = Field(default_factory=list, max_length=64)
    basis: str = "caller_reported_readiness"


class Snapshot(Record):
    agents: dict[Name, Agent] = Field(default_factory=dict, max_length=256)
    tasks: dict[Name, Task] = Field(default_factory=dict, max_length=256)
    sync_points: dict[Name, Sync] = Field(default_factory=dict, max_length=256)


class Policy(Record):
    roster: dict[Name, Rank] = Field(default_factory=dict, max_length=256)
    informational_actions: list[Name] = Field(default_factory=list, max_length=64)


class Store:
    max_bytes = 1_048_576

    @property
    def path(self):
        return state_path("hat_orchestrator", "state.json")

    def read(self) -> Snapshot:
        path = self.path
        if not path.exists():
            return Snapshot()
        with path.open("rb") as stream:
            content = stream.read(self.max_bytes + 1)
        if len(content) > self.max_bytes:
            raise Refusal(RESOURCE_LIMIT, "Orchestrator state exceeds 1 MiB; ask the operator to archive records.")
        return Snapshot.model_validate(json.loads(content))

    @property
    def backup_path(self):
        """The state the last live write replaced: the backup the action gate reads before it allows a write (an
        overwrite with nothing to undo it from is held, ACTION_GATING-01)."""
        return state_path("hat_orchestrator", "state.previous.json")

    def checkpoint(self):
        """Keep the present state as the backup, atomically, and return the backup's path: the state file's own bytes,
        or, before the first write, the empty state the store reads when there is no file."""
        path, kept = self.path, self.backup_path
        kept.parent.mkdir(parents=True, exist_ok=True)
        content = Snapshot().model_dump_json().encode("utf-8")
        if path.exists():
            with path.open("rb") as stream:
                content = stream.read(self.max_bytes + 1)
            if len(content) > self.max_bytes:
                raise Refusal(RESOURCE_LIMIT, "Orchestrator state exceeds 1 MiB; ask the operator to archive records.")
        temporary = state_path("hat_orchestrator", f"previous-{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(kept)
        finally:
            temporary.unlink(missing_ok=True)
        return kept

    def policy(self) -> Policy:
        path = state_path("hat_orchestrator", "operator-roster.json")
        if not path.exists():
            raise Refusal("G6_E_HAT_ROSTER_MISSING", "No operator roster is configured.")
        with path.open("rb") as stream:
            content = stream.read(65_537)
        if len(content) > 65_536:
            raise Refusal(RESOURCE_LIMIT, "Operator roster exceeds 64 KiB.")
        return Policy.model_validate(json.loads(content))

    @contextmanager
    def transaction(self):
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = state_path("hat_orchestrator", "write.lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise Refusal("G6_E_HAT_STATE_BUSY", "Another writer or an interrupted writer holds the state lock.",
                          "Ask the operator to inspect write.lock; remove it only after confirming no writer is active.") from None
        temporary = state_path("hat_orchestrator", f"state-{uuid4().hex}.tmp")
        try:
            os.close(fd)
            snapshot = self.read()
            yield snapshot
            # Validate again after mutation; bounded dictionaries may not grow past 256 records.
            checked = Snapshot.model_validate(snapshot.model_dump())
            encoded = checked.model_dump_json().encode("utf-8")
            if len(encoded) > self.max_bytes:
                raise Refusal(RESOURCE_LIMIT, "Orchestrator state would exceed 1 MiB.")
            self.checkpoint()   # under the lock: the backup is exactly the state this write replaces
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
