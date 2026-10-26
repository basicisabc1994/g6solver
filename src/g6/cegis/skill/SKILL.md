---
name: g6-cegis
description: Use when code has a fixed shape with a few unknown parts (constants, a sign, a comparison, a choice between strings) and you are about to pick those parts by hand from a handful of examples, or to trust generated code because it passed the examples it was written from.
---

# G6 CEGIS

Fills the holes in a Python code sketch by counterexample-guided synthesis: fit the examples, check the candidate on
an explicit input domain, add the input where it fails, repeat. Every candidate runs in a child process, and a result
says exactly which domain it rests on.

## When to use it

- A function's shape is known and a few constants or choices are not: `return x * ?? + ??`,
  `x if ??b else -x`, `s + ??{"!","?"}`.
- Generated code passed the examples it was shown, and you need to know whether it also holds on the inputs nobody
  showed it.
- You have an oracle (a reference function, or a table of cases) and want the smallest program of that shape that
  agrees with it on a whole domain.

## When not to use it

- Code with no fixed shape: write it, then test it with the repository's tests and report through
  `coding_agent_gate`.
- A small distilled function to check and test on held-out cases: use `distillation`.
- A property to decide with no program to synthesise: use `formal`.
- Over MCP: it has no block, so `g6_invoke` cannot reach it; it is Python only.

## Python API

- `fit_template`: fits one explicit numeric family on training `HoldoutCase` records, returns a frozen `Sketch`
  and a `TemplateFit` report judged by `g6.distillation.evaluate` on held-out cases with a required negative control.
- `TemplateFit`: `family`, `sketch`, `report` and `completion_state`; only a passed held-out report is a qualified draft.

- `cegis`: the loop; `cegis(sketch, oracle, domain=..., verifier=None, max_iterations=20, timeout_s=120.0)` returns a
  `CegisResult`.
- `Sketch.from_src`: a sketch from source with `??` (an integer, from `lo` to `hi`, -32 to 32 by default), `??b` (a
  boolean) and `??{"a","b"}` (one of those strings) holes; a sketch the pre-filter refuses raises `ValueError`.
- `InputDomain.ints`: integer arguments over a range, every combination or `max_inputs` drawn with `seed`.
- `InputDomain.of`: arguments with listed values.
- `TableOracle`: an oracle that is a table of input and output cases; an input it does not list is unanswered and
  counted as skipped.
- `Z3Verifier`: an optional second check: Z3 looks for any input of the whole domain on which the candidate breaks an
  SMT-LIB `spec` over the arguments and `result`.
- `SubprocessRunner`: runs candidates in a child process with a wall-clock allowance (`timeout_s`), a memory cap
  (`mem_mb`; none on macOS, which refuses it) and a kill of the whole process tree.
- `CegisResult`: `status`, `control`, `program`, `counterexamples`, `boundary`, `trace`, `reason` and
  `completion_state`.
- `CegisResult.to_envelope`: the result as the library envelope, with a `G6_E_CEGIS_*` warning card.
- `Counterexample`: a verifier's input where the candidate breaks the spec.
- `NoCounterexample`: a verifier's decision that no input of its encoded domain breaks the spec.
- `Inconclusive`: a verifier with no decision, with its `degradation_reason`.

## Reading the result

Numeric families: linear, polynomial (degree 1..3), power, clamp, rational, piecewise_linear, exponential, log,
affine_vec and scaled_sigmoid. Choose a family before seeing held-out outcomes. Fitted coefficients fill source
text, which becomes a `Sketch`; candidate source never runs in this process. Training execution must match every
sample, then distillation runs the frozen candidate and controls. Shared case ids or equal training inputs are
refused as leakage, including renamed cases. A fit takes 2 to 10000 training and 1 to 10000 held-out cases, and at
most 1000000 training-by-held-out pairs, since the leak check compares every pair in this process. No CLI, model
fallback, promotion or reuse store is involved.
`G6_E_CEGIS_TEMPLATE_*` refusals name missing dependencies, invalid samples, failed fitting/execution or leakage.

- `no_counterexample_in_domain`: `qualified-draft` with `G6_E_CEGIS_DOMAIN_BOUNDED`. The program agrees with the oracle
  on every input of the domain (or of its draw) the oracle answered; `boundary` names the domain, whether it was exhaustive or drawn,
  how many inputs were compared and how many skipped. It is not a proof outside that domain, and nothing here is
  `verified`.
- `blocked-escalated`: `counterexample_budget_exhausted` (`G6_E_CEGIS_BUDGET_EXHAUSTED`), `unsat` (no control of the
  sketch fits, said only after an exhaustive search, `G6_E_CEGIS_UNSAT`), `timeout` (`G6_E_CEGIS_TIMEOUT`) and
  `inconclusive` (`G6_E_CEGIS_INCONCLUSIVE`: nothing compared, a search that was not exhaustive, or a verifier with no
  decision, never read as a pass).

## Alone and composed

Alone, from Python: `from g6.cegis import Sketch, InputDomain, cegis`. There is no MCP op.

Composed:

- `Z3Verifier` asks `formal` (`g6.formal.check_sat`) for a counterexample over the whole domain. Its counterexample is
  fed back only after the candidate, run out of process, disagrees with your oracle there.
- `g6.metaharness` names it as a remedy for wrong answers on code tasks: for code of a fixed shape with a few unknown
  parts, search the parts against tests.
- Its sketches pass the AST pre-filter `g6.core.source_check`, shared with `distillation`.

## Failure modes it catches

- `overfit_to_examples` (generated code that only works on the examples it was shown): a candidate that fits the
  examples is then run on every input of the domain (or the `max_inputs` drawn from a larger one) that your oracle
  answers, out of process; the first disagreement
  becomes a new example and the search runs again. A result reports how many inputs were compared and how many the
  oracle skipped, so a fit to two cases cannot pass for a fit to the domain, and an oracle that answers nothing gives
  `inconclusive`.

## Blind spots

- A result holds only on its domain. Outside it, nothing is claimed. A domain larger than `max_inputs` (2000 by
  default) is checked only on a seeded draw of that many inputs, and `boundary` says so (`exhaustive` false).
- Your oracle is the standard. If it is wrong, the program agrees with a wrong answer; it runs in your own process.
- A `TableOracle` compares only the cases it lists: with two cases, `compared` is 2, whatever the domain's size.
- The child is not an operating-system sandbox: it runs as the same user, behind an AST pre-filter, a time limit, a
  memory cap (except on macOS) and, on POSIX, CPU and file-size limits.
- `Z3Verifier` reads only one `return` of int and bool arithmetic, comparisons and conditionals; anything else is
  `inconclusive` (`z3_untranslatable`), never approximated.
- Hole values are bounded (integers from `lo` to `hi`); a constant outside the range cannot be found.

Numeric fitting identifies only these families. Rational fitting fixes the denominator constant to one; it cannot
represent poles at zero in that form. Clamp needs both plateaus. Log uses a bounded series in generated code.
Rank-deficient samples refuse. Near-duplicates and repeated tuning against the same holdout can still leak.

## What it needs

- `pip install "g6[optim]"` for numeric fitting: NumPy and SciPy (BSD-3-Clause), loaded at call time.
  Without either, fitting returns `G6_E_CEGIS_TEMPLATE_EXTRA`. The existing integer-hole loop needs neither.

- `pip install "g6[formal]"` for Z3, used only by `Z3Verifier`; without it every verifier check is
  `inconclusive` (`z3_unavailable`). The loop itself runs on Python alone.
- No outside program: candidates run in a child Python process that G6 starts itself.

## Examples

```python
from g6.cegis import InputDomain, Sketch, TableOracle, Z3Verifier, cegis

sketch = Sketch.from_src("def f(x):\n    return x * ?? + ??\n")
domain = InputDomain.ints(["x"], -10, 10)

found = cegis(sketch, lambda x: 3 * x + 1, domain=domain)
assert found.status == "no_counterexample_in_domain" and found.control == {0: 3, 1: 1}
assert found.boundary["compared"] == 21 and found.completion_state == "qualified-draft"
assert found.to_envelope()["warning_card"]["code"] == "G6_E_CEGIS_DOMAIN_BOUNDED"

two_cases = cegis(sketch, TableOracle.of([({"x": 0}, 1), ({"x": 1}, 4)]), domain=domain)
assert two_cases.boundary["compared"] == 2 and two_cases.boundary["skipped"] == 19

checked = cegis(sketch, lambda x: 3 * x + 1, domain=domain, verifier=Z3Verifier("(= result (+ (* 3 x) 1))"))
assert checked.boundary["verifier"]["outcome"] == "no_counterexample"
```

The second run fits the same program from two cases, and its `boundary` says only 2 of 21 inputs were compared.

## The four open problems

- **Learning signals.** Not yet: it writes nothing to a run ledger itself. Each result keeps its counterexamples,
  `trace` and `boundary`, the labelled record a learner needs; recording it is the caller's step today.
- **Evaluators the model cannot influence.** The check is your oracle on an explicit domain, with candidates run out
  of process, and optionally Z3; no model call runs anywhere in the package (`boundary` records `model_calls` as 0).
  The candidate never judges itself.
- **Transfer by failure mode.** The loop belongs to sketches, not to a task: any sketch and oracle are checked the same
  way. Not yet: its catch rate on task families it was not built on has not been measured.
- **Enough held-out cases.** Each candidate is checked on every answered input of the domain (or of its draw), not only on the
  examples it was fitted to, and `boundary` says how many were compared. Not yet: its own catch rate and false-alarm
  rate are not judged by a sequential test.
