# SPDX-License-Identifier: Apache-2.0
"""Reading what Lean reports, in pure Python (owner, 2026-10-20; audit 017 FM-001). No Lean is needed: every message
below has the shape Lean 4.22.0 prints with ``--json`` (one JSON object per line), and the texts were recorded from
runs of it. The judge decides from Lean's own report of the theorem, read from the lines G6 appended, never from a
message the caller's source could print.

The file G6 builds is the caller's source with one line inserted after its imports (``#check_failure`` on the theorem,
which must not exist yet) and G6's block after it: a marker with a nonce the caller cannot know, ``#version``, ``#check``
twice (as printed, then with every name in full and every numeral's type), ``#print`` for the kind, ``#print axioms``,
an ``example`` when an expected statement is given, and the closing marker."""
from __future__ import annotations

import json

import pytest

from g6.lean import lean_output as lo
from g6.lean.envelope import (
    ELABORATION_FAILED,
    NONSTANDARD_AXIOM,
    NOT_A_THEOREM,
    OUTPUT_UNRECOGNISED,
    RUN_FAILED,
    SORRY,
    STATEMENT_MISMATCH,
    THEOREM_MISSING,
)

NONCE = "0123456789abcdef" * 2
SOURCE = "theorem two_add : 2 + 2 = 4 := rfl\n"


def _msg(line: int, data: str, *, severity: str = "information", column: int = 0, kind: str = "[anonymous]",
         end: tuple[int, int] | None = None, file: str = "Proof.lean") -> str:
    end_line, end_column = end or (line, column + 6)
    return json.dumps({"severity": severity, "pos": {"line": line, "column": column}, "kind": kind,
                       "keepFullRange": False, "isSilent": False, "fileName": file,
                       "endPos": {"line": end_line, "column": end_column}, "data": data, "caption": ""},
                      ensure_ascii=False)


def _layout(source: str = SOURCE, theorem: str = "two_add", expected: str = "") -> lo.Layout:
    return lo.build_file(source, theorem, expected, NONCE)[1]


def _output(layout: lo.Layout, *, statement: str = "2 + 2 = 4", explicit: str = "", kind: str = "",
            axioms: str = "", caller: tuple[str, ...] = (), skip: tuple[str, ...] = (),
            extra: dict[str, list[str]] | None = None) -> str:
    """What Lean prints for ``layout`` when everything is in order, with the parts a test changes."""
    name = layout.theorem
    say = {
        "pre": [_msg(layout.line("pre"), f"unknown identifier '_root_.{name}'", column=16, kind="unknownIdentifier")],
        "begin": [_msg(layout.line("begin"), f"{lo.MARK}{NONCE}_BEGIN")],
        "version": [_msg(layout.line("version"), "Lean 4.22.0\nTarget: x86_64-w64-windows-gnu Windows")],
        "check": [_msg(layout.line("check"), f"{name} : {statement}")],
        "explicit": [_msg(layout.line("explicit"), f"{name} : {explicit or statement}")],
        "kind": [_msg(layout.line("kind"), kind or f"@[defeq] theorem {name} : {statement} :=\n⋯")],
        "axioms": [_msg(layout.line("axioms"), axioms or f"'{name}' does not depend on any axioms")],
        "end": [_msg(layout.line("end"), f"{lo.MARK}{NONCE}_END")],
    }
    for role in skip:                                    # the default answer goes; any extra one stays
        say.pop(role, None)
    for role, lines in (extra or {}).items():
        say[role] = say.get(role, []) + lines
    out: list[str] = []
    for role in ("pre", "caller", "begin", "version", "check", "explicit", "kind", "axioms", "expected", "end"):
        out += list(caller) if role == "caller" else say.get(role, [])
    return "\n".join(out) + "\n"


def test_the_file_is_the_source_with_g6s_lines_around_it() -> None:
    source = "import Std\n\ntheorem two_add : 2 + 2 = 4 := rfl"
    text, layout = lo.build_file(source, "two_add", "2 + 2 = 4", NONCE)
    lines = text.split("\n")
    assert lines[0] == "import Std" and lines[1] == "#check_failure @_root_.two_add" and layout.line("pre") == 2
    assert lines[2:4] == ["", "theorem two_add : 2 + 2 = 4 := rfl"] and layout.caller_lines == 3
    assert [layout.caller_line(n) for n in (1, 3, 4)] == [1, 2, 3] and layout.caller_line(2) is None
    assert lines[layout.line("begin") - 1] == f'#print "{lo.MARK}{NONCE}_BEGIN"'
    assert lines[layout.line("check") - 1] == "#check @_root_.two_add"
    assert lines[layout.line("kind") - 1] == "set_option pp.proofs false in #print _root_.two_add"
    assert lines[layout.line("axioms") - 1] == "#print axioms _root_.two_add"
    assert lines[layout.line("expected") - 1] == "example : (2 + 2 = 4) := @_root_.two_add"
    assert lines[layout.line("end") - 1] == f'#print "{lo.MARK}{NONCE}_END"' and layout.line("end") > layout.line("check")
    assert "pp.fullNames true" in lines[layout.line("explicit") - 1]


def test_an_expected_statement_is_written_on_one_line() -> None:
    text, layout = lo.build_file(SOURCE, "two_add", "2 +\n  2 = 4", NONCE)
    assert text.split("\n")[layout.line("expected") - 1] == "example : (2 + 2 = 4) := @_root_.two_add"
    assert lo.build_file(SOURCE, "two_add", "", NONCE)[1].line("expected") is None


def test_a_clean_theorem_passes_with_lean_s_own_statement_and_axioms() -> None:
    layout = _layout()
    verdict = lo.judge(_output(layout), 0, layout)
    assert verdict["code"] == "", verdict["reason"]
    assert (verdict["statement"], verdict["axioms"], verdict["kind"]) == ("2 + 2 = 4", [], "theorem")
    assert (verdict["lean_version"], verdict["lean_target"]) == ("4.22.0", "x86_64-w64-windows-gnu Windows")
    assert verdict["statement_matched"] is None


def test_the_standard_axioms_pass_and_are_listed() -> None:
    layout = _layout("theorem em_use (p : Prop) : p ∨ ¬p := Classical.em p\n", "em_use")
    output = _output(layout, statement="∀ (p : Prop), p ∨ ¬p",
                     axioms="'em_use' depends on axioms: [propext, Classical.choice, Quot.sound]")
    verdict = lo.judge(output, 0, layout)
    assert verdict["code"] == "" and verdict["axioms"] == ["propext", "Classical.choice", "Quot.sound"]


def test_sorry_in_the_axioms_blocks_even_without_a_warning() -> None:
    layout = _layout()
    verdict = lo.judge(_output(layout, axioms="'two_add' depends on axioms: [sorryAx]"), 0, layout)
    assert verdict["code"] == SORRY and "sorryAx" in verdict["reason"] and verdict["axioms"] == ["sorryAx"]


def test_a_sorry_warning_anywhere_blocks_even_when_the_theorem_is_clean() -> None:
    # recorded: ``theorem viaStop : 1 = 2 := by stop rfl`` gives this warning and sorryAx, with exit code 0
    layout = _layout("theorem helper : 1 = 2 := by\n  stop\n  rfl\n" + SOURCE)
    line = layout.file_line(1)                       # G6's own line comes first: there are no imports
    assert line == 2
    warning = _msg(line, "declaration uses 'sorry'", severity="warning", column=8, kind="hasSorry", end=(line, 14))
    verdict = lo.judge(_output(layout, caller=(warning,)), 0, layout)
    assert verdict["code"] == SORRY and verdict["messages"][0]["kind"] == "hasSorry"


@pytest.mark.parametrize(("axioms", "named"), [
    ("[cheat]", "cheat"), ("[Lean.ofReduceBool]", "Lean.ofReduceBool"), ("[propext, myAxiom, Quot.sound]", "myAxiom"),
])
def test_any_other_axiom_blocks_and_is_named(axioms: str, named: str) -> None:
    layout = _layout()
    verdict = lo.judge(_output(layout, axioms=f"'two_add' depends on axioms: {axioms}"), 0, layout)
    assert verdict["code"] == NONSTANDARD_AXIOM and named in verdict["reason"]


@pytest.mark.parametrize("kind", ["@[defeq] def two_add : 2 + 2 = 4 :=\n⋯", "def two_add : Nat :=\n1",
                                  "@[reducible] def two_add : Nat :=\n2", "axiom two_add : 2 + 2 = 4",
                                  "opaque two_add : Nat", "constructor two_add : Nat → Nat"])
def test_a_constant_that_is_not_a_theorem_blocks(kind: str) -> None:
    layout = _layout()
    verdict = lo.judge(_output(layout, kind=kind), 0, layout)
    assert verdict["code"] == NOT_A_THEOREM and verdict["kind"] != "theorem"


def test_a_private_or_protected_theorem_is_a_theorem() -> None:
    for prefix in ("private ", "protected ", "@[simp] protected "):
        layout = _layout()
        verdict = lo.judge(_output(layout, kind=f"{prefix}theorem two_add : 2 + 2 = 4 :=\nrfl"), 0, layout)
        assert verdict["code"] == "" and verdict["kind"] == "theorem", (prefix, verdict["reason"])


def test_a_theorem_lean_does_not_know_is_missing() -> None:
    layout = _layout()
    unknown = [_msg(layout.line(r), "unknown identifier '_root_.two_add'", severity="error", column=8,
                    kind="unknownIdentifier") for r in ("check", "explicit", "kind", "axioms")]
    output = _output(layout, skip=("check", "explicit", "kind", "axioms"),
                     extra={"check": unknown[:1], "explicit": unknown[1:2], "kind": unknown[2:3],
                            "axioms": unknown[3:]})
    verdict = lo.judge(output, 1, layout)
    assert verdict["code"] == THEOREM_MISSING and "unknown identifier" in verdict["reason"]


def test_a_theorem_that_existed_before_the_source_is_not_the_sources() -> None:
    # recorded: ``#check_failure @_root_.Nat.add_comm`` after ``import Std``: the term elaborates, so it is an error
    layout = _layout("-- nothing here\n", "Nat.add_comm")
    output = _output(layout, statement="∀ (n m : Nat), n + m = m + n", skip=("pre",), extra={"pre": [
        _msg(layout.line("pre"), "Nat.add_comm : ∀ (n m : Nat), n + m = m + n"),
        _msg(layout.line("pre"), "unexpected success", severity="error")]})
    verdict = lo.judge(output, 1, layout)
    assert verdict["code"] == THEOREM_MISSING and "before your source" in verdict["reason"]


def test_an_error_in_the_source_blocks_with_its_place() -> None:
    layout = _layout("theorem two_add : 2 + 2 = 4 := rfl\n#check nonexistent\n")
    line = layout.file_line(2)
    error = _msg(line, "unknown identifier 'nonexistent'", severity="error", column=7, kind="unknownIdentifier",
                 end=(line, 18))
    verdict = lo.judge(_output(layout, caller=(error,)), 1, layout)
    assert verdict["code"] == ELABORATION_FAILED and "line 2, column 7" in verdict["reason"]
    assert verdict["messages"] == [{"file": "Proof.lean", "line": 2, "column": 7, "end_line": 2, "end_column": 18,
                                    "severity": "error", "kind": "unknownIdentifier",
                                    "text": "unknown identifier 'nonexistent'"}]


def test_an_error_after_the_import_line_is_given_the_callers_own_line_number() -> None:
    source = "import Std\ntheorem two_add : 2 + 2 = 4 := rfl\n#check nonexistent\n"
    layout = _layout(source)
    error = _msg(4, "unknown identifier 'nonexistent'", severity="error", column=7, kind="unknownIdentifier",
                 end=(4, 18))
    verdict = lo.judge(_output(layout, caller=(error,)), 1, layout)
    assert verdict["code"] == ELABORATION_FAILED and verdict["messages"][0]["line"] == 3


def test_an_error_where_the_source_stops_is_the_sources() -> None:
    # an unfinished declaration at the end of the source is reported where G6's block begins
    layout = _layout("theorem two_add : 2 + 2 = 4 :=\n")
    error = _msg(layout.line("begin"), "unexpected token '#print'; expected term", severity="error")
    verdict = lo.judge(_output(layout, extra={"begin": [error]}), 1, layout)
    assert verdict["code"] == ELABORATION_FAILED


def test_the_expected_statement_must_match_as_lean_prints_it() -> None:
    layout = _layout(expected="3 + 1 = 4")
    verdict = lo.judge(_output(layout), 0, layout)
    assert verdict["code"] == STATEMENT_MISMATCH and verdict["statement_matched"] is False
    assert "2 + 2 = 4" in verdict["reason"] and "3 + 1 = 4" in verdict["reason"]


def test_whitespace_does_not_count_in_the_comparison() -> None:
    layout = _layout("theorem add_zero' (n : Nat) : n + 0 = n := by simp\n", "add_zero'", "∀ (n : Nat), n + 0 = n")
    verdict = lo.judge(_output(layout, statement="∀ (n : Nat),\n  n + 0 = n"), 0, layout)
    assert verdict["code"] == "" and verdict["statement_matched"] is True


def test_a_statement_that_prints_the_same_but_is_another_proposition_is_a_mismatch() -> None:
    # recorded: ``theorem fin3 : (2 : Fin 3) + 2 = 4`` prints as ``2 + 2 = 4``; Lean refuses it as a proof of that
    layout = _layout("theorem fin3 : (2 : Fin 3) + 2 = 4 := by decide\n", "fin3", "2 + 2 = 4")
    mismatch = _msg(layout.line("expected"), "type mismatch\n  fin3\nhas type\n  (2 : Fin 3) + 2 = 4 : Prop\n"
                    "but is expected to have type\n  (2 : Nat) + 2 = 4 : Prop", severity="error", column=25)
    output = _output(layout, explicit="(2 : Fin (3 : Nat)) + (2 : Fin (3 : Nat)) = (4 : Fin (3 : Nat))",
                     extra={"expected": [mismatch]})
    verdict = lo.judge(output, 1, layout)
    assert verdict["code"] == STATEMENT_MISMATCH and "type mismatch" in verdict["reason"]
    assert verdict["statement_explicit"] == "(2 : Fin (3 : Nat)) + (2 : Fin (3 : Nat)) = (4 : Fin (3 : Nat))"


def test_messages_the_source_prints_cannot_stand_in_for_g6s() -> None:
    """Survival control for the spoofing guard: text that looks like G6's markers and a clean axiom report, printed
    from the caller's own lines, is ignored; the report on G6's own line decides."""
    layout = _layout("theorem two_add : 2 + 2 = 4 := by\n  stop\n  rfl\n")
    line = layout.file_line(1)
    fake = (_msg(line, f"{lo.MARK}{NONCE}_BEGIN"), _msg(line, "'two_add' does not depend on any axioms"),
            _msg(line, f"{lo.MARK}{NONCE}_END"))
    verdict = lo.judge(_output(layout, axioms="'two_add' depends on axioms: [sorryAx]", caller=fake), 0, layout)
    assert verdict["code"] == SORRY
    only_fakes = "\n".join(fake) + "\n"
    assert lo.judge(only_fakes, 0, layout)["code"] in (OUTPUT_UNRECOGNISED, RUN_FAILED)


def test_markers_with_another_nonce_are_not_g6s() -> None:
    layout = _layout()
    output = _output(layout).replace(NONCE, "f" * 32)
    assert lo.judge(output, 0, layout)["code"] == OUTPUT_UNRECOGNISED


@pytest.mark.parametrize(("output", "code"), [
    ("", RUN_FAILED),
    ("INTERNAL PANIC: out of memory\n", RUN_FAILED),
    ('{"severity": "information"}\n', RUN_FAILED),
])
def test_a_run_that_stopped_before_g6s_checks_is_not_read_as_an_answer(output: str, code: str) -> None:
    assert lo.judge(output, 1, _layout())["code"] == code


def test_an_extra_message_on_g6s_lines_is_not_read_as_an_answer() -> None:
    layout = _layout()
    output = _output(layout, extra={"axioms": [_msg(layout.line("axioms"), "'two_add' does not depend on any axioms")]})
    assert lo.judge(output, 0, layout)["code"] == OUTPUT_UNRECOGNISED


def test_a_failing_exit_code_with_nothing_wrong_reported_is_not_a_pass() -> None:
    layout = _layout()
    assert lo.judge(_output(layout), 1, layout)["code"] == OUTPUT_UNRECOGNISED


def test_a_kind_report_for_another_constant_is_not_read() -> None:
    layout = _layout()
    output = _output(layout, kind="@[defeq] theorem other : 2 + 2 = 4 :=\n⋯")
    assert lo.judge(output, 0, layout)["code"] == OUTPUT_UNRECOGNISED


# --- the readers ------------------------------------------------------------------------------------------------------

def test_parse_messages_keeps_what_is_not_a_message() -> None:
    messages, other = lo.parse_messages(_msg(3, "x", severity="warning") + "\nnot json\n\n[1, 2]\n")
    assert [m["line"] for m in messages] == [3] and other == ["not json", "[1, 2]"]


@pytest.mark.parametrize(("text", "axioms"), [
    ("'t' does not depend on any axioms", []),
    ("'t' depends on axioms: [propext]", ["propext"]),
    ("'A.t' depends on axioms: [propext,\n Classical.choice,\n Quot.sound]", ["propext", "Classical.choice", "Quot.sound"]),
    ("'_private.Proof.0.A.hidden' depends on axioms: [sorryAx]", ["sorryAx"]),
    ("t depends on axioms: [propext]", None),
    ("'t' depends on axioms: propext", None),
])
def test_parse_axioms(text: str, axioms: list[str] | None) -> None:
    assert lo.parse_axioms(text) == axioms


@pytest.mark.parametrize(("text", "kind"), [
    ("@[defeq] theorem two_add : 2 + 2 = 4 :=\n⋯", ("theorem", "two_add")),
    ("private theorem A.hidden : True :=\ntrivial", ("theorem", "A.hidden")),
    ("protected theorem Nat.add_comm : ∀ (n m : Nat), n + m = m + n :=\n⋯", ("theorem", "Nat.add_comm")),
    ("@[reducible] def A.ab : Nat :=\n2", ("def", "A.ab")),
    ("recursor Nat.rec.{u} : {motive : Nat → Sort u} →", ("recursor", "Nat.rec")),
    ("axiom propext : ∀ {a b : Prop}, (a ↔ b) → a = b", ("axiom", "propext")),
    ("something else entirely", None),
])
def test_parse_kind(text: str, kind: tuple[str, str] | None) -> None:
    assert lo.parse_kind(text) == kind


@pytest.mark.parametrize(("text", "statement"), [
    ("two_add : 2 + 2 = 4", "2 + 2 = 4"),
    ("@A.foo : ∀ {α : Type} (xs : List α), xs ++ [] = xs", "∀ {α : Type} (xs : List α), xs ++ [] = xs"),
    ("hidden : True", "True"),
    ("no colon here", None),
])
def test_parse_check(text: str, statement: str | None) -> None:
    assert lo.parse_check(text) == statement


def test_parse_the_version_two_ways() -> None:
    assert lo.parse_version("Lean 4.22.0\nTarget: x86_64-w64-windows-gnu Windows") == ("4.22.0",
                                                                                         "x86_64-w64-windows-gnu Windows")
    assert lo.parse_version_flag("Lean (version 4.22.0, x86_64-w64-windows-gnu, commit "
                                 "ba2cbbf09d4978f416e0ebd1fceeebc2c4138c05, Release)\n") == {
        "version": "4.22.0", "target": "x86_64-w64-windows-gnu", "commit": "ba2cbbf09d4978f416e0ebd1fceeebc2c4138c05",
        "build": "Release"}
    assert lo.parse_version("Lean four") is None and lo.parse_version_flag("lean, maybe") is None
