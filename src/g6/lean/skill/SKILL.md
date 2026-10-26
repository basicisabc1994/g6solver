---
name: g6-lean
description: Use when you have written or been handed a Lean 4 proof and are about to say it proves a theorem, or when a claim needs a machine-checked proof that a person will inspect, and you need to know the theorem is really declared, has no sorry, uses only Lean's standard axioms and states what you expect; or when you have a statement in Lean and want G6 to search for a proof it then checks the same way.
---

# G6 Lean

Checks a Lean 4 proof of a named theorem, out of process, with Lean installed on your machine. A proof passes only
when Lean itself reports that the theorem is declared in your file, is a `theorem`, has no `sorry`, uses only Lean's
standard axioms and, when you give one, has the statement you expect. `prove` searches for such a proof of your own
statement, and `sketch` asks your model for an outline in words.

## When to use it

- You are about to say "the proof checks" or "Lean accepts it" about a file.
- A proof was generated and may hide a `sorry`, an added `axiom`, `native_decide` or a theorem of a different
  statement.
- A claim must rest on a formal proof a person will inspect, and you want the statement Lean actually checked.
- You have the statement in Lean and want a proof found: `prove` tries the core tactics, then your model's proposals.

## When not to use it

- A decision you can get without a proof (satisfiable, valid, a counterexample): use `formal`.
- An algebra identity or an integral: use `algebra`.
- A proof that needs Mathlib or a Lake project: only `Init` and `Std` may be imported here, so nothing in G6 checks
  it.
- Lean is not installed and the person running the server has not allowed it: say so, and ask them.
- The claim is still in prose: a formal statement is a draft for a person to read before anything is proved about it.

## Ops

- `check_proof`: `source` (one Lean 4 file) is screened, then Lean elaborates it and G6 reads Lean's own `#check`,
  `#print` and `#print axioms` on `theorem` (its full name); `expected_statement` is optional (sensitive: runs Lean, a
  program on your machine, on your source).
- `lean_version`: which Lean would check, from `lean --version`; nothing is checked (sensitive: runs Lean).
- `prove`: `statement` (written as Lean prints it), optional `preamble` (your definitions), `budget` (at most 32
  candidates) and `deadline_s` (the whole search, 600 seconds by default, at most 3600; each check gets what is left): G6 writes `theorem g6_goal : <statement> := <proof>` for `by rfl`, `by decide`, `by simp`, `by omega`,
  then for proofs the block's injected model proposes, each told Lean's errors on the last one; every candidate is
  judged only by `check_proof` against your statement (sensitive: runs Lean).
- `sketch`: the block's injected model outlines a proof of `statement` in words; Lean checks nothing.

## Reading the result

The reply gives `kind`, `statement` (as Lean prints it), `statement_matched`, `axioms`, `messages` (Lean's file, line,
column and text, on your own line numbers) and `refusals` (what the screen refused, with line and column).

- `qualified-draft`, `G6_E_LEAN_PROOF_CHECKED` (on `warning_card.code`; the result's own `code` is empty): Lean's
  kernel accepted a proof of exactly this statement, with only the axioms listed. Whether the statement says what you
  mean, with the definitions in your file, is for a person to inspect; nothing here is `verified`.
- `blocked-escalated`: `G6_E_LEAN_SORRY`, `G6_E_LEAN_NONSTANDARD_AXIOM`, `G6_E_LEAN_AXIOM_DECLARED`,
  `G6_E_LEAN_STATEMENT_MISMATCH`, `G6_E_LEAN_THEOREM_MISSING`, `G6_E_LEAN_NOT_A_THEOREM`, `G6_E_LEAN_CODE_EXECUTION`,
  `G6_E_LEAN_IMPORT_REFUSED`, `G6_E_LEAN_ELABORATION_FAILED`, `G6_E_LEAN_LEAN_MISSING` and the other screen codes.

`prove` returns `proof`, the `source` checked and `check` (that `check_proof` result, unchanged) on
`G6_E_LEAN_PROOF_FOUND`, `qualified-draft`; `candidates` lists each one tried with its code. It is `blocked-escalated`
with `G6_E_LEAN_SEARCH_EXHAUSTED` when none passed, or at once with Lean's own code when no candidate could change the
outcome (Lean missing, with how to install it; a statement Lean prints otherwise), and with `G6_E_LEAN_TIME_LIMIT`
when the deadline passes. A model reply that declares its own theorem is
`G6_E_LEAN_PROPOSAL_REFUSED` and never checked. `sketch` is `qualified-draft` with `G6_E_LEAN_SKETCH_UNCHECKED`, a draft
only; with no model, `G6_E_LEAN_NO_MODEL`.

## Alone and composed

Alone, from Python: `from g6.lean import check_proof, prove, sketch` (`prove(statement, llm=your_caller)`), or
`LeanBlock(llm=your_caller)`; `get_registry().invoke("lean", ...)` builds a block with no model unless you pass
`llm=`. Over MCP: `g6_invoke("lean", "check_proof", params_json)`, which runs only when the person running the server
allows it; `ops` and `help` answer anywhere. Over MCP, `prove` and `sketch` use the model the person set in
`~/.g6/.env` (for example `G6_LLM_PROVIDER=claude_code`), and with none `prove` uses the core tactics only.

Composed:

- Decide the claim with `formal` first when it is decidable; write the Lean proof for what needs one.
- Give `coding_agent_gate`'s `verify_answer` the reply as an evidence item of kind `log` before reporting a proof as
  done (it counts as caller-declared), and hand the printed `statement` to a person to inspect.

## Failure modes it catches

- `unfinished_proof` (a proof that only looks finished): the screen refuses `sorry`, `admit`, `stop`, `axiom` and
  `opaque` declarations and anything that runs code (`#eval`, `native_decide`, `unsafe`, `implemented_by` and the
  like) before Lean runs; then Lean's own `#print axioms`, on lines G6 appends and marks with a nonce the source
  cannot know, must list only `propext`, `Classical.choice` and `Quot.sound`. `sorryAx` is `G6_E_LEAN_SORRY`, any other
  axiom `G6_E_LEAN_NONSTANDARD_AXIOM`.
- `misstated_theorem` (a proof of a different statement from the one you meant): with `expected_statement`, the
  statement Lean prints must match it, and Lean must accept the theorem as a proof of the expected statement read on
  its own, so `2 + 2 = 4` over `Fin 3` does not pass for one over the naturals (`G6_E_LEAN_STATEMENT_MISMATCH`). Lean
  must also report the name unknown before your source, so a theorem from an import cannot pass as yours.
- `unchecked_maths_claim` (a maths claim accepted without a machine-checked proof): `prove` takes a candidate only
  when `check_proof` passed it for `g6_goal` and your statement, by Lean's report, whatever the model says of its own
  proof; G6 writes the theorem line itself, so a reply cannot swap in another statement.

## Blind spots

- Without `expected_statement`, a proof of some other statement under the right name passes; give it whenever you
  can.
- A statement can match and still not mean what the claim in words means, through a definition or instance in your
  file. A person must read it.
- The screen refuses whole classes of constructs (new syntax, most attributes and options, imports beyond `Init` and
  `Std`), so some sound proofs are refused, not checked.
- The screen reads comments and strings too: `theorem two_add : 2 + 2 = 4 := rfl -- not sorry` is refused with
  `G6_E_LEAN_SORRY`. Keep refused words out of comments.
- Trust rests on Lean's kernel and the standard axioms; G6 does not check Lean itself.
- Time and memory caps can stop a long elaboration.
- `prove` tries a fixed order and only `Init` and `Std`: tactics from Mathlib (`ring`, `linarith`) are not there, and
  a model proposing them fails. Write the statement as Lean prints it (`2 + 2 = 4`, not `2+2=4`), or every candidate
  is a mismatch.
- A `sketch` is a model's words: it may be wrong and nothing checks it.
- `deadline_s` cannot cut a model call short (the caller takes no time limit): a proposal can overrun the deadline by
  up to your caller's own timeout; the search then stops before checking anything more.

## What it needs

- Lean 4, an outside program G6 never installs. Install elan, Lean's toolchain manager, as the Lean project describes
  at lean-lang.org (it puts `lean` on PATH), or set `G6_LEAN` to a `lean` executable. G6 never installs a toolchain,
  Lake or Mathlib and never downloads anything. No pip extra.
- Over MCP the three Lean ops run only when the person running the server lists them (or `lean`) in
  `G6_MCP_SENSITIVE`; an agent never edits that setting to grant itself one.
- For model proposals and sketches, your own `g6.core.llm` caller, passed from Python; G6 never picks a model.

## Examples

```g6-example op=check_proof
{"source": "theorem two_add : 2 + 2 = 4 := rfl", "theorem": "two_add", "expected_statement": "2 + 2 = 4"}
```

With Lean installed and the op allowed, the reply is `qualified-draft` with `G6_E_LEAN_PROOF_CHECKED`, `kind`
`theorem`, `statement` `2 + 2 = 4`, `statement_matched` true and `axioms` empty.

The screen and the declaration check run before Lean is looked for, so this runs anywhere:

```python
from g6.lean import check_proof

gap = check_proof("theorem two_add : 2 + 2 = 4 := by sorry", "two_add")
assert gap.completion_state == "blocked-escalated" and gap.code == "G6_E_LEAN_SORRY"
assert gap.refusals[0]["line"] == 1
assert check_proof("-- nothing here", "two_add").code == "G6_E_LEAN_THEOREM_MISSING"
assert check_proof("def two_add : Nat := 4", "two_add").code == "G6_E_LEAN_NOT_A_THEOREM"
```

`sketch` with no model configured says so, and how to configure one:

```g6-example op=sketch
{"statement": "∀ (n : Nat), n + 0 = n"}
```

A model's reply that declares its own theorem is refused before any check, and a sketch is labelled a draft:

```python
from g6.core.llm import FakeLLM
from g6.lean import prove, sketch

swapped = prove("2 + 2 = 5", llm=FakeLLM(default="theorem g6_goal : True := trivial"), tactics=(), budget=1)
assert swapped.completion_state == "blocked-escalated" and swapped.code == "G6_E_LEAN_SEARCH_EXHAUSTED"
assert swapped.candidates[0]["code"] == "G6_E_LEAN_PROPOSAL_REFUSED"
draft = sketch("2 + 2 = 4", llm=FakeLLM(default="Both sides reduce to 4."))
assert draft.warning_card["code"] == "G6_E_LEAN_SKETCH_UNCHECKED" and draft.check is None
```

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch is a labelled result with its
  own `G6_E_LEAN_*` code, Lean's messages and the screen's refusals in `evidence`; `prove` lists each candidate's
  code, the success per tactic on your own goals; recording it is the caller's step today.
- **Evaluators the model cannot influence.** The judge is Lean's own report, read from lines G6 appends with a nonce
  the source cannot know, plus the package's own screen. A model only proposes text for `prove`; Lean, through
  `check_proof`, judges it. The caller supplies the proof, never the check.
- **Transfer by failure mode.** The checks belong to proofs, not to a task: an axiom list or a `sorry` means the same
  in any file. Not yet: its catch rate on task families it was not built on has not been measured, nor any transfer
  of the tactic order by goal shape.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test; the tactic order is fixed, not
  tuned on any goals. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
