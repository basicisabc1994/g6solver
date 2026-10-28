# G6

**Build agents from parts you can check.** G6 is an open-source library of AI components for reasoning,
evidence, verification and action control. Its north star is **AI for AI**: agents construct applications,
propose redesigns and test hypotheses against frozen checks, with evaluation and activation outside the candidate's
writable scope. Components and workflow tools are available today; benefits for a particular application require testing.

Every result carries one of three labels, and G6 never upgrades a label it did not earn:

| Label | Meaning |
|---|---|
| `verified` | A registered check was independently reproduced for the stated artifact and claim scope; not complete correctness. |
| `qualified-draft` | Usable, but something is unproven, and the result says what. |
| `blocked-escalated` | Not safe to rely on; the result says what is needed. |

## What the checks establish

- **The rule for labels.** `verified` should need independent evidence: a check the producer did not write, frozen
  before the work, that G6 has seen fail a planted wrong answer. A check you or your agent wrote is not independent,
  so its result is `qualified-draft` at best, and a combined result takes the weakest label. At every door (the MCP
  server, `g6 invoke`, `g6.invoke` in Python, an agent's tool calls, a navigator plan's steps, a pipeline of steps, a
  bundle and the harness) a `verified` stands only when G6's own code re-runs a check there, against what you gave
  it, and reproduces it; any other is held at `qualified-draft`, a re-check that finds a mismatch blocks, and the
  reply says what was claimed. No component you can reach through those doors has such a re-check yet, so nothing that comes
  through them is `verified` today; the one path that can be is G6's file-integrity check (`g6.integrity`) called
  from Python with your own pin. `get_registry().invoke` is the raw library call beneath the doors: it returns a
  component's own answer, unchecked. From the MCP server, `g6 invoke` and `g6.invoke`, `ok` is false whenever the
  state is `blocked-escalated`; a blocked result keeps its answer in `result`. G6 re-checks artifacts with its own
  code; it does not defend against malicious code in its own process.
- **Checks are hashed first.** In the harness, each check's source text is hashed before the work runs, so editing a
  check afterwards is caught. The hash covers the source only, not values the check reads from outside it, and the
  check runs in the same process as the work: it catches mistakes, not code written to defeat it.
- **Results are published, misses included,** on the [Research](site/docs/research.md) page, as a worked example.
- **Local-first and open.** No telemetry and no server of ours. Data goes only to the model you choose.
- **Small parts.** 34 components in the core harness, each usable on its own, extensions that join the library one at
  a time, and 11 bundles that package them for one problem each.

## Install

```bash
git clone https://github.com/basicisabc1994/g6solver && cd g6solver
pip install ".[all]"        # Python 3.12 or 3.13
g6 doctor                   # read-only installation check
g6 setup --client codex      # register this installation and install its skill; or --client claude
```

Not a programmer? Ready-made **bundles** (double-click apps) come with the first release; see the
[install guide](site/docs/install.md).

## Use it

**From a coding agent (MCP), the main surface:**

```bash
g6 setup --client claude     # user-scoped registration, absolute Python path
g6 setup --client codex      # install using-g6 for Codex too
```

The agent gets discovery, `g6_compose`, `g6_review_patch`, and `metaharness_*` tools for constructing,
measuring and revising applications. Long runs return job IDs; poll `metaharness_job`. [AGENT_GUIDE.md](AGENT_GUIDE.md) is written for the agent itself.

G6 runs untrusted code for you only inside an operating-system boundary: bubblewrap on Linux (install the `bubblewrap`
package), an AppContainer on Windows (after the one-time access grant `g6 doctor` prints). `g6 doctor` says which you
have. Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows
before the one-time access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a
repository's tests) unless you allow it once with g6 setup. Code you allow then has your own rights: it can read your
key file ~/.g6/.env and your other files, and its network guard works inside Python only. You are responsible for
what you run. `G6_ALLOW_UNCONFINED=1`, set where you start G6, does the same as allowing it; `G6_REQUIRE_ISOLATION=1`
refuses such code whatever you allowed.

Most checks need no model. The few steps that do use the model you set in your key file, `~/.g6/.env`, whose values are local preferences under a trusted host: for example the line `G6_LLM_PROVIDER=claude_code` (your own Claude Code sign-in), `G6_LLM_PROVIDER=codex`
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

## Build and test an application

Start with [the AI-for-AI workflow](docs/AI_FOR_AI_WORKFLOW.md). The loop is:
`metaharness_start` ? confirm ? diagnose ? propose code with a hypothesis ? evaluate ? decide ? client consent ? run.
Use `g6_compose` for explicit component connections. `g6_review_patch` is the first reference application: give
it the patched repository, the task, and the requirements the patch must meet, each with the tests that check it.
You can also give it the task's intent: its success criteria become requirements and its non-goals and failure
criteria behaviour the patch must keep, each not established until a requirement with the same text names the
tests that check it.
It runs those tests inside an operating-system boundary, compares the changed tests, test helpers and test-runner
files with the base commit for the signs of weakening G6 knows how to find (a static comparison: it cannot show
that the tests are as strong as before), then runs the other tests that touch the changed modules, and returns one
outcome: test integrity compromised, defect detected, insufficient evidence, or supported within scope. Supported
within scope means every stated requirement passed its stated checks; it does not show that the patch does
everything the task needs. Each requirement gets its own evidence, and merging always needs a separate approval.
You can also propose checks of your own (`proposed_checks`, at most 20 test files): G6 runs each inside the same
boundary on a copy of the patch and on one of the base commit's code, and says whether it found a counterexample.
A proposed check never passes a requirement, and a failing one gives at most insufficient evidence, with a
reproduction you can run with plain pytest.
G6 keeps its record of its own test runs (test receipts) in a file your user account owns: it stops a caller's
claimed results from counting as G6's, not a program running as you.
Enable it explicitly with `g6 setup --client codex --allow patch_review.review`.

Fewer than 30 held-out cases means exploration only. Generated-code activation still requires independently
configured safety checks; source support and passing tests cannot replace them. A local file is not a human-only
authorization boundary: an agent with unrestricted same-user shell access can edit it. Use a trusted host or
separate account to enforce that separation.

The public `v0.1.0` tag is immutable. See [the v0.1.1 release gates](docs/releases/v0.1.1.md) for known limitations
and the checks required before recommending a new release.

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
