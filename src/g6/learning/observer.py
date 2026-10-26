# SPDX-License-Identifier: Apache-2.0
"""Events from a learning run, dispatched to observers. An observer that raises is recorded, never silenced, and
the others still run. Ported from the archive's ``learning_layer/observer.py``; ``JsonlObserver`` takes an explicit
path."""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ObserverFailure:
    event_type: str
    observer_index: int
    error_type: str
    error_message: str


@dataclass(frozen=True)
class LearningEvent:
    event_type: str
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    data: dict[str, Any] = field(default_factory=dict)
    # failures from earlier observers in the same emit travel with the event, so later observers see them
    observer_errors: list[ObserverFailure] = field(default_factory=list)


class LearningObserver(Protocol):
    def on_event(self, event: LearningEvent) -> None: ...


class LogObserver:
    """Logs each event through ``logging``."""

    def __init__(self, logger_name: str = "g6.learning") -> None:
        self._logger = logging.getLogger(logger_name)

    def on_event(self, event: LearningEvent) -> None:
        self._logger.info("[%s] %s", event.event_type, json.dumps(event.data, default=str))


class JsonlObserver:
    """Appends each event as one JSON line to ``path``."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def on_event(self, event: LearningEvent) -> None:
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event_type": event.event_type, "timestamp": event.timestamp, **event.data},
                                default=str) + "\n")


class CallbackObserver:
    def __init__(self, callback: Callable[[LearningEvent], None]) -> None:
        self._callback = callback

    def on_event(self, event: LearningEvent) -> None:
        self._callback(event)


class EventEmitter:
    def __init__(self, observers: list[LearningObserver] | None = None) -> None:
        self._observers: list[LearningObserver] = list(observers or [])
        self.failures: list[ObserverFailure] = []

    def emit(self, event_type: str, **data: Any) -> list[ObserverFailure]:
        """Send ``event_type`` to every observer; return (and keep, in ``failures``) the ones that raised."""
        event = LearningEvent(event_type=event_type, data=data)
        for index, observer in enumerate(self._observers):
            try:
                observer.on_event(event)
            except Exception as exc:  # noqa: BLE001 - recorded on the event and returned, not suppressed
                event.observer_errors.append(ObserverFailure(event_type, index, type(exc).__name__, str(exc)))
        self.failures.extend(event.observer_errors)
        return list(event.observer_errors)


__all__ = ["CallbackObserver", "EventEmitter", "JsonlObserver", "LearningEvent", "LearningObserver", "LogObserver",
           "ObserverFailure"]
