# G6

**Hold your AI agent to the evidence.** G6 is an open-source harness that checks an agent's work, gates its risky
actions, and labels every result for exactly what was shown. It runs on your computer, with a local model or your own
key. Apache-2.0, free, no account and no hosted service.

Every result carries one of three labels, and G6 never upgrades a label it did not earn:

| Label | Meaning |
|---|---|
| `verified` | Every required check passed on independent evidence. |
| `qualified-draft` | Usable, but something is unproven, and the result says what. |
| `blocked-escalated` | Not safe to rely on; the result says what is needed. |

## Why you can rely on what it says

- **Labels never rise.** `verified` needs independent evidence: a check the producer did not write, frozen before the
  work, that G6 has seen fail a planted wrong answer. A check you or your agent wrote is not independent, so its result
  is `qualified-draft` at best. When results are combined, the overall label is the weakest one.
- **Checks are frozen first.** In the harness, each check's code is hashed before the work runs, so success cannot
  be redefined afterwards.
- **Results are published, misses included,** on the [Research](site/docs/research.md) page, as a worked example.
- **Local-first and open.** No telemetry and no server of ours. Data goes only to the model you choose.
- **Small parts.** 34 components in the core harness, each usable on its own, extensions that join the library one at
  a time, and 11 bundles that package them for one problem each.

## Install

```bash
git clone https://github.com/basicisabc1994/g6solver && cd g6solver
pip install ".[all]"        # Python 3.12 or 3.13
g6 doctor                   # read-only installation check
```

Not a programmer? Ready-made **bundles** (double-click apps) come with the first release; see the
[install guide](site/docs/install.md).

## Use it

**From a coding agent (MCP), the main surface:**

```bash
claude mcp add g6 -- g6-mcp     # Claude Code
codex mcp add g6 -- g6-mcp      # Codex
```

The agent gets five discovery tools (`g6_help`, `g6_components`, `g6_find`, `g6_describe` and `g6_invoke`) and seven
`metaharness_*` tools for tuning. [AGENT_GUIDE.md](AGENT_GUIDE.md) is written for the agent itself.

Where no operating-system backend confines it (Linux and macOS today, and Windows before the one-time access grant),
untrusted code that G6 runs for you (a patch, a generated script, a repository's tests) has your own rights: it can
read your key file ~/.g6/.env and your other files, and its network guard works inside Python only. You are
responsible for what you run.

Most checks need no model. The few steps that do use the model you set in your key file, `~/.g6/.env`, which only you
write: for example the line `G6_LLM_PROVIDER=claude_code` (your own Claude Code sign-in), `G6_LLM_PROVIDER=codex`
(your Codex sign-in), `G6_LLM_PROVIDER=ollama` (a model on your computer), or a provider and its key. With none set,
those steps say so and your agent can do them itself.

**From the command line:**

```bash
g6 find "check whether my sources support this claim"
g6 invoke grounding check_claim '{"claim": "Refunds take 30 days", "sources": {"policy": "Refunds take 14 days."}}'
g6 bundle                   # list bundles; g6 bundle safe-actions opens one in your browser
```

**From Python:**

```python
from g6.action_gating import evaluate_action

ev = evaluate_action({"action_type": "send_email", "target_resource": "all-customers",
                      "reversibility": "irreversible", "blast_radius": "public"})
print(ev.gate_decision.decision)    # block: irreversible and public, with no verification plan
```

## What is in it

- **Components** ([catalogue](site/docs/components.md), generated from the code). Each one works alone and declares
  any other component it imports. Those with an MCP block can be called over MCP and the CLI, and the catalogue's
  *surface* column says which; the rest are Python-only for now.
  - Evidence and claims: `grounding`, `epistemic_integrity`, `evidence`, `invariants`.
  - Actions and runtime safety: `action_gating`, `action_logic`, `safety_engine`, `guards`, `goal_engine`.
  - Coding agents: `coding_agent_gate`, `workforce`.
  - Tasks, plans and context: `intent_engine`, `reliability_pipeline`, `solver`, `navigator`, `context`.
  - Reasoning and search: `reasoning`, `debate`, `abstraction`.
  - Formal methods, shown together on the Components page: `formal`, `algebra`, `lean`, `model_checking`,
    `datalog`, `prolog`, `cegis`, `csf`, `causal`, `optimisation`, `graph_reasoning`.
  - Governed learning: `learning`, `self_training`, `distillation`, `metaharness`.
- **The harness** (`g6.harness`): the one place components are composed. A standard (goal plus checks) runs through
  intake, plan, execute, adjudicate and remember.
- **Extensions**: components outside the core, each with its tests, its skill and the
  failures it guards against. The [catalogue](site/docs/components.md) lists the ones in the library now.
- **Bundles** (`g6.bundles`): 11 single-problem apps that open a private page on your computer.
- **The meta-harness** (`g6.metaharness`): tunes G6 to a task you repeat. It proposes changes to how G6 runs it, tests
  each on held-out examples you trust at equal compute, and activates nothing until a person approves. When a change
  fails, it says why and what to try next: root causes, and the G6 components that address them
  ([Tune it to your work](site/docs/metaharness.md)).
- **A stop switch** (`g6.core.stop`): `G6_STOP`, or a `STOP` file in `$G6_HOME`.
- **Research** (`g6.research`, off unless `G6_ECHA_RESEARCH=1`): an evidence kernel, a bridge from missions to
  evidence certificates (partly ported) and governed adaptation. The specifications are in [docs/theory](docs/theory).

## Status of each capability

| Capability | Label |
|---|---|
| Result labels and the envelope on every return, checked by `g6.invariants` | `implemented` |
| Claim checking against your sources (`grounding.check_claim`) | `implemented`. Studied on one public dataset; the result is on the [Research](site/docs/research.md) page |
| Action gating: allow, ask a person, or block (`action_gating`) | `implemented`. Studied on a public agent benchmark; the result, its miss included, is on the [Research](site/docs/research.md) page |
| End-to-end harness: frozen checks, adjudication, memory of failed checks (`g6.harness`) | `implemented` |
| Checks on a coding agent's "fixed it" (`coding_agent_gate`, `workforce`, the agent-change-check bundle) | `implemented` |
| Governed learning: hints credited from paired runs, a promotion gate for harness changes | `implemented`. Earlier self-training benchmark gains are withdrawn pending re-validation |
| Bundles (11) | `implemented`. Downloads come with the first release; their study has not run (see [Research](site/docs/research.md)) |
| Research kernel (the evidence kernel, governed adaptation, the mission bridge) | `wired-but-inert`: off unless `G6_ECHA_RESEARCH=1` |
| G6 helps independent coding agents on held-out tasks | `aspirational`: protocol published, not run |
| Meta-harness: tune G6 to your own recurring work (`g6.metaharness`) | `implemented`. The studies run with it are on the [Research](site/docs/research.md) page, as a worked example |

Until a pre-registered study says otherwise, treat G6 as careful engineering, not as a proven uplift. The studies run
so far, including how G6 with its default settings compares with one direct model call, are on the
[Research](site/docs/research.md) page, as a worked example.

## Develop

```bash
pip install -e ".[dev,all]"
pytest -q && ruff check src tests && lint-imports
python tools/gen_catalog.py     # after changing a component
```

Licence: Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)).
