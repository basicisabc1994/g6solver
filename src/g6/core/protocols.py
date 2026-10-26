# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""
Protocols — mvp.core.protocols

Structural typing contracts for all AIBlock-compatible components.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from g6.core.result import Result


@runtime_checkable
class Ingestible(Protocol):
    """Can accept raw input and return a Result."""

    def ingest(self, data: Any) -> Result[Any]:  # noqa: ANN401
        ...


@runtime_checkable
class Emittable(Protocol):
    """Can produce output from internal state and return a Result."""

    def emit(self) -> Result[Any]:  # noqa: ANN401
        ...


@runtime_checkable
class Storable(Protocol):
    """Can persist and restore its state."""

    def save(self, path: str) -> Result[str]:
        ...

    def load(self, path: str) -> Result[bool]:
        ...


@runtime_checkable
class Inferrable(Protocol):
    """Can run forward inference given an input."""

    def infer(self, data: Any) -> Result[Any]:  # noqa: ANN401
        ...


@runtime_checkable
class Learnable(Protocol):
    """Can update its parameters given labelled data."""

    def learn(self, data: Any, labels: Any) -> Result[Any]:  # noqa: ANN401
        ...


@runtime_checkable
class InductiveBias(Protocol):
    """Declares its structural assumptions about the hypothesis space."""

    @property
    def bias(self) -> dict[str, Any]:  # noqa: ANN401
        ...


@runtime_checkable
class ConfigurableBias(Protocol):
    """Can accept runtime bias parameter updates from the evolution loop."""

    def configure_bias(self, params: dict[str, Any]) -> None:
        ...


class SourceOpsDescriptor(BaseModel):
    """Declared backend-native source operations and their constraints."""

    model_config = ConfigDict(frozen=True)

    declared_ops: tuple[str, ...] = ()
    source_capabilities: dict[str, Any] = Field(default_factory=dict)
    source_constraints: dict[str, Any] = Field(default_factory=dict)
    source_health: dict[str, Any] = Field(default_factory=dict)
    source_read: dict[str, Any] = Field(default_factory=dict)
    source_mutation_intent: dict[str, Any] = Field(default_factory=dict)
    source_mutation_apply: dict[str, Any] = Field(default_factory=dict)
    source_rollback_support: dict[str, Any] = Field(default_factory=dict)
    source_degradation_report: dict[str, Any] = Field(default_factory=dict)
    required_tools: tuple[str, ...] = ()
    allowed_mutation_surfaces: tuple[str, ...] = ()
    unsupported_ops: tuple[str, ...] = ()
    backend_constraints: dict[str, Any] = Field(default_factory=dict)

    def allows(self, op: str) -> bool:
        return op in set(self.declared_ops)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@runtime_checkable
class SourceOps(Protocol):
    """Backend-native source operation capability surface."""

    def source_capabilities(self) -> Result[Any]:
        ...

    def source_constraints(self) -> Result[Any]:
        ...

    def source_health(self) -> Result[Any]:
        ...

    def source_read(self, data: Any) -> Result[Any]:  # noqa: ANN401
        ...

    def source_mutation_intent(self, data: Any) -> Result[Any]:  # noqa: ANN401
        ...

    def source_mutation_apply(self, data: Any) -> Result[Any]:  # noqa: ANN401
        ...

    def source_rollback_support(self) -> Result[Any]:
        ...

    def source_degradation_report(self) -> Result[Any]:
        ...
