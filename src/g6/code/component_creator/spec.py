# SPDX-License-Identifier: Apache-2.0
"""The spec a scaffold is rendered from, and the two ways to draft one.

:class:`ComponentSpec` is the schema (pydantic, extra fields refused): a name, the family it joins, a one-line
summary, its ops (each with what it does, its kind, whether it is sensitive, the fields it reads and, optionally, the
library function it wraps), its input fields, and the failure modes it guards against and can cause.
:func:`spec_problems` is what the schema cannot say: the failure-mode codes checked against the registry by
``g6.core.failure_modes.problems_in``, the rule every component is held to (FW-03).

:func:`from_module` reads a Python module or package as text with :mod:`ast` (it is never imported, so none of its
code runs): its public functions (``__all__`` when it is a literal, otherwise the names without a leading underscore),
each one's parameters and annotations, its public classes and their methods, and maps each function to an op by its
name's prefix (``get_`` reads, ``delete_`` deletes, ...), the archive integrator's table. The mapping is a guess from a
name: which ops are sensitive is the author's to decide.

:func:`from_text` asks the injected model caller to draft a spec from a description, and checks the reply against
the schema; what the model drafted is a claim about what the component should be.
"""
from __future__ import annotations

import ast
import keyword
import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from g6.code._types import CodeRefused
from g6.core.failure_modes import problems_in
from g6.core.llm import CONFIGURE_MODEL, LLMError, extract_json
from g6.core.registry import FAMILIES

NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
CALLS = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
FieldType = Literal["str", "int", "float", "bool", "list", "dict", "any"]
Kind = Literal["read", "query", "write", "create", "delete", "action"]
#: Names a scaffold's input model keeps for itself.
RESERVED_FIELDS = frozenset({"op", "request_id", "run_id", "model_config", "model_fields"})
RESERVED_OPS = frozenset({"ops", "help"})
SPEC_INVALID = "G6_E_SCAFFOLD_SPEC_INVALID"
NO_MODEL = "G6_E_SCAFFOLD_NO_MODEL"
MODEL_FAILED = "G6_E_SCAFFOLD_MODEL_FAILED"
SOURCE_INVALID = "G6_E_SCAFFOLD_SOURCE_INVALID"
#: Function-name prefixes to op kinds (the archive integrator's table, ``map_ops.py`` ``_OP_PATTERNS``).
PREFIXES: tuple[tuple[Kind, tuple[str, ...]], ...] = (
    ("read", ("get_", "fetch_", "load_", "read_")),
    ("write", ("set_", "update_", "save_", "write_", "put_")),
    ("create", ("create_", "add_", "insert_", "new_", "make_")),
    ("delete", ("delete_", "remove_", "drop_", "clear_")),
    ("query", ("list_", "search_", "find_", "filter_", "query_")),
)
#: The kinds that may change something, so a mapped op of that kind starts out sensitive.
CHANGES = frozenset({"write", "create", "delete", "action"})
_SIMPLE = {"str": "str", "int": "int", "float": "float", "bool": "bool", "list": "list", "dict": "dict",
           "List": "list", "Dict": "dict", "tuple": "list", "Tuple": "list", "Sequence": "list", "Mapping": "dict"}


def _identifier(value: str, label: str) -> str:
    value = value.strip()
    if not NAME.fullmatch(value) or keyword.iskeyword(value):
        raise ValueError(f"{label} {value!r} must be a lower-case Python identifier of at most 40 characters")
    return value


def _description(value: str) -> str:
    if any(unicodedata.category(ch).startswith("C") or ch in "\u2028\u2029" for ch in value):
        raise ValueError("descriptions must be one line without control characters")
    return value


class FieldSpec(BaseModel):
    """One input field of the scaffold's typed input."""

    model_config = ConfigDict(extra="forbid")

    name: str
    type: FieldType = "any"
    required: bool = False
    does: str = Field("", max_length=200)

    @field_validator("does")
    @classmethod
    def _does(cls, value: str) -> str:
        return _description(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        name = _identifier(value, "field")
        if name in RESERVED_FIELDS:
            raise ValueError(f"field {name!r} is kept by every block's input")
        return name


class OpSpec(BaseModel):
    """One op: what it does, its kind, whether it is sensitive, the fields it reads, and the library function it
    wraps (``calls``, dotted) or none."""

    model_config = ConfigDict(extra="forbid")

    name: str
    does: str = Field(min_length=1, max_length=300)
    kind: Kind = "action"
    sensitive: bool = False
    params: tuple[str, ...] = Field((), max_length=20)
    calls: str = Field("", max_length=200)

    @field_validator("does")
    @classmethod
    def _does(cls, value: str) -> str:
        return _description(value)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        name = _identifier(value, "op")
        if name in RESERVED_OPS:
            raise ValueError(f"op {name!r} is kept by every block")
        return name

    @field_validator("calls")
    @classmethod
    def _calls(cls, value: str) -> str:
        if value and not CALLS.fullmatch(value):
            raise ValueError("calls is a dotted name: module.function")
        return value


class ComponentSpec(BaseModel):
    """What a scaffold is rendered from."""

    model_config = ConfigDict(extra="forbid")

    name: str
    family: str = ""
    summary: str = Field(min_length=1, max_length=200)
    ops: tuple[OpSpec, ...] = Field(min_length=1, max_length=30)
    fields: tuple[FieldSpec, ...] = Field((), max_length=60)
    mitigates: tuple[str, ...] = Field((), max_length=10)
    introduces: tuple[str, ...] = Field((), max_length=10)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _identifier(value, "name")

    @field_validator("family")
    @classmethod
    def _family(cls, value: str) -> str:
        if value and value not in FAMILIES:
            raise ValueError(f"family must be one of {', '.join(sorted(FAMILIES))}")
        return value

    @field_validator("summary")
    @classmethod
    def _summary(cls, value: str) -> str:
        _description(value)
        if '"""' in value or "\\" in value:
            raise ValueError("summary must not contain triple quotes or backslashes")
        return value.strip()

    @model_validator(mode="after")
    def _consistent(self) -> ComponentSpec:
        ops = [op.name for op in self.ops]
        if len(set(ops)) != len(ops):
            raise ValueError("op names repeat")
        names = [f.name for f in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("field names repeat")
        unknown = sorted({p for op in self.ops for p in op.params} - set(names))
        if unknown:
            raise ValueError(f"ops read fields the spec does not define: {', '.join(unknown)}")
        return self

    @property
    def package(self) -> str:
        return f"g6.{self.family}.{self.name}" if self.family else f"g6.{self.name}"


def spec_problems(spec: ComponentSpec) -> list[str]:
    """What stops ``spec`` becoming a component the library takes: no family, and the failure-mode declarations as
    the registry reads them (``problems_in``: none guarded against, an unknown code, an alias, a code both guarded
    against and caused)."""
    problems = [] if spec.family else [f"name a family the component joins (one of {', '.join(sorted(FAMILIES))})"]
    found = {"__g6_mitigates__": spec.mitigates, "__g6_introduces__": spec.introduces}
    return problems + problems_in(found, spec.package)


def validation_errors(exc: ValidationError) -> list[str]:
    """A pydantic error list in words, at most ten."""
    return [f"{'.'.join(str(x) for x in e.get('loc') or ()) or '(spec)'}: {e.get('msg', '')}" for e in exc.errors()[:10]]


# --- spec_from_module -------------------------------------------------------------------------------------------------

def kind_of(name: str) -> Kind:
    """The op kind a function's name suggests (``action`` when no prefix matches)."""
    for kind, prefixes in PREFIXES:
        if name.startswith(prefixes):
            return kind
    return "action"


def _type_of(annotation: ast.expr | None) -> FieldType:
    if annotation is None:
        return "any"
    node = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""
    return _SIMPLE.get(name, "any")  # type: ignore[return-value]


def _public(tree: ast.Module) -> tuple[list[str] | None, list[str]]:
    """``__all__`` when it is a literal list or tuple of strings (else None), and the names it lists that the module
    does not define itself (re-exported, not read)."""
    listed: list[str] | None = None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__all__"):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
                raise CodeRefused(SOURCE_INVALID, f"__all__ cannot be read as a literal: {type(exc).__name__}") from None
            if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
                listed = list(value)
    if listed is None:
        return None, []
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    return listed, [n for n in listed if n not in defined]


def read_module(source: str, module: str) -> dict[str, Any]:
    """The public interface of one module's source, read with :mod:`ast` (never imported)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise CodeRefused(SOURCE_INVALID, f"{module} does not parse: {exc}") from None
    listed, reexported = _public(tree)

    def public(name: str) -> bool:
        return name in listed if listed is not None else not name.startswith("_")

    functions, classes = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and public(node.name):
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
            params = [{"name": a.arg, "type": _type_of(a.annotation), "required": d is None,
                       "positional_only": a in args.posonlyargs,
                       "annotation": ast.unparse(a.annotation) if a.annotation else ""}
                      for a, d in zip(positional, defaults)]
            params += [{"name": a.arg, "type": _type_of(a.annotation), "required": d is None,
                        "positional_only": False, "annotation": ast.unparse(a.annotation) if a.annotation else ""}
                       for a, d in zip(args.kwonlyargs, args.kw_defaults)]
            functions.append({"name": node.name, "line": node.lineno, "async": isinstance(node, ast.AsyncFunctionDef),
                              "params": params, "kind": kind_of(node.name),
                              "variadic": bool(args.vararg or args.kwarg),
                              "returns": ast.unparse(node.returns) if node.returns else ""})
        elif isinstance(node, ast.ClassDef) and public(node.name):
            methods = [n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
            classes.append({"name": node.name, "line": node.lineno, "methods": methods})
    return {"module": module, "functions": functions, "classes": classes, "all": listed, "reexported": reexported}


def _leaf(name: str) -> str:
    text = re.sub(r"[^a-z0-9_]", "_", name.rsplit(".", 1)[-1].lower()).strip("_") or "wrapped"
    text = text if text[0].isalpha() else f"m_{text}"
    return text[:40] if not keyword.iskeyword(text) else f"{text}_lib"


def from_module(modules: Mapping[str, str], package: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """A spec (as a dict) mapping each public function of ``modules`` (import name to source) to one op that calls it,
    the interfaces read, and what was left out and why. Async and variadic functions are left out: a scaffold calls a
    function with the fields it declares, once."""
    read = [read_module(source, name) for name, source in sorted(modules.items())]
    ops: list[dict[str, Any]] = []
    fields: dict[str, dict[str, Any]] = {}
    left_out: list[str] = []
    taken: set[str] = set()
    for interface in read:
        for fn in interface["functions"]:
            where = f"{interface['module']}.{fn['name']}"
            if fn["async"] or fn["variadic"] or any(p["positional_only"] for p in fn["params"]):
                why = ("async" if fn["async"] else "takes *args or **kwargs" if fn["variadic"]
                       else "has positional-only parameters, which a scaffold's keyword call cannot pass")
                left_out.append(f"{where}: {why}; wrap it by hand")
                continue
            op_name = _leaf(fn["name"]) if _leaf(fn["name"]) not in taken | RESERVED_OPS else \
                _leaf(f"{interface['module'].rsplit('.', 1)[-1]}_{fn['name']}")
            if op_name in taken | RESERVED_OPS:
                left_out.append(f"{where}: its op name {op_name!r} is taken")
                continue
            params = []
            for p in fn["params"]:
                if p["name"] in RESERVED_FIELDS or not NAME.fullmatch(p["name"]) or keyword.iskeyword(p["name"]):
                    left_out.append(f"{where}: parameter {p['name']!r} cannot be an input field; the op leaves it out")
                    continue
                known = fields.setdefault(p["name"], {"name": p["name"], "type": p["type"], "required": False,
                                                      "does": f"{p['name']} for {fn['name']}"})
                if known["type"] != p["type"]:
                    known["type"] = "any"
                params.append(p["name"])
            taken.add(op_name)
            ops.append({"name": op_name, "does": f"calls {where}; what it returns is not checked",
                        "kind": fn["kind"], "sensitive": fn["kind"] in CHANGES, "params": params, "calls": where})
        for cls in interface["classes"]:
            left_out.append(f"{interface['module']}.{cls['name']}: a class (methods {', '.join(cls['methods']) or 'none'}"
                            "); a method needs an instance, so it is not mapped")
        for name in interface["reexported"]:
            left_out.append(f"{interface['module']}.{name}: listed in __all__ but defined elsewhere; not read")
    spec = {"name": _leaf(package), "family": "", "summary": f"Wraps the {package} library's public functions.",
            "ops": ops, "fields": list(fields.values()), "mitigates": [], "introduces": []}
    return spec, read, left_out


# --- spec_from_text ---------------------------------------------------------------------------------------------------

SYSTEM = (
    "You draft the spec of a G6 component from a description. Reply with one JSON object and nothing else, with "
    "exactly these keys: name (a lower-case Python identifier), family (one of: " + ", ".join(sorted(FAMILIES)) +
    "), summary (one line), ops (a list of objects with name, does, kind (read, query, write, create, delete or "
    "action), sensitive (true when the op reaches the network, spends money, changes the machine or acts outside "
    "it) and params (field names)), fields (a list of objects with name, type (str, int, float, bool, list, dict or "
    "any), required and does), mitigates (failure-mode codes the component guards against) and introduces "
    "(failure-mode codes its own use can cause). Do not invent a library function to call.")


def _who(caller: Any) -> str:
    model = getattr(caller, "model", None)
    return f"{type(caller).__name__}({model})" if model else type(caller).__name__


def from_text(text: str, caller: Any, *, max_tokens: int = 2048) -> tuple[ComponentSpec, dict[str, Any]]:
    """The injected caller's draft of a spec for ``text``, checked against :class:`ComponentSpec`; raises
    :class:`CodeRefused` when there is no caller, the call fails, or the reply is not a spec."""
    if caller is None:
        raise CodeRefused(NO_MODEL, "no model was given to the block (over MCP: no model is configured), so no spec "
                                    f"was drafted. To give G6 a model, {CONFIGURE_MODEL}")
    try:
        said = str(caller.complete(SYSTEM, text, max_tokens=max_tokens))
    except LLMError as exc:
        raise CodeRefused(MODEL_FAILED, f"{_who(caller)} did not answer: {str(exc)[:300]}") from None
    claim = {"kind": "claim", "by": _who(caller), "about": "the spec of the component the description asks for",
             "reply_chars": len(said)}
    try:
        value = extract_json(said)
    except LLMError:
        raise CodeRefused(SPEC_INVALID, f"{_who(caller)}'s reply holds no JSON object", [claim]) from None
    if not isinstance(value, dict):
        raise CodeRefused(SPEC_INVALID, f"{_who(caller)}'s reply is JSON but not an object", [claim])
    for op in value.get("ops") or ():
        if isinstance(op, dict) and op.get("calls"):
            raise CodeRefused(SPEC_INVALID, f"{_who(caller)} named a library function to call ({str(op['calls'])[:80]})"
                                            "; a drafted spec never does (use spec_from_module on the library)",
                              [claim])
    try:
        spec = ComponentSpec.model_validate(value)
    except ValidationError as exc:
        raise CodeRefused(SPEC_INVALID, f"{_who(caller)}'s draft is not a spec: {'; '.join(validation_errors(exc))}",
                          [claim]) from None
    return spec, claim


__all__ = ["CHANGES", "MODEL_FAILED", "NO_MODEL", "PREFIXES", "SOURCE_INVALID", "SPEC_INVALID", "SYSTEM",
           "ComponentSpec", "FieldSpec", "OpSpec", "from_module", "from_text", "kind_of", "read_module",
           "spec_problems", "validation_errors"]
