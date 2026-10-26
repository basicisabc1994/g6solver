---
name: g6-prolog
description: Use when an answer depends on what a Prolog program derives (rules with recursion, lists, arithmetic or terms with structure) and you are about to state the answers, or that a goal does or does not follow, from your own reading of the program instead of running it.
---

# G6 Prolog

Runs a goal against a Prolog program with SWI-Prolog, installed on your machine, out of process: a fresh `swipl` for
each call, with no start-up files and no packs, bounded by inference, time and stack limits. The program is read clause
by clause and never consulted, so no directive runs; the program and the goal must keep to a restricted syntax (below);
and every answer is re-checked in a second, fresh SWI-Prolog process.

The restricted syntax is not a security boundary, and neither is SWI-Prolog's sandbox library, which checks the goal as
an extra layer: SWI-Prolog runs as you. Run programs you do not trust inside your own sandbox, such as a container or a
virtual machine.

## When to use it

- You are about to say "the program gives these answers" or "this goal follows" about Prolog you wrote or were given.
- The rules recurse, build lists or terms, or do arithmetic, where reading by eye misses or invents an answer.
- You need every answer the program gives, and to know whether the search finished.
- You want to know whether a program reads at all, and which predicates it defines, before you rely on it.

## When not to use it

- Facts and rules with no function symbols or arithmetic, where you want each answer's derivation: use `datalog`,
  which needs no outside program and shows why each answer holds.
- Facts with degrees of truth or confidence: use `graph_reasoning`.
- Arithmetic, quantifiers or a counterexample over numbers: use `formal`.
- A program that reads or writes files, prints, changes the database (`assertz/1`), catches errors, uses constraints
  (`dif/2`, a constraint library) or calls a goal it builds while running (`call/1`): the restricted syntax refuses it,
  by design. Run it in your own SWI-Prolog.
- A program you do not trust, run outside your own sandbox: G6 does not contain it.
- SWI-Prolog is not installed and the person running the server has not allowed it: say so, and ask them.

## Ops

- `query`: the answers to `goal` (one Prolog goal, such as `ancestor(tom, X)`) from `program`, at most
  `max_solutions` of them (100 by default), each as variable bindings, with `exhausted` (sensitive: runs SWI-Prolog,
  a program on your machine).
- `prove`: whether `goal` is derivable from `program` (`succeeded`), with its first answer (sensitive: runs
  SWI-Prolog).
- `check_program`: SWI-Prolog's read of `program`: the predicates it defines, and each syntax error, directive, refused
  clause and goal outside the restricted syntax with its line and column; nothing is queried (sensitive: runs
  SWI-Prolog).
- `swipl_version`: which SWI-Prolog would run a program, from `swipl --version` (sensitive: runs SWI-Prolog).

## The restricted syntax

- No directives (`:- ...`, `?- ...`). Grammar rules (`-->`) are read as the clauses they translate to.
- A program defines only its own predicates: no clause for a built-in, a library predicate (`append/3`, `member/2`:
  call yours `my_append/3`, or use the library's) or a hook SWI-Prolog calls (`portray/1`, `message_hook/3`), and no
  `Module:Head`.
- Every goal, in a clause body and in `goal`, calls a predicate the program defines or one on the allow-list
  (`allowed/1` in the package's `driver.pl`): `true`, `fail`, `false`, `!`; `=`, `\=`, `==`, `\==`, `@<`, `@>`, `@=<`,
  `@>=`, `compare/3`; `is`, `=:=`, `=\=`, `<`, `>`, `=<`, `>=`, `between/3`, `succ/2`, `plus/3`; `var`, `nonvar`,
  `atom`, `number`, `integer`, `float`, `atomic`, `compound`, `callable`, `is_list`, `ground`; `functor/3`, `arg/3`,
  `=..`, `copy_term/2`; `atom_codes/2`, `atom_chars/2`, `char_code/2`, `atom_number/2`, `number_codes/2`,
  `number_chars/2`, `atom_length/2`; `member/2`, `memberchk/2`, `append/3`, `length/2`, `nth0/3`, `nth1/3`, `last/2`,
  `reverse/2`, `msort/2`, `sort/2`, `sort/4`, `sum_list/2`, `max_list/2`, `min_list/2`, `numlist/3`, `list_to_set/2`.
- The control constructs (`,`, `;`, `->`, `*->`, `\+`) and `findall/3`, `bagof/3`, `setof/3` (with `^`), `forall/2`
  and `aggregate_all/3` (`count`, `sum`, `max`, `min`, `bag`, `set`) have their goal arguments checked the same way.
  `maplist/2` to `maplist/4` are allowed when the closure names a predicate (`maplist(double, L, M)`), which is
  checked with its added arguments; `call/N`, `foldl` and the other meta predicates are left out.
- Never a variable goal, the goal argument of `findall/3` and the others included, and never `Module:Goal`.

## Reading the result

`answers` lists each answer's `bindings` (each named variable of the goal and its value as Prolog text; a variable
left free is `_V1`, `_V2` and so on) and `text`, the whole answer as the re-check read it. `exhausted` is true only
when the search finished below `max_solutions`. `recheck` holds the second process's verdict on each answer and, when
the search finished, on its end (index -1). `problems` hold what was refused or did not read, with `line` and `column`
(counted from 0, as SWI-Prolog counts). `time_limit` is at most 600 seconds; `inference_limit` is 10,000,000 by
default; `memory_mb` is 2048 by default.

- `qualified-draft` (on `warning_card.code`; the result's own `code` is empty): `G6_E_PROLOG_ANSWERS`,
  `G6_E_PROLOG_NO_ANSWERS`, `G6_E_PROLOG_GOAL_DERIVABLE`, `G6_E_PROLOG_GOAL_NOT_DERIVABLE`,
  `G6_E_PROLOG_PROGRAM_READ` and `G6_E_PROLOG_VERSION_READ`. Nothing here is `verified`.
- `blocked-escalated`: `G6_E_PROLOG_GOAL_NOT_ALLOWED` (a goal outside the restricted syntax; the reason names the
  predicate and where it appears), `G6_E_PROLOG_UNDEFINED_PREDICATE`, `G6_E_PROLOG_DIRECTIVE_REFUSED`,
  `G6_E_PROLOG_CLAUSE_REFUSED`, `G6_E_PROLOG_SYNTAX_ERROR`, `G6_E_PROLOG_UNSAFE_GOAL` (SWI-Prolog's sandbox library
  refused a goal the restricted syntax allowed), `G6_E_PROLOG_GOAL_ERROR`, `G6_E_PROLOG_INFERENCE_LIMIT`,
  `G6_E_PROLOG_TIME_LIMIT` and `G6_E_PROLOG_MEMORY_LIMIT` (the answers found before the limit are kept, not
  re-checked), `G6_E_PROLOG_CERTIFICATE_REFUTED` (the re-check disagreed), `G6_E_PROLOG_RECHECK_UNFINISHED` and
  `G6_E_PROLOG_SWIPL_MISSING`.

## Alone and composed

Alone, from Python: `from g6.prolog import query, prove, check_program` (or
`get_registry().invoke("prolog", "query", {...})`). Over MCP: `g6_invoke("prolog", "query", params_json)`, which runs
only when the person running the server allows it; `ops` and `help` answer anywhere.

Composed:

- Run `check_program` first on a program you did not write, then `query` or `prove`.
- When the same rules fit Datalog, run them through `datalog` too: two engines that agree are worth more than one.
- Give `coding_agent_gate`'s `verify_answer` the reply as an evidence item of kind `log` before reporting a
  conclusion that rests on it (it counts as caller-declared).

## Failure modes it catches

- `unsupported_conclusion` (a conclusion the rules and facts do not support): `prove` answers from the program as
  SWI-Prolog runs it, not from a reading of it, and each answer is re-checked: put back into the goal, it must hold
  again as given, its free variables left free, in a second, fresh process, or the reply is
  `G6_E_PROLOG_CERTIFICATE_REFUTED`. A goal that fails comes back not derivable, and the failure is re-checked too.
- `missed_implication` (a consequence the rules imply that goes unnoticed): `query` collects every answer the program
  gives up to `max_solutions`, and `exhausted` says whether the search finished; when it did, the search is run again
  in the second process with a cap one higher and must give the same answers in the same order and no more, so a
  cut-short list is never passed off as complete.

## Blind spots

- Not a security boundary: the restricted syntax and SWI-Prolog's sandbox library check what a program calls, but
  SWI-Prolog runs as you, and containing a program you do not trust is your own sandbox's job (a container or a
  virtual machine).
- Not derivable means not derivable from this program under Prolog's closed world, not false: a fact or rule the
  program leaves out makes a true goal fail.
- The answers are what your program says as SWI-Prolog runs it, depth first. A wrong fact or rule gives a wrong answer
  that passes every check, and a left-recursive rule loops until a limit stops it.
- The restricted syntax refuses whole classes of harmless programs: anything that prints, reads, asserts, catches,
  uses constraints or calls a goal built while running; a predicate named like a library one must be renamed.
- No directives at all: no operator, flag, `dynamic`, `table` or module declarations.
- An answer that is a cyclic term is refused (`G6_E_PROLOG_GOAL_ERROR`), not reported.
- Memory: SWI-Prolog's stacks are capped on every system at half of `memory_mb` (at least 64 MB); the whole process
  is capped only on Windows. Inference, time and stack limits stop long or large searches.

## What it needs

- SWI-Prolog, an outside program G6 never installs (BSD-2-Clause). Install it from swi-prolog.org, which puts `swipl`
  on PATH, or set `G6_SWIPL` to the `swipl` program. No pip extra: caller code never runs in G6's process.
- Over MCP all four ops run only when the person running the server lists them (or `prolog`) in `G6_MCP_SENSITIVE`;
  an agent never edits that setting to grant itself one.

## Examples

```g6-example op=query
{"program": "parent(tom, bob).\nparent(bob, ann).\nancestor(X, Y) :- parent(X, Y).\nancestor(X, Z) :- parent(X, Y), ancestor(Y, Z).\n", "goal": "ancestor(tom, Who)"}
```

With SWI-Prolog installed and the op allowed, the reply is `qualified-draft` with `G6_E_PROLOG_ANSWERS`: `Who` is
`bob`, then `ann`, `exhausted` is true and `recheck.passed` is true.

```g6-example op=prove
{"program": "parent(tom, bob).\nparent(bob, ann).\n", "goal": "parent(ann, tom)"}
```

`qualified-draft` with `G6_E_PROLOG_GOAL_NOT_DERIVABLE` and `succeeded` false: not derivable from this program, which
is not the same as false.

```g6-example op=query
{"program": "", "goal": "shell(ls)"}
```

`blocked-escalated` with `G6_E_PROLOG_GOAL_NOT_ALLOWED`: `shell/1` is not on the allow-list, so the goal never runs.

```python
from g6.prolog import query

found = query("parent(tom, bob).", "parent(tom, Who)")
if found.code == "G6_E_PROLOG_SWIPL_MISSING":  # SWI-Prolog is not installed here
    assert "swi-prolog.org" in found.warning_card["suggested_action"]
else:
    assert found.answers[0]["bindings"] == {"Who": "bob"} and found.completion_state == "qualified-draft"
    assert query("", "shell(ls)").code == "G6_E_PROLOG_GOAL_NOT_ALLOWED"
assert query("p(1).", "p(X)", time_limit=601).code == "G6_E_PROLOG_TIME_LIMIT_TOO_LONG"
```

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_PROLOG_*` code, SWI-Prolog's messages in `problems` or `reason` and the re-check in `recheck`; recording
  it is the caller's step today.
- **Evaluators the model cannot influence.** The answers are SWI-Prolog's, from a driver G6 writes, and the re-check is
  a second SWI-Prolog process; no model call runs anywhere in the package. The caller writes the program and the goal,
  never the check.
- **Transfer by failure mode.** The re-check belongs to Prolog, not to a task: any answer is put back into its goal
  the same way. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
