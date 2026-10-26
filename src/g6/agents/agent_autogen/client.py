# SPDX-License-Identifier: Apache-2.0
"""G6ChatClient and GatedTool: what an AutoGen team talks to and calls when G6 runs it (needs the extra).

:class:`G6ChatClient` is AutoGen's ``ChatCompletionClient`` over one injected ``g6.core.llm`` caller: AutoGen's
messages become one system text and one transcript, sent with ``complete``. ``g6.core.llm`` callers take text, so the
tools AutoGen offers are written into the system text with a fixed reply form (a JSON object ``{"tool_calls":
[{"name", "arguments"}]}``); a reply in that form becomes AutoGen's function calls, any other reply is text. The caller
reports no tokens, so the usage AutoGen reads is zero. No key, no provider and no client of AutoGen's own is used.

:class:`GatedTool` is an AutoGen tool whose every call goes through the run's ``GatedCalls`` (the family's gate,
then the Python callable or the G6 component op): outside a run G6 is running it answers with a refusal, and a call
the gate does not let run answers with the verdict, its code and the reason, which the model reads as the result.
"""
from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import Any

from autogen_core import CancellationToken
from autogen_core.models import (
    AssistantMessage,
    ChatCompletionClient,
    CreateResult,
    FunctionExecutionResultMessage,
    ModelInfo,
    RequestUsage,
    SystemMessage,
    UserMessage,
)
from autogen_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict

from g6.agents._runtime import GatedCalls
from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, LLMError, extract_json

#: The run's gated calls, set by the adapter in the thread that runs the team.
CURRENT: ContextVar[GatedCalls | None] = ContextVar("g6_autogen_calls", default=None)
NOT_IN_A_RUN = "G6_E_AGENT_AUTOGEN_NOT_IN_A_RUN"
_TOOLS = ("You can call tools. To call one or more, reply with only a JSON object and nothing else, of the form "
          '{"tool_calls": [{"name": "<tool name>", "arguments": {<arguments>}}]}. Otherwise reply in plain text. '
          "The tools:")
_INFO: ModelInfo = {"vision": False, "function_calling": True, "json_output": False, "family": "unknown",
                    "structured_output": False, "multiple_system_messages": True}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p if isinstance(p, str) else f"{getattr(p, 'name', 'call')}({getattr(p, 'arguments', '')})"
                        for p in content)
    return str(content)


def _schema(tool: Any) -> tuple[str, str, dict[str, Any]]:
    schema = tool.schema if hasattr(tool, "schema") else tool
    return (str(schema.get("name", "")), str(schema.get("description", "")),
            dict((schema.get("parameters") or {}).get("properties") or {}))


def render(messages: Sequence[Any], tools: Sequence[Any] = ()) -> tuple[str, str]:
    """AutoGen's messages (and tools) as the system text and the transcript one ``complete`` call takes."""
    system: list[str] = []
    lines: list[str] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            system.append(message.content)
        elif isinstance(message, UserMessage):
            lines.append(f"[{message.source}] {_text(message.content)}")
        elif isinstance(message, AssistantMessage):
            lines.append(f"[{message.source}] {_text(message.content)}")
        elif isinstance(message, FunctionExecutionResultMessage):
            lines += [f"[tool result {r.name}] {r.content}" for r in message.content]
    if tools:
        system.append("\n".join([_TOOLS, *(f"- {name}: {about} Arguments: {json.dumps(props)}"
                                           for name, about, props in map(_schema, tools))]))
    return "\n\n".join(system), "\n\n".join(lines)


def tool_calls(text: str) -> list[Any] | None:
    """The function calls a reply asks for, in the fixed form; ``None`` when it is plain text."""
    from autogen_core import FunctionCall

    if "tool_calls" not in text:
        return None
    try:
        found = extract_json(text)
    except LLMError:
        return None
    calls = found.get("tool_calls") if isinstance(found, dict) else None
    if not isinstance(calls, list) or not calls:
        return None
    out = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("name"), str):
            return None
        arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
        out.append(FunctionCall(id=uuid.uuid4().hex[:12], name=call["name"], arguments=json.dumps(arguments)))
    return out


class G6ChatClient(ChatCompletionClient):
    """See the module docstring."""

    def __init__(self, llm: LLMCaller, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        self.llm, self.max_tokens = llm, int(max_tokens)
        self.calls = 0
        self._usage = RequestUsage(prompt_tokens=0, completion_tokens=0)

    async def create(self, messages: Sequence[Any], *, tools: Sequence[Any] = (), tool_choice: Any = "auto",
                     json_output: Any = None, extra_create_args: Mapping[str, Any] | None = None,
                     cancellation_token: CancellationToken | None = None) -> CreateResult:
        if os.environ.get("G6_DISABLE_LLM"):
            raise LLMError("G6_E_LLM_DISABLED: model calls are disabled by G6_DISABLE_LLM")
        offered = list(tools) if tool_choice != "none" else []
        system, transcript = render(messages, offered)
        self.calls += 1
        text = self.llm.complete(system, transcript, max_tokens=self.max_tokens)
        calls = tool_calls(text) if offered else None
        if calls:
            return CreateResult(finish_reason="function_calls", content=calls, usage=self._usage, cached=False)
        return CreateResult(finish_reason="stop", content=text, usage=self._usage, cached=False)

    async def create_stream(self, messages: Sequence[Any], *, tools: Sequence[Any] = (), tool_choice: Any = "auto",
                            json_output: Any = None, extra_create_args: Mapping[str, Any] | None = None,
                            cancellation_token: CancellationToken | None = None
                            ) -> AsyncGenerator[str | CreateResult, None]:
        yield await self.create(messages, tools=tools, tool_choice=tool_choice, json_output=json_output,
                                extra_create_args=extra_create_args, cancellation_token=cancellation_token)

    async def close(self) -> None:
        return None

    def actual_usage(self) -> RequestUsage:
        return self._usage

    def total_usage(self) -> RequestUsage:
        return self._usage

    def count_tokens(self, messages: Sequence[Any], *, tools: Sequence[Any] = ()) -> int:
        system, transcript = render(messages, tools)
        return (len(system) + len(transcript)) // 4

    def remaining_tokens(self, messages: Sequence[Any], *, tools: Sequence[Any] = ()) -> int:
        return max(0, 200_000 - self.count_tokens(messages, tools=tools))

    @property
    def capabilities(self) -> Any:  # AutoGen's older name for model_info
        return {k: _INFO[k] for k in ("vision", "function_calling", "json_output")}

    @property
    def model_info(self) -> ModelInfo:
        return dict(_INFO)  # type: ignore[return-value]


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="allow")


class GatedTool(BaseTool[_Arguments, str]):
    """An AutoGen tool decided by G6's gate before each call; see the module docstring. ``fn`` is the tool (a Python
    callable); without one, ``name`` is a G6 component's op (``component.op``)."""

    def __init__(self, name: str, fn: Callable[..., Any] | None = None, description: str = "") -> None:
        super().__init__(_Arguments, str, name, description or f"{name}, decided by G6's gate before it runs")
        self.fn = fn

    async def run(self, args: _Arguments, cancellation_token: CancellationToken) -> str:
        calls = CURRENT.get()
        if calls is None:
            return json.dumps({"verdict": "refused", "code": NOT_IN_A_RUN,
                               "reason": "this tool runs only inside a team that G6 is running"})
        return json.dumps(calls.call(self.name, dict(args.model_extra or {}), fn=self.fn), default=str)


__all__ = ["CURRENT", "NOT_IN_A_RUN", "G6ChatClient", "GatedTool", "render", "tool_calls"]