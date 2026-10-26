# SPDX-License-Identifier: Apache-2.0
"""``query``, ``prove``, ``check_program`` and ``swipl_version`` (owner, 2026-10-20; audit 017 FM-010): the earlier
version consulted the caller's program, so its directives ran, let a goal reach ``shell/1``, had no time limit and
shared one database between calls. Here every op runs a fresh ``swipl`` on G6's fixed driver, which reads the program
without consulting it, accepts only the restricted syntax (owner, 2026-10-21: the program's own predicates and a short
allow-list; no directives, no variable or module-qualified goals) and bounds the goal, and every answer is re-checked
in a second process. The best state is ``qualified-draft``. The restricted syntax is checked by its refusals only.

The refusals of the limits and of a missing SWI-Prolog run anywhere: they are decided before any program starts. The
known answers need SWI-Prolog (``swipl`` on PATH, or ``G6_SWIPL``) and are skipped with the reason when it is absent."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from g6.invariants import check_envelope
from g6.mcp import invoke_reply
from g6.prolog import check_program, driver, ops, prove, query, runner, swipl_version
from g6.prolog.envelope import (
    ANSWERS,
    CERTIFICATE_REFUTED,
    CLAUSE_REFUSED,
    DIRECTIVE_REFUSED,
    GOAL_DERIVABLE,
    GOAL_ERROR,
    GOAL_NOT_ALLOWED,
    GOAL_NOT_DERIVABLE,
    INFERENCE_LIMIT,
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    MEMORY_LIMIT,
    NO_ANSWERS,
    NOT_CHECKED,
    PROGRAM_READ,
    SWIPL_ENV,
    SWIPL_MISSING,
    SYNTAX_ERROR,
    TIME_LIMIT,
    TIME_LIMIT_TOO_LONG,
    UNDEFINED_PREDICATE,
    VERSION_READ,
    finish,
)
from g6.prolog.schema import PrologResult

FAMILY = """parent(tom, bob).
parent(tom, liz).
parent(bob, ann).
parent(bob, pat).
parent(pat, jim).
ancestor(X, Y) :- parent(X, Y).
ancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).
"""
LOOP = "loop :- loop.\n"


def _resolved() -> str | None:
    named = os.environ.get(SWIPL_ENV, "").strip()
    if named:
        return shutil.which(named) or (named if Path(named).is_file() else None)
    return shutil.which("swipl")


SWIPL = _resolved()
needs_swipl = pytest.mark.skipif(SWIPL is None, reason="SWI-Prolog is not installed: install it from swi-prolog.org so "
                                                       f"swipl is on PATH, or set {SWIPL_ENV}")


@pytest.fixture
def no_swipl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``G6_SWIPL`` names a program that does not exist, so nothing can run: a refusal that is not SWIPL_MISSING was
    decided before SWI-Prolog was looked for."""
    missing = tmp_path / "no-swipl-here"
    monkeypatch.setenv(SWIPL_ENV, str(missing))
    return missing


def _blocked(result: PrologResult, code: str) -> None:
    assert (result.code, result.completion_state) == (code, "blocked-escalated"), (result.code, result.reason)
    assert result.warning_card["code"] == code and check_envelope(result.model_dump(mode="json")) == []


def _draft(result: PrologResult, card: str) -> None:
    assert (result.code, result.completion_state) == ("", "qualified-draft"), (result.code, result.reason)
    assert result.warning_card["code"] == card and check_envelope(result.model_dump(mode="json")) == []


def _bindings(result: PrologResult) -> list[dict[str, str]]:
    return [a["bindings"] for a in result.answers or []]


# --- no SWI-Prolog needed ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("run", [lambda: query(FAMILY, "ancestor(tom, X)"), lambda: prove(FAMILY, "parent(tom, bob)"),
                                 lambda: check_program(FAMILY), lambda: swipl_version()])
def test_no_swipl_is_refused_and_says_how_to_install_it(run, no_swipl: Path) -> None:
    result = run()
    _blocked(result, SWIPL_MISSING)
    action = result.warning_card["suggested_action"]
    assert "swi-prolog.org" in action and SWIPL_ENV in action and "G6 never installs it" in action
    assert result.evidence[0]["external_program_ran"] is False and str(no_swipl) in result.reason


def test_no_swipl_on_path_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(SWIPL_ENV, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    _blocked(query(FAMILY, "ancestor(tom, X)"), SWIPL_MISSING)
    _blocked(swipl_version(), SWIPL_MISSING)


@pytest.mark.parametrize(("run", "code"), [
    (lambda: query(FAMILY, "ancestor(tom, X)", time_limit=601), TIME_LIMIT_TOO_LONG),
    (lambda: query(FAMILY, "ancestor(tom, X)", time_limit=0), INPUT_INVALID),
    (lambda: query(FAMILY, "ancestor(tom, X)", time_limit=float("inf")), INPUT_INVALID),
    (lambda: query(FAMILY, "ancestor(tom, X)", max_solutions=ops.MAX_SOLUTIONS + 1), INPUT_TOO_LARGE),
    (lambda: query(FAMILY, "ancestor(tom, X)", max_solutions=0), INPUT_INVALID),
    (lambda: query(FAMILY, "ancestor(tom, X)", inference_limit=0), INPUT_INVALID),
    (lambda: query(FAMILY, "ancestor(tom, X)", inference_limit=ops.MAX_INFERENCE_LIMIT + 1), INPUT_TOO_LARGE),
    (lambda: query(FAMILY, "ancestor(tom, X)", memory_mb=10), INPUT_INVALID),
    (lambda: query(FAMILY, "ancestor(tom, X)", max_output_bytes=runner.OUTPUT_CAP_LIMIT + 1), INPUT_TOO_LARGE),
    (lambda: query("p(1).\n" * (ops.MAX_PROGRAM_CHARS // 6 + 1), "p(X)"), INPUT_TOO_LARGE),
    (lambda: query("\n" * (ops.MAX_PROGRAM_LINES + 1), "true"), INPUT_TOO_LARGE),
    (lambda: query(FAMILY, "true, " * (ops.MAX_GOAL_CHARS // 6 + 1) + "true"), INPUT_TOO_LARGE),
    (lambda: query(FAMILY, "   "), INPUT_INVALID),
    (lambda: query(["parent(tom, bob)."], "parent(tom, X)"), INPUT_INVALID),
    (lambda: prove(FAMILY, ""), INPUT_INVALID),
    (lambda: check_program(None), INPUT_INVALID),
    (lambda: swipl_version(time_limit=-1), INPUT_INVALID),
])
def test_bad_inputs_and_the_limits_are_refused_before_swipl_is_looked_for(run, code: str, no_swipl: Path) -> None:
    result = run()
    _blocked(result, code)
    assert result.evidence[0]["external_program_ran"] is False


def test_the_result_cannot_be_verified() -> None:
    with pytest.raises(ValidationError):
        PrologResult(operation="query", completion_state="verified")


def test_a_hand_built_answer_with_no_recheck_is_not_checked() -> None:
    """Only a result whose answers a second process re-checked is a draft; a claim of answers alone is not."""
    claimed = finish("query", swipl_ran=True, swipl_version="10.0.2", answers=[{"bindings": {"X": "1"}, "text": "['X'=1]"}],
                     exhausted=True)
    _blocked(claimed, NOT_CHECKED)
    short = finish("query", swipl_ran=True, swipl_version="10.0.2", exhausted=True,
                   answers=[{"bindings": {"X": "1"}, "text": "['X'=1]"}, {"bindings": {"X": "2"}, "text": "['X'=2]"}],
                   recheck={"passed": True, "checked": 1})
    _blocked(short, NOT_CHECKED)
    _blocked(finish("prove", swipl_ran=True, swipl_version="10.0.2", answers=[], succeeded=True,
                    recheck={"passed": True, "checked": 1}), NOT_CHECKED)
    _blocked(finish("query", answers=[], recheck={"passed": True, "checked": 1}), NOT_CHECKED)


def test_a_finished_search_is_a_draft_only_when_its_end_was_rechecked() -> None:
    """I2: with ``exhausted`` true the re-check covers every answer and the search's end (run again with a cap one
    higher, it gave the same answers and no more); a result with no answers must say the search finished."""
    one = [{"bindings": {"X": "1"}, "text": "['X'=1]"}]
    ran = {"swipl_ran": True, "swipl_version": "10.0.2"}
    _blocked(finish("query", **ran, answers=one, exhausted=True, recheck={"passed": True, "checked": 1}), NOT_CHECKED)
    _draft(finish("query", **ran, answers=one, exhausted=True, recheck={"passed": True, "checked": 2}), ANSWERS)
    _draft(finish("query", **ran, answers=one, exhausted=False, recheck={"passed": True, "checked": 1}), ANSWERS)
    _blocked(finish("query", **ran, answers=[], exhausted=None, recheck={"passed": True, "checked": 0}), NOT_CHECKED)
    _draft(finish("query", **ran, answers=[], exhausted=True, recheck={"passed": True, "checked": 1}), NO_ANSWERS)


# --- known answers, with SWI-Prolog ----------------------------------------------------------------------------------

@needs_swipl
def test_an_ancestor_query_gives_the_known_answers_each_rechecked() -> None:
    result = query(FAMILY, "ancestor(tom, Who)")
    _draft(result, ANSWERS)
    assert sorted(b["Who"] for b in _bindings(result)) == ["ann", "bob", "jim", "liz", "pat"]
    assert result.exhausted is True and result.stopped_by == ""
    assert result.recheck["passed"] is True and result.recheck["checked"] == 6  # five answers, and the search's end
    assert [v["verdict"] for v in result.recheck["verdicts"]] == ["agreed"] * 6
    assert {p["name"]: p["clauses"] for p in result.predicates} == {"parent": 5, "ancestor": 2}
    assert "not verified" in result.warning_card["message"] and result.swipl_version


@needs_swipl
def test_append_splits_a_list_of_three_exactly_four_ways() -> None:
    result = query("", "append(X, Y, [1,2,3])")
    _draft(result, ANSWERS)
    assert _bindings(result) == [{"X": "[]", "Y": "[1,2,3]"}, {"X": "[1]", "Y": "[2,3]"},
                                 {"X": "[1,2]", "Y": "[3]"}, {"X": "[1,2,3]", "Y": "[]"}]
    assert result.exhausted is True


@needs_swipl
def test_reaching_max_solutions_is_not_exhausted() -> None:
    result = query("nat(0).\nnat(N) :- nat(M), N is M + 1.\n", "nat(X)", max_solutions=3)
    _draft(result, ANSWERS)
    assert _bindings(result) == [{"X": "0"}, {"X": "1"}, {"X": "2"}]
    assert (result.exhausted, result.stopped_by) == (False, "max_solutions")
    assert "there may be more" in result.warning_card["message"]


@needs_swipl
def test_variables_the_answer_leaves_free_are_named_and_shared() -> None:
    result = query("", "X = f(A, A, _B), length(L, 2)")
    _draft(result, ANSWERS)
    (answer,) = _bindings(result)
    assert answer["X"] == "f(_V1,_V1,_V2)" and answer["A"] == "_V1" and answer["L"] == "[_V3,_V4]"


@needs_swipl
def test_a_goal_that_fails_is_not_derivable_and_the_failure_is_rechecked() -> None:
    failed = prove(FAMILY, "ancestor(jim, tom)")
    _draft(failed, GOAL_NOT_DERIVABLE)
    assert failed.succeeded is False and failed.answers == [] and failed.exhausted is True
    assert failed.recheck["verdicts"] == [{"index": -1, "verdict": "agreed", "why": "the goal failed again"}]
    message = failed.warning_card["message"]
    assert "not derivable from this program" in message and "not the same as false" in message
    empty = query(FAMILY, "parent(jim, Child)")
    _draft(empty, NO_ANSWERS)
    assert empty.answers == [] and "closed world" in empty.warning_card["message"]


@needs_swipl
def test_a_goal_that_succeeds_is_derivable() -> None:
    result = prove(FAMILY, "ancestor(tom, jim)")
    _draft(result, GOAL_DERIVABLE)
    assert result.succeeded is True and result.answers == [{"bindings": {}, "text": "[]"}]


# --- ordinary programs keep to the restricted syntax -----------------------------------------------------------------

@needs_swipl
def test_list_processing_keeps_to_the_restricted_syntax() -> None:
    result = query("", "numlist(1, 5, L), sum_list(L, S), max_list(L, Max), min_list(L, Min), reverse(L, R), "
                       "last(L, Last), nth0(0, L, First), nth1(2, L, Second), length(L, N), memberchk(3, L), "
                       "msort([b, a, c, a], M), sort([b, a, c, a], Set), sort(0, @>=, [1, 3, 2, 3], D), "
                       "list_to_set([a, b, a], U), append(L, [6], L6), member(6, L6)")
    _draft(result, ANSWERS)
    assert _bindings(result) == [{"L": "[1,2,3,4,5]", "S": "15", "Max": "5", "Min": "1", "R": "[5,4,3,2,1]",
                                  "Last": "5", "First": "1", "Second": "2", "N": "5", "M": "[a,a,b,c]",
                                  "Set": "[a,b,c]", "D": "[3,3,2,1]", "U": "[a,b]", "L6": "[1,2,3,4,5,6]"}]


@needs_swipl
def test_arithmetic_keeps_to_the_restricted_syntax() -> None:
    result = query("", "X is 2 ^ 10, Y is 7 mod 3, between(1, 3, B), succ(B, C), plus(B, C, P), B < C, C =< 4, "
                       "X =:= 1024, X =\\= 0, X >= 1000, Y > 0")
    _draft(result, ANSWERS)
    assert [(b["B"], b["C"], b["P"]) for b in _bindings(result)] == [("1", "2", "3"), ("2", "3", "5"), ("3", "4", "7")]
    assert {(b["X"], b["Y"]) for b in _bindings(result)} == {("1024", "1")}


@needs_swipl
def test_terms_types_and_conversions_keep_to_the_restricted_syntax() -> None:
    result = query("", "T = f(a, 1), functor(T, F, A), arg(1, T, Arg), T =.. List, copy_term(g(V, V), Copy), "
                       "atom(F), integer(A), compound(T), callable(T), atomic(F), nonvar(T), var(V), is_list(List), "
                       "ground(T), number(1.5), float(1.5), atom_codes(abc, Codes), atom_chars(Word, [h, i]), "
                       "char_code(Ch, 0'a), atom_number('12', Num), number_codes(Seven, [0'7]), "
                       "number_chars(Eight, ['8']), atom_length(hello, Len), compare(<, a, b), a @< b, b @> a, "
                       "a @=< a, b @>= a, a \\== b, a \\= b, a == a")
    _draft(result, ANSWERS)
    (answer,) = _bindings(result)
    assert {k: answer[k] for k in ("F", "A", "Arg", "List", "Codes", "Word", "Ch", "Num", "Seven", "Eight", "Len")} \
        == {"F": "f", "A": "2", "Arg": "a", "List": "[f,a,1]", "Codes": "[97,98,99]", "Word": "hi", "Ch": "a",
            "Num": "12", "Seven": "7", "Eight": "8", "Len": "5"}


@needs_swipl
def test_findall_bagof_setof_forall_and_aggregate_all_keep_to_the_restricted_syntax() -> None:
    result = query("", "findall(X, member(X, [c, a, b]), All), setof(X, member(X, [c, a, b]), Sorted), "
                       "bagof(X-Y, member(X-Y, [1-a, 2-b]), Pairs), setof(K, V^member(K-V, [b-1, a-2]), Keys), "
                       "aggregate_all(count, member(_, [a, b]), Count), "
                       "aggregate_all(sum(E), member(E, [1, 2, 3]), Sum), "
                       "aggregate_all(max(E), member(E, [1, 3, 2]), Max), "
                       "aggregate_all(bag(E), member(E, [b, a]), Bag), forall(member(Z, [1, 2]), Z > 0), "
                       "\\+ member(d, All), (member(a, All) -> Found = yes ; Found = no)")
    _draft(result, ANSWERS)
    (answer,) = _bindings(result)
    assert {k: answer[k] for k in ("All", "Sorted", "Pairs", "Keys", "Count", "Sum", "Max", "Bag", "Found")} == {
        "All": "[c,a,b]", "Sorted": "[a,b,c]", "Pairs": "[1-a,2-b]", "Keys": "[a,b]", "Count": "2", "Sum": "6",
        "Max": "3", "Bag": "[b,a]", "Found": "yes"}


@needs_swipl
def test_maplist_runs_with_a_closure_that_names_a_predicate() -> None:
    result = query("double(X, Y) :- Y is 2 * X.\n", "maplist(double, [1, 2, 3], L), maplist(succ, L, M), "
                                                    "maplist(integer, M), maplist(plus(1), [1, 2], [2, 3])")
    _draft(result, ANSWERS)
    assert _bindings(result) == [{"L": "[2,4,6]", "M": "[3,5,7]"}]


@needs_swipl
def test_a_grammar_rule_reads_as_its_clause() -> None:
    result = query("greeting --> [hello], who.\nwho --> [world].\nwho --> [prolog].\n", "greeting([hello, W], [])")
    _draft(result, ANSWERS)
    assert _bindings(result) == [{"W": "world"}, {"W": "prolog"}]


@needs_swipl
def test_an_infinite_loop_is_stopped_by_the_inference_limit() -> None:
    result = query(LOOP, "loop", inference_limit=100_000)
    _blocked(result, INFERENCE_LIMIT)
    assert (result.stopped_by, result.exhausted, result.recheck) == ("inference_limit", False, None)


@needs_swipl
def test_answers_found_before_a_limit_are_kept_and_not_reported_as_checked() -> None:
    result = query("p(1).\np(2).\np(_) :- loop.\n" + LOOP, "p(X)", inference_limit=100_000)
    _blocked(result, INFERENCE_LIMIT)
    assert _bindings(result) == [{"X": "1"}, {"X": "2"}] and result.recheck is None
    assert "were not re-checked" in result.reason


@needs_swipl
def test_an_infinite_loop_is_stopped_by_the_time_limit() -> None:
    result = query(LOOP, "loop", time_limit=1, inference_limit=ops.MAX_INFERENCE_LIMIT)
    _blocked(result, TIME_LIMIT)
    assert result.stopped_by == "time_limit"
    run = result.evidence[0]["search"]  # well within the process's own deadline: the limit plus the start-up allowance
    assert run["execution"]["elapsed_seconds"] < 1 + runner.STARTUP_ALLOWANCE_S
    assert run["execution"]["cleanup_confirmed"] is True and not Path(run["run_dir"]).exists()


@needs_swipl
def test_a_search_that_builds_too_much_is_stopped_by_the_stack_limit() -> None:
    """I1: the stacks are capped on every system by --stack-limit, half of memory_mb (the whole process only on
    Windows, through the Job Object). Twenty million numbers do not fit in 256 MB of stack: the run reports the limit
    with its own code, not a crash or a hang."""
    result = query("", "numlist(1, 20000000, L), sum_list(L, S)", memory_mb=512,
                   inference_limit=ops.MAX_INFERENCE_LIMIT)
    _blocked(result, MEMORY_LIMIT)
    assert (result.stopped_by, result.exhausted, result.recheck) == ("memory_limit", False, None)
    assert "stack limit of 256 MB" in result.reason and "were not re-checked" in result.reason


# --- the restricted syntax's refusals --------------------------------------------------------------------------------

@needs_swipl
@pytest.mark.parametrize(("goal", "named"), [
    ("shell(ls)", "shell/1"),
    ('format("hello~n")', "format/1"),
    ('format("~w", [x])', "format/2"),
    ('print_message(error, format("hello", []))', "print_message/2"),
    ("assertz(seen(1))", "assertz/1"),
    ("retract(seen(_))", "retract/1"),
    ("catch(member(X, [1]), _, true)", "catch/3"),
    ("call(true)", "call/1"),
    ("dif(X, a)", "dif/2"),  # so no answer carries a constraint
    ("lists:append(X, Y, [1])", "lists:append/3"),
    ("findall(X, G, L)", "a variable"),
    ("forall(member(X, [1]), G)", "a variable"),
    ("bagof(X, Y^G, L)", "a variable"),
    ("maplist(G, [1])", "maplist/2"),
    ("findall(X, call(X), L)", "call/1"),
    ("forall(member(X, [1]), assertz(seen(X)))", "assertz/1"),
    ("setof(X, Y^shell(Y), L)", "shell/1"),
    ("aggregate_all(foo, true, C)", "aggregate_all/3"),
])
def test_a_goal_outside_the_restricted_syntax_is_refused_and_never_runs(goal: str, named: str) -> None:
    result = query("", goal)
    _blocked(result, GOAL_NOT_ALLOWED)
    assert named in result.reason and result.reason.endswith("(in the goal)")
    assert result.problems[0]["stage"] == "goal" and result.answers is None and result.recheck is None


@needs_swipl
@pytest.mark.parametrize(("program", "named"), [
    ("go(Command) :- shell(Command).\n", "shell/1"),
    ("p :- process_create(path(ls), [], []).\n", "process_create/3"),
    ("call_it(G) :- call(G).\n", "call/1"),
    ("call_it(G) :- G.\n", "a variable"),
    ("p(L) :- maplist(G, L).\n", "maplist/2"),
    ("p :- lists:append(_, _, [1]).\n", "lists:append/3"),
    ('say --> {format("hello")}.\n', "format/1"),
])
def test_a_clause_body_outside_the_restricted_syntax_is_refused(program: str, named: str) -> None:
    for result in (check_program(program), query(program, "true")):
        _blocked(result, GOAL_NOT_ALLOWED)
        assert named in result.reason and result.problems[0]["stage"] == "program"
        assert result.problems[0]["line"] == 1 and result.answers is None


@needs_swipl
def test_a_call_to_a_predicate_nothing_defines_is_named() -> None:
    result = query(FAMILY, "grandparent(tom, X)")
    _blocked(result, UNDEFINED_PREDICATE)
    assert "grandparent/2" in result.reason and result.problems[0]["stage"] == "goal"
    body = check_program("p :- q.\n")
    _blocked(body, UNDEFINED_PREDICATE)
    assert "q/0" in body.reason and body.problems[0]["line"] == 1


@needs_swipl
def test_an_error_in_the_goal_is_written_as_a_term_not_translated() -> None:
    """R2: the driver writes an error with write_term (quoted, no portray hook, attributes ignored); no term the caller
    built goes through SWI-Prolog's message translation."""
    result = query("", "X is foo + 1")
    _blocked(result, GOAL_ERROR)
    assert "type_error(evaluable,foo/0)" in result.reason


@needs_swipl
def test_a_directive_is_refused_and_never_runs() -> None:
    for result in (query(":- initialization(main).\nparent(tom, bob).\n", "parent(tom, X)"),
                   check_program(":- dynamic(seen/1).\nparent(tom, bob).\n"),
                   prove(":- initialization(main).\n?- true.\n", "true")):
        _blocked(result, DIRECTIVE_REFUSED)
        assert result.problems[0]["line"] == 1 and result.problems[0]["problem"] == "directive"
        assert result.answers is None


@needs_swipl
@pytest.mark.parametrize(("program", "line", "named"), [
    ("atom_length(abc, 3).\n", 1, "atom_length/2"),
    ("parent(tom, bob).\nsandbox:safe_primitive(shell(_)).\n", 2, "sandbox:safe_primitive/1"),
    ("user:portray(_) :- true.\n", 1, "user:portray/1"),
    ("(a, b) :- true.\n", 1, "','/2"),
    ("42.\n", 1, "42"),
    ("portray(_) :- true.\n", 1, "portray/1"),
    ("message_hook(_, _, _) :- fail.\n", 1, "message_hook/3"),
    ("exception(_, _, _) :- fail.\n", 1, "exception/3"),
    ("attr_portray_hook(_, _).\n", 1, "attr_portray_hook/2"),
    ("term_expansion(a, b).\n", 1, "term_expansion/2"),
    ("goal_expansion(a, b).\n", 1, "goal_expansion/2"),
    ("append([], L, L).\n", 1, "append/3"),
    ("p(1).\nmember(X, [X|_]).\n", 2, "member/2"),
    ("'$p'(1).\n", 1, "'$p'/1"),
])
def test_a_program_may_define_only_its_own_predicates(program: str, line: int, named: str) -> None:
    """No clause whose head is a built-in, a library predicate or a hook SWI-Prolog calls, or names another module."""
    result = query(program, "true")
    _blocked(result, CLAUSE_REFUSED)
    assert result.problems[0]["line"] == line and result.problems[0]["problem"] == "clause"
    assert named in result.reason and result.answers is None


@needs_swipl
def test_a_syntax_error_is_reported_with_its_line_and_column() -> None:
    result = check_program("p(1).\np(a b).\nq(1).\n")  # not p(2 3): SWI-Prolog reads 2 3 as the digit groups of 23
    _blocked(result, SYNTAX_ERROR)
    (problem,) = result.problems
    assert problem["line"] == 2 and isinstance(problem["column"], int) and "Syntax error" in problem["message"]
    assert {p["name"] for p in result.predicates} == {"p", "q"}


@needs_swipl
def test_a_quasi_quotation_is_not_parsed_by_calling_a_predicate() -> None:
    result = check_program("p({|shell||echo pwned|}).\n")
    _blocked(result, SYNTAX_ERROR)


@needs_swipl
@pytest.mark.parametrize("goal", ["true. fail", "true. fail.", "X = 1. Y = 2"])
def test_a_goal_must_be_exactly_one_term(goal: str) -> None:
    result = query("", goal)
    _blocked(result, SYNTAX_ERROR)
    assert result.problems[0]["stage"] == "goal"


@needs_swipl
@pytest.mark.parametrize("goal", ["X = 1", "X = 1.", "X = 1 % a comment"])
def test_a_goal_reads_with_or_without_its_full_stop(goal: str) -> None:
    assert _bindings(query("", goal)) == [{"X": "1"}]


@needs_swipl
def test_check_program_lists_the_predicates_and_runs_nothing() -> None:
    result = check_program(FAMILY + "greeting --> [hello], world.\nworld --> [world].\n")
    _draft(result, PROGRAM_READ)
    assert [(p["name"], p["arity"], p["clauses"], p["line"]) for p in result.predicates] == [
        ("parent", 2, 5, 1), ("ancestor", 2, 2, 6), ("greeting", 2, 1, 8), ("world", 2, 1, 9)]
    assert result.answers is None and "nothing was queried" in result.warning_card["message"]


@needs_swipl
def test_swipl_version_reads_the_version_through_the_outside_program_declaration() -> None:
    result = swipl_version()
    _draft(result, VERSION_READ)
    assert result.swipl_version.split(".")[0].isdigit()
    assert result.evidence[0]["run"]["argv"][1:] == ["--version"]


@needs_swipl
def test_the_program_and_the_goal_never_reach_a_command_line() -> None:
    program = FAMILY + "secret_marker_clause(abc123).\n"
    goal = "ancestor(tom, Who), Who \\== secret_goal_marker"
    result = query(program, goal)
    assert result.completion_state == "qualified-draft"
    for run in (result.evidence[0]["search"], result.evidence[0]["recheck_run"]):
        argv = run["argv"]
        assert argv[1:] == [*runner.FLAGS, f"--stack-limit={runner.DEFAULT_MEMORY_MB // 2}m", runner.DRIVER_FILE]
        assert not any("secret" in part or "ancestor" in part for part in argv)
        assert not Path(run["run_dir"]).exists()



@needs_swipl
def test_a_planted_wrong_answer_is_refuted_by_the_recheck(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1: patch the first run's parsed output to add a bogus solution. The re-check, in a second process, must refuse
    it; without the re-check the bogus answer would be reported as a qualified draft."""
    honest = query("", "append(X, Y, [1,2,3])")
    _draft(honest, ANSWERS)  # survival control: the same query, unplanted, passes
    read = driver.read_output
    calls = []

    def planted(text: str, nonce: str, exit_code: int) -> dict:
        found = read(text, nonce, exit_code)
        calls.append(found)
        if len(calls) == 1:
            found["answers"].append({"bindings": {"X": "[9]", "Y": "[]"}, "text": "['X'=[9],'Y'=[]]"})
        return found

    monkeypatch.setattr(driver, "read_output", planted)
    result = query("", "append(X, Y, [1,2,3])")
    _blocked(result, CERTIFICATE_REFUTED)
    assert "answer 5" in result.reason and "[9]" in result.reason
    verdicts = [v["verdict"] for v in result.recheck["verdicts"]]
    assert verdicts == ["agreed"] * 4 + ["disagreed"] * 2 and result.recheck["passed"] is False
    assert result.recheck["verdicts"][-1]["index"] == -1  # run again, the search gave four answers, not five


@needs_swipl
def test_a_planted_answer_for_a_goal_that_fails_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    read = driver.read_output
    calls = []

    def planted(text: str, nonce: str, exit_code: int) -> dict:
        found = read(text, nonce, exit_code)
        calls.append(found)
        if len(calls) == 1:
            found["answers"] = [{"bindings": {}, "text": "[]"}]
        return found

    monkeypatch.setattr(driver, "read_output", planted)
    result = prove(FAMILY, "ancestor(jim, tom)")
    _blocked(result, CERTIFICATE_REFUTED)
    assert result.succeeded is True and result.recheck["verdicts"][0]["verdict"] == "disagreed"


def _planted(change):  # noqa: ANN001, ANN202 -- a stand-in for driver.read_output
    """driver.read_output with ``change`` applied to the first run's read (the search), never to the re-check's."""
    read = driver.read_output
    calls = []

    def planted(text: str, nonce: str, exit_code: int) -> dict:
        found = read(text, nonce, exit_code)
        calls.append(found)
        if len(calls) == 1:
            change(found)
        return found

    return planted


@needs_swipl
def test_a_free_variable_answer_stays_general_and_is_rechecked_as_given() -> None:
    """I2: an answer that leaves a variable free is re-checked as given: put back, the goal must hold without binding
    it. ``p(_)`` holds for any value, so its answer stays ``_V1``."""
    general = query("p(_).\np(1).\n", "p(X)")
    _draft(general, ANSWERS)
    assert _bindings(general) == [{"X": "_V1"}, {"X": "1"}]
    assert [v["verdict"] for v in general.recheck["verdicts"]] == ["agreed"] * 3


@needs_swipl
def test_an_answer_more_general_than_the_program_gives_is_refuted(monkeypatch: pytest.MonkeyPatch) -> None:
    """I2: planted, the answer X = _V1 for a program that gives only X = 1. Put back, p(_V1) succeeds only by binding
    _V1, which the re-check must not count as agreeing."""
    def general(found: dict) -> None:
        found["answers"] = [{"bindings": {"X": "_V1"}, "text": "['X'=_V1]"}]

    monkeypatch.setattr(driver, "read_output", _planted(general))
    result = query("p(1).\n", "p(X)")
    _blocked(result, CERTIFICATE_REFUTED)
    assert result.recheck["verdicts"][0]["verdict"] == "disagreed" and "answer 1" in result.reason
    assert result.recheck["passed"] is False


@needs_swipl
@pytest.mark.parametrize("change", [lambda found: found["answers"].pop(1), lambda found: found["answers"].reverse()],
                         ids=["an answer missing", "the answers out of order"])
def test_a_finished_search_is_rechecked_by_running_it_again(change, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    """I2: ``exhausted`` is a claim too. Planted, append's four splits with one left out, or in another order: every
    answer left still holds, so only the search run again, with a cap one higher, catches it."""
    honest = query("", "append(X, Y, [1,2,3])")
    _draft(honest, ANSWERS)  # survival control: unplanted, the same query passes, its end re-checked
    assert honest.recheck["verdicts"][-1]["index"] == -1 and honest.recheck["checked"] == 5
    monkeypatch.setattr(driver, "read_output", _planted(change))
    result = query("", "append(X, Y, [1,2,3])")
    _blocked(result, CERTIFICATE_REFUTED)
    last = result.recheck["verdicts"][-1]
    assert (last["index"], last["verdict"]) == (-1, "disagreed") and result.recheck["passed"] is False
    assert "the claim that the search finished" in result.reason


@needs_swipl
def test_the_mcp_probe_runs_when_the_server_allows_prolog() -> None:
    reply = invoke_reply("prolog", "query", '{"program": "", "goal": "append(X, Y, [1,2])"}',
                         allow_sensitive=frozenset({"prolog"}))
    assert check_envelope(reply) == [] and reply["ok"] is True and reply["completion_state"] == "qualified-draft"
    payload = reply["result"]
    assert len(payload["answers"]) == 3 and payload["exhausted"] is True and payload["recheck"]["passed"] is True
    assert reply["warning_card"]["code"] == ANSWERS
