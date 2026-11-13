# Section 13 implementation report

Branch: `claude/allowance`. Base: `ea3f96c016e5c318e50566c579eff073617e039f`.
Frozen source read with `git show`, from design commit `12bfd76b37f9267ec779b3712c9658c563a96bd4`:
`docs/superpowers/specs/2026-11-06-explicit-models-one-question-design.md`, section 13 in full, appendix C 17–19,
section 16, section 14.1 and the cited parts of 3.5 and 5. No design text was substituted from this worktree.
The capability and skill routing are committed together; the completion message prints the commit id.

## What was built

The public facade is `src/g6/allowance.py`; I/O and policy helpers live in the private infrastructure package
`src/g6/_allowance/`. The MCP tool is the agent surface; this introduces no standalone component op.

| Frozen text | Implementation |
|---|---|
| 13.1, reuse the raw Codex read | `store.refresh_codex` calls only existing `self_maintenance.usage.codex_limits`. `usage.py` and `pacing.py` are byte-for-byte unchanged from the base. No new weekly meter or ledger reuse. |
| 13.2, lossless normalisation | `Reading` and `Window` retain provider/profile/window identities, duration, used share, reset, declared semantics, actual observation time, separate last-good/attempt times, source version, availability, window failures, quota-block state and ordinary-use permission. Extra/malformed windows remain visible; foreign labels become stable digests. Missing is unknown. |
| 13.2, Claude observer | Owner-installed wrapper isolates observation in a process with a 0.75s budget and a 1 MiB parsing cap. It forwards original bytes once, with original shell stdout, stderr and exit behavior. Renderer failure and observer failure are checked separately. Native CMD and PowerShell quoting are exercised through harmless real renderers. |
| 13.2, installation/rollback | Pure preview produces exact one-line JSON settings and the separate original configuration. Rollback restores the saved status-line property while preserving concurrent unrelated settings. Supported forms and shell choices are explicit. No person's settings were read or edited. No G6 CLI command was added. |
| 13.2, durable cache | SQLite transactions order actual observations, merge by identity, retain per-window failures and last-good time, fence late writers after profile changes, and lease background Codex refreshes. Capture publication uses atomic replacement under a second writer transaction after the observation commits, with profile/time rechecks. Capture contains only allow-listed `rate_limits` and `read_at`. |
| 13.2, pure pacing | Fixed weekly target f, ceiling min(1,7f/5), reserve cap before 6/7 elapsed. Short windows constrain capacity and get no weekly pace. All-window freshness, ordinary-use permission, owner caps, reserve, useful work, support and continuity precede any increase. Unknown/stale data recommends zero additional optional agents. |
| 13.2, advice/display | One additive MCP tool, `g6_allowances`, returns both providers and every window's age, headroom, blocking state, pace and reason. No launch enforcement, automatic transfer or disclosure grant. Tool inventory and both developer-document copies now count 32 tools. |
| 13.2 delivery and 14.1 | Claude plugin emits one context line only on recommendation changes, reading cached state only. Shipped `src/g6/skills/using-g6/SKILL.md` and root `AGENTS.md` route both agents before optional fan-out. The stricter existing 1,000-word skill cap passes. |
| 13.3 | Offline deterministic fixtures and failure tests; all provider invocations are faked. Claude fixtures are explicitly synthetic. The real-capture acceptance test skips at the named path until a real sanitised capture is checked. No usage-benefit claim. |
| 13.4 and 16 | Installation and optional control remain the person's decisions. The module docstring states allowance readings carry no person's data. No model requests, source grants, credential reads or real provider executables were used. GUI and TUI/CLI code were not changed. |

Defaults: reserve 0.2; release at 6/7 of a fixed week; maximum age 120s inclusive; refresh 60s; near-full 0.95 inclusive;
pace tolerance 0.05 inclusive; owner parallelism cap 2. Policy ranges and supported installation forms are in
[allowances.md](../../allowances.md). Production adapters do not establish sign-in continuity, so production cannot
suggest increased optional use. The pure policy tests both the positive established-binding case and its refusal boundaries.

## Tests and first observed failures

Every retained new test case below was written before its implementation or repair. One native PowerShell test initially
used the wrong exit oracle; its correction is recorded explicitly below. The real-capture test is skipped, never treated as
support. Existing tool-list, plugin-hook and layer-inventory checks were also observed failing for the new capability:
missing `g6_allowances`, missing second hook, and missing `g6.allowance` from the package-layer inventory, respectively.
They now pass their new assertions. Other failures in the same files match the base.

| New file | First run | Final run |
|---|---|---|
| `test_readings.py` | 27 failures: package absent; later window-loss regressions failed before repairs | 30 passed |
| `test_advice.py` | 34 failures: package absent; two future-start boundary cases failed before the final guard | 36 passed |
| `test_store.py` | 6 failures: package absent; five partial/race/tie/durability regressions failed before repairs | 11 passed |
| `test_statusline.py` | 10 failures: package absent; native-shell comparison exposed CMD quoting and the PowerShell oracle issue | 12 passed, 1 capture test skipped |
| `test_surface.py` | 4 failures: missing tool/package/skill route | 4 passed |

## Before and after tables

Coverage: all executable test files selected by the supplied `cfu_importers.py` for changed modules, plus the requested
MCP tool-list, door/envelope parity, existing pacing/usage and four hook/document consumers found from the code.
The finder’s non-test `conformance/kit.py` is exercised through `test_conformance.py`. `tests/contract/` does not exist
at the base. No whole-suite invocation was used. Each file ran in its own process, with the supplied interpreter,
`-q -p no:cacheprovider -p sandbox_tmp`, a unique worktree basetemp, worktree TMP/TEMP/G6_HOME and PYTHONPATH including
`.build`. The supplied kernel32 helper checked free memory before every run; the runner waits below 2.5 GB.

Before: 149 files; 7100 passed, 1134 failed, 190 skipped, 1 xfailed. After: 154 files; 7193 passed, 1134 failed, 191 skipped, 1 xfailed.
All 149 existing-file counts and failing node sets match before/after. The five added files total 93 passed and one
real-capture skip. File-level exit 5 on the optional-agent modules means all cases skipped. Rows marked sandbox-limited
contain `WinError 5`; their error is `PermissionError: [WinError 5] Access is denied`. Other matching failures are
reported as pre-existing without assuming their cause. The tables are limited to this sandbox run.

| Test file | Before | After | Note |
|---|---|---|---|
| `tests/abstraction/test_abstraction_server_files.py` | 7 passed | 7 passed |  |
| `tests/abstraction/test_abstraction_without_extra.py` | 2 passed | 2 passed |  |
| `tests/action_gating/test_block_refusal_codes.py` | 4 passed, 2 failed | 4 passed, 2 failed | pre-existing failures |
| `tests/action_gating/test_entrypoint_consistency.py` | 5 failed | 5 failed | pre-existing failures |
| `tests/action_gating/test_merge_operation_budget.py` | 18 passed | 18 passed |  |
| `tests/action_gating/test_merge_review_scope.py` | 30 passed | 30 passed |  |
| `tests/action_gating/test_merge_safeguards.py` | 10 passed | 10 passed |  |
| `tests/action_gating/test_review_b1_gate_floors.py` | 34 passed, 2 failed | 34 passed, 2 failed | pre-existing failures |
| `tests/action_logic/test_block_refusal_codes.py` | 10 passed | 10 passed |  |
| `tests/agents/test_agent_autogen.py` | 1 skipped | 1 skipped |  |
| `tests/agents/test_agent_claude.py` | 21 passed, 15 failed | 21 passed, 15 failed | pre-existing failures |
| `tests/agents/test_agent_codex.py` | 22 passed, 15 failed | 22 passed, 15 failed | pre-existing failures |
| `tests/agents/test_agent_langgraph.py` | 1 skipped | 1 skipped |  |
| `tests/agents/test_agent_nanoclaw.py` | 17 passed | 17 passed |  |
| `tests/agents/test_agent_runtime.py` | 57 passed, 3 failed | 57 passed, 3 failed | pre-existing failures |
| `tests/agents/test_agent_smolagents.py` | 1 skipped | 1 skipped |  |
| `tests/agents/test_agent_smolagents_isolation.py` | 7 passed, 1 failed | 7 passed, 1 failed | sandbox-limited: WinError 5 |
| `tests/algebra/test_algebra_block.py` | 44 passed, 14 failed | 44 passed, 14 failed | pre-existing failures |
| `tests/algebra/test_ops.py` | 30 passed, 7 failed | 30 passed, 7 failed | sandbox-limited: WinError 5 |
| `tests/algorithm_design/test_algorithm_design_block.py` | 45 passed | 45 passed |  |
| `tests/allowance/test_advice.py` | not present | 36 passed | new |
| `tests/allowance/test_readings.py` | not present | 30 passed | new |
| `tests/allowance/test_statusline.py` | not present | 12 passed, 1 skipped | new |
| `tests/allowance/test_store.py` | not present | 11 passed | new |
| `tests/allowance/test_surface.py` | not present | 4 passed | new |
| `tests/causal/test_causal_block.py` | 32 passed, 2 skipped | 32 passed, 2 skipped |  |
| `tests/code/test_adapt_healing.py` | 10 passed, 10 failed | 10 passed, 10 failed | sandbox-limited: WinError 5 |
| `tests/code/test_adapt_webpage.py` | 13 passed, 7 failed, 2 skipped | 13 passed, 7 failed, 2 skipped | pre-existing failures |
| `tests/code/test_build.py` | 61 passed, 3 failed | 61 passed, 3 failed | pre-existing failures |
| `tests/code/test_component_creator.py` | 25 passed | 25 passed |  |
| `tests/code/test_design_governance.py` | 14 passed | 14 passed |  |
| `tests/code/test_meta_programming.py` | 22 passed, 2 failed | 22 passed, 2 failed | sandbox-limited: WinError 5 |
| `tests/code/test_review_fixes.py` | 49 passed, 11 failed, 2 skipped | 49 passed, 11 failed, 2 skipped | sandbox-limited: WinError 5 |
| `tests/code/test_swe_diagnostics.py` | 14 passed, 2 failed | 14 passed, 2 failed | pre-existing failures |
| `tests/coding_agent_gate/test_audit_004_regressions.py` | 16 passed | 16 passed |  |
| `tests/coding_agent_gate/test_block_refusal_codes.py` | 2 passed | 2 passed |  |
| `tests/coding_agent_gate/test_merge_protected_paths.py` | 13 passed | 13 passed |  |
| `tests/coding_agent_gate/test_receipt_evidence.py` | 17 passed | 17 passed |  |
| `tests/cogarch/test_cog_arch_actr.py` | 30 passed | 30 passed |  |
| `tests/cogarch/test_cog_arch_aixi.py` | 29 passed | 29 passed |  |
| `tests/cogarch/test_cog_arch_gps.py` | 30 passed, 3 failed | 30 passed, 3 failed | sandbox-limited: WinError 5 |
| `tests/cogarch/test_decision_making.py` | 28 passed | 28 passed |  |
| `tests/complexity/test_complexity_block.py` | 26 passed | 26 passed |  |
| `tests/conformance/test_conformance.py` | 2064 passed, 111 failed, 5 skipped | 2064 passed, 111 failed, 5 skipped | sandbox-limited: WinError 5 |
| `tests/core/test_component_skills.py` | 950 passed, 19 failed, 1 skipped | 950 passed, 19 failed, 1 skipped | sandbox-limited: WinError 5 |
| `tests/core/test_compose_mcp.py` | 5 passed, 1 failed | 5 passed, 1 failed | pre-existing failures |
| `tests/core/test_describe_needs.py` | 4 passed | 4 passed |  |
| `tests/core/test_discovery_cli_mcp.py` | 13 passed, 1 failed | 13 passed, 1 failed | pre-existing failures |
| `tests/core/test_door_promotion.py` | 56 passed | 56 passed |  |
| `tests/core/test_families.py` | 15 passed | 15 passed |  |
| `tests/core/test_invoke_parity.py` | 15 passed | 15 passed |  |
| `tests/core/test_mcp_improve.py` | 94 passed | 94 passed |  |
| `tests/core/test_mcp_invoke_envelope.py` | 16 passed, 1 failed | 16 passed, 1 failed | pre-existing failures |
| `tests/core/test_mcp_mission.py` | 12 passed, 41 failed, 9 skipped | 12 passed, 41 failed, 9 skipped | pre-existing failures |
| `tests/core/test_mcp_model_door.py` | 16 passed | 16 passed |  |
| `tests/core/test_mcp_offline_start.py` | 3 passed, 1 failed | 3 passed, 1 failed | pre-existing failures |
| `tests/core/test_mcp_registered_command.py` | 23 passed, 1 failed | 23 passed, 1 failed | pre-existing failures |
| `tests/core/test_mcp_review_loop.py` | 9 passed, 30 failed | 9 passed, 30 failed | pre-existing failures |
| `tests/core/test_mcp_review_reply.py` | 7 passed | 7 passed |  |
| `tests/core/test_mcp_review_reply_integrity.py` | 1 passed | 1 passed |  |
| `tests/core/test_mcp_sensitive_opt_in.py` | 7 passed | 7 passed |  |
| `tests/core/test_outside_door.py` | 4 passed | 4 passed |  |
| `tests/core/test_setup_clients.py` | 7 passed | 7 passed |  |
| `tests/core/test_skills.py` | 12 passed | 12 passed |  |
| `tests/core/test_unconfined_warning.py` | 11 passed, 1 failed | 11 passed, 1 failed | sandbox-limited: WinError 5 |
| `tests/core/test_vetting.py` | 24 passed | 24 passed |  |
| `tests/datalog/test_datalog_block.py` | 31 passed | 31 passed |  |
| `tests/embodied/test_adapt_physical_ai.py` | 23 passed | 23 passed |  |
| `tests/embodied/test_embodied_merges.py` | 11 passed | 11 passed |  |
| `tests/embodied/test_motion_grasp.py` | 26 passed | 26 passed |  |
| `tests/epistemic_integrity/test_block_refusal_codes.py` | 2 passed | 2 passed |  |
| `tests/epistemic_integrity/test_evidence_keys_refused.py` | 12 passed | 12 passed |  |
| `tests/formal/test_formal_block.py` | 55 passed | 55 passed |  |
| `tests/goal_engine/test_goal_engine_block.py` | 8 passed | 8 passed |  |
| `tests/graph_reasoning/test_graph_reasoning_block.py` | 26 passed | 26 passed |  |
| `tests/grounding/test_block_refusal_codes.py` | 10 passed | 10 passed |  |
| `tests/grounding/test_coverage.py` | 12 passed | 12 passed |  |
| `tests/grounding/test_verbatim_inside_a_denial.py` | 3 passed | 3 passed |  |
| `tests/harness/test_completion_hook.py` | 28 passed, 26 failed | 28 passed, 26 failed | sandbox-limited: WinError 5 |
| `tests/harness/test_completion_loop_followup.py` | 8 passed, 18 failed | 8 passed, 18 failed | sandbox-limited: WinError 5 |
| `tests/harness/test_patch_report.py` | 40 passed, 6 failed, 5 skipped | 40 passed, 6 failed, 5 skipped | pre-existing failures |
| `tests/harness/test_patch_review.py` | 24 passed, 113 failed, 13 skipped, 1 xfailed | 24 passed, 113 failed, 13 skipped, 1 xfailed | sandbox-limited: WinError 5 |
| `tests/harness/test_patch_review_imports.py` | 1 passed, 20 failed, 6 skipped | 1 passed, 20 failed, 6 skipped | sandbox-limited: WinError 5 |
| `tests/harness/test_proposed_checks.py` | 62 passed, 21 failed, 8 skipped | 62 passed, 21 failed, 8 skipped | sandbox-limited: WinError 5 |
| `tests/harness/test_verification_formal.py` | 27 passed, 37 failed, 3 skipped | 27 passed, 37 failed, 3 skipped | pre-existing failures |
| `tests/harness/test_verification_proposer.py` | 4 passed, 31 failed, 2 skipped | 4 passed, 31 failed, 2 skipped | pre-existing failures |
| `tests/harness/test_verification_templates.py` | 87 passed, 93 failed, 11 skipped | 87 passed, 93 failed, 11 skipped | sandbox-limited: WinError 5 |
| `tests/integrations/test_adapt_rq.py` | 74 passed | 74 passed |  |
| `tests/integrations/test_database_surface.py` | 16 passed, 2 skipped | 16 passed, 2 skipped |  |
| `tests/integrations/test_integrations2_surface.py` | 2 passed | 2 passed |  |
| `tests/jobs/test_care_guards.py` | 33 passed | 33 passed |  |
| `tests/jobs/test_duty_of_care_block.py` | 119 passed, 1 skipped | 119 passed, 1 skipped |  |
| `tests/knowledge/test_chunker.py` | 23 passed | 23 passed |  |
| `tests/knowledge/test_colbert.py` | 22 passed, 2 skipped | 22 passed, 2 skipped |  |
| `tests/knowledge/test_contexthub.py` | 31 passed | 31 passed |  |
| `tests/knowledge/test_converter.py` | 30 passed, 10 failed | 30 passed, 10 failed | sandbox-limited: WinError 5 |
| `tests/knowledge/test_elastic.py` | 32 passed | 32 passed |  |
| `tests/knowledge/test_fetcher.py` | 65 passed, 1 failed | 65 passed, 1 failed | pre-existing failures |
| `tests/knowledge/test_indexer.py` | 36 passed | 36 passed |  |
| `tests/knowledge/test_manifest.py` | 20 passed, 1 failed | 20 passed, 1 failed | pre-existing failures |
| `tests/knowledge/test_search.py` | 50 passed | 50 passed |  |
| `tests/lean/test_lean_block.py` | 28 passed | 28 passed |  |
| `tests/lean/test_search.py` | 34 passed, 2 failed | 34 passed, 2 failed | sandbox-limited: WinError 5 |
| `tests/llm_router/test_llm_router_block.py` | 61 passed, 2 failed | 61 passed, 2 failed | pre-existing failures |
| `tests/llm_router/test_llm_router_outcomes.py` | 24 passed | 24 passed |  |
| `tests/llm_router/test_llm_router_ready.py` | 103 passed | 103 passed |  |
| `tests/media/test_audio.py` | 15 passed, 26 failed | 15 passed, 26 failed | sandbox-limited: WinError 5 |
| `tests/media/test_blender.py` | 78 passed, 28 failed, 1 skipped | 78 passed, 28 failed, 1 skipped | sandbox-limited: WinError 5 |
| `tests/media/test_charts.py` | 29 passed, 104 failed | 29 passed, 104 failed | sandbox-limited: WinError 5 |
| `tests/media/test_comfyui.py` | 37 passed, 15 failed | 37 passed, 15 failed | sandbox-limited: WinError 5 |
| `tests/media/test_diagrams.py` | 61 passed, 17 failed, 1 skipped | 61 passed, 17 failed, 1 skipped | sandbox-limited: WinError 5 |
| `tests/media/test_ffmpeg.py` | 32 passed, 27 failed | 32 passed, 27 failed | sandbox-limited: WinError 5 |
| `tests/media/test_generative_art_block.py` | 22 passed, 12 failed | 22 passed, 12 failed | sandbox-limited: WinError 5 |
| `tests/media/test_image.py` | 41 passed, 26 failed | 41 passed, 26 failed | sandbox-limited: WinError 5 |
| `tests/media/test_synth.py` | 28 passed, 18 failed | 28 passed, 18 failed | sandbox-limited: WinError 5 |
| `tests/media/test_voice.py` | 35 passed, 5 failed, 1 skipped | 35 passed, 5 failed, 1 skipped | sandbox-limited: WinError 5 |
| `tests/memory/test_ctx_ace.py` | 55 passed, 2 failed | 55 passed, 2 failed | pre-existing failures |
| `tests/memory/test_ctx_cognee.py` | 54 passed | 54 passed |  |
| `tests/metaharness/test_forged_confirmation.py` | 3 passed, 7 failed | 3 passed, 7 failed | pre-existing failures |
| `tests/metaharness/test_loop_followup.py` | 48 passed, 11 failed, 1 skipped | 48 passed, 11 failed, 1 skipped | sandbox-limited: WinError 5 |
| `tests/metaharness/test_loop_runner.py` | 20 passed, 67 failed, 1 skipped | 20 passed, 67 failed, 1 skipped | pre-existing failures |
| `tests/metaharness/test_phase0_guardrails.py` | 26 passed | 26 passed |  |
| `tests/metaharness/test_phase6_mcp.py` | 2 passed, 9 failed | 2 passed, 9 failed | pre-existing failures |
| `tests/metaharness/test_review_repairs.py` | 30 passed, 4 failed | 30 passed, 4 failed | pre-existing failures |
| `tests/metaharness/test_uat_diagnostics.py` | 15 passed | 15 passed |  |
| `tests/ml/test_automl.py` | 26 passed, 5 skipped | 26 passed, 5 skipped |  |
| `tests/ml/test_bayesian.py` | 46 passed | 46 passed |  |
| `tests/ml/test_pandas.py` | 25 passed | 25 passed |  |
| `tests/ml/test_pytorch.py` | 34 passed, 1 failed, 13 skipped | 34 passed, 1 failed, 13 skipped | sandbox-limited: WinError 5 |
| `tests/ml/test_sklearn.py` | 29 passed, 1 failed, 11 skipped | 29 passed, 1 failed, 11 skipped | sandbox-limited: WinError 5 |
| `tests/model_checking/test_model_checking_block.py` | 42 passed | 42 passed |  |
| `tests/optimisation/test_nonlinear.py` | 28 passed | 28 passed |  |
| `tests/optimisation/test_optimisation_block.py` | 36 passed | 36 passed |  |
| `tests/prolog/test_ops.py` | 25 passed, 76 skipped | 25 passed, 76 skipped |  |
| `tests/prolog/test_prolog_block.py` | 41 passed | 41 passed |  |
| `tests/self_maintenance/test_envelope.py` | 94 passed | 94 passed |  |
| `tests/self_maintenance/test_pacing.py` | 75 passed | 75 passed |  |
| `tests/self_maintenance/test_self_maintenance_block.py` | 5 passed | 5 passed |  |
| `tests/self_maintenance/test_usage.py` | 162 passed, 7 failed | 162 passed, 7 failed | sandbox-limited: WinError 5 |
| `tests/site/test_get_started.py` | 11 passed | 11 passed |  |
| `tests/site/test_launch_copy.py` | 16 passed | 16 passed |  |
| `tests/skills/test_using_g6.py` | 10 passed | 10 passed |  |
| `tests/sources/test_australian_legal_corpus.py` | 66 passed | 66 passed |  |
| `tests/sources/test_ctx_geo.py` | 29 passed | 29 passed |  |
| `tests/sources/test_ctx_git.py` | 19 passed, 39 failed, 3 skipped | 19 passed, 39 failed, 3 skipped | sandbox-limited: WinError 5 |
| `tests/sources/test_ctx_langextract.py` | 22 passed, 4 failed | 22 passed, 4 failed | sandbox-limited: WinError 5 |
| `tests/sources/test_ctx_library_mapper.py` | 59 passed | 59 passed |  |
| `tests/sources/test_ctx_vision.py` | 15 passed, 11 failed | 15 passed, 11 failed | sandbox-limited: WinError 5 |
| `tests/tools/test_doc_examples.py` | 3 passed, 1 failed | 3 passed, 1 failed | pre-existing failures |
| `tests/tools/test_mcp_smoke.py` | 3 passed, 1 failed | 3 passed, 1 failed | pre-existing failures |
| `tests/workspace/test_align_prompt_library.py` | 25 passed | 25 passed |  |
| `tests/workspace/test_prompt_catalogue.py` | 6 passed, 1 failed | 6 passed, 1 failed | sandbox-limited: WinError 5 |
| `tests/workspace/test_shared_workspace.py` | 23 passed | 23 passed |  |
| `tests/workspace/test_workspace_manager.py` | 34 passed | 34 passed |  |

## Checks and review

- `uvx ruff check`: all 15 changed Python files pass, Ruff 0.17.3. It ran offline from the already installed distribution,
  using a local wheel and worktree-only tool/cache directories.
- `lint-imports --no-cache`: 878 files, 3,064 dependencies, three contracts kept, zero broken.
- Bound-file check: five certificate records scanned before editing; 112 referenced paths, zero changed intersections.
  Certificate files, `core/llm.py`, `core/host_models.py`, `self_maintenance/usage.py` and `self_maintenance/pacing.py` unchanged.
- Independent read-only review: three Important findings, all repaired after new failing regressions: extra-window loss,
  partial near-full updates, and profile invalidation versus late writers. Timestamp-tie and capture-publication tests
  were added during that repair pass. No Critical finding was reported. G6 MCP review was not called: the owner forbids it.
- The affected-file after run exposed unintended registration of the first helper-package layout as a component
  (four conformance failures and ten component-skill errors). The facade/private-package split corrected that layout
  without changing the bound registry. Both discovery/conformance files were rerun on the final layout.
- Final documentation-scanner recheck: 23 passed and the same one pre-existing failing test. That later scan also
  traversed generated `.build` test-copy trees containing copies of the base registrations; those scratch trees are
  excluded from the commit.
- No real `codex` or `claude` was started. No push, stash, rebase, checkout or settings installation was performed.

## Failure-class walk

| Class | Walk over the implementation and its limit |
|---|---|
| `identity_from_rendering` | Provider/window/profile are typed bindings, independent of a rendered label. Unknown foreign labels are digested. Profile switches invalidate rows and fence old writers; unobservable sign-in continuity still stays unknown. |
| `one_fact_two_rules` | Fractional pacing lives only in `advice.py`; the old loop is untouched. Partial cache updates retain stronger reported capacity without copying the owner's near-full threshold into storage. Separate observed, last-good and attempt times avoid treating a failed reset as a new complete reading. |
| `own_parser_for_foreign_format` | Codex uses its existing raw reader. Claude uses standard JSON and checked timestamps, accepting only known scalar fields for captures. Unsupported versions and unknown window semantics never inherit a pace. Native CMD shell text is passed under CMD's own quoting contract. |
| `failure_read_as_success` | Missing, malformed, expired, future, stale, blocked, unsupported and unbound states cannot request more optional work. Observer failure is isolated from rendering; renderer failure cannot suppress a valid observation. Capture absence remains an explicit support limitation. |
| `check_then_act_race` | Cache ordering, binding checks, refresh leases and note deduplication run under writer transactions. Capture publication rechecks profile and observation time. Advice is not launch admission and makes no reservation or exhaustion-prevention claim. |
| `state_not_durable` | SQLite persists observations, leases, profile bindings and note state across instances. The observation commits before atomic capture publication; an injected post-publication failure cannot roll it back. A failed/old writer cannot renew good time. Power-loss durability beyond the filesystem's guarantees is not established. |
| `untrusted_input_given_authority` | Raw fields cannot establish support, semantics, profile continuity, useful work or disclosure permission. Owner settings are separate. Capture output contains no raw account/session identifiers. Error text and foreign labels are not emitted as instructions. Useful work remains a caller assertion. |
| `boundary_case` | Tests cover percentage/duration/time validation, nearly full short windows, ordinaryUsageAllowed=false, freshness/reset boundaries, fractional reserve release, pace tolerance, invalid caps, missing producers, racing writers, timestamp ties, native quoting, renderer failure, observer timeout and rollback. |
| `guard_covers_less_than_it_guards` | The guards cover advisory recommendations and local cache/capture integrity. They do not authenticate provider accounts, reserve pending work, constrain external agent sessions, grant source disclosure or establish causal usage benefit. These limits are returned/documented. |

## Rulings

Ruling: `g6_allowances` is the additive MCP name — the owner waived the absent vocabulary file and this follows existing
snake_case g6 names — cost if wrong: a future naming alias or migration.

Ruling: only explicitly declared fixed weekly windows earn pacing — duration/reset alone do not establish semantics —
cost if wrong: less pacing advice until the provider contract is checked and declared.

Ruling: raw sign-in continuity remains unestablished — credentials cannot be read and the raw sources carry no established
account binding — cost if wrong: production remains conservative even with unused capacity; profile-token rotation still
invalidates old data.

Ruling: keep the existing 1,000-word skill cap and edit the shipped source skill — the tree's actual path and test are
stricter than the brief/design shorthand — cost if wrong: less detail in routing, with detail in component documentation.

Ruling: optional launch control is not enabled — there is no owner-enabled controlled launch boundary — cost if wrong:
parallel sessions can consume the same displayed room; advice cannot prevent exhaustion.

Ruling: compare original shell exit behavior — PowerShell's shell status can differ from a native child's status —
cost if wrong: unsupported shell forms need an explicit installation contract rather than an inferred translation.

Ruling: retain stronger partial capacity and mark equal-time conflicts unknown — failed metadata cannot justify a lower
reported constraint — cost if wrong: capacity may be understated until a complete later reading arrives.

Ruling: commit normalised observations before publishing optional captures — publication must not roll back a reading
already captured — cost if wrong: interrupted publication can leave an older or missing capture, with its old time intact.

Ruling: future-dated observations and clock rollback stay unusable — elapsed time cannot safely be invented — cost if wrong:
a saved refresh lease can keep the display unknown until the clock catches up or the owner rotates the profile token.

Ruling: keep the public facade as a module and helpers as private infrastructure — G6 automatically registers public
top-level packages as components, and section 13 adds an MCP tool rather than a new component op — cost if wrong:
private helper paths may need an explicit public API later; the MCP name and public facade stay stable.

Ruling: an implied fixed-window start in the future supplies no current headroom — future capacity cannot justify
current optional spending — cost if wrong: preannounced windows remain unknown until they begin.

## Left open

- Real Claude Code support: inspect a real sanitised 2.1.319 capture at
  `tests/fixtures/claude_statusline_2_1_319.capture.json`; its acceptance test stays skipped until then. Adding a file alone
  never changes the adapter's support declaration. No installation in the person's settings was performed.
- Account-continuity establishment for either provider. Owner tokens supply local cache isolation, not account identity.
- POSIX execution acceptance. Only preview quoting has structural checks here; native execution checks cover Windows CMD
  and PowerShell. No universal shell-installation claim.
- Causal advice benefit, equal-workload/paired period measurements, fresh-session routing uptake and actual exhaustion/cost
  observations. Synthetic deterministic checks establish none of these.
- Optional admission control and cross-provider execution/disclosure are not enabled. Their required seams, reservations
  and grants are not inferred.
- Existing test failures and sandbox-limited files in the table require the owner's outside-sandbox rerun. No new failing
  node or count was observed among existing files.

## Every retained new case and its first failure

The capture-acceptance case is intentionally skipped with the reason that real Claude support is not established.

| Case | First observed failure |
|---|---|
| `tests/allowance/test_advice.py::test_fixed_window_whose_start_is_in_future_cannot_supply_current_capacity[18000]` | Future-start fixed short window was called available and could supply current capacity. |
| `tests/allowance/test_advice.py::test_fixed_window_whose_start_is_in_future_cannot_supply_current_capacity[604800]` | Future-start fixed week was called available rather than unknown. |
| `tests/allowance/test_advice.py::test_fractional_weekly_pace[0.1-behind pace]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_fractional_weekly_pace[0.45-on pace]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_fractional_weekly_pace[0.55-on pace]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_fractional_weekly_pace[0.56-ahead of pace]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_fresh_behind_pace_with_independent_work_permits_advice_and_age_boundary` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[blocked]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[continuity]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[expired]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[failed]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[full short]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[future]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[missing]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[near full]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[no work]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[owner cap]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[reserve]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[rolling]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[stale]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_no_increase_without_every_condition[unsupported]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_reserve_release_is_fractional_week_only[0-0]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_reserve_release_is_fractional_week_only[0.7142857142857143-0.8]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_reserve_release_is_fractional_week_only[0.8571418571428571-0.8]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_reserve_release_is_fractional_week_only[0.8571428571428571-1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[max_age_seconds-0]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[near_full_share-0]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[near_full_share-1.1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[pace_tolerance-nan]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[parallelism_cap--1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[parallelism_cap-1.5]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[parallelism_cap-True]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[refresh_seconds-121]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[reserve_release_fraction-1.1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[reserve_share--1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_advice.py::test_settings_ranges[reserve_share-1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_additional_sets_preserve_nonstandard_reported_window_identity` | Reported nonstandard window became a missing record; full constraint was lost. |
| `tests/allowance/test_readings.py::test_all_codex_windows_permission_and_explicit_semantics` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_codex_additional_limit_sets_and_full_unknown_window_are_retained` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_duration_or_reset_keeps_known_full_constraint[resetsAt-10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_duration_or_reset_keeps_known_full_constraint[resetsAt-nan]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_duration_or_reset_keeps_known_full_constraint[resetsAt-tomorrow]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_duration_or_reset_keeps_known_full_constraint[windowDurationMins-0]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_duration_or_reset_keeps_known_full_constraint[windowDurationMins-True]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_duration_or_reset_keeps_known_full_constraint[windowDurationMins-inf]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[-1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[101]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[10]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[None]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[True]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[inf]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_invalid_share_is_unknown_and_does_not_drop_other_windows[nan]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_malformed_extra_codex_window_is_retained_beside_valid_sibling` | Only three windows returned; malformed reported primary disappeared. |
| `tests/allowance/test_readings.py::test_missing_or_malformed_claude_windows_are_explicit[None]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_missing_or_malformed_claude_windows_are_explicit[payload1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_missing_or_malformed_claude_windows_are_explicit[payload2]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_missing_or_malformed_claude_windows_are_explicit[payload3]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_missing_or_malformed_claude_windows_are_explicit[payload4]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_permission_is_not_inferred_from_truthiness[1]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_permission_is_not_inferred_from_truthiness[None]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_permission_is_not_inferred_from_truthiness[permission2]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_permission_is_not_inferred_from_truthiness[true]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_synthetic_claude_shape_is_not_supported_and_iso_reset_is_checked` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_synthetic_fixture_is_marked_and_every_claude_window_is_retained` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_readings.py::test_unrecognised_claude_window_retains_constraint_without_granting_semantics` | Only two windows returned; extra full constraint disappeared. |
| `tests/allowance/test_readings.py::test_window_contract_rejects_naive_times_and_invalid_identity` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_observer_worker_timeout_is_bounded_and_cannot_break_renderer` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_original_bytes_stdout_stderr_and_failure_exit_survive_observer_error[False]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_original_bytes_stdout_stderr_and_failure_exit_survive_observer_error[True]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_preview_preserves_original_config_quoting_and_exact_rollback[cmd]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_preview_preserves_original_config_quoting_and_exact_rollback[powershell]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_preview_preserves_original_config_quoting_and_exact_rollback[sh]` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_preview_runs_through_native_shell_with_spaced_paths_and_quoted_arguments[cmd]` | Native CMD quote forwarding failed; PowerShell exit oracle initially assumed the native child status. |
| `tests/allowance/test_statusline.py::test_preview_runs_through_native_shell_with_spaced_paths_and_quoted_arguments[powershell]` | Initial child-status expectation was wrong: original PowerShell returns 1 for native exit 7. The oracle was corrected to compare original-shell behavior; no production fix was needed for this case. |
| `tests/allowance/test_statusline.py::test_renderer_failure_does_not_prevent_observation` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_rollback_restores_renderer_without_overwriting_concurrent_settings` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_unsupported_forms_and_recursive_wrapper_are_refused` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_statusline.py::test_worker_capture_and_malformed_or_oversized_input_do_not_renew_good_time` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_store.py::test_capture_is_allowlisted_ordered_and_usable_as_fixture` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_store.py::test_equal_time_capture_cannot_reverse_first_saved_observation` | Equal-time capture changed from 99% to 10%. |
| `tests/allowance/test_store.py::test_equal_time_conflict_keeps_stronger_constraint_and_records_unknown` | Equal-time lower value replaced 99%. |
| `tests/allowance/test_store.py::test_failure_after_capture_publication_does_not_rollback_durable_observation` | Published 99% capture accompanied a rolled-back 10% cache. |
| `tests/allowance/test_store.py::test_failure_merges_by_identity_without_refreshing_last_good_and_restart` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_store.py::test_partial_near_full_update_keeps_new_constraint_and_old_last_good_time` | New 99% was replaced by old 10%. |
| `tests/allowance/test_store.py::test_profile_change_fences_late_writer_and_switching_back_does_not_revive_cache` | Invalidated old profile was recreated by a late writer. |
| `tests/allowance/test_store.py::test_profile_switch_missing_background_producer_and_expiry_are_unknown` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_store.py::test_racing_sessions_and_late_old_write_cannot_replace_newer_observation` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_store.py::test_refresh_lease_is_atomic_and_expires_after_crash` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_store.py::test_refresh_uses_only_raw_reader_and_failure_keeps_good_time` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_surface.py::test_configured_report_refresh_is_asynchronous_and_hook_never_refreshes` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_surface.py::test_context_note_only_on_advice_change_across_process_state` | Allowance package absent: ModuleNotFoundError. |
| `tests/allowance/test_surface.py::test_mcp_tool_returns_both_providers_and_honest_advisory_limits` | Real MCP client could not find g6_allowances. |
| `tests/allowance/test_surface.py::test_skill_codex_guidance_and_plugin_route_to_shipped_tool` | Shipped skill had no allowance route. |
