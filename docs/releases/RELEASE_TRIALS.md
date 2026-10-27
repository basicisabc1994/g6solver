# Release trials — 27 October 2026

These observations advance the v0.1.1 gates; they do not constitute a release approval.
Candidate application logic is unchanged from 509e047; the final package version is 0.1.1. Trial protocol/runner were frozen
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
final release artifacts and cross-platform CI remain separate gates.

## Final version and certificates

The source version was frozen at 0.1.1 before issuance. A fresh independent reader
verified that all 98 rule-function ASTs and all source/rule/case/adapter/refusal
bindings were unchanged, reviewed the changed shared enforcement code, and then
verified the exact version-only delta. Original source-reading attribution and
limitations are preserved. The new attestation is a delta review, not a new reading
of the source clauses and not authenticated domain-expert approval.

All five certificates were issued through the normal tool, with 150 passing checks
and no named gaps or current-artifact inspection problems. Each remains
`qualified-draft`. Numerical runs used Windows's process-local unconfined opt-in;
OS boundary evidence remains the separate Windows/Linux tests. Source files and
private quote material stayed outside the public checkout.

The final wheel passed installed-package/MCP smoke on Python 3.12 and 3.13. Two
builds using the same SOURCE_DATE_EPOCH produced identical wheel bytes. The release
workflow now requires a matching stable source version, refuses an existing release,
and creates a draft without overwriting assets. Nothing was tagged or published.

All five normal offline `--check` reruns exited 0 and reproduced the committed certificates.
The installed wheel also passed all five certificate artifact-binding inspections.

## Hosted platform follow-up

The first Windows, Linux and macOS bundle builds and executable smokes passed in
run 40450792761. The hosted site suite passed 2,714 tests with one skip. Initial
conformance failures exposed a recorder that mistook bubblewrap for its payload
and the synthetic PR merge's different first-parent history; both were reproduced
and fixed without changing certificate validation. CI now checks the reviewed head.

Further platform logs exposed a real macOS output-collection problem: `/var` is a
trusted parent alias, but was rejected by the new link checks. The sandbox now
canonicalizes its host-created root before child execution; links created by the
child are still refused. A failing alias regression passed after the fix, with
an independent review. The broader Linux regression run passed 108 tests (19
platform skips), and selected Windows confinement tests passed 41 (one skip).

Two Linux tests expected the old unconfined dummy-key/proxy behavior. They now
explicitly exercise that mode, with a separate real bubblewrap test proving that
confined repository tests receive no model-key or proxy configuration.

This source change supersedes the earlier wheel/bundle bytes. The three affected
ML certificates require normal renewal; the other two bindings remain unchanged.
Consult the current wheel manifest and PR checks for the refreshed candidate.

The owner volunteered to pilot both clients. Follow [OWNER_PILOT.md](OWNER_PILOT.md)
and record results separately; no human observations have yet been supplied.
