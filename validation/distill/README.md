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
callback can receive them. The ordinary API returns no patterns or transformed
tests. `acceptance_observations` now returns transformed inputs and labels only
to the same two protected evaluator modules, after the identical identity,
registration and digest checks. It is not exported in `__all__`.

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

Wave 2 acceptance lives in `src/g6/distill/acceptance_gate.py`. It is a
source-checkout evaluator; it needs this validation package and protected policy.
The public distill package does not import it. `decide(candidate, Library(),
registration_digest)` is a batch of one; the runner must use `decide_batch` once
for the complete predeclared tick batch. The gate writes reservations before
evaluation, records every completed decision, and refuses spent or unfinished
evaluations across process restarts. A stale writer lock requires owner recovery.

`held_out/policy.json` keeps the unchanged default `EvidencePolicy`, utility
penalty 3, paired FPR margin +.02 and BH q=.05. Its `registrations` map is empty:
no live study is enabled by this build. Owner entries are keyed by registration
digest and contain `path` (checkout-relative registration JSON), `commit` (full
registration commit), and `concepts`, mapping concept digests to `{commit, path}`
of the independently kept first concept JSON snapshot. The gate checks strict
commit ancestry and exact committed registration/concept content. These are
owner custody anchors, not caller claims about dates. The registration's study
metadata must contain `acceptance_policy_sha256` (`_policy_digest()`),
`acceptance_code_sha256` (`_judging_digest()`), and `acceptance_controls` (positive
then negative case digests), plus `family` matching both concept and contract.
All training cases named by the concept and candidate must be registered tuning
cases; an absent or non-tuning training reference blocks. Policy digest excludes the anchor map so owner
enrolment does not circularly alter the registration it pins.

Case bodies are explicit content-addressed files at
`$G6_HOME/distill/held_out/cases/<case_id>.json`. Sealed objects remain in the
existing `objects/` custody. Independent oracle exclusions cannot become
negatives. The gate runs each method three times, checks controls, compares the
union against the entire active library through the existing metaharness gate,
requires positive utility and the stricter FPR bound, replays every active
entry's frozen suite, and checks dev plus monitor FPR. It applies BH to the
complete batch as well as the unchanged alpha/K sign-test threshold. Candidate
predicates additionally require `G6_REQUIRE_ISOLATION=1`; owner permission for
unconfined exploratory scripts cannot admit a predicate. No executor or model
runs inside the metaharness adapter. Unknown probe or applies_when semantics
abstain; they are not treated as clean observations.

Sign-test units, precision counts and negative-rate pairs aggregate by bug.
Positive success requires every registered positive variant to fire; a false
alarm on any negative variant counts as one refuted unit and cannot also count
as a true-positive fire. This conservative aggregation prevents operator
replication from shrinking intervals. Wilson intervals describe the measured
precision/recall/FPR; admission uses the existing paired statistics. The reported
minimum detectable gain is the optimistic all-wins/no-losses unit fraction at
this split size and alpha/K, not a claim of 80% power.

Authority is A0 for proposals and withdrawn/superseded entries; A1 probation
after acceptance; A2 only after held-out precision lower95 >= .90, >=30 units,
>=10 legitimate negative units, held-out FPR upper95 <= .05, >=20 labelled
field fires with precision lower95 >= .85, and fresh whole-library regression.
A0/A1 concerns support at most insufficient evidence. A2 concerns may support
defect detection. Silence never passes a requirement at any authority.

`promote` reads independent field rows from unmounted evaluator custody at
`held_out/field/<candidate_id>.jsonl` (a ChainLedger, kind `field`). Bodies have
exactly `candidate_id`, `fire_id`, `source` (person/maintainer/oracle), `label`
(confirmed/refuted/unlabelled), and `receipt_sha256`. Unique fires prevent
double counting. An unlabelled fire may later receive exactly one confirmed or
refuted observation with the same fire id; the latest label replaces its
unlabelled count, never adds another fire. Terminal relabelling is refused.
The wire slice owns obtaining authentic labels and writing
these receipts; predictions, extra fields and caller counters are refused.
`monitor` demotes A2 to A1 at `demote_alpha`, using the existing exact binomial
upper bound. Its reported p-value upper bound is the threshold, not an invented
exact p-value. Owner `demote`/`withdraw` require the wire slice's consent dialog;
the Python API records a name as given and does not authenticate it.

`recheck` withdraws on reproduced own-suite drift, >10% weekly cost overruns,
or an independently observed pre-stated withdrawal condition. Health rows at
`held_out/health/<candidate_id>.jsonl` (kind `health`) contain `candidate_id`,
`observation_id`, `source`, timezone-aware `observed_at`, `wall_s`,
`withdrawal_condition`, and `receipt_sha256`; optional observations are null.
`ablate` archives a redundant method only when removal loses no verdict on the
family's complete frozen suite. Downward changes cascade to every dependent
composition/reuse method at A0; re-admission needs a fresh unspent acceptance
decision. Bodies and lineage are retained, never deleted. Decisions/reports
are in `distill/acceptance_gate.jsonl` and `acceptance_gate_reports/`; library
snapshots remain in the records slice's library ledger.
