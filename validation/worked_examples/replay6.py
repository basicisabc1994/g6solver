# SPDX-License-Identifier: Apache-2.0
"""Replay 6 (PLAN.md, 2026-11-04, evening): a formal model of the rule the issue states, checked by G6 on every case of a finite
domain, then run on replay 3's machinery. It changes no run, no verdict and no rule.

Why: the generation bugs' runs fixed the issue's example and left the rest of a stated rule to the old code, so the cases a test
happens to sample miss them. A model gives every case of its domain a value, and G6 runs every case.

Reader 1 (Codex, asked as replay 3 asks its reviewer, through ``final_review._step``) reads the issue and the parent and writes
the model, one JSON document where replay 3's reply carries its test: named factors (their product the cases, at most
``MAX_CASES``), named observations, a probe whose ``observe(case)`` calls the code, and a rule table, each row quoting the issue.
Reader 2 (Claude, ``g6.core.host_models.ClaudeCodeCaller``, no tool) is given the issue, the factors, the observations and the
probe, never reader 1's rules, the parent or any output of the code, and writes its own table. G6's checks are plain code here,
before anything runs: the model's form, rows whose quote is not the issue's own words dropped, a table that gives one case two
values for one observation refused, and the two tables compared on every case both cover, where any difference leaves the bug
ambiguous and nothing is flagged on it. Every comparison of two values, a table's with a factor's, the two tables', and the
compiled file's of an observation with its expectation, is by one rule (``canon``), and every value the probe is given is the one
the tables read (``_loads``, ``_exact``). Where the model stands, the agreed cells are compiled to one pytest file (G6's own
text, the probe as one text, run in a process of its own that is given the cases and never an expectation, the cells as JSON
data) and replay 3's ``run_on`` runs it on the parent and on each patch, so replay 3's ``verdict`` gives the parent gate, by
case. Each bug's record holds its status (``STATUSES``), the counts and both tables.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import inspect
import itertools
import json
import math
import re
import subprocess
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
#: ``same_case``: replay 6's registration states the parent gate by case, a patch flagged when an agreed case that fails on the
#: parent still fails on it.
FORMAL = r3.Instruction(FORMAL_FIRST, INSTRUCTION, f"final_review_formal-{INSTRUCTION}", f"{we.WE}/formal", "test_formal.py",
                        same_case=True)
FOLDER = FORMAL.folder
#: Reader 2's whole system text, in general words only. It is shown the issue, reader 1's factors, observations and probe, and
#: nothing else (``reader_2_text``).
READER_2_SYSTEM = """You are writing, as data, the rule a reported bug breaks, over an input domain another reader has modelled.
You are given the issue and its discussion; the domain's factors, each a list of JSON values, where a case is one value of each
factor and every combination is a case; the names of the observations a probe returns for a case; and the probe's Python source,
which shows what each observation is. An observation whose call raised has its exception's type name, as a string. You are shown
no other code and no output of any code.

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


def _no_constant(name: str) -> Any:
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


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object, at any depth; ``_Refused`` where it names one key twice."""
    got: dict[str, Any] = {}
    for key, value in pairs:
        if key in got:
            raise _Refused(f"an object that names {key[:40]!r} twice")
        got[key] = value
    return got


def _loads(text: str) -> Any:
    """JSON as JSON has it, read exactly: NaN and Infinity, which Python's reader takes, are refused; a number with a fraction or
    an exponent is its exact ``Decimal`` (an integer is Python's int, as exact); a number no float holds, an object that names
    a key twice and a string that is no Unicode text are ``_Refused``. What it gives ``canon`` spells: a value nested too deep
    for it is a RecursionError here, as one too deep for the reader is."""
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


def parse_model(text: str) -> tuple[dict[str, Any] | None, str]:
    """Reader 1's model, or None and why it breaks its form: factors of distinct JSON scalars or lists, ``1..MAX_CASES`` cases
    in their product, observations distinct identifiers, a probe of some source, and a table ``check_rows`` passes."""
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
#: The compiled file's own text, the same for every bug: these imports, then ``canon`` as this module has it, then the probe as one
#: text and the agreed cells as one JSON text, then ``_TEST``. At the first cell the probe runs in a process of its own, given the
#: cases and never an expectation, so nothing it does reaches what a cell expects, and no name it defines is this file's: the
#: file's cases are the cells and no others.
_HEAD = '''# G6's cases: the cells both readers' tables agree on, as JSON data, each expectation by its one spelling (canon, below), and
# the probe, as one text, run in a process of its own that is given the cases and never an expectation.
import json
import subprocess
import sys
from decimal import Decimal

import pytest


'''
_TEST = '''
#: The probe's process: G6's own program, run by this file's interpreter in the directory pytest runs in, so the project imports
#: as it does for any test. It reads the probe and the cases from its input, never an expectation, runs the probe as its main
#: code, under no module name of its own, and writes one JSON answer: the type name of what the probe raised as it ran, or for
#: each case its observations or the type name of what observe raised. What the probe prints goes nowhere, and the process
#: ends once it has answered.
_G6_CHILD = """
def _g6_child(json, os, sys):
    sent = json.loads(sys.stdin.buffer.read())
    answer = os.fdopen(os.dup(1), "w", encoding="ascii")
    os.dup2(2, 1)
    main = sys.modules["__main__"].__dict__
    try:
        exec(compile(sent["probe"], "probe.py", "exec", dont_inherit=True), main)
        observe, seen = main.get("observe"), []
        for case in sent["cases"]:
            try:
                seen.append({"seen": json.loads(json.dumps(observe(case), default=repr))})
            except BaseException as exc:
                seen.append({"raised": type(exc).__name__})
        said = {"seen": seen}
    except BaseException as exc:
        said = {"raised": type(exc).__name__}
    answer.write(json.dumps(said))
    answer.flush()
    os._exit(0)


_g6_child(__import__("json"), __import__("os"), __import__("sys"))
"""
#: How long the probe's process may run, in seconds, before it is killed and every cell skipped.
_G6_SECONDS = 120
_g6_answer = []


def _g6_ask():
    """The probe's process, run once: each case's entry by the case's canon, or None and why, where the probe raised as it ran,
    or the process overran _G6_SECONDS and was killed, or gave no answer this file reads. Then every cell is skipped, and
    none fails."""
    cases = {canon(cell["case"]): cell["case"] for cell in _G6_CELLS}
    sent = json.dumps({"probe": _G6_PROBE, "cases": list(cases.values())}).encode("ascii")
    try:
        done = subprocess.run([sys.executable, "-c", _G6_CHILD], input=sent, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=_G6_SECONDS)
        said = json.loads(done.stdout.decode("ascii"))
    except subprocess.TimeoutExpired:                 # run kills the process before it raises this
        return None, f"the probe's process ran past {_G6_SECONDS} seconds"
    except (OSError, ValueError):                     # no process, or no JSON: a UnicodeError is a ValueError
        said = None
    if isinstance(said, dict) and isinstance(said.get("raised"), str):
        return None, f"the probe raised {said['raised']}"
    seen = said.get("seen") if isinstance(said, dict) else None
    if not isinstance(seen, list) or len(seen) != len(cases) or not all(
            isinstance(one, dict) and ("seen" in one or isinstance(one.get("raised"), str)) for one in seen):
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
        pytest.skip(f"observe raised {one['raised']}")
    seen = one["seen"]
    names = [cell["observation"], *([cell["equals"]] if "equals" in cell else [])]
    if not isinstance(seen, dict) or any(name not in seen for name in names):
        pytest.skip("observe gave no such observation")
    # The tables' one rule (canon): an observation that is no JSON value, a number that is not finite, fails its cell.
    assert canon(seen[cell["observation"]]) == (canon(seen[cell["equals"]]) if "equals" in cell else cell["want"])
'''


def _case_id(number: int, name: str, case: Mapping[str, Any]) -> str:
    label = ",".join(f"{factor}={canon(value)}" for factor, value in case.items())
    return f"c{number:03d}-{name}-" + re.sub(r"[^A-Za-z0-9_.,=+-]+", "_", label)[:60]


def compile_test(probe: str, factors: Mapping[str, Sequence[Any]], agreed: Mapping[tuple[str, str], Any]) -> str:
    """One pytest file: G6's own text with ``canon`` in it, the probe as one string, sent with the cases to a process of its own
    and never with an expectation, the agreed cells as one JSON text read by ``json.loads``, each case as Python's reader gives
    it and each expectation by its ``canon``, then one test with a case for each cell, in the domain's order, observations by
    name. No other text of a model is in it."""
    cells = []
    for number, case in enumerate(cases(factors)):
        key = canon(case)
        for name in sorted(name for at, name in agreed if at == key):
            want = agreed[key, name]
            cells.append({"id": _case_id(number, name, case), "case": _python(case), "observation": name,
                          **({"equals": want["equals"]} if _equals(want) else {"want": canon(want)})})
    data = json.dumps(cells, sort_keys=True, ensure_ascii=True, allow_nan=False)
    return _HEAD + inspect.getsource(canon) + f"\n\n_G6_PROBE = {probe!r}\n_G6_CELLS = json.loads({data!r})\n\n" + _TEST


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
    """What reader 2 is given: the issue's text, and reader 1's factors, observations and probe. Never reader 1's rules."""
    return (f"<issue>\n{issue}\n</issue>\n\n<factors>\n{json.dumps(_python(model['factors']), indent=1)}\n</factors>\n\n"
            f"<observations>\n{json.dumps(model['observations'])}\n</observations>\n\n<probe>\n{model['probe']}\n</probe>\n")


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
    """G6's checks on reader 1's answer, with reader 2 asked where its model is valid: the bug's record, its ``status`` and, where
    the model stands, the compiled file as its test. A reader that failed is no answer, which replay 3 asks again."""
    found = dict(found)
    if r3.no_answer(found):
        return {**found, "status": "no_answer"}
    if not found.get("test"):
        return {**found, "status": "no_model"}
    issue = f"{title}\n\n{text}"
    record = {**found, "model": found["test"], "test": ""}
    model, why = parse_model(found["test"])
    if model is None:
        return _closed(record, "invalid", why)
    first, dropped = kept_rows(model["rules"], issue)
    record |= {"tables": {"reader_1": _python(first)}, "dropped": {"reader_1": dropped}}
    one = table(first, model["factors"])
    if one is None:
        return _closed(record, "conflict", "reader 1's table gives one case two values for one observation")
    try:
        reply = caller.complete(READER_2_SYSTEM, reader_2_text(issue, model))
    except (RuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        # an LLMError is a RuntimeError, and a text its call cannot encode or decode a UnicodeError
        return {**record, "status": "no_answer", "error": f"reader 2: {type(exc).__name__}: {exc}"[:400]}
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


def review_bug(ctx: we.Context, root: Path, task: we.Task, rows: Sequence[Mapping[str, Any]], title: str, text: str, *,
               step: Callable[..., Any] | None = None, caller: Any = None, run: r3.Run | None = None) -> list[dict[str, Any]]:
    """Replay 3's ``review_bug`` under ``FORMAL``, unchanged, with ``ask`` as its reviewer: the bug's record saved whole before
    anything runs, then the compiled file run on the parent and on each patch by ``run_on``."""
    asking = functools.partial(ask, caller=caller or reader_2(), step=step or fr._step, root=root)
    return r3.review_bug(ctx, root, task, rows, title, text, ask=asking, run=run, ins=FORMAL)


# --- the counts --------------------------------------------------------------------------------------------------------------
def summary(root: Path, tasks: Sequence[str]) -> dict[str, Any]:
    """Prediction 5's counts, and nothing of any text: each bug's status and whether its two readers disagreed (ambiguous, or any
    cell both cover differs; None where the tables were never compared), by status, and in all. A bug with no record whose
    attempts ended in no answer (``no_answers``) is ``no_answer``; one never attempted is "no record"."""
    bugs = []
    for task in tasks:
        found = (fr.read(r3.bug_record(root, task, FORMAL)) or {}).get("found")
        if not isinstance(found, dict):
            bugs.append({"task": task, "status": "no_answer" if no_answers(root, task) else "no record", "disagreed": None})
            continue
        status, compared = str(found.get("status")), found.get("comparison")
        disagreed = (status == "ambiguous" or bool(compared.get("cells_differ"))) if isinstance(compared, dict) else None
        bugs.append({"task": task, "status": status, "disagreed": disagreed})
    return {"bugs": bugs, "by_status": dict(sorted(Counter(bug["status"] for bug in bugs).items())),
            "compared": sum(bug["disagreed"] is not None for bug in bugs), "disagreed": sum(bug["disagreed"] is True for bug in bugs)}


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
