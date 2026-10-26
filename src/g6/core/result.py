# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""
Result — mvp.core.result

Railway-oriented boundary type. All AIBlock edges return Result[Output].
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Callable, Generic, TypeVar

Output = TypeVar("Output")
T = TypeVar("T")


class Status(Enum):
    OK = auto()
    FAIL = auto()


class Result(Generic[Output]):
    """Lightweight railway Result — wraps a value or an error string."""

    def __init__(self, status: Status, value: Output | None, error: str | None) -> None:
        self._status = status
        self._value = value
        self._error = error

    # --- factories -----------------------------------------------------------

    @classmethod
    def ok(cls, value: Output) -> "Result[Output]":
        return cls(Status.OK, value, None)

    @classmethod
    def fail(cls, error: str | object) -> "Result[Output]":
        return cls(Status.FAIL, None, str(error) if not isinstance(error, str) else error)

    # --- predicates ----------------------------------------------------------

    def is_ok(self) -> bool:
        return self._status is Status.OK

    def is_fail(self) -> bool:
        return self._status is Status.FAIL

    @property
    def is_failure(self) -> bool:
        """Property alias for is_fail() — for ergonomic boolean checks."""
        return self._status is Status.FAIL

    # --- accessors -----------------------------------------------------------

    @property
    def value(self) -> Output:
        if self._status is not Status.OK:
            raise ValueError(f"Result is FAIL: {self._error}")
        return self._value  # type: ignore[return-value]

    def unwrap(self) -> Output:
        """Alias for .value — raises ValueError if FAIL."""
        return self.value

    @property
    def error(self) -> str:
        if self._status is not Status.FAIL:
            raise ValueError("Result is OK — no error.")
        return self._error  # type: ignore[return-value]

    # --- combinators ---------------------------------------------------------

    def map(self, fn: Callable[[Output], T]) -> "Result[T]":
        if self._status is Status.OK:
            try:
                return Result.ok(fn(self._value))  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001
                return Result.fail(str(exc))
        return Result.fail(self._error)  # type: ignore[arg-type]

    def flat_map(self, fn: Callable[[Output], "Result[T]"]) -> "Result[T]":
        if self._status is Status.OK:
            try:
                return fn(self._value)  # type: ignore[arg-type]
            except Exception as exc:  # noqa: BLE001
                return Result.fail(str(exc))
        return Result.fail(self._error)  # type: ignore[arg-type]

    def or_else(self, default: Output) -> Output:
        return self._value if self._status is Status.OK else default  # type: ignore[return-value]

    # --- dunder --------------------------------------------------------------

    def __repr__(self) -> str:
        if self._status is Status.OK:
            return f"Result.ok({self._value!r})"
        return f"Result.fail({self._error!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Result):
            return NotImplemented
        return self._status == other._status and self._value == other._value and self._error == other._error
