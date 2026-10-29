# G6

**Build agents from parts you can check.** G6 is an open-source library of AI components for reasoning,
evidence, verification and action control. Its north star is **AI for AI**: agents construct applications,
propose redesigns and test hypotheses against frozen checks, with evaluation and activation outside the candidate's
writable scope. Components and workflow tools are available today; benefits for a particular application require testing.

**Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.**

The first use is a check on a coding agent's work. Before your agent (Claude Code or Codex) says a Python change is
done, it calls `g6_review_patch`: G6 runs the tests that check each requirement inside an operating-system boundary,
looks for signs that the tests were weakened, and returns a report your agent shows you as it is. Your agent fixes
what G6 reproduces and asks again. Merging stays your decision. Patch review makes no model call unless the caller
turns one on.

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
- **Small parts.** 37 components in the core harness, each usable on its own, extensions that join the library one at
  a time, and 11 bundles that package them for one problem each.

## Install

You need Python 3.12 or 3.13, git, and Claude Code or Codex. Run these yourself, in a terminal, not through your
agent:

```bash
git clone https://github.com/basicisabc1994/g6solver && cd g6solver
python -m venv --copies .venv                           # G6's own environment, with a copy of Python
source .venv/bin/activate                               # PowerShell: .venv\Scripts\Activate.ps1
pip install ".[all]"
g6 doctor                                               # read-only: what this computer offers G6
g6 setup --client claude --allow patch_review.review    # or --client codex; give --client twice for both
```

G6 gets an environment of its own, made with `--copies`: on Linux, G6 cannot yet tell its own Python from a project
environment linked to the same one, and refuses to review then.

`g6 setup` registers this installation with the client (`claude mcp add` or `codex mcp add`, with this installation's
Python, started with `-P` so that the packages in the project's folder never replace G6's own), installs the `using-g6`
skill for it, and allows patch review, the one sensitive operation the first review needs. Restart the client
afterwards. Its last lines show the first review call.

If the client already has an entry called `g6`, setup leaves it as it is and stops. See it with `claude mcp get g6` (or
`codex mcp get g6`). To replace it, remove it with `claude mcp remove --scope user g6` (or `codex mcp remove g6`), then
run `g6 setup` again.

G6 runs a patch and your repository's tests for you only inside an operating-system boundary: bubblewrap on Linux
(install the `bubblewrap` package), an AppContainer on Windows (after the one-time access grant `g6 doctor` prints).
`g6 doctor` says which you have. Where no operating-system boundary can confine it (macOS, Linux without bubblewrap,
and Windows before the one-time access grant), G6 refuses to run untrusted code for you (a patch, a generated script,
a repository's tests) unless you allow it once with g6 setup. Code you allow then has your own rights: it can read
your key file ~/.g6/.env and your other files, and its network guard works inside Python only. You are responsible for
what you run. `G6_ALLOW_UNCONFINED=1`, set where you start G6, does the same as allowing it; `G6_REQUIRE_ISOLATION=1`
refuses such code whatever you allowed. Patch review needs an operating-system boundary (Linux with bubblewrap,
Windows after the one-time access grant): allowing unconfined runs does not make it run, and on macOS it refuses.

## The first review

In a Python repository, ask your agent for a change, then ask it to review the change with G6 before it says the task
is done. The skill tells it how:

1. It turns the task into numbered requirements, each with the pytest tests that check it where such a test exists.
2. It calls `g6_review_patch` with the repository's folder, the task and the requirements, and polls
   `metaharness_job` until the review ends.
3. It shows you the reply's readable report (`assessment.report`) as it is: what G6 reproduced first, then what is not
   established, then what you decide. The reply is compact; the full assessment is saved in G6's state folder
   (`~/.g6/reviews/`).
4. When G6 reproduces a defect, it fixes it and reviews again, with each earlier reproduction added as a stated check,
   so you can see the fix pass the original counterexample. It stops after three rounds, when a round changes nothing,
   or when the report says only you can decide.

G6 runs the tests with the repository's own `.venv` or `venv`; without one, with the Python running G6, which then
needs pytest and the project's dependencies. For Poetry, conda or another environment, see `G6_REPO_PYTHON` in
[the workflow guide](docs/AI_FOR_AI_WORKFLOW.md#reference-application-review-a-python-patch).

The outcome is one of: test integrity compromised, defect detected, insufficient evidence, or supported within scope.
Supported within scope means every stated requirement passed its stated checks; it is not proof that the patch does
everything the task needs. Merging needs your separate decision. The patch's own code runs in pytest's own process,
in the repository's own environment (its `.venv`, which the agent can change): a patch written to defeat the checks
can make them report a pass, and G6 does not catch that.

Not a programmer? Ready-made **bundles** (double-click apps) are described in the
[install guide](site/docs/install.md).

## Use it

**From a coding agent (MCP), the main surface:** after `g6 setup`, the agent gets discovery, `g6_compose`,
`g6_review_patch`, and `metaharness_*` tools for constructing, measuring and revising applications. Long runs return
job IDs; poll `metaharness_job`. [AGENT_GUIDE.md](AGENT_GUIDE.md) is written for the agent itself.

Most checks need no model. The few steps that do use the model you set in your key file, `~/.g6/.env`, whose values
are local preferences under a trusted host: for example the line `G6_LLM_PROVIDER=claude_code` (your own Claude Code
sign-in), `G6_LLM_PROVIDER=codex` (your Codex sign-in), `G6_LLM_PROVIDER=ollama` (a model on your computer), or a
provider and its key. With none set, those steps say so and your agent can do them itself.

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
`metaharness_start` -> confirm -> diagnose -> propose code with a hypothesis -> evaluate -> decide -> client consent -> run.
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
boundary on a copy of the patch and on one of the base commit's code, and says whether it found a counterexample,
or, for a check of what the change should bring, whether it fails on the patch, whatever the base gives. A
proposed check never passes a requirement, and a failing one gives at most insufficient evidence, with a
reproduction you can run with plain pytest: the repository's own command, without G6's boundary (with
`PYTHONPATH=src` where G6 put `src` on the import path).
G6 keeps its record of its own test runs (test receipts) in a file your user account owns: it stops a caller's
claimed results from counting as G6's, not a program running as you.
Enable it explicitly with `g6 setup --client claude --allow patch_review.review` (or `--client codex`).

Fewer than 30 held-out cases means exploration only. Generated-code activation still requires independently
configured safety checks; source support and passing tests cannot replace them. A local file is not a human-only
authorization boundary: an agent with unrestricted same-user shell access can edit it. Use a trusted host or
separate account to enforce that separation.

The public `v0.1.0` tag is immutable. [The launch release notes](docs/releases/launch.md) say what ships, what does
not, and its limits; [the v0.1.1 release gates](docs/releases/v0.1.1.md) list the checks required before
recommending a new release.

## What is in it

- **Components** ([catalogue](site/docs/components.md), generated from the code). Each one works alone and declares
  any other component it imports. Those with an MCP block can be called over MCP and the CLI, and the catalogue's
  *surface* column says which; the rest are Python-only for now.
  - Evidence and claims: `grounding`, `epistemic_integrity`, `evidence`, `invariants`.
  - Actions and runtime safety: `action_gating`, `action_logic`, `safety_engine`, `guards`, `goal_engine`.
  - Coding agents: `coding_agent_gate`, `workforce`, `complexity`.
  - Tasks, plans and context: `intent_engine`, `reliability_pipeline`, `solver`, `navigator`, `context`.
  - Reasoning and search: `reasoning`, `debate`, `abstraction`, `algorithm_design`.
  - Formal methods, shown together on the Components page: `formal`, `algebra`, `lean`, `model_checking`,
    `datalog`, `prolog`, `cegis`, `csf`, `causal`, `optimisation`, `graph_reasoning`.
  - Governed learning: `learning`, `self_training`, `distillation`, `metaharness`, `self_maintenance`.
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
| Patch review for a coding agent's Python patch (`g6_review_patch`) | `implemented`. Its two studies are registered and have not run |
| Governed learning: hints credited from paired runs, a promotion gate for harness changes | `implemented`. Earlier self-training benchmark gains are withdrawn pending re-validation |
| Bundles (11) | `implemented`. Downloads come with the first release; their study has not run (see [Research](site/docs/research.md)) |
| Research kernel (the evidence kernel, governed adaptation, the mission bridge) | `wired-but-inert`: off unless `G6_ECHA_RESEARCH=1` |
| G6 helps independent coding agents on held-out tasks | `aspirational`: protocol published, not run |
| Meta-harness: tune G6 to your own recurring work (`g6.metaharness`) | `implemented`. The studies run with it are on the [Research](site/docs/research.md) page, as a worked example |
| G6 maintains its own repository through its self-improvement loop | `aspirational`: designed, and being built in the open |

Until a pre-registered study says otherwise, treat G6 as careful engineering, not as a proven uplift. The studies run
so far, including how G6 with its default settings compares with one direct model call, are on the
[Research](site/docs/research.md) page, as a worked example.

## G6 maintaining G6, in public

We're building a loop in which G6 maintains its own GitHub repository. It finds where G6 falls short, has Claude Code
and Codex work on the biggest gap in isolated containers, checks every change with G6's own review, and opens a pull
request. A maintainer reviews and merges every change. Everything is public: the code, the decisions, the evidence and
the agents' transcripts. It's an open experiment in self-improvement infrastructure for AI agents, and we'll report
what it shows, including if it doesn't help. The loop works only on G6 itself. Read
[the design](https://github.com/basicisabc1994/g6solver/blob/main/docs/superpowers/specs/2026-10-29-self-improvement-loop-design.md).

## Develop

```bash
pip install -e ".[dev,all]"
pytest -q && ruff check src tests && lint-imports
python tools/gen_catalog.py     # after changing a component
```

Licence: Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)).
