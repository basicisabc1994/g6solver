# SPDX-License-Identifier: Apache-2.0
"""What a smolagents agent talks to, runs code in and calls when G6 runs it (needs the extra).

* :class:`G6Model`: smolagents' ``Model`` over one injected ``g6.core.llm`` caller. smolagents' messages become one
  system text and one transcript sent with ``complete``; the reply is cut at the first stop sequence smolagents asked
  for (the caller has none of its own); smolagents reads its own tool-call and code forms from the text. No model
  class of smolagents, no key and no provider is used (AGENT_SMOLAGENTS-04).
* :class:`G6Executor`: smolagents' ``PythonExecutor`` over a
  :class:`~g6.agents.agent_smolagents.sandbox.SandboxSession`, so every code step is screened and run in
  ``g6.core.sandbox`` (AGENT_SMOLAGENTS-02); never smolagents' in-process executor, nor its hosted ones.
* :func:`gated`: a smolagents ``Tool`` whose every call goes first through the run's ``GatedCalls`` (the family's
  gate), over a tool you passed in or a G6 component's op; a call the gate does not let run answers with the verdict,
  its code and the reason, which the model reads as the result.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

from smolagents import Tool
from smolagents.local_python_executor import CodeOutput, PythonExecutor
from smolagents.models import ChatMessage, MessageRole, Model

from g6.agents._runtime import GatedCalls
from g6.agents.agent_smolagents.sandbox import SandboxSession
from g6.core.llm import DEFAULT_MAX_TOKENS, LLMCaller, LLMError


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text")
    return "" if content is None else str(content)


def render(messages: Sequence[Any]) -> tuple[str, str]:
    """smolagents' messages as the system text and the transcript one ``complete`` call takes."""
    system: list[str] = []
    lines: list[str] = []
    for message in messages:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", "")
        role = getattr(role, "value", role)
        content = _text(message.get("content") if isinstance(message, dict) else getattr(message, "content", ""))
        if role == "system":
            system.append(content)
        else:
            lines.append(f"[{role}] {content}")
    return "\n\n".join(system), "\n\n".join(lines)


class G6Model(Model):
    """See the module docstring."""

    def __init__(self, llm: LLMCaller, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> None:
        super().__init__(model_id="g6.core.llm")
        self.llm, self.max_tokens, self.calls = llm, int(max_tokens), 0

    def generate(self, messages: list[Any], stop_sequences: list[str] | None = None,
                 response_format: dict[str, str] | None = None, tools_to_call_from: list[Any] | None = None,
                 **kwargs: Any) -> ChatMessage:
        if os.environ.get("G6_DISABLE_LLM"):
            raise LLMError("G6_E_LLM_DISABLED: model calls are disabled by G6_DISABLE_LLM")
        system, transcript = render(messages)
        self.calls += 1
        text = self.llm.complete(system, transcript, max_tokens=self.max_tokens)
        for stop in stop_sequences or ():
            if stop and stop in text:
                text = text[:text.index(stop)]
        return ChatMessage(role=MessageRole.ASSISTANT, content=text)


class G6Executor(PythonExecutor):
    """See the module docstring."""

    def __init__(self, session: SandboxSession) -> None:
        self.session = session

    def send_tools(self, tools: dict[str, Any]) -> None:
        extra = sorted(set(tools) - {"final_answer"})
        if extra:
            raise ValueError(f"a code agent's code runs in G6's sandbox, which cannot call tools: {extra}")

    def send_variables(self, variables: dict[str, Any]) -> None:
        self.session.variables = dict(variables)

    def __call__(self, code_action: str) -> CodeOutput:
        output, printed, final = self.session.run(code_action)
        return CodeOutput(output=output, logs=printed, is_final_answer=final)


def gated(calls: GatedCalls, declared: str, inner: Tool | None = None) -> Tool:
    """A smolagents tool, decided by the gate before each call: ``inner`` (a tool you passed in, named ``declared``),
    or the G6 component op ``declared`` (``component.op``), which the model calls as ``component__op`` with its
    parameters in ``params``."""
    if inner is not None:
        name, about, inputs, output = inner.name, inner.description, dict(inner.inputs), inner.output_type
    else:
        name, output = declared.replace(".", "__"), "object"
        about = f"Calls the G6 op {declared}, decided by G6's gate first. Give the op's parameters in params."
        inputs = {"params": {"type": "object", "description": "the op's parameters", "nullable": True}}

    class GatedTool(Tool):
        skip_forward_signature_validation = True

        def __init__(self) -> None:
            self.name, self.description, self.inputs, self.output_type = name, about, inputs, output
            super().__init__()

        def forward(self, *args: Any, **kwargs: Any) -> Any:
            if inner is not None:
                record = calls.call(declared, kwargs, fn=lambda **a: inner.forward(**a))
            else:
                record = calls.call(declared, kwargs.get("params") or {})
            if record["verdict"] == "run" and "result" in record:
                return record["result"]
            return f"[{record['code']}] {record['verdict']}: {record.get('reason') or record.get('error', '')}"

    return GatedTool()


__all__ = ["G6Executor", "G6Model", "gated", "render"]