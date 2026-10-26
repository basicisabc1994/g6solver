---
name: g6-metaharness
description: Use when a repeated task fails, costs too much or runs slowly, and you have labelled development and held-out cases for testing proposed changes before a person activates them.
---

# G6 meta-harness

A person confirms a workflow: its task, checks, oracle, cases and evidence policy. Strategies propose acting profiles;
the existing meta-gate measures them on held-out cases and recommends at best (`qualified-draft`). Proposing,
measuring and recommending never activate a profile. The workspace keeps the existing hash-chained run ledger.

## When to use it

- Improve a repeated task using declared models, prompt or skill text, checks, hints and pipeline steps.
- Compare measured quality, cost and latency; diagnose failed or unchecked development steps.
- Keep approved versions reversible and watch their live results and version scores.

## When not to use it

- A one-off task, missing labelled cases, or checks that cannot distinguish an answer from an empty output.
- Certifying security or proving generated code correct. A skill scan and a passing suite have limited coverage.
- Changing judges through a proposal: a person changes and reconfirms the workflow first.

## Python API

- `adapt`: create the handle in `workspace=`; default state uses `g6.core.storage.state_path`.
- `MetaHarness.confirm`: freeze the workflow and judging code, recording the supplied person's name.
- `MetaHarness.baseline`: measure the current version on dev and held-out, recording version scores.
- `MetaHarness.candidate`: parse a profile; proposals carry parent digests and never change the active version.
- `MetaHarness.propose`: run a strategy against development failures and measured traces only.
- `MetaHarness.evaluate`: run paired held-out cases and controls through the declared executor.
- `MetaHarness.decide`: recommend, refuse or block; show quality, measured dollars and latency together, unknown
  costs explicitly. Required missing measurements refuse; measured regressions remain visible even on refusal.
- `MetaHarness.activate`: person approval after a recommendation; by digest, review its card and resubmit the
  card's candidate digest. The proposer's name cannot approve.
- `MetaHarness.rollback`: return to an approved version, even while stopped.
- `MetaHarness.run`: run a live task; preserve its harness result.
- `MetaHarness.diagnose`: explain development failures and available remedies.
- `MetaHarness.monitor`: existing live drift demotion plus version-score diagnostics and optional promotion caps.
- `MetaHarness.promotion`: optional readiness policy; accepts a `PromotionRequest`, or only holds/lowers a mode
  without one. Each upward step requires its own latest full held-out report, unused for promotion, its own
  threshold and a named person's approval claim. Modes grant no execution permission or gate bypass.
- `Workflow`: frozen judges, declared executors/models and optional `promotion_policy`. Code patches need a trusted
  `patch_evaluator(base, candidate, artifacts)` that runs independent checks and returns measured `safety_violations`,
  `injection_resilience_not_worse`, `evaluator_confidence` and check names in `checked_by`. Configure it, reconfirm
  and re-evaluate; missing, failed or malformed evidence blocks promotion. Reports bind observations to both profiles.
  The callback is judging code, like the oracle; run untrusted code through the existing sandbox.
- `Model`: injected caller; optional `usage` reads each attempt's receipt, returning missing fields when unknown.
  Retry costs/tokens are summed only with complete receipts; missing attempt receipts leave their totals unknown.
- `Profile`: acting settings including `prompt`, `skill_text`, routing, extra checks and pipeline steps.
- `MeasuredStep`: measured duration, optional tokens/cost, dependencies and `checked_by`; unchecked means `None`.
- `MeasuredTrace`: one dev trace; `validate_dag` rejects cycles, missing dependencies and duplicate step names.
- `CostStrategy`: rank measured or dated-price spend through `g6.llm_router`, proposing cheaper declared routes.
- `SpeedStrategy`: propose declared alternative models on one reused critical path, stopping at `max_candidates` (3).
- `QualityStrategy`: add a declared optional check or repair/second-look pipeline step for failed/unchecked steps.
- `FailureTraceStrategy`: one bounded injected caller reads dev failures and proposes prompt/skill text. It records
  calls and optional adapter-reported spend; code-bearing text must pass a conservative scan. Text is never executed.
  Missing models and unavailable backends retain their error code and remedy; malformed replies name the required format.
- `EvidencePolicy`: thresholds and reuse cap; `staged_evaluation=True` rejects below 80% at 5 cases or 60% at the
  cumulative first 20, then runs all cases. Survivors still need the full meta-gate. Incompatible with `looks>1`.
- `pool.Pool`: `novelty_weight` adds mean disagreement on aligned measured dev outcomes to parent selection.
- `PromotionPolicy`: required pass rates for draft, assisted and autopilot, in nondecreasing order; start in shadow.
- `PromotionRequest`: typed mode, report digest, approval name and security claims; unknown fields/modes refuse.
- `detect_anchor_decoupling`: paired development versus held-out score changes across versions.
- `detect_agreement_collapse`: exact comparison of agree/disagree counts for any named judge pair.

## MCP tools

There is no registry block or `g6_invoke` op. The seven existing tools are unchanged:
`metaharness_inspect`, `metaharness_propose`, `metaharness_evaluate`, `metaharness_decide`, `metaharness_diagnose`,
`metaharness_approve`, `metaharness_rollback`. A strategy runs over MCP only when the server operator writes it into
the workflows file the server loads (`G6_METAHARNESS`); that is the operator's opt-in. Its model caller is
`g6.core.llm`, with keys only from `~/.g6/.env`.
The existing `space` remains available; tool arguments never import code. Promotion policy stays Python-only;
no MCP op reaches it. These dedicated tools are not controlled by `G6_MCP_SENSITIVE`; there is no automatic paid fallback.

Approval tools ask the person through MCP elicitation and reject the proposing session. If the client cannot ask,
use the refusal's instructions for approval from another session or the person's Python process.

## What a person must approve

Confirmation, activation, rollback, online tuning and audit-rate changes retain their existing person checks.
Every upward readiness step separately records an approval claim; security declarations remain claims and can
only lower a cap. An agent must hand decisions to a person, never invent `accepted_by`. Readiness is
`qualified-draft` at best. The monitor may demote without approval, never promote.

## Alone and composed

Use the Python API or existing dedicated MCP tools. `g6.harness` runs checks, `g6.learning` supplies candidate hints,
`g6.llm_router` supplies dated price advice, and `g6.core.ledger` records proposals, evaluations and warnings.
`code_factory` uses `g6.core.sandbox`; `coding.coding_workflow` uses `g6.workforce.repo_tests`.
No archive execution engine, scheduler, installer or second gate is included.

## Failure modes it catches

- `repeated_mistake`: diagnosis and development proposals tested on held-out cases.
- `inconsistent_workflow_model`: trace DAG validation and explicit check provenance.
- `excessive_token_use`: measured spend proposals; no fixed predicted saving or unknown price treated as zero.
- `slow_workflow`: dependency-aware critical path proposals, judged by actual latency and quality.
- `weak_link`: added declared checks/repair steps for failed or unchecked development steps.
- `hidden_tradeoff`: quality, dollars and latency shown together by the gate.
- `unseen_early_warning`: anchor decoupling and agreement-collapse diagnostics write deduplicated ledger tickets.
- `untested_prompt_change`: failure-driven prompt/skill proposals still require held-out evidence and approval.
- `unchecked_self_change`: staged rejection, measured parent selection, lineage and separate person approval;
  readiness declarations cannot substitute for measurements.

## Blind spots

Prices are dated estimates, never receipts. Missing receipts leave cost unknown; legacy workflows without a cost
objective can still be judged on their original objectives. Trace timing adds measurement overhead. Built-in
synchronous calls form a serial dependency chain in first-use order; concurrent custom executors need their own
faithful DAG observations. Intermediate outputs stay unchecked unless independently measured. Trace inputs are
claims for proposing, never admission evidence.

Version diagnostics require complete baselines for multiple approved versions. Dependence, reused cases and
adaptive tuning limit statistical calibration; increased agreement may reflect real improvement. Warnings do not
prove shared error. The skill-code scan can reject harmless text and cannot establish semantic security.

A named approval is recorded as given, not authenticated. Whoever can rewrite the workspace can rewrite evidence.
Repeated access can fit held-out outcomes; reuse is capped. Exact duplicate detection misses paraphrases. Live
checks have no answer key. Novelty favours diversity, not correctness; staging can reject unevenly ordered tasks.

## What it needs

These merges add no outside program, key or optional dependency. Models are injected `g6.core.llm` callers.
The MCP server needs `g6[mcp]` and an owner-declared workflow. Existing LoRA training uses `g6[lora]`
(torch, transformers, peft), a local model and an adapter loader. Repository tests use pytest through
`g6.workforce.repo_tests` with your permissions, without an operating-system sandbox. Code execution retains
`g6.core.sandbox`'s limits; the text proposer does not execute code.

## Examples

An offline loop with an injected fake model. Odd sums improve only after the prompt changes:

```python
import itertools
import re
from pathlib import Path

from g6.core.llm import FakeLLM
from g6.harness import Check
from g6.metaharness import Model, Suite, SuiteCase, Workflow, adapt


def reply(system, user):
    a, b = (int(n) for n in re.search(r"(\d+)\+(\d+)", user).groups())
    return str(a + b + 1 if "CAREFUL" not in system and (a + b) % 2 else a + b)


def cases(prefix, n, start):
    return tuple(SuiteCase(f"{prefix}{i}", f"What is {start + i}+{3 + i % 7}?", str(start + i + 3 + i % 7))
                 for i in range(n))


number = Check("the answer is a whole number", lambda out: out.strip().isdigit(), check_id="number",
               known_bad=("twelve", ""), known_good="12")
wf = Workflow(name="sums", task="Compute this. {input} Reply with the number only.", checks=(number,),
              oracle=lambda out, expected: out.strip() == expected,
              suite=Suite(dev=cases("d", 10, 10), held_out=cases("h", 40, 500),
                          provenance={"dev": "caller", "held_out": "independent"}),
              models={"local": Model(FakeLLM(reply))})
mh = adapt(wf, workspace=Path("metaharness-sums").resolve(), clock=itertools.count(0.0, 0.001).__next__)
assert mh.envelope["completion_state"] == "qualified-draft"
assert mh.confirm(accepted_by="Ada")["warning_card"]["code"] == "G6_E_META_CONFIRMED"
assert mh.baseline()["completion_state"] == "qualified-draft"

proposal = mh.candidate({"routing": {"answer": "local"}, "prompt": "CAREFUL: add digit by digit."},
                        proposer="Bo")["proposal"]
report = mh.evaluate(proposal)["report"]
decided = mh.decide(report, proposal)
assert (decided["completion_state"], decided["decision"].verdict) == ("qualified-draft", "recommend")

refused = mh.activate(decided["decision"], accepted_by="Bo")          # the proposer cannot approve
assert refused["warning_card"]["code"] == "G6_E_META_APPROVER_IS_PROPOSER"
assert mh.activate(decided["decision"], accepted_by="Ada")["head_version"] == 1
assert mh.run("What is 2+3?")["profile_version"] == 1
assert mh.rollback(0, accepted_by="Ada")["warning_card"]["code"] == "G6_E_META_ROLLED_BACK"
```

## The four open problems

- **Learning signals**: existing ledger evaluations, measured dev traces and version-warning tickets; no second
  bus or trace store. Optional online tuning still produces candidates only.
- **Evaluators the model cannot influence**: confirmed workflow and pinned judging code; proposals cannot edit
  judges. Staged evaluation only rejects early. Supplied approvals/security facts are explicitly claims.
- **Transfer by failure mode**: strategies target observed failures; imported profiles still face the destination's
  held-out gate. No demonstrated transfer to unrelated task families.
- **Enough held-out cases**: default policy requires 30 cases, 5 disagreements and a sign-test win at 0.05/5;
  baseline estimates power, reuse is bounded and every surviving staged candidate faces the full gate.
