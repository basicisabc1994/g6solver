# Rules: what follows from facts and rules

Use this route when an answer depends on what a set of facts and rules gives, and you are about to state it from your
own reading. Three components hold G6's rule reasoning. Read `g6_describe` for the one you pick before you choose
fields; each op goes through `g6_invoke`.

| The rules are | Component | Start with |
|---|---|---|
| Plain facts and rules, each true or false: permissions, eligibility, dependencies, reachability | `datalog` | `datalog.check`, then `datalog.query` or `datalog.evaluate`; `datalog.explain` shows why an atom holds |
| A Prolog program: recursion over lists or terms, arithmetic | `prolog` | `prolog.check_program`, then `prolog.query` or `prolog.prove` |
| Facts that are only partly certain, spread over a graph | `graph_reasoning` | `graph_reasoning.check`, then `graph_reasoning.reason` or `graph_reasoning.query`; `graph_reasoning.explain` shows how the bounds were reached |

## Facts that arrive over time

A `datalog` session keeps facts over a fixed program: name a `session`, give its `program` on first use, add facts
with `datalog.assert_facts`, and read with the ops above. `datalog.reset` clears the asserted facts and keeps the
program. A session's program does not change; a different program on a read is refused. Both writing ops are
sensitive and dry-run by default, so the person who runs the server allows them.

`prolog` runs SWI-Prolog, a program on the person's machine, so its ops are sensitive too; `prolog.swipl_version`
says whether it is installed.

## What each answer is

Each component checks its own result before it answers and says what it could not check. Keep the label it returns.
A derivation shows that the rules give the conclusion; it does not show that the rules are the right ones.

## Experta

Experta is retired with no engine port: G6 ships no second rule engine. Work that would have gone to a forward-chaining
rule engine goes to a `datalog` session, backward chaining to `prolog`, and rules over uncertain graph facts to
`graph_reasoning`. A `datalog` session keeps facts over a fixed session program; it is not a record of rules that ran
anywhere else.
