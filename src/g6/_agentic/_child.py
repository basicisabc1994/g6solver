# SPDX-License-Identifier: Apache-2.0
"""The agentic override kernel's confined child, and its launch (design section 4 step 4, calls 10.2 and 11).

The parent starts ``python -I`` running :func:`main` through ``g6.core.component_process.run_contained`` inside a plan
of ``g6.core.confine``, both used unchanged: its own run folder is all it may write, it reads only the interpreter's
folders and G6's package, it has no network, and its environment carries no key, sign-in or model setting. Where no
operating-system backend can confine it, nothing starts (:class:`Refused`, ``G6_E_ISOLATION_UNAVAILABLE``): the
kernel never runs a substitute unconfined, whatever the person allowed for other runs.

The channel is JSON lines, one exchange per run (``run_contained`` takes its input up front): the request goes in on
stdin; the child runs the operation once with each named function replaced by a stub; the stub's first call that has
no answer yet ends the run with one line, ``{"ask": {"call", "digest", "index"}}``; the parent asks the model, checks
the value, and starts the child again with every answer so far, which the stubs replay in order (each call must match
the one asked, by digest, or the run is refused: an operation that is not pure computation cannot replay). The run that
asks nothing more ends with ``{"done": {"ok": result}}``, ``{"done": {"failure": text}}`` or
``{"done": {"raised": text}}``. A ``check`` request runs the component's declared checker on a final answer, with
nothing substituted. Keys and sign-ins stay in the parent: no model is called here.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import sys
import types
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

#: Environment variables the child may see (the interpreter needs these on Windows); nothing else is passed.
_ENV_KEEP = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
_CHILD = "import sys; sys.path.append({folder!r}); from g6._agentic._child import main; sys.exit(main())"
MEMORY_MB = 1024
#: The longest reply line the parent decodes from a child.
REPLY_CHARS = 4_000_000


class Refused(RuntimeError):
    """The child stage did not give an answer; ``str()`` starts with the stable code, and ``isolation`` is the run's
    record when a plan was made."""

    def __init__(self, message: str, isolation: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.isolation = dict(isolation or {})


class _Asked(BaseException):
    """Ends the operation at a call the parent has not answered yet (a BaseException, so ``except Exception`` in the
    operation does not keep it; the ask is recorded before it is raised, so even a swallowed one is reported)."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def call_digest(call: Mapping[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(canonical(call).encode("utf-8")).hexdigest()


def resolve(name: str, package: str) -> Callable[..., Any]:
    """The function ``module:qualname`` names, which must be a plain function defined in ``package`` (the component's
    own code); ValueError otherwise."""
    module_name, _, qualname = name.partition(":")
    if not qualname or not (module_name == package or module_name.startswith(package + ".")):
        raise ValueError(f"{name} is not a function of the component's own package {package}")
    target: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        if not part.isidentifier() or part.startswith("__"):
            raise ValueError(f"{name} is not a plain name")
        target = getattr(target, part)
    if not isinstance(target, types.FunctionType):
        raise ValueError(f"{name} is not a function")
    if target.__module__ != module_name:   # a name imported from elsewhere is that other module's code
        raise ValueError(f"{name} is not defined in {module_name}")
    return target


def return_type(function: Callable[..., Any]) -> Any:
    import typing

    return typing.get_type_hints(function).get("return", Any)


def _json(value: Any) -> Any:
    try:
        return json.loads(canonical(value))
    except (RecursionError, MemoryError):
        raise ValueError("a JSON record exceeds decoder limits") from None


def _component(component: str) -> tuple[Any, str]:
    from g6.core.registry import get_registry

    registry = get_registry()
    ref = registry.refs.get(component)
    if ref is None or ref.source != "builtin":
        raise ValueError(f"{component} is not one of G6's own components")
    return registry, ref.module.split(":", 1)[0]


def serve(request: Mapping[str, Any]) -> dict[str, Any]:
    """One run of the child's work (the seam :func:`main` serves): ``{"ask": ...}``, ``{"done": ...}`` or
    ``{"refused": text}``."""
    registry, package = _component(request["component"])
    if request.get("kind") == "check":
        checker = resolve(request["checker"], package)
        return {"done": {"ok": _json(checker(request["params"], request["result"]))}}
    from pydantic import TypeAdapter

    answers = list(request.get("answers") or ())
    state: dict[str, Any] = {"calls": 0, "ask": None, "diverged": ""}

    def stub_for(name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        adapter = TypeAdapter(return_type(original))

        def stub(*args: Any, **kwargs: Any) -> Any:
            index = state["calls"]
            state["calls"] += 1
            try:
                call = {"function": name, "args": _json(list(args)), "kwargs": _json(kwargs)}
            except (TypeError, ValueError):
                state["diverged"] = f"{name} was called with arguments JSON cannot hold"
                raise _Asked() from None
            digest = call_digest(call)
            if index < len(answers):
                if answers[index].get("call") != digest:
                    state["diverged"] = f"call {index} of {name} is not the call that was answered"
                    raise _Asked()
                return adapter.validate_json(canonical(answers[index]["value"]), strict=True)
            state["ask"] = {"call": call, "digest": digest, "index": index}
            raise _Asked()
        return stub

    from g6._agentic import protected

    module = registry.get_module(request["component"])
    declared = (*tuple(getattr(module, "__g6_verifiers__", ())), getattr(module, "__g6_agentic_check__", "") or "")
    for name in request["functions"]:   # fail closed before any swap: a protected object, whatever name reaches it
        resolve(name, package)          # only the component's own code (ValueError otherwise)
        said = protected.reason(name, verifiers=tuple(v for v in declared if v))
        if said:
            return {"refused": said}
    replaced: list[tuple[Any, str, Any]] = []
    try:
        for name in request["functions"]:
            original = resolve(name, package)
            stub = stub_for(name, original)
            for module in [m for key, m in list(sys.modules.items())
                           if m is not None and (key == package or key.startswith(package + "."))]:
                for attr, value in list(vars(module).items()):
                    if value is original:
                        replaced.append((module, attr, value))
                        setattr(module, attr, stub)
        outcome: Any = None
        raised = ""
        try:
            outcome = registry.invoke(request["component"], request["op"], dict(request["params"]),
                                      block_sensitive=False)
        except _Asked:
            pass
        except Exception as exc:  # noqa: BLE001 - the operation's failure is its outcome, reported to the parent
            raised = f"{type(exc).__name__}: {exc}"[:2000]
    finally:
        for module, attr, value in reversed(replaced):
            setattr(module, attr, value)
    if state["diverged"]:
        return {"refused": f"[G6_E_AGENTIC_NOT_PURE] {state['diverged']}: the operation did not replay"}
    if state["ask"] is not None:
        return {"ask": state["ask"]}
    if raised:
        return {"done": {"raised": raised}}
    if outcome.__class__.__name__ == "Success":
        return {"done": {"ok": _json(outcome.unwrap())}}
    return {"done": {"failure": str(outcome.failure())[:2000]}}


def main() -> int:
    """The confined child's entry: one request on stdin, one JSON line on stdout."""
    try:
        request = json.loads(sys.stdin.read())     # a request past the decoder's limits is this child's failure too
        _, package = _component(request["component"])
        if request.get("inside"):
            resolve(request["inside"], package)(request.get("isolation") or {})
        reply = serve(request)
    except Exception as exc:  # noqa: BLE001 - the child's own failure is reported, never raised past its reply
        reply = {"refused": f"[G6_E_AGENTIC_CHILD_FAILED] {type(exc).__name__}: {exc}"[:2000]}
    sys.stdout.write(canonical(reply) + "\n")
    sys.stdout.flush()
    return 0


def launch(request: Mapping[str, Any], *, timeout_s: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run :func:`main` on ``request`` in a confined child: its reply and the run's isolation record. Raises
    :class:`Refused` when no backend can confine it (nothing starts), when it runs out of time, or when it fails."""
    import g6
    from g6._agentic import decode
    from g6.core import component_process, confine
    from g6.core.errors import ISOLATION_UNAVAILABLE

    python = sys.executable
    argv = [python, "-I", "-c", confine.CHILD_PRELUDE + _CHILD.format(folder=str(Path(g6.__file__).resolve().parents[1]))]
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    with confine.run_folder(prefix="g6_agentic_") as cwd:
        plan = confine.plan(confine.Confinement(read_write=(cwd,), read_only=confine.interpreter_paths(python)), python)
        record = plan.record()
        try:
            if plan.backend is None:
                raise Refused(f"[{ISOLATION_UNAVAILABLE}] the agentic kernel runs a substitute only inside an "
                              f"operating-system boundary, and none can confine it here ({plan.why}); nothing was run. "
                              "Run g6 doctor: it says what this computer needs for a boundary.", record)
            body = canonical({**dict(request), "isolation": record}).encode("utf-8")
            try:
                with component_process.window(timeout_s):
                    done = component_process.run_contained(argv, env=env, cwd=cwd, input_bytes=body,
                                                           memory_bytes=MEMORY_MB * 1024 * 1024, confined=plan)
            except component_process.ExecutionInterrupted as exc:
                raise Refused(f"[G6_E_AGENTIC_TIMEOUT] the child ran longer than {timeout_s:g}s and was killed", record) from exc
            except (OSError, ConnectionResetError) as exc:
                raise Refused(f"[G6_E_AGENTIC_CHILD_FAILED] the child could not be run: {exc}", record) from exc
        finally:
            plan.release()
    stderr = done.stderr.decode("utf-8", "replace")
    failure = confine.import_failure(stderr)
    if failure:
        raise Refused(failure, record)
    lines = [line for line in done.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    try:   # bounded: the line can carry what a substitute made
        reply = decode.loads(lines[-1], max_chars=REPLY_CHARS)
    except (IndexError, decode.Undecodable):
        reply = None
    if done.returncode != 0 or not isinstance(reply, dict):
        raise Refused(f"[G6_E_AGENTIC_CHILD_FAILED] the child exited with {done.returncode}: {stderr[-400:]}", record)
    if "refused" in reply:
        raise Refused(str(reply["refused"]), record)
    return reply, record


def signature(function: Callable[..., Any]) -> str:
    return f"{function.__name__}{inspect.signature(function)}"


__all__ = ["MEMORY_MB", "Refused", "call_digest", "canonical", "launch", "main", "resolve", "return_type", "serve",
           "signature"]
