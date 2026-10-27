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

Repeat real Codex decline and acceptance after upgrading, then test Claude Code.
Do not treat terminal consent or automated protocol handlers as those human trials.
The unfamiliar-user and application-utility gates remain open.
