# Distillation cases (0.3.0 wave 1)

`operators.py` constructs Python training changes and conservative legitimate
refactorings. It labels nothing. Judging code lives under `held_out/` so the
existing protected-path gate refuses changes by proposers. The package root
imports no judging code or data.

`held_out.case_gate.CaseRecord` is an explicit-input adapter: fixed implementation
files, target module, changed lines, before/after tests, known-wrong alternatives,
bug/repository unit identity, and development/selection provenance. Its id is
`core.ledger.digest` of its body. It refuses final cohorts, amended bugs, V7 and
the existing weakening detector's fixture source. It never discovers or reads a
corpus. The design's archive remains an owner-supplied input; synthetic fixtures
in the tests are not study cases. A G6-on-G6 unit must be its repository identity.

`cut(cases, seed=...)` hashes whole unit identities into 50% tuning, 20%
acceptance, 15% monitor and 15% reserve; these are hash bands, not promises of
exact counts for a small population. Selection units always go to `unseen`.
Conflicting unit cohorts and duplicate cases are refused. `training_view`
returns tuning cases only. Only the evaluator should hold the full case list.

`held_out.oracle.label(case, policy=..., workspace=...)` runs both suites on
the fixed implementation, all known-wrong alternatives and a shared deterministic
mutant sample. It stages each run fresh, then delegates to the existing
`workforce.repo_tests._run` with `copied=True`, `network=False` and
`require_isolation=True`. It uses `repo_tests.established(...).of(test_path)`;
skipped tests, collection/setup failures, absent loading evidence, timeouts and
missing isolation cannot become a pass, kill or negative label. Mutants come
only from `workforce.mutation_check.mutants`, on the registered changed lines.
The default sample cap is 128 and the minimum is 30 distinct mutants. Small
planted tests explicitly choose a smaller policy, which is separately hashed.

A positive means either a known-wrong implementation fails before and passes
after, or the after kill set is a **strict subset** of the before kill set.
A lower kill count alone is insufficient. A negative means no loss observed on
this registered sample. Unsettled evidence or an insufficient sample is excluded.
Isolation refusal propagates as `G6_E_ISOLATION_UNAVAILABLE`; there is no retry
without confinement. No second runner is implemented.

`held_out.registration_gate.freeze` exclusively creates a registration containing
case references (no test bodies), the seeded cut, manifest digest, three operator
digests, oracle/dependency digest, complete oracle policy and digest, kill and
exclusion rules, and study metadata. `read_registration` requires a digest kept
independently, recomputes the cut and hashes, and rejects changed judging code.
The owner commits the registration before the first Concept; that chronology
check belongs to acceptance. This builder performs no git writes. Study metadata
is an extension slot for the study slice's model ids, prompt digests, commit,
seeds and endpoint bars; this slice does not fabricate them.

`held_out.sealed_gate.install(source, home=...)` keeps an explicitly supplied
opaque operator file at `home/distill/held_out/objects/<sha256>.json`, returning
only `SealedRef(sha256=...)`. `pin` hashes opaque bytes and `verify` returns no
content. All components of input/custody paths must be ordinary, unlinked paths.
No real held-out payload is included in this change. Other-family authorship
and its evidence digest must be supplied before freezing; metadata is a custody
claim that the owner must check, not authentication by a model.

The sealed wire format is a strict JSON object with `version: 1`, `author_family`
(`claude`, `codex`, or `non_model`), `training_family` (`claude` or `codex`),
`authorship_sha256`, and `operators`. Each operator has a unique `name`, Python
AST pattern strings `before` and `after`, and an optional zero-based `occurrence`
(`-1` selects the last match). Patterns each contain one statement or expression;
the interpreter substitutes matching AST nodes without executing the patterns.
The transformation patterns themselves exist only in the opaque file. No generic
callback can receive them, and no API returns them or transformed tests.

`evaluate_sealed` requires a frozen registration path/digest, matching operator
digest/policy, and a registered non-tuning case. It verifies the same bytes it
parses and returns only oracle result records. The manifest binds the base case
plus deterministic sealed operators; result case ids identify the transformed
cases. Entry is restricted by actual module identity and exact filename to
`src/g6/distill/acceptance_gate.py` and `validation/distill/held_out/study_gate.py`.
These entry modules belong to the acceptance/study slices, not this change.
The latter is the integration name chosen here because the design names the
study path but no module filename. No arm, proposer, synthesiser or runner is
an allowed caller. Custody is never an execution workspace or mission mount.

These are application-channel restrictions. Same-account hostile Python or a
shell can defeat in-process identity checks. The design requires OS custody and
unmounted mission containers; this change does not claim a same-user boundary.

At integration, replace `CaseRecord`/`Provenance` with records-slice adapters and
emit only tuning facts/failure records to synthesis. Never add a held-out value
to `FailureRecord.split`. Acceptance must enforce registration-before-concept
and the policy's five-evaluation spend limit; the study owns six blocks and its
report-only unseen looks. Neither lifecycle is implemented in this wave-1 slice.
