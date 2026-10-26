# SPDX-License-Identifier: Apache-2.0
"""``check_proof`` and ``lean_version`` (owner, 2026-10-20; audit 017 FM-001): the earlier version labelled any file
that elaborated ``verified``, even one holding only a comment. Here a pass needs a named theorem, declared in the
source, whose statement and axioms Lean itself reports, and it is ``qualified-draft``, never ``verified``.

The refusals run anywhere: Lean missing, the screen, the declaration check and the limits are decided before any
program starts. The known answers need Lean 4 (``lean`` on PATH, or ``G6_LEAN``) and are skipped with the reason when
it is absent; no Mathlib is assumed. The survival controls run Lean with G6's source screen switched off, to show
that Lean's own report of the axioms and the kind still blocks what the screen would have refused."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from g6.invariants import check_envelope
from g6.lean import check_proof, lean_version, proof
from g6.lean.envelope import (
    ELABORATION_FAILED,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    LEAN_ENV,
    LEAN_MISSING,
    NONSTANDARD_AXIOM,
    NOT_A_THEOREM,
    NOT_CHECKED,
    OUTPUT_TOO_LARGE,
    PROOF_CHECKED,
    SORRY,
    STATEMENT_MISMATCH,
    STATEMENT_REFUSED,
    THEOREM_MISSING,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    VERSION_READ,
    finish,
)
from g6.lean.schema import LeanResult

TWO = "theorem two_add : 2 + 2 = 4 := rfl\n"
EM = "theorem em_use (p : Prop) : p ∨ ¬p := Classical.em p\n"
FIN3 = "theorem fin3 : (2 : Fin 3) + 2 = 4 := by decide\n"
LIAR = """def Liar (n : Nat) : Prop := n = n + 1

@[app_unexpander Liar] def unexpandLiar : Lean.PrettyPrinter.Unexpander
  | `($_ $n) => `($n = $n)
  | _ => throw ()

theorem looks_false : ¬ Liar 3 := by simp [Liar]
"""
SLOW = "".join(f"theorem s{i} : ∀ a b : Fin 40, a + b = b + a := by decide\n" for i in range(100))


def _resolved() -> str | None:
    named = os.environ.get(LEAN_ENV, "").strip()
    if named:
        return shutil.which(named) or (named if Path(named).is_file() else None)
    return shutil.which("lean")


HAVE_LEAN = _resolved() is not None
needs_lean = pytest.mark.skipif(not HAVE_LEAN, reason=f"Lean 4 is not installed: install elan, Lean's toolchain "
                                                      f"manager, so lean is on PATH, or set {LEAN_ENV}")


@pytest.fixture
def no_lean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``G6_LEAN`` names a program that does not exist, so nothing can run: a refusal that is not LEAN_MISSING was
    decided before Lean was looked for."""
    missing = tmp_path / "no-lean-here"
    monkeypatch.setenv(LEAN_ENV, str(missing))
    return missing


def _blocked(result: LeanResult, code: str) -> None:
    assert (result.code, result.completion_state) == (code, "blocked-escalated"), (result.code, result.reason)
    assert result.warning_card["code"] == code and check_envelope(result.model_dump(mode="json")) == []


# --- no Lean needed -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run", [lambda: check_proof(TWO, "two_add"), lambda: lean_version()])
def test_no_lean_is_refused_and_says_how_to_install_it(run, no_lean: Path) -> None:
    result = run()
    _blocked(result, LEAN_MISSING)
    action = result.warning_card["suggested_action"]
    assert "elan" in action and LEAN_ENV in action and "never installs" in action
    assert result.evidence[0]["external_program_ran"] is False and str(no_lean) in result.reason


def test_no_lean_on_path_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(LEAN_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    _blocked(check_proof(TWO, "two_add"), LEAN_MISSING)


def test_sorry_is_refused_before_lean_runs(no_lean: Path) -> None:
    source = "theorem t : 1 = 2 := by sorry\n"
    result = check_proof(source, "t")
    _blocked(result, SORRY)
    assert result.refusals[0]["line"] == 1 and result.refusals[0]["column"] == source.index("sorry") == 24
    assert result.evidence[0]["external_program_ran"] is False and result.axioms is None


def test_a_file_that_is_only_a_comment_proves_nothing(no_lean: Path) -> None:
    """The FM-001 regression: the earlier version labelled this ``verified`` because Lean exits 0 on it."""
    for name in ("two_add", "Nat.add_comm"):
        result = check_proof("-- nothing here\n", name)
        _blocked(result, THEOREM_MISSING)
        assert name in result.reason and result.evidence[0]["external_program_ran"] is False


def test_a_def_is_not_a_theorem(no_lean: Path) -> None:
    result = check_proof("def x : Nat := 1\n", "x")
    _blocked(result, NOT_A_THEOREM)
    assert "def" in result.reason


def test_a_theorem_named_but_declared_under_another_name_is_missing(no_lean: Path) -> None:
    _blocked(check_proof("namespace A\ntheorem t : True := trivial\nend A\n", "t"), THEOREM_MISSING)


def test_an_expected_statement_that_could_carry_a_command_is_refused(no_lean: Path) -> None:
    result = check_proof(TWO, "two_add", expected_statement="True) := trivial #exit example : (True")
    _blocked(result, STATEMENT_REFUSED)
    assert result.refusals and all(f["where"] == "expected_statement" for f in result.refusals)


@pytest.mark.parametrize(("kwargs", "code"), [
    ({"time_limit": 601}, TIME_LIMIT_TOO_LONG), ({"time_limit": 0}, INPUT_INVALID),
    ({"time_limit": float("inf")}, INPUT_INVALID), ({"time_limit": True}, INPUT_INVALID),
    ({"memory_mb": 1}, INPUT_INVALID), ({"memory_mb": 1 << 20}, INPUT_TOO_LARGE),
    ({"max_output_bytes": 0}, INPUT_INVALID),
    ({"expected_statement": "x" * 20_000}, INPUT_TOO_LARGE),
])
def test_the_limits_are_refused_before_anything_runs(kwargs: dict, code: str, no_lean: Path) -> None:
    _blocked(check_proof(TWO, "two_add", **kwargs), code)


@pytest.mark.parametrize("name", ["", "two add", "a..b", "_root_.two_add", "two_add #exit", "x;y", "9lives",
                                  "«two_add»", "two_add\n", "a" * 300])
def test_a_theorem_name_g6_does_not_read_is_refused(name: str, no_lean: Path) -> None:
    _blocked(check_proof(TWO, name), INPUT_INVALID)


def test_a_source_over_the_size_limit_is_refused(no_lean: Path) -> None:
    _blocked(check_proof(TWO + "-- " + "x" * 300_000 + "\n", "two_add"), INPUT_TOO_LARGE)
    _blocked(check_proof(TWO + "\n" * 30_000, "two_add"), INPUT_TOO_LARGE)
    _blocked(check_proof(b"theorem t : True := trivial", "t"), INPUT_INVALID)  # type: ignore[arg-type]


def test_no_result_can_be_verified() -> None:
    with pytest.raises(ValidationError):
        LeanResult(operation="check_proof", completion_state="verified")


@pytest.mark.parametrize("fields", [
    {},
    {"lean_ran": True, "kind": "theorem", "statement": "True"},
    {"lean_ran": True, "kind": "def", "statement": "True", "axioms": []},
    {"lean_ran": True, "kind": "theorem", "statement": "True", "axioms": ["cheat"]},
    {"lean_ran": True, "kind": "theorem", "statement": "True", "axioms": ["sorryAx"]},
    {"lean_ran": True, "kind": "theorem", "statement": "True", "axioms": [], "expected_statement": "True"},
    {"lean_ran": False, "kind": "theorem", "statement": "True", "axioms": []},
])
def test_finish_blocks_an_answer_without_lean_s_report(fields: dict) -> None:
    """Survival control for the label: a pass needs Lean to have run and reported a theorem, its statement, standard
    axioms only and, when one was expected, a matching statement; a result built without them is blocked, whoever
    built it."""
    result = finish("check_proof", **fields)
    assert result.code == NOT_CHECKED and result.completion_state == "blocked-escalated"
    assert check_envelope(result.model_dump(mode="json")) == []


# --- known answers: Lean needed ---------------------------------------------------------------------------------------

@needs_lean
def test_two_and_two_is_checked_as_a_qualified_draft() -> None:
    result = check_proof(TWO, "two_add", request_id="q", run_id="r")
    assert result.completion_state == "qualified-draft" and result.code == "", (result.code, result.reason)
    assert result.warning_card["code"] == PROOF_CHECKED and check_envelope(result.model_dump(mode="json")) == []
    assert (result.statement, result.axioms, result.kind) == ("2 + 2 = 4", [], "theorem")
    assert result.lean_version.startswith("4.") and (result.request_id, result.run_id) == ("q", "r")
    message = result.warning_card["message"]
    assert "kernel" in message and "2 + 2 = 4" in message and "for a person to inspect" in message
    evidence = result.evidence[0]
    assert evidence["external_program_ran"] is True and evidence["screened"] is True
    assert evidence["execution"]["cleanup_confirmed"] is True and not Path(evidence["run_dir"]).exists()


@needs_lean
def test_excluded_middle_uses_the_standard_axioms_and_says_so() -> None:
    result = check_proof(EM, "em_use", expected_statement="∀ (p : Prop), p ∨ ¬p")
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert "Classical.choice" in result.axioms and set(result.axioms) <= {"propext", "Classical.choice", "Quot.sound"}
    assert result.statement_matched is True and "Classical.choice" in result.warning_card["message"]


@needs_lean
def test_a_theorem_in_a_namespace_with_std_imported() -> None:
    source = "import Std\nnamespace A\ntheorem foo (n : Nat) : n + 0 = n := rfl\n"   # the namespace is left open
    result = check_proof(source, "A.foo", expected_statement="∀ (n : Nat),\n  n + 0 = n")
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    assert result.statement == "∀ (n : Nat), n + 0 = n" and result.statement_matched is True


@needs_lean
def test_sorry_is_refused_before_lean_runs_even_with_lean_installed() -> None:
    result = check_proof("theorem t : 1 = 2 := by sorry\n", "t")
    _blocked(result, SORRY)
    assert result.evidence[0]["external_program_ran"] is False


@needs_lean
def test_the_comment_only_file_and_the_def_are_blocked_with_lean_installed() -> None:
    _blocked(check_proof("-- nothing here\n", "two_add"), THEOREM_MISSING)
    _blocked(check_proof("def two_add : Nat := 4\n", "two_add"), NOT_A_THEOREM)


@needs_lean
def test_an_expected_statement_that_differs_is_blocked() -> None:
    result = check_proof(TWO, "two_add", expected_statement="2 + 2 = 5")
    _blocked(result, STATEMENT_MISMATCH)
    assert result.statement == "2 + 2 = 4" and result.statement_matched is False


@needs_lean
def test_a_statement_that_prints_the_same_but_means_another_thing_is_blocked() -> None:
    # In Fin 3, 2 + 2 is 1, which is 4: the statement prints as 2 + 2 = 4, but read on its own that is about Nat.
    result = check_proof(FIN3, "fin3", expected_statement="2 + 2 = 4")
    _blocked(result, STATEMENT_MISMATCH)
    assert "Fin" in result.statement_explicit and "Fin" in result.reason


@needs_lean
def test_a_type_error_is_blocked_with_its_line_and_column() -> None:
    source = TWO + "theorem bad : True := (5 : Nat)\n"
    result = check_proof(source, "two_add")
    _blocked(result, ELABORATION_FAILED)
    errors = [m for m in result.messages if m["severity"] == "error"]
    assert errors and errors[0]["line"] == 2 and errors[0]["column"] == source.split("\n")[1].index("(5")
    assert errors[0]["file"] == "Proof.lean" and errors[0]["text"]


@needs_lean
def test_a_run_past_its_time_limit_is_killed_with_its_process_tree() -> None:
    result = check_proof(SLOW, "s0", time_limit=3)
    _blocked(result, TIME_LIMIT)
    evidence = result.evidence[0]
    assert result.elapsed_s < 30 and evidence["execution"]["cleanup_confirmed"] is True
    assert not Path(evidence["run_dir"]).exists()


@needs_lean
def test_output_past_its_cap_stops_the_run() -> None:
    _blocked(check_proof(TWO + "#check two_add\n" * 50, "two_add", max_output_bytes=200), OUTPUT_TOO_LARGE)


@needs_lean
def test_the_source_reaches_lean_as_a_file_never_on_the_command_line() -> None:
    sneaky = TWO + '-- " & echo pwned & --run evil.lean\n'
    result = check_proof(sneaky, "two_add")
    assert result.completion_state == "qualified-draft", (result.code, result.reason)
    argv = result.evidence[0]["argv"]
    assert argv[1:] == ["--json", "Proof.lean"] and not any("pwned" in a or "evil" in a for a in argv)


@needs_lean
def test_lean_version_names_the_lean_that_would_check() -> None:
    result = lean_version()
    assert result.completion_state == "qualified-draft" and result.warning_card["code"] == VERSION_READ
    assert result.lean_version.startswith("4.") and "checked nothing" in result.warning_card["message"]


# --- survival controls: the screen switched off, Lean's own report still blocks -------------------------------------

@needs_lean
@pytest.mark.parametrize(("source", "name", "code", "said"), [
    ("theorem t : 1 = 2 := by\n  stop\n  rfl\n", "t", SORRY, "sorryAx"),
    ("theorem t : 1 = 2 := by sorry\n", "t", SORRY, "sorryAx"),
    ("axiom cheat : False\ntheorem t : 1 = 2 := cheat.elim\n", "t", NONSTANDARD_AXIOM, "cheat"),
    ("theorem t : 10 < 20 := by native_decide\n", "t", NONSTANDARD_AXIOM, "Lean.ofReduceBool"),
    ("def x : Nat := 1\n", "x", NOT_A_THEOREM, "def"),
    ("-- nothing here\n", "two_add", THEOREM_MISSING, "two_add"),
    ("-- nothing here\n", "Nat.add_comm", THEOREM_MISSING, "before your source"),
])
def test_without_the_screen_lean_s_report_still_blocks(source: str, name: str, code: str, said: str) -> None:
    result = proof._check_proof(source, name, screened=False)
    _blocked(result, code)
    assert said in result.reason and result.evidence[0]["screened"] is False
    assert result.evidence[0]["external_program_ran"] is True


@needs_lean
def test_without_the_screen_a_printer_that_lies_about_the_statement_is_caught() -> None:
    """An ``app_unexpander`` (refused by the screen) makes ``¬ Liar 3``, which is true, print as ``¬3 = 3``, which
    reads as false. The printed text matches, so only Lean reading the expected statement itself catches it."""
    result = proof._check_proof(LIAR, "looks_false", expected_statement="¬3 = 3", screened=False)
    _blocked(result, STATEMENT_MISMATCH)
    assert result.statement == "¬3 = 3" and "type mismatch" in result.reason
