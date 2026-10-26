# SPDX-License-Identifier: Apache-2.0
"""What g6.lean refuses before Lean runs, and which declarations a source makes, in pure Python; nothing here runs a
program.

:func:`screen` reads the whole text, comments and strings included, so nothing can hide where this screen and Lean's
own reader would disagree; a word in a comment is refused like one in code. It refuses, each with its own code and the
line (from 1) and column (from 0, as Lean counts):

* ``G6_E_LEAN_SORRY``: ``sorry``, ``admit``, ``sorryAx`` and the ``stop`` tactic, which leaves the rest of a proof as
  ``sorry`` (measured with Lean 4.22.0: a warning only, exit code 0);
* ``G6_E_LEAN_CODE_EXECUTION``: ``#eval``, ``#guard``, ``run_cmd``, ``run_tac``, ``run_elab``, ``run_meta``, the IO
  monad (``IO``, ``EIO``, ``BaseIO``), anything ``unsafe``, ``implemented_by``, ``extern``, ``initialize``,
  ``native_decide``, ``decide +native`` and ``Lean.ofReduceBool``: they compile and run code, or trust its answer;
* ``G6_E_LEAN_AXIOM_DECLARED``: ``axiom`` and ``opaque`` declarations, which let a file assume anything;
* ``G6_E_LEAN_SYNTAX_EXTENSION``: ``macro``, ``macro_rules``, ``syntax``, ``elab``, ``elab_rules``, ``notation``,
  ``infix``, ``infixl``, ``infixr``, ``prefix``, ``postfix``, ``declare_syntax_cat``, ``binder_predicate`` and syntax
  quotations, which change how Lean reads the file and how it prints a statement;
* ``G6_E_LEAN_ATTRIBUTE_REFUSED``: any attribute outside :data:`ALLOWED_ATTRIBUTES`. An ``app_unexpander`` written with
  nothing but ``Init`` made ``¬ Liar 3`` (true) print as ``¬3 = 3`` (which reads as false), measured with Lean 4.22.0;
* ``G6_E_LEAN_OPTION_REFUSED``: ``set_option`` outside :data:`ALLOWED_OPTIONS` and their caps (``pp.*`` would change
  what G6 reads; ``maxHeartbeats 0`` removes Lean's own limit);
* ``G6_E_LEAN_IMPORT_REFUSED``: ``import`` of anything but ``Init`` and ``Std``, which ship with Lean, and ``prelude``,
  which drops ``Init`` so a file can define its own ``Eq``;
* ``G6_E_LEAN_COMMAND_REFUSED``: every ``#`` command except :data:`ALLOWED_COMMANDS`: ``#print`` (G6 adds its own),
  ``#exit`` (stops before G6's checks), ``#guard_msgs`` (can swallow Lean's messages) and the rest;
* ``G6_E_LEAN_MARKER_IN_SOURCE``: the text G6 marks its own checks with;
* ``G6_E_LEAN_SOURCE_REFUSED``: control, format and private-use characters (a bidirectional override or a zero-width
  space shows a reader something other than what Lean reads), and a carriage return outside a CRLF line ending.

This is a screen, not the proof of soundness: Lean's ``#print axioms`` on the theorem is the real guard
(:mod:`g6.lean.lean_output`). :func:`screen_statement` is the stricter screen for an expected statement, which G6
writes into its own ``example`` line: one term, no comments, strings, commands or unbalanced brackets.
:func:`declarations` and :func:`header_lines` read the code with comments and strings removed.
"""
from __future__ import annotations

import bisect
import re
import unicodedata
from typing import Any

from g6.lean.envelope import (
    ATTRIBUTE_REFUSED,
    AXIOM_DECLARED,
    CODE_EXECUTION,
    COMMAND_REFUSED,
    IMPORT_REFUSED,
    MARKER_IN_SOURCE,
    OPTION_REFUSED,
    SORRY,
    SOURCE_REFUSED,
    STATEMENT_REFUSED,
    SYNTAX_EXTENSION,
)

#: The text G6 marks its own checks with; a source may not contain it.
MARK_PREFIX = "G6_LEAN_CHECK"
#: Modules a source may import (each with its submodules): both ship with Lean.
ALLOWED_IMPORTS = ("Init", "Std")
#: The ``#`` commands a source may use: they only print, and only on the source's own lines.
ALLOWED_COMMANDS = ("#check", "#check_failure", "#reduce", "#synth")
#: Attributes a source may use: none of them runs code, changes how Lean prints, or changes how numerals elaborate.
ALLOWED_ATTRIBUTES = ("simp", "reducible", "irreducible", "semireducible", "inline", "noinline", "specialize", "ext",
                      "refl", "symm", "trans", "elab_as_elim", "induction_eliminator", "cases_eliminator",
                      "match_pattern", "instance", "grind", "norm_cast", "push_cast")
#: Options a source may set, with the range a number must lie in (``None``: true or false). ``linter.*`` options may be
#: set true or false too. Lean's defaults are maxHeartbeats 200000 and maxRecDepth 512.
ALLOWED_OPTIONS: dict[str, tuple[int, int] | None] = {
    "maxHeartbeats": (1, 1_000_000), "maxRecDepth": (1, 10_000), "synthInstance.maxHeartbeats": (1, 1_000_000),
    "synthInstance.maxSize": (1, 10_000), "autoImplicit": None, "relaxedAutoImplicit": None,
}
MAX_FINDINGS = 100

_NAME = r"(?<![\w'])"        # a name, qualified or not: Lean.ofReduceBool, Foo.IO
_KEY = r"(?<![\w'.])"        # a keyword, never after a dot: r.stop is a field, stop is the tactic
_END = r"(?![\w'!?])"

_WORDS: list[tuple[str, re.Pattern[str], str]] = [(code, re.compile(pattern), why) for code, pattern, why in [
    (SORRY, rf"{_NAME}(?:sorry|sorryAx){_END}", "leaves a goal unproved, which Lean accepts with only a warning"),
    (SORRY, rf"{_KEY}admit{_END}", "leaves a goal unproved, which Lean accepts with only a warning"),
    (SORRY, rf"{_KEY}stop{_END}", "is the stop tactic, which leaves the rest of the proof as sorry"),
    (CODE_EXECUTION, rf"{_KEY}run_(?:cmd|tac|elab|meta){_END}", "runs code while Lean reads the file"),
    (CODE_EXECUTION, rf"{_NAME}(?:IO|EIO|BaseIO){_END}", "is the IO monad, for programs that act on your machine"),
    (CODE_EXECUTION, rf"{_NAME}unsafe[\w'!?]*", "is unsafe code, which Lean does not check"),
    (CODE_EXECUTION, rf"{_KEY}(?:implemented_by|extern){_END}", "swaps a definition for code Lean runs unchecked"),
    (CODE_EXECUTION, rf"{_KEY}(?:builtin_)?initialize{_END}", "runs code when the file is loaded"),
    (CODE_EXECUTION, rf"{_NAME}native_decide{_END}|\+\s*native{_END}|{_KEY}native\s*:=",
     "compiles code, runs it and trusts its answer (the axiom Lean.ofReduceBool)"),
    (CODE_EXECUTION, rf"{_NAME}(?:of)?[Rr]educe(?:Bool|Nat){_END}", "trusts compiled code's answer as a proof"),
    (AXIOM_DECLARED, rf"{_KEY}(?:axiom|opaque){_END}", "declares a constant without a proof, so a file can assume "
                                                        "anything, False included"),
    (SYNTAX_EXTENSION, rf"{_KEY}(?:macro_rules|macro|syntax|elab_rules|elab|notation3|notation|infixl|infixr|infix|"
                       rf"prefix|postfix|declare_syntax_cat|binder_predicate){_END}",
     "extends Lean's syntax, which changes how the file is read and how a statement is printed"),
    (SYNTAX_EXTENSION, r"`\(", "is a syntax quotation, which macros and printers are built from"),
    (IMPORT_REFUSED, rf"{_KEY}prelude{_END}", "drops Init, so the file could define its own Eq, Nat or True"),
    (MARKER_IN_SOURCE, re.escape(MARK_PREFIX), "is the text G6 marks its own checks with"),
]]
_HASH = re.compile(r"(?<![\w'#])#[A-Za-z_][\w!?]*")
_RUNS_CODE = frozenset({"#eval", "#eval!", "#guard"})
_OPTION = re.compile(rf"{_KEY}set_option{_END}")
_IMPORT = re.compile(rf"{_KEY}import{_END}")
_ATTRIBUTE = re.compile(r"@\[|(?<![\w'.])attribute\s*\[")
_ATTRIBUTE_NAME = re.compile(r"\s*(?:-\s*)?(?:(?:local|scoped)\s+)?([A-Za-z_][\w.!?]*)")
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Zl", "Zp"})
_ORDER = {code: i for i, code in enumerate([SOURCE_REFUSED, MARKER_IN_SOURCE, SORRY, CODE_EXECUTION, AXIOM_DECLARED,
                                             SYNTAX_EXTENSION, IMPORT_REFUSED, COMMAND_REFUSED, OPTION_REFUSED,
                                             ATTRIBUTE_REFUSED, STATEMENT_REFUSED])}


class _Lines:
    def __init__(self, text: str) -> None:
        self.text = text
        self.starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def place(self, index: int) -> tuple[int, int]:
        row = bisect.bisect_right(self.starts, index) - 1
        return row + 1, index - self.starts[row]

    def finding(self, code: str, construct: str, index: int, why: str) -> dict[str, Any]:
        line, column = self.place(index)
        end = self.text.find("\n", self.starts[line - 1])
        shown = "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in _BAD_CATEGORIES else c
                        for c in self.text[self.starts[line - 1]:None if end < 0 else end]).strip()
        return {"code": code, "construct": construct, "line": line, "column": column, "text": shown[:200],
                "reason": f"{construct} at line {line}, column {column} {why}"}


def _characters(lines: _Lines) -> list[dict[str, Any]]:
    found, text = [], lines.text
    for i, ch in enumerate(text):
        if ch in "\t\n" or (ch == "\r" and text[i + 1:i + 2] == "\n"):
            continue
        if unicodedata.category(ch) in _BAD_CATEGORIES:
            found.append(lines.finding(SOURCE_REFUSED, f"U+{ord(ch):04X}", i, "is a control or invisible character: "
                                       "a reader would not see what Lean reads"))
            if len(found) >= 20:
                break
    return found


def _options(lines: _Lines) -> list[dict[str, Any]]:
    found, text = [], lines.text
    allowed = ", ".join(f"{n} ({'true or false' if r is None else f'{r[0]} to {r[1]}'})"
                        for n, r in ALLOWED_OPTIONS.items()) + " and linter options (true or false)"
    for match in _OPTION.finditer(text):
        rest = re.match(r"[ \t]+(\S+)(?:[ \t]+(\S+))?", text[match.end():])
        name, value = (rest.group(1), rest.group(2) or "") if rest else ("set_option", "")
        bounds = ALLOWED_OPTIONS.get(name, False)
        if bounds is None or (bounds is False and name.startswith("linter.")):
            ok = value in ("true", "false")
        elif bounds:
            ok = value.isdigit() and bounds[0] <= int(value) <= bounds[1]
        else:
            ok = False
        if not ok:
            found.append(lines.finding(OPTION_REFUSED, name, match.start(),
                                       f"is set to {value or 'nothing'}, outside what G6 allows: {allowed}"))
    return found


def _imports(lines: _Lines) -> list[dict[str, Any]]:
    found, text = [], lines.text
    for match in _IMPORT.finditer(text):
        end = text.find("\n", match.end())
        rest = text[match.end():None if end < 0 else end]
        rest = re.split(r"--|/-", rest, maxsplit=1)[0]
        modules = list(re.finditer(r"\S+", rest))
        if not modules:
            found.append(lines.finding(IMPORT_REFUSED, "import", match.start(), "names no module on its own line"))
        for module in modules:
            name = module.group()
            if not any(name == m or name.startswith(m + ".") for m in ALLOWED_IMPORTS):
                found.append(lines.finding(IMPORT_REFUSED, name, match.end() + module.start(),
                                           "is not Init or Std, the libraries that ship with Lean"))
    return found


def _closing(text: str, start: int, limit: int = 2000) -> int:
    depth = 1
    for i in range(start, min(len(text), start + limit)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _attributes(lines: _Lines) -> list[dict[str, Any]]:
    found, text = [], lines.text
    allowed = ", ".join(ALLOWED_ATTRIBUTES)
    for match in _ATTRIBUTE.finditer(text):
        close = _closing(text, match.end())
        if close < 0:
            found.append(lines.finding(ATTRIBUTE_REFUSED, "@[" if match.group() == "@[" else "attribute",
                                       match.start(), "opens an attribute list that does not close"))
            continue
        at, depth = match.end(), 0
        for i in range(match.end(), close + 1):
            ch = text[i] if i < close else ","
            depth += ch in "([{"
            depth -= ch in ")]}"
            if ch == "," and depth <= 0:
                entry = _ATTRIBUTE_NAME.match(text, at)
                if not entry or entry.end() > i:
                    found.append(lines.finding(ATTRIBUTE_REFUSED, text[at:i].strip()[:40] or "[]", at,
                                               f"is not an attribute G6 reads; allowed: {allowed}"))
                elif entry.group(1) not in ALLOWED_ATTRIBUTES:
                    found.append(lines.finding(ATTRIBUTE_REFUSED, entry.group(1), entry.start(1),
                                               f"is an attribute outside those G6 allows: {allowed}"))
                at = i + 1
    return found


def _sorted(found: list[dict[str, Any]], *, dedupe: bool = True) -> list[dict[str, Any]]:
    """By place, the more specific code first; one finding per place when ``dedupe``; at most MAX_FINDINGS."""
    found = sorted(found, key=lambda f: (f["line"], f["column"], _ORDER.get(f["code"], 99)))
    if dedupe:
        seen, kept = set(), []
        for f in found:
            if (f["line"], f["column"]) not in seen:
                seen.add((f["line"], f["column"]))
                kept.append(f)
        found = kept
    return found[:MAX_FINDINGS]


def screen(source: str) -> list[dict[str, Any]]:
    """Everything in ``source`` G6 refuses before Lean runs, in the order it appears; ``[]`` when nothing is."""
    lines = _Lines(source)
    found = _characters(lines)
    for code, pattern, why in _WORDS:
        found += [lines.finding(code, m.group(), m.start(), why) for m in pattern.finditer(source)]
    for match in _HASH.finditer(source):
        command = match.group()
        if command in _RUNS_CODE:
            found.append(lines.finding(CODE_EXECUTION, command, match.start(), "runs code while Lean reads the file"))
        elif command not in ALLOWED_COMMANDS:
            found.append(lines.finding(COMMAND_REFUSED, command, match.start(),
                                       f"is a command G6 does not run; allowed: {', '.join(ALLOWED_COMMANDS)}"))
    return _sorted(found + _options(lines) + _imports(lines) + _attributes(lines))


# --- the expected statement ------------------------------------------------------------------------------------------

_COMMAND_WORDS = re.compile(
    rf"{_KEY}(?:theorem|lemma|def|example|abbrev|instance|structure|class|inductive|coinductive|axiom|opaque|namespace|"
    rf"section|end|open|variable|universe|set_option|attribute|import|mutual|deriving|noncomputable|private|protected|"
    rf"partial|unsafe|export|omit|include|initialize|local|scoped|macro|syntax|notation|elab|prelude){_END}")
_PAIRS = {"(": ")", "[": "]", "{": "}", "⟨": "⟩", "⦃": "⦄"}


def screen_statement(statement: str) -> list[dict[str, Any]]:
    """Everything that stops ``statement`` being written into G6's ``example`` line as one term; ``[]`` when nothing
    does. Each finding keeps its own code and says ``where``: ``expected_statement``."""
    lines = _Lines(statement)
    found = screen(statement)
    one_term = "is not part of one term, and the expected statement must be exactly one"
    for match in re.finditer(r'"|--|/-|(?<![\w\'#])#[A-Za-z_][\w!?]*', statement):
        found.append(lines.finding(STATEMENT_REFUSED, match.group(), match.start(), one_term))
    found += [lines.finding(STATEMENT_REFUSED, m.group(), m.start(), "is a command word: " + one_term)
              for m in _COMMAND_WORDS.finditer(statement)]
    stack: list[tuple[str, int]] = []
    for i, ch in enumerate(statement):
        if ch in _PAIRS:
            stack.append((ch, i))
        elif ch in _PAIRS.values():
            if not stack or _PAIRS[stack[-1][0]] != ch:
                found.append(lines.finding(STATEMENT_REFUSED, ch, i, "closes a bracket that is not open"))
                break
            stack.pop()
    if stack and not any(f["construct"] in _PAIRS.values() for f in found):
        found.append(lines.finding(STATEMENT_REFUSED, stack[-1][0], stack[-1][1], "opens a bracket that never closes"))
    return [dict(f, where="expected_statement") for f in _sorted(found, dedupe=False)]


# --- declarations ----------------------------------------------------------------------------------------------------

_IDENT_PART = r"(?:«[^»\n]*»|[^\W\d][\w'!?]*)"
_IDENT = re.compile(rf"{_IDENT_PART}(?:\.{_IDENT_PART})*")
_CHAR = re.compile(r"'(?:\\(?:u\{[0-9a-fA-F]+\}|x[0-9a-fA-F]{2}|.)|[^\\'\n])'")
_RAW = re.compile(r'r(#*)"')
_DECLARING = frozenset({"theorem", "def", "abbrev", "instance", "structure", "class", "inductive", "axiom", "opaque"})
_KEYWORDS = _DECLARING | {"example", "namespace", "section", "end", "mutual", "open", "variable", "universe",
                          "set_option", "attribute", "noncomputable", "private", "protected", "partial", "unsafe",
                          "import", "deriving", "where", "with", "at", "by", "fun", "have", "show", "from", "let", "in",
                          "if", "then", "else", "do", "match", "prelude", "nonrec"}


def _string_end(text: str, i: int, interpolated: bool) -> int:
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if depth == 0:
            if ch == '"':
                return i + 1
            if interpolated and ch == "{":
                depth = 1
        elif ch == '"':
            i = _string_end(text, i + 1, False)
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return len(text)


def _tokens(text: str) -> list[tuple[str, int]]:
    """The code's tokens with where each starts: comments, strings and characters removed, names kept whole."""
    out: list[tuple[str, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        after_name = i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_'!?")
        if ch.isspace():
            i += 1
        elif text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/-", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/-", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("-/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
        elif ch == '"':
            i = _string_end(text, i + 1, i > 0 and text[i - 1] == "!")
        elif ch == "r" and not after_name and (raw := _RAW.match(text, i)):
            close = '"' + raw.group(1)
            end = text.find(close, raw.end())
            i = n if end < 0 else end + len(close)
        elif ch == "'" and not after_name and (char := _CHAR.match(text, i)):
            i = char.end()
        elif not after_name and (name := _IDENT.match(text, i)):
            out.append((name.group(), i))
            i = name.end()
        else:
            out.append((ch, i))
            i += 1
    return out


def _is_name(token: str) -> bool:
    return bool(_IDENT.fullmatch(token)) and token not in _KEYWORDS


def _parts(name: str) -> list[str]:
    return [p.strip("«»") for p in re.findall(_IDENT_PART, name)]


def declarations(source: str) -> list[dict[str, Any]]:
    """Each named declaration in ``source`` with its kind (``theorem``, ``def``, ...), full name (namespaces
    included, ``_root_.`` honoured) and place. Comments and strings declare nothing."""
    lines, tokens = _Lines(source), _tokens(source)
    stack: list[tuple[str, str | None]] = []
    found: list[dict[str, Any]] = []

    def named_here(j: int) -> bool:   # token j is a name on the same line as token j - 1
        return (j < len(tokens) and _is_name(tokens[j][0])
                and lines.place(tokens[j][1])[0] == lines.place(tokens[j - 1][1])[0])

    k = 0
    while k < len(tokens):
        word = tokens[k][0]
        if word == "namespace" and k + 1 < len(tokens) and _is_name(tokens[k + 1][0]):
            stack += [("namespace", p) for p in _parts(tokens[k + 1][0])]
            k += 2
        elif word in ("section", "end"):
            parts = _parts(tokens[k + 1][0]) if named_here(k + 1) else []
            if word == "section":
                stack += [("section", p) for p in parts] or [("section", None)]
            else:
                del stack[max(0, len(stack) - max(1, len(parts))):]
            k += 2 if parts else 1
        elif word == "mutual":
            stack.append(("mutual", None))
            k += 1
        elif word == "deriving":
            k += 2 if k + 1 < len(tokens) and tokens[k + 1][0] == "instance" else 1
        elif word in _DECLARING:
            kind, j = word, k + 1
            if word == "class" and j < len(tokens) and tokens[j][0] == "inductive":
                kind, j = "inductive", j + 1
            if word == "instance" and j < len(tokens) and tokens[j][0] == "(":
                depth = 0
                while j < len(tokens):
                    depth += {"(": 1, ")": -1}.get(tokens[j][0], 0)
                    j += 1
                    if depth == 0:
                        break
            if j < len(tokens) and _is_name(tokens[j][0]):
                name = tokens[j][0]
                parts = _parts(name)
                if parts and parts[0] == "_root_":
                    full = parts[1:]
                else:
                    full = [p for scope, p in stack if scope == "namespace" and p] + parts
                line, column = lines.place(tokens[j][1])
                found.append({"kind": kind, "name": ".".join(full), "line": line, "column": column})
                j += 1
            k = j
        else:
            k += 1
    return found


def header_lines(source: str) -> int:
    """How many lines the source's header (its imports) takes: G6 writes its own first line after them."""
    tokens, last = _tokens(source), -1
    k = 0
    while k < len(tokens) and tokens[k][0] == "import":
        k += 1
        while k < len(tokens) and _is_name(tokens[k][0]):
            last = tokens[k][1] + len(tokens[k][0])
            k += 1
    return 0 if last < 0 else _Lines(source).place(last - 1)[0]


__all__ = ["ALLOWED_ATTRIBUTES", "ALLOWED_COMMANDS", "ALLOWED_IMPORTS", "ALLOWED_OPTIONS", "MARK_PREFIX",
           "declarations", "header_lines", "screen", "screen_statement"]
