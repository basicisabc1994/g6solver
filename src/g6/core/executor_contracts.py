# SPDX-License-Identifier: Apache-2.0
"""Versioned executor observations and requests, without execution or acceptance authority.

Declaration matching is not qualification. These immutable wire records contain
no stores, credentials, scheduler, approval mechanism or verification decision.
The existing workforce authorizes dispatch and independently judges artifacts.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
Count = Annotated[int, Field(ge=0)]
Positive = Annotated[int, Field(gt=0)]
Seconds = Annotated[float, Field(gt=0)]
Dispatch = Literal["not_sent", "sent", "unknown"]
ExecutionState = Literal["refused", "running", "finished", "failed", "unknown"]
Cancellation = Literal["unsupported", "request_only", "process_termination"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    if len(set(paths)) != len(paths):
        raise ValueError("file paths must be unique")
    for name in paths:
        path = PurePosixPath(name)
        if (not path.parts or path.is_absolute() or ".." in path.parts or ":" in name
                or "\\" in name or path.as_posix() != name or "\x00" in name):
            raise ValueError("file paths must be canonical relative file names")
    return paths


def _json(text: str) -> str:
    def invalid(value):
        raise ValueError(f"non-finite JSON constant {value}")

    value = json.loads(text, parse_constant=invalid)
    if not isinstance(value, (dict, list)):
        raise ValueError("the payload must be a JSON object or array")  # noqa: TRY004 - Pydantic validation error
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False, validate_default=True)
    contract_version: Literal[1] = 1

    @field_validator("contract_version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported executor contract version")
        return value

    @field_validator("*", mode="after")
    @classmethod
    def aware_times(cls, value):
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise ValueError("executor timestamps require a timezone")
        return value

    @model_validator(mode="after")
    def encodable(self):
        # Bindings must be calculable for every admitted wire record, including
        # nested JSON strings and arbitrary instructions (no lone surrogates).
        json.dumps(self.model_dump(mode="json"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        return self

    @property
    def sha256(self) -> str:
        return _sha(json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8"))


class Money(Record):
    """Wire amount in workforce Money's minor units/currency, never a budget authority."""
    amount_minor_units: Count
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class ExecutorUsage(Record):
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    cost: Money | None = None
    cost_source: Name = "unknown"
    token_source: Name = "unknown"
    token_scope: Literal["lower_bound", "complete"] = "lower_bound"
    cost_scope: Literal["unknown", "lower_bound", "complete"] = "unknown"

    @model_validator(mode="after")
    def complete_totals(self):
        if self.token_scope == "complete" and (self.input_tokens is None or self.output_tokens is None):
            raise ValueError("complete token totals require both observed counts")
        if (self.cost_scope == "unknown") != (self.cost is None):
            raise ValueError("cost scope must describe the supplied observation")
        return self

    @property
    def tokens_complete(self) -> bool:
        return self.token_scope == "complete"


class ModelIdentity(Record):
    provider: Name
    model: Name
    revision: str | None = None


class ModelCapabilities(Record):
    adapter_id: Name
    adapter_version: Name
    provider_version: Name
    models: tuple[ModelIdentity, ...]
    features: tuple[Name, ...]
    context_tokens: Positive
    output_tokens: Positive
    execution: Literal["local", "remote"]
    egress: tuple[Name, ...] = ()
    cancellation: Literal["unsupported"] = "unsupported"  # v1 complete() cannot confirm backend cancellation
    reports_usage: bool = False
    reports_cost: bool = False
    qualification_refs: tuple[Name, ...] = ()


class InferenceRequest(Record):
    request_id: Name
    mission_id: Name
    run_id: Name
    standard_sha256: Digest
    model: ModelIdentity
    input_json: Annotated[str, Field(max_length=262144)]
    settings_json: Annotated[str, Field(max_length=16384)] = "{}"
    required_capabilities: tuple[Name, ...]
    max_output_tokens: Positive
    max_input_tokens: Positive
    cost_ceiling: Money
    # Each request represents one dispatch. SDK retries/fanout need separate
    # G6 admission and receipts, never an adapter's private retry authority.
    max_attempts: Annotated[int, Field(ge=1, le=1)] = 1
    timeout_seconds: Seconds
    deadline: datetime
    authorized_egress: tuple[Name, ...] = ()

    _valid_json = field_validator("input_json", "settings_json")(_json)

    @field_validator("settings_json")
    @classmethod
    def settings_object(cls, value):
        if not isinstance(json.loads(value), dict):
            raise ValueError("settings must be a JSON object")  # noqa: TRY004 - Pydantic validation error
        return value


def _lifecycle(record):
    if record.state in ("running", "finished") and record.dispatch != "sent":
        raise ValueError("running or finished execution requires an observed dispatch")
    if record.state == "refused" and record.dispatch != "not_sent":
        raise ValueError("pre-dispatch refusal cannot describe a sent or uncertain request")
    if record.state == "finished" and record.finished_at is None:
        raise ValueError("finished execution requires a finish timestamp")
    if record.state == "running" and record.finished_at is not None:
        raise ValueError("running execution cannot have a finish timestamp")
    times = [t for t in (record.started_at, record.finished_at, record.observed_at) if t is not None]
    if times != sorted(times):
        raise ValueError("execution timestamps are out of order")
    return record


class InferenceReceipt(Record):
    request_sha256: Digest
    requested: ModelIdentity
    observed: ModelIdentity | None = None
    dispatch: Dispatch
    state: ExecutionState
    output_json: Annotated[str, Field(max_length=262144)] | None = None
    observed_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    usage: ExecutorUsage
    receipt_refs: tuple[Name, ...] = ()

    @field_validator("output_json")
    @classmethod
    def output(cls, value):
        return _json(value) if value is not None else None

    @model_validator(mode="after")
    def lifecycle(self):
        _lifecycle(self)
        if self.state == "finished" and self.output_json is None:
            raise ValueError("finished inference requires output")
        return self

    def matches(self, request: InferenceRequest) -> bool:
        """Binding only; not task success, usage accuracy or permission to consume."""
        return self.request_sha256 == request.sha256 and self.requested == self.observed == request.model


class ExecutionLimits(Record):
    max_model_calls: Count
    max_tool_calls: Count
    max_output_tokens: Count
    max_output_bytes: Count
    max_runtime_seconds: Seconds
    cost_ceiling: Money


class AgentPermissions(Record):
    read_files: tuple[Name, ...]
    write_files: tuple[Name, ...] = ()
    tools: tuple[Name, ...]
    network: tuple[Name, ...] = ()
    effects: tuple[Name, ...] = ()

    _file_paths = field_validator("read_files", "write_files")(_paths)

    @model_validator(mode="after")
    def write_scope(self):
        if not set(self.write_files) <= set(self.read_files):
            raise ValueError("write scope must be included in the task's declared workspace files")
        return self


class AgentCapabilities(Record):
    adapter_id: Name
    adapter_version: Name
    executor_id: Name
    executor_version: Name
    models: tuple[ModelIdentity, ...]
    tools: tuple[Name, ...]
    effects: tuple[Name, ...]
    isolation: Name
    environment: Name
    network: bool = False
    status: bool
    artifacts: bool
    cancellation: Cancellation
    resume: bool
    idempotency: bool
    max_output_bytes: Count
    max_runtime_seconds: Seconds
    qualification_refs: tuple[Name, ...] = ()


class AgentRequirements(Record):
    """Mandatory declarations, separate from the permissions an executor may use."""
    effects: tuple[Name, ...] = ()
    isolation: tuple[Name, ...] = ()
    status: bool = True
    artifacts: bool = True
    cancellation: Cancellation = "unsupported"
    resume: bool = False
    idempotency: bool = False


class AgentTask(Record):
    request_id: Name
    owner_id: Name
    mission_id: Name
    run_id: Name
    task_id: Name
    attempt: Positive
    idempotency_key: Name
    adapter_id: Name
    adapter_version: Name
    executor_id: Name
    executor_version: Name
    workspace_id: Name
    baseline_sha256: Digest
    standard_sha256: Digest
    premortem_sha256: Digest
    instructions: Annotated[str, Field(min_length=1, max_length=65536)]
    model: ModelIdentity
    input_refs: tuple[Name, ...] = ()
    permissions: AgentPermissions
    requirements: AgentRequirements = Field(default_factory=AgentRequirements)
    limits: ExecutionLimits
    deadline: datetime


class AgentRunHandle(Record):
    """Correlation only: adapters still need authenticated caller/workspace bindings."""
    owner_id: Name
    mission_id: Name
    run_id: Name
    task_id: Name
    attempt: Positive
    workspace_id: Name
    adapter_id: Name
    adapter_version: Name
    task_sha256: Digest
    backend_id: Name

    @classmethod
    def from_task(cls, task: AgentTask, *, backend_id: str):
        fields = ("owner_id", "mission_id", "run_id", "task_id", "attempt", "workspace_id",
                  "adapter_id", "adapter_version")
        return cls(**{name: getattr(task, name) for name in fields}, task_sha256=task.sha256, backend_id=backend_id)

    def matches(self, task: AgentTask) -> bool:
        return self == self.from_task(task, backend_id=self.backend_id)


class AgentStatus(Record):
    handle: AgentRunHandle
    state: ExecutionState
    dispatch: Dispatch
    observed_at: datetime
    source: Literal["observed", "reported", "mixed"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    effects: Literal["none_observed", "workspace_only_observed", "unknown"] = "unknown"
    progress: str = ""
    error: str = ""
    usage: ExecutorUsage
    receipt_refs: tuple[Name, ...] = ()

    _valid_lifecycle = model_validator(mode="after")(_lifecycle)


class CancellationReceipt(Record):
    handle: AgentRunHandle
    requested_at: datetime
    acknowledged: bool
    process_stopped: bool
    effects: Literal["none_observed", "workspace_only_observed", "unknown"]
    observed_at: datetime | None = None
    reason: str = ""
    receipt_refs: tuple[Name, ...] = ()

    @model_validator(mode="after")
    def observations(self):
        if (self.process_stopped or self.acknowledged) and (self.observed_at is None or not self.receipt_refs):
            raise ValueError("cancellation observations require a timestamp and receipt reference")
        if self.observed_at is not None and self.observed_at < self.requested_at:
            raise ValueError("cancellation observation predates the request")
        return self


class FileArtifact(Record):
    path: Name
    content_sha256: Digest
    size_bytes: Count
    content_ref: Name

    @field_validator("path")
    @classmethod
    def path_valid(cls, value):
        return _paths((value,))[0]

    @classmethod
    def from_text(cls, path: str, text: str):
        raw = text.encode("utf-8")
        digest = _sha(raw)
        return cls(path=path, content_sha256=digest, size_bytes=len(raw), content_ref="sha256:" + digest)


class ArtifactManifest(Record):
    handle: AgentRunHandle
    baseline_sha256: Digest
    files: tuple[FileArtifact, ...]
    receipt_refs: tuple[Name, ...] = ()

    @model_validator(mode="after")
    def unique_files(self):
        _paths(tuple(file.path for file in self.files))
        return self

    def matches(self, task: AgentTask, contents: dict[str, str]) -> bool:
        """Scope and actual-byte binding only; caller separately establishes artifact custody."""
        if not (self.handle.matches(task) and self.baseline_sha256 == task.baseline_sha256
                and {file.path for file in self.files} == set(contents)
                and set(contents) <= set(task.permissions.write_files)):
            return False
        return all(isinstance(contents[file.path], str)
                   and _sha(contents[file.path].encode("utf-8")) == file.content_sha256
                   and len(contents[file.path].encode("utf-8")) == file.size_bytes for file in self.files)


def agent_incompatibilities(task: AgentTask, capabilities: AgentCapabilities) -> tuple[str, ...]:
    """Compare declarations; a match grants neither permission nor measured qualification."""
    failures = []
    if (task.adapter_id, task.adapter_version, task.executor_id, task.executor_version) != (
            capabilities.adapter_id, capabilities.adapter_version, capabilities.executor_id, capabilities.executor_version):
        failures.append("adapter")
    if task.model not in capabilities.models:
        failures.append("model")
    if not set(task.permissions.tools) <= set(capabilities.tools):
        failures.append("tools")
    if task.permissions.network and not capabilities.network:
        failures.append("network")
    if task.limits.max_output_bytes > capabilities.max_output_bytes:
        failures.append("output_bytes")
    if task.limits.max_runtime_seconds > capabilities.max_runtime_seconds:
        failures.append("runtime")
    required = task.requirements
    if not set(required.effects) <= set(capabilities.effects):
        failures.append("effects")
    if required.isolation and capabilities.isolation not in required.isolation:
        failures.append("isolation")
    for feature in ("status", "artifacts", "resume", "idempotency"):
        if getattr(required, feature) and not getattr(capabilities, feature):
            failures.append(feature)
    cancellation = ("unsupported", "request_only", "process_termination")
    if cancellation.index(required.cancellation) > cancellation.index(capabilities.cancellation):
        failures.append("cancellation")
    return tuple(failures)


class ModelProvider(Protocol):
    def capabilities(self) -> ModelCapabilities: ...
    def complete(self, request: InferenceRequest) -> InferenceReceipt: ...


class CodingAgent(Protocol):
    def capabilities(self) -> AgentCapabilities: ...
    def execute(self, task: AgentTask) -> AgentRunHandle: ...
    def status(self, handle: AgentRunHandle) -> AgentStatus: ...
    def cancel(self, handle: AgentRunHandle) -> CancellationReceipt: ...
    def artifacts(self, handle: AgentRunHandle) -> ArtifactManifest: ...
