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

## Counts

| Table | implemented and verified | implemented, not verified | missing | deferred | Rows |
|---|---|---|---|---|---|
| 1. Earlier plan items | 37 | 14 | 27 | 12 | 90 |
| 2. Phase 0 findings | 6 | 2 | 20 | 1 | 29 |
| 3. Revision 3 steps and amendments | 35 | 7 | 16 | 8 | 66 |
| 4. Verification search | 3 | 2 | 5 | 0 | 10 |

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
| P0-06 | The version reads as a development version though v0.1.0 exists | implemented and verified | `src/g6/__init__.py:12` is `0.1.1`. Test: `tests/tools/test_release_guard.py::test_wrong_or_development_version_cannot_be_released`. The published v0.1.0 apps keep their old label | — |
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
whether it preserved what it must not break, and whether it is tested enough. Go-live waits for its study, V10.

| # | Item | Status | Evidence | Owned by |
|---|---|---|---|---|
| VS-pre | The existing parts the workstream builds on are present | implemented, not verified | `IntentContract` (`src/g6/intent_engine/schema.py:105`), `IntentElicitor` (`elicitor.py:42`), `detect_test_weakening` (`src/g6/safety_engine/weakening.py:142`), `check_invariant` and `prove_invariant` (`src/g6/model_checking/symbolic.py:202-212`), `check_contract` (`src/g6/formal/contract.py:22`), `check_equivalence` (`src/g6/formal/z3_depth.py:541`), `Z3Verifier` (`src/g6/cegis/z3_verifier.py:201`) and `layer_violations` (`src/g6/code/design_governance/graph.py:259`). Their fitness for this use is untested | — |
| VS-0 | Register the V10 protocol before any verification-search code | implemented and verified | `cd52d48` on `claude/vs` adds `docs/validation/patch_review_search/PROTOCOL.md` and its registry entry; the file's hash with Unix line endings (`2573c84e…`) matches the entry, and no verification-search code exists before it. `tests/site/test_evidence.py` passes against it. Not yet on `codex/ai-for-ai-continuation` | — |
| VS-1 | Intent modelling: requirements from an intent contract, the provenance of every requirement, and an optional `intent` parameter | implemented and verified | `43f0234` on `claude/vs`. `requirements_from_intent` and the `intent` parameter of `review_patch` and `g6_review_patch`. Tests: `tests/harness/test_verification_search.py`, and the identity test `tests/harness/test_patch_review_off_path.py` with its golden file: with no `intent`, every decided field is unchanged. Continuous integration: run 40768957226 on `051b062`, a descendant, passed all nine jobs. Not yet on `codex/ai-for-ai-continuation` | — |
| VS-2 | The executor, the classifier and the completion report for checks the caller supplies | implemented and verified | `bb218ce` and `ae11694`, then the fix pass after its review, `d8f06c6` and `051b062`, on `claude/vs`. The optional `proposed_checks` run confined on the patched tree and at the base commit, are classified by their declared intent, never pass a requirement, and give at most `insufficient_evidence`; every reply has `assessment.completion`. Tests: `tests/harness/test_proposed_checks.py`. Continuous integration: run 40768957226 on `051b062` passed all nine jobs. Not yet on `codex/ai-for-ai-continuation` | — |
| VS-3 | The template registry and compiler, five templates, and test adequacy through mutation | implemented, not verified | `51ba696`, `43a85ee` and `633f61b` on `claude/vs` (with the pool-compatibility merge `c8a15c9`). `src/g6/harness/verification_templates.py`, the `template_instances` and `search` parameters, and test adequacy reported only. Tests: `tests/harness/test_verification_templates.py`. So far only local Docker runs with bubblewrap. Its independent review and its continuous-integration run are pending | VS-3 |
| VS-4 | The formal path, with a three-part report: specification, model, correspondence | missing | Not started | VS-4 |
| VS-5 | The opt-in proposer through the person's own model, with injection fixtures and one small model smoke on development fixtures | missing | Not started | VS-5 |
| VS-6 | The ledgers, adoption detection, `g6_confirm_behaviour`, and worked examples from three kinds of application | missing | No `g6_confirm_behaviour` tool | VS-6 |
| VS-7 | Freeze the workstream's commit, then run V10 together with V9 | missing | Waits for VS-0 to VS-6 | VS-7 |
| V10 | The V10 feasibility report, which go-live needs | missing | Not started | VS-7 |

## Could not be determined

- **Rankings:** whether `g6 find` still ranks an unrelated component second for a claim check (P0-10), and the
  ranking of the queries in P0-24 and P0-25. They need a run, and none was made for this audit.
- **Windows boundary runs:** the AppContainer runs on Windows are recorded only as local results (row E15), and
  they were not rerun.
- **Docker runs:** the bubblewrap runs in Docker that are recorded for Step 2 were not rerun. The rows cite the
  Linux jobs of run 40654008114 instead.
- **Old docs problems:** the plain-language no-model text in the apps, and the dead link, the paths and the
  disagreeing counts that Phase 0 found in the docs, were not checked one by one.
