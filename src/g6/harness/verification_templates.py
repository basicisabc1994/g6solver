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
  layer rule, read statically from ``design_governance``'s import graph of the package before and after (no code
  runs). The contract is the repository's own, an import-linter configuration (``.importlinter``, ``setup.cfg``
  or ``pyproject.toml``) as the base commit declares it, so the patch cannot loosen it (provenance
  ``repository_stated``), or one the instance states (``stated``); against either, a new forbidden import is a failed
  check, so ``defect_detected``. The owner's ruling R-C1: a check that finds none never passes its requirement, which
  keeps the status its stated checks give it; the contract holding is reported in the activity and in
  ``completion.preservation``, and as ``vacuous`` ("does not exercise the change") when the patch could not have
  broken it (``_unexercised``). An instance a model proposed (``model_hypothesis``, which only G6's own code sets)
  gives at most insufficient evidence, whatever contract it names, the repository's included (reviewer B's B1).
  A part of a contract that matches no module of the package, and a module outside the package, are reported as out
  of scope, never as violations. It follows chains of imports inside the package as import-linter does (a forbidden
  contract with ``allow_indirect_imports`` checks direct ones only), with those inside functions included and those
  under ``TYPE_CHECKING`` too unless the configuration's ``exclude_type_checking_imports`` leaves them out; dynamic
  imports are not seen. An option, contract type or layer syntax of the configuration that G6 does not implement
  (``_unsupported``) makes the instance not applicable, with the option named, and so does a package that more than
  one folder holds: G6 never guesses.

An instance is data, never code: ``{id, template, requirement_id, intent, bindings}`` (``template_instances``). A name
in the bindings is a dotted name of the letters A to Z (either case), digits and ``_``, a module path and then a
top-level attribute (``package.module.function``); an exception is a built-in exception's name or such a dotted name;
there is no keyword and no double-underscore name in either. A literal is JSON
data (no tuple, set, bytes, number that is not finite, or key that is not text), at most ``MAX_DEPTH`` levels deep and
``MAX_BINDINGS_CHARS`` characters as JSON. Anything else is refused, and so is the whole review, before anything runs.
``prepare`` then checks each instance against the tree (``Tree``), reading files and never importing them in G6's
process: each name must resolve to a module file in an import root, the top folder or ``src`` and a top-level definition in it (a
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
import collections
import configparser
import dataclasses
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

from g6.core import component_process
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
#: A structural check whose contract the patch could not have broken (the owner's ruling R-C1).
VACUOUS = "vacuous"


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
        return _keyword_names(value)


def _keyword_names(value: dict) -> dict:
    if any(not key.isascii() or not key.isidentifier() or keyword.iskeyword(key) for key in value):
        raise ValueError("each key of kwargs must be a parameter's name: the letters A to Z (either case), digits "
                         "and _")
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


#: ``state_invariant.v1``'s limits: the depth of a search whose traces are replayed, and how many traces it replays.
MAX_FORMAL_DEPTH = 12
MAX_TRACES = 16
DEFAULT_TRACES = 8
#: The operators the replay evaluates an invariant with, on the values it observed (the fixed text's ``_value``).
REPLAY_OPS = frozenset({"and", "or", "not", "=>", "xor", "=", "distinct", "<", "<=", ">", ">=", "+", "-", "*", "div",
                        "mod", "ite"})
#: State variable names G6 keeps for its own (a trace's history).
RESERVED = "g6_"
_INTEGER = re.compile(r"-?[0-9]{1,9}")


class CallSpec(Call):
    """A call of a top-level callable the tree defines (``call``), with JSON arguments."""
    call: Name


class ObserveSpec(CallSpec):
    """How a state variable is read on the real state: ``call(state, *args, **kwargs)``, its value; or, with
    ``refusal``, an access outcome (true when allowed): refused when it raises ``exception``, or returns a falsy value."""
    refusal: Literal["exception", "falsy_return"] | None = None
    exception: ErrorName | None = None

    @model_validator(mode="after")
    def _refusal(self):
        if (self.refusal == "exception") != (self.exception is not None):
            raise ValueError("refusal exception names the exception type the call raises to refuse (exception); any "
                             "other read names none")
        return self


class StateInvariantBindings(_Strict):
    """``state_invariant.v1``: a constructor for the initial state, an abstract state model in model_checking's closed
    grammar, the Int state variable whose value at each step names the action that led to it (``action``), each such
    value's operation call (``actions``, called with the state first), how each state variable the invariants read is
    observed on the real state (``observe``), and the search: ``bounded`` to ``depth`` steps, replaying at most
    ``traces`` of them, or ``induction``."""
    constructor: CallSpec
    model: dict[str, Any]
    action: str = Field(min_length=1, max_length=100)
    actions: dict[str, CallSpec] = Field(min_length=1, max_length=16)
    observe: dict[str, ObserveSpec] = Field(min_length=1, max_length=32)
    depth: int = Field(default=4, ge=1)
    traces: int = Field(default=DEFAULT_TRACES, ge=1)
    search: Literal["bounded", "induction"] = "bounded"

    @model_validator(mode="after")
    def _model(self):
        from g6.model_checking.encoding import read_model
        from g6.model_checking.envelope import INPUT_TOO_LARGE, Refusal

        variables = self.model.get("variables")
        if isinstance(variables, dict) and any(isinstance(name, str) and name.startswith(RESERVED)
                                               for name in variables):
            raise ValueError(f"model: no state variable's name may begin with {RESERVED}, which G6 keeps for its own")
        try:
            read_model(self.model, need_invariants=True)
        except Refusal as exc:
            if exc.code != INPUT_TOO_LARGE:   # over the limits: refused for this instance, before anything runs
                raise ValueError(f"model: {exc.reason}") from None
        if not isinstance(variables, dict) or variables.get(self.action) != "Int":
            raise ValueError("action must name an Int state variable of the model: the one whose value at each step "
                             "says which action led to that step")
        if any(not _INTEGER.fullmatch(key) or str(int(key)) != key for key in self.actions):
            # the review of VS-4, M3: "01" beside "1" would read as one action, with one of the calls dropped
            raise ValueError("actions maps each value of the action variable, written as a whole number in its "
                             "canonical form (no leading zero or sign but a minus, and 0 for zero), to an operation "
                             "call")
        if any(name not in variables or name == self.action for name in self.observe):
            raise ValueError("observe names state variables of the model, other than the action variable")
        return self


class AccessStep(CallSpec):
    """A step of access_rule.v1's multi-step form: an operation call (state first), and its effect on whether the
    access is allowed: ``allow`` (grant, share), ``refuse`` (revoke), or ``none``."""
    effect: Literal["allow", "refuse", "none"]


class AccessStepsBindings(_Strict):
    """access_rule.v1's multi-step form: from the constructor's state, any sequence of the named ``steps`` (grant,
    share, revoke, ...), then the access (``operation`` on the state with ``args``, refused by ``refusal``) allowed
    exactly when the steps' effects, from ``initially``, say so. It goes through state_invariant.v1's path: G6 builds the
    model from the steps (``access_model``)."""
    constructor: CallSpec
    operation: Name
    args: list[Any] = Field(default_factory=list, max_length=32)
    kwargs: dict[str, Any] = Field(default_factory=dict, max_length=32)
    who: str = Field(min_length=1, max_length=100)
    resource: str = Field(min_length=1, max_length=100)
    refusal: Literal["exception", "falsy_return"]
    exception: ErrorName | None = None
    steps: dict[str, AccessStep] = Field(min_length=1, max_length=8)
    initially: Literal["allowed", "refused"]
    depth: int = Field(default=4, ge=1)
    traces: int = Field(default=DEFAULT_TRACES, ge=1)

    @field_validator("steps")
    @classmethod
    def _names(cls, value: dict) -> dict:
        if any(not re.fullmatch(_PART, name, re.ASCII) for name in value):
            raise ValueError("each step's name is a plain name: letters A to Z (either case), digits and _")
        return value

    @field_validator("kwargs")
    @classmethod
    def _keywords(cls, value: dict) -> dict:
        return _keyword_names(value)

    @model_validator(mode="after")
    def _refusal(self):
        if (self.refusal == "exception") != (self.exception is not None):
            raise ValueError("refusal exception names the exception type the operation raises to refuse (exception); "
                             "refusal falsy_return names none")
        return self


def access_model(b: AccessStepsBindings) -> dict:
    """The state_invariant.v1 bindings a multi-step access rule goes through: G6's model, from the steps alone, of
    whether the access may be allowed (``may``, set by each step's effect), the access as the code answers it
    (``allowed``, observed on the real state, free in the model) and the step taken (``act``: 1, 2, ... by the steps'
    names in order); the rule, ``(= allowed may)``."""
    names = sorted(b.steps)
    effect = {"allow": "(= next_may true)", "refuse": "(= next_may false)", "none": "(= next_may may)"}
    moves = [f"(and (= next_act {i}) {effect[b.steps[name].effect]})" for i, name in enumerate(names, 1)]
    return {"constructor": b.constructor.model_dump(),
            "model": {"variables": {"act": "Int", "allowed": "Bool", "may": "Bool"},
                      "init": f"(and (= act 0) (= may {'true' if b.initially == 'allowed' else 'false'}))",
                      "transition": moves[0] if len(moves) == 1 else "(or " + " ".join(moves) + ")",
                      "invariants": {"access_rule": "(= allowed may)"}},
            "action": "act",
            "actions": {str(i): {"call": b.steps[name].call, "args": b.steps[name].args,
                                 "kwargs": b.steps[name].kwargs} for i, name in enumerate(names, 1)},
            "observe": {"allowed": {"call": b.operation, "args": b.args, "kwargs": b.kwargs, "refusal": b.refusal,
                                    "exception": b.exception}},
            "depth": b.depth, "traces": b.traces, "search": "bounded"}


class _AccessForms:
    """access_rule.v1's bindings: its single-step form (``AccessBindings``), or, with ``steps``, its multi-step form,
    checked as the state_invariant.v1 bindings it goes through too."""

    @staticmethod
    def model_validate(data):
        if isinstance(data, dict) and "steps" in data:
            found = AccessStepsBindings.model_validate(data)
            StateInvariantBindings.model_validate(access_model(found))
            return found
        return AccessBindings.model_validate(data)


def multi_step(one) -> bool:
    """Whether an instance is access_rule.v1's multi-step form, which goes through state_invariant.v1's path."""
    return one.template == "access_rule.v1" and isinstance(one.bindings, dict) and "steps" in one.bindings


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
    #: The review's import roots: the runs import from them first, then from the top folder and ``src``.
    roots: tuple[str, ...] = ()

    @classmethod
    def read(cls, root: Path, change: repo_tests.Change, deadline: float | None = None,
             roots: tuple[str, ...] = ()) -> Tree:
        known = repo_tests._paths(repo_tests._git(root, "ls-files", "-co", "--exclude-standard", "-z"))
        at_base = repo_tests._paths(repo_tests._git(root, "ls-tree", "-r", "-z", "--name-only", change.base_commit))
        return cls(root, change, frozenset(known), frozenset(at_base), deadline=deadline, roots=tuple(roots))

    def folders(self) -> tuple[str, ...]:
        """The folders a module is found in, in the order the runs' import path has them: the import roots, the top
        folder, ``src``; each as a prefix of a path."""
        return (*(root.rstrip("/") + "/" for root in self.roots), "", "src/")

    def in_time(self) -> None:
        """An ``Inapplicable`` once the review's deadline has passed."""
        if self.deadline is not None and _clock() >= self.deadline:
            raise Inapplicable(OUT_OF_TIME)

    def has(self, rel: str, side: str) -> bool:
        return rel in (self.at_base if side == BASE else self.known)

    def preload(self, rels: list[str]) -> None:
        """The base commit's texts of ``rels``, read in a few git calls (``repo_tests.texts_at``); one git does not
        hold as a file of at most ``MAX_MODULE_BYTES`` reads as unreadable."""
        wanted = [rel for rel in rels if (rel, BASE) not in self._texts]
        if wanted:
            found = repo_tests.texts_at(self.root, self.change.base_commit, wanted, limit=MAX_MODULE_BYTES)
            self._texts.update({(rel, BASE): found.get(rel) for rel in wanted})

    def text(self, rel: str, side: str) -> str:
        """``rel``'s text on ``side`` (a ``ValueError`` when it cannot be read): on the working tree, from the disk;
        at the base commit, always from git's blob, never from the working tree, whatever git's index flags say
        (review C2)."""
        key = (rel, side)
        if self._texts.get(key, "") is None:
            raise ValueError(f"{rel} is not a file of at most {MAX_MODULE_BYTES} bytes at the base commit")
        if key not in self._texts:
            if side == PATCH:
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
    """The file of the module ``parts`` names on ``side``, as the runs' import path finds it: the import roots, the
    top folder, then ``src``; a package's ``__init__.py`` before a module of the same name. "" when none."""
    if any(part in _LEFT_OUT for part in parts):
        return ""
    for folder in tree.folders():
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
        raise Inapplicable(f"{dotted}: no module {module} {WHERE[side]} (G6 looks for it in the import roots, the top "
                           "folder and src, as the runs' import path has them)")
    try:   # a RecursionError or a MemoryError, Python's parser out of depth, is G6's own error (review I3), not here
        found, star = _top_level(ast.parse(tree.text(rel, side)), attribute)
    except (SyntaxError, ValueError, OSError, repo_tests.RepoRefused) as exc:
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


def _replayable(node, sorts: dict[str, str], name: str, used: set[str], depth: int = 1):
    """An invariant's parsed term as the replay evaluates it, nested lists of operators, variables, whole numbers and
    booleans; an ``Inapplicable`` naming anything else (a quantifier, a let, a real number, a bit-vector)."""
    if depth > 64:
        raise Inapplicable(f"invariant {name} is nested more than 64 levels deep, more than the replay evaluates")
    if isinstance(node, str):
        if node in ("true", "false"):
            return node == "true"
        if node.isdigit():
            return int(node)
        if node in sorts:
            if sorts[node] not in ("Int", "Bool"):
                raise Inapplicable(f"{node} is a {sorts[node]}: the replay reads Int or Bool variables only")
            used.add(node)
            return node
        raise Inapplicable(f"invariant {name} uses {node[:40]}, which the replay cannot evaluate on the real state")
    head = node[0] if node else None
    if not isinstance(head, str) or head not in REPLAY_OPS:
        shown = head if isinstance(head, str) else "a term in operator position"
        raise Inapplicable(f"invariant {name} uses {str(shown)[:40]}, which the replay cannot evaluate on the real "
                           "state")
    return [head, *(_replayable(part, sorts, name, used, depth + 1) for part in node[1:])]


def _state_invariant(tree: Tree, b: StateInvariantBindings, side: str) -> Applied:
    """Every name resolved and every call's arguments bound; the model inside model_checking's limits and G6's own; each
    invariant one the replay evaluates, reading at least one observed variable (else a trace that breaks it would say
    nothing of the code). The literal still lacks its traces: the formal path adds them."""
    from g6.formal.z3_bridge import _term
    from g6.model_checking.encoding import MAX_VARIABLES, read_model
    from g6.model_checking.envelope import Refusal

    try:
        model = read_model(b.model, need_invariants=True)
    except Refusal as exc:
        raise Inapplicable(f"the model is over model_checking's limits: {exc.reason}") from None
    if b.depth > MAX_FORMAL_DEPTH:
        raise Inapplicable(f"depth {b.depth} is above the limit of {MAX_FORMAL_DEPTH} steps for a search whose traces "
                           "are replayed")
    if b.traces > MAX_TRACES:
        raise Inapplicable(f"traces {b.traces} is above the limit of {MAX_TRACES} replayed traces")
    if b.search == "bounded" and len(model.variables) + 1 + b.depth > MAX_VARIABLES:
        raise Inapplicable(f"the model has {len(model.variables)} state variables; with the {1 + b.depth} G6 adds to "
                           f"keep each trace's history (a step counter and one per step), that is over model_checking's "
                           f"limit of {MAX_VARIABLES}")
    for name in b.observe:
        if model.variables[name] not in ("Int", "Bool"):
            raise Inapplicable(f"{name} is a {model.variables[name]}: the replay reads Int or Bool variables only")
    invariants, read = {}, set()
    for name, text in model.invariants.items():
        used: set[str] = set()
        invariants[name] = _replayable(_term(text, f"invariant {name}").tree, model.variables, name, used)
        if not used & set(b.observe):
            raise Inapplicable(f"invariant {name} reads no observed variable, so a trace that breaks it would say "
                               "nothing of the code: a result about it is about the model only")
        read |= used
    constructor = resolve(tree, b.constructor.call, side)
    notes = _checked(b.constructor.call, constructor, [(b.constructor.args, b.constructor.kwargs, "the constructor")])
    actions = {}
    for key, call in b.actions.items():
        target = resolve(tree, call.call, side)
        notes += _checked(call.call, target, [([None, *call.args], call.kwargs, f"action {key}")])
        actions[str(int(key))] = {"call": target.spec(), "args": call.args, "kwargs": call.kwargs}
    observe = {}
    for name, call in b.observe.items():
        target = resolve(tree, call.call, side)
        notes += _checked(call.call, target, [([None, *call.args], call.kwargs, f"the read of {name}")])
        observe[name] = {"call": target.spec(), "args": call.args, "kwargs": call.kwargs, "refusal": call.refusal,
                         "error": _error_target(tree, call.exception, side).spec() if call.exception else None}
    return Applied({"constructor": {"call": constructor.spec(), "args": b.constructor.args,
                                    "kwargs": b.constructor.kwargs},
                    "actions": actions, "observe": observe,
                    "sorts": {name: model.variables[name] for name in sorted(read | set(b.observe))},
                    "invariants": invariants}, list(dict.fromkeys(notes)))


# --- the fixed texts ------------------------------------------------------------------------------------------------------

_HEADER = """\
# Generated by G6 from the fixed text of a reviewed template. Everything the caller stated is in BINDINGS, one
# Python literal; nothing else here comes from the caller. Run it with pytest from the top folder of a copy of the
# repository: it imports the repository's code by the names BINDINGS holds.
import builtins
import collections.abc
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


def _attributes(value):
    # an object's own attributes, from its __dict__ and the __slots__ of its classes, or None when it has neither
    found, seen = {}, False
    if isinstance(getattr(value, "__dict__", None), dict):
        found.update(vars(value))
        seen = True
    for kind in type(value).__mro__:
        slots = kind.__dict__.get("__slots__", ())
        for slot in [slots] if isinstance(slots, str) else slots:
            if slot not in ("__dict__", "__weakref__") and hasattr(value, slot):
                found[slot] = getattr(value, slot)
                seen = True
    return {str(key): item for key, item in found.items()} if seen else None


def _sorted(items):
    return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))


def _plain(value, depth=0):
    # the value in a canonical structural form, never its repr: a tuple as a list; any mapping as its items, sorted;
    # a set sorted; another object as its type's qualified name and its attributes (__dict__, __slots__), so an
    # insertion order, a hash order or a changed repr is no difference; one with neither by its type and its repr
    if depth > 8:
        return {"deeper": _name(type(value))}
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if value == value and abs(value) != float("inf") else {"float": repr(value)}
    if isinstance(value, type):
        return {"class": _name(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    if isinstance(value, (set, frozenset)):
        return _sorted(_plain(item, depth + 1) for item in value)
    if isinstance(value, collections.abc.Mapping):
        if all(type(key) is str for key in value):
            return {key: _plain(item, depth + 1) for key, item in value.items()}
        return _sorted([_plain(key, depth + 1), _plain(item, depth + 1)] for key, item in value.items())
    found = _attributes(value)
    if found is None:
        return {"type": _name(type(value)), "repr": re.sub(r" at 0x[0-9A-Fa-f]+", "", _shown(value))}
    return {"type": _name(type(value)), "attributes": {key: _plain(item, depth + 1) for key, item in found.items()}}


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


_REPLAY = """

MARK = "G6-OBSERVATION"
_NONE = object()


def _value(term, values):
    # one term of an invariant, as G6 read it (nested lists), evaluated on the values given
    if isinstance(term, (bool, int)):
        return term
    if isinstance(term, str):
        return values[term]
    op, parts = term[0], term[1:]
    if op == "ite":
        return _value(parts[1], values) if _value(parts[0], values) else _value(parts[2], values)
    found = [_value(part, values) for part in parts]
    if op == "and":
        return all(found)
    if op == "or":
        return any(found)
    if op == "not":
        return not found[0]
    if op == "=>":
        result = found[-1]
        for premise in reversed(found[:-1]):
            result = (not premise) or result
        return result
    if op == "xor":
        result = found[0]
        for other in found[1:]:
            result = result != other
        return result
    if op == "=":
        return all(a == b for a, b in zip(found, found[1:]))
    if op == "distinct":
        return all(found[i] != found[j] for i in range(len(found)) for j in range(i + 1, len(found)))
    if op in ("<", "<=", ">", ">="):
        compare = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
                   ">=": lambda a, b: a >= b}[op]
        return all(compare(a, b) for a, b in zip(found, found[1:]))
    if op == "+":
        return sum(found)
    if op == "-":
        return -found[0] if len(found) == 1 else found[0] - sum(found[1:])
    if op == "*":
        result = 1
        for factor in found:
            result *= factor
        return result
    if op in ("div", "mod"):
        a, b = found
        rest = a % abs(b)   # SMT-LIB: a = b * q + r, with 0 <= r < |b|
        return rest if op == "mod" else (a - rest) // b
    raise ValueError(op + " is not an operator the replay evaluates")


def _call(entry, state=_NONE):
    function = _load(entry["call"])
    args, kwargs = copy.deepcopy(entry["args"]), copy.deepcopy(entry["kwargs"])
    return function(*args, **kwargs) if state is _NONE else function(state, *args, **kwargs)


def _read(state, read):
    if read["refusal"] is None:
        return _call(read, state)
    error = _error(read["error"]) if read["refusal"] == "exception" else None
    try:
        result = _call(read, state)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt) or error is None or not isinstance(exc, error):
            raise
        return False
    return True if error is not None else bool(result)


def _not_replayable(why, exc=None):
    return {"outcome": "not_replayable", "why": why + ("" if exc is None else " raised " + _name(type(exc))[:100])}


def _replay(trace):
    try:
        state = _call(BINDINGS["constructor"])
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return _not_replayable("the constructor", exc)
    for step, value in enumerate(trace["actions"]):
        try:
            _call(BINDINGS["actions"][str(value)], state)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            return _not_replayable("step %d (action %d)" % (step + 1, value), exc)
    values = dict(trace["ghost"])
    for name, read in BINDINGS["observe"].items():
        try:
            got = _read(state, read)
        except BaseException as exc:
            if isinstance(exc, KeyboardInterrupt):
                raise
            return _not_replayable("reading " + name, exc)
        if type(got) not in (bool, int) or (type(got) is bool) != (BINDINGS["sorts"][name] == "Bool"):
            return _not_replayable("reading %s gave a %s, not a %s" % (name, _name(type(got))[:60],
                                                                         BINDINGS["sorts"][name]))
        values[name] = got
    try:
        broken = [name for name, term in BINDINGS["invariants"].items() if not _value(term, values)]
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return _not_replayable("evaluating the invariants on the values read", exc)
    observed = {name: values[name] for name in BINDINGS["observe"]}
    return {"outcome": "broken" if broken else "holds", "broken": broken, "observed": observed}


def test_replay(capsys):
    # each trace from a fresh initial state: the operation calls in order, then the invariants on the real state
    outcomes = []
    for index, trace in enumerate(BINDINGS["traces"]):
        outcome = _replay(trace)
        outcomes.append(outcome)
        line = json.dumps({"input": index, "output": outcome}, sort_keys=True)
        with capsys.disabled():
            print(MARK + " " + line, flush=True)
    broken = [str(index) for index, outcome in enumerate(outcomes) if outcome["outcome"] == "broken"]
    if broken:
        pytest.fail("trace(s) " + ", ".join(broken) + " break the invariant on the real state", pytrace=False)
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
    #: Whether its check is compiled from what a model checker finds (``g6.harness.verification_formal``), not from the
    #: instance's bindings alone.
    formal: bool = False

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


_CALLED = ("the names resolve to the code the runs import: a module in an import root, the top folder or src, and a top-level name "
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
         "the relation is checked on the inputs given; for a function of whole numbers and booleans in Z3Verifier's "
         "subset, Z3 also searches every whole number from the smallest input to the largest when that span holds "
         "at most 10,000 of them (else the inputs given), and each input it finds is re-run on the real code "
         "before it is reported"),
        _relation, _text("relation.v1", _RELATION)),
    TemplateDefinition(
        "access_rule.v1", REQUIREMENT_SATISFACTION,
        "who may perform which operation on which resource: allowed and refused cases, each with its expected result",
        _AccessForms,
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
         "outputs are compared in a canonical structural form: a mapping as its items sorted, a set sorted, another "
         "object as its type's qualified name and its attributes (one with none by its type and its repr, without "
         "memory addresses); a long output by its SHA-256; an input the base commit observes differently on two "
         "runs is unstable, and never a difference",
         "a difference is a counterexample when the instance's intent is preserve, and a question when it is change; "
         "a difference Z3 finds at a value the caller did not declare is a question"),
        _differential, _text("differential.v1", _DIFFERENTIAL)),
    TemplateDefinition(
        "forbidden_dependency.v1", SAFETY_STRUCTURAL, "the patch adds no import that breaks a declared layer rule",
        ForbiddenBindings,
        ("the imports are read from the package's files, before and after, and no code runs",
         "imports between modules of the package count, and chains of them, as import-linter follows them: dynamic "
         "imports (importlib, __import__) are not seen, and modules outside the package are out of scope",
         "the repository's own contract is the one its import-linter configuration declares at the base commit"),
        None, "", ("preserve",)),
)}
#: The templates whose check G6 compiles from what a model checker finds on a model the caller states: kept apart from
#: ``TEMPLATES``, whose checks compile from an instance's bindings alone, because their results come in three parts.
FORMAL_TEMPLATES: dict[str, TemplateDefinition] = {definition.id: definition for definition in (
    TemplateDefinition(
        "state_invariant.v1", SAFETY_STRUCTURAL,
        "an invariant over sequences of the named operations holds on the real state after each trace a model checker "
        "finds",
        StateInvariantBindings,
        ("the model is the caller's abstraction of the operations: a trace it allows that the code does not follow, "
         "or along which the code keeps the invariant, is about the model only (model_only), and nobody is accused",
         "a trace is replayed from a fresh initial state: the constructor, then each operation called with the state "
         "first and its literal arguments, then each observed variable read and the invariants evaluated on those "
         "values, with the model's own values for the variables the code does not show",
         "a bounded search covers paths of at most depth steps, and G6 replays at most the instance's number of traces; "
         "k-induction and a bounded 'none found' are results about the model only"),
        _state_invariant, _text("state_invariant.v1", _REPLAY), formal=True),
)}
#: Every template a review takes, by id: a view of both registries.
REGISTRY = collections.ChainMap(TEMPLATES, FORMAL_TEMPLATES)


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
        definition = REGISTRY.get(one.template)
        if definition is None:
            raise ValueError(f"{where}: template must be one of {', '.join(sorted(REGISTRY))}")
        if vs.reserved_id(one.id):
            raise ValueError(f"{where}: {vs.RESERVED_ID}")
        if one.intent not in definition.intents:
            raise ValueError(f"{where}: {one.template} checks that behaviour is kept: its intent is preserve")
        problem = literal_problem(one.bindings)
        if problem:
            raise ValueError(f"{where}: {problem}")
        try:
            definition.bindings.model_validate(one.bindings)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"])
            raise ValueError(f"{where}: bindings{'.' + loc if loc else ''}: {first['msg']}") from exc
        if one.requirement_id not in requirement_ids:
            raise ValueError(f"{where}: requirement_id {one.requirement_id!r} names no requirement this review "
                             "assesses")
        found.append(one)
    if len({one.id for one in found}) != len(found):
        raise ValueError("template instance ids must be unique")
    return found


def instance_digest(one: TemplateInstance) -> str:
    """The SHA-256 that cites an instance: its template's id and version, the digest of the template's fixed text, and
    the instance's requirement, intent, bindings and their provenance, as canonical JSON; for access_rule.v1's
    multi-step form, the template it goes through and the digest of that template's fixed text too."""
    definition = REGISTRY[one.template]
    body = {"template": one.template, "template_text_sha256": definition.text_sha256, "id": one.id,
            "requirement_id": one.requirement_id, "intent": one.intent, "bindings": one.bindings,
            "provenance": one.provenance}
    if multi_step(one):
        through = FORMAL_TEMPLATES["state_invariant.v1"]
        body["through"] = {"template": through.id, "template_text_sha256": through.text_sha256}
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
    #: Each formal instance that applies, for the formal path (``g6.harness.verification_formal``): the instance, its
    #: bindings, what applying it gave (the literal still without traces), and the root its check is placed in.
    formal: list[dict] = field(default_factory=list)
    #: Each relation.v1 and differential.v1 instance that compiled, for the formal path's symbolic search; and the
    #: tree the names were read from.
    symbolic: list[dict] = field(default_factory=list)
    tree: Tree | None = None


def _listing(one: TemplateInstance) -> dict:
    definition = REGISTRY[one.template]
    return {"activity": "template instance", "check": one.id, "template": one.template, "family": definition.family,
            "requirement": one.requirement_id, "intent": one.intent, "instance_sha256": instance_digest(one),
            "bindings_provenance": one.provenance, "seconds": 0.0, "model_calls": 0}


def compiled_check(root: Path, one: TemplateInstance, data: dict, notes: list[str], *, ident: str = "",
                   definition: TemplateDefinition | None = None,
                   **origin: Any) -> tuple[vs.ProposedCheck, str, vs.Origin]:
    """``one``'s template's fixed text (or ``definition``'s, the template it goes through) compiled for ``data``
    (``compile_text``), as a check placed in the copies, and the origin its activity, reasons and reproduction name.
    ``ident``: the check's id when it is not the instance's (a further check of one instance); ``origin``: further
    fields of its ``Origin``."""
    definition = definition or REGISTRY[one.template]
    source = compile_text(definition, data)
    if len(source.encode("utf-8")) > vs.MAX_SOURCE_BYTES:
        raise Inapplicable("its compiled check would hold more than 64 kilobytes")
    check = vs.ProposedCheck(id=ident or one.id, requirement_id=one.requirement_id, intent=one.intent,
                             file=f"test_g6_{definition.id.split('.')[0]}.py", source=source)
    try:
        placed = vs.placements(root, [check])[check.id]
    except ValueError as exc:
        raise Inapplicable(f"G6 could not place its compiled check: {exc}") from exc
    about = {key: value for key, value in _listing(one).items() if key in (
        "template", "family", "instance_sha256", "bindings_provenance")}
    about["notes"] = notes
    fields = {"activity": "template instance", "proposed_by": COMPILED_BY, "about": about,
              "label": f"an instance of {one.template} G6 compiled",
              "unconfirmed": "G6 compiled it from the caller's bindings, and a compiled check decides no requirement",
              "caution": (f"G6 generated the check from the fixed text of template {one.template}, with the caller's "
                          "bindings written in it as one literal; it imports and runs the repository's code, so G6 ran "
                          "it only inside its operating-system boundary, with no network. Read it before you run it, "
                          "or run it in a container."),
              "template": one.template,
              "refuted_by_difference": one.template == "differential.v1" and one.intent == "preserve", **origin}
    return check, placed, vs.Origin(**fields)


def prepare(root: Path, change: repo_tests.Change, instances: list[TemplateInstance], *,
            enabled: bool = True, deadline: float | None = None, roots: tuple[str, ...] = ()) -> Prepared:
    """Each instance checked against the tree and, where it applies, compiled and placed (or, for the structural
    template, checked): reading files only (the review calls it after tier 1, so it never takes tier 1's time), and
    the structural check no further than ``deadline`` (the review's, on ``_clock``); names resolve in the review's
    import ``roots`` first, as the runs import. ``enabled`` false (``search.templates``): none is run, each listed as
    skipped. An instance the tree gives nothing to apply to is refused; one G6's own reading raises on (a file nested
    too deeply for Python's parser: a RecursionError or a MemoryError) is an activity's error, never the review's."""
    found = Prepared(instances=len(instances))
    if not instances:
        return found
    if not enabled:
        found.activities = [{**_listing(one), "result": "skipped", "reason": TEMPLATES_OFF} for one in instances]
        return found
    tree = found.tree = Tree.read(root, change, deadline, roots)
    for one in instances:
        definition = REGISTRY[one.template]
        try:
            if definition.formal or multi_step(one):   # its check is compiled from what a model checker finds
                through = FORMAL_TEMPLATES["state_invariant.v1"] if multi_step(one) else definition
                bindings = through.bindings.model_validate(
                    access_model(AccessStepsBindings.model_validate(one.bindings)) if multi_step(one) else
                    one.bindings)
                found.formal.append({"instance": one, "bindings": bindings, "root": root, "through": through,
                                     "applied": through.applies(tree, bindings, _side(one))})
            elif definition.text:
                bindings = definition.bindings.model_validate(one.bindings)
                applied = definition.applies(tree, bindings, _side(one))
                check, placed, origin = compiled_check(root, one, applied.data, applied.notes)
                found.checks.append(check)
                found.placed[one.id] = placed
                found.origins[one.id] = origin
                if one.template in ("relation.v1", "differential.v1"):   # the formal path may search it too
                    found.symbolic.append({"instance": one, "bindings": bindings, "data": applied.data,
                                           "notes": applied.notes, "root": root})
            else:
                result = structural(tree, one)
                found.structural.append(result)
                found.activities.append(result["activity"])
        except (component_process.ExecutionInterrupted, component_process.ContainmentFailure):
            raise   # an operator stop or unconfirmed process cleanup must reach the enclosing door
        except (Inapplicable, ValueError, OSError, SyntaxError, repo_tests.RepoRefused) as exc:
            why = str(exc) if isinstance(exc, Inapplicable) else (
                f"G6 could not read what it names ({type(exc).__name__})")
            _not_applied(found, one, definition, {**_listing(one), "result": "refused", "reason": why})
        except Exception as exc:   # RecursionError, MemoryError, any other: an activity's error (the VS-3 review, I2)
            _not_applied(found, one, definition, {**_listing(one), "result": "error", "reason": vs.stopped(exc),
                                                  "stopped": f"template instance {one.id}"})
    return found


def _not_applied(found: Prepared, one: TemplateInstance, definition: TemplateDefinition, activity: dict) -> None:
    """``one`` refused, or stopped on an error, as its activity says; a structural instance's check did not run."""
    found.activities.append(activity)
    if not definition.text and not definition.formal:
        provenance = _contract_provenance(one)
        found.structural.append({"id": one.id, "requirement": one.requirement_id, "verdict": "not_run",
                                 "normative": _normative(one),
                                 "contract_provenance": provenance, "activity": activity, "violations": []})


# --- forbidden_dependency.v1: imports read before and after, against a declared contract ----------------------------------

@dataclass(frozen=True)
class _Contract:
    name: str
    package: str
    layers: tuple[str, ...]
    forbidden: tuple[tuple[str, str], ...]
    ignore: tuple[str, ...] = ()
    #: A forbidden contract whose ``allow_indirect_imports`` is true: its direct imports only. Every other contract
    #: follows import chains inside the package, as import-linter does (review C3).
    direct: bool = False
    #: The configuration's ``exclude_type_checking_imports``: imports under ``if TYPE_CHECKING`` are left out.
    no_type_checking: bool = False


_CONFIGS = (".importlinter", "setup.cfg", "pyproject.toml")
_LIST_KEYS = frozenset({"layers", "containers", "source_modules", "forbidden_modules", "ignore_imports",
                        "root_packages", "modules"})
_KIND = re.compile(r"[a-z_]{1,40}")
_IGNORE = re.compile(r"\s*([A-Za-z0-9_.*]+)\s*->\s*([A-Za-z0-9_.*]+)\s*")
#: What G6 implements of an import-linter configuration (review I1): the top-level options; each contract type's
#: options besides ``type``, ``name`` and ``id`` (``unmatched_ignore_imports_alerting`` only says how an ignored import
#: that matches nothing is reported, and G6 never reports one; ``broken_contract_guidance`` is text for a person); and
#: the options it reads at one value only. Any other option, value, type or layer syntax makes the instance not
#: applicable, with the option named: G6 never guesses what one means.
_TOP_OPTIONS = frozenset({"root_package", "root_packages", "exclude_type_checking_imports", "show_timings",
                          "contracts"})
_CONTRACT_OPTIONS = {
    "layers": frozenset({"layers", "containers", "ignore_imports", "unmatched_ignore_imports_alerting",
                         "broken_contract_guidance", "exhaustive", "exhaustive_ignores"}),
    "forbidden": frozenset({"source_modules", "forbidden_modules", "ignore_imports", "allow_indirect_imports",
                            "unmatched_ignore_imports_alerting", "broken_contract_guidance", "as_packages"})}
_FLAGS = frozenset({"exclude_type_checking_imports", "allow_indirect_imports"})
_ONLY = {"include_external_packages": False, "exhaustive": False, "as_packages": True}


def _flag(value) -> bool | None:
    """An import-linter boolean option's value (``True``, ``true``, ``False``, ``false``, or TOML's own), None for any
    other."""
    if isinstance(value, bool):
        return value
    text = value.strip() if isinstance(value, str) else ""
    return {"True": True, "true": True, "False": False, "false": False}.get(text)


def _named(key) -> str:
    """An option's name as a reason shows it: the repository's text, cut short, with nothing but letters, digits, ``_``,
    ``.`` and ``-`` kept."""
    return re.sub(r"[^A-Za-z0-9_.-]", "?", str(key))[:60]


def _unsupported(options: dict, raw: list[dict]) -> list[str]:
    """What an import-linter configuration sets that G6 does not implement (``_TOP_OPTIONS``, ``_CONTRACT_OPTIONS``)."""
    found = []

    def judge(label: str, key, value, known: frozenset) -> None:
        if key in _ONLY:
            if _flag(value) is not _ONLY[key]:
                found.append(f"{label}{key} at a value other than {_ONLY[key]}")
        elif key in _FLAGS and key in known:
            if _flag(value) is None:
                found.append(f"{label}{key} at a value G6 cannot read")
        elif key not in known:
            found.append(f"{label}{_named(key)}")

    for key, value in options.items():
        judge("", key, value, _TOP_OPTIONS | {"include_external_packages"})
    for index, item in enumerate(raw):
        label = f"contract {index + 1}: "
        kind = item.get("type")
        if kind not in _CONTRACT_OPTIONS:
            found.append(label + (f"a contract of type {kind}" if isinstance(kind, str) and _KIND.fullmatch(kind)
                                  else "a contract of a type G6 cannot read"))
            continue
        for key, value in item.items():
            if key not in ("type", "name", "id"):
                judge(label, key, value, _CONTRACT_OPTIONS[kind])
        if kind == "layers" and any("|" in layer or ":" in layer for layer in _lines(item.get("layers"))):
            found.append(f"{label}independent or non-independent sibling layers (a | b, a : b)")
    return found


def _contract_provenance(one: TemplateInstance) -> str:
    contract = one.bindings.get("contract") if isinstance(one.bindings, dict) else None
    return "repository_stated" if contract == "repository" else one.provenance


def _normative(one: TemplateInstance) -> bool:
    """Whether ``one``'s result decides its requirement: never for an instance a model proposed
    (``model_hypothesis``), whatever contract its bindings name, the repository's included (reviewer B's B1); its
    failure is an unconfirmed finding, so at most insufficient evidence."""
    return one.provenance != "model_hypothesis" and _contract_provenance(one) != "model_hypothesis"


def _lines(value) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip() and not line.strip().startswith("#")]
    return []


def _ini(text: str) -> tuple[list[str], list[dict], dict] | None:
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
    return roots, contracts, dict(main.items())


def _toml(text: str) -> tuple[list[str], list[dict], dict] | None:
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
    return (roots, [item for item in contracts if isinstance(item, dict)] if isinstance(contracts, list) else [],
            dict(section))


def _declared(tree: Tree, side: str) -> tuple[str, tuple[list[str], list[dict], dict]] | None:
    """The first import-linter configuration on ``side``, and what it holds: the root packages, the contracts, and
    the top-level options."""
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


def _read_contracts(roots: list[str], raw: list[dict], options: dict) -> tuple[list[_Contract], list[str]]:
    """The layers and forbidden contracts of an import-linter configuration whose every option G6 implements
    (``_unsupported`` is empty), each with its package; and what is out of scope, said in G6's words (a name that is not
    a dotted name, a contract outside the root package)."""
    found, out = [], []
    roots = [root for root in roots if _dotted_ok(root)]
    no_type_checking = bool(_flag(options.get("exclude_type_checking_imports", False)))
    for index, item in enumerate(raw):
        label = f"contract {index + 1}"
        kind = item.get("type")
        ignore = tuple(_lines(item.get("ignore_imports")))
        direct = kind == "forbidden" and bool(_flag(item.get("allow_indirect_imports", False)))
        if kind == "layers":
            layers = [layer[1:-1].strip() if layer.startswith("(") and layer.endswith(")") else layer
                      for layer in _lines(item.get("layers"))]
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
            found.append(_Contract(label, package, stack, pairs, ignore, direct=direct,
                                   no_type_checking=no_type_checking))
    return found, out


def _package_files(tree: Tree, package: str, side: str) -> tuple[dict[str, str], str] | None:
    """The package's Python files on ``side``, by path relative to the folder that holds it (as design_governance reads
    them), and that folder's prefix (an import root's, "" or "src/"); None when it has none there. The same rule on
    both sides: a package that more than one of those folders holds is an ``Inapplicable``, never a silent choice."""
    from g6.code.design_governance import graph

    stem = package.replace(".", "/")
    listed = tree.at_base if side == BASE else tree.known
    held = []
    for folder in dict.fromkeys(tree.folders()):
        names = sorted(rel for rel in listed if rel.endswith(".py") and (
            rel.startswith(folder + stem + "/") or rel == folder + stem + ".py")
            and graph.module_of(rel[len(folder):]) is not None)
        if names:
            held.append((folder, names))
    if not held:
        return None
    if len(held) > 1:
        raise Inapplicable(f"the package {package} is in more than one folder {WHERE[side]} ("
                           + ", ".join(f"{folder}{stem}/" for folder, _ in held) + "), so G6 cannot say which one "
                           "the imports name")
    [(folder, names)] = held
    if len(names) > MAX_PACKAGE_FILES:
        raise Inapplicable(f"{package} holds more than {MAX_PACKAGE_FILES} files {WHERE[side]}")
    files, size = {}, 0
    if side == BASE:
        tree.preload(names)
    for rel in names:
        tree.in_time()
        files[rel[len(folder):]] = text = tree.text(rel, side)
        size += len(text)
        if size > MAX_PACKAGE_CHARS:
            raise Inapplicable(f"{package} holds more than {MAX_PACKAGE_CHARS} characters {WHERE[side]}")
    return files, folder


def _matches(pattern: str, name: str) -> bool:
    """Whether a module's dotted ``name`` matches an ``ignore_imports`` pattern, part by part: ``*`` is one part,
    ``**`` one or more, any other part itself. The parts of ``name`` a prefix of the pattern can reach are kept as a
    set, so it takes at most as many steps as the pattern's parts times the name's, never a regular expression's
    backtracking (reviewer B's B5: a rule of many ``**`` held G6's process for seconds, past every deadline)."""
    parts = name.split(".")
    reached = {0}   # how many of the name's parts the pattern so far has matched
    for part in pattern.split("."):
        if part == "**":
            reached = set(range(min(reached) + 1, len(parts) + 1)) if reached else set()
        else:
            reached = {at + 1 for at in reached if at < len(parts) and part in ("*", parts[at])}
        if not reached:
            return False
    return len(parts) in reached


def _ignored(violation: dict, ignore: tuple[str, ...]) -> bool:
    for rule in ignore:
        found = _IGNORE.fullmatch(rule)
        if found and _matches(found[1], violation["from"]) and _matches(found[2], violation["to"]):
            return True
    return False


def _matched(found, contract: _Contract) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...], set[str]]:
    """The contract's layers and forbidden pairs that match a module of the graph ``found``, and the prefixes that
    match none (left out)."""
    def rank(module: str) -> int | None:
        hits = [i for i, prefix in enumerate(contract.layers) if _in(module, prefix)]
        return max(hits, key=lambda i: len(contract.layers[i])) if hits else None

    assigned = {rank(module) for module in found.modules}
    layers = tuple(layer for i, layer in enumerate(contract.layers) if i in assigned)
    matched = lambda prefix: any(_in(module, prefix) for module in found.modules)   # noqa: E731
    pairs = tuple(pair for pair in contract.forbidden if matched(pair[0]) and matched(pair[1]))
    missing = {layer for layer in contract.layers if layer not in layers} | {
        prefix for pair in contract.forbidden if pair not in pairs for prefix in pair if not matched(prefix)}
    return layers, pairs, missing


def _counted(found, contract: _Contract) -> dict[str, list]:
    """The package's imports the contract counts, by importing module, each module's in target and line order: those
    under ``if TYPE_CHECKING`` left out when the configuration says so, and those its ``ignore_imports`` names
    removed, as import-linter removes them before it looks for chains."""
    out: dict[str, list] = {}
    for edge in sorted(found.edges, key=lambda e: (e.source, e.target, e.line)):
        if not (contract.no_type_checking and edge.type_checking) and not _ignored(
                {"from": edge.source, "to": edge.target}, contract.ignore):
            out.setdefault(edge.source, []).append(edge)
    return out


def _reached(edges: dict[str, list], start: str, stop: Callable[[str], bool], through: Callable[[str], bool]) -> dict:
    """The modules ``stop`` holds for that ``start`` reaches, each with the imports of the shortest chain to it (a
    breadth-first search, in a fixed order), passing through modules ``through`` holds for only."""
    parent: dict[str, Any] = {start: None}
    queue, reached = collections.deque([start]), {}
    while queue:
        node = queue.popleft()
        for edge in edges.get(node, ()):
            if edge.target in parent:
                continue
            parent[edge.target] = edge
            if stop(edge.target):
                chain, step = [], edge
                while step is not None:
                    chain.append(step)
                    step = parent[step.source]
                reached[edge.target] = chain[::-1]
            elif through(edge.target):
                queue.append(edge.target)
    return reached


def _breaches(found, contract: _Contract, in_time: Callable[[], None] = lambda: None) -> tuple[list[dict], set[str]]:
    """The contract's breaches in the graph ``found``, with what no module of it is in left out; and the prefixes it
    left out. Import chains are followed inside the package as import-linter follows them (review C3): a module of a
    lower layer that reaches a module of a higher one, directly or through modules in no layer (a chain between two
    layers passes through no other); a module of a pair's source that reaches one of its forbidden prefix through
    modules in neither, or directly only when the contract allows indirect imports. Each breach names its chain, and
    the file and line of the chain's first import. ``in_time`` raises once the review's time is spent."""
    layers, pairs, missing = _matched(found, contract)
    edges = _counted(found, contract)

    def rank(module: str) -> int | None:
        hits = [i for i, prefix in enumerate(layers) if _in(module, prefix)]
        return max(hits, key=lambda i: len(layers[i])) if hits else None

    def breach(rule: str, chain: list, why: str) -> dict:
        middle = [edge.source for edge in chain[1:]]
        return {"rule": rule, "from": chain[0].source, "to": chain[-1].target, "file": chain[0].file,
                "line": chain[0].line, "lazy": chain[0].lazy, "chain": [chain[0].source, *(e.target for e in chain)],
                "why": why + (f", even through {', '.join(middle)}" if middle else "")}

    out = []
    ranks = {module: rank(module) for module in found.modules}
    for module, own in sorted(ranks.items()) if len(layers) > 1 else ():
        if own is None:
            continue
        in_time()
        for target, chain in _reached(edges, module, lambda m: ranks.get(m) is not None,
                                      lambda m: ranks.get(m) is None).items():
            if ranks[target] < own:
                out.append(breach("layers", chain, f"{layers[own]} is below {layers[ranks[target]]}, so it may not "
                                                   "import it"))
    for source, banned in pairs:
        for module in sorted(m for m in found.modules if _in(m, source) and not _in(m, banned)):
            in_time()
            through = (lambda m: False) if contract.direct else (   # noqa: E731
                lambda m, source=source, banned=banned: not _in(m, source) and not _in(m, banned))
            for chain in _reached(edges, module, lambda m, banned=banned: _in(m, banned), through).values():
                out.append(breach("forbidden", chain, f"{source} may not import {banned}"))
    return out, missing


def _unexercised(before, after, contract: _Contract) -> str:
    """Why the patch could not have broken ``contract`` (the owner's ruling R-C1: such a contract is reported as
    vacuous, "does not exercise the change"), or "" when it could: the contract, as G6 can check it on the package,
    has two layers or a forbidden pair, and the patch adds an import inside the package that a chain can pass through
    (any, for a contract that follows chains; for one of direct imports only, one from a module of a pair's source),
    one the contract's ignored imports allow included. A change that leaves every module's imports as they were is not
    one a structural check sees; only an added import can break a contract."""
    layers, pairs, _ = _matched(after, contract)
    if len(layers) < 2 and not pairs:
        return ("a contract of fewer than two layers and no forbidden pair, as G6 can check it on the package, can "
                "never be broken")
    def edges(found) -> set[tuple[str, str]]:   # noqa: E306
        return {(edge.source, edge.target) for edge in found.edges} if found is not None else set()

    added = edges(after) - edges(before)
    if contract.direct:
        added = {(source, target) for source, target in added if any(_in(source, pair[0]) for pair in pairs)}
    if not added:
        return "the patch adds no import inside the package that could break the contract"
    return ""


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
        rel, (roots, raw, options) = declared
        unsupported = _unsupported(options, raw)
        if unsupported:   # review I1: never a guess at what an option means
            raise Inapplicable(f"the repository's import-linter configuration ({rel}) sets "
                               + "; ".join(unsupported[:5]) + (f" and {len(unsupported) - 5} more" if
                                                               len(unsupported) > 5 else "")
                               + ", which G6 does not implement, so it does not apply that contract")
        contracts, out = _read_contracts(roots, raw, options)
        source = f"{rel} at the base commit"
        if rel in tree.change.files:
            limits.append(f"the patch changes {rel}; G6 used the contract as the base commit declares it")
        if any(item.get("ignore_imports") for item in raw):
            limits.append("an ignored import that matches no import is not reported (import-linter reports one by "
                          "default)")
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
        kept = dataclasses.replace(contract, layers=tuple(layer for layer in contract.layers if layer not in outside),
                                   forbidden=tuple(pair for pair in contract.forbidden if pair[0] not in outside
                                                   and pair[1] not in outside))
        if kept.layers or kept.forbidden:
            scoped.append(kept)
    if not scoped:
        raise Inapplicable("no contract G6 can read applies to the package: " + "; ".join(out[:5]) if out else
                           "the contract names nothing G6 can check")
    graphs: dict[tuple[str, str], tuple] = {}
    new, already, unparsed, checked, vacuous = [], [], [], 0, []
    for contract in scoped:
        for side in (BASE, PATCH):
            if (contract.package, side) not in graphs:
                tree.in_time()
                files = _package_files(tree, contract.package, side)
                tree.in_time()
                graphs[contract.package, side] = (graph.build(files[0], contract.package, before_each=tree.in_time),
                                                  files[1]) if files else (None, "")
        (before, _), (after, folder) = graphs[contract.package, BASE], graphs[contract.package, PATCH]
        if after is None or not after.modules:
            out.append(f"{contract.name}: the package {contract.package} has no module {WHERE[PATCH]}")
            continue
        named = set(contract.layers) | {prefix for pair in contract.forbidden for prefix in pair}
        old, missing_before = _breaches(before, contract, tree.in_time) if before is not None else ([], named)
        found, missing_after = _breaches(after, contract, tree.in_time)
        out += [f"{contract.name}: {prefix} matches no module of the package, so it is out of scope"
                for prefix in sorted(missing_before & missing_after)]
        if named <= missing_before & missing_after:
            continue
        checked += 1
        why = _unexercised(before, after, contract)
        if why:
            vacuous.append(f"{contract.name}: {why}")
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
        violations.append({key: item[key] for key in ("rule", "from", "to", "file", "line", "lazy", "why", "chain")}
                          | {"text": text})
    normative = _normative(one)
    if violations:
        verdict = "failed" if normative else "counterexample"
    elif unparsed:
        verdict = "not_run"
        limits.append(f"{len(unparsed)} file(s) of the package do not parse {WHERE[PATCH]}, so their imports are "
                      f"not seen: {', '.join(unparsed[:5])}")
    elif len(vacuous) == checked:   # no contract the patch could have broken: it does not exercise the change
        verdict = VACUOUS
    else:
        verdict = "passed" if normative else "not_refuted"
    if vacuous and verdict != VACUOUS:
        limits += [f"{item}; it does not exercise the change" for item in vacuous]
    shown = "; ".join(f"{v['from']} imports {v['to']} ({v['file']}:{v['line']})" for v in violations[:5])
    detail = (f"{one.id}: the patch adds {len(violations)} import(s) that {source} forbids: {shown}" if violations
              else f"{one.id}: no import the patch adds breaks {source}")
    activity = {**_listing(one), "provenance": "structural", "contract_provenance": provenance,
                "contract_source": source, "result": verdict if verdict != "not_run" else "not_established",
                "violations": violations, "already_at_base": [{key: item[key] for key in ("from", "to", "file",
                                                                                          "line")}
                                                              for item in already],
                "out_of_scope": list(dict.fromkeys(out)), "limits": list(dict.fromkeys(limits)), "ran_code": False,
                **({"note": "does not exercise the change: " + "; ".join(vacuous)} if verdict == VACUOUS else {})}
    reproduction = {"kind": "static", "imports": violations, "contract": source, "contract_provenance": provenance,
                    "how": "read each file at its line on the working tree: the import is there, and the base "
                           "commit's tree has no import between those modules; nothing needs to run",
                    **({"command": "lint-imports"} if provenance == "repository_stated" else {})}
    return {"id": one.id, "requirement": one.requirement_id, "verdict": verdict, "normative": normative,
            "contract_provenance": provenance, "contract_source": source, "detail": detail, "activity": activity,
            "violations": violations, "reproduction": reproduction}
