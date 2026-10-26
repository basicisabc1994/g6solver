# SPDX-License-Identifier: Apache-2.0
"""g6.lean's source screen (owner, 2026-10-20; audit 017 FM-001): what it refuses before Lean runs, each finding with
its own code and its line and column, and what it lets through. Pure Python; no Lean is needed.

The screen is not the proof of soundness. It reads the whole text, comments and strings included, so nothing can hide
where the screen and Lean read differently; Lean's own report of the theorem's axioms is the real guard
(tests/lean/test_lean_output.py and, with Lean installed, tests/lean/test_check_proof.py)."""
from __future__ import annotations

import pytest

from g6.lean import screen
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
    SYNTAX_EXTENSION,
)

GOOD = """import Std
import Init.Data.List.Basic

/-- Two and two. -/
@[simp] theorem two_add : 2 + 2 = 4 := rfl

namespace Demo
set_option maxHeartbeats 400000 in
theorem em_use (p : Prop) : p ∨ ¬p := Classical.em p
attribute [local simp] Nat.add_comm
attribute [-simp] Nat.add_comm
set_option linter.unusedVariables false
set_option autoImplicit false
#check em_use
#check_failure (1 : String)
#reduce 2 + 2
#synth Inhabited Nat
instance (priority := low) natFive : Inhabited Nat := ⟨5⟩
structure Point where
  x : Nat
  deriving Repr, BEq
def xs' : Array Nat := #[1, 2, 3]
theorem uses_prime (stopwatch isSorry : Nat) (h : stopwatch = isSorry) : isSorry = stopwatch := h.symm
theorem range_end (r : Std.Range) : r.stop = r.stop := rfl
@[reducible, inline] def double (n : Nat) : Nat := 2 * n
end Demo
"""


def _at(source: str, needle: str) -> tuple[int, int]:
    """The line (from 1) and column (from 0, as Lean counts) where ``needle`` first appears."""
    index = source.index(needle)
    return source.count("\n", 0, index) + 1, index - (source.rfind("\n", 0, index) + 1)


def test_a_plain_proof_passes_the_screen() -> None:
    assert screen.screen(GOOD) == []


REFUSED: list[tuple[str, str, str, str]] = [  # source, code, construct, where the finding points
    ("theorem t : 1 = 2 := sorry", SORRY, "sorry", "sorry"),
    ("theorem t : 1 = 2 := by\n  admit", SORRY, "admit", "admit"),
    ("theorem t : 1 = 2 := sorryAx _ false", SORRY, "sorryAx", "sorryAx"),
    ("theorem t : 1 = 2 := by\n  stop\n  rfl", SORRY, "stop", "stop"),
    ("theorem t : 1 = 2 := «sorry»", SORRY, "sorry", "sorry"),
    ("-- sorry, not finished\ntheorem t : True := trivial", SORRY, "sorry", "sorry"),
    ('#eval IO.println "x"', CODE_EXECUTION, "#eval", "#eval"),
    ("#eval! 1", CODE_EXECUTION, "#eval!", "#eval!"),
    ("run_cmd pure ()", CODE_EXECUTION, "run_cmd", "run_cmd"),
    ("theorem t : True := by\n  run_tac pure ()", CODE_EXECUTION, "run_tac", "run_tac"),
    ("def f : IO Unit := pure ()", CODE_EXECUTION, "IO", "IO"),
    ("def f : BaseIO Unit := pure ()", CODE_EXECUTION, "BaseIO", "BaseIO"),
    ("unsafe def f : Nat := 1", CODE_EXECUTION, "unsafe", "unsafe"),
    ("def f : Nat := unsafeCast 1", CODE_EXECUTION, "unsafeCast", "unsafeCast"),
    ("@[implemented_by g] def f : Nat := 1", CODE_EXECUTION, "implemented_by", "implemented_by"),
    ('@[extern "c_f"] def f : Nat := 1', CODE_EXECUTION, "extern", "extern"),
    ("initialize pure ()", CODE_EXECUTION, "initialize", "initialize"),
    ("theorem t : 10 < 20 := by native_decide", CODE_EXECUTION, "native_decide", "native_decide"),
    ("theorem t : 10 < 20 := by decide +native", CODE_EXECUTION, "+native", "+native"),
    ("theorem t : 10 < 20 := Lean.ofReduceBool _ _ rfl", CODE_EXECUTION, "ofReduceBool", "ofReduceBool"),
    ("axiom cheat : False\ntheorem t : 1 = 2 := cheat.elim", AXIOM_DECLARED, "axiom", "axiom"),
    ("opaque secret : Nat", AXIOM_DECLARED, "opaque", "opaque"),
    ('macro "go" : tactic => `(tactic| rfl)', SYNTAX_EXTENSION, "macro", "macro"),
    ('syntax "go" : tactic', SYNTAX_EXTENSION, "syntax", "syntax"),
    ("macro_rules\n  | `(tactic| go) => `(tactic| rfl)", SYNTAX_EXTENSION, "macro_rules", "macro_rules"),
    ('notation "TT" => True', SYNTAX_EXTENSION, "notation", "notation"),
    ('infixl:65 " +++ " => Nat.add', SYNTAX_EXTENSION, "infixl", "infixl"),
    ('elab "go" : tactic => pure ()', SYNTAX_EXTENSION, "elab", "elab"),
    ("def q := `(1 + 1)", SYNTAX_EXTENSION, "`(", "`("),
    ("@[app_unexpander Liar] def u : Lean.PrettyPrinter.Unexpander := fun _ => throw ()", ATTRIBUTE_REFUSED,
     "app_unexpander", "app_unexpander"),
    ("attribute [delab app.Liar] u", ATTRIBUTE_REFUSED, "delab", "delab"),
    ("@[simp, csimp] theorem t : True := trivial", ATTRIBUTE_REFUSED, "csimp", "csimp"),
    ("@[simp theorem t : True := trivial", ATTRIBUTE_REFUSED, "@[", "@["),
    ("set_option pp.all true", OPTION_REFUSED, "pp.all", "set_option"),
    ("set_option maxHeartbeats 0 in\ntheorem t : True := trivial", OPTION_REFUSED, "maxHeartbeats", "set_option"),
    ("set_option maxHeartbeats 99999999 in\ntheorem t : True := trivial", OPTION_REFUSED, "maxHeartbeats",
     "set_option"),
    ("set_option maxRecDepth 1000000 in\ntheorem t : True := trivial", OPTION_REFUSED, "maxRecDepth", "set_option"),
    ("set_option autoImplicit 3", OPTION_REFUSED, "autoImplicit", "set_option"),
    ("set_option warningAsError true", OPTION_REFUSED, "warningAsError", "set_option"),
    ("import Mathlib", IMPORT_REFUSED, "Mathlib", "Mathlib"),
    ("import Lean", IMPORT_REFUSED, "Lean", "Lean"),
    ("import Std Lean.Elab", IMPORT_REFUSED, "Lean.Elab", "Lean.Elab"),
    ("/- a header comment -/ import Lean", IMPORT_REFUSED, "Lean", "Lean"),
    ("import\n  Lean", IMPORT_REFUSED, "import", "import"),
    ("prelude\ntheorem t : True := trivial", IMPORT_REFUSED, "prelude", "prelude"),
    ("#print axioms two_add", COMMAND_REFUSED, "#print", "#print"),
    ("theorem t : True := trivial\n#exit", COMMAND_REFUSED, "#exit", "#exit"),
    ("/-- error: x -/\n#guard_msgs in\ntheorem t : 1 = 2 := rfl", COMMAND_REFUSED, "#guard_msgs", "#guard_msgs"),
    ("-- G6_LEAN_CHECK_0_BEGIN\ntheorem t : True := trivial", MARKER_IN_SOURCE, "G6_LEAN_CHECK", "G6_LEAN_CHECK"),
    ("theorem t : True := trivial ‮", SOURCE_REFUSED, "U+202E", "‮"),
    ("theorem t​ : True := trivial", SOURCE_REFUSED, "U+200B", "​"),
    ("theorem t : True := trivial\x00", SOURCE_REFUSED, "U+0000", "\x00"),
    ("theorem t : True := trivial\rtheorem u : True := trivial", SOURCE_REFUSED, "U+000D", "\r"),
]


@pytest.mark.parametrize(("source", "code", "construct", "where"), REFUSED,
                         ids=[f"{case[2]!r}-{i}" for i, case in enumerate(REFUSED)])
def test_each_refused_construct_has_its_own_code_and_place(source: str, code: str, construct: str, where: str) -> None:
    found = screen.screen(source)
    assert found, f"the screen let {construct!r} through"
    first = found[0]
    assert (first["code"], first["construct"]) == (code, construct), found
    assert (first["line"], first["column"]) == _at(source, where), found
    assert first["reason"] and first["text"]


def test_every_finding_is_reported_in_the_order_it_appears() -> None:
    source = "import Lean\naxiom a : False\ntheorem t : True := by\n  sorry\n#print t\n"
    found = screen.screen(source)
    assert [(f["code"], f["line"]) for f in found] == [(IMPORT_REFUSED, 1), (AXIOM_DECLARED, 2), (SORRY, 4),
                                                       (COMMAND_REFUSED, 5)]


def test_a_crlf_line_ending_is_not_a_control_character() -> None:
    assert screen.screen(GOOD.replace("\n", "\r\n")) == []


def test_an_attribute_that_runs_code_is_reported_once_with_the_more_specific_code() -> None:
    found = screen.screen("@[implemented_by g] def f : Nat := 1")
    assert [f["code"] for f in found] == [CODE_EXECUTION]


def test_the_lists_the_screen_allows_are_small_and_named() -> None:
    assert set(screen.ALLOWED_IMPORTS) == {"Init", "Std"}
    assert set(screen.ALLOWED_COMMANDS) == {"#check", "#check_failure", "#reduce", "#synth"}
    assert "maxHeartbeats" in screen.ALLOWED_OPTIONS and not any(o.startswith("pp.") for o in screen.ALLOWED_OPTIONS)
    assert "simp" in screen.ALLOWED_ATTRIBUTES
    assert not {"app_unexpander", "delab", "implemented_by", "extern", "csimp", "macro", "export"} & set(
        screen.ALLOWED_ATTRIBUTES)


# --- the expected statement -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("statement", ["2 + 2 = 4", "∀ (n : Nat), n + 0 = n", "∀ {α : Type} (xs : List α), xs ++ [] = xs",
                                       "∀ (p : Prop), p ∨ ¬p", "(2 : Fin 3) + 2 = 4", "∀ (n : Nat),\n  n + 0 = n",
                                       "∃ x, ⟨x, 1⟩ = (⟨2, 1⟩ : Nat × Nat)", "#[1, 2].size = 2"])
def test_a_statement_that_is_one_term_passes(statement: str) -> None:
    assert screen.screen_statement(statement) == []


@pytest.mark.parametrize(("statement", "construct"), [
    ("True := trivial\nexample : False", "example"),
    ("True) := trivial theorem t : (False", ")"),
    ("(True", "("),
    ("True ]", "]"),
    ('"a" = "a"', '"'),
    ("True -- comment", "--"),
    ("True /- comment -/", "/-"),
    ("True #print axioms t", "#print"),
    ("1 = 2 := sorry", "sorry"),
    ("open Nat in succ 0 = 1", "open"),
    ("set_option pp.all true in True", "set_option"),
])
def test_a_statement_that_could_carry_a_command_is_refused(statement: str, construct: str) -> None:
    found = screen.screen_statement(statement)
    assert found and construct in [f["construct"] for f in found], found


# --- declarations: which theorem the source declares ---------------------------------------------------------------

def _names(source: str) -> dict[str, str]:
    return {d["name"]: d["kind"] for d in screen.declarations(source)}


def test_declarations_are_read_with_their_namespaces() -> None:
    names = _names(GOOD)
    assert names["two_add"] == "theorem" and names["Demo.em_use"] == "theorem"
    assert names["Demo.xs'"] == "def" and names["Demo.natFive"] == "instance" and names["Demo.Point"] == "structure"
    assert names["Demo.double"] == "def"


def test_namespaces_sections_and_root_names() -> None:
    source = """namespace A.B
theorem one : True := trivial
end A.B
namespace A
namespace B
private theorem two : True := trivial
end B
section Inner
protected theorem three : True := trivial
theorem _root_.four : True := trivial
end Inner
noncomputable section
theorem C.five : True := trivial
end
mutual
theorem six : True := trivial
end
end A
theorem seven : True := trivial
abbrev eight : Nat := 8
class inductive Nine | a
"""
    assert _names(source) == {"A.B.one": "theorem", "A.B.two": "theorem", "A.three": "theorem", "four": "theorem",
                              "A.C.five": "theorem", "A.six": "theorem", "seven": "theorem", "eight": "abbrev",
                              "Nine": "inductive"}


def test_comments_and_strings_declare_nothing() -> None:
    source = '-- theorem a : True := trivial\n/- theorem b : True\n/- nested -/ theorem c -/\n' \
             'def s : String := "theorem d : True"\ndef ch : Char := \'"\'\ntheorem e : True := trivial\n'
    assert _names(source) == {"s": "def", "ch": "def", "e": "theorem"}


def test_the_header_ends_after_the_last_import() -> None:
    assert screen.header_lines("theorem t : True := trivial\n") == 0
    assert screen.header_lines("import Std\n\ntheorem t : True := trivial\n") == 1
    assert screen.header_lines("/-! A module doc. -/\nimport Std\nimport Init.Data.List\n-- note\ntheorem t : True := "
                               "trivial\n") == 3
