# M1b result: closed at acquisition (2026-11-21)

## Outcome
The owner closed M1b at acquisition. The K_0 funnel admitted none of the 9 acquisition bugs, so no label was read, no
construction was labelled and CV3 counted 0 of the 3 repositories it needs. Under CV4 the pilot makes no statement
about constructions or distillation.

## What ran
- **Configuration.** The frozen chooser selected K_0 from the inventory (`INVENTORY_RESULT.md`): 13 repositories,
  reduced tier, with roles 4 acquisition, 4 admission and 2 monitor. Reuse was not measured.
- **Draw.** `allocation_m1b.py` made the draw (`allocation_m1b.json`). Its `--check` recomputed the binding,
  recounted every configuration, re-ran the chooser and rebuilt the manifest byte for byte.
- **Acquisition units.** 9 bugs in 4 repositories:
  - marshmallow-code/marshmallow#3024;
  - pytest-dev/pytest-mock#604 and #617;
  - pytest-dev/pytest-xdist#1324;
  - r1chardj0n3s/parse#230, #256, #257, #258 and #261.
- **Funnel.** `run_m1b.py` read each bug's material from git and ran the suites on the host under AppContainer
  isolation.
- **Run 1 aborted before any result was written.** An operator's output did not compile: a statement was inserted
  ahead of `from __future__`. The arm now records such a variant as a construction refusal. The fix went through
  a review loop of 4 reviews and 3 repairs, with findings 3, 4, 3 and 2, every one reproduced. The repairs:
  - keep a settled label when the headline step cannot run;
  - count only headline-eligible strata (`witness_positive`, `n1`, `n2`) in CV1-CV3;
  - end the run on our own construction or headline errors instead of recording them as material.
- **Run 2.** Made at the end of that loop (commit 38039a13) in 9 suite runs and 79 s. No admission or monitor unit
  was run.

## Result
Every acquisition bug stopped at `fixed_suites`. That stage requires the fixed code to pass its maintainers' previous
and new tests.

| Repository | Bugs | Fixed suite | Mechanism |
|---|---|---|---|
| r1chardj0n3s/parse | 5 | did not settle | A test is skipped on every current interpreter. The reader settles a test file only when every record passed, so a raw `passed` becomes `unknown`. |
| pytest-dev/pytest-mock | 2 | did not settle | The plugin under test is not registered when its tree is not installed, so the `mocker` fixture is missing. A nested pytest run also did not return within 20 s. |
| pytest-dev/pytest-xdist | 1 | did not settle (`missing_module`) | `xdist/_version.py` is generated at build time and absent from the tree. The static import check accepted the package prefix. |
| marshmallow-code/marshmallow | 1 | settled `failed` | The fix removes behaviour the previous test module asserts, and one expectation depends on the platform. |

Owner ruling D6: the registered attrition reason "a fixed suite that does not settle" also covers a fixed suite that
settles as `failed`. All 9 losses are therefore registered, label-independent attrition, recorded before any label.

## Why: the static filter is weaker than a run
The fit rules that produced the counts read sources only. Four things they cannot see:
- skip markers, which they record as a flag rather than a rule;
- tests of a pytest plugin's own fixtures, which need the plugin installed;
- generated modules, accepted through a package prefix;
- previous tests that the fix itself breaks.

The fit check (A6) agreed with the M1 funnel, but K_0 had no admitted bug to test it on. On the first real runs the
static predicate admitted 9 bugs that the run predicate refused.

## If M1b is reopened
The route is a new registration with a label-independent run screen:
- it runs the two fixed suites on the fixed code only, never the previous code or mutants;
- it covers every candidate before roles are drawn;
- then a recount and a new draw.

That registration must state the prior exposure:
- every candidate was read by the static filter;
- these 9 bugs were run;
- their test modules were read during the diagnosis.

## Open findings, each for its own change
- `erasure.indexed_erasures`: an integer literal of more than about 4300 digits raises in `ast.dump`, and the run
  ends.
- Under a SyntaxWarning-as-error setting, the arm refuses operators that fit accepts, because fit suppresses that
  warning. Run 2 set no such policy.
- Fit's operator predicate ignores the size limits of the constructed record, so fit can count an operator the arm
  cannot build. Fit is frozen and produced the drawn counts.
