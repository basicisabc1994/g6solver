# Release trials — 27 October 2026

These observations advance the v0.1.1 gates; they do not constitute a release approval.
Candidate application code is unchanged from 509e047. Trial protocol/runner were frozen
at 6d16ef6 before execution. No model inference or public publication occurred.

## Installation and compatibility

- Fresh Python 3.13.5 environment installed the development wheel with `[all]`.
- Installed-wheel smoke passed: isolated settings, Codex skill installation, strict
  composition, separate stdio MCP process and tool discovery.
- 39 selected composition, setup, declarative workflow, terminal and MCP tests passed
  against that installed wheel. General runner tests used the explicit Windows
  unconfined opt-in; these are compatibility tests, not OS confinement evidence.
- Actual Codex CLI 0.164.0 and Claude Code 2.1.311 registration succeeded in temporary
  homes. Each client's `mcp get g6` confirmed the full interpreter path and each skill
  was installed. The user's client configuration was not changed.

This is automated installation evidence. It does not substitute for an unfamiliar
person completing installation and understanding the workflow.

## Client consent

`tools/probe_codex_consent.py` uses an isolated Codex app server, creates an ephemeral
thread and calls G6 tools directly. It never calls `turn/start` or supplies credentials.
The installed client discovered G6 and created the synthetic workflow. No elicitation
request reached the probe on direct confirmation calls, including when the probe was
ready to accept. G6 refused every confirmation and provided the terminal fallback.

Result: **fail-closed behavior observed; interactive client consent still unverified**.
Automated response handling would not authenticate a human even if delivered.
Claude's real elicitation UI remains untested; CLI registration alone establishes none
of its consent semantics. The protocol mechanism is described in the
[official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).

[Recorded direct-call results](../validation/results/release_0_1_1/codex-direct-consent.json).

## Patch-review engineering trial

Six synthetic cases ran in Linux bubblewrap with no network, using the
[frozen protocol](../../validation/patch_review/PROTOCOL.md). All five executed test
cases recorded the actual bubblewrap backend; the stale artifact was refused before
test execution. The simplistic comparator accepts a patch whenever its selected
test passes. Both conditions use the same observed test run.

| Fixture | Selected-test comparator | G6 overall decision | Specific observation |
|---|---|---|---|
| Correct addition fix | Accept | Blocked | Tests pass; merge authority/profile absent |
| Unfixed subtraction | Refuse | Blocked | Selected test fails |
| Weakened test | Accept | Blocked | Test weakening flagged |
| Runner configuration changed | Accept | Blocked | Runner change flagged |
| Unselected negative-input regression | Accept | Blocked | Selected tests miss the regression |
| Stale artifact digest | Not paired | Blocked | Refused before tests |

The comparator accepts two behaviorally wrong patches and one policy-violating runner
change. G6 accepts none, but it also blocks the correct patch. **This does not establish
utility or improved correctness.** The unselected regression is blocked by absent
action authorization, not detected as a functional bug. Passing selected tests cannot
support a claim about the entire patch. The next application trial needs separate
measures of useful review findings, false assurances and acceptable task completion;
counting blocks alone would reward an unusable system.

[All observations and scoped receipts](../validation/results/release_0_1_1/patch-review-engineering.json).

## Remaining human trials

For each supported client, an unfamiliar user should install in a fresh project, find
G6's tools, construct a small workflow and inspect its frozen digest. Confirm that the
actual UI displays that digest and the execution consequences. Decline once and verify
no confirmation; then accept and inspect the recorded consent. Exercise missing
elicitation through the documented terminal fallback. Record client/version, observed
prompts, errors, manual fixes and time. Do not put credentials or personal names in the
public record. An exploration-only run is not successful tuning.

The broader 8/10 usability gate, application utility comparison, live-client consent,
certificate renewal, release artifacts and cross-platform CI remain separate gates.
