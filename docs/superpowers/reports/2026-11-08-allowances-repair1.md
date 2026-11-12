# Allowance repair 1 for review 1

Retained: the binding-generation root (findings 2 and 5) and malformed-window root (finding 3).
Reverted under Rule 5: the wrapper implementation root (findings 1, 4 and 6), including its individually passing argument and
PowerShell exit changes. Those three findings are offered, not marked fixed. Their counterexamples remain in the tree and remain red.

Branch remains `claude/allowance`, HEAD `eabe3ef`; change base `ea3f96c`. No staging or commit was attempted.
The four changed module originals were saved byte-for-byte and restored for the before runs of later-added cases,
without git checkout. The review tree was read only. Copies and all test state stayed in this worktree.

## Exact commit message and per-finding results

`.build/commit_msg.txt` is UTF-8 without BOM, LF only. Rule 5 requires truthful `Offered:` entries for the three
unapplied findings, so this message has three `Fixed:` paragraphs and three `Offered:` paragraphs.

```text
Fence allowance generations and isolate malformed windows [skip ci]

Offered: Finding 1's accepted leading-hyphen profile can prevent renderer startup.
Class: boundary_case. Its copied test failed on eabe3ef and still fails in the retained tree. The first build tested ordinary tokens. The wrapper root was reverted under Rule 5 because its full timing inventory was not cleared; the individually passing option-value change was not retained.

Fixed: Finding 2's old refresh could revive an invalidated cache after a profile switch away and back.
Class: check_then_act_race. test_finding_2.py found two windows before and an empty cache after. The first build compared tokens only and did not test returning old work after ABA rotation. The 208-case generation inventory and legacy store checks pass. Mixed-version workers and power loss were not checked.

Fixed: Finding 3's malformed foreign label could discard valid sibling windows.
Class: boundary_case and identity_from_rendering. All three test_finding_3.py cases had zero windows before and three after. The 41-case window inventory and 30 legacy reading cases pass. The first build used ordinary UTF-8 labels and did not challenge escaped surrogates or rendered-spelling collisions. Real Claude capture support remains unestablished.

Offered: Finding 4's blocked observer stdin write can exceed its intended budget and delay rendering.
Class: boundary_case. test_finding_4.py measured about 2 seconds against its 1.25-second assertion before and still fails after the wrapper revert. The first build tested a tiny timeout without a blocked full-pipe write. The attempted root passed this reproduction but failed three full-command Windows timing cases, so no wrapper timing repair is retained.

Fixed: Finding 5's stale report could restore an obsolete binding and delete a newer reading.
Class: check_then_act_race. test_finding_5.py lost q's two windows before and retains both after. The first build read settings and bound later without a generation compare-and-set. Reports snapshot generations before reading settings; all writes and rebinds check them transactionally. The generation inventory, matched importers and legacy checks pass. Outside-sandbox checks remain to the owner.

Offered: Finding 6's PowerShell invocation can replace an explicit renderer exit 7 with 1.
Class: boundary_case (PowerShell exit propagation). test_finding_6.py fails before and in the retained tree. The first build compared a native-child PowerShell baseline but did not test explicit shell exit 7. Its individually passing exit propagation change was reverted with the uncleared wrapper root. Native Unix execution remains unestablished.

Checks: 80 matched before/after test files, no new failing nodes; 208 generation cases and 41 window cases pass. Review copies retain the three offered failures and pass findings 2, 3 and 5 plus all 29 audit cases. Ruff passes; three import contracts kept; no bound files changed. Native sh's 20 signal-pipe failures remain separate from owner-folder sandbox limits. No model, real provider, network, personal settings, git staging or commit operation was performed.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016opK1M2VFbXVve6kiJtAxW
```

## Why the wrapper root was reverted

The attempted rewrite attached option values, propagated the PowerShell exit code, separated Windows pipe exchange from
its deadline-controlling thread, started rendering independently and isolated cleanup failures. Each original wrapper
reproduction passed in that candidate. Its full native timing inventory did not pass, even after reserving cleanup time.
No timing assertion was relaxed. These final attempted Windows cases exceeded renderer time plus 0.75 seconds:

| Attempted Windows case | Renderer baseline | Wrapped | Ceiling |
|---|---:|---:|---:|
| `test_shell_and_installation_inventory[stalled-cmd-cmd]` / `zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz` | 0.203s | 1.188s | 0.953s |
| `test_shell_and_installation_inventory[stalled-powershell-cmd]` / `--` | 0.468s | 1.219s | 1.218s |
| `test_shell_and_installation_inventory[slow-cmd-cmd]` / `--` | 0.203s | 1.015s | 0.953s |

All 286 measured attempted rows preserved bytes and exit codes. The incomplete matrix and its three overruns remain in
`.build/repair1/attempt-matrix.jsonl` and `.build/repair1/final_roots/wrapper-matrix.xml`. The attempted source is retained
only as `.build/repair1/offered_statusline.py`; it is not the shipped source. The retained source adds only the generation
capture/transport needed by the binding root, including a bounded metadata lookup. It keeps the original wrapper quoting,
synchronous renderer/observer order and IPC behaviour, with their known failures explicitly documented.

Options: an independently supervised observer outside the renderer's lifetime would need a new ownership/cancellation
protocol and full acceptance checks. Alternatively, changing the frozen timing contract to allow separate startup or
scheduling slack would require the owner to change that contract; its cost is a weaker timing promise. Neither is applied.

## Inventories and test-first evidence

- Wrapper: code-derived shell branches and parser choices; all nine renderer/invocation shell combinations; command-object
  installation, preserved padding/unrelated settings and rollback; five accepted token edges; refusal of spaces, quotes,
  percent, caret, dollar, non-ASCII, NUL and surrogates; exits 0, 1, 7, 255; binary stdout/stderr/input over 1 MiB; stalled,
  slow, crashing and oversized observers; real child-start checks. The retained inventory has 40 failed, 34 passed,
  matching its original-source result. Its failures are the offered wrapper class plus 20 native sh baseline failures.
- Native sh: Git sh cannot create its signal pipe (`Win32 error 5`), on original-renderer or common-launcher baselines too.
  These twenty combinations remain explicit failures. No alternate execution route or permission bypass was attempted.
  POSIX argv checks have two leading-hyphen failures and three passing cases after the wrapper revert; Unix execution
  remains unestablished. Native reproductions have Windows-only guards for other platforms.
- Generations: every SQL-writing method (`__init__`, `bind`, `_save`, `claim_refresh`, `changed_note`), every binding/removal,
  180 writer-pair/transaction-boundary interleavings, observation/capture boundaries, delayed refresh thread start,
  ABA rotation, removal/back, stale settings, stale notes, malformed cache, legacy migration and interruption after each
  SQL write with restart. All 208 cases pass, versus 116 failed and 92 passed on original sources.
- Windows: all three foreign-label sites (Claude window, Codex window, Codex limit set), every SHA-256 site (including
  validated profiles and fixed hook text), JSON decoding, reset conversion, semantics, capture allow-list and per-window
  normalisation. Surrogates, NUL, long labels, wrong types, rendered-spelling collisions, non-reflexive labels and failed
  representations remain beside a valid full window. All 41 cases pass, versus 18 failed and 23 passed originally.
- Generation lookup and cleanup: two cases pass before and after. They caught an unhandled cleanup exception in the
  discarded threaded candidate and an unbounded metadata lookup in the first generation transport. The retained bounded
  metadata lookup avoids adding that new renderer regression. No quota input or foreign label can supply authority.

Each review reproduction was copied into `tests/allowance/repair1/` and watched fail on original module bytes first.
The pair-writer test initially counted an operation that SQLite had refused; that oracle was corrected and rerun against
the original sources. A test-list BOM initially produced one invalid filename; the actual agent test was then rerun and
passed before and after. These harness corrections were not product findings or waived assertions.

## Before and retained-after tables

All 32 importer-selected files, plus the supplied first-build list expanded to individual files: 80 files. Later edits
were followed by the union of reader and status-line importer lists, then the status-line list after the wrapper revert.
The final rows supersede earlier candidates. Every previously passing case remains passing on the matched checks;
failing-node sets introduce no new node. Existing failures are not called passing. `tests/contract/` is absent.

One file per process and one test process at a time; required worktree TMP/TEMP/G6_HOME/PYTHONPATH, sandbox plugin and
unique basetemp on every run. The supplied free-memory helper ran before every process, waiting below 2.5 GB.
No whole-suite invocation, real provider, model, Docker, network or personal settings operation was used.

| Test file | Before | After | Note |
|---|---|---|---|
| `tests/agents/test_tool_relay_door.py` | 2 passed | 2 passed |  |
| `tests/allowance/repair1/test_finding_1.py` | 1 failed | 1 failed | wrapper root reverted; known failures retained |
| `tests/allowance/repair1/test_finding_2.py` | 1 failed | 1 passed |  |
| `tests/allowance/repair1/test_finding_3.py` | 3 failed | 3 passed |  |
| `tests/allowance/repair1/test_finding_4.py` | 1 failed | 1 failed | wrapper root reverted; known failures retained |
| `tests/allowance/repair1/test_finding_5.py` | 1 failed | 1 passed |  |
| `tests/allowance/repair1/test_finding_6.py` | 1 failed | 1 failed | wrapper root reverted; known failures retained |
| `tests/allowance/repair1/test_generation_inventory.py` | 116 failed, 92 passed | 208 passed |  |
| `tests/allowance/repair1/test_observer_cleanup.py` | 2 passed | 2 passed |  |
| `tests/allowance/repair1/test_posix_preview.py` | 2 failed, 3 passed | 2 failed, 3 passed | wrapper root reverted; known failures retained |
| `tests/allowance/repair1/test_window_inventory.py` | 18 failed, 23 passed | 41 passed |  |
| `tests/allowance/repair1/test_wrapper_inventory.py` | 40 failed, 34 passed | 40 failed, 34 passed | wrapper root reverted; known failures retained |
| `tests/allowance/test_advice.py` | 36 passed | 36 passed |  |
| `tests/allowance/test_readings.py` | 30 passed | 30 passed |  |
| `tests/allowance/test_statusline.py` | 12 passed, 1 skipped | 12 passed, 1 skipped |  |
| `tests/allowance/test_store.py` | 11 passed | 11 passed |  |
| `tests/allowance/test_surface.py` | 4 passed | 4 passed |  |
| `tests/context/test_context_envelope.py` | 1 passed | 1 passed |  |
| `tests/core/test_discovery_cli_mcp.py` | 1 failed, 13 passed | 1 failed, 13 passed | matching baseline failures |
| `tests/core/test_door_promotion.py` | 56 passed | 56 passed |  |
| `tests/core/test_envelope_egress_field.py` | 2 passed | 2 passed |  |
| `tests/core/test_invoke_parity.py` | 15 passed | 15 passed |  |
| `tests/core/test_mcp_improve.py` | 94 passed | 94 passed |  |
| `tests/core/test_mcp_invoke_envelope.py` | 1 failed, 16 passed | 1 failed, 16 passed | matching baseline failures |
| `tests/core/test_mcp_mission.py` | 41 failed, 12 passed, 9 skipped | 41 failed, 12 passed, 9 skipped | matching baseline failures |
| `tests/core/test_mcp_model_door.py` | 16 passed | 16 passed |  |
| `tests/core/test_mcp_review_loop.py` | 30 failed, 9 passed | 30 failed, 9 passed | matching baseline failures |
| `tests/core/test_mcp_review_reply.py` | 7 passed | 7 passed |  |
| `tests/core/test_outside_door.py` | 4 passed | 4 passed |  |
| `tests/core/test_reliability_envelope.py` | 2 passed | 2 passed |  |
| `tests/formal/test_envelope_and_surface.py` | 26 passed | 26 passed |  |
| `tests/goal_engine/test_goal_engine_envelope.py` | 11 passed | 11 passed |  |
| `tests/harness/test_completion_hook.py` | 26 failed, 28 passed | 26 failed, 28 passed | sandbox-limited: WinError 5 |
| `tests/harness/test_patch_report.py` | 6 failed, 40 passed, 5 skipped | 6 failed, 40 passed, 5 skipped | matching baseline failures |
| `tests/harness/test_patch_review.py` | 113 failed, 24 passed, 13 skipped, 1 xfailed | 113 failed, 24 passed, 13 skipped, 1 xfailed | sandbox-limited: WinError 5 |
| `tests/harness/test_patch_review_imports.py` | 20 failed, 1 passed, 6 skipped | 20 failed, 1 passed, 6 skipped | sandbox-limited: WinError 5 |
| `tests/harness/test_proposed_checks.py` | 21 failed, 62 passed, 8 skipped | 21 failed, 62 passed, 8 skipped | sandbox-limited: WinError 5 |
| `tests/harness/test_verification_formal.py` | 37 failed, 27 passed, 3 skipped | 37 failed, 27 passed, 3 skipped | matching baseline failures |
| `tests/harness/test_verification_proposer.py` | 31 failed, 4 passed, 2 skipped | 31 failed, 4 passed, 2 skipped | matching baseline failures |
| `tests/harness/test_verification_templates.py` | 93 failed, 87 passed, 11 skipped | 93 failed, 87 passed, 11 skipped | sandbox-limited: WinError 5 |
| `tests/invariants/test_envelope.py` | 18 passed | 18 passed |  |
| `tests/metaharness/test_forged_confirmation.py` | 7 failed, 3 passed | 7 failed, 3 passed | matching baseline failures |
| `tests/metaharness/test_loop_envelope.py` | 102 failed, 10 passed | 102 failed, 10 passed | matching baseline failures |
| `tests/metaharness/test_phase6_mcp.py` | 9 failed, 2 passed | 9 failed, 2 passed | matching baseline failures |
| `tests/metaharness/test_uat_diagnostics.py` | 15 passed | 15 passed |  |
| `tests/self_maintenance/test_envelope.py` | 94 passed | 94 passed |  |
| `tests/self_maintenance/test_pacing.py` | 75 passed | 75 passed |  |
| `tests/self_maintenance/test_usage.py` | 7 failed, 162 passed | 7 failed, 162 passed | sandbox-limited: WinError 5 |
| `tests/site/test_about_page.py` | 1 passed | 1 passed |  |
| `tests/site/test_call_to_action.py` | 569 passed | 569 passed |  |
| `tests/site/test_components_page.py` | 21 passed, 1 skipped | 21 passed, 1 skipped |  |
| `tests/site/test_contact.py` | 5 passed | 5 passed |  |
| `tests/site/test_docs_copy.py` | 6 passed | 6 passed |  |
| `tests/site/test_essay_figures.py` | 3 passed | 3 passed |  |
| `tests/site/test_evidence.py` | 19 passed | 19 passed |  |
| `tests/site/test_failure_modes_page.py` | 6 passed | 6 passed |  |
| `tests/site/test_faq_page.py` | 7 passed | 7 passed |  |
| `tests/site/test_get_started.py` | 11 passed | 11 passed |  |
| `tests/site/test_graph_page.py` | 11 passed | 11 passed |  |
| `tests/site/test_guide.py` | 4 passed | 4 passed |  |
| `tests/site/test_guide_diagrams.py` | 6 passed | 6 passed |  |
| `tests/site/test_home_page.py` | 16 passed | 16 passed |  |
| `tests/site/test_honesty.py` | 436 passed | 436 passed |  |
| `tests/site/test_kinetic.py` | 287 passed | 287 passed |  |
| `tests/site/test_launch_copy.py` | 16 passed | 16 passed |  |
| `tests/site/test_license_page.py` | 2 passed | 2 passed |  |
| `tests/site/test_live_only.py` | 8 passed | 8 passed |  |
| `tests/site/test_loop_in_public.py` | 3 passed | 3 passed |  |
| `tests/site/test_metaharness_page.py` | 7 passed | 7 passed |  |
| `tests/site/test_narrative.py` | 7 passed | 7 passed |  |
| `tests/site/test_pages.py` | 1696 passed | 1696 passed |  |
| `tests/site/test_research_page.py` | 13 passed | 13 passed |  |
| `tests/site/test_static_build.py` | 7 passed | 7 passed |  |
| `tests/site/test_theme.py` | 2 passed | 2 passed |  |
| `tests/site/test_tried_it.py` | 3 passed | 3 passed |  |
| `tests/site/test_worked_examples.py` | 4 passed | 4 passed |  |
| `tests/skills/test_using_g6.py` | 10 passed | 10 passed |  |
| `tests/skills/test_vendored_superpowers.py` | 7 passed | 7 passed |  |
| `tests/solver/test_solver_envelope.py` | 4 passed | 4 passed |  |
| `tests/workforce/test_workforce_door_codes.py` | 3 passed | 3 passed |  |

Only rows containing G6 owner-folder `PermissionError: [WinError 5]` are labelled sandbox-limited. Git sh's signal-pipe
failure is reported separately. Other identical failures are baseline failures in this environment; an outside-sandbox
cause is not assumed. The owner still needs the outside-sandbox rerun.

## Review copies and probes on the retained tree

| Review copy | Retained result | Reason for failure |
|---|---|---|
| `test_finding_1.py` | 1 failed in 0.46s | wrapper root reverted: profile interpreted as an option |
| `test_finding_2.py` | 1 passed in 0.44s |  |
| `test_finding_3.py` | 3 passed in 0.65s |  |
| `test_finding_4.py` | 1 failed in 2.58s | wrapper root reverted: blocked write exceeds budget |
| `test_finding_5.py` | 1 passed in 0.40s |  |
| `test_finding_6.py` | 1 failed in 1.54s | wrapper root reverted: PowerShell changes explicit exit |
| `test_audit.py` | 29 passed in 1.02s |  |
| `probe_shells.py` | 3 matching rows, 5 mismatches | Four leading-hyphen argument failures; one PowerShell exit propagation failure. |

All copies and support scripts are under `.build/reviews/allow1/`. The audit root lookup and temp fixture were adapted
only for that deeper location. The shell probe printed all eight comparisons and then asserted equality; it has five
mismatches, rather than hiding them behind its original print-only exit. The review tree was never edited.

## Checks and class-list walk

Ruff passes on all 15 changed Python files. Import linter: 878 files, 3,064 dependencies, three contracts kept, zero broken.
Five certificates and 112 referenced paths: no changed bound file or certificate. Bound core callers, usage.py and pacing.py
are unchanged from the change base. Public MCP reply fields, command names, advice policy and capture fields stay intact;
only the private binding table adds its required generation. G6 MCP review was not called because this brief forbids it.

| Class | Walk and limit |
|---|---|
| `identity_from_rendering` | Foreign labels use exact string bytes, a separate wrong-type digest domain and explicit ambiguous-window records; no raw spelling becomes authority. Account continuity remains unknown. |
| `one_fact_two_rules` | Advice and the old loop stay unchanged. One generation predicate guards quota writers; one share calculation survives metadata failures. No second pacing or reserve rule was added. |
| `own_parser_for_foreign_format` | Standard JSON, timestamp/base64 parsing and native shell quoting remain. Wrapper shell failures are offered, not called repaired. |
| `failure_read_as_success` | Malformed windows and ambiguities retain their own failures and no new good time. Stale generations write nothing; observer return status cannot establish cache freshness, support or continuity. |
| `check_then_act_race` | Settings snapshot precedes file reading. Compare-and-set, quota writes, leases, note writes and capture rechecks share writer transactions with their generation checks. |
| `state_not_durable` | Generations, migration and invalidation survive restart; injected SQL interruptions roll back. Observation-before-capture behaviour stays. Mixed-version workers and power loss were not checked. |
| `untrusted_input_given_authority` | Payload fields cannot establish support, semantics, sign-in identity, source grants or launch permission. Capture fields remain allow-listed; advice launches nothing. |
| `boundary_case` | ABA, removal/back, stale settings, malformed state, surrogate/NUL/long/wrong-typed labels and digest ambiguity have retained passing cases. Wrapper option, exit and deadline boundaries remain open. |
| `guard_covers_less_than_it_guards` | Generation guards cover every retained store write and rebind. The wrapper timing guard was not sufficient in the full matrix, so that root was reverted. No reservation or exhaustion-prevention claim is made. |

## Rulings

Ruling: revert all three wrapper findings together — its full timing inventory still failed after a real attempt and the
brief requires root repairs — cost if wrong: three individually reproduced bugs remain open pending a cleared root repair.

Ruling: removing a configured profile retires its generation — otherwise removal/back is another ABA hole — cost if wrong:
a conservative cache miss until a new bound observation arrives.

Ruling: reject stale settings snapshots without retrying obsolete data — older reports cannot restore a prior binding —
cost if wrong: a fresh report is needed after contention.

Ruling: retain digest ambiguities as separate failed windows — a malformed identity cannot discard siblings — cost if wrong:
extra unknown windows and conservative advice until unambiguous input arrives.

Ruling: keep native sh unestablished — its own baseline cannot create a signal pipe and no bypass was attempted — cost if
wrong: a separate native execution check remains necessary.

## Left open

Findings 1, 4 and 6; the complete wrapper native timing/byte/exit root; twenty native sh combinations; outside-sandbox reruns
of owner-folder and other baseline failures; a checked real sanitised Claude 2.1.319 capture; account continuity; mixed-version
workers, power-loss behaviour and causal usage benefit. The real-capture admission case stays skipped. No optional admission,
provider switch or source-disclosure grant was added. Review 2 and the owner's eventual commit remain outside this repair.
