# SPDX-License-Identifier: Apache-2.0
"""Bounded learner turns, trusted tool adapters and a data-only procedure interpreter."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from g6.core import llm
from g6.recursion.packets import PublicTask, build_actor_packet, public_task
from g6.recursion.recorder import EpisodeRecorder
from g6.recursion.tasks import TaskSpec

TOOL_INVALID = "G6_E_RSI_TOOL_CALL_INVALID"
PROCEDURE_INVALID = "G6_E_RSI_PROCEDURE_INVALID"
BUDGET_EXHAUSTED = "G6_E_RSI_BUDGET_EXHAUSTED"
RESPONSE_INVALID = "G6_E_RSI_RESPONSE_INVALID"
MAX_TEXT = 200_000
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


class ActorError(ValueError):
    """A stable failure code; exception text from injected code is never surfaced."""

    def __init__(self, code: str, *, observation: Any = None) -> None:
        super().__init__(code)
        self.observation = observation


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _object(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


def _loads(text: str) -> Any:
    if type(text) is not str or len(text) > MAX_TEXT:
        raise ValueError("JSON size or type")
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif is_dataclass(value):
        value = asdict(value)
    return _loads(_canonical(value))


class ActorCaller(llm.OpenAICompatibleCaller):
    """Ollama's OpenAI-compatible endpoint, with structured messages and tools."""

    def __init__(self, model: str = "gpt-oss:20b", *, base_url: str = "http://127.0.0.1:11434/v1",
                 timeout: float = 60) -> None:
        address = urlsplit(base_url)
        if (address.scheme != "http" or address.hostname not in ("127.0.0.1", "::1") or address.username
                or address.password or address.query or address.fragment
                or not math.isfinite(timeout) or not 0 < timeout <= 600):
            raise ActorError("G6_E_RSI_ENDPOINT_REFUSED")
        super().__init__(model, base_url=base_url, api_key=None, timeout=timeout, temperature=0, seed=0)

    def complete_request(self, request: dict) -> dict:
        if request.get("model") != self.model:
            raise ActorError("G6_E_RSI_MODEL_MISMATCH")
        llm._start_model_call()
        return llm._post_json(f"{self.base_url}/chat/completions", request, {}, self.timeout)


def _schema(value: Any, schema: dict) -> None:
    """The small JSON-schema subset used by the fixed tool registry."""
    if not schema:
        return
    kind = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
             "boolean": bool, "null": type(None)}
    expected = types.get(kind)
    if expected is None or (type(value) not in expected if isinstance(expected, tuple) else type(value) is not expected):
        raise ActorError(TOOL_INVALID)
    if "enum" in schema and value not in schema["enum"]:
        raise ActorError(TOOL_INVALID)
    if kind == "object":
        properties = schema.get("properties", {})
        if any(key not in value for key in schema.get("required", ())):
            raise ActorError(TOOL_INVALID)
        if schema.get("additionalProperties", False) is False and set(value) - properties.keys():
            raise ActorError(TOOL_INVALID)
        for key in value.keys() & properties.keys():
            _schema(value[key], properties[key])
    elif kind == "array":
        if len(value) > schema.get("maxItems", 1000):
            raise ActorError(TOOL_INVALID)
        for child in value:
            if "items" in schema:
                _schema(child, schema["items"])
    elif kind == "string" and len(value) > schema.get("maxLength", MAX_TEXT):
        raise ActorError(TOOL_INVALID)
    elif kind in ("integer", "number") and (not math.isfinite(value)
            or value < schema.get("minimum", -1e100) or value > schema.get("maximum", 1e100)):
        raise ActorError(TOOL_INVALID)


def _parameters(properties: dict, required: Sequence[str] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _reference_excluded(reference: str, repo: str) -> bool:
    from g6.core import confine
    from g6.workforce import repo_tests

    try:
        root = Path(repo)
        if not root.is_absolute() or root.drive.startswith("\\\\"):
            return False
        if not reference:
            return True
        hidden = Path(reference)
        if not hidden.is_absolute() or hidden.drive.startswith("\\\\"):
            return False
        root, hidden = root.resolve(), hidden.resolve()
        python = repo_tests.interpreter(root)
        mounts = (root, *(Path(p).resolve() for p in (*confine.interpreter_paths(python),
                                                     *repo_tests._environment_of(python))))
        return not any(hidden.is_relative_to(mount) or mount.is_relative_to(hidden) for mount in mounts)
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Any]

    def wire(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                  "parameters": _plain(self.parameters)}}


class ToolPort:
    """Only trusted registrations are executable; procedures cannot register code."""

    def __init__(self, tools: Sequence[Tool] = ()) -> None:
        self._tools = {}
        for tool in tools:
            if not _NAME.fullmatch(tool.name) or tool.name in self._tools:
                raise ActorError(TOOL_INVALID)
            self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def schemas(self, names: Sequence[str]) -> list[dict]:
        if len(set(names)) != len(names) or any(name not in self._tools for name in names):
            raise ActorError(PROCEDURE_INVALID)
        return [self._tools[name].wire() for name in names]

    def validate(self, name: str, arguments: dict) -> None:
        if name not in self._tools:
            raise ActorError(TOOL_INVALID)
        try:
            _plain(arguments)
            _schema(arguments, self._tools[name].parameters)
        except (TypeError, ValueError, RecursionError, OverflowError):
            raise ActorError(TOOL_INVALID) from None

    def invoke(self, name: str, arguments: dict) -> Any:
        self.validate(name, arguments)
        try:
            result = _plain(self._tools[name].handler(**arguments))
        except Exception:
            raise ActorError("G6_E_RSI_TOOL_FAILED") from None
        if isinstance(result, dict) and (result.get("completion_state") == "blocked-escalated"
                or result.get("status") in ("refused", "timeout", "inconclusive")
                or "verdict" in result and result["verdict"] != "passed"
                or result.get("passed") is False or result.get("ok") is False):
            raise ActorError("G6_E_RSI_TOOL_FAILED", observation=result)
        return result

    @classmethod
    def standard(cls, *, task: TaskSpec | None = None, repo: str | None = None) -> ToolPort:
        """Adapt the existing engines. Public tests never receive a hidden reference."""
        text = {"type": "string"}
        data = {"type": "object", "additionalProperties": True}
        visible = public_task(task) if isinstance(task, TaskSpec) else None
        reference = task.hidden_ref if visible is not None else None

        def run_tests(solution):
            from g6.recursion.tasks import TaskSpec, licence_for, run_checks

            if visible is None or reference is None or repo is None or not _reference_excluded(reference, repo):
                raise ActorError("G6_E_RSI_PUBLIC_TASK_REQUIRED")
            sanitized = TaskSpec(visible.task_id, visible.family, visible.source, licence_for(visible.source),
                                 visible.prompt, visible.public_tests, "", "", "2024-10-01", "eval")
            return run_checks(sanitized, solution, repo=repo)

        def formal(query):
            from g6.formal.facade import solve

            return solve(query)

        def contract(properties, premises=(), timeout=10):
            from g6.formal.contract import check_contract

            return check_contract(properties, premises=premises, timeout=timeout)

        def datalog(program, facts=None, engine="auto", time_limit=10):
            from g6.datalog.datalog_block import DatalogBlock, DatalogInput

            return DatalogBlock().infer(DatalogInput(op="evaluate", program=program, facts=facts or {},
                                                     engine=engine, time_limit=time_limit)).unwrap()

        def cegis(source, examples, domain, max_iterations=20, timeout_s=10):
            from g6.cegis.loop import cegis as synthesize
            from g6.cegis.sketch import Sketch
            from g6.cegis.verify import InputDomain, TableOracle

            result = synthesize(Sketch.from_src(source), TableOracle.of([(e["inputs"], e["output"])
                                                                       for e in examples]),
                                domain=InputDomain.of(domain, max_inputs=1000, seed=0),
                                max_iterations=max_iterations, timeout_s=timeout_s)
            return result.to_envelope()

        def check_plan(steps):
            from g6.navigator.plan import check_plan as check

            return check(steps).to_dict()

        strings = {"type": "array", "items": text, "maxItems": 32}
        seconds = {"type": "number", "minimum": 0.001, "maximum": 60}
        return cls((
            Tool("run_tests", "Run only public task checks in the confined repository runner.",
                 _parameters({"solution": text}, ("solution",)), run_tests),
            Tool("formal", "Solve a typed logical query.", _parameters({"query": data}, ("query",)), formal),
            Tool("contract", "Check SMT properties.", _parameters({"properties": strings, "premises": strings,
                 "timeout": seconds}, ("properties",)), contract),
            Tool("datalog", "Evaluate a public Datalog program.", _parameters({"program": text, "facts": data,
                 "engine": {"type": "string", "enum": ["auto", "python", "z3"]}, "time_limit": seconds},
                 ("program",)), datalog),
            Tool("cegis", "Synthesize against public input/output examples.", _parameters({"source": text,
                 "examples": {"type": "array", "items": _parameters({"inputs": data,
                     "output": {}}, ("inputs", "output"))}, "domain": data,
                 "max_iterations": {"type": "integer", "minimum": 1, "maximum": 20}, "timeout_s": seconds},
                 ("source", "examples", "domain")), cegis),
            Tool("check_plan", "Check a static tool plan; execute nothing.",
                 _parameters({"steps": {"type": "array", "items": data, "maxItems": 32}}, ("steps",)), check_plan),
        ))


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProcedureStep(_Data):
    op: Literal["actor", "tool"] = "actor"
    prompt: str = Field("Solve the public task and submit an answer.", max_length=2000)
    tool: str = ""
    arguments: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def shape(self):
        if ("{{" in self.prompt or "}}" in self.prompt or self.op == "actor" and (self.tool or self.arguments)
                or self.op == "tool" and not _NAME.fullmatch(self.tool)):
            raise ValueError(PROCEDURE_INVALID)
        return self


class ProcedureSpec(_Data):
    steps: tuple[ProcedureStep, ...] = Field(default=(ProcedureStep(),), min_length=1, max_length=32)
    tools: tuple[str, ...] = Field(default=(), max_length=16)
    search_width: int = Field(1, ge=1, le=4, strict=True)
    repair_budget: int = Field(0, ge=0, le=8, strict=True)
    packet_tokens: int = Field(1024, ge=256, le=8192, strict=True)
    stop_rules: tuple[Literal["final", "failure"], ...] = ("final", "failure")
    reasoning_effort: Literal["low", "medium", "high"] = "low"

    @classmethod
    def from_content(cls, content: str) -> ProcedureSpec:
        return cls.model_validate(_loads(content))


class EpisodeBudget(_Data):
    max_requests: int = Field(8, ge=1, le=64, strict=True)
    max_tool_calls: int = Field(16, ge=0, le=128, strict=True)
    max_output_tokens: int = Field(4096, ge=1, le=65536, strict=True)
    tokens_per_request: int = Field(1024, ge=1, le=8192, strict=True)


@dataclass(frozen=True)
class Attempt:
    task_id: str
    status: str
    answer: str = ""
    code: str = ""
    requests: int = 0
    tool_calls: int = 0
    output_tokens: int = 0


class ActorPort:
    def __init__(self, caller: ActorCaller | llm.FakeLLM, *, model_id: str,
                 adapter_id: str | None = None) -> None:
        if not model_id or adapter_id is not None and (type(adapter_id) is not str or not adapter_id.strip()):
            raise ActorError("G6_E_RSI_MODEL_MISMATCH")
        if not isinstance(caller, (ActorCaller, llm.FakeLLM)) or (
                isinstance(caller, ActorCaller) and caller.model != model_id):
            raise ActorError("G6_E_RSI_MODEL_MISMATCH")
        self.caller, self.model_id, self.adapter_id = caller, model_id, adapter_id

    def request(self, packet, *, schemas: list[dict], prompt: str, effort: str, seed: int,
                max_tokens: int) -> dict:
        system = "\n".join(item.value for item in [*packet.authority, *packet.output_contract])
        user = _canonical({"task": [item.value for item in packet.task], "provenance": packet.metadata["provenance"],
                           "step": prompt})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        # Packet pruning keeps groups intact; original turn numbers recover chronological order.
        for item in sorted(packet.tool, key=lambda item: int(item.key.split(":")[1])):
            messages.extend(_loads(item.value))
        return {"model": self.model_id, "messages": messages, "tools": schemas,
                "reasoning_effort": effort, "seed": seed, "temperature": 0, "max_tokens": max_tokens}

    def complete_request(self, request: dict) -> dict:
        if isinstance(self.caller, ActorCaller):
            return self.caller.complete_request(request)
        text = self.caller.complete("Return an OpenAI chat-completions response object.", _canonical(request),
                                    max_tokens=request["max_tokens"])
        try:
            return _loads(text)
        except (TypeError, ValueError, RecursionError):
            raw = text[:MAX_TEXT] if isinstance(text, str) else None
            raise ActorError(RESPONSE_INVALID, observation={"raw_response": raw}) from None


def _turn(reply: dict, tools: ToolPort, allowed: tuple[str, ...]) -> tuple[dict, list[tuple[str, str, dict]]]:
    try:
        choices = reply["choices"]
        if type(choices) is not list or len(choices) != 1 or choices[0].get("finish_reason") not in ("stop", "tool_calls"):
            raise ActorError(RESPONSE_INVALID)
        message = choices[0]["message"]
        if type(message) is not dict or message.get("role") != "assistant":
            raise ActorError(RESPONSE_INVALID)
        content, calls = message.get("content"), message.get("tool_calls", [])
        if (content is not None and type(content) is not str or type(calls) is not list
                or content and ("<|" in content or len(content) > MAX_TEXT)
                or not calls and not (content and content.strip())):
            raise ActorError(RESPONSE_INVALID)
        parsed, ids = [], set()
        for call in calls:
            try:
                ident, function = call["id"], call["function"]
                name, arguments = function["name"], _loads(function["arguments"])
                if (call["type"] != "function" or type(ident) is not str or not ident or len(ident) > 128
                        or ident in ids or name not in allowed):
                    raise ValueError
                tools.validate(name, arguments)
                ids.add(ident)
                parsed.append((ident, name, arguments))
            except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
                raise ActorError(TOOL_INVALID) from None
        return message, parsed
    except ActorError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, RecursionError):
        raise ActorError(RESPONSE_INVALID) from None


def run_procedure(task, spec: ProcedureSpec | Mapping | str, *, tools: ToolPort, actor: ActorPort,
                  recorder: EpisodeRecorder, seed: int = 0, budget: EpisodeBudget | None = None) -> Attempt:
    """Run strict data steps. All budgets span every search candidate and repair."""
    visible = public_task(task)
    budget = budget or EpisodeBudget()
    requests = tool_calls = output_tokens = 0
    answer = ""

    def finish(status, code=""):
        result = Attempt(visible.task_id, status, answer if status == "completed" else "", code,
                         requests, tool_calls, output_tokens)
        recorder.record("attempt", asdict(result))
        return result

    def failure(code):
        recorder.record("failure", {"code": code, "request": requests, "tool_calls": tool_calls})

    def dispatch(name, arguments, ident):
        nonlocal tool_calls
        if tool_calls >= budget.max_tool_calls:
            raise ActorError(BUDGET_EXHAUSTED)
        tool_calls += 1
        recorder.record("tool_call", {"id": ident, "name": name, "arguments": arguments})
        try:
            result = tools.invoke(name, arguments)
        except ActorError as exc:
            recorder.record("tool", {"id": ident, "name": name, "result": exc.observation, "code": str(exc)})
            raise
        recorder.record("tool", {"id": ident, "name": name, "result": result})
        return {"role": "tool", "tool_call_id": ident, "content": _canonical(result)}

    try:
        if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
            raise ValueError
        spec = ProcedureSpec.from_content(spec) if isinstance(spec, str) else ProcedureSpec.model_validate(spec)
        schemas = tools.schemas(spec.tools)
        for step in spec.steps:
            if step.op == "tool":
                if step.tool not in spec.tools:
                    raise ValueError
                tools.validate(step.tool, step.arguments)
    except (ValueError, TypeError, RecursionError):
        failure(PROCEDURE_INVALID)
        return finish("failed", PROCEDURE_INVALID)
    recorder.record("start", {"task_id": visible.task_id, "procedure": spec.model_dump(mode="json"), "seed": seed,
                               "budget": budget.model_dump(), "model_id": actor.model_id, "adapter_id": actor.adapter_id})
    last_code = RESPONSE_INVALID
    for candidate in range(spec.search_width):
        observations = []
        answer = ""
        repairs = 0
        candidate_failed = False
        for step in spec.steps:
            if step.op == "tool":
                try:
                    ident = f"step-{tool_calls}"
                    observation = dispatch(step.tool, step.arguments, ident)
                    observations.append(({"role": "assistant", "content": None, "tool_calls": [
                        {"id": ident, "type": "function", "function": {"name": step.tool,
                         "arguments": _canonical(step.arguments)}}]}, observation))
                except ActorError as exc:
                    last_code = str(exc)
                    failure(last_code)
                    if last_code == BUDGET_EXHAUSTED:
                        return finish("budget_exhausted", last_code)
                    candidate_failed = True
                    break
                continue
            while True:
                try:
                    if requests >= budget.max_requests or output_tokens >= budget.max_output_tokens:
                        raise ActorError(BUDGET_EXHAUSTED)
                    packet = build_actor_packet(visible, model_id=actor.model_id, adapter_id=actor.adapter_id,
                                                run_id=recorder.episode_id, budget=spec.packet_tokens,
                                                observations=tuple(observations))
                    recorder.record("packet", packet.model_dump(mode="json"))
                    if packet.used_tokens > packet.budget_tokens:
                        raise ActorError(BUDGET_EXHAUSTED)
                    limit = min(budget.tokens_per_request, budget.max_output_tokens - output_tokens)
                    request = actor.request(packet, schemas=schemas, prompt=step.prompt, effort=spec.reasoning_effort,
                                            seed=(seed + candidate) % (2**31), max_tokens=limit)
                    recorder.record("request", request)
                    requests += 1
                    # Reserve before transport: a missing usage report never grants extra tokens.
                    output_tokens += limit
                    try:
                        reply = actor.complete_request(request)
                    except ActorError as exc:
                        if exc.observation is not None:
                            recorder.record("response", exc.observation)
                        raise
                    except Exception:
                        raise ActorError("G6_E_RSI_ACTOR_FAILED") from None
                    try:
                        reply = _plain(reply)
                    except (TypeError, ValueError, RecursionError, OverflowError):
                        recorder.record("response", {"invalid_json": True})
                        raise ActorError(RESPONSE_INVALID) from None
                    recorder.record("response", reply if isinstance(reply, dict) else {"raw": reply})
                    message, calls = _turn(reply, tools, spec.tools)
                    if not calls:
                        answer = message["content"]
                        if "final" in spec.stop_rules:
                            return finish("completed")
                        observations.append((message,))
                        break
                    if tool_calls + len(calls) > budget.max_tool_calls:
                        raise ActorError(BUDGET_EXHAUSTED)
                    results = []
                    for index, (ident, name, arguments) in enumerate(calls):
                        try:
                            results.append(dispatch(name, arguments, ident))
                        except ActorError as exc:
                            detail = exc.observation if exc.observation is not None else {"code": str(exc)}
                            results.append({"role": "tool", "tool_call_id": ident, "content": _canonical(detail)})
                            for pending_id, _name, _arguments in calls[index + 1:]:
                                results.append({"role": "tool", "tool_call_id": pending_id,
                                                "content": _canonical({"code": "G6_E_RSI_TOOL_NOT_RUN"})})
                            observations.append((message, *results))
                            raise
                    observations.append((message, *results))
                except ActorError as exc:
                    last_code = str(exc)
                    failure(last_code)
                    if last_code == BUDGET_EXHAUSTED:
                        return finish("budget_exhausted", last_code)
                    if "failure" in spec.stop_rules or repairs >= spec.repair_budget:
                        candidate_failed = True
                        break
                    repairs += 1
                    observations.append(({"role": "user", "content": f"Repair the previous failure: {last_code}"},))
            if candidate_failed:
                break
        if not candidate_failed and answer:
            return finish("completed")
        if not candidate_failed:
            last_code = RESPONSE_INVALID
            failure(last_code)
    return finish("failed", last_code)


__all__ = ["ActorCaller", "ActorPort", "Tool", "ToolPort", "ActorError", "ProcedureStep", "ProcedureSpec",
           "EpisodeBudget", "Attempt", "run_procedure", "EpisodeRecorder", "PublicTask", "public_task"]
