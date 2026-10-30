# Plan audit: the release checklist

This is the release checklist. It maps each item of the earlier plans, each Phase 0 usability finding, each step and
amendment of the current plan (revision 3) and each phase of the verification search to its status. The status was
checked against the code, the tests and the continuous-integration runs, not taken from other documents. It is updated
at each step, and a row changes only with new evidence.

**Status:** Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

- **Audited commit:** `a2df4fd` (branch `claude/step2`), on 28 October 2026. Later commits are named only where a row
  says so. The Step 2 rows (S2-12 onward, and the fix-pass table) were checked again on 29 October 2026 against the
  frozen Step 2 commit `b3c342b`, and VS-0 against `cd52d48` on branch `claude/vs`. Rows S2-16, S2-18 and S2-19
  were checked on 29 October 2026 against `8b5364c` on branch `claude/pool-compat`, and rows VS-1 to VS-3 against
  `051b062` and `633f61b` on branch `claude/vs`.
- **Minimal launch update (29 October 2026, night):** section 5 and the rows of section 4 were checked against the
  tip of the launch line, `0ce616e` (branch `claude/launch`), merged into this branch by `d42efb4`. They rest on the
  launch commits' messages, which record each fix with the test that failed first and now passes, and on finding each
  named test in the merged tree. The suites were not rerun for this update, and no continuous-integration run exists
  for the launch line's tip (most launch commits carry `[skip ci]`).
- **Sources:** the plans are kept outside this repository, and their items are described here in plain words. The
  Phase 0 usability ledger is also kept outside this repository; its identifiers (P0-01 to P0-32) are used below. It
  was recorded on 27 October 2026 by following each public journey literally from a fresh clone of public `main` at
  `419e43f`, in Windows PowerShell 5.1.

## How to read a status

| Status | Meaning |
|---|---|
| implemented and verified | The code exists at the audited commit, and a named test or continuous-integration run covers it. For a registered protocol: the text is registered and its hash was recomputed for this audit. |
| implemented, not verified | Code or text exists, but no test or run covers the part that matters, or it lives outside this repository. |
| missing | Not found in the code at the audited commit. |
| deferred | A ruling or the plan puts it later. The last column says where. |

Continuous-integration runs cited below:

| Run | Commit | Result |
|---|---|---|
| 40613841959 | `5733be2` (the starting commit) | passed |
| 40633095401 | `6654b19` (Step 1's exit commit) | all nine jobs passed |
| 40644090822 | `94d99ee` (the tip of `codex/ai-for-ai-continuation`) | all nine jobs passed |
| 40654008114 | `a2df4fd` (the audited commit) | conformance, scan and site passed. All six test jobs failed on one test (row S2-12). Every other test in those jobs passed, so a test cited with this run passed there. |
| 40602312893 | `30ab0cf` | the last bundle build; passed |
| 40768957226 | `051b062` (VS-2's tip on `claude/vs`) | all nine jobs passed |

On the Linux jobs bubblewrap is installed and expected, and a confined test fails rather than skips
(`tools/ci_bubblewrap.sh:17`, `tests/conftest.py:55-87`). The Windows and macOS jobs set the person's opt-in
(`.github/workflows/ci.yml:36-42`). Those jobs check the refusal logic and the opt-in, not a boundary.

## Release gates

The gates of `docs/releases/v0.1.1.md`, then the gates the current plan adds. Each gate is open until its evidence
exists.

| Gate | Required evidence | State at `a2df4fd` |
|---|---|---|
| Software | Split regression suites, built-wheel install smoke, supported dependency versions | **Open.** Met at `94d99ee` (run 40644090822; the wheel smoke for `6654b19` is recorded in `docs/validation/results/release_0_1_1/wheel.json`). Not met at `a2df4fd`: run 40654008114 failed. Met again at the frozen Step 2 commit `b3c342b` (run 40699680573, all nine jobs; row S2-12); its wheel smoke is not recorded yet. |
| Isolation | Actual Windows and Linux adversarial probes; skips do not count as passes | **Open.** Linux: the bubblewrap boundary tests run in continuous integration (rows E13, E14). Windows: the AppContainer boundary tests are on record only as local runs (row E15). macOS has no boundary (row E23). |
| Client consent | Real supported Claude Code and Codex behaviour, including decline and missing elicitation | **Open.** In Codex the owner saw the form: Esc cancelled it, an acceptance was recorded, and no Decline control was visible (`docs/releases/UAT_ROUND_1.md:61-65`). Claude Code is untested (row S5-1). |
| Usability | Fresh-user install, discover, invoke, compose and read journey without manual debugging | **Open.** No fresh-user journey has run. 20 of the 29 rows of table 2 are still missing. |
| Utility | Correctness, false assurances, failure recovery, cost and time against a stated baseline | **Open.** V9 is registered and has not run (row S4-3). The six-case engineering trial is synthetic, and its committed result is from the earlier version of the review (row S2-11). |
| Broader usability | Later ten-journey trial: 8 of 10 usable, zero unexplained errors | **Open.** Not run (row S5-5). |
| Artifacts | Docs and bundles match the tested commit; stale certificates regenerated through their normal checks | **Open.** The certificates are current (conformance passed in run 40654008114). The recorded wheel was built at `6654b19`, before Step 2. The bundles were last built at `30ab0cf`. The site and docs still differ from the code (tables 1 and 2). |
| V10 feasibility report (current plan) | The verification-search study's feasibility report | **Open.** The V10 protocol is registered (row VS-0). VS-1 and VS-2 pass continuous integration on their branch. VS-3 is built and under review, and VS-4 onward is being built. |
| Step 5 trial (current plan) | The owner pilot and the fresh-user trial | **Open.** Rows S5-1 to S5-7. |
| Security reviews (current plan) | A fresh reviewer for each security-sensitive change, with its findings fixed | **Open.** The second review of Step 2 found three critical and six important problems; the fix pass fixed each with a test that failed first (rows S2-C1 to S2-15). The review of the fix pass itself was thin, and a deep adversarial review is owed before go-live (rows S2-12, S2-17). |

### Release gates at the minimal launch

On 29 October 2026 (night) the owner decided on a minimal launch for Claude Code and Codex. For this first launch it
supersedes holding go-live for V10, the 8-of-10 fresh-user trial before posts, and venture in Step 5. The table below
gives each gate above its state at the launch tip `0ce616e`, then the gates the launch adds. The rows behind it are in
section 5.

| Gate | State at `0ce616e` |
|---|---|
| Software | **Met at `0c44e6d`.** Run 40944507516 passed all nine jobs (row L-G3). The earlier failures are fixed (rows L-CI1, L-CI2, L-G3). |
| Isolation | **Open.** Linux: bubblewrap, as before. Windows: the AppContainer grant is the person's one-time step. macOS: patch review refuses to run (row L-I1). The boundary half of the launch review had no new escape probes (row L-A). |
| Client consent | **Met.** The owner's pilot in Claude Code and in Codex (row L-G4, `docs/releases/OWNER_PILOT.md`); its six fixes are in section 5.3d. Codex's form has no Decline, and the owner accepts a cancel as its no. |
| Usability | **Superseded for the first launch** by the owner's decision. Not run. |
| Utility | **Superseded for the first launch.** No claim is made that G6 finds more defects or saves time (row L-G5). V9 and V10 are registered and have not run. |
| Broader usability | **Superseded for the first launch.** Not run. |
| Artifacts | **Open.** The version is 0.2.0 (row L-G6), and the five certificates were renewed after it through normal confined issuance (`55ad65d`); the catalogue was already current. The site build from the release commit is still to make. |
| V10 feasibility report | **Deferred** (row L-D1). |
| Step 5 trial | **Superseded for the first launch;** the owner's pilot remains (row L-G4). |
| Security reviews | **Open.** Half A and half B of the launch review found problems that are fixed (rows L-A to L-B8). The review of W1 to W5 found F1 to F8, fixed (rows L-W6, L-F1 to L-F8); the review of that fix pass found two High problems, fixed (rows L-R1 to L-R5). The fresh review of the second fix pass found two Important problems, fixed with their tests (rows L-R6 to L-R8); that last fix pass has no fresh review of its own. The integrity fix of the VS-4 review has no separate fresh review on record (row L-G1). |
| CI on the release commit (launch) | **Met at `0c44e6d`**, macOS included (row L-G3). A commit made after it needs its own run before the tag. |
| Honest copy (launch) | **Open.** The commit messages record the site and honesty tests green; see the caveats in row L-G5. |

### Launch blockers that look most important

1. **The study's repositories cannot all be checked yet.** Step 2 is frozen at `b3c342b` (row S2-12). At that
   commit:
   - G6 refuses openlibrary, about a third of the study pool, because of its submodule folders (row S2-16);
   - on ansible's images it tests the installed copy of the package instead of the patch (row S2-18).

   A branch fixes both and passes planted-change controls on development images of both repositories. It is not yet
   merged and has no continuous-integration run. qutebrowser, the third repository, still cannot be checked (row
   S2-19).
2. **The site still makes claims the plans retired.** The homepage shows per-task-set winners chosen after the fact,
   counts a person's hand prompt as G6's own option, and gives V6b a badge that is not its verdict. There is no
   honesty test, and several site tests require the old claims (rows E25 to E34).
3. **The status line is missing.** The required status line appears nowhere a reader of G6 would see it: not in the
   README, not in AGENT_GUIDE, not on the site (row E87).
4. **The first install steps still fail on Windows PowerShell 5.1, and the two front doors install different
   things.** The clone line uses `&&`. Get started installs `.[mcp]` where the README installs `.[all]`. Eight places
   in the docs and the code still register a bare `g6-mcp` at the client's default scope, and so does every client
   snippet on Get started (P0-01, P0-12, P0-13, P0-14).
5. **Client consent and every human trial are still to run** (rows S5-1 to S5-5), and **the verification search is
   at its first phase** (table 4), while go-live waits for V10.

These five blockers were written on 28 and 29 October. For the minimal launch, blockers 1 and 5 no longer hold the
release (the study pool and V10 continue after launch), and blocker 2, the site claims, and blocker 4, the install
steps, are the subject of the launch's copy rows (section 5.6). Blocker 3 is met on the pages the launch ships (row
L-G5). Client consent remains a launch gate (row L-G4).

## Counts

| Table | implemented and verified | implemented, not verified | missing | deferred | Rows |
|---|---|---|---|---|---|
| 1. Earlier plan items | 37 | 14 | 27 | 12 | 90 |
| 2. Phase 0 findings | 6 | 2 | 20 | 1 | 29 |
| 3. Revision 3 steps and amendments | 35 | 7 | 16 | 8 | 66 |
| 4. Verification search (at `0ce616e`; it was 3, 2, 5, 0 at `051b062`) | 3 | 4 | 0 | 3 | 10 |
| 5. The minimal launch | 58 | 12 | 1 | 17 | 88 |

## 1. Earlier plan items

The plan of 27 October 2026 and the two drafts of 28 October that revision 3 replaced. Items that revision 3 carries
forward in the same form are listed once, in table 3.

### Phase 0: use it like a user

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E01 | Follow the README, Get started and an app literally from a fresh clone, and record every stop | implemented, not verified | The usability ledger (outside this repository) covers journeys 1, 2 and 4. Journey 3 stops at the person's double-click (P0-21). No agent session has tried the ten problems | Step 5 |

### A0: records and the README

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E02 | Record the rulings in a decisions file | implemented, not verified | Kept outside this repository; nothing here depends on it | — |
| E03 | The README states the label rule and the threat statement where they are read | implemented and verified | `README.md:24-29`. Tests: `tests/core/test_door_promotion.py::test_the_threat_statement_is_where_the_rule_is_read` and `::test_the_docs_say_no_door_gives_verified_today_and_what_the_raw_call_is`. Commits `b1131f2`, `3ad72c0`; run 40644090822 | — |
| E04 | The README says what a check's hash covers, and that a check runs in the work's own process | implemented, not verified | `README.md:30-32` (`3ad72c0`). No test pins the sentence | Step 5 |
| E05 | Every install message in the library points to the repository, not the package index, with a test that scans the library | implemented, not verified | `tests/tools/test_install_messages.py::test_every_install_command_names_the_repository`. Gaps: `src/g6/cli/doctor.py:205` (`pip install --force-reinstall g6`) is not caught by the test's pattern, and five lines in certificate-bound files are waived until the next issuance window (`core/llm.py:516,541`, `csf/__init__.py:22`, `ml/_types.py:112,220`) | next certificate issuance window |

### A1: trust doors

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E06 | One door, `door_reply`, behind `g6_invoke`, `g6 invoke` and `g6.invoke`. A component's own `verified` is held and the copy of its result is corrected | implemented and verified | `src/g6/core/door.py:178-208, 242-268`. Tests: `tests/core/test_door_promotion.py::test_verified_with_string_evidence_over_mcp_is_held_and_its_result_corrected` and `::test_cli_and_mcp_give_the_same_outcome`. Commit `1d06c06`; run 40644090822 | — |
| E07 | The promotion law is always on, and its switches are removed | implemented and verified | `src/g6/core/reliability_envelope.py:21-23`. Tests: `test_door_promotion.py::test_no_retired_switch_is_read_anywhere_in_the_library` and `::test_off_words_do_not_disable_the_law`. Commit `5f6bd7d` | — |
| E08 | The task-contract floor and its switch are deleted, and the old "verified without evidence" code is retired | implemented and verified | Tests: `test_door_promotion.py::test_an_envelope_has_no_task_contract_floor` and `tests/core/test_mcp_invoke_envelope.py::test_verified_without_evidence_is_held_at_draft`. Commit `1cc2cd7` | — |
| E09 | Only G6's own integrity re-check can keep `verified` | implemented and verified | `reliability_envelope.py:73-76`; `src/g6/integrity/tree.py:110,137`. Tests: `test_door_promotion.py::test_an_oracle_anyone_else_registers_does_not_promote` and `::test_a_tree_changed_after_the_check_blocks_and_rebuilding_reruns_it`. Commit `5f6bd7d` | — |
| E10 | Pipelines, bundles and the harness apply the same rule | implemented and verified | `src/g6/core/ai_block.py:205-227`. Tests: `tests/core/test_ai_block_composition_state.py::test_two_self_verified_stages_are_held`, `tests/bundles/test_bundle_runner.py::test_a_bundle_that_says_verified_is_held` and `tests/harness/test_harness.py::test_an_issuers_verified_is_held_at_the_harness_door`. Commit `b3da261` | — |
| E11 | An extension cannot take the name of a G6 package | implemented and verified | `src/g6/core/registry.py:71-77`. Test: `tests/core/test_families.py::test_an_extension_cannot_take_the_name_of_a_g6_package`. Commit `be34681` | — |

### A2: isolation

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E12 | One backend interface: AppContainer on Windows, bubblewrap chosen on Linux | implemented and verified | `src/g6/core/confine.py:426-442, 695, 1298`. Tests: `tests/core/test_confine_backend.py::test_a_backend_needs_only_ready_prepare_and_start` and `tests/core/test_confine_bwrap.py::test_bubblewrap_is_linuxs_backend_and_says_how_to_install_it_where_it_is_missing`. Commits `0cefb2d`, `1c3b48c` | — |
| E13 | Linux jobs install bubblewrap, and a confined test fails rather than skips there | implemented and verified | `.github/workflows/ci.yml:34-35`; `tools/ci_bubblewrap.sh:17`. Test: `test_confine_bwrap.py::test_the_bwrap_fixture_fails_rather_than_skips_where_ci_expects_bubblewrap`. Commit `db25469` | — |
| E14 | On Linux a confined run cannot see a planted key or anything outside its folder, has no network, can write only its folder, and a grandchild is killed on time-out | implemented and verified | `tests/core/test_confine_bwrap_linux.py`, five tests, among them `::test_a_planted_key_file_and_a_file_outside_the_run_folder_stay_hidden` and `::test_a_grandchild_that_leaves_the_session_is_killed_when_the_run_times_out`. Linux jobs of run 40644090822 | — |
| E15 | The Windows boundary is checked by continuous integration | implemented, not verified | The Windows jobs take the person's opt-in (`ci.yml:36-42`). The AppContainer boundary tests are on record only as local runs | release gate "Isolation" |
| E16 | On Linux, `g6 doctor` starts bubblewrap's test run and gives the fix for each reason | implemented and verified | `src/g6/cli/doctor.py:162-190`. Test: `tests/core/test_doctor_isolation.py::test_on_linux_each_reason_comes_with_its_own_fix`. Commit `cb0305e` | — |
| E17 | CEGIS candidates and outside programs run through `confine`, each with its own folders | implemented and verified | `src/g6/cegis/runner.py:97-113`. Test: `tests/cegis/test_cegis_isolation.py::test_a_cegis_candidate_runs_inside_bubblewrap_on_linux`. Commit `72ffc56` | — |
| E18 | SWI-Prolog, Lean, TLC and SageMath run through `confine` | implemented and verified | `src/g6/prolog/runner.py:176-178`, `lean/runner.py:127-130`, `model_checking/tlc.py:292-295`, `algebra/runner.py:163-166`. Tests: `tests/core/test_outside_programs_confined.py::test_with_no_backend_each_is_refused_before_it_starts_unless_allowed` and `::test_swi_prolog_runs_inside_bubblewrap_on_linux`. Commit `f07e4ab`. Real Lean and SageMath installations are untested | — |
| E19 | The dropped isolation records are kept, and a refused run is never scored as an answer | implemented and verified | Tests: `tests/code/test_isolation_records.py::test_adapt_healings_case_runs_keep_their_isolation` and `tests/metaharness/test_isolation_refused.py::test_a_code_candidate_refused_for_isolation_is_never_scored`. Commit `f25cd57` | — |
| E20 | Refusal by default. `g6 setup` records the person's choice once, and requiring isolation outranks any allowance | implemented and verified | `confine.py:199-225`; `src/g6/core/choices.py:24-39`. Tests: `tests/core/test_isolation_choice.py::test_requiring_isolation_outranks_any_allowance` and `::test_setup_records_the_answer_given_on_its_command_line`. Commit `df2aae5`. Limit: the choice file is a preference that a same-user shell can change (`choices.py:5-8`) | — |
| E21 | Confined children get an allowlisted environment and no extra descriptors | implemented and verified | `confine.py:487-534`. Tests: `tests/core/test_confine_child_secrets.py::test_confined_spawn_does_not_inherit_credentials` and `::test_confined_spawn_refuses_extra_inherited_descriptors`. Commit `24bf4b5` | — |
| E22 | The public pages describe refusal by default and the opt-in | implemented, not verified | `web/templates/pages/security.html`, `get_started.html`, `web/content/faq.md`, `developers.md` and `site/docs/install.md` mention `g6 setup` or the opt-in. No site test for that wording was found | Step 5 |
| E23 | A boundary on macOS | deferred | `confine.py` selects no backend on macOS, so people there opt in | out of scope since 27 October 2026 |

### A3: smaller trust fixes

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E24 | Approver names are recorded as given, not authenticated, and approval comes through the client in the proposing session | implemented and verified | `src/g6/metaharness/surface.py:311-314`. Test: `tests/metaharness/test_phase6_mcp.py::test_proposing_session_can_present_digest_bound_client_consent`. Commit `568cece` | — |

### A4: site and README honesty

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E25 | The homepage drops the per-task-set winner chosen after the fact | missing | `web/templates/pages/landing.html:189`. Pinned by `tests/site/test_evidence.py::test_each_task_sets_winning_option_is_g6s_best_and_says_what_it_changed` | Step 5 |
| E26 | Registered verdicts appear on the homepage and the research page | missing | `web/pages/views.py:29-37` passes none | Step 5 |
| E27 | A person's hand prompt is not counted as one of G6's options | missing | `web/pages/evidence.py:28, 322` | Step 5 |
| E28 | V6b's badge shows its registered verdict | missing | `research.html:174` shows "Gain", computed in `evidence.py:429-434`. The registered verdict is `contaminated_evaluation` (`docs/validation/results/V6b/analysis.json`) | Step 5 |
| E29 | The research page labels after-the-fact picks as such | implemented and verified | `evidence.py:363, 366`. Test: `tests/site/test_evidence.py::test_fixed_arm_and_posthoc_selection_are_separate`. Commit `e4200d9` | — |
| E30 | The retired claims are rewritten in place | missing | `web/templates/base.html:7,12,19,57`; `landing.html:71, 94-95, 101-102, 147-148, 161, 165`; `about.html:63-64`; `research.html:134-135`; `web/pages/narrative.py:23`; `web/content/faq.md:50-51, 78`. Five site tests require these claims, among them `tests/site/test_about_page.py::test_about_says_g6_engineers_itself_with_a_person_approving` | Step 5 |
| E31 | The ways-of-access copy says what tuning needs and drops "all work today" | missing | `components.html:35`; `landing.html:241`. Pinned by `tests/site/test_home_page.py::test_all_three_ways_to_use_g6_are_available_now` | Step 5 |
| E32 | The site says that the approver's name is not checked | implemented, not verified | `web/content/faq.md:359-361`. No site test was found | Step 5 |
| E33 | The forbidden status word is gone from public copy | implemented, not verified | None in the pages, the content, the site docs or the README. It is still in four blog essays (for example `web/templates/blog/epistemology_of_ai.html:113`), in `docs/validation/REGISTRY.md:339` and in the V6 and V6b result READMEs. Only the components page is tested (`tests/site/test_components_page.py`) | Step 5 |
| E34 | One site test file for the honesty rules | missing | `tests/site/test_honesty.py` does not exist | Step 5 |

### A5: release

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E35 | Land the stack fast-forward after one certificate pass | implemented and verified | The five certificates were renewed at `6654b19`, issued confined. Conformance passed in runs 40644090822 and 40654008114 | — |
| E36 | A fresh review turns each claim of the outside evaluations into a test that no longer reproduces it | implemented, not verified | `docs/CONTINUATION_REVIEW.md` records one review and three fixes. The door claims have tests (rows E06 to E10). The homepage claim still stands (rows E25 to E28) | Step 5 |
| E37 | Package index, tag and site deploy | deferred | The owner's steps, after the gates | owner |

### Phase F: it works for the person's own problem

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E38 | Start a data-only workflow over MCP from cases and a check | implemented and verified | `src/g6/mcp/__init__.py:193`; `src/g6/metaharness/declarative.py:79-103`. Tests: `tests/metaharness/test_declarative.py::test_start_persists_data_only_workflow_and_reloads_without_registration` and `tests/metaharness/test_phase6_mcp.py::test_data_only_application_over_real_mcp_client` | — |
| E39 | More check kinds (pattern, test command, agent-written) and more executors (G6's model, the person's agent, a bundle) | deferred | Only `exact`, `contains` and `number` exist (`declarative.py:41`), and only the workflow's own `solve` runs | Step 6 |
| E40 | Confirmation through the client's prompt, bound to the digest, with a terminal fallback | implemented and verified | `mcp/__init__.py:203-221`. Tests: `test_declarative.py::test_confirmation_is_bound_to_workflow_digest` and `tests/metaharness/test_workflow_cli.py::test_terminal_confirmation_checks_digest_and_records_consent`. Limits: the fallback is two commands, a declined approval prompt returns no terminal command (`mcp/__init__.py:283-285`), and no test checks the fallback text in the reply | — |
| E41 | Workflows are found in the project and in the home folder without registering again | implemented and verified | `surface.py:117-130`. Test: `test_declarative.py::test_global_discovery_and_project_precedence` | — |
| E41a | Workflows declared in Python (`G6_METAHARNESS`) still load, as an override | implemented, not verified | `surface.py:119-130` merges them last. No test of the override was found | Step 5 |
| E42 | Predictable project selection: walk up to `.g6/` or the git root, and `inspect` lists the folders it searched | missing | The project is the server's working folder only (`surface.py:117`), and the not-found reply lists names only (`surface.py:139-142`) | Step 5 |
| E43 | Approval in the same session, through the client | implemented and verified | `surface.py:155-181`. Test: `test_phase6_mcp.py::test_over_mcp_declined_or_missing_client_consent_never_approves` | — |
| E44 | Run the approved version over MCP; long operations become jobs with a status tool | implemented and verified | `mcp/__init__.py:235-243`; `src/g6/metaharness/jobs.py`. Test: `tests/metaharness/test_jobs.py::test_job_returns_result_without_restarting_the_operation`. Jobs live only in the server's process | — |
| E45 | A cancel tool, and a check of Codex's tool time limit | deferred | Neither was found. Step 3 defers cancellation, and it needs persistence only if an evaluation outlasts a client's time limit, which has not been measured | Step 3 |
| E46 | Export an approved version as a skill or recipe for the person's agent | implemented, not verified | `src/g6/metaharness/api.py:992` exports from Python only. No MCP tool exports | Step 6 |
| E47 | Below 30 held-out cases the loop runs in explore mode, and the gate cannot approve | implemented and verified | `surface.py:192`; `src/g6/metaharness/gate.py:199-202`. Test: `test_declarative.py::test_generated_application_is_measured_but_small_split_cannot_activate`. The reply states the total needed, not how many more | — |
| E48 | A case-builder that draws cases from logs or from repository history | missing | Not found | not scheduled in revision 3 |
| E49 | Help opens with the ways in and the four steps; improvement intents route to the meta-harness; search by failure mode | implemented and verified | `src/g6/core/discovery.py:121-126, 153-158`. Tests: `tests/core/test_discovery_cli_mcp.py::test_improvement_intents_point_to_the_workflow_loop` and `::test_find_reaches_a_component_by_the_failure_mode_it_declares`. The recorded queries are in P0-24 and P0-25 | — |
| E50 | `g6 setup` registers Codex with the full interpreter path and installs the skill | implemented and verified | `src/g6/cli/setup.py:149-165`. Test: `tests/core/test_setup_clients.py::test_setup_registers_explicit_client_with_absolute_interpreter_and_skills` | — |
| E51 | `g6 setup` registers Claude Code at user scope | implemented, not verified | `setup.py:149-150`. No test asserts `--scope user` | Step 5 |
| E52 | `g6 setup` runs doctor, writes the key file from the person's own answers, and defaults to the host model | missing | `setup.py:89-128` does none of these | Step 5 |
| E53 | A short `using-g6` skill that teaches find, compose, fit, the labels and asking the person | implemented and verified | `src/g6/skills/using-g6/SKILL.md`, about 500 words. Tests: `tests/skills/test_using_g6.py::test_it_stays_short` and `::test_every_tool_and_code_it_names_is_real` | — |
| E54 | The meta-harness skill installs by default | missing | `src/g6/cli/__init__.py:90-91` installs `using-g6` only, and `tests/core/test_skills_cli.py::test_install_with_no_names_copies_only_the_using_g6_skill` pins that | Step 5 |
| E55 | The ten-problem usability trial | missing | Not run. Revision 2 turned it into ten coding-agent verification journeys (row S5-5) | Step 5 |
| E56 | Python 3.14 | deferred | `pyproject.toml:12` refuses it, and the test matrix is 3.12 and 3.13 (`ci.yml:14`) | Step 6 |
| E57 | One install line, user scope wherever a client is registered, and commands that work in Windows PowerShell 5.1 | missing | P0-01, P0-12 and P0-13 | Step 5 |
| E58 | MCP blocks for the core components | deferred | No block for workforce, guards, context, reasoning or evidence. Ruling: MCP blocks only where patch review or activation needs them; workforce's runs are reached through `g6_review_patch` | Step 6 |
| E59 | Python-only components are labelled as such in find, describe and the card, with the import line | missing | The label is only in each component's skill text (for example `src/g6/workforce/skill/SKILL.md:70`) | Step 5 |
| E60 | The person's model is chosen through `g6 setup` | implemented and verified | `setup.py:105-128` (`--model` writes only the provider line). Test: `tests/core/test_setup_clients.py::test_explicit_host_model_choice_updates_only_provider_line`. Setup does not ask the question itself. The unconfined-code choice is row E20 | — |
| E61 | Groups of sensitive operations are chosen once and recorded, and a refusal names the setup question that unlocks it | missing | The choice exists only in the client registration's environment (`setup.py:152-153`). The refusal names `G6_MCP_SENSITIVE` (`mcp/__init__.py:84-85`) | Step 5 |
| E62 | A refusal reads `ok: false` | implemented and verified | Row S1-2 | — |
| E63 | `G6_E_NO_BLOCK` says "Python library only" and gives the import line | missing | `registry.py:173` | Step 5 |
| E64 | The install guide keeps the address's `#token=` | missing | `site/docs/install.md:29`; `web/content/install.md:20`. The app's own page does print it (`src/g6/bundles/server.py:309`) | Step 5 |
| E65 | The model apps offer the Claude Code and Codex sign-in, with Anthropic's kit bundled | deferred | `bundles/server.py:34`; `packaging/build_bundles.py:39` | not scheduled in revision 3 |
| E66 | agent-change-check says plainly when no Python with pytest is found, and its smoke test fails in that case | missing | `packaging/smoke_bundle.py:97-101` | not scheduled in revision 3 |
| E67 | The current macOS "Open Anyway" step | missing | `site/docs/install.md:35-37` still gives the right-click step | Step 5 |
| E68 | The Windows SmartScreen steps, and the apps labelled unsigned | implemented, not verified | `site/docs/install.md:31-33`. No test found | Step 5 |
| E69 | Docs that run: every snippet in the README, Get started, the developers and meta-harness pages, and AGENT_GUIDE | implemented, not verified | `tests/tools/test_doc_examples.py::test_example_runs` runs only `site/docs/developers.md` | Step 5 |
| E70 | The skill-example test skips only when the missing module fails to import | missing | `tests/core/test_component_skills.py:152-157` skips on any exception whenever an extra is missing | Step 5 |
| E71 | The harness and operator skills appear in `g6 skills list` | missing | `registry.py:31` excludes them | Step 5 |
| E72 | The docs hub placeholder is replaced or removed | missing | `web/templates/docs_hub/landing.html:21` | Step 5 |

### Phase B: the repository check, composition and the headline study

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E73 | The repository check on the three Python repositories' images, including old git and submodules | missing | Git older than 2.31 and submodules are refused, not supported (`src/g6/workforce/repo_tests.py:23-24, 240`). No probe on those images is on record | Step 4, before the feasibility freeze |
| E74 | Block contracts are described as what they are | implemented and verified | Row S1-5 | — |
| E75 | Strict composition with an assurance report | implemented and verified | `src/g6/core/compose.py:34-174`. Tests: `tests/core/test_compose.py` (ten tests). Commit `568cece` | — |
| E76 | Real contracts for grounding, the coding gate and action gating | implemented and verified | `grounding_block.py:57-63`; `coding_agent_gate_block.py:95-99`; `action_gating_block.py:66-69`. Tests: `tests/core/test_compose_mcp.py::test_real_grounding_composition_returns_contract_report` and `::test_real_action_contract_matches_the_output`. No test composes the coding gate | — |
| E77 | `g6_compose` on MCP | implemented and verified | `mcp/__init__.py:126-136`. Test: `test_compose_mcp.py::test_missing_input_names_the_stage_and_field` | — |
| E78 | The homepage hero and its three ways in | implemented and verified | `landing.html:3, 27, 231-235`. Test: `tests/site/test_narrative.py::test_the_home_page_starts_from_the_problem_then_says_what_g6_is` | — |
| E79 | A status line on the homepage, and a compose page | missing | Neither a status line nor a compose route exists (`web/pages/routes.py`) | Step 5 |
| E80 | Evidence passed between stages carries a grade, per-stage digests and a freshness stamp, and an adapter that strengthens evidence is refused | missing | `compose.py` matches evidence kinds (`:84-98`) and marks producer evidence as declared (`:143-147`). It has none of the four | Step 3 |
| E81 | Study V8 | deferred | `docs/validation/REGISTRY.md:461` has a draft entry only | Step 6 |
| E82 | A headline from a registered verdict | deferred | No registered verdict yet | after V10 |

### Items from the two drafts of 28 October that revision 3 states differently

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| E83 | Patch review reports claim support from grounding without letting it decide the outcome | implemented and verified | Test: `tests/harness/test_patch_review.py::test_grounding_is_reported_and_does_not_decide_the_outcome`; run 40654008114 | — |
| E84 | A regression nobody selected is caught by the touched modules' tests | implemented and verified | Test: `test_patch_review.py::test_confined_a_derived_regression_is_a_defect_and_a_pre_existing_failure_is_not`; Linux jobs of run 40654008114 | — |
| E85 | Jobs persist in G6's home folder | deferred | Jobs live in the server's process (`src/g6/metaharness/jobs.py`) | Step 3, and only if an evaluation outlasts a client's time limit |
| E86 | Composition and gate demonstrations in both clients | deferred | Carried into Step 3's two demonstrations (row S3-5) | Step 3 |
| E87 | The status line wherever G6's status is stated | missing | The required line appears only in `docs/validation/patch_review_utility/PROTOCOL.md`. The README, AGENT_GUIDE and the site state no status | Step 5 |
| E88 | The theory documents render on GitHub and read for people who are not specialists | missing | `\operatorname` appears 10 times in `docs/theory/G6_Computational_Structural_Intermediate_Representation.md` and once in `G6_Kernel_Spec_and_Theorems.md`. There is no `tests/tools/test_theory_math.py`, and no "in plain words" or "in the code" parts | a separate pull request, not blocking |
| E89 | The extension programme | deferred | Paused by the owner | Step 6 |

### Withdrawn by later rulings (not counted)

- **Deleting and re-tagging v0.1.0:** v0.1.0 stays immutable (`docs/releases/v0.1.1.md:3`).
- **The separate-session approval rule, and a proposer-session check on rollback:** replaced by approval through the
  client's prompt, in the same session too (rows E24, E43).
- **Extending the check hash to captured values, defaults and module bytes:** replaced by the narrowed README claim
  (row E04).
- **A new install extra for the solvers:** replaced by `.[all]` as the one install line (row E57).
- **The reference patch-claim composition:** replaced by `src/g6/harness/patch_review.py` (Step 2).
- **The first draft's thirty-case utility trial and its "accept-recommended" outcome:** replaced by V9 and the four
  outcomes (table 3).
- **The ten varied problems as the launch usability trial:** replaced by ten coding-agent verification journeys
  (row S5-5).
- **V7's design D:** retired unrun, replaced by V8 (draft), then by V9 for the first proof.

## 2. Phase 0 findings

Three ledger rows recorded steps that worked and need nothing: P0-03 (the install into a fresh environment), P0-09
(the claim check with escaped quotes) and P0-20 (the downloaded app's hash matched).

| # | Finding, in plain words | Status | Evidence at `a2df4fd` | Owned by |
|---|---|---|---|---|
| P0-01 | The README's clone line joins two commands with `&&`, which Windows PowerShell 5.1 rejects | missing | `README.md:41`; also `web/templates/pages/get_started.html:24`, `landing.html:230`, `site/docs/index.md:115-116`, `site/docs/guide/index.md:347` | Step 5 |
| P0-02 | The README never says to make a virtual environment | missing | `README.md:40-45` | Step 5 |
| P0-04 | Doctor calls "no model" fine without saying what will not work or how to choose one | missing | `src/g6/cli/doctor.py:139`. The fix text at `:140` is printed only when a check is not ok (`:241`) | Step 5 |
| P0-05 | Doctor's Windows isolation line is long and technical, and its closing line points to "optional extras" | missing | `doctor.py:182`, `:244`; `src/g6/core/confine.py:547-548, 858-859` | Step 5 |
| P0-06 | The version reads as a development version though v0.1.0 exists | implemented and verified | `src/g6/__init__.py:12` is `0.2.0` (the owner, 2026-10-29). Tests: `tests/tools/test_release_guard.py::test_wrong_or_development_version_cannot_be_released` and `::test_the_launch_notes_name_the_stable_version_g6_reports`. The published v0.1.0 apps keep their old label | — |
| P0-07 | `g6 init` leaves an empty folder: no key file, no model, no next step | missing | `g6 init` still prints only the folder (`src/g6/cli/__init__.py:227`). `g6 setup` exists, but it ends with "Restart the client to load it." (`src/g6/cli/setup.py:166`) | Step 5 |
| P0-08 | The README's `g6 invoke` example breaks in Windows PowerShell 5.1, which strips the inner quotes | missing | `README.md:79`. Parameters are taken only as one positional JSON string (`cli/__init__.py:152`) | Step 5 |
| P0-10 | `g6 find` prints raw scores, and an unrelated component ranks second for a claim check | missing | `cli/__init__.py:236` prints raw output. The ranking was not rerun | Step 5 |
| P0-11 | The bundle list says "built, awaiting its certificate" without saying whether it can be used | missing | `src/g6/bundles/__init__.py:85`. Pinned by `tests/bundles/test_compliance_gap.py::test_it_is_held_as_built_awaiting_its_certificate_until_its_sources_are_registered` | Step 5 |
| P0-12 | Client registration uses a bare `g6-mcp` at the client's default scope, so the server does not start outside an activated environment | implemented, not verified | The README uses `g6 setup --client claude` (`README.md:55`), which registers the full interpreter at user scope (`setup.py:150, 156`); only the Codex path is tested (row E50). The bare form remains in `site/docs/index.md:117`, `site/docs/guide/index.md:357`, `site/docs/developers.md:89`, `web/content/guide.md:454`, `web/content/developers.md:89`, `landing.html:230`, `src/g6/mcp/__init__.py:11` and `src/g6/coding_agent_gate/skill/SKILL.md:84`. `tests/site/test_home_page.py::test_the_home_page_says_the_thesis` requires it on the landing page | Step 5 |
| P0-13 | Get started installs `.[mcp]` where the README installs `.[all]` | missing | `get_started.html:25`; `landing.html:230` | Step 5 |
| P0-14 | Get started's client snippets each use a bare `g6-mcp` | missing | `web/pages/clients.py:24-25`. Pinned by `tests/site/test_get_started.py::test_each_snippet_is_the_shape_its_client_reads` | Step 5 |
| P0-15 | Get started's model panel has no Claude Code or Codex sign-in, though the README offers one | missing | `web/pages/clients.py:76-88` | Step 5 |
| P0-16 | Nothing warns that Windows Notepad may save the key file as `.env.txt` | missing | `get_started.html:95-97` | Step 5 |
| P0-17 | Get started says downloads come with the first release, though a release exists | missing | `site/docs/install.md:3`; `web/content/install.md:3`; `get_started.html:23`; `README.md:47, 150` | Step 5 |
| P0-18 | The app guide says to copy the address without its `#token=` | missing | Row E64 | Step 5 |
| P0-19 | The macOS step no longer works for unsigned apps on recent macOS | missing | Row E67 | Step 5 |
| P0-21 | Double-clicking the app and its SmartScreen prompt | deferred | The person's own step | Step 5 |
| P0-22 | Help says nothing about the meta-harness or its four steps | implemented and verified | `src/g6/core/discovery.py:155`. Test: `tests/core/test_discovery_cli_mcp.py::test_improvement_intents_point_to_the_workflow_loop` | — |
| P0-23 | Help names the parameter `params`; the tool takes `params_json` | implemented and verified | `discovery.py:166`. Test: `tests/core/test_invoke_parity.py::test_the_docs_name_g6_invoke_as_a_door_and_say_what_ok_means` | — |
| P0-24 | Asking which planned actions are safe does not rank action gating in the top three | missing | No routing change (`discovery.py:121` routes only improvement intents). The ranking was not rerun | Step 5 |
| P0-25 | "keeps picking the wrong invoice total" and "make my support ticket triage better" do not reach the meta-harness | missing | The routing rule at `discovery.py:121` covers "tune", "improve", "keeps failing" and "make my agent better", and neither recorded query | Step 5 |
| P0-26 | Describe returns the whole skill (14 to 16 thousand characters), and workforce and the meta-harness show no operations | missing | `discovery.py:147` always attaches the skill. Neither component has a block | Step 5 |
| P0-27 | The action gating call fails on the README's own keys, and describe gives no input shape | missing | Describe now shows each operation's required inputs (`src/g6/core/registry.py:131`), not a schema. The error still names no expected shape (`registry.py:223`) | Step 5 |
| P0-28 | A refusal arrives as a tool result that does not report an error, with the same sentence in four fields | missing | `src/g6/mcp/__init__.py:121-124`; `src/g6/core/door.py:152`; `src/g6/core/reliability_envelope.py:457-496` | Step 5 |
| P0-29 | The four-step loop cannot start over MCP without a Python file and a restart | implemented and verified | `metaharness_start` (`mcp/__init__.py:193`), with project and home discovery. Tests: rows E38 and E41 | — |
| P0-30 | No operation runs a repository's tests over MCP | implemented and verified | `g6_review_patch` (`mcp/__init__.py:152`). Test: `tests/harness/test_patch_review.py::test_the_mcp_tool_takes_requirements_and_describes_the_four_outcomes` | — |
| P0-31 | The skill says to declare the workflow in Python and approve from another session | implemented and verified | `src/g6/skills/using-g6/SKILL.md:43-50`. Test: `tests/skills/test_using_g6.py` asserts the same-session consent and unauthenticated-name sentences (lines 96-97) | — |
| P0-32 | The skill says workforce and the meta-harness are Python-only, with no way across over MCP | implemented, not verified | `SKILL.md:16` no longer says so. No test asserts that the old claim is gone | Step 5 |

## 3. Revision 3: steps and amendments

### Step 0 and Step 1

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| S0 | Move the earlier plan text into an archive marked as not instructions | implemented, not verified | Outside this repository; read for this audit | — |
| S1-1 | Diagnose continuous integration on the starting commit | implemented and verified | Run 40613841959 on `5733be2` passed | — |
| S1-2 | `ok` is false whenever the state is `blocked-escalated`, and `g6 invoke` then exits 1 | implemented and verified | `src/g6/core/door.py:208`. Tests: `tests/core/test_mcp_invoke_envelope.py::test_ok_is_false_when_the_result_is_blocked` and `tests/core/test_door_promotion.py::test_cli_and_mcp_give_the_same_outcome`. Commit `4151fed`; run 40644090822 | — |
| S1-3 | `g6.invoke` is a checked Python call with the same trust rules, pinned by a parity test | implemented and verified | `src/g6/__init__.py:15`. Tests: `tests/core/test_invoke_parity.py`, ten tests, among them `::test_one_outcome_on_every_surface` and `::test_a_sensitive_op_is_refused_on_every_surface_unless_the_person_allows_it` | — |
| S1-4a | Help names `params_json` | implemented and verified | Row P0-23 | — |
| S1-4b | The other drift fixes: the module's tool list, the approval wording, the skill's arrows, the owner pilot table and the wheel record | implemented, not verified | `src/g6/mcp/__init__.py:4-9`; `tests/metaharness/test_phase6_mcp.py:2-3`; `src/g6/skills/using-g6/SKILL.md:46`; `docs/releases/OWNER_PILOT.md:90-92`; `docs/validation/results/release_0_1_1/wheel.json` (source `6654b19`). No test pins this wording | — |
| S1-4c | Drift still present | missing | `src/g6/mcp/__init__.py:11` registers a bare `g6-mcp` at the client's default scope. `src/g6/metaharness/skill/SKILL.md:78` still offers approval "from another session". `README.md:96` shows "?" where arrows were meant, and so does V8's heading at `docs/validation/REGISTRY.md:461` | Step 5 |
| S1-5 | Contract wording: "declared structural obligations" | implemented and verified | `src/g6/core/block_contract.py`. Test: `tests/core/test_contract_wording.py`. Commits `5275b8a`, `02aa544` | — |
| S1-6 | Step 1's exit: continuous integration green on the exact commit, certificates renewed, a reproducible wheel | implemented and verified | Runs 40633095401 (`6654b19`) and 40644090822 (`94d99ee`), nine jobs each. `wheel.json` records an identical repeat build. `codex/ai-for-ai-continuation` is at `94d99ee` | — |
| S1b | Register the patch-review utility protocol (V9) before any Step 2 code | implemented and verified | The hash of `docs/validation/patch_review_utility/PROTOCOL.md` with Unix line endings was recomputed for this audit and matches `docs/validation/REGISTRY.md:476` (`1f2f74c6…`). Commit `c8a1a97` comes before Step 2's first commit, `bf5aaec`, and neither file has changed since | — |

### Step 2: patch review with scoped assessments

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| S2-1 | Four outcomes, each with its scope, in a fixed precedence | implemented and verified | `src/g6/harness/patch_review.py:19-25, 61-63`. Tests in `tests/harness/test_patch_review.py`: `::test_a_correct_patch_is_supported_within_its_stated_scope`, `::test_a_defect_inside_the_declared_coverage_is_detected_and_named` and `::test_a_weakened_test_compromises_test_integrity`; run 40654008114 | — |
| S2-2 | Each requirement passes on named checks, fails, or is not established; G6 never invents a pass | implemented and verified | `patch_review.py:299-305`. Tests: `::test_a_requirement_with_no_check_is_not_established_and_the_outcome_is_insufficient_evidence` and `::test_a_stated_check_that_did_not_run_is_not_evidence` | — |
| S2-3 | Merge authorisation is reported apart and needs separate approval; the report keeps its three parts apart | implemented and verified | `patch_review.py:83, 583, 593`. Test: `::test_the_reply_keeps_the_three_a5_parts_apart_and_merge_needs_separate_approval` | — |
| S2-4 | Action gating evaluates the merge when a merge profile and the host's authority are supplied | deferred | No merge profile exists, so the merge always needs separate approval (a ruling) | a later slice |
| S2-5 | Tiers 1 and 2 | implemented and verified | `patch_review.py:438-464`. Test: `::test_confined_a_derived_regression_is_a_defect_and_a_pre_existing_failure_is_not` | — |
| S2-6 | Tier 3: the full suite, mutation and old-against-new probes | deferred | Reported as skipped (`patch_review.py:90, 464`) | Step 3, VS-3 |
| S2-7 | Only G6's confined runs mint receipts; they are stored append-only and honoured only for the digest the host binds; caller input never creates that authority | implemented and verified | `src/g6/workforce/receipt_store.py:107-139`; `src/g6/coding_agent_gate/coding_agent_gate_block.py:108-112, 147-172`. Tests: `tests/coding_agent_gate/test_receipt_evidence.py::test_a_caller_cannot_send_g6_provenance_or_a_receipts_body` and `::test_at_the_registry_and_mcp_doors_a_genuine_receipt_stays_caller_declared`, and `test_patch_review.py::test_confined_receipts_make_the_gate_g6_observed_and_a_forged_one_does_not` | — |
| S2-8 | The documentation says the store does not protect against code running as the same user | implemented, not verified | `receipt_store.py:29`; `README.md:108`. No test | — |
| S2-9 | A block for any other reason is never recorded as a detection | implemented and verified | Tests: `::test_a_stale_digest_is_a_refusal_with_no_outcome` and `::test_unavailable_confinement_is_a_refusal_with_no_outcome` | — |
| S2-10 | Trial records keep each stage's reason codes, the checks run and the cost; acceptance is counted from the outcome | implemented and verified | `validation/patch_review/run.py:51-75`. Test: `test_patch_review.py::test_the_trial_counts_acceptances_from_the_outcome` | — |
| S2-11 | The six engineering cases under bubblewrap, with the four-outcome review | implemented, not verified | No result file from this version is committed. `docs/validation/results/release_0_1_1/patch-review-engineering.json` dates from the earlier version (`c1773b8`) | Step 2 exit |
| S2-12 | Step 2's exit: continuous integration green on all nine jobs, then the freeze and the fast-forward | implemented and verified | Run 40699680573 on `b3c342b`: all nine jobs pass. The earlier failure (run 40654008114, `receipt_store.py` calling `shutil.which`) is fixed in `f62e254`. `codex/ai-for-ai-continuation` was fast-forwarded from `94d99ee` to `b3c342b`. The independent security review of the fix pass was thin: a review by another model family reported nothing after five commands, and a fresh adversarial review was stopped by a safety filter before it ran any probe. The owner chose to freeze on that, together with the 14 probes of the second review rerun under bubblewrap | — |

### Step 2 fix pass (the second independent review)

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| S2-C1 | `pytest.toml` and `.pytest.toml` are treated as runner configuration everywhere it matters | implemented and verified | `673876b`. Tests: `test_patch_review.py::test_every_configuration_file_pytest_reads_is_one_g6_checks` (compares G6's lists with the installed pytest's names) and the `pytest.toml` cases of `::test_a_changed_runner_configuration_compromises_test_integrity`; run 40699680573 | — |
| S2-C2 | Weakening is detected in every changed test-related file, not only in the asserts of `test_*.py` | implemented and verified | `1f3de4e`. Tests: `test_patch_review.py::test_every_changed_test_file_and_test_helper_is_compared_for_weakening` and `::test_a_moved_test_helper_is_compared_with_its_old_text_not_read_as_deleted` | — |
| S2-C3 | Ignored bytecode, plugin entry points in packaging folders, links and compiled extensions count as loadable | implemented and verified | `a9b8ead`. Tests: `test_patch_review.py::test_an_ignored_bytecode_file_or_plugin_declaration_compromises_integrity`, six cases in `tests/workforce/test_receipt_store.py`, and `::test_a_git_ignored_link_can_change_the_run` (confined, Linux) | — |
| S2-I4 | A tree swapped between the snapshot and the run is reported as an integrity problem | implemented and verified | `4c4aef3`. Tests: `test_patch_review.py::test_a_run_that_began_on_another_tree_compromises_integrity_whatever_the_gate_says` and `test_receipt_store.py::test_a_run_says_the_digest_of_the_tree_it_began_on` | — |
| S2-I5 | A check that never started, or a whole review that ran out of time, is not reported as a defect | implemented and verified | `2c525d2`. Test: `test_patch_review.py::test_a_run_out_of_time_is_insufficient_evidence_and_a_check_it_never_reached_did_not_run`; the earlier expectation that pinned the wrong meaning was corrected in the same commit, as the plan's ruling allowed | — |
| S2-I6 | A test moved with `git mv` is replayed at its old path | implemented and verified | `9e5ee0a`. Tests: `test_patch_review.py::test_a_failing_derived_file_g6_could_not_run_at_the_base_commit_is_said_so_not_called_pre_existing` and, confined, `test_repo_tests_run.py::test_i6_a_moved_test_file_is_replayed_at_its_old_path_at_the_base_commit` | — |
| S2-I7 | An empty `[pytest]` section is still a section | implemented and verified | `d96d686`. Three cases in `test_patch_review.py::test_a_project_file_counts_only_when_its_test_runner_settings_change`, with an empty `[tool.pytest]` table as the control | — |
| S2-I8 | The diff reaches the gate, and a check that also passes at the base becomes an unknown with a next step | implemented and verified | `05bd0f7`, `2bc3b5b`. Tests: `test_patch_review.py::test_a_shortcut_in_the_diff_compromises_integrity_with_or_without_the_claim` and `::test_a_stated_check_that_passes_at_the_base_commit_too_is_an_unknown_with_a_next_action` | — |
| S2-I9 | Correct patches are not given a worse outcome (test identifiers matched against pytest's root folder, more unscanned folders, a shortcut found only in the claim) | implemented and verified | `5d4950e`, `a9b8ead`, `05bd0f7`. Tests: `test_patch_review.py::test_test_ids_are_read_against_the_root_folder_pytest_used`, five cases in `::test_ignored_environments_caches_and_other_files_do_not_withhold_the_receipt`, and `::test_only_integrity_class_gate_findings_change_the_outcome` | — |
| S2-13 | Public wording that claimed too much, and the missing same-user limit | implemented and verified | `3ba5dcd`. Test: `::test_the_docs_say_what_the_integrity_checks_and_the_receipts_cannot_show`, and the description of `g6_review_patch` states the same-user limit | — |
| S2-14b | On Windows, test files are recognised regardless of letter case | implemented and verified | `1f3de4e`, `b3c342b`. Tests: `test_repo_tests.py::test_14b_a_test_file_is_named_with_the_case_pytest_matches_on_this_system` and `::test_14b_the_tests_a_change_touches_name_a_test_file_with_the_case_pytest_matches` (each failed on Windows before its change) | — |
| S2-15 | The gate's diff scan reads only test, runner and continuous-integration files, so a correct change to source code is not accused | implemented and verified | `4441684`. Tests: `test_patch_review.py::test_a_source_change_the_diff_scan_would_flag_is_not_an_integrity_problem` and `::test_the_diff_is_split_by_file_whatever_the_names_and_the_lines`, with controls that still find a shortcut in a test or workflow file | — |
| S2-16 | The study pool's repositories can be checked: openlibrary's unregistered submodule folders and its tracked links | implemented, not verified | At `b3c342b` this is missing: G6 refuses any repository with a gitlink, and its scratch copies leave out every link, including the tracked `infogami` link openlibrary imports through. On branch `claude/pool-compat` (`771b2ca` to `8b5364c`), not yet on the line of record: <br>– a gitlink folder with no `.git` inside is accepted, and G6 hashes its files itself (bytecode folders left out); <br>– an initialised submodule is still refused; <br>– a change inside such a folder gives `insufficient_evidence` (`change_inside_submodule`); <br>– a tracked relative link whose target stays inside the copy is recreated in the copy, and any other link is left out and listed. <br>A file edited inside such a folder before G6 sees the repository cannot be detected, because git keeps no copy of it. The reply says so, and an expected-fail test records the limit. Tests: `tests/workforce/test_repo_tests_gitlinks.py`. So far only local Docker runs with bubblewrap (`tests/workforce`: 1607 passed, 47 skipped); no continuous-integration run yet. On the four openlibrary development images, the review accepts a harmless change and catches a planted break | merge into the verification-search branch, then continuous integration |
| S2-18 | A review tests the patched code, not a copy of the package installed outside the repository | implemented, not verified | At `b3c342b` this is missing. Where an environment installs the repository's own package outside the repository, as ansible's development images do, the tests load the installed, unpatched copy, so a defective patch can be accepted. On branch `claude/pool-compat`: <br>– G6 records which file each module was loaded from; <br>– a run that loaded a changed module from outside the tree, or cannot say what it loaded, is no evidence of the patch (`changed_code_not_loaded`: the outcome is at most `insufficient_evidence`, and the run gets no receipt); <br>– an `import_roots` parameter names the folders that make the patched code importable. <br>Tests: `tests/harness/test_patch_review_imports.py`. Local Docker runs only so far. On ansible's development image, the review with `import_roots=["lib"]` catches a planted break, and without it reports `changed_code_not_loaded` | as S2-16 |
| S2-19 | qutebrowser, the study's third repository, can be checked | missing | Under bubblewrap a confined run has no `/tmp`, and with the network off no `/etc/hosts`. qutebrowser's test plugin starts a display server that needs `/tmp`, so every run waits until its deadline. The fix changes `src/g6/core/confine.py`, which five certificates bind, so it needs an independent review and a normal reissue of those certificates. After pytest finishes, qutebrowser's test process also exits with an error from the display library. How G6 should read that is decided once it has been observed under G6 | the one certificate reissue before the study freeze |
| S2-17 | A deep adversarial review of patch review, for escapes and for correct patches accused | deferred | Moved by the owner to the security gate before go-live (see S2-12) | go-live security gate |
| S2-M | The deferred minor findings 10, 11, 12 and 14a | deferred | Ledgered, not fixed in this pass | after the fix pass |

### Steps 3 to 6 and the theory documents

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| S3-1 | One supported workflow, changing the patch reviewer's check selection, with its safety criteria locked outside the editable schema | missing | Not started | Step 3 |
| S3-2 | An evaluator shipped in the package, read-only frozen cases, a confined candidate, an execution receipt, and a fresh review before first use | missing | `Workflow.patch_evaluator` is a hook the host supplies (`src/g6/metaharness/schema.py:287`), and without one the gate blocks (`gate.py:88-102`) | Step 3 |
| S3-3 | The gate receives four separate quantities: the p-value, the improvement with its interval, a calibrated lower confidence bound and the evaluator's receipt | missing | Not started | Step 3 |
| S3-4 | Promotion uses the existing frozen policy, whose threshold is 0.01, and the gate's minimum confidence | implemented, not verified | `EvidencePolicy` has `alpha=0.05` and `max_evaluations=5` (`schema.py:104-117`). The minimum is 0.75 (`src/g6/self_training/patch_promotion_gate.py:80`, checked at `:303`), which confirms the reading the amendment asked to check. Step 3 does not use these yet | Step 3 |
| S3-5 | Two demonstrations kept apart: the mechanism, and adaptation on fresh frozen cases | missing | Not started | Step 3 |
| S3-6 | Composition: receipt references, per-stage digests, and a test that an adapter cannot strengthen evidence | missing | Row E80 | Step 3 |
| S4-1 | The protocol and its registry entry come before any evaluation data | implemented and verified | Row S1b | — |
| S4-2 | Study scripts: the review-contract splitter, the label runner, the arms, the statistics and the report checks | missing | Being built on a separate branch; not pushed when this audit was written | Step 4 |
| S4-3 | Freeze G6's commit and the case manifest, then the seeded draw, the patches, the labels and the arms | missing | Waits for the verification-search freeze | Step 4 |
| S4-4 | Experiment 2, human effort | deferred | Its reviewers have not been recruited | Step 4 |
| S5-1 | The owner pilot on the tested wheel in both clients, including a retest of the preserved decision | missing | `docs/releases/OWNER_PILOT.md:86-94` is mostly pending. Codex cancel and accept were observed (`UAT_ROUND_1.md:61-65`). Claude Code is untested | Step 5 |
| S5-2 | A decision saved before an upgrade comes back protected, with both hashes and no prompt | implemented and verified | `G6_E_META_PROTECTED` (`src/g6/metaharness/promotion.py`). Test: `tests/metaharness/test_cross_process_approval.py::test_upgrade_refuses_approval_before_consent_with_judging_hashes`. The owner's retest is row S5-1 | — |
| S5-3 | Consent that changes between display and acceptance, and a missing prompt, are refused | implemented and verified | Tests: `tests/metaharness/test_uat_diagnostics.py::test_changed_digest_refuses_before_any_consent_request` and `tests/metaharness/test_phase6_mcp.py::test_over_mcp_declined_or_missing_client_consent_never_approves` | — |
| S5-4 | A refusal to approve a decision that did not recommend, on a fresh fixture, in a real client | missing | The owner's step | Step 5 |
| S5-5 | Ten coding-agent verification journeys with unfamiliar people | missing | Not run | Step 5 |
| S5-6 | The journey's friction: project selection, PowerShell commands and an environment step, doctor's wording, setup's next steps, `g6_find` routing, a slimmer describe | missing | P0-01, P0-02, P0-04, P0-05, P0-07, P0-08, P0-16, P0-24, P0-25 and P0-26, and row E42, are open | Step 5 |
| S5-7 | Site and docs honesty for the journey, with Get started pointing to `g6 setup` and `.[all]` | missing | Rows E25 to E34, E64, E67 to E72; `get_started.html:25` still installs `.[mcp]` | Step 5 |
| S6-1 | The library comparison and the question whether an agent builds a patch-review workflow from G6's parts with less effort | deferred | After Steps 4 and 5 | Step 6 |
| S6-2 | V8 as a registered feasibility study | deferred | Row E81 | Step 6 |
| S6-3 | More executors, wider MCP blocks, Python 3.14 and the extension programme | deferred | Rows E39, E56, E58, E89 | Step 6 |
| ST | The theory documents | missing | Row E88 | a separate pull request, not blocking |

### Amendments A1 to A6

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| A1 | The gate gets four separate quantities, and promotion uses the existing frozen threshold | missing | Rows S3-3 and S3-4 | Step 3 |
| A2 | Experiment 1 must also reward correct acceptance, within a 15-point margin | implemented and verified | `docs/validation/patch_review_utility/PROTOCOL.md:50, 258`; registered text, hash recomputed. No study has run | — |
| A3 | The protocol comes before Step 2; feasibility is kept apart from confirmation | implemented and verified | `PROTOCOL.md:57-80, 99-107`. Registered before Step 2 (row S1b) | — |
| A4 | Experiment 2 is randomised and counterbalanced, and time is reported with the errors | implemented and verified | `PROTOCOL.md:297-315`; registered text | — |
| A5 | The report keeps three parts apart, including a standing note that the specification may be incomplete | implemented and verified | `PROTOCOL.md:333`; `src/g6/harness/patch_review.py:83`. Test: `test_patch_review.py::test_the_reply_keeps_the_three_a5_parts_apart_and_merge_needs_separate_approval` | — |
| A6 | Each receipt binds the source before and after the run, the executable, the test selection, the isolation and the result; the store's same-user limit is documented | implemented and verified | `src/g6/workforce/receipt_store.py:183-191`. Tests: `tests/workforce/test_receipt_store.py::test_a_confined_run_with_receipt_records_one_receipt_binding_the_run` and `::test_a_run_confined_by_bubblewrap_records_its_receipt`. The limit is row S2-8 | — |

## 4. Verification search

The workstream that adds an independent check that an agent's change completed its task: what it was asked to do,
whether it preserved what it must not break, and whether it is tested enough. Go-live waited for its study, V10; for the minimal launch (section 5) V10 and VS-5's
study continue after launch. The rows give each phase's status at the launch tip `0ce616e`. A row's earlier text is
kept in the history of this file.

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| VS-pre | The existing parts the workstream builds on are present | implemented, not verified | `IntentContract` (`src/g6/intent_engine/schema.py:105`), `IntentElicitor` (`elicitor.py:42`), `detect_test_weakening` (`src/g6/safety_engine/weakening.py:142`), `check_invariant` and `prove_invariant` (`src/g6/model_checking/symbolic.py:202-212`), `check_contract` (`src/g6/formal/contract.py:22`), `check_equivalence` (`src/g6/formal/z3_depth.py:541`), `Z3Verifier` (`src/g6/cegis/z3_verifier.py:201`) and `layer_violations` (`src/g6/code/design_governance/graph.py:259`). Their fitness for this use is untested | — |
| VS-0 | Register the V10 protocol before any verification-search code | implemented and verified | `cd52d48` on `claude/vs` adds `docs/validation/patch_review_search/PROTOCOL.md` and its registry entry; the file's hash with Unix line endings (`2573c84e…`) matched the entry, and no verification-search code exists before it. `tests/site/test_evidence.py` passes against it. At `0ce616e`: both studies were re-registered as version 2 before any task was drawn (`683308d`, with arm R and broken-task handling), under new registry entries (`docs/validation/REGISTRY.md:529, 552`). The hashes of the two files with Unix line endings were recomputed for this update and match: V9 version 2 `39a7859e…`, V10 version 2 `715dce8a…`. Rows S1b and A2 to A4 cite version 1 of V9 (`1f2f74c6…`), which stays registered | — |
| VS-1 | Intent modelling: requirements from an intent contract, the provenance of every requirement, and an optional `intent` parameter | implemented and verified | `43f0234` on `claude/vs`. `requirements_from_intent` and the `intent` parameter of `review_patch` and `g6_review_patch`. Tests: `tests/harness/test_verification_search.py`, and the identity test `tests/harness/test_patch_review_off_path.py` with its golden file: with no `intent`, every decided field is unchanged. Continuous integration: run 40768957226 on `051b062`, a descendant, passed all nine jobs. At `0ce616e` it is on the launch line, and the identity test still has its golden file (`tests/harness/test_patch_review_off_path.py`; it needs bubblewrap and skips elsewhere) | — |
| VS-2 | The executor, the classifier and the completion report for checks the caller supplies | implemented and verified | `bb218ce` and `ae11694`, then the fix pass after its review, `d8f06c6` and `051b062`, on `claude/vs`. The optional `proposed_checks` run confined on the patched tree and at the base commit, are classified by their declared intent, never pass a requirement, and give at most `insufficient_evidence`; every reply has `assessment.completion`. Tests: `tests/harness/test_proposed_checks.py`. Continuous integration: run 40768957226 on `051b062` passed all nine jobs. At `0ce616e` it is on the launch line. Later fixes: a refusal is read only from G6's own record of the run, and a check that errors only on the patch gives `check_error_on_patch` (`7ae5155`, half B's B2) | — |
| VS-3 | The template registry and compiler, five templates, and test adequacy through mutation | implemented, not verified | `51ba696`, `43a85ee` and `633f61b` on `claude/vs` (with the pool-compatibility merge `c8a15c9`). `src/g6/harness/verification_templates.py`, the `template_instances` and `search` parameters, and test adequacy reported only. Tests: `tests/harness/test_verification_templates.py`. At `0ce616e`: its independent review found three critical, seven important and two minor problems (C1 to C3, I1 to I7, M1, M2); the fixes are `fa42d9d` (R-C1: a structural check never passes a requirement; git's index flags never hide a change), `f786774` (import chains, contract options honoured or refused, one folder per package), `ab7be35` (G6's own errors stay activities, the deadline holds, no link outside is read) and `906a4f1` (a canonical structural form, a stable base), each with tests that failed first. The review's report is not in the repository; this row rests on those commit messages. The runs were local Docker runs with bubblewrap; run 40780356106 failed on two further problems that are fixed (row L-CI3). No green run exists for the launch line's tip | VS-3 |
| VS-4 | The formal path, with a three-part report: specification, model, correspondence | implemented, not verified | `8653440`, `8a5a1b4`, `644c7e7` and `52544c1`: `state_invariant.v1`, `access_rule.v1`'s multi-step form, Z3 counterexamples re-run confined, and the docs. Tests: `tests/harness/test_verification_formal.py`. Its review found a critical integrity problem (finding 1: a weakened test hidden by git's own index or replace refs read `supported_within_scope`), fixed in `d58e5d1` and `a95525d` (`tests/workforce/test_repo_tests.py::test_the_snapshot_binds_a_changed_files_bytes_whatever_the_repositorys_index_says`), and findings 2 to 6 in `c3d7351`. A fresh review of that fix is a launch gate (row L-G1). Not verified: no green continuous-integration run for the launch line's tip | VS-4 |
| VS-5 | The opt-in proposer through the person's own model, with injection fixtures and one small model smoke on development fixtures | implemented, not verified | In the code and off by default (`search={"propose": true}`): `34390b2`, `a24b6f2`. Tests: `tests/harness/test_verification_proposer.py` (a fake model; no model called). One paid smoke on six development fixtures: 42 calls, $0.15 of its $10 stop (`df55a78`, `validation/proposer_smoke/record.json`); three of six planted defects came back as unconfirmed findings, one was missed, and every `state_invariant.v1` instance was refused. The request was fixed afterwards (`d7c285c`); the smoke has not been rerun. No study. Its fresh review is half B of the launch review (rows L-B to L-B8) | VS-5 |
| VS-6 | The ledgers, adoption detection, `g6_confirm_behaviour`, and worked examples from three kinds of application | deferred | No `g6_confirm_behaviour` tool at `0ce616e` | after launch |
| VS-7 | Freeze the workstream's commit, then run V10 together with V9 | deferred | Waits for VS-6 and the study; V10 continues in parallel after launch | after launch |
| V10 | The V10 feasibility report, which go-live needed | deferred | Registered as version 2 and not run (row VS-0). The minimal launch does not wait for it | after launch |

## 5. The minimal launch

The owner's decision of 29 October 2026 (night): a minimal launch for Claude Code and Codex. For this first launch it
supersedes holding go-live for V10, the 8-of-10 fresh-user trial before posts, and venture in Step 5. Audited at
`0ce616e`, the tip of `claude/launch`; the rows added on 30 October 2026 (L-W7, sections 5.3b and 5.3c, L-P5, L-P6,
L-G2d) and the rows they changed were audited at `432e623`; row L-G3 and the two gate lines that cite it record the
run on `0c44e6d`. The release notes are `docs/releases/launch.md`.

- **Ships:** G6's local MCP server, installed from the repository and connected by `g6 setup --client claude` or
  `--client codex`; `g6_review_patch`, with no model calls by G6; the repair loop the agent runs, through the skill;
  the readable report, which leads with reproduced findings; the install wording and setup's closing lines; the skill
  text for both clients.
- **Deferred until after launch:** VS-5's study and V10 (they continue in parallel); B1 and B2; C; E0; E6; the
  confine `/tmp` change.
- **Not skipped (the release gates):** the VS-4 review's integrity fix with a fresh review of it; a deep fresh
  security review of exactly what ships; continuous integration green on the release commit (a red macOS job is
  acceptable); the owner's pilot in Claude Code and in Codex; honest copy.
- **The owner's steps:** squash-merge PR #1, tag the release, deploy the site.

How the statuses were set: "implemented and verified" means the commit message records the test failing first and then
passing and the named test exists in the merged tree. The suites were not rerun for this update. Where a row says
"unverified", the history does not hold the evidence, and the row says what is missing.

### 5.1 What ships

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-S1 | The local MCP server installs from the repository and connects with `g6 setup --client claude` or `--client codex --allow patch_review.review`. Setup's closing lines print the first review call, or how to allow patch review, and say how to replace an existing `g6` entry | implemented and verified | `39a43d7`, `3a37d1e`, `0f62916`, `28a2c3b`. Tests: `tests/core/test_setup_clients.py` and, in `tests/site/test_launch_copy.py`, `::test_get_started_shows_the_whole_first_journey_for_both_clients_in_order`, `::test_the_readme_shows_the_same_journey` and `::test_every_first_journey_installs_all_and_runs_setup_never_a_bare_mcp_add`. Not covered: the owner's pilot in the two clients (row L-G4) | — |
| L-S2 | `g6_review_patch` makes no model call by G6 | implemented and verified | Patch review calls no model unless the caller sets `search={"propose": true}`, and then only with a configured model and a boundary (`34390b2`; `docs/releases/launch.md`). Tests: `tests/harness/test_verification_proposer.py` (a fake model throughout). The proposer is in the code, off by default, unused by the launch skill, with one development smoke and no study (row VS-5) | — |
| L-S3 | The repair loop through the skill: review the patch before saying a task is done, fix what G6 reproduced, add each reproduction as a stated check, review again, stop after three rounds, never call a patch verified, never merge | implemented, not verified | `a2658bc`, `b379a9a`; the skill is `src/g6/skills/using-g6/SKILL.md` (975 words under the 1,000-word cap, and the references file). Tests of the text: `tests/skills/test_using_g6.py` (names are real, templates and parameters exist, stays short). Not verified: the skill's agent trials (`docs/skills/using-g6/TESTING.md`) were not run, because they need model calls, so no evidence shows that an agent follows the loop | — |
| L-S4 | The readable report, `assessment.report`: at most 40 lines, reproduced findings first, then what is not established, then what the person decides | implemented and verified | `d87ed76`, `db53e43` (the repository's plain command, base evidence in words, no tier names), `6e5de6f`, `759f9fa`. Tests: `tests/harness/test_patch_report.py` | — |
| L-S5 | A non-zero exit after a session that passed is `run_inconsistent` (insufficient evidence), not an integrity accusation | implemented and verified | `5edb483`, the owner's ruling. Tests: `tests/workforce/test_repo_tests_run.py::test_a_run_that_exits_with_an_error_after_every_test_passed_is_not_counted_as_a_pass`, `tests/harness/test_patch_review.py::test_a_run_that_exits_with_an_error_after_every_test_passed_is_insufficient_evidence` and the verdict table in `tests/harness/test_patch_review_security.py`. The ruling is asymmetric on purpose: exit 0 with a failure shown, or fewer tests finished than collected, stays untrusted | — |

### 5.2 The launch's security review, in two halves

Half A covers isolation, integrity, MCP and setup. Its first reviewer was stopped by a safety classifier before writing
any probe. The owner chose a fresh reviewer, who read the code in full and ran the project's existing sandbox and
integrity suites (Docker with bubblewrap included) and **wrote no new escape probes: the boundary half had no new
escape probes.** Half B covers templates, the verification search, the formal path and the proposer, and also served as
VS-5's fresh review. Half B's reviewer wrote probes, and its findings are fixed with those probes as tests. Neither
reviewer's report is in the repository; the rows rest on the commit messages that name the findings and on the owner's
account.

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-A | Half A: isolation, integrity, MCP, setup. A fresh reviewer, no new escape probes | implemented, not verified | The findings below, fixed in `eb97841` to `f9d769a`, on the reviewed commit `b5c5705`. Unverified: the reviewer's report and the list of suites it ran are not in the repository, and the history records no run of those suites | — |
| L-C1 | C1 (Critical): a weakened test in a file pytest's default names miss (`app/tests.py`, `testcalc.py`) read `supported_within_scope` | implemented and verified | `eb97841`. Tests: `tests/harness/test_patch_review_security.py::test_a_weakened_test_outside_pytests_default_names_compromises_integrity` and the cases for the repository's `python_files` in seven configuration forms. The development cases' outcomes are unchanged (the identity test) | — |
| L-I1 | I1: with unconfined runs allowed and no boundary, patch review ran the checks unconfined | implemented and verified | `f41e158`; the copy in `a2b82de`. Patch review now never runs a patch without the boundary. Tests: `test_patch_review_security.py::test_with_unconfined_runs_allowed_and_no_boundary_the_review_starts_nothing` and `::test_each_run_patch_review_makes_is_refused_before_it_starts_without_the_boundary`; `tests/site/test_launch_copy.py::test_patch_review_says_it_needs_the_boundary_whatever_the_person_allowed`. On macOS patch review refuses | — |
| L-I2 | I2: a visible file the patch adds that could change what the tests load (a plugin declaration, an extension module, bytecode, a link, a module's twin) went past integrity | implemented and verified | `d63f40a`; the follow-up test in `27cd305`. Reason `changed_file_can_change_tests`. Tests: six cases in `test_patch_review_security.py`, with controls for an ordinary new module and a moved module | — |
| L-I3 | I3: a re-pinned data file or snapshot was never mentioned | implemented and verified | `1e21764`. Tests: `test_patch_review_security.py::test_changed_test_data_or_a_snapshot_is_insufficient_evidence_and_the_person_is_asked` and `::test_a_changed_file_outside_the_test_folders_leaves_the_outcome_as_it_was`. Cost accepted: a correct patch that updates a snapshot reads `insufficient_evidence` until the person looks | — |
| L-I4 | I4: a touched-module test that fails and is not compared with the base was only an unknown | implemented and verified | `1fcc74c`. Evidence reason `derived_test_not_compared` on nine branches; one test per branch in `test_patch_review_security.py`. One existing test was changed as strictly as the fix required (`test_a_derived_test_failing_on_base_and_patch_is_pre_existing_not_a_defect`) | — |
| L-I5 | I5: the patch's own code runs in pytest's own process, so a patch written to defeat the checks can make them pass | implemented and verified | A limit stated, not removed: `a2b82de` adds one sentence to the README, the release notes and the workflow guide. Test: `test_launch_copy.py::test_patch_review_says_the_patch_runs_in_pytests_own_process`. G6 does not catch this case (section 5.9) | — |
| L-I6 | I6: a module the patch imports that is not installed was blamed on the environment, so an agent following the advice would install whatever the name says | implemented and verified | `c568c30`. Reason `patch_imports_missing_module`; the next action asks the person to check the name before installing anything. Tests: `test_patch_review_security.py::test_a_module_the_patch_imports_that_is_not_installed_is_asked_about_never_blamed_on_the_environment` and a confined case | — |
| L-M1 | M1: a report that hid a failure G6 had recorded read `run_inconsistent` instead of integrity | implemented and verified | `f9d769a`. Test: `test_patch_review_security.py::test_confined_a_report_that_hides_a_failure_g6_recorded_compromises_integrity` | — |
| L-M2 | M2 to M5, the deferred minors of half A | deferred | Unverified: no commit or document in the history lists them. The owner's brief for this update names them | after launch |
| L-B | Half B: templates, the verification search, the formal path and the proposer; also VS-5's fresh review | implemented, not verified | The findings below. Unverified: the reviewer's report is not in the repository. The probes it names are cited in the commit messages (`probe_proposer_repository_contract.py`, `probe_classifier_refusal_text.py`, `probe_proposer_limits_through_default_caller.py`, `probe_ignore_imports_backtracking.py`, `probe_cross_venv_home_bind.py`), and their files were not looked for | — |
| L-B1 | B1: a proposer reply naming the repository's contract was counted as normative and produced `defect_detected` | implemented and verified | `f3411d1`. Test: `tests/harness/test_patch_review_security_b.py::test_an_instance_a_model_proposed_is_never_normative_whatever_contract_it_names`, a confined case with the reviewer's probe, and the control that the same instance stated by the caller is a defect as before | — |
| L-B2 | B2: a patch could silence a counterexample by printing words like "Connection refused" | implemented and verified | `7ae5155`. A refusal is read only from G6's own record of the run. Tests: `test_patch_review_security_b.py::test_a_refusal_is_read_from_g6s_own_record_of_the_run_never_from_text_the_code_printed` and the confined `test_confined_the_patch_cannot_silence_a_counterexample`. Cost: section 5.9 | — |
| L-B3 | B3: the report said the checks came from "you" | implemented and verified | `6e5de6f`. Test: `tests/harness/test_patch_report.py::test_the_report_says_the_checks_were_stated_in_the_request_never_by_you` | — |
| L-B4 | B4: the proposer's call and token limits did not hold behind G6's own retry | implemented and verified | `0c2a4f9`. Test: `tests/harness/test_verification_proposer.py::test_the_limits_hold_for_every_request_the_provider_receives_behind_g6s_retry` | — |
| L-B5 | B5: an `ignore_imports` rule could take seconds to match, by backtracking | implemented and verified | `98c45f9`. Tests: `tests/harness/test_verification_templates.py::test_a_rule_of_many_wildcards_against_a_long_module_name_is_matched_in_a_moment` and `::test_ignore_imports_wildcards_mean_what_import_linter_says` | — |
| L-B6 | B6: a proposed check's reproduction line left out G6's caution | implemented and verified | `759f9fa`. Test: `tests/harness/test_patch_report.py::test_a_proposed_checks_reproduction_carries_g6s_caution` | — |
| L-B7 | B7: public text that the findings contradicted (the plugin check, the proposer's limits, the reproduction's meaning) | implemented and verified | `d01710d`. Four tests in `test_patch_review_security_b.py`, among them `::test_the_docs_say_what_the_plugin_check_refuses_and_what_it_does_not_detect` | — |
| L-BC | Half B's Critical: a committed `.venv` whose `pyvenv.cfg` named a folder of the person's (for example the key folder) got that folder bound, read only, into the confined run | implemented and verified | Fixed on the owner's own instruction, `5dad0fe`: `repo_tests.interpreter` takes the repository's environment or `G6_REPO_PYTHON` only when every folder a confined run reads is inside the repository, is one G6's own interpreter reads, or holds a Python standard library; otherwise `G6_E_REPO_PYTHON_INVALID`. `confine.py` is not edited. Tests: `test_patch_review_security.py::test_a_repository_environment_that_would_open_a_folder_of_the_persons_is_refused_before_anything_runs`, `::test_confined_a_repository_environment_never_opens_a_folder_of_the_persons` (the control reads the key out of sight) and two more | — |
| L-B8 | B8 to B10, the deferred findings of half B | deferred | Unverified: no commit or document in the history lists them. The owner's brief for this update names them | after launch |

### 5.3 The fixes found by the worked examples (W1 to W5), merged as `a923002`

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-W1 | **Security fix.** The server starts with `python -P`, so a project's packages never replace G6's own. Before the fix, in starlette's repository G6 ran the repository's `starlette` outside any boundary | implemented and verified | `1e6aad6`. Tests: `tests/core/test_mcp_registered_command.py` (the exact registered command, started in a project that shadows pydantic, starlette, anyio, mcp and typing_extensions; a plain `-c` start imports all five, as the control; a scan of the docs and code for a bare start) and the pin in `tests/core/test_setup_clients.py`. An entry made before the fix keeps the old command; the release notes say how to replace it. `promotion_custody.py` still starts a `python -m` child (section 5.9) | — |
| L-W2 | A finished review replies compactly over MCP: the report first, with the full assessment saved and `detail="full"` to read it back | implemented and verified | `ac18373`. Before: 64,000 to 242,000 characters per reply. After: about 8,000. Tests: `tests/core/test_mcp_review_reply.py` (seven tests, with a real reply as the fixture `tests/core/data/worked_example_review_reply.json`). The Python API's reply and every decided field are unchanged | — |
| L-W3 | Guidance for git-ignored files named like modules: which files, why, and `git add -f <path>` or remove them | implemented and verified | `c9ddf51`. Only the guidance is new; no outcome or decided field changes. Tests: `tests/harness/test_ignored_file_guidance.py` (five tests). The rule itself still errs toward an alarm (section 5.9) | — |
| L-W4 | A refusal of template bindings names each binding missing or misnamed and gives one valid instance | implemented and verified | `12469f1`. Tests: `tests/harness/test_template_examples.py` (every example is valid against the real schema; the refusal names each binding). The skill's `references/templates.md` gives the same examples | — |
| L-W5 | **Security fix.** The server makes no network request when it starts or lists its tools, and FastMCP reads no project `.env` | implemented and verified | `755ccba`. FastMCP asked PyPI for its latest version at each start, and read its settings from the project's `.env`, where a line could name a class for FastMCP to import and build. Tests: `tests/core/test_mcp_offline_start.py` (every outbound connection and name lookup recorded and refused; the person's own `FASTMCP_CHECK_FOR_UPDATES` is kept as the survival control) | — |
| L-W6 | A fresh security review of W1 to W5, and of what the server reads from the project | implemented, not verified | Done. A safety classifier stopped the reviewer at its last step, a planted-conftest check of its finding F4; it was not retried. Its findings are rows L-F1 to L-F8; F4 was re-graded High from the reviewer's existing output. Unverified: the report is not in the repository | — |
| L-W7 | W1 carried to every G6 module a person is told to run: `python -P -m g6.<module>`, above all `g6.workforce.decide`, which records the person's own decisions | implemented and verified | `83de848`: 13 places in workforce, `g6.bundles`, `g6.research.echa.example` and the loop design. Tests: `tests/core/test_mcp_registered_command.py` (the scan finds a bare `python -m g6.<module>` anywhere in the docs, skills and source text, with survival controls for the decide forms and passing controls for `-P`, `-I` and `python -m pytest`; built site assets and the worked examples' record are left out as not instructions) and `tests/workforce/test_workforce_result.py` | — |

### 5.3b The review of W1 to W5: F1 to F8, merged as `92cb164`

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-F1 | **Security fix, Windows.** Every git command starts the git on `PATH`, never a `git.exe` in the project's folder | implemented and verified | `6026282`. Tests: `tests/workforce/test_repo_tests_git_program.py` (a `git.exe` stand-in in the current folder never runs; no argv in `src/g6` begins with a bare `git`) | — |
| L-F2 | **Security fix.** A meta-harness confirmation a project's files carry never counts, and the tools that run a workflow's code are sensitive operations | implemented and verified | `eccee83`. Confirmations are recorded under `G6_HOME`; `metaharness_run`, `metaharness_evaluate_start` and `metaharness_evaluate` need `G6_MCP_SENSITIVE`. Tests: `tests/metaharness/test_forged_confirmation.py`. Its review found a version row the project's store carried still counted (row L-R2) | — |
| L-F3 | **Security fix.** G6's second MCP server, `g6.abstraction.server`, starts with `-P` and reads no project `.env` | implemented and verified | `c0d4d5c`. Tests: `tests/abstraction/test_abstraction_server_start.py` (a project with planted packages and a `.env`; a plain start imports the planted package, as the control) | — |
| L-F4 | **Integrity fix (re-graded High).** A file a patch force-adds under a git-ignored path is a changed file; before, it was in neither the changed-file list nor the ignored-file scan, and a force-added `conftest.py` read `supported_within_scope` | implemented and verified | `a195be7`. W3b's advice no longer says `git add -f`. Tests: `tests/harness/test_force_added_files.py` (benign content only). Its review found the index paths it reads unchecked (row L-R1) | — |
| L-F5 | File and binding names reach G6's guidance as one inert line | implemented and verified | `71018c5`. Tests: `tests/harness/test_inert_names.py`. Its review found the reasons and unknowns still carried line breaks (row L-R4) | — |
| L-F6 | The compact reply keeps every integrity reason whole and first | implemented and verified | `615e29c`. Tests: `tests/core/test_mcp_review_reply_integrity.py` | — |
| L-F7 | The `reviews/` folder's access rule and clean-up; the doctor's migration | deferred | Ledgered by the review, not fixed in the pass | after launch |
| L-F8 | pdm-backend's `.pdm-build` output is not scanned as an ignored input (the false alarm of worked example 9) | implemented and verified | `805837a`. Tests: `tests/harness/test_pdm_build_output.py`, with controls that an ignored top-level `.pth` and an ignored `conftest.py` still compromise integrity | — |

### 5.3c The review of that fix pass, fixed in `0c55c8b`

A fresh reviewer read `9150733..eccee83`. A safety classifier stopped it while it wrote its report: findings 1 and 2
arrived whole, and the rest were read from its probes and mutation runs. It was not retried.

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-R1 | **Security fix (High, a regression from F4).** An entry written into the repository's own index could name a path outside it, and G6 put that file's text in the diff the gate and the proposer read and its hash in the snapshot. Now a path git never stages itself (a control character, a drive, or an empty, `.`, `..` or `.git` part) refuses the review with `G6_E_GIT_REPO_INVALID` | implemented and verified | `repo_tests._index_path`, applied in `_tracked_new`, `_held`, `_gitlinks` and the snapshot's marked entries. Tests: `tests/harness/test_force_added_files.py` (five forms of the entry, two survival controls for `@scope/util.py` and `-notes.txt`), 17 passed confined in Docker and on Windows in the fresh review at `55ad65d` (row L-R6). Its review found the check judged the text only (row L-R8) | — |
| L-R2 | **Security fix (High, F2 left open).** A version the project's store carried still ran, was compared in audits and could be a rollback target. G6 now records each version it makes the head under `G6_HOME`, and the head, the previous approved version and every rollback target come only from those | implemented and verified | `metaharness.api.versions_path`. A confirmation counts the store's version 0 only when it is exactly the initial version it makes, and otherwise refuses with the next step. Tests: `tests/metaharness/test_forged_confirmation.py` (a shipped activation, a version written after confirmation, a shipped initial version with other code). Not covered: `level2.credit` and `report` still read versions G6 did not record (section 5.9) | — |
| L-R3 | A person's third confirmation of the same workflow was refused (the store reused one row id) | implemented and verified | Each re-confirmation gets its own id (`metaharness/store.py`). Test: the two-homes case in `tests/metaharness/test_forged_confirmation.py` | — |
| L-R4 | The reasons, unknowns and other next actions still carried a repository's file names with their line breaks | implemented and verified | `patch_review._one_line`; it decides nothing and the identity test passes. Test: `tests/harness/test_inert_names.py` (a crafted name in every text of the reply), 4 passed in Docker and on Windows in the fresh review at `55ad65d`. It does not cover U+0085, U+2028 and U+2029, or a caller's requirement id (row L-RM2) | — |
| L-R5 | Coverage: reverting F4 in `diff_text`, `snapshot` and `_base_revision`, or removing F2's gate from the evaluation tools, failed no test | implemented and verified | Tests in `tests/harness/test_force_added_files.py` and `tests/metaharness/test_forged_confirmation.py`; each reversion was checked to fail them | — |
| L-R6 | A fresh review of `83de848` and `0c55c8b`, by reading and the existing tests only (the owner's choice, 2026-10-30) | implemented, not verified | Done at `55ad65d`: no Critical, two Important (rows L-R7, L-R8), seven Minor (row L-RM2). Every existing test it ran passed: 34 files confined in Docker, the touched ones on Windows too. Unverified: the report is not in the repository | — |
| L-R7 | **Security fix (Important).** The store kept a profile under its digest and never checked that the content has that digest, so a shipped store whose row under the digest of the initial version a person's confirmation makes held other code ran that code under the person's confirmation | implemented and verified | `432e623`. The store's rule now requires the content's digest too, on every load and append, and a store or run ledger that does not verify blocks the workflow (`G6_E_META_WORKFLOW_INVALID`) instead of raising. Test: `tests/metaharness/test_forged_confirmation.py::test_a_shipped_profile_row_whose_content_is_not_its_digest_never_runs` (before the fix, the confirmation counted the row) | — |
| L-R8 | **Security fix (Important).** The index-path check of row L-R1 judged a path's text only: an entry beyond a link (`cfg/config`, with `cfg` a link to .git) or a name Windows reads as .git (`.git.`, `.git `, `GIT~1`, `.git::$INDEX_ALLOCATION`) still reached the repository's git config | implemented and verified | `432e623`. `_index_path` now refuses a path beyond a link or junction (git never stages one) and applies git's core.protectNTFS reading of `.git`, on every platform. Tests: `tests/harness/test_force_added_files.py::test_an_index_entry_beyond_a_link_refuses_the_review` (Linux; skipped where the account cannot make links) and `::test_an_index_entry_naming_the_git_folder_as_windows_reads_it_refuses_the_review` (four forms); the survival list adds `.github/notes.txt`. Cost: a working tree that replaced a tracked folder with a link is refused until the change is staged | — |
| L-RM | A review with hundreds of integrity reasons gives a compact reply over its budget, because those reasons are kept whole | deferred | A ruling: the client saves the long reply to a file, and the outcome and the report still say integrity is compromised | after launch |
| L-RM2 | The fresh review's minor findings: M1, the index check refuses a tracked name with a control character or a top-level `X:` name, which git stages on Linux and macOS, with wording that is then untrue; M2, the monitor's demotion can fail on a store row G6 did not record; M3, an unreadable versions file is rewritten with only the new version; M4, two G6 homes void each other's confirmation; M5, `inspect`, `export` and `activate`'s snapshot label read unrecorded rows; M6, `_one_line` misses U+0085, U+2028 and U+2029, and a caller's requirement id is used raw; M7, the `-P` scan misses a `python -c` start that is not the server's, and descriptions in the site's built knowledge graph. Also its open question: a new file's raw name in `diff_text` can give the gate's diff parse a wrong name for that file | deferred | Graded Minor by their effect on a founder using the launch: none makes a review or a workflow run accept what it should refuse, and integrity checks by name read the change's true file names. Each comes with the test the review named | after launch |

### 5.3d The owner's pilot (30 October 2026): six fixes the owner approved, in `24917ea`, and its fresh review

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-O1 | A check whose test file could not load at the base commit is never said to fail there | implemented and verified | `24917ea`: `patch_review._not_loaded` (such checks become `not_run` there, one unknown per file) and `patch_report._base_words` (failing, passing and could-not-run counted apart). Reproduced from the pilot with plain pytest first. Tests: `tests/harness/test_patch_review.py::test_a_stated_check_whose_test_file_cannot_load_at_the_base_commit_is_not_said_to_fail_there`, `tests/harness/test_patch_report.py::test_checks_that_could_not_run_at_the_base_commit_are_never_said_to_fail_there`, `::test_a_mixed_base_run_counts_each_kind_in_plain_words`; Linux bubblewrap: the touched files pass | — |
| L-O2 | The report lists the unknowns and names the agent's proposed checks and template instances with their results, which never pass a requirement | implemented and verified | `24917ea`, and the wording the fresh review corrected (row L-O7). Tests: `tests/harness/test_patch_report.py::test_the_unknowns_are_listed_not_only_counted`, `::test_many_unknowns_are_cut_at_the_reports_limit_and_say_where_the_rest_are`, `::test_the_agents_own_checks_are_reported_with_their_results_and_decide_nothing`, `::test_a_structural_instances_result_is_said_in_plain_words_and_not_said_to_decide_nothing` | — |
| L-O3 | After a no or a cancel in the form, `metaharness_confirm` tells the agent to ask the person and offers no other way to confirm | implemented and verified | `24917ea`, `src/g6/mcp/__init__.py`; the terminal command stays for a client with no form. Tests: `tests/metaharness/test_uat_diagnostics.py::test_a_no_in_the_form_is_never_followed_by_another_way_to_confirm` (decline, cancel), `::test_a_client_without_a_form_is_told_the_person_can_confirm_in_a_terminal` | — |
| L-O4 | `g6 setup` replaces an earlier copy of its own skill and says so | implemented and verified | `24917ea`, `src/g6/cli/setup.py`. Test: `tests/core/test_setup_clients.py::test_setup_replaces_an_earlier_copy_of_its_own_skill_and_says_so` | — |
| L-O5 | The docs say to start the meta-harness outside a repository you review | implemented and verified | README and `docs/AI_FOR_AI_WORKFLOW.md`. `24917ea` first said to git-ignore `.g6/`; the fresh review showed that makes G6 judge its own `main.py` as an ignored file that could replace a tracked module, so the advice changed. Test: `tests/site/test_launch_copy.py::test_the_workflow_docs_say_to_keep_workflows_out_of_a_reviewed_repository` | — |
| L-O6 | The skill starts a review from the folder, the task and the requirements, adds optional inputs only in their documented shapes, and after a no asks the person | implemented, not verified | `24917ea`, `src/g6/skills/using-g6/SKILL.md`, within 1,000 words. Tests: `tests/skills/test_using_g6.py` (10). Not verified: the skill's agent tests were not re-run (section 5.9) | — |
| L-O7 | A fresh review of `24917ea` | implemented and verified | No Critical. Two Important, fixed with tests that failed first: "which decide nothing" was false for a structural instance or a counterexample that decides the outcome (now "which never pass a requirement", structural results in words, no "(above)"); and the `.gitignore` advice (row L-O5). The report is not in the repository; that fix pass has no fresh review of its own | — |
| L-OM | The review's minor findings: a cancel from a client that shows no form leaves the person no route but the docs; `--force` on a linked skill folder stops setup half-way, and replacing deletes a person's own edits with no backup; an in-place upgrade whose registration is refused keeps the old skill; the skill's trims lost "each reproduction's text included", the meaning of "real interface", and the parameter name `repo` | deferred | Graded Minor by their effect on a founder using the launch | after launch |

### 5.4 L1: an environment linked to G6's own Python

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-L1 | On Linux, a project environment linked to the same Python as G6's own made almost every review fail with `G6_E_ISOLATION_UNAVAILABLE`. It is now refused up front, before anything runs, and the install copy makes G6's own environment with `python -m venv --copies` | implemented and verified | `2680cc9` (the refusal and the install copy in the README, Get started, the docs index, both guides, the workflow guide and the release notes), `18ae192` (the home page's quick start). Tests: `tests/harness/test_patch_review_security.py::test_an_environment_linked_to_the_same_python_as_g6s_is_refused_before_anything_runs`, the confined positive control (a `--copies` venv reviews and reads `supported_within_scope`), `tests/site/test_launch_copy.py::test_every_install_makes_g6s_own_environment_with_copies_first_and_says_why` and `::test_the_home_pages_quick_start_first_makes_g6s_own_environment_and_links_get_started`. `confine.py` is not edited; its real fix waits for the issuance window (row L-D6). The refusal wording is marked "PENDING copy" in the commit message, and no record shows the owner approved it (section 5.9) | — |

### 5.5 The continuous-integration fixes

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-CI1 | The install message: the study caller said `pip install 'g6[anthropic]'`, which would fetch a package of that name from the index | implemented and verified | `1b32138` (run 40810994710 at `28a2c3b` failed all six test jobs on `tests/tools/test_install_messages.py::test_every_install_command_names_the_repository`). Red then green with that file in its own process | — |
| L-CI2 | The scan waiver: the scan job flagged the fixture of `tests/test_no_local_paths.py` as a user path | implemented and verified | `76441f5` pins a `SCAN_EXCEPTIONS` entry to the file's SHA-256; `3b963c4` fixes its own comment, which quoted a path. The scan of a clean export went from 1 finding to 0, and a path planted in another file is still flagged. `tests/tools/test_export_public.py` | — |
| L-CI3 | The failures of run 40780356106: the workforce skill over its word cap (1,829 against 1,800), and Windows link copies. Setup's registration tests on Linux | implemented and verified | `362ac00` (1,796 words), `ab7be35` (item 12, links made with the system's separator), `8426ae3` (setup's tests replace `STEPS` too). Each is reported red then green. No run has confirmed these on the later commits | — |

### 5.6 The copy

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-P1 | The home page's quick start: the headline "Now you can make AI agents check their own work", the subheading with its limit sentence, and the two-command start preceded by the line that makes G6's own environment | implemented and verified | `d2eb51b`, `28a2c3b`, `18ae192`. Tests: `tests/site/test_home_page.py`, `tests/site/test_launch_copy.py` (the install line, no bare `claude mcp add`, no `.[mcp]` in a first journey, the 65-word cap) | — |
| L-P2 | The `state_invariant.v1` request shows what its binding takes | implemented and verified | `d7c285c`, from the VS-5 smoke, where every such instance was refused. Tests: `tests/harness/test_verification_proposer.py::test_the_state_invariant_request_shows_the_bindings_parts_and_an_example_that_fits` and two more. Not verified: whether a model now fills the binding correctly; the smoke was not rerun (no spend) | — |
| L-P3 | "G6 maintaining G6, in public": a home page section beside the worked example, the FAQ's "Does G6 now improve itself automatically?" (still "No" for the meta-harness) and a README section with an `aspirational` row. The loop's design is brought into the line | implemented and verified | `0ce616e`. The copy says the loop is being built, a maintainer reviews and merges every change, everything is public, what it shows is reported including a null result, and it works only on G6. Design: `docs/superpowers/specs/2026-10-29-self-improvement-loop-design.md`, with the owner's answers O1 (subscriptions used headless, never exposed to outside users), O2 (no second Windows account, so each study report states the same-user limit) and O3 (a GitHub App and rulesets). Tests: `tests/site/test_loop_in_public.py` (three tests: the page, the FAQ and README with the link, the design's presence) | — |
| L-P4 | The README, the release notes, Get started and the FAQ, Security and Developers pages: the whole first journey for both clients, the repair loop, and the status line word for word | implemented and verified | `3a37d1e`, `0f62916`, `a2b82de`, `28a2c3b`. Tests: `tests/site/test_launch_copy.py` (the status line on every page and file that ships, the journey in order, no claim of more defects found or time saved, the release notes' parts) | — |
| L-P5 | The worked examples' record, `validation/worked_examples/`: ten recent real bugs, round 1 with no model of G6's own and round 2 with G6's own model on, labelled worked examples, not a study | implemented and verified | `9ad134d` (round 1: G6 reproduced no defect, six of ten patches failed the maintainers' tests, three integrity alarms, all false) and `677ac2c` (round 2: 51 proposals, none a counterexample, five of ten failed, one false alarm, $13.48 for both rounds). The plan was fixed before the first run. Tests: `tests/validation/test_worked_examples.py` (the scripts, with fakes only). The full trails stay in the local archive | — |
| L-P6 | "What happened when we tried it" on the home page and in the README: both rounds in plain words, and that G6 is not yet a defect finder | implemented and verified | `6d24127`, `8df9d74`. Tests: `tests/site/test_tried_it.py` (the statements, the link, the worked-example label, never "experimental", "verified" or "proof of concept", and "under way" gone) | — |

### 5.7 The release gates

| # | Gate | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-G1 | The VS-4 review's integrity fix, with a fresh review of it | implemented, not verified | The fix is `d58e5d1` and `a95525d`: the changed files never come from the repository's index or its replace refs, and the snapshot binds a changed file's bytes whatever the index says. Both are ancestors of `b5c5705`, the commit half A reviewed. Unverified: no commit message or document names a review of these two commits on their own, so it is not shown that the fresh review looked at them | go-live security gate |
| L-G2a | The deep security review, half A: no new escape probes | implemented, not verified | Rows L-A to L-M2 | go-live security gate |
| L-G2b | The deep security review, half B | implemented, not verified | Rows L-B to L-B8 | go-live security gate |
| L-G2c | The deep security review, W1 to W5 | implemented, not verified | Rows L-W6 and L-F1 to L-F8 | go-live security gate |
| L-G2d | The fresh reviews of the fix passes: of F1 to F8 (rows L-R1 to L-R5), and of `83de848` and `0c55c8b` (row L-R6, its findings fixed in rows L-R7 and L-R8) | implemented, not verified | Rows L-R1 to L-R8. Neither report is in the repository. The last fix pass (L-R7, L-R8) is verified by its tests and has no fresh review of its own | go-live security gate |
| L-G3 | Continuous integration green on the release commit (a red macOS job is acceptable) | implemented and verified | Run 40944507516 on `0c44e6d` passed all nine jobs: the scan, conformance, the site, and the tests on Linux, Windows and macOS under Python 3.12 and 3.13. On its first attempt the Windows 3.12 job failed on two timing guards only (`tests/jobs/test_care_crisis.py::test_the_scans_work_is_bounded`, at 1.246 s, and `tests/knowledge/test_converter.py::test_review_stray_end_tags_under_many_open_elements_convert_in_linear_time`). `0c44e6d` touches neither, both passed in run 40915484031, and the re-run of that job passed. Run 40915484031 on `133d099` passed every test job and failed the scan on six findings in the worked examples' files: a fake key, and the home folder of the container's user five times. `0c44e6d` fixed them without a waiver. Earlier: 40780356106 and 40810994710 at `28a2c3b` (rows L-CI1, L-CI2). Not covered: a commit after `0c44e6d` (this row's own update, the owner's pilot record) needs its own run before the tag | — |
| L-G4 | The owner's pilot in Claude Code and in Codex: setup, one real review, allow and decline in the dialog | implemented and verified | The owner, 30 October 2026, on `f08bc96` (the code of `0c44e6d`): setup for both clients, one confined review in each (supported within scope, 12.2 and 6.6 seconds), and the form answered no (Claude Code: declined; Codex has no Decline, so Esc), cancel and yes, each recorded as expected. The record and its ten findings: `docs/releases/OWNER_PILOT.md`; the fixes: section 5.3d. Not shown: unfamiliar people, and the fixes in a second pilot | — |
| L-G5 | Honest copy: the status line, never "verified" as a claim, no claim that G6 catches more defects or saves time | implemented, not verified | Pinned by `tests/site/test_launch_copy.py::test_each_page_that_ships_carries_the_status_line_word_for_word`, `::test_the_launch_copy_claims_no_more_defects_found_and_no_time_saved` and `tests/site/test_loop_in_public.py`, which also checks the section never says "experimental" or "verified". Not verified: `tests/site/test_honesty.py` still does not exist (row E34); the word "experimental" still appears in three blog essays (`web/templates/blog/context_window_is_not_memory.html`, `epistemology_of_ai.html`, `two_types_of_thinking.html`; row E33), which no test reads; rows E25 to E32 were not re-read for this update | owner |
| L-G6 | The owner's steps: squash-merge PR #1, tag the release, deploy the site; set the version number | missing | The version is set: 0.2.0 (the owner, 2026-10-29), in `src/g6/__init__.py` and the title of `docs/releases/launch.md`; the tag is then `v0.2.0` (`tools/check_release.py`). The version is an input every certificate binds, so the five were renewed after it: an independent version-only attestation (`6f542d4`), then normal confined issuance with `--check` (`55ad65d`; only hashes changed, every position still cites `229873b`). The merge, the tag and the site remain pending | owner |

### 5.8 Deferred until after launch

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| L-D1 | VS-5's study and V10 (they continue in parallel) | deferred | Rows VS-5 and V10. Both studies are registered as version 2 and have not run (`docs/releases/launch.md`) | after launch |
| L-D2 | B1 and B2: premortem and mission over MCP, and the verification programme | deferred | The owner's decision. Not in the launch's code | after launch |
| L-D3 | C: the plugins for Claude Code and Codex, and the Stop hook | deferred | `docs/releases/launch.md`, "What does not ship" | after launch |
| L-D4 | E0: venture, the third work mode | deferred | `docs/releases/launch.md`, "What does not ship" | after launch |
| L-D5 | E6: the continuous-integration check that runs patch review on a pull request | deferred | `docs/releases/launch.md`, "What does not ship" | after launch |
| L-D6 | The confine `/tmp` change, and the real fix of the link-resolving comparison in `confine.interpreter_paths` | deferred | Both change `src/g6/core/confine.py`, which certificates bind: they need an independent review and the one certificate reissue (rows S2-19, L-L1). The commit `2680cc9` says the comparison gets its fix "in the post-launch issuance window, with the /tmp change" | after launch |
| L-D7 | Refining the ignored-file rule from module names anywhere to import roots | deferred | W3's finding (row L-W3). It comes after launch, with a fresh security review | after launch |
| L-D8 | Holding go-live for V10, the 8-of-10 fresh-user trial before posts, and venture in Step 5 | deferred | Superseded for this first launch by the owner's decision of 29 October 2026 (night); rows S5-5 and V10 keep their own status | after launch |
| L-D9 | The access grant printed one command per line, saying to run it in PowerShell or cmd (Claude Code's `!` runs Git Bash, where `/grant` fails) | deferred | The owner's pilot, finding 7. The text is in `src/g6/core/confine.py`, which certificates bind: it waits for the reissue window | after launch |
| L-D10 | `g6 doctor` says whether patch review is allowed | deferred | The owner's pilot, finding 8; plan A2 named it | after launch |
| L-D11 | Patch review leaves G6's own `.g6/` project folder out of the change and of its ignored-file judgement | deferred | The owner's pilot, finding 5, and the fresh review's second Important finding. It changes what the integrity scan reads, so it needs its own security review | after launch |

### 5.9 Known limits and residual risks

These are limits, not rows. None changes a status above.

- **Windows suites.** `tests/workforce/test_repo_tests_run.py` (33 failures) and `tests/workforce/test_receipt_channel.py`
  (5) fail on a PC without the AppContainer folder grant. They are refusals without a grant, not defects (as at
  `df55a78`). Unverified: these counts come from the owner's brief; the suites were not rerun, and no commit message
  gives the counts.
- **Environments refused.** A `.venv` linked outside the repository, and a Windows Store Python environment, are now
  refused (L1; `G6_E_REPO_PYTHON_INVALID`). The commit message of `2680cc9` states the Linux case and the
  environments linked to G6's own Python; the Windows Store case is from the owner's brief and is not in the
  history.
- **B2's cost.** A correct patch whose proposed check errors only on the patch reads `insufficient_evidence`
  (`check_error_on_patch`; `7ae5155`). The same kind of cost is accepted for a patch that updates a snapshot (`1e21764`).
- **Component-skill examples.** Three fail on Windows for environment reasons (`adapt_healing`, `cegis`,
  `distillation`); in the `g6-a2-linux` image, `cegis` fails because G6 is installed there through `PYTHONPATH` and
  its child runs with `-I`. Unverified: from the owner's brief; not in the history.
- **The ignored-file rule** names files by module name anywhere (W3's finding), which errs toward an alarm: a data
  fixture called `a.py` can raise test integrity in doubt. Refining it comes after launch.
- **`core/promotion_custody.py`** starts a `python -m` child. It is certificate-bound and unreachable in this release
  (its service module is not in this G6, so `custody_configured` raises before any spawn); it waits for the issuance
  window.
- **The meta-harness's credit and report** (`metaharness/level2.py`'s credit and the `report` call) still read every
  version the project's store holds, including ones G6 did not record (row L-R2). They decide nothing that runs or
  rolls back; the fix comes after launch.
- **The worked examples' limits for G6**: a package that writes a git-ignored file when it is installed (black's
  `_black_version.py`) defeats every proposed check in G6's copy; G6's own Anthropic caller records no token counts;
  `state_invariant.v1` instances from the model were always refused (row L-P5).
- **macOS**: the test suite passes in continuous integration (row L-G3), but no person has used G6 there, and patch
  review refuses to run there.
- **The patch's own code runs in pytest's own process** in the repository's environment, which the agent can change
  (I5). A patch written to defeat the checks can make them report a pass, and G6 does not catch that.
- **Local settings** assume a trusted host: an agent with the account's unrestricted shell can change them
  (`docs/releases/launch.md`).
- **The skill's agent tests** (`docs/skills/using-g6/TESTING.md`) were not run, because they need model calls. The
  owner's pilot used the skill in both clients before its fixes; the fixed skill has not been used by an agent.
- **Wording not yet approved.** The source marks 16 user-facing texts "PENDING copy" (11 in
  `src/g6/workforce/repo_tests.py`, 4 in `src/g6/harness/patch_review.py`, 1 in `src/g6/workforce/bench.py`). No
  record in the history shows the owner has reviewed them.
- **Earlier rows.** Rows whose evidence cites `claude/vs` or `claude/pool-compat` as "not yet on the line of record"
  (S2-16, S2-18) now have their code on the launch line (merge `c8a15c9`); their statuses were not changed, because
  no continuous-integration run covers them there.

## Could not be determined

- **Rankings:** whether `g6 find` still ranks an unrelated component second for a claim check (P0-10), and the
  ranking of the queries in P0-24 and P0-25. They need a run, and none was made for this audit.
- **Windows boundary runs:** the AppContainer runs on Windows are recorded only as local results (row E15), and
  they were not rerun.
- **Docker runs:** the bubblewrap runs in Docker that are recorded for Step 2 were not rerun. The rows cite the
  Linux jobs of run 40654008114 instead.
- **Old docs problems:** the plain-language no-model text in the apps, and the dead link, the paths and the
  disagreeing counts that Phase 0 found in the docs, were not checked one by one.
