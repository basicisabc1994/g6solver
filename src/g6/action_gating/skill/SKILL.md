---
name: g6-action-gating
description: Use when an agent proposes an outward action and needs a deterministic decision before acting, when a workflow must stay inside the bounds a person reviewed it for, or when the person wants per-run operation count limits and injected safeguards that can only tighten that decision.
---

# G6 action gate

One pre-flight gate, decision vocabulary and audit store. It never performs the proposed action. Optional Python
checks tighten the gate; operation budgets reserve counts and do not authorise outward acts.

## When to use it

- Before sending, paying, promising, deleting, running code, deploying or changing permissions outside the conversation.
- When an action depends on factual preconditions, verification, coordination or world-state evidence.
- When the person supplies count limits over `g6.csf.OPERATIONS`, trusted safeguard predicates or a finite model to check.
- When a workflow was reviewed for some actions only, and must not drift beyond them: pass its review scope.

## When not to use it

- Routine repository changes: `coding_agent_gate.preflight`; a read-only lookup needs no outward-action approval.
- Whole-run time, cost and kill-switch controls: `safety_engine`.
- As the sole security boundary: the caller must obey the decision and provide trustworthy facts.

## Ops

- `evaluate`: normalise and decide one `action`, with an optional review `scope`; returns its capsule, decision,
  evidence and audit record.
- `explain`: evaluate the same action (and `scope`) and render the risk message.
- `budget_check`: preview by default; with `dry_run=false`, reserve one count for `run_id` and `operation`, or refuse with the remaining count; append the check. Sensitive.
- `budget_record`: preview by default; with `dry_run=false`, record an observation against an unrecorded reservation without spending another count. Sensitive.
- `budget_report`: read used and remaining counts for `run_id`; creates no directory, ledger or lock file.
- `budget_policy`: preview `policy={"limits": {operation: count}}`; `dry_run` defaults true. Applying a policy is sensitive and refused over MCP unless the host allows it.

## Reading the result

The gate's decisions are `allow`, `allow_with_notice`, `require_additional_information`, `require_confirmation`,
`require_mitigation`, `require_human_review` and `block`. Read `gate_decision.reason_codes`, `required_user_message`,
`warning_card` and `evidence`. Results are at most `qualified-draft`, never `verified`. A missing action gives
`G6_E_ACTION_GATING_ACTION_REQUIRED`; invalid action fields give `G6_E_ACTION_GATING_ACTION_INVALID`.
Typed block inputs forbid unknown fields. Risk and safety-QA confidence fields remain present as null: the rules
produce outcomes and rationale, not confidence estimates, in both responses and audit records.

All three budget write ops are refused with `block_sensitive=True`, including previews. A trusted host must allow
the op and pass `dry_run=false` to write through the block. These local ledger writes need no outward-action gate.
A check/record preview writes nothing, reports `dry_run=true`, and never grants a reservation or reports an
observation as recorded. With an installed policy its code is `G6_E_OPERATION_DRY_RUN`; read failures remain refusals.

Known action profiles set risk floors; supplied risk can only raise them. Unknown actions or unknown reach need
information. A `write_file` is reversible only when the action names a `checkpoint` or `backup` (at its top level or
in `action_params`) that the gate can read itself: an absolute path that exists and is not the file being written.
Otherwise its reversibility is unknown and it needs information; a `delete_file` is irreversible whatever it names.
Declared authority is not observed authority. Secret-looking paths need human review. An irreversible,
external or real-world action without a complete verification contract blocks; missing coordination or world state
needs review. Unsupported factual preconditions can also block. The hypothesis context is used only when its existing
hypothesis switch is enabled. A person's stored key can permit a supported paid/API action with a notice; it never
overrides a block or makes a declared verification plan real.

`evaluate_action` also accepts these host-injected Python checks:

- `safeguards=Interceptor(...)`: frozen `InteractionContext`, named `Safeguard` predicates and `Outcome` values.
  Exceptions become `BLOCK`; malformed answers become `INCONCLUSIVE`. The fixed fold orders `BLOCK` above
  `HITL_REQUIRED`, `INCONCLUSIVE`, `WARN`, `ALLOW`; the result maps into the gate's decisions. Empty folds refuse.
  An explicitly configured empty interceptor blocks with `G6_E_SAFEGUARDS_INACTIVE`. With no interceptor the existing
  gate still runs, and evidence records that code with `evaluated_count=0`. Every result counts evaluated predicates.
  `shadow=True` is an explicit constructor choice: findings become notices but cannot loosen a prior hold. There is
  no environment toggle. An injected audit sink receives metadata only; no default file. Sink failure blocks even
  in shadow mode (`G6_E_SAFEGUARDS_AUDIT_FAILED`).
- `harm_judge=` takes an injected `g6.core.llm` caller. It can only tighten; unavailable or malformed judgement
  needs review (`G6_E_HARM_JUDGE_FAILED`). No default model or paid fallback.
- `csf_model=` and `csf_epsilon=` use `g6.csf.reachability_bound`. No model is constructed from history. Missing,
  invalid or excessive bounds block; a bound within epsilon says something about the supplied model only.
- `review_creator=` receives a held action and decision. A missing receipt or exception blocks
  (`G6_E_REVIEW_CREATION_FAILED`). A receipt records a request for review, never approval.
- `scope=` (also a block field) is a `scope.ReviewScope`: `scope_id`, `allowed_task_patterns` (wildcards over the
  whole action type, ignoring case, never its description), `forbidden_actions`, `risk_ceiling` (`low`, `medium` or `high`) and an
  aware `expires_at`. Outside the patterns, above the ceiling or expired needs review (`G6_E_SCOPE_OUTSIDE`,
  `G6_E_SCOPE_RISK_CEILING`, `G6_E_SCOPE_EXPIRED`); forbidden or unreadable blocks (`G6_E_SCOPE_FORBIDDEN_ACTION`,
  `G6_E_SCOPE_INVALID`). Inside it the gate's own decision stands. Unknown fields are refused.
- `scope.review_record(scope, artifact=, checks=, receipts=, approval_id=, ledger=)` reads harness-run and held-out
  receipts through `g6.evidence` (only those in `ledger`, where their runner recorded them) and a person's approval issued on the control plane for `scope.approval_scope(scope)`. It is
  `qualified-draft` at best, blocked when expired, refuted, unobserved or undecided, and grants nothing.

Configured checks require an audit write. Any write failure or malformed receipt blocks with
`G6_E_AUDIT_WRITE_FAILED`. The existing `audit_store` offers `verify_chain_report`, `export_anchor` and
`verify_chain(anchor=...)`. Reports locate the first broken position, including a missing/extra tail; an anchor
head mismatch alone cannot locate a fully rewritten chain. External anchors are exact snapshots: later legitimate
appends differ too. Keep anchors separately. Missing anchors, including legacy unanchored logs, refuse; there is no
implicit migration or re-chaining of corrupt history.

`OperationBudget.check` reserves before execution. `guarded(operation)` wraps trusted synchronous Python functions
with an explicit `run_id`; refusal, missing policy, malformed checks and exceptions prevent calling the function.
Unknown operations refuse. Failed execution still spends its reservation. `record` is observational, not a second
charge; a failed observation after execution reports that the function already ran. Policy changes never reset usage.
Missing policy is `G6_E_OPERATION_POLICY_MISSING`; exhausted limits are `G6_E_OPERATION_BUDGET_EXHAUSTED`; ledger
failures are `G6_E_OPERATION_LEDGER_FAILED`. These are count bounds, not invented probabilities.
File-backed checks, records and policy changes hold an exclusive OS lock across reload, count and append;
separate instances and processes share it. The persistent `.lock` sidecar uses `msvcrt.locking` on Windows and
`fcntl.flock` elsewhere. Lock acquisition times out after five seconds and refuses. Reports read validated snapshots
without creating the lock; a concurrent partial write may produce a refusal. Python `check` and `record` are explicit
mutating calls; the block's dry-run default does not change the decorator's reservation semantics.

## Alone and composed

Use `g6_invoke("action_gating", "evaluate", params_json)` or Python `evaluate_action(action)`.
`action_logic` supplies verification, coordination and hypothesis controls; `epistemic_integrity` grades declared
factual evidence. `g6.csf` supplies the operation vocabulary and optional model bound. Budgets use
`g6.core.ledger.ChainLedger`; safeguards feed the same `GateDecision`, not another gate. `native_ops` retains its
single-use tokens and simulated executor (`executed: false`); it is not a live execution backend.

## Failure modes it catches

- `unsafe_autonomous_action`: deterministic floors, context checks and fail-closed required audits.
- `repeated_mistake`: a supplied predicate catches the known mistake again; no automatic lesson harvesting.
- `fail_open_check`: empty configured safeguards, raised predicates, missing policies and failed dependencies refuse.
- `overstated_verification`: rule passes, model judgements and inactive checks never establish verification.
- `tampered_audit_record`: chain, indexed-field, length and head checks detect altered history; external anchors detect a rewritten internal anchor.
- `unbounded_risky_operation`: per-run reservations refuse once the person's operation limit is spent.
- `beyond_review_scope`: an action outside its declared review scope goes to a person or is blocked; a scope never
  lowers a hold.

## Blind spots

- Declared action names, evidence, contexts and probabilities may misdescribe reality. A complete plan is only text.
- A write's checkpoint is checked for being there and readable, not for holding the file's earlier content.
- Predicates and injected callers are trusted host code; do not load caller or model-written code as a predicate.
- Shadow findings do not stop otherwise permitted actions. No predicate catalogue is installed automatically.
- A person with database write access can rewrite its internal anchor; retain external anchors independently.
- All budget writers must use the same local ledger and lock; direct `ChainLedger` writes bypass that coordination.
  Keep the lock file in place. Hosts must bind run identities and preserve the ledger; changing a run ID or replacing
  an unanchored ledger defeats aggregate limits. This is not a distributed quota service.
- Protected repository changes belong to the coding gate. Count availability grants no action approval.
- Catch rates, false alarms and real-world safety are not established. It declares no failure mode of its own.
- A review scope is the caller's declaration: the gate cannot tell whether a person really reviewed those bounds, and
  an action type can be misnamed. A record's receipts count only under `g6.evidence`'s grounding rules.

## What it needs

Existing Python dependencies only: pydantic and standard-library SQLite. No new extras or outside programs.
The audit database and default count ledger live under `g6.core.storage.state_path` in `$G6_HOME`; Python callers
can inject in-memory stores. Optional model calls require the person's own `g6.core.llm` caller.

## Examples

```g6-example op=evaluate
{"action":{"action_type":"send_email","target_resource":"recipient@example.invalid"}}
```

This blocks: the outward action lacks a verification plan.

```g6-example op=explain
{"action":{"action_type":"write_file","target_resource":"notes.txt"}}
```

```g6-example op=budget_check
{"run_id":"example","operation":"compute"}
```

```g6-example op=budget_record
{"run_id":"example","operation":"compute"}
```

```g6-example op=budget_report
{"run_id":"example"}
```

These check/record examples are sensitive dry-run previews and require host permission. They reserve and record nothing. With no installed policy, checks/reports refuse; a real observation without a reservation refuses.

```g6-example op=budget_policy
{"policy":{"limits":{"compute":2}}}
```

This sensitive preview requires host permission even though it defaults to dry-run.

```python
from g6.action_gating import OperationBudget, evaluate_action
from g6.action_gating.audit import ActionGateAuditLogger
from g6.action_gating.safeguards import Interceptor, Outcome, Safeguard
from g6.core.ledger import ChainLedger

budget = OperationBudget({"compute": 1}, ledger=ChainLedger())
assert budget.check("example", "compute").remaining == 0
assert budget.check("example", "compute").decision == "block"
checks = Interceptor([Safeguard("require-review", lambda ctx: Outcome.HITL_REQUIRED)])
result = evaluate_action({"action_type": "read_file", "target_resource": "notes.txt"},
                         safeguards=checks, audit_logger=ActionGateAuditLogger())
assert result.gate_decision.decision == "require_human_review"
assert result.completion_state == "blocked-escalated"

from g6.action_gating import ReviewScope

notes = ReviewScope(scope_id="notes", allowed_task_patterns=["read_file"], risk_ceiling="low",
                    expires_at="2099-01-01T00:00:00+00:00")
held = evaluate_action({"action_type": "write_file", "target_resource": "notes.txt"}, scope=notes,
                       audit_logger=ActionGateAuditLogger())
assert "G6_E_SCOPE_OUTSIDE" in held.gate_decision.reason_codes
```

## The four open problems

- **Learning signals**: audit evidence records optional checks; the count ledger records reservations and refusals.
  Not yet: automatic learning from either trail.
- **Evaluators the model cannot influence**: deterministic floors stay authoritative; optional judges only tighten.
  The agent still supplies facts. Not yet: pinned rules or independently observed action contexts.
- **Transfer by failure mode**: predicates and count caps apply across actions through shared types.
  Not yet: measured performance across unseen task families.
- **Enough held-out cases**: Not yet: regressions and survival cases do not establish catch or false-alarm rates.
