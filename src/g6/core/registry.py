# SPDX-License-Identifier: Apache-2.0
"""Component discovery for the installed `g6` package.

Replaces an earlier registry that located components by walking up to a
source directory, and therefore failed inside an installed wheel.

Discovery sources, in order:
  1. sub-packages of `g6` that declare `__g6_component__ = True` in their `__init__`
     (found with pkgutil, so it works from a wheel or a source checkout);
  2. the sub-packages of each family package (`g6.<family>.<name>`, DECISIONS #37), registered under their own
     names; a family package itself is not a component, and its `_`-prefixed modules are shared family code;
  3. third-party extensions registered under the `g6.components` entry-point group.

Nothing is imported until it is asked for: listing names is cheap, `get_class`
imports exactly one component. A name already taken is kept by the first package that took it (a core package before
any family member) and the clash is recorded in `collisions()`, which `g6 doctor` reports as a failure.
"""
from __future__ import annotations

import importlib
import os
import pkgutil
from dataclasses import dataclass
from importlib import metadata
from typing import Any

import g6

ENTRY_POINT_GROUP = "g6.components"
# Sub-packages of g6 that are infrastructure, not components.
_NOT_COMPONENTS = frozenset({"core", "harness", "mcp", "cli", "bundles", "research", "operator", "triage", "integrity",
                             "skills"})
#: The extension families (DECISIONS #37). Each is a package under `g6` that declares `__g6_family__ = "<its name>"`.
FAMILIES = frozenset({"media", "ml", "agents", "knowledge", "memory", "sources", "deploy", "workspace", "code",
                      "embodied", "cogarch", "provers", "domains", "jobs", "integrations"})


@dataclass(frozen=True)
class ComponentRef:
    name: str
    module: str
    source: str  # "builtin" | "extension"
    family: str | None = None  # the family package a member sits in; None for a top-level package


class ComponentRegistry:
    def __init__(self) -> None:
        self._refs: dict[str, ComponentRef] | None = None
        self._collisions: list[tuple[str, str, str]] = []

    def _discover(self) -> dict[str, ComponentRef]:
        refs: dict[str, ComponentRef] = {}
        self._collisions = []

        def add(ref: ComponentRef) -> None:
            if ref.name in refs:
                self._collisions.append((ref.name, refs[ref.name].module, ref.module))
            else:
                refs[ref.name] = ref

        tops = [info for info in pkgutil.iter_modules(g6.__path__)
                if info.ispkg and info.name not in _NOT_COMPONENTS and not info.name.startswith("_")]
        for info in tops:
            if info.name not in FAMILIES:
                add(ComponentRef(info.name, f"g6.{info.name}", "builtin"))
        for info in tops:
            if info.name in FAMILIES:
                for member in pkgutil.iter_modules([os.path.join(p, info.name) for p in g6.__path__]):
                    if member.ispkg and not member.name.startswith("_"):
                        add(ComponentRef(member.name, f"g6.{info.name}.{member.name}", "builtin", info.name))
        for ep in metadata.entry_points(group=ENTRY_POINT_GROUP):
            # Extensions may not shadow a built-in component.
            refs.setdefault(ep.name, ComponentRef(ep.name, ep.value, "extension"))
        return refs

    @property
    def refs(self) -> dict[str, ComponentRef]:
        if self._refs is None:
            self._refs = self._discover()
        return self._refs

    def names(self) -> list[str]:
        return sorted(self.refs)

    def collisions(self) -> list[tuple[str, str, str]]:
        """Each ``(name, module that kept it, module left out)`` where two packages declare the same name."""
        self.refs
        return list(self._collisions)

    def get_module(self, name: str) -> Any:
        ref = self.refs.get(name)
        if ref is None:
            raise KeyError(f"unknown component: {name}")
        return importlib.import_module(ref.module.split(":", 1)[0])

    def get_class(self, name: str) -> Any:
        """Return the component's primary block class, or None if it declares none.

        A component names its block with `__g6_block__ = "ClassName"` in its package `__init__`.
        """
        mod = self.get_module(name)
        attr = getattr(mod, "__g6_block__", None)
        return getattr(mod, attr, None) if attr else None

    def describe(self, name: str) -> dict[str, Any]:
        """Machine-readable summary of one component (no execution)."""
        from g6.core.maturity import get_maturity

        if name not in self.refs:
            return {"name": name, "available": False}
        cls = self.get_class(name)
        mod = self.get_module(name)
        ref = self.refs[name]
        return {
            "name": name,
            "available": True,
            "source": ref.source,
            "family": ref.family or getattr(mod, "__g6_family__", None),
            "package": ref.module.split(":", 1)[0],
            "block": cls.__name__ if cls else None,
            "maturity": (get_maturity(cls) if cls else None) or getattr(mod, "__g6_maturity__", None),
            "sensitive_ops": sorted(getattr(mod, "__g6_sensitive_ops__", ())),
            "summary": (mod.__doc__ or "").strip().splitlines()[0] if mod.__doc__ else "",
        }

    def invoke(
        self, name: str, op: str, params: dict[str, Any] | None = None, *, block_sensitive: bool = False,
        llm: Any = None,
    ) -> Any:
        """Run ``op`` on component ``name`` through its block's typed input.

        Returns ``returns.result.Success(dict)`` or ``Failure(str)``. Fails closed: an unknown
        component, a component without a block, input that does not validate, or a sensitive op
        on an untrusted surface (``block_sensitive=True``) is refused — raw input is never passed
        through to ``infer`` unvalidated.

        ``llm`` is a model caller (``g6.core.llm``) for a component that declares ``__g6_model__ = "<keyword>"``, the
        keyword its block's constructor takes one by: its block is built as ``cls(<keyword>=llm)``. Every other
        component's block is built with no argument, as without ``llm``. A sensitive op is refused before any block is
        built, so a model never opens one.

        A block may define ``screen_raw(raw) -> Result | None``, called with the raw parameters (and ``op``) before
        typed validation, so a safety check can answer a call its typed input would refuse. ``None`` lets the call go
        on to typed validation exactly as without the hook; a ``Result`` ends the call, mapped as ``infer``'s result
        is. The hook never feeds ``infer``: raw input still reaches it only through typed validation. A hook that
        raises, or answers with anything but a ``Result`` or ``None``, refuses the call (``G6_E_SCREEN_RAW_FAILED``).
        """
        from returns.result import Failure, Success

        from g6.core.errors import SCREEN_RAW_FAILED
        from g6.core.result import Result

        code = f"[{name.upper()}_ERROR]"
        if name not in self.refs:
            return Failure(f"[G6_E_UNKNOWN_COMPONENT] {name}")
        mod = self.get_module(name)
        if block_sensitive and op in getattr(mod, "__g6_sensitive_ops__", ()):
            return Failure(f"[G6_E_SENSITIVE_COMPONENT_DENIED] {name}.{op} is not available on this surface")
        cls = self.get_class(name)
        if cls is None:
            return Failure(f"[G6_E_NO_BLOCK] {name} declares no block")
        keyword = getattr(mod, "__g6_model__", None)
        block = cls(**{keyword: llm}) if llm is not None and isinstance(keyword, str) and keyword else cls()
        screen = getattr(block, "screen_raw", None)
        if callable(screen):
            try:
                early = screen({**(params or {}), "op": op})
            except Exception as exc:  # noqa: BLE001 - a raw screen that breaks refuses the call; it never falls through
                return Failure(f"[{SCREEN_RAW_FAILED}] {name}.{op}: its raw screen raised {type(exc).__name__}; "
                               "the call is refused")
            if early is not None:
                if not isinstance(early, Result):
                    return Failure(f"[{SCREEN_RAW_FAILED}] {name}.{op}: its raw screen answered "
                                   f"{type(early).__name__}, not a Result; the call is refused")
                return Failure(str(early.error)) if early.is_fail() else Success(early.value)
        data, err = build_typed_input(block, {**(params or {}), "op": op}, code=code, op=op)
        if err is not None:
            # the stable code first, so every surface reads the same G6_E_* (CAG-010); the component's own code follows
            return Failure(f"[G6_E_INPUT_VALIDATION] {err}")
        result = block.infer(data)
        if result.is_fail():
            return Failure(str(result.error))
        return Success(result.value)


def build_typed_input(block: Any, raw: dict[str, Any], *, code: str, op: str) -> tuple[Any, str | None]:
    """Validate ``raw`` against the type annotated on ``block.infer``'s first parameter.

    Returns ``(data, None)`` or ``(None, "<code> <op>: <field>: <message>")``.
    A block whose ``infer`` takes an untyped argument receives the dict unchanged.
    """
    import inspect
    import typing

    from pydantic import BaseModel, ValidationError

    params = [p for p in inspect.signature(block.infer).parameters.values() if p.name != "self"]
    if not params:
        return None, f"{code} {op}: block.infer takes no input"
    input_type = typing.get_type_hints(block.infer).get(params[0].name)
    if not (isinstance(input_type, type) and issubclass(input_type, BaseModel)):
        return raw, None
    fields = input_type.model_fields
    if "op" not in fields and "operation" in fields:
        raw = {**{k: v for k, v in raw.items() if k != "op"}, "operation": op}
    try:
        return input_type.model_validate(raw), None
    except ValidationError as ve:
        first = ve.errors()[0]
        field = ".".join(str(x) for x in first.get("loc") or ()) or "(input)"
        detail = f"{field} is required" if first.get("type") == "missing" else f"{field}: {first.get('msg', '')}"
        return None, f"{code} {op}: {detail}"


_REGISTRY: ComponentRegistry | None = None


def get_registry() -> ComponentRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ComponentRegistry()
    return _REGISTRY
