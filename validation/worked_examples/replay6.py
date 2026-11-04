# SPDX-License-Identifier: Apache-2.0
"""Replay 6 (PLAN.md, 2026-11-04, evening): a formal model of the rule the issue states, checked by G6 on every case of a finite
domain, then run on replay 3's machinery. It changes no run, no verdict and no rule.

Why: the generation bugs' runs fixed the issue's example and left the rest of a stated rule to the old code, so the cases a test
happens to sample miss them. A model gives every case of its domain a value, and G6 runs every case.

Reader 1 (Codex, asked as replay 3 asks its reviewer, through ``final_review._step``) reads the issue and the parent and writes
the model, one JSON document where replay 3's reply carries its test: named factors (their product the cases, at most
``MAX_CASES``), named observations, a probe whose ``observe(case)`` calls the code, and a rule table, each row quoting the issue.
Reader 2 (Claude, ``g6.core.host_models.ClaudeCodeCaller``, no tool) is given the issue, the factors, the observations and the
probe with no comment or docstring and no literal that tells one of reader 1's expectations (``_shown``, the masks counted),
never reader 1's rules, the parent or any output of the code, and writes its own table. G6's checks are plain code here,
before anything runs: the model's form, rows whose quote is not the issue's own words dropped, a table that gives one case two
values for one observation refused, and the two tables compared on every case both cover, where any difference leaves the bug
ambiguous and nothing is flagged on it. Every comparison of two values, a table's with a factor's, the two tables', and the
compiled file's of an observation with its expectation, is by one rule (``canon``), and every value the probe is given is the one
the tables read (``_loads``, ``_exact``). Where the model stands, the agreed cells are compiled to one pytest file (G6's own
text, the probe as one text, run in a namespace and a process group of its own that is given the cases and never an
expectation, its observations JSON by their types alone and read by the model's own rule, the cells as JSON data) and replay
3's ``run_on`` runs it on the parent and on each patch, each run in a container of its own, so replay 3's ``verdict`` gives the
parent gate, by case. Each bug's record holds its status (``STATUSES``), the counts, both tables and the compiler that made it
(``COMPILER``): a record that stands, made by another, is compiled again from its answers with no reader asked (``again``).
"""
from __future__ import annotations

import argparse
import ast
import functools
import hashlib
import inspect
import io
import itertools
import json
import math
import re
import subprocess
import tokenize
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import final_review as fr
import replay3 as r3
import run as we
import wecommon as c
from g6.core.host_models import HOOKS_OFF, HOST_FAILED, ClaudeCodeCaller, _result
from g6.core.llm import LLMError

#: The most cases a model's domain may have: the product of its factors' sizes.
MAX_CASES = 512
#: Reader 2's model, and the two settings it is given in its settings file: its environment never carries a ``CLAUDE_CODE_*``
#: name (``host_models._environment``), so they go where a settings file's ``env`` sets them.
READER_2_MODEL = "claude-opus-5-5"
READER_2_ENV = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1"}
#: What a bug's record says of its model. ``no_answer`` is never kept as a bug's record, as replay 3 asks again on a no answer:
#: each attempt that ends in it is kept in a record of its own beside it (``no_answers``), which ``summary`` counts.
STATUSES = ("stands", "ambiguous", "conflict", "no_model", "no_answer", "invalid", "no_agreed_case")


def _swap(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"replay 3's instruction no longer holds {old[:40]!r} once")
    return text.replace(old, new)


#: The task, in general words only: no bug, file or issue of any stage. Its limits and its follow-through are replay 3's text, and
#: so is its reply's form, with what goes in the fence and what an expectation rests on changed for a model.
FORMAL_FIRST = """<task>
You are writing a formal model of the rule a reported bug breaks, before anyone shows you a fix. Your cwd has:
- issue.txt: the issue and its discussion.
- parent/: the repository just before the fix.

No fix is in your cwd, and none is described to you. A fix that makes only the issue's own example pass is not a fix: the same
defect usually shows on other inputs of the same kind. G6 runs every case of your model's domain, so model the domain the rule
covers, not only its example.

1. Name the rule the issue states: not only its example, but what it says must hold for every input of the same kind. Give it on
   the RULE line, in one or two sentences.
2. Model the rule's input domain as a few named factors, each a short list of distinct JSON values (strings, numbers, booleans,
   null, or lists of these): the issue's own example, other values of the same kind, the edges of the input's range, the sibling
   paths or options the rule covers. A case is one value of each factor, and every combination is a case: the product of the
   factors' sizes is at most 512.
3. Name the observations the rule speaks of, each a Python identifier.
4. Write the probe: Python source that defines observe(case: dict) -> dict. It is given a case, a dict from each factor's name to
   one of its values; it calls the code under test through its public interface and returns a dict from each observation's name
   to a JSON value. Catch any exception around each call and give its type's name, as a string, as that observation's value, so
   that observe never raises. The probe imports only the project and the standard library.
5. Write rules only where the issue's words state the outcome. Each rule has "when": for some factors, the one value or the list
   of values it covers (a factor it leaves out covers all its values); "expect": for some observations, the JSON value the issue
   states, or {"equals": "<another observation>"} where the issue states that the two agree; and "quote": the issue's own words
   the rule rests on, copied exactly from issue.txt. Two rules that cover one case must not expect different values for one
   observation. Write no rule whose outcome the issue does not state: a wrong rule is worse than none. If the issue states no
   rule over such a domain, say so and write no model. That is a valid and useful answer.
</task>

""" + functools.reduce(lambda text, swap: _swap(text, *swap), (
    ("<a complete pytest file, or nothing if VERDICT is none>", "<the model, one JSON object, or nothing if VERDICT is none>"),
    ("VERDICT is defect if you wrote a test of the rule, and none if you wrote no test. Name each test test_<something>. The test "
     "file must\nimport only the project and the standard library, and pytest if needed.",
     "VERDICT is defect if you wrote a model, and none if you wrote none. The model, in the fence above, is one JSON object:\n"
     '{"factors": {"<factor>": [<value>, ...]}, "observations": ["<observation>", ...], "probe": "<the probe\'s Python source>",\n'
     ' "rules": [{"when": {"<factor>": <value or [values]>}, "expect": {"<observation>": <value or {"equals": "<observation>"}>},\n'
     '            "quote": "<the issue\'s own words>"}]}'),
    ("Every expectation must point at the issue or at a file and line in parent/. If you are unsure of an expected value, drop that "
     "test.", "Every expectation must rest on the issue's own words, quoted in its rule. If you are unsure of an expected value, "
              "drop that rule."),
), r3.RULE_FIRST[r3.RULE_FIRST.index("<hard_limits>"):])
INSTRUCTION = hashlib.sha256(FORMAL_FIRST.encode("utf-8")).hexdigest()[:16]
def stale(found: Mapping[str, Any]) -> bool:
    """Whether a bug's saved record will be made again before any patch runs (``again``): one that stands, made by another
    compiler than this module's (``COMPILER``). Until it is, no verdict its old test gave speaks for a patch (``replay3.reused``),
    so the scheduler leaves the bug's patches waiting and the counts take none of them."""
    return found.get("status") == "stands" and found.get("compiler") != COMPILER


#: ``same_case``: replay 6's registration states the parent gate by case, a patch flagged when an agreed case that fails on the
#: parent still fails on it. ``own_containers``: the parent's run and the patch's each in a container of its own (PLAN.md,
#: 2026-11-04, night), so nothing the probe leaves in the project folder as one runs reaches the other. ``stale``: a record
#: another compiler made waits to be made again, and its verdicts with it.
FORMAL = r3.Instruction(FORMAL_FIRST, INSTRUCTION, f"final_review_formal-{INSTRUCTION}", f"{we.WE}/formal", "test_formal.py",
                        same_case=True, own_containers=True, stale=stale)
FOLDER = FORMAL.folder
#: The compiler's name (PLAN.md, 2026-11-04, night): a digest of this module's own source text, its line ends read as one,
#: which holds every text the compiled file is made of and every check that decides its cells, so no change to any of them goes
#: unseen. A bug's saved record that stands, made by any other, is compiled again from its answers (``again``).
COMPILER = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]
#: Reader 2's whole system text, in general words only. It is shown the issue, reader 1's factors, observations and probe, and
#: nothing else (``reader_2_text``).
READER_2_SYSTEM = """You are writing, as data, the rule a reported bug breaks, over an input domain another reader has modelled.
You are given the issue and its discussion; the domain's factors, each a list of JSON values, where a case is one value of each
factor and every combination is a case; the names of the observations a probe returns for a case; and the probe's Python source,
its comments and docstrings removed and some literals shown as MASKED.literal, which shows what each observation is. An
observation whose call raised has its exception's type name, as a string. You are shown no other code and no output of any
code.

Write rules only where the issue's words state the outcome. Each rule has "when": for some factors, the one value or the list of
values it covers, each one of that factor's values (a factor it leaves out covers all its values); "expect": for some
observations, the JSON value the issue states, or {"equals": "<another observation>"} where the issue states that the two agree;
and "quote": the issue's own words the rule rests on, copied exactly. Two rules that cover one case must not expect different
values for one observation. Write no rule whose outcome the issue does not state: a wrong rule is worse than none. If the issue
states no outcome over this domain, write no rule.

Reply with one JSON object and nothing else: {"rules": [{"when": {...}, "expect": {...}, "quote": "..."}, ...]}
"""


# --- G6's checks: plain code, before anything runs ---------------------------------------------------------------------------
def canon(value: object) -> str:
    """A JSON value's one spelling, and the one rule by which two values are compared here: a table's with a factor's, the two
    tables', and, in the compiled file, which holds this function as G6's own text, an observation's with its expectation. A
    number is spelled by its exact decimal value, so 2 and 2.0 are one value and 9007199254740992.0 and 9007199254740993.0 two (a
    float by the shortest text that reads back as it, which is what JSON writes of it); a boolean is never a number; null, a
    string, a list and an object by their parts, an object's keys sorted. Anything else, a number that is not finite among
    them, is no JSON value: ValueError."""
    if value is None or isinstance(value, (bool, str)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(float.__repr__(value)) if isinstance(value, float) else Decimal(value)
        if not number.is_finite():
            raise ValueError(f"{value!r} is not a finite number")
        sign, digits, exponent = number.as_tuple()
        kept = "".join(map(str, digits)).rstrip("0")
        return f"{'-' * sign}{kept}e{exponent + len(digits) - len(kept)}" if kept else "0"
    if isinstance(value, list):
        return "[" + ",".join(map(canon, value)) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ",".join([f"{json.dumps(key, ensure_ascii=False)}:{canon(value[key])}" for key in sorted(value)]) + "}"
    raise ValueError(f"a {type(value).__name__} is not a JSON value")


class _Refused(ValueError):
    """JSON that reads but holds no one exact value, so a model or a row holding it is invalid: a number no float holds, as
    1e309, which Python's own reader makes infinite, so no value observe returns can be compared with it; an object that names
    one key twice, of which Python's reader keeps the last, so the first is never checked; a string that is no Unicode text,
    as a lone surrogate, which no UTF-8 text holds."""


# Quoted where a name or a subscript would be read as each runs: the compiled file holds these as they are, on the bug's Python.
def _no_constant(name: str) -> "Any":
    raise ValueError(f"{name} is not a JSON value")


def _number(text: str) -> Decimal:
    """A JSON number with a fraction or an exponent, as its exact decimal value; ``_Refused`` where no float holds it."""
    try:
        number = Decimal(text)
    except ArithmeticError:                           # an exponent beyond any Decimal's
        raise _Refused(f"a number no float holds ({text[:40]})") from None
    if not math.isfinite(float(number)):
        raise _Refused(f"a number no float holds ({text[:40]})")
    return number


def _object(pairs: "list[tuple[str, Any]]") -> "dict[str, Any]":
    """A JSON object, at any depth; ``_Refused`` where it names one key twice."""
    got: dict[str, Any] = {}
    for key, value in pairs:
        if key in got:
            raise _Refused(f"an object that names {key[:40]!r} twice")
        got[key] = value
    return got


def _loads(text: str) -> "Any":
    """JSON as JSON has it, read exactly: NaN and Infinity, which Python's reader takes, are refused; a number with a fraction or
    an exponent is its exact ``Decimal`` (an integer is Python's int, as exact); a number no float holds, an object that names
    a key twice and a string that is no Unicode text are ``_Refused``. What it gives ``canon`` spells: a value nested too deep
    for it is a RecursionError here, as one too deep for the reader is. The one reader of the model, of reader 2's reply and, in
    the compiled file, which holds it as G6's own text, of the probe's answer."""
    value = json.loads(text, parse_float=_number, parse_constant=_no_constant, object_pairs_hook=_object)
    try:
        canon(value).encode("utf-8")                  # its spelling holds every string and key of it as it is
    except UnicodeEncodeError:
        raise _Refused("a string that is no Unicode text (a lone surrogate)") from None
    return value


def _python(value: Any) -> Any:
    """A value read by ``_loads`` as Python's own reader gives it, each ``Decimal`` a float: what the probe is given, what reader
    2 is shown and what a record keeps. Every comparison is of the exact value (``canon``)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return list(map(_python, value))
    if isinstance(value, dict):
        return {key: _python(one) for key, one in value.items()}
    return value


def _plain(value: Any) -> bool:
    """A factor's value: a JSON scalar, or a list of such values."""
    if isinstance(value, list):
        return all(map(_plain, value))
    return value is None or isinstance(value, (bool, int, float, Decimal, str))


def _exact(value: Any) -> bool:
    """Whether every number a value holds is one a float or an int holds as written: an integer is, as Python's int; a number
    with a fraction or an exponent is where its float has its spelling (``canon``). A factor's value is given to the probe so,
    so no two values the tables tell apart are one float to the probe, and reader 2 is shown what the probe is given; an
    agreed expectation that is not so can never equal what observe returns, so its cell is dropped (``formal``)."""
    if isinstance(value, list):
        return all(map(_exact, value))
    if isinstance(value, dict):
        return all(map(_exact, value.values()))
    return not isinstance(value, Decimal) or canon(float(value)) == canon(value)


def _covers(spec: Any, values: Sequence[Any]) -> set[str] | None:
    """The values of a factor a rule's ``when`` gives it: the one value, where ``spec`` is one of the factor's values, else each
    value of the list ``spec`` is; None where it is neither."""
    known = {canon(value) for value in values}
    if canon(spec) in known:
        return {canon(spec)}
    if isinstance(spec, list) and spec and all(canon(one) in known for one in spec):
        return {canon(one) for one in spec}
    return None


def _equals(want: Any) -> bool:
    return isinstance(want, dict) and set(want) == {"equals"} and isinstance(want["equals"], str)


def check_rows(rows: Any, factors: Mapping[str, Sequence[Any]], observations: Sequence[str]) -> str:
    """Why a rule table breaks its form, or "": each row an object whose ``when`` names only known factors and their values,
    whose ``expect`` names at least one known observation (an ``equals`` naming another known one), with a quote."""
    if not isinstance(rows, list):
        return "rules is not a list"
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("when"), dict):
            return f"rule {number} has no when object"
        for factor, spec in row["when"].items():
            if factor not in factors:
                return f"rule {number} names an unknown factor {factor!r}"
            if _covers(spec, factors[factor]) is None:
                return f"rule {number} gives factor {factor!r} a value it does not have"
        if not isinstance(row.get("expect"), dict) or not row["expect"]:
            return f"rule {number} has no expect object of at least one observation"
        for name, want in row["expect"].items():
            if name not in observations:
                return f"rule {number} expects an unknown observation {name!r}"
            if _equals(want) and (want["equals"] not in observations or want["equals"] == name):
                return f"rule {number} expects {name} to equal an unknown observation {want['equals']!r}"
        if not isinstance(row.get("quote"), str):
            return f"rule {number} has no quote"
    return ""


def _inert(node: ast.AST) -> bool:
    """A statement that is a string alone, a docstring or any other: it does nothing as it runs, and holds only text."""
    value = node.value if isinstance(node, ast.Expr) else None
    if isinstance(value, ast.JoinedStr):
        return all(isinstance(part, ast.Constant) for part in value.values)
    return isinstance(value, ast.Constant) and isinstance(value.value, (str, bytes))


def _tree(text: str) -> ast.AST | None:
    """``text`` as Python's own parser reads it; None where it cannot."""
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


#: The spellings of the values that tell nothing, so a literal of one is never masked: a boolean, null, -1, 0 and 1, the empty
#: string, and the empty list and object.
_SILENT = frozenset(map(canon, (None, True, False, -1, 0, 1, "", [], {})))
#: The nodes a literal may be: a constant, a sign before one, and the displays ``ast.literal_eval`` can read.
_LITERALS = (ast.Constant, ast.UnaryOp, ast.List, ast.Tuple, ast.Set, ast.Dict)


def _telling(rules: Sequence[Mapping[str, Any]]) -> tuple[set[str], set[str], str]:
    """What of reader 1's table reader 2 must never be shown, each by its ``canon``: every value a row expects, every row, and
    the rules list."""
    return {canon(want) for row in rules for want in row["expect"].values()}, set(map(canon, rules)), canon(list(rules))


def _holds(value: Any, rows: set[str]) -> bool:
    """Whether a JSON value is one of these rows or holds one, at any depth: an object's keys as well as its values (follow-up
    review 2, finding 1), walked with a stack of its own, so no nesting the JSON reader accepts exhausts Python's (follow-up
    review 3, finding 1)."""
    stack = [value]
    while stack:
        one = stack.pop()
        if canon(one) in rows:
            return True
        if isinstance(one, list):
            stack.extend(one)
        elif isinstance(one, dict):
            stack.extend([*one, *one.values()])
    return False


def _tells(node: ast.AST, told: tuple[set[str], set[str], str]) -> bool:
    """Whether a literal tells one of reader 1's expectations (``_telling``): its value, as ``ast.literal_eval`` reads it, is
    a JSON value that is no value that tells nothing (``_SILENT``) and is an expected value or the rules list, or is or holds
    a row; or it is a string whose text, read as JSON by the model's own rule (``_loads``), is or holds, at any depth, an
    expected value that is not silent, a row or the rules list. A node that is no literal, or whose value is no JSON value, as a
    tuple, tells none: its parts are read on their own. A value the probe builds as it runs, from literals none of which tells,
    is not found here: only running the probe could find it (PLAN.md, follow-up review 1)."""
    expected, rows, rules = told
    try:
        value = ast.literal_eval(node)
        spelled = canon(value)
        if spelled not in _SILENT and (spelled in expected or spelled == rules or _holds(value, rows)):
            return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    if not isinstance(value, str):
        return False
    try:
        read = _loads(value)
    except (ValueError, RecursionError):              # no JSON text, or none by the model's rule (``_Refused`` is a ValueError)
        return False
    try:
        return _holds(read, (expected - _SILENT) | rows | {rules})
    except RecursionError:                            # a value too deep to spell by ``canon``: masked, as one that may tell
        return True


def _masks(nodes: Sequence[ast.AST], told: tuple[set[str], set[str], str]) -> list[ast.AST]:
    """The nodes whose text is masked: each literal that tells (``_tells``), or, for a part of an f-string, the f-string whole,
    as some Pythons' parsers place an f-string's parts only within the whole."""
    owner: dict[int, ast.AST] = {}
    for node in nodes:                                # outermost first: ``ast.walk`` goes down the tree a level at a time
        if isinstance(node, ast.JoinedStr):
            for part in ast.walk(node):
                owner.setdefault(id(part), node)
    return [owner.get(id(node), node) for node in nodes if isinstance(node, _LITERALS) and _tells(node, told)]


#: What stands in reader 2's probe for a literal that tells (``_shown``): an expression wherever a literal is, and a value
#: pattern in a ``case``, where ``...`` is no pattern (follow-up review 1, finding 1).
MASK = "MASKED.literal"
#: What reader 2 is shown in place of a probe whose masks leave text Python's parser cannot read (``_shown``).
WITHHELD = "(the probe is withheld: masking what would tell reader 1's expectations left text Python cannot read)"


def _shown(probe: str, rules: Sequence[Mapping[str, Any]] = ()) -> tuple[str, int] | None:
    """The probe as reader 2 is shown it, and how many of its literals were masked. Its text, with its newlines as Python reads
    them, less every comment, as Python's own tokenizer reads one, and with every statement that is a string alone
    (``_inert``), as Python's own parser reads one, standing as ``pass``, a statement where a statement was, so no text of
    reader 1's that does nothing as the probe runs reaches reader 2 and no suite is left empty; and each literal that tells
    one of the expectations of ``rules`` (``_masks``) replaced by ``MASK``. What is shown is read again by Python's own
    parser, whether or not anything was masked, and where it cannot read it (a sign before a mask in a ``case``, which allows a
    sign only before a number), ``WITHHELD`` is shown in its place, never the text. None where the tokenizer cannot read the
    probe. A probe the parser cannot read loses its comments alone (``parse_model`` refuses one: it cannot run, and none of
    its literals can be found)."""
    text = probe.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    try:
        edits = [(token.start, token.end, "") for token in tokenize.generate_tokens(io.StringIO(text).readline)
                 if token.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):         # an IndentationError is a SyntaxError
        return None
    tree = _tree(text)
    nodes = list(ast.walk(tree)) if tree else []

    def at(row: int, column: int) -> tuple[int, int]:  # the parser counts a column in UTF-8 bytes, the tokenizer in characters
        return row, len(lines[row - 1].encode("utf-8")[:column].decode("utf-8"))

    def edit(node: ast.AST, new: str) -> tuple[tuple[int, int], tuple[int, int], str]:
        return at(node.lineno, node.col_offset), at(node.end_lineno, node.end_col_offset), new

    edits += [edit(node, "pass") for node in nodes if _inert(node)]   # a statement where a statement was
    edits += [edit(node, MASK) for node in (_masks(nodes, _telling(rules)) if rules else ())]
    starts = list(itertools.accumulate((len(line) + 1 for line in lines), initial=0))
    spans = [(starts[row - 1] + column, starts[end_row - 1] + end_column, new)
             for (row, column), (end_row, end_column), new in edits]
    kept, done, masked = [], 0, 0
    # By where each begins, the longest first and a cut before a mask: one that begins within another is within it.
    for start, end, new in sorted(spans, key=lambda span: (span[0], -span[1], span[2])):
        if start >= done:
            kept.append(text[done:start] + new)
            done, masked = end, masked + (new == MASK)
    shown = "".join(kept) + text[done:]
    return (shown if tree is None or _tree(shown) is not None else WITHHELD), masked


def _bare(probe: str) -> str | None:
    """The probe with no comment and no statement that is a string alone, and no literal masked (``_shown``); None where the
    tokenizer cannot read it."""
    shown = _shown(probe)
    return None if shown is None else shown[0]


def parse_model(text: str) -> tuple[dict[str, Any] | None, str]:
    """Reader 1's model, or None and why it breaks its form: factors of distinct JSON scalars or lists, ``1..MAX_CASES`` cases
    in their product, observations distinct identifiers, a probe of some source Python's tokenizer and parser both read, and a
    table ``check_rows`` passes."""
    try:
        got = _loads(text)
    except _Refused as exc:
        return None, f"the model holds {exc}"
    except (ValueError, RecursionError) as exc:
        return None, f"the model is not JSON ({type(exc).__name__})"
    if not isinstance(got, dict):
        return None, "the model is not a JSON object"
    factors, observations, probe, rows = (got.get(key) for key in ("factors", "observations", "probe", "rules"))
    if not isinstance(factors, dict) or not all(isinstance(values, list) for values in factors.values()):
        return None, "factors is not an object of lists"
    for name, values in factors.items():
        if not all(map(_plain, values)):
            return None, f"a value of factor {name!r} is not a JSON scalar or list"
        if not all(map(_exact, values)):
            return None, f"factor {name!r} holds a number no float holds exactly as written, so the probe cannot be given it"
        if len({canon(value) for value in values}) != len(values):
            return None, f"factor {name!r} repeats a value"
    size = math.prod(len(values) for values in factors.values())
    if not 1 <= size <= MAX_CASES:
        return None, f"the domain has {size} cases, outside 1 to {MAX_CASES}"
    if not isinstance(observations, list) or not observations or len(set(map(str, observations))) != len(observations) or \
            not all(isinstance(name, str) and name.isidentifier() for name in observations):
        return None, "observations are not distinct Python identifiers"
    if not isinstance(probe, str) or not probe.strip():
        return None, "the probe is no Python source"
    if _bare(probe) is None:
        return None, "the probe cannot be read by Python's tokenizer, so reader 2 cannot be shown it"
    if _tree(probe) is None:
        return None, "the probe cannot be read by Python's parser: it cannot run, and no literal of it can be masked for reader 2"
    why = check_rows(rows, factors, observations)
    if why:
        return None, why
    return {"factors": factors, "observations": observations, "probe": probe, "rules": rows}, ""


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def kept_rows(rows: Sequence[Mapping[str, Any]], issue: str) -> tuple[list[Mapping[str, Any]], int]:
    """The rows whose quote is a span of the issue's words, whitespace collapsed on both sides, and how many were dropped. An
    empty quote quotes nothing."""
    words = _collapsed(issue)
    kept = [row for row in rows if _collapsed(row["quote"]) and _collapsed(row["quote"]) in words]
    return kept, len(rows) - len(kept)


def cases(factors: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Every case of the domain, in one order: the factors by name, each factor's values in the model's order."""
    names = sorted(factors)
    return [dict(zip(names, values)) for values in itertools.product(*(factors[name] for name in names))]


def table(rows: Sequence[Mapping[str, Any]], factors: Mapping[str, Sequence[Any]]) -> dict[tuple[str, str], Any] | None:
    """A table's expectation for each (case, observation) a row covers, by the case's ``canon``; None where two rows give one
    case two expectations for one observation, so the table conflicts with itself and is refused."""
    cells: dict[tuple[str, str], Any] = {}
    for case in cases(factors):
        key = canon(case)
        for row in rows:
            if all(canon(case[factor]) in _covers(spec, factors[factor]) for factor, spec in row["when"].items()):
                for name, want in row["expect"].items():
                    if (key, name) in cells and canon(cells[key, name]) != canon(want):
                        return None
                    cells[key, name] = want
    return cells


def compare(one: Mapping[tuple[str, str], Any], two: Mapping[tuple[str, str], Any]
            ) -> tuple[dict[tuple[str, str], Any], dict[str, int]]:
    """The cells both tables cover and give the same expectation, and the counts of the comparison: cells both cover, differ on
    and agree on, the distinct cases and observations of each, and the cells one table alone covers."""
    both = sorted(one.keys() & two.keys())
    differ = [cell for cell in both if canon(one[cell]) != canon(two[cell])]
    agreed = {cell: one[cell] for cell in both if canon(one[cell]) == canon(two[cell])}
    return agreed, {"cells_both": len(both), "cells_differ": len(differ), "cells_agree": len(agreed),
                    "cases_differ": len({case for case, _ in differ}), "cases_agree": len({case for case, _ in agreed}),
                    "observations_differ": len({name for _, name in differ}),
                    "observations_agree": len({name for _, name in agreed}),
                    "cells_reader_1_only": len(one.keys() - two.keys()), "cells_reader_2_only": len(two.keys() - one.keys())}


# --- the compiled file -------------------------------------------------------------------------------------------------------
#: The compiled file's own text, the same for every bug: these imports, then ``canon`` and ``_loads`` as this module has them
#: (``_OWN``), then the probe as one text and the agreed cells as one JSON text, then ``_TEST``. At the first cell the probe runs
#: in a process of its own, given the cases and never an expectation, so nothing it does reaches what a cell expects, and no
#: name it defines is this file's: the file's cases are the cells and no others.
_HEAD = '''# G6's cases: the cells both readers' tables agree on, as JSON data, each expectation by its one spelling (canon, below), and
# the probe, as one text, run in a process of its own that is given the cases and never an expectation.
import json
import math
import os
import signal
import subprocess
import sys
from decimal import Decimal

import pytest


'''
_TEST = '''
#: The probe's process: G6's own program, run by this file's interpreter in the directory pytest runs in and on pytest's own
#: import path, so the project imports as it does for any test. It reads the probe, the cases and that path from its input,
#: never an expectation, runs the probe as its main code in a module of its own, and writes one JSON answer: the type name of
#: what the probe raised as it ran, or for each case the type name of what observe raised, or its observations where they are
#: a JSON value by their types alone, or else the type name of what observe gave. What the probe prints goes nowhere, and the
#: process ends once it has answered.
_G6_CHILD = """
def _g6_child(json, math, os, sys):
    sent = json.loads(sys.stdin.buffer.read())
    answer = os.fdopen(os.dup(1), "w", encoding="ascii")
    os.dup2(2, 1)
    # What decides and writes a value is taken here, before the probe runs, so no name the probe defines or replaces is one
    # of them. A value is JSON by its types alone, each exactly one of JSON's: nothing it supplies, as an equality or an
    # iteration of a type of its own, decides it.
    kind, failed, dumps, finite, items, encode, every = type, BaseException, json.dumps, math.isfinite, dict.items, str.encode, all
    Dict, List, Str, Int, Float, Bool = dict, list, str, int, float, bool

    def plain(value):
        of = kind(value)
        if of is Dict:
            return every(kind(key) is Str and plain(key) and plain(one) for key, one in items(value))
        if of is List:
            return every(plain(one) for one in value)
        if of is Str:
            return encode(value, "utf-8") is not None  # strict: a lone surrogate, which is no Unicode text, raises
        if of is Float:
            return finite(value)
        return of is Int or of is Bool or value is None

    main = kind(sys)("__main__")  # a module of the probe's own, found by its name as a file's main module is
    sys.modules["__main__"] = main
    sys.path[:] = sent["path"]
    try:
        exec(compile(sent["probe"], "probe.py", "exec", dont_inherit=True), main.__dict__)
        observe, seen = main.__dict__.get("observe"), []
        for case in sent["cases"]:
            try:
                got = observe(case)
            except failed as exc:
                seen.append(dumps({"raised": kind(exc).__name__}))
                continue
            try:
                one = dumps({"seen": got}) if plain(got) else ""
            except failed:  # no Unicode text, or too deep or too long to write: no JSON value here
                one = ""
            seen.append(one or dumps({"not_json": kind(got).__name__}))
        said = '{"seen": [' + ", ".join(seen) + "]}"
    except failed as exc:
        said = dumps({"raised": kind(exc).__name__})
    answer.write(said)
    answer.flush()
    os._exit(0)


_g6_child(__import__("json"), __import__("math"), __import__("os"), __import__("sys"))
"""
#: How long the probe's process may run, in seconds, before it is ended with every process it started and every cell skipped.
_G6_SECONDS = 120
#: The probe's process starts a process group of its own, so what it starts is ended with it (_g6_end). On Windows it is created
#: suspended (0x4, CREATE_SUSPENDED), held in a job object of its own (_g6_hold) and only then resumed, so not even a launcher's
#: first process runs outside the job (follow-up review 3, finding 3).
_G6_APART = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x4} if os.name == "nt"
             else {"start_new_session": True})
_g6_answer = []


def _g6_kernel():
    """Windows' kernel32, with the types of the calls that hold the probe's process in a job object and end the job."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, result, args in (("CreateJobObjectW", wintypes.HANDLE, [ctypes.c_void_p, wintypes.LPCWSTR]),
                               ("OpenProcess", wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]),
                               ("AssignProcessToJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.HANDLE]),
                               ("TerminateJobObject", wintypes.BOOL, [wintypes.HANDLE, wintypes.UINT]),
                               ("CloseHandle", wintypes.BOOL, [wintypes.HANDLE])):
        call = getattr(kernel, name)
        call.restype, call.argtypes = result, args
    return kernel


def _g6_hold(child):
    """On Windows, a job object of its own holding the probe's process, made while that process is still suspended (_G6_APART),
    so it has run nothing: once it is held it is resumed, and every process it starts, and every one those start, is in the job,
    whether or not the process that started it still runs, and ends with the job (_g6_end). None elsewhere, where the process
    group its own session began holds them. An OSError where Windows will not hold it or resume it."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    kernel = _g6_kernel()
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.restype, ntdll.NtResumeProcess.argtypes = ctypes.c_long, [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    process = kernel.OpenProcess(0x0901, False, child.pid)   # PROCESS_SET_QUOTA, PROCESS_TERMINATE, PROCESS_SUSPEND_RESUME
    held = bool(job) and bool(process) and bool(kernel.AssignProcessToJobObject(job, process))
    error = ctypes.get_last_error()
    resumed = held and ntdll.NtResumeProcess(process) == 0
    for handle in (process,) + (() if resumed else (job,)):
        if handle:
            kernel.CloseHandle(handle)
    if not resumed:
        raise ctypes.WinError(error) if not held else OSError("the probe's process was held but could not be resumed")
    return job


def _g6_end(child, job=None):
    """The probe's process ended with every process it started, then reaped: on POSIX its process group, which its own session
    began, on Windows its job (_g6_hold)."""
    try:
        if os.name == "nt":
            _g6_kernel().TerminateJobObject(job, 1)
        else:
            os.killpg(child.pid, signal.SIGKILL)
    except OSError:                                   # the group had ended
        pass
    child.kill()
    child.communicate()


def _g6_ask():
    """The probe's process, run once: each case's entry by the case's canon, or None and why, where the process overran
    _G6_SECONDS and was ended with every process it started, or exited other than 0, whatever it wrote, or gave no answer
    this file reads by the model's own rule (_loads), or the probe raised as it ran. Then every cell is skipped, and none
    fails."""
    cases = {canon(cell["case"]): cell["case"] for cell in _G6_CELLS}
    path = [entry for entry in sys.path if isinstance(entry, str)]
    sent = json.dumps({"probe": _G6_PROBE, "cases": list(cases.values()), "path": path}).encode("ascii")
    try:
        # -I and -S: no PYTHONPATH, user site, site, sitecustomize or .pth file, so nothing of the project runs as the
        # interpreter starts, before its process is held (_g6_hold); its path is the one sent (follow-up review 2, finding 4).
        child = subprocess.Popen([sys.executable, "-I", "-S", "-c", _G6_CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, **_G6_APART)
    except OSError:
        return None, "the probe's process did not start"
    try:
        job = _g6_hold(child)
    except OSError:
        child.kill()
        child.communicate()
        return None, "the probe's process could not be held with every process it starts"
    try:
        out = child.communicate(sent, timeout=_G6_SECONDS)[0]
    except subprocess.TimeoutExpired:
        _g6_end(child, job)
        return None, f"the probe's process ran past {_G6_SECONDS} seconds"
    finally:
        if job:
            _g6_kernel().CloseHandle(job)
    if child.returncode != 0:                         # a process that failed gives no answer, whatever it wrote
        return None, f"the probe's process exited {child.returncode}"
    try:
        said = _loads(out.decode("ascii"))
    except (ValueError, RecursionError):              # no JSON, or none by the model's rule: a UnicodeError is a ValueError
        said = None
    if isinstance(said, dict) and isinstance(said.get("raised"), str):
        return None, f"the probe raised {said['raised']}"
    seen = said.get("seen") if isinstance(said, dict) else None
    if not isinstance(seen, list) or len(seen) != len(cases) or not all(
            isinstance(one, dict) and ("seen" in one or isinstance(one.get("raised"), str)
                                       or isinstance(one.get("not_json"), str)) for one in seen):
        return None, "the probe's process gave no answer"
    return dict(zip(cases, seen)), ""


@pytest.mark.parametrize("cell", _G6_CELLS, ids=[cell["id"] for cell in _G6_CELLS])
def test_formal(cell):
    if not _g6_answer:
        _g6_answer.append(_g6_ask())
    answer, why = _g6_answer[0]
    if answer is None:
        pytest.skip(why)
    one = answer[canon(cell["case"])]
    if "seen" not in one:
        pytest.skip(f"observe raised {one['raised']}" if "raised" in one else
                    f"what observe gave ({one['not_json']}) is no JSON value, so its case is unobservable")
    seen = one["seen"]
    names = [cell["observation"], *([cell["equals"]] if "equals" in cell else [])]
    if not isinstance(seen, dict) or any(name not in seen for name in names):
        pytest.skip("observe gave no such observation")
    # The tables' one rule (canon); what observe gave is a JSON value as it was, or its case was unobservable and skipped.
    assert canon(seen[cell["observation"]]) == (canon(seen[cell["equals"]]) if "equals" in cell else cell["want"])
'''


def _case_id(number: int, name: str, case: Mapping[str, Any]) -> str:
    label = ",".join(f"{factor}={canon(value)}" for factor, value in case.items())
    return f"c{number:03d}-{name}-" + re.sub(r"[^A-Za-z0-9_.,=+-]+", "_", label)[:60]


#: This module's own text in the compiled file: the one rule two values are compared by, and the one reader of JSON.
_OWN = (canon, _Refused, _no_constant, _number, _object, _loads)


def compile_test(probe: str, factors: Mapping[str, Sequence[Any]], agreed: Mapping[tuple[str, str], Any]) -> str:
    """One pytest file: G6's own text with ``canon`` and ``_loads`` in it (``_OWN``), the probe as one string, sent with the
    cases to a process of its own and never with an expectation, the agreed cells as one JSON text read by ``json.loads``, each
    case as Python's reader gives it and each expectation by its ``canon``, then one test with a case for each cell, in the
    domain's order, observations by name. No other text of a model is in it."""
    cells = []
    for number, case in enumerate(cases(factors)):
        key = canon(case)
        for name in sorted(name for at, name in agreed if at == key):
            want = agreed[key, name]
            cells.append({"id": _case_id(number, name, case), "case": _python(case), "observation": name,
                          **({"equals": want["equals"]} if _equals(want) else {"want": canon(want)})})
    data = json.dumps(cells, sort_keys=True, ensure_ascii=True, allow_nan=False)
    own = "\n\n".join(map(inspect.getsource, _OWN))
    return _HEAD + own + f"\n\n_G6_PROBE = {probe!r}\n_G6_CELLS = json.loads({data!r})\n\n" + _TEST


# --- the two readers ---------------------------------------------------------------------------------------------------------
class Reader2(ClaudeCodeCaller):
    """``ClaudeCodeCaller`` as it is, no tool, its own clean environment, with ``READER_2_ENV`` added to the settings file it is
    given beside hooks off, and a reply taken only from a result Claude Code marks a success."""

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        argv, stdin = super()._command(program, system, user, work, files)
        settings = Path(argv[argv.index("--settings") + 1])
        settings.write_text(json.dumps({**json.loads(HOOKS_OFF), "env": READER_2_ENV}), encoding="utf-8")
        return argv, stdin

    def _reply(self, code: int, printed: str, errors: str, files: Path, hide: tuple[str, ...]) -> str:
        """``ClaudeCodeCaller``'s reading of what Claude Code printed, given a result only where its subtype is ``success``:
        any other subtype, or none, is no answer (an LLMError), as an error result, a result with no text and no result are."""
        result = _result(printed)
        if result is not None and result.get("subtype") != "success":
            raise LLMError(f"{HOST_FAILED}: claude exited {code} and gave a result not marked a success "
                           f"(subtype {str(result.get('subtype'))[:40]!r})")
        return super()._reply(code, printed, errors, files, hide)


def reader_2() -> ClaudeCodeCaller:
    """Reader 2, built here in the replay's own code, which is what lets it use the person's Claude Code sign-in
    (``g6.core.host_models``): nothing starts until it is asked."""
    return Reader2(READER_2_MODEL)


def reader_2_text(issue: str, model: Mapping[str, Any]) -> str:
    """What reader 2 is given: the issue's text, and reader 1's factors, observations and probe, the probe with no comment or
    docstring and no literal that tells one of the expectations of reader 1's rules (``_shown``; ``parse_model`` refuses one
    it cannot read). Never reader 1's rules."""
    return (f"<issue>\n{issue}\n</issue>\n\n<factors>\n{json.dumps(_python(model['factors']), indent=1)}\n</factors>\n\n"
            f"<observations>\n{json.dumps(model['observations'])}\n</observations>\n\n"
            f"<probe>\n{_shown(model['probe'], model.get('rules') or [])[0]}\n</probe>\n")


def reader_2_rules(reply: str) -> list[Any] | None:
    """Reader 2's table: its reply as one JSON object with a ``rules`` list, bare or in one fence; None if it is not one. A
    reply that holds a number no float holds, an object naming a key twice or a string that is no Unicode text is JSON, and
    raises ``_Refused``: its table is invalid (``formal``)."""
    text = reply.strip()
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\n(.*?)\n?```", text, re.S)
    try:
        got = _loads(fenced[1] if fenced else text)
    except _Refused:
        raise
    except (ValueError, RecursionError):
        return None
    return got["rules"] if isinstance(got, dict) and isinstance(got.get("rules"), list) else None


def _closed(record: dict[str, Any], status: str, why: str) -> dict[str, Any]:
    """A bug's model that runs nothing: no test, so each patch is "none", and the record kept, so it is not asked again."""
    return {**record, "verdict": "none", "test": "", "status": status, "status_reason": why}


def formal(found: Mapping[str, Any], title: str, text: str, caller: Any) -> dict[str, Any]:
    """G6's checks on reader 1's answer, with reader 2 asked where its model is valid: the bug's record, its ``status``, the
    compiler that made it (``COMPILER``), how many literals of the probe reader 2 was shown masked (``masked``) and its reply,
    and, where the model stands, the compiled file as its test. A reader that failed is no answer, which replay 3 asks again."""
    found = dict(found)
    if r3.no_answer(found):
        return {**found, "status": "no_answer"}
    if not found.get("test"):
        return {**found, "status": "no_model"}
    issue = f"{title}\n\n{text}"
    record = {**found, "model": found["test"], "test": "", "compiler": COMPILER}
    model, why = parse_model(found["test"])
    if model is None:
        return _closed(record, "invalid", why)
    first, dropped = kept_rows(model["rules"], issue)
    record |= {"tables": {"reader_1": _python(first)}, "dropped": {"reader_1": dropped}}
    one = table(first, model["factors"])
    if one is None:
        return _closed(record, "conflict", "reader 1's table gives one case two values for one observation")
    record["masked"] = _shown(model["probe"], model["rules"])[1]
    try:
        reply = caller.complete(READER_2_SYSTEM, reader_2_text(issue, model))
    except (RuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        # an LLMError is a RuntimeError, and a text its call cannot encode or decode a UnicodeError
        return {**record, "status": "no_answer", "error": f"reader 2: {type(exc).__name__}: {exc}"[:400]}
    record["reader_2_reply"] = reply                  # kept, so the model can be compiled again exactly with no call (``again``)
    try:
        rows = reader_2_rules(reply)
    except _Refused as exc:
        return _closed(record, "invalid", f"reader 2: its reply holds {exc}")
    if rows is None:
        return {**record, "status": "no_answer", "error": "reader 2 gave no JSON object with a rules list"}
    why = check_rows(rows, model["factors"], model["observations"])
    if why:
        return _closed(record, "invalid", f"reader 2: {why}")
    second, dropped = kept_rows(rows, issue)
    record["tables"]["reader_2"], record["dropped"]["reader_2"] = _python(second), dropped
    two = table(second, model["factors"])
    if two is None:
        return _closed(record, "conflict", "reader 2's table gives one case two values for one observation")
    agreed, record["comparison"] = compare(one, two)
    if record["comparison"]["cells_differ"]:
        return _closed(record, "ambiguous", "the two tables differ on a case both cover")
    if not agreed:
        return _closed(record, "no_agreed_case", "the two tables agree on no case")
    # An agreed number no float holds exactly as written can never equal what observe returns: its cell would fail on the
    # parent and on every patch alike, and the gate flag every patch. It is dropped and counted; an int is exact and stays.
    held = {cell: want for cell, want in agreed.items() if _exact(want)}
    record["comparison"]["cells_unholdable"] = len(agreed) - len(held)
    if not held:
        return _closed(record, "no_agreed_case", "every agreed cell expects a number no float holds exactly as written")
    return {**record, "test": compile_test(model["probe"], model["factors"], held), "status": "stands", "status_reason": ""}


#: What names an attempt's record beside a bug's: the bug record's name, this, and the attempt's own nonce.
_NO_ANSWER = ".no_answer."


def no_answers(root: Path, task: str) -> list[Path]:
    """The records of a bug's attempts that ended in no answer, one an attempt, beside the bug's record and never at its
    place, so none is read as an answer and the bug is still asked again."""
    saved = r3.bug_record(root, task, FORMAL)
    return sorted(path for path in saved.parent.glob("*.json") if path.name.startswith(saved.stem + _NO_ANSWER))


def ask(ctx: we.Context, task: we.Task, patch: Path, title: str, text: str, prompt: str, where: str, file: str, *,
        candidate: bool, run: bool, caller: Any, step: Callable[..., Any], root: Path) -> tuple[dict[str, Any], None, None]:
    """Replay 3's ``ask`` for replay 6: reader 1 through ``step`` as replay 3 asks its reviewer, then ``formal``. An attempt that
    ends in no answer is kept in a record of its own, with its reason (``no_answers``)."""
    found, _, _ = step(ctx, task, patch, title, text, prompt, where, file, candidate=candidate, run=run)
    got = formal(found, title, text, caller)
    if got["status"] == "no_answer":
        saved = r3.bug_record(root, task.id, FORMAL)
        saved.parent.mkdir(parents=True, exist_ok=True)
        fr.write_whole(saved.with_name(f"{saved.stem}{_NO_ANSWER}{uuid.uuid4().hex}.json"),
                       json.dumps({"task": task.id, "instruction": FORMAL.digest, "status": "no_answer",
                                   "status_reason": r3.no_answer(got)}, indent=1))
    return got, None, None


class _Said:
    """A reader's saved reply, given again, so that no reader is asked."""

    def __init__(self, reply: str):
        self.reply = reply

    def complete(self, system: str, user: str) -> str:
        return self.reply


def again(root: Path, task: str, title: str, text: str) -> bool:
    """A bug's saved record that stands, made by another compiler (``COMPILER``), made again by this one from the answers it
    holds and saved whole in its place, with no reader asked: reader 1's model and reader 2's reply. A record older than the
    reply's keeping has the table kept of it, whose numbers are floats, so two values the reply told apart can only become one,
    which leaves the bug ambiguous and flags nothing. Its masks stay the ones reader 2 was shown when it was asked. Whether it
    was made again. A record that does not stand has nothing compiled, and is kept as it was."""
    saved = r3.bug_record(root, task, FORMAL)
    found = (fr.read(saved) or {}).get("found")
    if not isinstance(found, dict) or not stale(found):
        return False
    reply = found.get("reader_2_reply")
    if not isinstance(reply, str):
        reply = json.dumps({"rules": (found.get("tables") or {}).get("reader_2") or []})
    answer = {**{key: found.get(key, "") for key in ("verdict", "rule", "why", "error")}, "test": found.get("model", "")}
    got = formal(answer, title, text, _Said(reply))
    if "masked" in got:                               # what reader 2 was shown, which no reader asked again changes
        got["masked"] = found.get("masked")
    if got["status"] == "no_answer":                  # read again, its answers are no answer: closed, as no reader is asked
        got = _closed({**got, "error": ""}, "invalid", f"made again by compiler {COMPILER}: {r3.no_answer(got)}"[:400])
    fr.write_whole(saved, json.dumps({"task": task, "instruction": FORMAL.digest, "found": got}, indent=1))
    return True


def review_bug(ctx: we.Context, root: Path, task: we.Task, rows: Sequence[Mapping[str, Any]], title: str, text: str, *,
               step: Callable[..., Any] | None = None, caller: Any = None, run: r3.Run | None = None) -> list[dict[str, Any]]:
    """Replay 3's ``review_bug`` under ``FORMAL``, unchanged, with ``ask`` as its reviewer: the bug's record saved whole before
    anything runs, then the compiled file run on the parent and on each patch by ``run_on``, each run in a container of its own.
    A saved record that stands, made by another compiler, is first made again (``again``)."""
    if rows:
        again(root, task.id, title, text)
    asking = functools.partial(ask, caller=caller or reader_2(), step=step or fr._step, root=root)
    return r3.review_bug(ctx, root, task, rows, title, text, ask=asking, run=run, ins=FORMAL)


# --- the counts --------------------------------------------------------------------------------------------------------------
def summary(root: Path, tasks: Sequence[str]) -> dict[str, Any]:
    """Prediction 5's counts, and nothing of any text: each bug's status, whether its two readers disagreed (ambiguous, or any
    cell both cover differs; None where the tables were never compared) and how many literals of its probe reader 2 was shown
    masked (None where reader 2 was never asked), by status, and in all. A bug with no record whose attempts ended in no answer
    (``no_answers``) is ``no_answer``; one never attempted is "no record"."""
    bugs = []
    for task in tasks:
        found = (fr.read(r3.bug_record(root, task, FORMAL)) or {}).get("found")
        if not isinstance(found, dict):
            bugs.append({"task": task, "status": "no_answer" if no_answers(root, task) else "no record", "disagreed": None,
                         "masked": None})
            continue
        status, compared, masked = str(found.get("status")), found.get("comparison"), found.get("masked")
        disagreed = (status == "ambiguous" or bool(compared.get("cells_differ"))) if isinstance(compared, dict) else None
        bugs.append({"task": task, "status": status, "disagreed": disagreed, "masked": masked if type(masked) is int else None})
    return {"bugs": bugs, "by_status": dict(sorted(Counter(bug["status"] for bug in bugs).items())),
            "compared": sum(bug["disagreed"] is not None for bug in bugs), "disagreed": sum(bug["disagreed"] is True for bug in bugs),
            "masked": sum(bug["masked"] or 0 for bug in bugs)}


def main(argv: Sequence[str] | None = None) -> None:
    """Replay 3's ``main`` and run loop, with replay 6's reviewer; ``--summary`` prints prediction 5's counts and runs nothing."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6, help="the G6 commit the runs' images were built from")
    ap.add_argument("--tasks", nargs="*", default=list(fr.TRAINING), help="the bugs to review; a bug being judged only after its runs end")
    ap.add_argument("--summary", action="store_true", help="print each bug's status and the readers' disagreement, and run nothing")
    args = ap.parse_args(argv)
    root = c.archive_root() / "round3"
    if args.summary:
        print(json.dumps(summary(root, args.tasks), indent=1), flush=True)
        return
    ctx = we.Context(archive=c.Archive(root), docker=we.Docker(), key_reader=lambda: "", issue_reader=we.read_issue,
                     g6=we.g6_info(args.g6), variant=we.ROUND3, arm="L9")
    import replay_compare     # here, as it imports this module

    replay_compare.patches(root, args.tasks)          # refuses a patch on two bugs, which this replay names by its bytes alone
    print(json.dumps({"instruction": FORMAL.digest}), flush=True)
    caller = reader_2()
    with r3.one_runner(root / "loop" / FORMAL.folder):
        for name in args.tasks:
            rows = r3.waiting(root, [name], FORMAL)
            if not rows:
                continue
            task = we.task_by_id(name)
            title, text = ctx.issue_reader(task)
            for record in review_bug(ctx, root, task, rows, title, text + we.read_thread(task), caller=caller):
                print(json.dumps({"task": name, "said": record["said"], "reason": record["reason"],
                                  "seconds": record["seconds"], "error": record["error"]}), flush=True)
            print(json.dumps({"task": name, "status": summary(root, [name])["bugs"][0]["status"]}), flush=True)


if __name__ == "__main__":
    main()
