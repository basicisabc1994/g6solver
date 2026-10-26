---
name: g6-algebra
description: Use when an answer, a test expectation or code rests on a symbolic calculation (simplifying, factoring, solving an equation, a derivative, an integral, a limit, a series, a matrix's determinant, inverse or eigenvalues, or an integer's factors, primality or greatest common divisor) and you are about to state the result from your own working.
---

# G6 algebra

Computer algebra with SymPy, run in G6's own worker process on input read only by G6's own parser. Every SymPy
answer goes through a second, independent re-check (SageMath's do not); an answer the re-check refutes is blocked, and
one it cannot decide is reported as not re-checked.

## When to use it

- You are about to write a formula, root, derivative, integral, limit or series into an answer, a test or code.
- A draft says "simplifies to", "the roots are" or "the integral is", and the working is yours.
- You need a matrix's determinant, inverse or eigenvalues, or an integer's prime factors, primality or greatest common
  divisor, exactly.
- You need to know whether a solution set is complete, not only whether each root works.

## When not to use it

- A logic or constraint question (valid, satisfiable, a counterexample): use `formal`.
- The best schedule, allocation or route: use `optimisation`.
- A proof a person will inspect: use `lean`.
- Statistics or fitting measurements: this is exact algebra; ordinary Python is the better tool.
- Arithmetic you can read at a glance: nothing.

## Ops

- `simplify`: SymPy's simplest form, re-checked at random points and by the difference simplifying to 0.
- `expand`: products and powers multiplied out, re-checked numerically and, where rational, exactly.
- `factor`: factors over the rationals, re-checked by expanding them back.
- `differentiate`: the derivative of `expression` in `variable` (`order`, default 1), re-checked by numeric
  differentiation of G6's own evaluation.
- `integrate`: an antiderivative, re-checked by differentiating it back; with `lower` and `upper` (`oo` allowed) a
  definite integral, re-checked by numeric quadrature.
- `solve`: one equation or a small system (`equations`, `unknowns`, `domain` `complex` or `real`); every solution
  substituted back, and `complete` says whether SymPy reports the set as complete. Domain `real` is for one equation
  in one unknown only.
- `limit`: the limit at `point` from `direction` (`+-`, `+` or `-`), re-checked by approaching the point numerically.
- `series`: the Taylor series to `order`, each coefficient re-checked against derivatives.
- `determinant`: re-checked against mpmath's determinant of G6's evaluation and, for rational entries, by G6's own
  exact elimination.
- `inverse`: re-checked by multiplying back to the identity.
- `eigenvalues`: with multiplicities, each re-checked by det(A - lambda I) = 0 and against mpmath's own eigenvalues.
- `factor_integer`: the prime factors of `n`, re-checked by multiplying back and G6's own primality test.
- `is_prime`: SymPy's answer for `n`, re-checked by G6's own test.
- `gcd`: the greatest common divisor of `numbers`, re-checked by Bezout coefficients.
- `sage_evaluate`: one expression in SageMath's syntax, screened by G6's parser, then evaluated by SageMath and not
  re-checked (sensitive: runs SageMath, a program on your machine).
- `sage_version`: which SageMath would compute, from `sage --version` (sensitive: runs SageMath).

## Reading the result

`answer` is SymPy's canonical text and `latex` the same in LaTeX; `answers` lists the parts of a many-part answer.
`input` is G6's own reading of what you asked: check it says what you meant. `recheck.status` is `passed`, `refuted`
or `undecided`.

A draft's code is on `warning_card.code`; the result's own `code` is empty unless it is blocked.

- `qualified-draft`, `G6_E_ALGEBRA_RECHECKED`: the re-check passed. This is the best state; nothing here is
  `verified`.
- `qualified-draft`, `G6_E_ALGEBRA_NOT_RECHECKED`: SymPy answered but the re-check could not decide. Say so if you use
  it.
- `qualified-draft`, `G6_E_ALGEBRA_SAGE_ANSWER`: SageMath's answer, not re-checked; `G6_E_ALGEBRA_SAGE_VERSION_READ`:
  the version `sage_version` read, nothing computed.
- `blocked-escalated`, `G6_E_ALGEBRA_CERTIFICATE_REFUTED`: the re-check found the answer wrong; it is kept in
  `evidence`, not in `answer`. Other `G6_E_ALGEBRA_*` codes name a parser refusal (with line and column), a size or
  time limit, or SymPy missing.

The `variable` and `unknowns` declare themselves; every other name must be listed in `symbols`, as a list or with
assumptions (`{"a": ["real", "positive"]}`). Write products with `*`, powers with `^` or `**`.

## Alone and composed

Alone, over MCP: `g6_invoke("algebra", "solve", params_json)`. From Python: `from g6.algebra import solve, integrate`
(each returns an `AlgebraResult`), or `get_registry().invoke("algebra", "solve", {...})` from `g6.core.registry`.

Composed:

- Before reporting work that rests on a calculation, give `coding_agent_gate`'s `verify_answer` the reply as an
  evidence item of kind `log` (the op as `ref`, the reply as `output`). It counts as caller-declared.
- Send the logical step around the calculation (that a bound found here implies the claim) to `formal`, and a search
  for the best value under constraints to `optimisation`.

## Failure modes it catches

- `wrong_symbolic_result` (a wrong formula, root or integral stated with confidence): G6 evaluates the original from
  its own parse tree on mpmath, never through SymPy, and compares it with SymPy's answer at seeded random points and,
  where the input is rational, exactly in fractions; roots are substituted back at 60 digits, inverses multiplied back,
  factors expanded back. A disagreement blocks the answer with `G6_E_ALGEBRA_CERTIFICATE_REFUTED`.

## Blind spots

- Where the input is not rational, several re-checks are numeric only: agreement at three or more seeded points
  within a tolerance (one when nothing varies): a check, not a proof.
- `solve` shows each solution works, not that none is missing: `complete` is SymPy's own report.
- Some answers are not re-checked and say so (`G6_E_ALGEBRA_NOT_RECHECKED`): a Laurent or Puiseux series, a series at
  infinity, a derivative through `abs`, `sign`, `floor`, `ceiling`, `re`, `im` or `conjugate` of a variable not
  declared real, a re-check that ran out of time.
- For `is_prime`, SymPy's answer is definitive below 2^64 and G6's own test decides below about 3.3e24; above that, a
  prime comes back not re-checked.
- SageMath's answers are not re-checked at all.
- It checks the answer to the input G6 read. If the equation you sent is not the problem you meant, the re-check
  passes on the wrong problem.
- The language is fixed: numbers, declared symbols, `+ - * / ^`, a listed set of functions and `pi`, `E`, `I`.
  Anything else is refused, not approximated.

## What it needs

- `pip install "g6[cas]"` for SymPy. Without it every SymPy op is `blocked-escalated` with
  `G6_E_ALGEBRA_CAS_UNAVAILABLE` and the install line.
- SageMath, only for `sage_evaluate` and `sage_version`. It is GPL, so G6 never installs, bundles or imports it:
  install it yourself as sagemath.org describes, then put `sage` on PATH or set `G6_SAGE` to the sage program. Over
  MCP these two ops run only when the person running the server lists them (or `algebra`) in `G6_MCP_SENSITIVE`; an
  agent never edits that setting to grant itself one.

## Examples

```g6-example op=solve
{"equations": ["x^2 - 5*x + 6 = 0"], "unknowns": ["x"]}
```

The reply is `qualified-draft` with `G6_E_ALGEBRA_RECHECKED`: `answer` is `{2, 3}`, `complete` is true, and
`recheck.methods` says each solution was substituted back at 60 digits.

```g6-example op=integrate
{"expression": "sin(x)", "variable": "x", "lower": 0, "upper": "pi"}
```

`answer` is `2`, re-checked by mpmath's quadrature of G6's own evaluation of the integrand.

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each catch comes back as a
  `blocked-escalated` result with its own `G6_E_ALGEBRA_*` code and the refuted answer in `evidence`, which is the
  labelled record a learner needs; recording it is the caller's step today.
- **Evaluators the model cannot influence.** The re-checks are the package's own code: G6's parser and its mpmath
  evaluation of the original, never SymPy's reading of it, and no model call anywhere in the package. The caller
  chooses the input, never the check.
- **Transfer by failure mode.** Each re-check belongs to the operation, not to a task: substituting every root back
  works for any equation the language can write. Not yet: its catch rate on task families it was not built on has not
  been measured.
- **Enough held-out cases.** Not yet: its own catch rate and false-alarm rate are not judged by a sequential test. A
  repeated workflow that uses it can be tuned with `g6.metaharness`, whose gate judges a change on held-out cases.
