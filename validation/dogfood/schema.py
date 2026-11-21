# SPDX-License-Identifier: Apache-2.0
"""One line of the dogfooding log (n=1 dogfooding, not a study). Strict: unknown fields are refused."""
from __future__ import annotations

import datetime as dt
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
_PRIVATE_PATH = re.compile(r"(?i)([a-z]:[\\/]|\\Users\\|/Users/|/home/|\\\\)")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ToolCall(_Strict):
    tool: Text
    phase: Text
    # Place of the action actually taken among g6_select's candidates (1 = first), or null.
    rank: Annotated[int, Field(ge=1)] | None


class Refusal(_Strict):
    code: Annotated[str, StringConstraints(pattern=r"^G6_E_[A-Z0-9_]+$")]
    reroute_resolved: bool


class FalseAlarms(_Strict):
    count: Annotated[int, Field(ge=0)]
    notes: list[Text]

    @model_validator(mode="after")
    def _one_line_each(self) -> "FalseAlarms":
        if len(self.notes) != self.count:
            raise ValueError("false_alarms.notes needs exactly one line per false alarm")
        return self


class Entry(_Strict):
    task_id: Text
    date: dt.date  # UTC calendar date
    repo: Text
    task: Text
    calls: list[ToolCall]
    refusals: list[Refusal]
    # True when a suggested reroute named a tool or op that does not exist.
    reroute_to_absent_tool: bool
    false_alarms: FalseAlarms
    # True when every agentic override asked the person first (also True when there were none).
    overrides_asked_person: bool
    minutes: Annotated[int, Field(ge=0)]
    notes: Annotated[str, StringConstraints(max_length=2000)]

    @model_validator(mode="after")
    def _no_private_paths(self) -> "Entry":
        for name in ("repo", "task"):
            if _PRIVATE_PATH.search(getattr(self, name)):
                raise ValueError(f"{name} must not contain a private path")
        return self
