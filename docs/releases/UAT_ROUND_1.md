# Owner acceptance testing: Codex

On 28 October 2026 the owner exercised the prepared 0.1.1 wheel with Python
3.12.10 and Codex 0.164.0. These observations came from user-operated commands
and supplied client transcripts. They are not an unfamiliar-user trial or an
independent execution audit. Claude Code testing was deferred by the owner.

## Observed results

- Installation and component discovery succeeded. The user needed extra guidance
  to activate the virtual environment before using the bare `g6` command.
- Exact-match grounding remained qualified; a numerical mismatch was reported as
  a conflict. Composition preserved that refusal. Two-stage traces stopped after
  the first conflict, while a positive control reported both stages executed.
- A deliberately missing input mapping was refused without automatic repair.
- Codex displayed no reported confirmation prompt. G6 refused confirmation and
  terminal inspection showed the workflow still unconfirmed.
- The owner personally used the terminal fallback; consent persisted against the
  expected digest and established initial version 0.
- A hypothesis-bearing code candidate was stored without changing that version.
- After the owner's AppContainer read/execute grants, a single bounded evaluation
  completed and reported AppContainer with networking disabled. Baseline scored
  0/1, candidate 1/1, with a one-sided paired p-value of 0.5.
- The decision blocked promotion because independent patch safety evidence was
  absent. History retained version 0 and recorded the evaluation and decision.

Both examples were previously visible to the proposer. No generalization,
independent improvement or application utility was demonstrated. The patch-evidence
gate blocked first; minimum-case and statistical gates were not separately tested.
Successful activation, rollback and cross-component evidence transfer remain untested.

## Consent root cause and correction

A no-model probe against the installed Codex app server reproduced the missing
prompt. Codex logged a schema parse error for the top-level `title` added by
FastMCP's primitive response wrapper, then returned cancellation. G6 had collapsed
that response together with decline and unavailable elicitation into one generic
refusal. This explains the reproduced protocol failure; the live user's client
still needs a retest after installing the corrected candidate.

G6 now sends a minimal standard object schema with a required string through the
MCP SDK. It retains the name requirement rather than using an empty confirmation
schema that client policy could automatically accept. Responses distinguish
declined, cancelled, unavailable, invalid and changed-workflow outcomes. Cancellation
does not claim that a person saw or dismissed a prompt. Exception details are not
exposed; only the exception type is recorded.

The installed app-server probe now delivers three digest-bound requests and passes
automated decline/cancel/accept checks without starting a model turn. This validates
protocol delivery, not human UI behavior or identity. The supported request/response
flow is described in the [Codex app-server documentation](https://learn.chatgpt.com/docs/app-server).

## Diagnostics and remaining gates

Grounding's contract now names lexical sentence overlap instead of a model.
Degraded envelopes explain their assurance limitation without claiming execution
failure. Qualified lifecycle replies retain the required warning card but describe
the recorded state and useful next steps. Confirmation no longer suggests a
Python-only `mh.baseline()` instruction to MCP/terminal users.

The owner subsequently reported seeing the Codex consent form: Esc cancelled it
and left the fresh workflow unconfirmed. No separate Decline control was visible.
A later acceptance recorded consent to the expected workflow digest and initial
version 0, without evaluation or decisions. This establishes observed cancellation
and acceptance in Codex, not an explicit Decline UI path or authenticated identity.
Claude Code remains untested. Do not treat terminal consent or automated protocol
handlers as those human trials.
The unfamiliar-user and application-utility gates remain open.

## Approval after an installation upgrade

The owner attempted approval of the original blocked decision after installing the
consent-fix wheel. Approval refused and version 0 remained unchanged, but the error
was a decision-replay schema mismatch rather than the non-recommend approval guard.
Read-only investigation reproduced the saved judging-code hash from the previous
source. Only `metaharness/api.py` differed in that manifest; its diagnostic changes
changed the whole-file hash. The current gate therefore blocked on changed judging
code before reaching the original missing-patch-evidence reason.

The source correction checks judging-code drift before decision replay and before
requesting consent, including cached decisions. It returns `G6_E_META_PROTECTED`
with saved/current hashes and guidance to review the installation changes before
choosing whether to reconfirm. Reconfirmation is not candidate approval. No pilot
records were changed and no evaluation was repeated. This correction still needs
packaging and a client retest; it is not in the previously installed wheel.

Local validation of this correction: all 10 cross-process approval tests passed,
including four upgrade cases covering cached/reloaded decisions and favorable/
unfavorable outcomes; lint and whitespace checks passed. The broader metaharness
run returned 321 passed, 1 skipped and 20 failed, concentrated in
`test_phase5_structural.py` and `test_steps.py`. A distillate failure explicitly
reported unavailable AppContainer access for the development interpreter. No ACLs
or unconfined-execution settings were changed to make these tests pass. The broader
suite is not green and this result is not release clearance.

Follow-up ran the two locally failing modules plus the cross-process approval
module under real Linux bubblewrap: 53 passed, 1 skipped, without a G6 unconfined
opt-in. The path-ID regression also passed in a Windows AppContainer using a
disposable interpreter and test-created repository with read/execute grants;
the pilot environment's permissions were not changed.

Hosted run 40602321117 (before this approval correction) passed both Linux matrix
jobs, conformance, scan and site. Windows failed the path-ID normalization test;
macOS failed that test and the related correct-patch suite comparison. These
cross-platform failures were subsequently reproduced with CI's pytest 9.2.
That version replaces automatic string IDs longer than 100 characters with
argument/index IDs such as `path0`; baseline and patched copies at different path
lengths can therefore disagree. The suite runner now requests the legacy string
ID strategy before normalizing repository roots. The regression fixture uses a
long filename so short Linux temp paths exercise this threshold too.

The two affected workforce modules passed all 40 tests under pytest 9.2 and real
Linux bubblewrap. Both targeted cases also passed under pytest 8.0.2 and under
pytest 9.1.1 in Windows AppContainer. Independent review found no blocking issues
in the approval or path-ID correction. Fresh hosted cross-platform CI remains
required; these local reproductions do not replace it.
