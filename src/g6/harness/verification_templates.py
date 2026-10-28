# SPDX-License-Identifier: Apache-2.0
"""Verification search, part 3: higher-order verification templates for G6's patch review.

A template is trusted, reviewed code (``TemplateDefinition``): an id with its version (``example.v1``), a family, a
pydantic model for its bindings, the assumptions it rests on, an applicability check against the tree under review,
and either a fixed text that compiles to a test or, for the structural template, a static check. ``TEMPLATES`` holds
the five of this version:

- ``example.v1`` (requirement satisfaction): a stated input gives a stated result (``returns``, compared as JSON writes
  both), an error (``raises``: an exception type) or an effect (``effect``: an argument's value after the call);
- ``relation.v1`` (requirement satisfaction): a metamorphic relation over the caller's inputs (at most 32), each the
  one argument: ``round_trip`` (``inverse(function(x)) == x``), ``idempotence`` (``function(function(x)) ==
  function(x)``), ``invariance`` under a named ``transformation`` (``function(transformation(x)) == function(x)``) or
  ``monotonicity`` (inputs in ascending order give results in the ``order`` stated);
- ``access_rule.v1`` (requirement satisfaction): who may perform an operation on a resource, single-step: allowed and
  refused cases, at least one of each, the refusal being an exception type or a falsy return, as the bindings declare;
- ``differential.v1`` (regression preservation): outside the change's declared scope, the base commit and the patch
  agree on calls the caller states, on outputs or on exceptions only (``mode``). It compiles to a probe (verification
  search's observation lines), run on both trees. The rule: a difference is a counterexample when the instance's
  intent is ``preserve`` (the caller declared that behaviour must not change), so at most insufficient evidence; and
  ``behaviour_changed``, a question only, when it is ``change``;
- ``forbidden_dependency.v1`` (safety and structural invariants): the patch adds no import that breaks a declared
  layer rule, read statically with ``design_governance``'s ``layer_violations`` on the package before and after (no
  code runs). The contract is the repository's own, an import-linter configuration (``.importlinter``, ``setup.cfg``
  or ``pyproject.toml``) as the base commit declares it, so the patch cannot loosen it (provenance
  ``repository_stated``), or one the instance states (``stated``); against either, a new forbidden import is a failed
  check, so ``defect_detected``, and a check that finds none counts for its requirement as a stated check does. A
  contract a model proposed (``model_hypothesis``, which only G6's own code sets) gives at most insufficient evidence.
  A part of a contract that matches no module of the package, and a module outside the package, are reported as out
  of scope, never as violations. It sees direct imports only (those inside functions and under ``TYPE_CHECKING``
  included), not dynamic ones.

An instance is data, never code: ``{id, template, requirement_id, intent, bindings}`` (``template_instances``). A name
in the bindings is a dotted name of the letters A to Z (either case), digits and ``_``, a module path and then a
top-level attribute (``package.module.function``); an exception is a built-in exception's name or such a dotted name;
there is no keyword and no double-underscore name in either. A literal is JSON
data (no tuple, set, bytes, number that is not finite, or key that is not text), at most ``MAX_DEPTH`` levels deep and
``MAX_BINDINGS_CHARS`` characters as JSON. Anything else is refused, and so is the whole review, before anything runs.
``prepare`` then checks each instance against the tree (``Tree``), reading files and never importing them in G6's
process: each name must resolve to a module file in the top folder or in ``src`` and a top-level definition in it (a
star import is not followed), on the working tree for an instance whose intent is ``change``, and at the base commit
for one that keeps behaviour (``preserve``, and every ``differential.v1``); a function's parameters must take each
call's arguments, read from its definition. One that does not apply is refused for that instance and listed in
``assessment.completion.activities`` with the reason; it never errors the review.

The compiler writes each check from the template's fixed text alone: the text holds one slot, ``BINDINGS = ...``,
where G6 writes the resolved bindings as one literal (Python's ``ascii``, so the text is ASCII), and nothing the caller
stated is written anywhere else; the check imports the code it names by those names at run time. The template's id,
version and the digest of its fixed text enter every digest that cites an instance (``instance_digest``), and the
compiled text names its template. A compiled check's provenance is ``proposed``: G6 generated it from the caller's
bindings, so it never passes a requirement, and a failure gives at most insufficient evidence. It runs through
verification search's executor exactly as a proposed check does.
"""
from __future__ import annotations

import ast
import builtins
import configparser
import hashlib
import inspect
import itertools
import json
import keyword
import math
import re
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from g6.harness import verification_search as vs
from g6.workforce import repo_tests

REQUIREMENT_SATISFACTION = "requirement_satisfaction"
REGRESSION_PRESERVATION = "regression_preservation"
SAFETY_STRUCTURAL = "safety_structural"
#: At most this many cases, inputs or calls per instance (plan rule 7: at most 32 inputs per probe).
MAX_INPUTS = vs.MAX_OBSERVATIONS
#: An instance's bindings: at most this many characters as JSON, and this many levels of nesting.
MAX_BINDINGS_CHARS = 32 * 1024
MAX_DEPTH = 16
MAX_NAME = 200
#: A differential call: at most this many characters as JSON, so its observation line fits.
MAX_CALL_CHARS = 300
#: A module G6 reads to resolve a name: at most this many bytes.
MAX_MODULE_BYTES = 1_000_000
#: The structural template's package: at most this many files and characters on each side.
MAX_PACKAGE_FILES = 2000
MAX_PACKAGE_CHARS = 20_000_000
#: The one place in a template's fixed text where the compiler writes the bindings.
SLOT = "@G6_BINDINGS_LITERAL@"
PATCH, BASE = "working tree", "base commit"
WHERE = {PATCH: "on the working tree", BASE: "at the base commit"}
TEMPLATES_OFF = "the search configuration runs free-form checks only (search.templates is false)"
#: Who proposed a compiled check (VS-D2), recorded and never authenticated.
COMPILED_BY = ("G6, from the caller's bindings: the caller is a calling agent whose bindings are never assumed "
               "independent of the patch's author")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
_PART = r"[A-Za-z_][A-Za-z0-9_]*"
_DOTTED = re.compile(rf"{_PART}(?:\.{_PART})*")
#: The built-in exceptions an exception binding may name alone.
BUILTIN_ERRORS = frozenset(name for name, value in vars(builtins).items()
                           if isinstance(value, type) and issubclass(value, BaseException))
#: Folders G6's copies leave out: a module in one is not in the copy that runs.
_LEFT_OUT = frozenset({"venv"})
#: G6's clock for the review's deadline (a test replaces it).
_clock = time.monotonic
OUT_OF_TIME = "the review's time budget was spent before G6 had read and parsed the package's files"


class Inapplicable(Exception):
    """An instance the tree under review gives the template nothing to apply to: refused for that instance only."""


# --- bindings: names and literals --------------------------------------------------------------------------------------

def _dotted(value: str, *, attribute: bool) -> str:
    if len(value) > MAX_NAME or not _DOTTED.fullmatch(value):
        raise ValueError("must be a dotted name of the letters A to Z (either case), digits and _ only, such as package.module.function")
    parts = value.split(".")
    if attribute and len(parts) < 2:
        raise ValueError("must name module.attribute: the module's dotted path, then a top-level name in it")
    if any(keyword.iskeyword(part) or (part.startswith("__") and part.endswith("__")) for part in parts):
        raise ValueError("must not name a keyword or a special double-underscore name")
    return value


def _error_name(value: str) -> str:
    if "." in value:
        return _dotted(value, attribute=True)
    if value not in BUILTIN_ERRORS:
        raise ValueError("must be a built-in exception's name, such as ValueError, or module.attribute")
    return value


Name = Annotated[str, AfterValidator(lambda value: _dotted(value, attribute=True))]
Module = Annotated[str, AfterValidator(lambda value: _dotted(value, attribute=False))]
ErrorName = Annotated[str, AfterValidator(_error_name)]


def literal_problem(value) -> str:
    """Why ``value`` is not JSON data a binding may hold ("" when it is): only None, a bool, an int, a finite float,
    text, a list, or an object whose keys are text, at most ``MAX_DEPTH`` levels deep and ``MAX_BINDINGS_CHARS``
    characters as JSON."""
    pending, seen = [(value, 1)], 0
    while pending:
        item, depth = pending.pop()
        seen += 1
        if depth > MAX_DEPTH or seen > 100_000:
            return f"bindings must be JSON data nested at most {MAX_DEPTH} levels deep"
        kind = type(item)
        if item is None or kind in (bool, int, str):
            continue
        if kind is float:
            if not math.isfinite(item):
                return "bindings must be JSON data, and a number that is not finite (NaN, infinity) is not"
            continue
        if kind is list:
            pending.extend((child, depth + 1) for child in item)
        elif kind is dict:
            if any(type(key) is not str for key in item):
                return "bindings must be JSON data: an object's keys are text"
            pending.extend((child, depth + 1) for child in item.values())
        else:
            return f"bindings must be JSON data: a {kind.__name__[:40]} is not"
    try:
        size = len(json.dumps(value, ensure_ascii=True, allow_nan=False))
    except (ValueError, RecursionError):
        return "bindings must be JSON data that JSON can write"
    if size > MAX_BINDINGS_CHARS:
        return f"bindings hold {size} characters as JSON; an instance's hold at most {MAX_BINDINGS_CHARS} characters"
    return ""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Call(_Strict):
    """A call's arguments: positional ``args`` and keyword ``kwargs``, each JSON data."""
    args: list[Any] = Field(default_factory=list, max_length=32)
    kwargs: dict[str, Any] = Field(default_factory=dict, max_length=32)

    @field_validator("kwargs")
    @classmethod
    def _keywords(cls, value: dict) -> dict:
        if any(not key.isascii() or not key.isidentifier() or keyword.iskeyword(key) for key in value):
            raise ValueError("each key of kwargs must be a parameter's name: the letters A to Z (either case), digits and _")
        return value


class Effect(_Strict):
    """An argument's value after the call: ``argument`` (the index of one of ``args``) ``equals`` a literal."""
    argument: int = Field(ge=0, le=31)
    equals: Any


class ExampleCase(Call):
    """One stated input, and exactly one of what it gives: ``returns``, ``raises`` or ``effect``."""
    returns: Any = None
    raises: ErrorName | None = None
    effect: Effect | None = None

    @model_validator(mode="after")
    def _one(self):
        stated = [name for name in ("returns", "raises", "effect") if name in self.model_fields_set]
        if len(stated) != 1 or (stated[0] != "returns" and getattr(self, stated[0]) is None):
            raise ValueError("a case states exactly one of returns (the result), raises (an exception type) or "
                             "effect (an argument's value after the call)")
        if self.effect is not None and self.effect.argument >= len(self.args):
            raise ValueError("effect.argument must be the index of one of the case's args")
        return self


class ExampleBindings(_Strict):
    function: Name
    cases: list[ExampleCase] = Field(min_length=1, max_length=MAX_INPUTS)


class RelationBindings(_Strict):
    relation: Literal["round_trip", "idempotence", "invariance", "monotonicity"]
    function: Name
    inverse: Name | None = None
    transformation: Name | None = None
    order: Literal["increasing", "decreasing"] | None = None
    strict: bool | None = None
    inputs: list[Any] = Field(min_length=1, max_length=MAX_INPUTS)

    @model_validator(mode="after")
    def _relation(self):
        needs = {"round_trip": "inverse", "invariance": "transformation"}.get(self.relation)
        for name, used_by in (("inverse", "round_trip"), ("transformation", "invariance")):
            given = getattr(self, name) is not None
            if name == needs and not given:
                raise ValueError(f"{self.relation} needs {name}")
            if name != needs and given:
                raise ValueError(f"{name} is used only by {used_by}")
        if self.relation != "monotonicity":
            if self.order is not None or self.strict is not None:
                raise ValueError("order and strict are used only by monotonicity")
            return self
        try:
            ascending = len(self.inputs) >= 2 and all(a < b for a, b in itertools.pairwise(self.inputs))
        except TypeError:
            ascending = False
        if not ascending:
            raise ValueError("monotonicity's inputs must be at least two, in ascending order, and comparable with "
                             "one another")
        return self


class AccessCase(Call):
    """One request: ``who`` asks for the operation on ``resource`` (labels for the report), with these arguments, and
    whether it must be ``allowed`` or ``refused``."""
    who: str = Field(min_length=1, max_length=100)
    resource: str = Field(min_length=1, max_length=100)
    expected: Literal["allowed", "refused"]


class AccessBindings(_Strict):
    operation: Name
    refusal: Literal["exception", "falsy_return"]
    exception: ErrorName | None = None
    cases: list[AccessCase] = Field(min_length=1, max_length=MAX_INPUTS)

    @model_validator(mode="after")
    def _cases(self):
        if (self.refusal == "exception") != (self.exception is not None):
            raise ValueError("refusal exception names the exception type the operation raises to refuse "
                             "(exception); refusal falsy_return names none")
        if {case.expected for case in self.cases} != {"allowed", "refused"}:
            raise ValueError("an access rule needs at least one allowed case and one refused case: a rule checked "
                             "on one side only passes a handler that allows, or refuses, everyone")
        return self


class DifferentialBindings(_Strict):
    function: Name
    mode: Literal["outputs", "exceptions"]
    calls: list[Call] = Field(min_length=1, max_length=MAX_INPUTS)

    @model_validator(mode="after")
    def _short(self):
        if any(len(json.dumps(call.model_dump(), sort_keys=True)) > MAX_CALL_CHARS for call in self.calls):
            raise ValueError(f"each call holds at most {MAX_CALL_CHARS} characters as JSON")
        return self


class Forbid(_Strict):
    source: Module
    forbidden: Module


class ContractBindings(_Strict):
    """A contract the instance states: ``layers``, highest first, and ``forbidden`` pairs (each a dotted prefix)."""
    layers: list[Module] = Field(default_factory=list, max_length=20)
    forbidden: list[Forbid] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _some(self):
        if not self.layers and not self.forbidden:
            raise ValueError("a contract states layers (highest first) or forbidden pairs")
        return self


class ForbiddenBindings(_Strict):
    package: Module | None = None
    contract: ContractBindings | Literal["repository"]

    @model_validator(mode="after")
    def _package(self):
        if self.contract == "repository" and self.package is not None:
            raise ValueError("with the repository's own contract, the package is the one its configuration names")
        if self.contract != "repository" and self.package is None:
            raise ValueError("a contract the instance states needs package: the package whose imports it rules")
        return self


class TemplateInstance(BaseModel):
    """A template instance: ``{id, template, requirement_id, intent, bindings}``, and where the bindings came from
    (``provenance``: a request's are ``stated``; only G6's own code sets ``model_hypothesis``)."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
    template: str = Field(min_length=1, max_length=100)
    requirement_id: str = Field(min_length=1, max_length=200)
    intent: Literal["preserve", "change"]
    bindings: dict[str, Any]
    provenance: Literal["stated", "model_hypothesis"] = "stated"


# --- the tree under review, read from files only ------------------------------------------------------------------------

@dataclass
class Tree:
    """The tree under review as G6 reads it, never importing it: the files git lists on the working tree (tracked, and
    untracked but not ignored: the files G6's copies hold) and at the base commit."""
    root: Path
    change: repo_tests.Change
    known: frozenset[str]
    at_base: frozenset[str]
    _texts: dict = field(default_factory=dict)
    #: The review's deadline (``_clock``'s time), past which the structural check reads and parses nothing more.
    deadline: float | None = None

    @classmethod
    def read(cls, root: Path, change: repo_tests.Change, deadline: float | None = None) -> Tree:
        known = repo_tests._paths(repo_tests._git(root, "ls-files", "-co", "--exclude-standard", "-z"))
        at_base = repo_tests._paths(repo_tests._git(root, "ls-tree", "-r", "-z", "--name-only", change.base_commit))
        return cls(root, change, frozenset(known), frozenset(at_base), deadline=deadline)

    def in_time(self) -> None:
        """An ``Inapplicable`` once the review's deadline has passed."""
        if self.deadline is not None and _clock() >= self.deadline:
            raise Inapplicable(OUT_OF_TIME)

    def has(self, rel: str, side: str) -> bool:
        return rel in (self.at_base if side == BASE else self.known)

    def text(self, rel: str, side: str) -> str:
        """``rel``'s text on ``side`` (a ``ValueError`` when it cannot be read): from the working tree when it is
        there and unchanged since the base commit, else from git at the base commit."""
        key = (rel, side)
        if key not in self._texts:
            if side == PATCH or (rel in self.known and rel not in self.change.files):
                parts = Path(rel).parts
                if any(repo_tests._linked(self.root.joinpath(*parts[:end])) for end in range(1, len(parts) + 1)):
                    raise ValueError(f"{rel} is a link")
                path = self.root / rel
                if not path.is_file() or path.stat().st_size > MAX_MODULE_BYTES:
                    raise ValueError(f"{rel} is not a file of at most {MAX_MODULE_BYTES} bytes")
                text = path.read_text(encoding="utf-8", errors="replace")
            else:
                text = repo_tests.read_at(self.root, self.change.base_commit, rel)
                if text is None or len(text) > MAX_MODULE_BYTES:
                    raise ValueError(f"{rel} is not a file of at most {MAX_MODULE_BYTES} bytes at the base commit")
            self._texts[key] = text
        return self._texts[key]


@dataclass(frozen=True)
class Target:
    """A name a binding resolved to: a built-in (``kind`` "builtin") or a top-level name of a module file."""
    module: str
    attribute: str
    file: str = ""
    node: ast.AST | None = None
    kind: str = "module"

    def spec(self) -> list[str]:
        """The name as the compiled check reads it from its literal: ``[kind, module, attribute]``."""
        return [self.kind, self.module, self.attribute]


def _module_file(tree: Tree, parts: list[str], side: str) -> str:
    """The file of the module ``parts`` names on ``side``, as the runs' import path finds it: the top folder, then
    ``src``; a package's ``__init__.py`` before a module of the same name. "" when none."""
    if any(part in _LEFT_OUT for part in parts):
        return ""
    for folder in ("", "src/"):
        stem = folder + "/".join(parts)
        for rel in (stem + "/__init__.py", stem + ".py"):
            if tree.has(rel, side):
                return rel
    return ""


def _bound_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for item in target.elts for name in _bound_names(item)]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return []


def _top_level(module: ast.Module, name: str) -> tuple[ast.AST | None, bool]:
    """The statement that last binds ``name`` at the module's top level (inside ``if``, ``try``, ``with`` and loops
    too, never inside a function or class), and whether the module has a star import."""
    found: ast.AST | None = None
    star = False

    def visit(body: list) -> None:
        nonlocal found, star
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == name:
                    found = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not (isinstance(node, ast.AnnAssign) and node.value is None) and any(
                        name in _bound_names(target) for target in targets):
                    found = node
            elif isinstance(node, ast.Import):
                if any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names):
                    found = node
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        star = True
                    elif (alias.asname or alias.name) == name:
                        found = node
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try, ast.TryStar)):
                for part in ("body", "orelse", "finalbody"):
                    visit(getattr(node, part, None) or [])
                for handler in getattr(node, "handlers", None) or []:
                    visit(handler.body)
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    visit(case.body)

    visit(module.body)
    return found, star


def resolve(tree: Tree, dotted: str, side: str) -> Target:
    """The top-level name ``dotted`` names (``module.attribute``) on ``side``, read from the module's file; an
    ``Inapplicable`` that says why when there is none."""
    parts = dotted.split(".")
    module, attribute = ".".join(parts[:-1]), parts[-1]
    rel = _module_file(tree, parts[:-1], side)
    if not rel:
        raise Inapplicable(f"{dotted}: no module {module} {WHERE[side]} (G6 looks for it in the top folder and in "
                           "src, as the runs' import path has them)")
    try:
        found, star = _top_level(ast.parse(tree.text(rel, side)), attribute)
    except (SyntaxError, ValueError, RecursionError, OSError, repo_tests.RepoRefused) as exc:
        raise Inapplicable(f"{dotted}: {rel} could not be read or parsed {WHERE[side]} ({type(exc).__name__})") from exc
    if found is None:
        raise Inapplicable(f"{dotted}: no top-level name {attribute} in {rel} {WHERE[side]}"
                           + ("; it could come only from a star import, which G6 does not follow" if star else ""))
    return Target(module, attribute, rel, found)


def _error_target(tree: Tree, name: str, side: str) -> Target:
    if "." not in name:
        return Target("", name, kind="builtin")
    target = resolve(tree, name, side)
    if isinstance(target.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise Inapplicable(f"{name} is a function {WHERE[side]}, not an exception type")
    return target


_HAS_DEFAULT = object()


def _parameters(func: ast.FunctionDef, *, skip_first: bool) -> inspect.Signature:
    a = func.args
    positional = [*a.posonlyargs, *a.args]
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    found = []
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        kind = (inspect.Parameter.POSITIONAL_ONLY if index < len(a.posonlyargs)
                else inspect.Parameter.POSITIONAL_OR_KEYWORD)
        found.append(inspect.Parameter(arg.arg, kind, default=inspect.Parameter.empty if default is None
                                       else _HAS_DEFAULT))
    if skip_first:
        found = found[1:]
    if a.vararg:
        found.append(inspect.Parameter(a.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        found.append(inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY,
                                       default=inspect.Parameter.empty if default is None else _HAS_DEFAULT))
    if a.kwarg:
        found.append(inspect.Parameter(a.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    return inspect.Signature(found)


def _signature(dotted: str, target: Target) -> tuple[inspect.Signature | None, str]:
    """The parameters a call of ``target`` binds against, read from its definition, or None and why they are not
    checked; an ``Inapplicable`` for an asynchronous function (a template calls synchronously)."""
    node = target.node
    if isinstance(node, ast.AsyncFunctionDef):
        raise Inapplicable(f"{dotted} is an asynchronous function: a template calls it synchronously")
    try:
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list:
                return None, "a decorated function"
            return _parameters(node, skip_first=False), ""
        if isinstance(node, ast.ClassDef):
            init = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "__init__"),
                        None)
            if node.decorator_list or node.keywords or init is None or init.decorator_list:
                return None, "a class whose __init__ G6 cannot read from its own body"
            return _parameters(init, skip_first=True), ""
    except (ValueError, TypeError):
        return None, "a definition whose parameters G6 cannot read"
    return None, "a name not defined by def or class (an assignment or an import)"


def _arguments(dotted: str, signature: inspect.Signature | None, args: list, kwargs: dict, which: str) -> None:
    if signature is None:
        return
    try:
        signature.bind(*args, **kwargs)
    except TypeError as exc:
        raise Inapplicable(f"{dotted} cannot be called with the arguments of {which}: {exc}") from exc


@dataclass(frozen=True)
class Applied:
    """What a template made of an instance that applies: the literal its check reads, and notes for the report."""
    data: dict
    notes: list[str]


def _checked(dotted: str, target: Target, calls: list[tuple[list, dict, str]]) -> list[str]:
    """Each call's arguments bound against ``target``'s parameters; the note when they cannot be read."""
    signature, why = _signature(dotted, target)
    for args, kwargs, which in calls:
        _arguments(dotted, signature, args, kwargs, which)
    return [] if signature is not None else [f"{dotted}'s parameters were not checked: it is {why}"]


def _side(one: TemplateInstance) -> str:
    """Where an instance's names must resolve: the working tree for new behaviour (``change``), the base commit for
    behaviour to keep (``preserve``) and for every comparison with the base (``differential.v1``)."""
    return PATCH if one.intent == "change" and one.template != "differential.v1" else BASE


def _example(tree: Tree, b: ExampleBindings, side: str) -> Applied:
    target = resolve(tree, b.function, side)
    notes = _checked(b.function, target, [(case.args, case.kwargs, f"case {index}")
                                          for index, case in enumerate(b.cases)])
    cases = []
    for case in b.cases:
        entry = {"args": case.args, "kwargs": case.kwargs}
        if "raises" in case.model_fields_set:
            entry.update(expect="raises", error=_error_target(tree, case.raises, side).spec())
        elif "effect" in case.model_fields_set:
            entry.update(expect="effect", argument=case.effect.argument, value=case.effect.equals)
        else:
            entry.update(expect="returns", value=case.returns)
        cases.append(entry)
    return Applied({"function": target.spec(), "cases": cases}, notes)


def _relation(tree: Tree, b: RelationBindings, side: str) -> Applied:
    target = resolve(tree, b.function, side)
    one_argument = [([None], {}, "one input")]
    notes = _checked(b.function, target, one_argument)
    other = b.inverse or b.transformation
    found = None
    if other:
        found = resolve(tree, other, side)
        notes += _checked(other, found, one_argument)
    return Applied({"relation": b.relation, "function": target.spec(), "other": found.spec() if found else None,
                    "order": b.order or "increasing", "strict": bool(b.strict), "inputs": b.inputs}, notes)


def _access(tree: Tree, b: AccessBindings, side: str) -> Applied:
    target = resolve(tree, b.operation, side)
    notes = _checked(b.operation, target, [(case.args, case.kwargs, f"case {index}")
                                           for index, case in enumerate(b.cases)])
    error = _error_target(tree, b.exception, side).spec() if b.exception else None
    return Applied({"operation": target.spec(), "refusal": b.refusal, "error": error,
                    "cases": [{"who": case.who, "resource": case.resource, "args": case.args, "kwargs": case.kwargs,
                               "expected": case.expected} for case in b.cases]}, notes)


def _differential(tree: Tree, b: DifferentialBindings, side: str) -> Applied:
    target = resolve(tree, b.function, side)
    notes = _checked(b.function, target, [(call.args, call.kwargs, f"call {index}")
                                          for index, call in enumerate(b.calls)])
    return Applied({"function": target.spec(), "mode": b.mode,
                    "calls": [{"args": call.args, "kwargs": call.kwargs} for call in b.calls]}, notes)


# --- the fixed texts ------------------------------------------------------------------------------------------------------

_HEADER = """\
# Generated by G6 from the fixed text of a reviewed template. Everything the caller stated is in BINDINGS, one
# Python literal; nothing else here comes from the caller. Run it with pytest from the top folder of a copy of the
# repository: it imports the repository's code by the names BINDINGS holds.
import builtins
import copy
import hashlib
import importlib
import json
import re

import pytest
"""

_COMMON = """

def _load(spec):
    # spec is [kind, module, attribute]: ["builtin", "", "ValueError"], or ["module", "package.module", "name"]
    kind, module, attribute = spec
    if kind == "builtin":
        return getattr(builtins, attribute)
    return getattr(importlib.import_module(module), attribute)


def _error(spec):
    found = _load(spec)
    if not (isinstance(found, type) and issubclass(found, BaseException)):
        raise TypeError(spec[2] + " is not an exception type")
    return found


def _name(kind):
    module = getattr(kind, "__module__", None) or ""
    name = getattr(kind, "__qualname__", None) or getattr(kind, "__name__", None) or "?"
    return name if module == "builtins" else module + "." + name


def _shown(value, limit=200):
    try:
        text = repr(value)
    except Exception as exc:
        text = "<a value whose repr raised " + _name(type(exc)) + ">"
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _raised(exc):
    # an exception, its type at the start of a line, so a refusal by the run's boundary reads as one
    try:
        said = str(exc)
    except Exception:
        said = ""
    return _name(type(exc)) + ": " + _shown(said)


def _json(value):
    # the value as JSON writes it, keys sorted, or None when it is not JSON data
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None


def _same(left, right):
    # equal as JSON writes both, or, where one is not JSON data, by ==
    one, other = _json(left), _json(right)
    if one is not None and other is not None:
        return one == other
    return bool(left == right)


def _fail(failures, raised):
    # one line that says what failed, then each exception raised, its type first
    if failures:
        more = len(failures) - 5
        summary = "; ".join(failures[:5]) + ("; and %d more" % more if more > 0 else "")
        pytest.fail("\\n".join([summary] + raised[:5]))
"""

_EXAMPLE = """

def test_example():
    function = _load(BINDINGS["function"])
    errors = {index: _error(case["error"]) for index, case in enumerate(BINDINGS["cases"])
              if case["expect"] == "raises"}
    failures, raised = [], []
    for index, case in enumerate(BINDINGS["cases"]):
        args, kwargs = copy.deepcopy(case["args"]), copy.deepcopy(case["kwargs"])
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            if index in errors and isinstance(exc, errors[index]):
                continue
            failures.append("case %d raised %s" % (index, _name(type(exc))))
            raised.append(_raised(exc))
            continue
        if index in errors:
            failures.append("case %d returned %s where %s was expected" % (index, _shown(result),
                                                                           _name(errors[index])))
        elif case["expect"] == "returns" and _json(result) != _json(case["value"]):
            failures.append("case %d returned %s, expected %s" % (index, _shown(result), _shown(case["value"])))
        elif case["expect"] == "effect" and _json(args[case["argument"]]) != _json(case["value"]):
            failures.append("case %d left argument %d as %s, expected %s" % (
                index, case["argument"], _shown(args[case["argument"]]), _shown(case["value"])))
    _fail(failures, raised)
"""

_RELATION = """

def _call(function, value):
    return function(copy.deepcopy(value))


def test_relation():
    relation, function = BINDINGS["relation"], _load(BINDINGS["function"])
    other = _load(BINDINGS["other"]) if BINDINGS["other"] else None
    failures, raised, results = [], [], []
    for index, value in enumerate(BINDINGS["inputs"]):
        try:
            if relation == "monotonicity":
                results.append((index, _call(function, value)))
                continue
            if relation == "round_trip":
                left, right = _call(other, _call(function, value)), value
            elif relation == "idempotence":
                once = _call(function, value)
                left, right = _call(function, once), once
            else:
                left, right = _call(function, _call(other, value)), _call(function, value)
            holds = _same(left, right)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            failures.append("input %d raised %s" % (index, _name(type(exc))))
            raised.append(_raised(exc))
            continue
        if not holds:
            failures.append("input %d: %s gave %s where %s was expected" % (index, relation, _shown(left),
                                                                            _shown(right)))
    for (first, low), (second, high) in zip(results, results[1:]):
        try:
            if BINDINGS["order"] == "increasing":
                ordered = low < high if BINDINGS["strict"] else low <= high
            else:
                ordered = low > high if BINDINGS["strict"] else low >= high
            ordered = bool(ordered)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            failures.append("inputs %d and %d: comparing their results raised %s" % (first, second,
                                                                                    _name(type(exc))))
            raised.append(_raised(exc))
            continue
        if not ordered:
            failures.append("inputs %d and %d gave %s and %s, which are not %s%s" % (
                first, second, _shown(low), _shown(high), "strictly " if BINDINGS["strict"] else "",
                BINDINGS["order"]))
    _fail(failures, raised)
"""

_ACCESS = """

def test_access_rule():
    operation = _load(BINDINGS["operation"])
    error = _error(BINDINGS["error"]) if BINDINGS["refusal"] == "exception" else None
    failures, raised = [], []
    for index, case in enumerate(BINDINGS["cases"]):
        asked = "case %d (%s on %s)" % (index, _shown(case["who"], 100), _shown(case["resource"], 100))
        try:
            result = operation(*copy.deepcopy(case["args"]), **copy.deepcopy(case["kwargs"]))
            refused = error is None and not result
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            if error is None or not isinstance(exc, error):
                failures.append(asked + " raised " + _name(type(exc)))
                raised.append(_raised(exc))
                continue
            refused = True
        if refused != (case["expected"] == "refused"):
            failures.append(asked + ": expected %s, was %s" % (case["expected"], "refused" if refused else "allowed"))
    _fail(failures, raised)
"""

_DIFFERENTIAL = """

MARK = "G6-OBSERVATION"


def _plain(value, depth=0):
    # the value as plain JSON data: a tuple as a list, a set sorted, anything else by its type and its repr
    if depth > 8:
        return {"deeper": _name(type(value))}
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if value == value and abs(value) != float("inf") else {"float": repr(value)}
    if type(value) in (list, tuple):
        return [_plain(item, depth + 1) for item in value]
    if type(value) in (set, frozenset):
        return sorted((_plain(item, depth + 1) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _plain(item, depth + 1) for key, item in value.items()}
    if type(value) is dict:
        pairs = [[_plain(key, depth + 1), _plain(item, depth + 1)] for key, item in value.items()]
        return sorted(pairs, key=lambda pair: json.dumps(pair, sort_keys=True))
    return {"type": _name(type(value)), "repr": re.sub(r" at 0x[0-9A-Fa-f]+", "", _shown(value))}


def _bounded(value):
    text = json.dumps(value, sort_keys=True)
    if len(text) <= 500:
        return value
    return {"sha256": hashlib.sha256(bytes(text, "utf-8")).hexdigest(), "start": text[:200]}


def _outcome(call):
    try:
        function = _load(BINDINGS["function"])
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"unresolved": _name(type(exc))}
    try:
        result = function(*copy.deepcopy(call["args"]), **copy.deepcopy(call["kwargs"]))
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"raised": _name(type(exc))}
    if BINDINGS["mode"] == "exceptions":
        return {"raised": None}
    try:
        return {"returned": _bounded(_plain(result))}
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {"unreadable": _name(type(exc))}


def test_differential(capsys):
    # what the code prints itself stays captured; each observation reaches the run's output
    for call in BINDINGS["calls"]:
        line = json.dumps({"input": call, "output": _outcome(call)}, sort_keys=True)
        with capsys.disabled():
            print(MARK + " " + line, flush=True)
"""


def _text(ident: str, body: str) -> str:
    """A template's fixed text: the header, its id, the shared helpers, the slot for the bindings, and its body."""
    text = f"{_HEADER}\nTEMPLATE = {ident!r}\n{_COMMON}\n\nBINDINGS = {SLOT}\n{body}"
    assert text.count(SLOT) == 1 and text.isascii()
    return text


def compile_text(definition: TemplateDefinition, data: dict) -> str:
    """The check ``definition``'s fixed text gives for ``data``: the text with the bindings written, once, as one
    literal (``ascii``). A literal that does not read back as ``data`` is refused (``ValueError``)."""
    literal = ascii(data)
    if ast.literal_eval(literal) != data:
        raise ValueError("the bindings do not read back as the same literal")
    return definition.text.replace(SLOT, literal)


# --- the registry ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TemplateDefinition:
    """A reviewed template: its id with its version, family, obligation, bindings model, assumptions, applicability
    (``applies``: the resolved literal, or ``Inapplicable``), fixed text ("" for the static structural template), and
    the intents an instance may declare."""
    id: str
    family: str
    obligation: str
    bindings: type[BaseModel]
    assumptions: tuple[str, ...]
    applies: Callable[[Tree, Any, str], Applied] | None
    text: str
    intents: tuple[str, ...] = ("preserve", "change")

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


_CALLED = ("the names resolve to the code the runs import: a module in the top folder or in src, and a top-level name "
           "defined in it")
TEMPLATES: dict[str, TemplateDefinition] = {definition.id: definition for definition in (
    TemplateDefinition(
        "example.v1", REQUIREMENT_SATISFACTION, "a stated input gives a stated result, error type or effect",
        ExampleBindings,
        (_CALLED, "a result is compared as JSON writes it (so 2 and 2.0 differ, and a tuple reads as a list); a "
                  "result JSON cannot write matches no stated result",
         "an error matches when the exception raised is an instance of the type named"),
        _example, _text("example.v1", _EXAMPLE)),
    TemplateDefinition(
        "relation.v1", REQUIREMENT_SATISFACTION,
        "a metamorphic relation holds on the caller's inputs: round trip, idempotence, invariance under a named "
        "transformation, or monotonicity",
        RelationBindings,
        (_CALLED, "each input is the function's one argument",
         "two results are equal as JSON writes them, or by == where one is not JSON data; monotonicity compares "
         "results with < and <=",
         "the relation is checked on the inputs given only: this version searches no other input"),
        _relation, _text("relation.v1", _RELATION)),
    TemplateDefinition(
        "access_rule.v1", REQUIREMENT_SATISFACTION,
        "who may perform which operation on which resource: allowed and refused cases, each with its expected result",
        AccessBindings,
        (_CALLED, "single-step: each case is one call, with the state it needs passed in its arguments",
         "a refusal is the exception type the bindings name, or a falsy return; any other exception is a failure",
         "a refusal the run's operating-system boundary makes (a PermissionError, say) reads as the operation's own "
         "when the bindings name that type"),
        _access, _text("access_rule.v1", _ACCESS)),
    TemplateDefinition(
        "differential.v1", REGRESSION_PRESERVATION,
        "outside the change's declared scope, the base commit and the patch agree, on outputs or on exceptions only",
        DifferentialBindings,
        (_CALLED + ", at the base commit", "the caller declares that the calls lie outside the change's scope",
         "outputs are compared as plain JSON data: a value JSON cannot write by its type and its repr, without "
         "memory addresses; a long output by its SHA-256",
         "a difference is a counterexample when the instance's intent is preserve, and a question when it is change"),
        _differential, _text("differential.v1", _DIFFERENTIAL)),
    TemplateDefinition(
        "forbidden_dependency.v1", SAFETY_STRUCTURAL, "the patch adds no import that breaks a declared layer rule",
        ForbiddenBindings,
        ("the imports are read from the package's files, before and after, and no code runs",
         "only direct imports between modules of the package count: dynamic imports (importlib, __import__) and "
         "imports through another module are not seen, and modules outside the package are out of scope",
         "the repository's own contract is the one its import-linter configuration declares at the base commit"),
        None, "", ("preserve",)),
)}


def template_instances(value, requirement_ids) -> list[TemplateInstance]:
    """``value`` as the review's template instances, or a ``ValueError`` that names the instance and what is wrong:
    each names a template of ``TEMPLATES`` and a requirement of the review (``requirement_ids``), declares an intent the
    template takes, and holds bindings that are JSON data its bindings model accepts. A request's instance cannot set
    ``provenance``; a ``TemplateInstance`` built by G6's own code keeps its own."""
    if not isinstance(value, (list, tuple)):
        raise ValueError("template_instances must be a list of {id, template, requirement_id, intent, bindings}")
    if len(value) > vs.MAX_CHECKS:
        raise ValueError(f"at most {vs.MAX_CHECKS} checks in all; {len(value)} template instances were given")
    found: list[TemplateInstance] = []
    for index, item in enumerate(value):
        data = item.model_dump() if isinstance(item, TemplateInstance) else item
        name = data.get("id") if isinstance(data, dict) else None
        where = f"template_instances.{index}" + (f" ({name})" if isinstance(name, str) and _ID.fullmatch(name) else "")
        if not isinstance(data, dict):
            raise ValueError(f"{where} must be an object {{id, template, requirement_id, intent, bindings}}")
        if not isinstance(item, TemplateInstance) and "provenance" in data:
            raise ValueError(f"{where}: provenance is set by G6, never by a request")
        if data.get("intent") not in ("preserve", "change"):
            raise ValueError(f"{where}: intent must be preserve (behaviour the patch must keep) or change (behaviour "
                             "the patch must bring); an instance that declares neither is not run")
        try:
            one = TemplateInstance.model_validate(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"{where}: {'.'.join(str(p) for p in first['loc']) or 'instance'}: {first['msg']}") from exc
        definition = TEMPLATES.get(one.template)
        if definition is None:
            raise ValueError(f"{where}: template must be one of {', '.join(sorted(TEMPLATES))}")
        if one.intent not in definition.intents:
            raise ValueError(f"{where}: {one.template} checks that behaviour is kept: its intent is preserve")
        problem = literal_problem(one.bindings)
        if problem:
            raise ValueError(f"{where}: {problem}")
        try:
            definition.bindings.model_validate(one.bindings)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise ValueError(f"{where}: bindings.{'.'.join(str(p) for p in first['loc'])}: {first['msg']}") from exc
        if one.requirement_id not in requirement_ids:
            raise ValueError(f"{where}: requirement_id {one.requirement_id!r} names no requirement this review "
                             "assesses")
        found.append(one)
    if len({one.id for one in found}) != len(found):
        raise ValueError("template instance ids must be unique")
    return found


def instance_digest(one: TemplateInstance) -> str:
    """The SHA-256 that cites an instance: its template's id and version, the digest of the template's fixed text, and
    the instance's requirement, intent, bindings and their provenance, as canonical JSON."""
    definition = TEMPLATES[one.template]
    body = {"template": one.template, "template_text_sha256": definition.text_sha256, "id": one.id,
            "requirement_id": one.requirement_id, "intent": one.intent, "bindings": one.bindings,
            "provenance": one.provenance}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()


# --- preparing the instances of a review, before anything runs ------------------------------------------------------------

@dataclass
class Prepared:
    """A review's template instances, prepared before anything runs: the compiled checks with their places and origins
    (run by ``verification_search.search``), each structural result, and the activity of each instance that is not a
    compiled check (refused, not run, or structural)."""
    checks: list[vs.ProposedCheck] = field(default_factory=list)
    placed: dict[str, str] = field(default_factory=dict)
    origins: dict[str, vs.Origin] = field(default_factory=dict)
    structural: list[dict] = field(default_factory=list)
    activities: list[dict] = field(default_factory=list)
    instances: int = 0


def _listing(one: TemplateInstance) -> dict:
    definition = TEMPLATES[one.template]
    return {"activity": "template instance", "check": one.id, "template": one.template, "family": definition.family,
            "requirement": one.requirement_id, "intent": one.intent, "instance_sha256": instance_digest(one),
            "bindings_provenance": one.provenance, "seconds": 0.0, "model_calls": 0}


def _compiled(root: Path, tree: Tree, one: TemplateInstance) -> tuple[vs.ProposedCheck, str, vs.Origin]:
    definition = TEMPLATES[one.template]
    applied = definition.applies(tree, definition.bindings.model_validate(one.bindings), _side(one))
    source = compile_text(definition, applied.data)
    if len(source.encode("utf-8")) > vs.MAX_SOURCE_BYTES:
        raise Inapplicable("its compiled check would hold more than 64 kilobytes")
    check = vs.ProposedCheck(id=one.id, requirement_id=one.requirement_id, intent=one.intent,
                             file=f"test_g6_{one.template.split('.')[0]}.py", source=source)
    try:
        placed = vs.placements(root, [check])[one.id]
    except ValueError as exc:
        raise Inapplicable(f"G6 could not place its compiled check: {exc}") from exc
    about = {key: value for key, value in _listing(one).items() if key in (
        "template", "family", "instance_sha256", "bindings_provenance")}
    about["notes"] = applied.notes
    label = f"an instance of {one.template} G6 compiled"
    origin = vs.Origin(
        activity="template instance", proposed_by=COMPILED_BY, about=about, label=label,
        unconfirmed="G6 compiled it from the caller's bindings, and a compiled check decides no requirement",
        caution=(f"G6 generated the check from the fixed text of template {one.template}, with the caller's bindings "
                 "written in it as one literal; it imports and runs the repository's code, so G6 ran it only inside "
                 "its operating-system boundary, with no network. Read it before you run it, or run it in a "
                 "container."),
        template=one.template, refuted_by_difference=one.template == "differential.v1" and one.intent == "preserve")
    return check, placed, origin


def prepare(root: Path, change: repo_tests.Change, instances: list[TemplateInstance], *,
            enabled: bool = True, deadline: float | None = None) -> Prepared:
    """Each instance checked against the tree and, where it applies, compiled and placed (or, for the structural
    template, checked): reading files only, before anything runs, and the structural check no further than
    ``deadline`` (the review's, on ``_clock``). ``enabled`` false (``search.templates``): none is run, each listed as
    skipped."""
    found = Prepared(instances=len(instances))
    if not instances:
        return found
    if not enabled:
        found.activities = [{**_listing(one), "result": "skipped", "reason": TEMPLATES_OFF} for one in instances]
        return found
    tree = Tree.read(root, change, deadline)
    for one in instances:
        definition = TEMPLATES[one.template]
        try:
            if definition.text:
                check, placed, origin = _compiled(root, tree, one)
                found.checks.append(check)
                found.placed[one.id] = placed
                found.origins[one.id] = origin
            else:
                result = structural(tree, one)
                found.structural.append(result)
                found.activities.append(result["activity"])
        except (Inapplicable, ValueError, OSError, SyntaxError, RecursionError, repo_tests.RepoRefused) as exc:
            why = str(exc) if isinstance(exc, Inapplicable) else (
                f"G6 could not read what it names ({type(exc).__name__})")
            refused = {**_listing(one), "result": "refused", "reason": why}
            found.activities.append(refused)
            if not definition.text:
                provenance = _contract_provenance(one)
                found.structural.append({"id": one.id, "requirement": one.requirement_id, "verdict": "not_run",
                                         "normative": provenance != "model_hypothesis",
                                         "contract_provenance": provenance, "activity": refused, "violations": []})
    return found


# --- forbidden_dependency.v1: imports read before and after, against a declared contract ----------------------------------

@dataclass(frozen=True)
class _Contract:
    name: str
    package: str
    layers: tuple[str, ...]
    forbidden: tuple[tuple[str, str], ...]
    ignore: tuple[str, ...] = ()


_CONFIGS = (".importlinter", "setup.cfg", "pyproject.toml")
_LIST_KEYS = frozenset({"layers", "containers", "source_modules", "forbidden_modules", "ignore_imports",
                        "root_packages", "modules"})
_KIND = re.compile(r"[a-z_]{1,40}")
_IGNORE = re.compile(r"\s*([A-Za-z0-9_.*]+)\s*->\s*([A-Za-z0-9_.*]+)\s*")


def _contract_provenance(one: TemplateInstance) -> str:
    contract = one.bindings.get("contract") if isinstance(one.bindings, dict) else None
    return "repository_stated" if contract == "repository" else one.provenance


def _lines(value) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip() and not line.strip().startswith("#")]
    return []


def _ini(text: str) -> tuple[list[str], list[dict]] | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    if not parser.has_section("importlinter"):
        return None
    main = parser["importlinter"]
    roots = _lines(main.get("root_packages", "")) or _lines(main.get("root_package", ""))
    contracts = [{key: (_lines(value) if key in _LIST_KEYS else value.strip()) for key, value in parser[name].items()}
                 for name in parser.sections() if name.startswith("importlinter:contract:")]
    return roots, contracts


def _toml(text: str) -> tuple[list[str], list[dict]] | None:
    try:
        section = tomllib.loads(text).get("tool", {}).get("importlinter")
    except (tomllib.TOMLDecodeError, AttributeError):
        return None
    if not isinstance(section, dict):
        return None
    roots = _lines(section.get("root_packages")) or _lines([section.get("root_package")])
    contracts = section.get("contracts") or []
    if isinstance(contracts, dict):
        contracts = list(contracts.values())
    return roots, [item for item in contracts if isinstance(item, dict)] if isinstance(contracts, list) else []


def _declared(tree: Tree, side: str) -> tuple[str, tuple[list[str], list[dict]]] | None:
    """The first import-linter configuration on ``side``, and what it holds."""
    for rel in _CONFIGS:
        if not tree.has(rel, side):
            continue
        try:
            text = tree.text(rel, side)
        except (ValueError, OSError, repo_tests.RepoRefused):
            continue
        parsed = _toml(text) if rel == "pyproject.toml" else _ini(text)
        if parsed is not None:
            return rel, parsed
    return None


def _dotted_ok(value: str) -> bool:
    try:
        _dotted(value, attribute=False)
    except ValueError:
        return False
    return True


def _in(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def _read_contracts(roots: list[str], raw: list[dict]) -> tuple[list[_Contract], list[str]]:
    """The layers and forbidden contracts of an import-linter configuration, each with its package; and what is out of
    scope, said in G6's words (a type G6 does not read, siblings in a layer, a name that is not a dotted name)."""
    found, out = [], []
    roots = [root for root in roots if _dotted_ok(root)]
    for index, item in enumerate(raw):
        label = f"contract {index + 1}"
        kind = item.get("type")
        if not isinstance(kind, str) or not _KIND.fullmatch(kind):
            out.append(f"{label}: a contract of a type G6 does not read")
            continue
        if kind not in ("layers", "forbidden"):
            out.append(f"{label}: a contract of type {kind} is not read (G6 reads layers and forbidden contracts)")
            continue
        ignore = tuple(_lines(item.get("ignore_imports")))
        if kind == "layers":
            layers = [layer[1:-1].strip() if layer.startswith("(") and layer.endswith(")") else layer
                      for layer in _lines(item.get("layers"))]
            if any("|" in layer or ":" in layer for layer in layers):
                out.append(f"{label}: a layer of sibling modules is not read")
                continue
            containers = _lines(item.get("containers")) or [""]
            stacks = [tuple(f"{c}.{layer}" if c else layer for layer in layers) for c in containers]
            pairs: tuple = ()
        else:
            stacks = [()]
            pairs = tuple((source, banned) for source in _lines(item.get("source_modules"))
                          for banned in _lines(item.get("forbidden_modules")))
            if not pairs:
                out.append(f"{label}: a forbidden contract that names no pair")
                continue
        for stack in stacks:
            names = [*stack, *(name for pair in pairs for name in pair)]
            if not names or not all(_dotted_ok(name) for name in names):
                out.append(f"{label}: a name that is not a dotted module name")
                continue
            package = next((root for root in roots if any(_in(name, root) for name in names)), None)
            if package is None:
                out.append(f"{label}: no module it names is inside the root package the configuration declares")
                continue
            found.append(_Contract(label, package, stack, pairs, ignore))
    return found, out


def _package_files(tree: Tree, package: str, side: str) -> tuple[dict[str, str], str] | None:
    """The package's Python files on ``side``, by path relative to the folder that holds it (as design_governance reads
    them), and that folder's prefix ("" or "src/"); None when it has none there."""
    from g6.code.design_governance import graph

    stem = package.replace(".", "/")
    listed = tree.at_base if side == BASE else tree.known
    for folder in ("", "src/"):
        names = sorted(rel for rel in listed if rel.endswith(".py") and (
            rel.startswith(folder + stem + "/") or rel == folder + stem + ".py")
            and graph.module_of(rel[len(folder):]) is not None)
        if names:
            break
    else:
        return None
    if len(names) > MAX_PACKAGE_FILES:
        raise Inapplicable(f"{package} holds more than {MAX_PACKAGE_FILES} files {WHERE[side]}")
    files, size = {}, 0
    for rel in names:
        tree.in_time()
        files[rel[len(folder):]] = text = tree.text(rel, side)
        size += len(text)
        if size > MAX_PACKAGE_CHARS:
            raise Inapplicable(f"{package} holds more than {MAX_PACKAGE_CHARS} characters {WHERE[side]}")
    return files, folder


def _pattern(text: str) -> re.Pattern:
    parts = [".+" if part == "**" else "[^.]+" if part == "*" else re.escape(part) for part in text.split(".")]
    return re.compile(r"\.".join(parts))


def _ignored(violation: dict, ignore: tuple[str, ...]) -> bool:
    for rule in ignore:
        found = _IGNORE.fullmatch(rule)
        if found and _pattern(found[1]).fullmatch(violation["from"]) and _pattern(found[2]).fullmatch(violation["to"]):
            return True
    return False


def _breaches(found, contract: _Contract) -> tuple[list[dict], set[str]]:
    """The contract's breaches in the graph ``found``, with what no module of it is in left out; and the prefixes it
    left out."""
    from g6.code._types import CodeRefused
    from g6.code.design_governance import graph

    def rank(module: str) -> int | None:
        hits = [i for i, prefix in enumerate(contract.layers) if _in(module, prefix)]
        return max(hits, key=lambda i: len(contract.layers[i])) if hits else None

    assigned = {rank(module) for module in found.modules}
    layers = tuple(layer for i, layer in enumerate(contract.layers) if i in assigned)
    matched = lambda prefix: any(_in(module, prefix) for module in found.modules)   # noqa: E731
    pairs = tuple(pair for pair in contract.forbidden if matched(pair[0]) and matched(pair[1]))
    missing = {layer for layer in contract.layers if layer not in layers} | {
        prefix for pair in contract.forbidden if pair not in pairs for prefix in pair if not matched(prefix)}
    if not layers and not pairs:
        return [], missing
    try:
        breaches = graph.layer_violations(found, layers, pairs)
    except CodeRefused:
        return [], missing
    return [item for item in breaches if not _ignored(item, contract.ignore)], missing


def structural(tree: Tree, one: TemplateInstance) -> dict:
    """``forbidden_dependency.v1`` on the tree: each contract's breaches on the package at the base commit and on the
    working tree, read statically, and the ones the patch adds. An ``Inapplicable`` when nothing can be checked."""
    from g6.code.design_governance import graph

    bindings = ForbiddenBindings.model_validate(one.bindings)
    provenance = _contract_provenance(one)
    limits = [TEMPLATES[one.template].assumptions[1]]
    out: list[str] = []
    if bindings.contract == "repository":
        declared = _declared(tree, BASE)
        if declared is None:
            added = _declared(tree, PATCH)
            raise Inapplicable("the repository declares no import-linter contract at the base commit" + (
                f" (the patch adds one in {added[0]}, which is not the repository's own)" if added else ""))
        rel, (roots, raw) = declared
        contracts, out = _read_contracts(roots, raw)
        source = f"{rel} at the base commit"
        if rel in tree.change.files:
            limits.append(f"the patch changes {rel}; G6 used the contract as the base commit declares it")
        if any(item.get("type") == "forbidden" and str(item.get("allow_indirect_imports", "")).lower() != "true"
               for item in raw):
            limits.append("import-linter also checks imports made through other modules; G6 checks direct ones only")
    else:
        stated = bindings.contract
        contracts = [_Contract("the instance's contract", bindings.package, tuple(stated.layers),
                               tuple((pair.source, pair.forbidden) for pair in stated.forbidden))]
        source = "the instance's bindings"
    scoped = []
    for contract in contracts:
        outside = [name for name in (*contract.layers, *(n for pair in contract.forbidden for n in pair))
                   if not _in(name, contract.package)]
        out += [f"{contract.name}: {name} is outside the package {contract.package}, so G6 does not check imports of "
                "it" for name in dict.fromkeys(outside)]
        kept = _Contract(contract.name, contract.package,
                         tuple(layer for layer in contract.layers if layer not in outside),
                         tuple(pair for pair in contract.forbidden if pair[0] not in outside
                               and pair[1] not in outside), contract.ignore)
        if kept.layers or kept.forbidden:
            scoped.append(kept)
    if not scoped:
        raise Inapplicable("no contract G6 can read applies to the package: " + "; ".join(out[:5]) if out else
                           "the contract names nothing G6 can check")
    graphs: dict[tuple[str, str], tuple] = {}
    new, already, unparsed, checked = [], [], [], 0
    for contract in scoped:
        for side in (BASE, PATCH):
            if (contract.package, side) not in graphs:
                tree.in_time()
                files = _package_files(tree, contract.package, side)
                tree.in_time()
                graphs[contract.package, side] = (graph.build(files[0], contract.package), files[1]) if files else (
                    None, "")
        (before, _), (after, folder) = graphs[contract.package, BASE], graphs[contract.package, PATCH]
        if after is None or not after.modules:
            out.append(f"{contract.name}: the package {contract.package} has no module {WHERE[PATCH]}")
            continue
        named = set(contract.layers) | {prefix for pair in contract.forbidden for prefix in pair}
        old, missing_before = _breaches(before, contract) if before is not None else ([], named)
        found, missing_after = _breaches(after, contract)
        out += [f"{contract.name}: {prefix} matches no module of the package, so it is out of scope"
                for prefix in sorted(missing_before & missing_after)]
        if named <= missing_before & missing_after:
            continue
        checked += 1
        seen = {(item["rule"], item["from"], item["to"]) for item in old}
        for item in found:
            located = {**item, "file": folder + item["file"], "contract": contract.name}
            (already if (item["rule"], item["from"], item["to"]) in seen else new).append(located)
        unparsed += [folder + entry["file"] for entry in after.unparsed]
        limits.append(f"{after.external} import(s) of modules outside the package {contract.package} "
                      f"{WHERE[PATCH]} were not checked")
    if not checked:
        raise Inapplicable("the contract matches no module of the package: " + "; ".join(out[:5]))
    edges: dict[tuple, dict] = {}
    for item in new:
        edges.setdefault((item["contract"], item["rule"], item["from"], item["to"]), item)
    violations = []
    for item in edges.values():
        try:
            text = tree.text(item["file"], PATCH).splitlines()[item["line"] - 1].strip()[:200]
        except (ValueError, IndexError, OSError):
            text = ""
        violations.append({key: item[key] for key in ("rule", "from", "to", "file", "line", "lazy", "why")}
                          | {"text": text})
    normative = provenance != "model_hypothesis"
    if violations:
        verdict = "failed" if normative else "counterexample"
    elif unparsed:
        verdict = "not_run"
        limits.append(f"{len(unparsed)} file(s) of the package do not parse {WHERE[PATCH]}, so their imports are "
                      f"not seen: {', '.join(unparsed[:5])}")
    else:
        verdict = "passed" if normative else "not_refuted"
    shown = "; ".join(f"{v['from']} imports {v['to']} ({v['file']}:{v['line']})" for v in violations[:5])
    detail = (f"{one.id}: the patch adds {len(violations)} import(s) that {source} forbids: {shown}" if violations
              else f"{one.id}: no import the patch adds breaks {source}")
    activity = {**_listing(one), "provenance": "structural", "contract_provenance": provenance,
                "contract_source": source, "result": verdict if verdict != "not_run" else "not_established",
                "violations": violations, "already_at_base": [{key: item[key] for key in ("from", "to", "file",
                                                                                          "line")}
                                                              for item in already],
                "out_of_scope": list(dict.fromkeys(out)), "limits": list(dict.fromkeys(limits)), "ran_code": False}
    reproduction = {"kind": "static", "imports": violations, "contract": source, "contract_provenance": provenance,
                    "how": "read each file at its line on the working tree: the import is there, and the base "
                           "commit's tree has no import between those modules; nothing needs to run",
                    **({"command": "lint-imports"} if provenance == "repository_stated" else {})}
    return {"id": one.id, "requirement": one.requirement_id, "verdict": verdict, "normative": normative,
            "contract_provenance": provenance, "contract_source": source, "detail": detail, "activity": activity,
            "violations": violations, "reproduction": reproduction}
