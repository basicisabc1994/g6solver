# Local generated families

`g6.recursion.generators.generate_family` produces the existing S1 `TaskSpec` records. It accepts a `FamilySpec` with a generated-source task, repository attribution and permissive licence, hidden `CheckSpec`, two separate reference origins, a second reference implementation, and an `IssueTemplate`. The primary implementation is the task's `hidden_ref`.

The shared path supports bug injection. Other operators are refused. It does not load sealed repositories, operators, seeds or registration data. Those belong to the protected evaluator.

Use `repo=` to stage checks through S1's `run_checks`, which requires confinement and disables networking. A `run(code, checks, timeout_s)` port can supply the same runner result dictionaries for a repository-specific adapter. That port is trusted infrastructure, not an actor tool. Tests in `tests/rsi/test_generators.py` use only synthetic local functions through this port.

Both references must pass public and hidden checks three times with stable test counts. References need different source ASTs and distinct non-actor origins without shared declared lineage. Every candidate from `mutation_check.mutants` is measured through `mutation_check.check`. All sampled mutants must fail the hidden suite; a survivor refuses the entire family. Empty samples, changed test counts, skipped checks, missing confinement, infrastructure errors and incomplete mutation runs also refuse admission. Runner exceptions propagate without retry.

Issue text uses a fixed rendering of behaviour and failure message. Template and suite provenance are checked before running. Supply the complete known actor-output corpus in `actor_outputs`: the text screen checks normalized full outputs and thirteen-word excerpts against task text, checks, references and metadata. Origins and the completeness of that corpus are caller declarations; the screen cannot determine undocumented authorship.

The returned `GeneratedFamily` records the seed, second reference, reference origins, hidden checks, repository terms, source digest and mutation counts. Its `tasks` are S1 tasks. Pass those tasks through S1's `to_suite` for public packets. The full family record contains hidden material and belongs only in evaluator storage. Result digests used by the mutation adapter identify local outcomes; they are not runner receipts or admission certificates.

`g6.rsi.family_bank.FamilyBank` owns family ids and source digests across train, dev and held_out. Add each admitted family once. `to_record()` returns detached registration metadata; `from_record()` restores ownership and refuses duplicate or malformed records. Restore the existing bank before adding another batch. Repository ids stay in one split, and a repository/bug-class pair cannot be registered twice under different family ids. The digest normalizes Python syntax and ignores test-suite edits, names, split and seed, so a source clone cannot cross splits by changing its checks. This intentionally groups identical source implementations even when their repository ids differ.

This slice supplies the generation and bank primitives. It does not fabricate a real 400-family corpus, allocate sealed panels, open slices or spend families. Registration, permissions, repository eligibility and panel use remain evaluator responsibilities.
