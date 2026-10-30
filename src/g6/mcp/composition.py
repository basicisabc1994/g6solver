# SPDX-License-Identifier: Apache-2.0
"""Data-only MCP composition. No dynamic imports or Python in tool arguments."""
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.core.compose import Stage, compose
from g6.core.door import blocked_reply, door_envelope


class StageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    component: str
    op: str
    inputs: dict[str, str] | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class CompositionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stages: list[StageInput] = Field(min_length=1, max_length=32)
    inputs: dict[str, Any]


def compose_reply(payload: dict, *, allow_sensitive: frozenset[str] = frozenset()) -> dict:
    try:
        request = CompositionInput.model_validate(payload)
        pipeline = compose(*(Stage(s.component, s.op, s.inputs, s.params) for s in request.stages),
                           caller_inputs=tuple(request.inputs), allow_sensitive=allow_sensitive)
        result = pipeline.infer(request.inputs).value
        envelope = door_envelope(result["completion_state"], who="composition", value=result,
                                 evidence=result["evidence"], warning_card=result["warning_card"])
        return {**result, **envelope}
    except ValidationError:
        return blocked_reply("[G6_E_INPUT_VALIDATION] composition needs stages and inputs; unknown fields refused")
    except (ValueError, KeyError, TypeError) as exc:
        return blocked_reply(str(exc))
