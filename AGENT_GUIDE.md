# G6 — guide for coding agents

You are an AI coding agent with access to G6, either as the `g6` MCP server or as the `g6` Python
package. G6 gives you checks you can run on your own work and on the actions you are about to take.
It does not think for you, and it never makes your result more certain than the evidence allows.

## Discover, then call

1. `g6_find(query)`: describe the task in plain words, for example "is this claim backed by these
   documents" or "should I run this shell command". You get components ranked by relevance; an empty list
   means nothing fits, so do not force one.
2. `g6_describe(component)`: maturity, ops and dependencies. Read the maturity reason.
3. `g6_invoke(component, op, params_json)`: one op, with typed input. Bad input fails with a named
   error (`[..._ERROR] op: field: message`), which you fix and retry. Never retry unchanged. Every reply
   carries `completion_state` and a `warning_card` (with a `G6_E_*` code) beside `result` or `error`:
   a result whose component reported no state is a `qualified-draft`, and a component's own `verified`
   stands only when G6's own code re-runs a check at the door, against what you gave it, and reproduces it.
   Any other is held at `qualified-draft` with a `G6_E_PROMOTION_LAW` card and `claimed_completion_state`
   (a re-check that finds a mismatch blocks), and `result` says the same. No component you can reach
   through a door has such a re-check yet, so nothing that comes through them is `verified` today. `ok` is false
   whenever the state is `blocked-escalated`: with no result, `error` says why; a blocked result keeps its answer in
   `result`, and its card says why. G6 re-checks artifacts with its own code; it does not defend against malicious
   code in its own process.

From Python, `g6.invoke(component, op, params)` is the same door as `g6_invoke`: the same label rule, the same `ok`,
and a sensitive op refused unless your user allowed it (`G6_MCP_SENSITIVE`, or `allow=("component.op",)` in their
own code). `g6.core.discovery.find / describe` work the same way. `g6.core.registry.get_registry().invoke(...)`
is the raw library call beneath the doors: it returns the component's own answer, unchecked, so read its state
as the component's claim. Or import the component directly.

## Rules

- **Labels are a floor, not a suggestion.** Report `qualified-draft` and `blocked-escalated` to your
  user as they are, together with the reasons. Do not summarise either one as "done" or "verified".
- **Never upgrade a label.** If you combine several results, the overall label is the weakest one.
- **`blocked-escalated` means stop that path.** Do what the result asks (more facts, a person's
  confirmation, a source), or tell your user you could not proceed.
- **Gates are advisory unless you obey them.** `action_gating` never executes anything. If it says
  `require_human_review` or `block`, do not run the action.
- **Sensitive ops are refused over MCP** unless the person who runs the server lists them in `G6_MCP_SENSITIVE`.
  Never change that setting yourself, and do not look for another route to them. Before your user lists one, tell
  them: Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows before the
  one-time access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a repository's tests)
  unless you allow it once with g6 setup. Code you allow then has your own rights: it can read your key file
  ~/.g6/.env and your other files, and its network guard works inside Python only. You are responsible for what you
  run.
- **Untrusted code runs only inside an operating-system boundary.** A run refused with `G6_E_ISOLATION_UNAVAILABLE`
  ran nothing: it is no answer, so never report it as a failed test or a wrong result. Tell your user what the refusal
  says; `g6 doctor` names what their computer needs for a boundary. Only they choose to run such code without one, in
  `g6 setup`: never run `g6 setup` or set `G6_ALLOW_UNCONFINED` yourself.
- **State lives in `$G6_HOME`** (default `~/.g6`). Do not write there yourself.

## Useful starting points

| You want to… | Call |
|---|---|
| check an action before running it | `action_gating.evaluate` with `{"action": {"action_type", "target_resource", "reversibility", "blast_radius", "target_backend"}}` (state what you know; unknown stays unknown; a declared fact can make a known action riskier, never safer). An irreversible or external action is blocked without a complete `verification_contract` (`expected_result`, `observation_window`, `leading_indicators`, `lagging_indicators`, `monitor`, `verification_time`, `stop_threshold`, `rollback_procedure`, `model_update_rule`) and goes to a person without `coordination_context` and `world_state`; `examples/demo_4_agent_discovery.py` shows one |
| check claims in your answer against sources you have | `grounding.check_claims` with `{"text", "sources": {name: text}}` |
| adjudicate claims in a message (type, evidence grade, disposition) | `epistemic_integrity.adjudicate` |
| check your drafted answer before reporting done | `coding_agent_gate.verify_answer` with `{task, answer, task_contract, evidence: [{kind: file|test|log|diff|review, ref, exit_code, output, reviewer}], verifier: {status, exit_code}}`; run `preflight` before acting. `pass` means no rule objected: still a `qualified-draft`. `needs_review` and `block` mean stop. A review counts only as `kind=review` naming someone other than you |
| order several actions safely under a budget | `action_logic.sequence_if_separable` |
| assess a Python patch before your user decides on it | `g6_review_patch` with `{repo, task, requirements: [{id, text, checks: [pytest node ids]}], tests}` (the person allows `patch_review.review`), and optionally `intent`, an intent contract `{goal, success_criteria, non_goals, failure_criteria, assumptions}`: its success criteria become requirements and its non-goals and failure criteria obligations to keep behaviour, each not established until a requirement with the same text names its checks; its assumptions are listed with their source and decide nothing; poll `metaharness_job`. `assessment.outcome` is `test_integrity_compromised`, `defect_detected`, `insufficient_evidence` or `supported_within_scope`, all `qualified-draft`. Supported within scope means every stated requirement passed its stated checks, not that the patch is complete. Report each requirement's status, the requirements with no adequate check, and the note that the specification may be incomplete. Never merge on it: `merge.status` is `requires_separate_approval` |

Not every component has an MCP block. One without a block is Python-only: `g6_describe` shows `"block": null` and no
ops, and `g6_invoke` answers `G6_E_NO_BLOCK`. `g6_find` can still rank them; when it does, tell your user the component
exists as a Python API rather than trying to invoke it. To run a task end to end with frozen checks, use
`g6.harness.run` from Python.

## Stop, and what does not exist yet

- **`G6_E_STOPPED` means a person stopped G6** (`G6_STOP`, or a `STOP` file in `$G6_HOME`). Stop that work and tell
  your user. Do not clear the stop yourself or route around it.
- **The meta-harness tunes G6 to a workflow your user declared** (`metaharness_*` tools, `implemented`).
  You may propose a profile, evaluate it, read the decision and diagnose (`metaharness_diagnose`). You cannot approve
  a change: `metaharness_approve` presents digest-bound client consent, including in the proposing session. Identity is recorded as given. **When a reply is not
  a recommendation, relay its `recovery` to your user:** the causes, then the remedies, saying which ones you can try
  as candidates (`who: candidate`) and which only your user can take (`who: person`). Never take a `person` step
  yourself. Never tell your user a change "improves results"
  unless its decision says `recommend`, and even then say it is a measured win on their held-out examples, not a
  guarantee. A workflow whose task trips the intake's risky check runs only if your user named themselves in `accept_risky`; never set it
  for them.

## Models

Components that need a model take an `LLMCaller` (`g6.core.llm`). Nothing calls a model unless
`G6_LLM_PROVIDER` names one (`claude_code` or `codex`, your user's own Claude Code or Codex sign-in; `ollama`; or
`anthropic`, `openai` or `openrouter` with a key; the default is `none`), and G6 never picks a paid provider. Over MCP,
an op that needs a model uses the one your user set in their key file; `claude_code` and `codex` count only when the
key file names them, never from the environment. With none set, the op is refused with a `*_NO_MODEL` code that says
how to set one: do that step yourself and call the op that checks it. From Python, you can pass yourself as the
caller. Keys and the provider line come only from your user's key file, `~/.g6/.env`, which they write themselves:
never write it, never copy a key into it, and never set a key or a provider in the environment expecting G6 to use
it. If a call fails for want of a key or a model, tell your user the line to add.

## Reporting back

When you have used G6, tell your user which component and op you called, the label it returned, and the
reasons, in one or two sentences.

For application construction and the Python patch reference, see [AI-for-AI workflow](docs/AI_FOR_AI_WORKFLOW.md). Start with `metaharness_start`; use `g6_compose` for explicit connections. Never interpret a contract check as empirical validation.
