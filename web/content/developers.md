# For developers and coding agents

**Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.**

G6 is a Python library (3.12 or 3.13). Every component works on its own and declares any other component it uses.
`g6.harness` is the one place they are composed: a standard (a goal and its checks) runs through intake, plan,
execute, adjudicate and remember.

```bash
# From a clone of the repository (G6 is not on PyPI):
pip install .               # core: pydantic + returns only
pip install ".[all]"        # optional extras: MCP server, Anthropic SDK, abstraction tools, Z3
g6 doctor                   # read-only check of the installation
g6 setup --client claude --allow patch_review.review    # or --client codex (run it yourself)
```

Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows before the one-time
access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a repository's tests) unless you
allow it once with g6 setup. Code you allow then has your own rights: it can read your key file ~/.g6/.env and your
other files, and its network guard works inside Python only. You are responsible for what you run. `g6 doctor` says whether that code runs confined on your computer: on Linux it needs
bubblewrap (the `bubblewrap` package), and on Windows it prints the one-time commands that grant the access. Run
`g6 setup` once yourself to make the choice for this computer.

## Run a task through the harness

```python
from g6.harness import Check, Standard, run

standard = Standard(
    goal="Summarise the refund policy in one sentence",
    checks=(Check("states the 14-day refund window", lambda out: "14 days" in out,
                  known_bad=("Refunds take 30 days.",), known_good="Refunds take 14 days."),),
)
result = run(standard, executor=lambda contract: "Refunds are issued within 14 days of a return.")
print(result["completion_state"])   # qualified-draft: the check passed, but you wrote it, so it is not independent
```

The checks are hashed before the executor runs. Each check must fail its `known_bad` outputs and pass its
`known_good` one, or it is reported as broken. A failed check is remembered for that task family under `$G6_HOME`,
and a later standard that drops it is refused until the check is back or a person waives it.

## Use a component directly

```python
from g6.action_gating import evaluate_action

ev = evaluate_action({"action_type": "delete_file", "target_backend": "local", "target_resource": "notes.txt"})
print(ev.gate_decision.decision, ev.completion_state)
```

```python
from g6.self_training import HarnessPatch, PatchEvalReport, decision_matches, govern_patch_promotion

patch = HarnessPatch(agent_id="proposer", target_surfaces=["documentation"], touched_paths=["docs/x.md"],
                     diff="--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n")
decision = govern_patch_promotion(
    patch,
    PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.05,
                    evaluator_confidence=0.9, independent_eval=True, evaluator_id="reviewer"),
    autonomous_low_risk=True,                           # opt in: low-risk changes may go ahead without a person
)
print(decision.decision, decision.reliability_label)   # promote_auto qualified-draft
assert decision_matches(decision, patch)                # apply only the change that was decided on
```

Without `autonomous_low_risk=True`, every change waits for a person: the same call returns `require_approval`.

## Components at a glance

The [catalogue](components.md) is generated from the code, so it always matches what you install.

| Area | Components |
|---|---|
| Evidence and claims | `grounding`, `epistemic_integrity`, `evidence`, `invariants` |
| Actions and runtime safety | `action_gating`, `action_logic`, `safety_engine`, `guards`, `goal_engine`, `csf` |
| Coding agents | `coding_agent_gate`, `workforce` |
| Tasks, plans and context | `intent_engine`, `reliability_pipeline`, `solver`, `navigator`, `context` |
| Reasoning, logic and search | `reasoning`, `debate`, `formal`, `cegis`, `abstraction` |
| Governed learning | `learning`, `self_training`, `distillation` |

A component with an MCP block also has a command-line surface, and the [catalogue](components.md) says which ones do. The others
are Python-only for now: `g6 describe` shows no ops for them, and `g6 invoke` answers `G6_E_NO_BLOCK`.

Outside the components: `g6.core.stop` (the stop switch), `g6.core.snapshots` (`g6 state`), `g6.integrity` (file
hashes against a pinned manifest), `g6.triage` (Python error triage) and `g6.operator` (a budgeted loop with a
simulated executor and an approval queue a person decides). `g6.research` is off unless `G6_ECHA_RESEARCH=1`.

## Use it from your coding agent (MCP)

```bash
g6 setup --client claude --allow patch_review.review    # or --client codex; give --client twice for both
```

Setup registers this installation with the client, with this installation's Python, installs the `using-g6` skill
for it, allows patch review, and ends by showing the first review call. If the client already has an entry called
`g6`, setup stops; remove it with `claude mcp remove --scope user g6` (or `codex mcp remove g6`) and run setup again.
For another client, register `g6-mcp` and give it `G6_MCP_SENSITIVE=patch_review.review` in its environment.

Six discovery tools: `g6_help`, `g6_components`, `g6_find`, `g6_describe`, `g6_invoke` and `g6_compose`; `g6_review_patch`;
and thirteen `metaharness_*` tools for [tuning](#tune-g6-to-your-workflow). The agent finds a component
by describing its task, reads what the component can do and how mature it is, then calls one op. Every reply carries
`completion_state`, a `warning_card` with a `G6_E_*` code, `evidence`, `request_id` and `run_id`. An op a component
marks sensitive is refused over MCP unless the person who runs the server lists it in `G6_MCP_SENSITIVE`.

`g6_review_patch` (allowed with `patch_review.review`) assesses a Python patch against the requirements you state, each
with the tests that check it, in test runs inside an operating-system boundary. It returns one outcome: test integrity
compromised, defect detected, insufficient evidence, or supported within scope. Supported within scope means every
stated requirement passed its stated checks, not that the patch does everything the task needs. Each requirement gets
its own evidence, and merging always needs a separate approval. It returns a job: poll `metaharness_job`. The reply's
`assessment.report` is the review as Markdown, for the agent to show the person verbatim: what G6 reproduced first,
then what is not established, then what the person decides. The `using-g6` skill tells the agent to fix what G6
reproduced, add each earlier reproduction as a stated check, and review again, for at most three rounds.

## Choose a model

Components that call a model take an `LLMCaller`. Pick one explicitly — G6 never guesses a paid provider:

| `G6_LLM_PROVIDER` | Notes |
|---|---|
| `none` (default) | Calls fail with a named error; pass your own caller. |
| `ollama` | Local and free. `OLLAMA_BASE_URL`, `G6_LLM_MODEL` (default `gpt-oss:20b`). |
| `anthropic` | Official SDK (`g6[anthropic]`), default model `claude-opus-5-5`. |
| `openai` / `openrouter` | OpenAI-compatible; `G6_LLM_MODEL` required. |

`G6_DISABLE_LLM` (any value) turns every model call off.

Keys go in your key file, `$G6_HOME/.env` (by default `~/.g6/.env`): one `NAME=value` per line, written by you and
never by G6. G6 reads `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY` only from that file, never from
the environment or a project's `.env`, so a key there is your approval for G6 to use it. The settings above may sit in
the same file; the environment overrides them. `g6 doctor` names what the file holds without showing a value, and a
snapshot never copies it.

```python
from g6.grounding import TextRetriever, check_claim

r = TextRetriever.from_texts({"policy.md": "Refunds are issued within 14 days of a return."})
print(check_claim("Refunds are issued within 30 days", r).status)   # conflict
```

## Stop it

Set `G6_STOP` to any value, or create a file named `STOP` in `$G6_HOME`. The harness, the workforce door, the safety
engine, the operator loop and the self-training cycle check it and start no new work. It is a check, not a
preemption.

## Tune G6 to your workflow

`g6.metaharness` (`implemented`) proposes changes to a workflow's profile (model per step, budgets,
prompt, hints, extra checks, pipeline steps, admitted distillates, a code overlay, an optional LoRA adapter), tests each
on held-out cases at equal compute through `g6.harness`, and activates nothing without a person's approval. When the
gate refuses, the reply carries `recovery` (root causes and remedies, each marked as a candidate the meta-harness can try
or a decision for a person), and `mh.diagnose()` maps the dev failures to the G6 components that address them. It is
built on `g6.reasoning`'s sign test, `g6.learning`'s hints, `g6.context`, `g6.self_training`'s patch gate and the
hash-chained ledger and vault in `g6.core`. Over MCP: `metaharness_*` tools, where approval always asks the person. See
[Tune it to your work](metaharness.md). The studies run with it are on the [Research](research.md#the-meta-harness)
page, as a worked example.
