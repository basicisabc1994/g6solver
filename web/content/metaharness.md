---
title: Tune G6 to your own work
---

# Tune G6 to your own work

An AI model cannot learn on the job. Making it better at your work means retraining it, which takes specialists and
data and can erase what it knew, or an expert rewriting its prompts and checks by trial and error, which goes stale as
the work changes. And an AI that changes itself unchecked could change the very checks meant to stop it.
{: .why }

**The meta-harness is the part of G6 that learns, safely.** It is one of G6's two parts; the other is the
[components](components.md), the prebuilt library it draws on. It proposes changes to how G6 runs your task, tests
them on held-out tasks you trust, and changes nothing until you approve. What it learns stays in the harness, where you
can see it, test it and roll it back, and your base model never changes. With a coding agent you can teach it in plain words: the
agent turns what you ask for into changes, and the meta-harness tests each one.

No single way of running an AI agent is best for every job. So the meta-harness (`g6.metaharness`) lets you adapt G6
to yours. You say what a good result looks like, which checks must always run, and which of your examples are kept
aside for testing. G6 then proposes changes to how it works: which of your models runs each step, what context and
hints to give, which extra checks or optional steps to add. It measures each change against your held-out examples at
equal compute, shows you the evidence, and waits for your decision. It cannot change the checks that judge it, and it
can always go back to a version you approved.

## What it did, on one computer

A worked example of what G6's self-engineering can do, built to show the power of the system, not a benchmark. On your own work, results will vary with the task and the data you have. Each result came from Claude Code
working with G6. G6's own loop did the tuning: it ran the task, diagnosed why answers failed, proposed fixes, tested
each one on cases it had not seen, and kept only what its gate passed, with a second local model (`gpt-oss:20b`)
reflecting on the failures. Claude Code ran the rounds, read G6's root-cause reports, and fixed the two limits in G6's
own method that those reports exposed. Everything ran on one computer, with an 8-billion-parameter model
(`llama3.1:8b`) doing the tasks.

One development run, in full: every task set it ran, including the two where G6 changed nothing.

| Task set | The model alone | G6, self-tuned | The gate | What G6 changed |
|---|---|---|---|---|
| GSM8K | 26 of 100 | **90 of 100** | approved in 8.9 minutes | Asked for the working step by step, with the answer on its own last line |
| banking77 | 52 of 100 | **81 of 100** | approved in 6.0 minutes | Showed eight labelled examples, with an instruction to name one intent |
| MASSIVE | 67 of 100 | **74 of 100** | approved in 1.7 minutes | Showed eight labelled examples |
| ARC-Challenge | 82 of 100 | 82 of 100 | refused after 3.5 minutes | Nothing: no change it tried won on held-out tasks |
| MBPP | 61 of 100 | 61 of 100 | refused after 14.5 minutes | Nothing: no change it tried won on held-out tasks |
| **All five** | 288 of 500 | **388 of 500** | | |

On GSM8K, G6 more than tripled what the model solves on its own, in under nine minutes, by correcting its own
failures: no retraining, and no person editing the task. Where it found nothing it could prove, it changed nothing:
on ARC-Challenge the model alone already scored 82. That is the gate working, and it is why results vary: G6 gains
most where a model's errors are systematic and a harness change addresses them.

The study registered before it ran, on an earlier version of the method, is on
[Research](research.md#the-meta-harness), as a worked example. An earlier development run, before Claude Code fixed
the two method limits G6's own diagnosis had found, approved changes on GSM8K (24 to 83, in 6.3 minutes) and
banking77 (48 to 82, in 4.9 minutes) and none on MASSIVE, where the run above then found a gain in under two minutes.

The last development run asked whether the two modes stack: self-tuning that starts from every prebuilt component switched
on. They did not. Self-tuning from G6 as built took GSM8K from 21 to 81 of 100 in 9.4 minutes, and every component
switched on took MASSIVE from 65 to 78; but self-tuning that started from every component on had no change approved
on any of the 4 task sets, and on SciFact every component on did worse than the model alone (57 against 64). There a
check asks every answer a second time with its own fixed question, so no instruction upstream could change the final
label: one layer can mask another. The gate also refused a SciFact change that won 15 to 1 on the tasks it was chosen
on and went 3 to 5 on fresh ones. So the two modes are alternatives, chosen per task on held-out evidence.

Every round's full scoreboard is on [Research](research.md#development), with
[how to read the results](research.md#reading).

## The loop

**propose → evaluate on held-out → gate → a person approves → monitor → roll back if it falls behind**, and when
the gate refuses: **diagnose → recover**.

| Step | What happens |
|---|---|
| Confirm | A person confirms the **workflow**: the task, its checks, the oracle that reads each held-out answer, your objectives, your models and the dev and held-out examples. It is frozen by digest. |
| Propose | A candidate **profile**: written by you or your agent, or found by a search over options you declare, or learned as hints from failures on the dev examples. A proposer never sees the held-out examples. |
| Evaluate | The current and candidate profiles run on every held-out example, paired, through `g6.harness`. A compute-matched control and a negative control run too. |
| Gate | Recommend only with at least 30 held-out examples, a clear win by an exact sign test, no extra compute that a control explains away, costs within 5% and no rise in false accepts. Otherwise refuse or block, with a `G6_E_META_*` code. |
| Approve | A person, never the proposer and never an automated name, activates a recommended change. A snapshot is taken first. |
| Monitor | A share of live runs also runs under the previous approved version. A version that falls behind is moved back automatically: the only change G6 makes on its own. |

The best label any of this gives is `qualified-draft`. A task result under a tuned profile keeps the label its own
evidence earns; a profile can never raise it.

## When it finds nothing: recovery

Most attempts to tune a workflow find nothing, at first. The meta-harness never leaves you with only a refusal: every
reply that is not a recommendation carries a **recovery** plan, which says why the attempt failed and what to do next.

| You see | Root cause it reports | What it suggests |
|---|---|---|
| `INSUFFICIENT_EVIDENCE` or `NO_GAIN`, but the change won more than it lost | **Underpowered**: a real gain may be there, but too few held-out examples to confirm it | Add held-out examples, with an estimate of how many. It says not to read the result as "no effect" |
| `NO_GAIN` with no sign of a gain | The change does not help | Run `mh.diagnose()` |
| Runs held at intake | The task names something high-stakes (a payment, a deletion), so every run waits for a person | Only you can lift it: `Workflow(accept_risky="<your name>")` |
| Answers rejected by a check | The output is not in the form the check wants | A prompt for that form, or a retry; if the check is wrong, only you can fix it |
| Wrong answers | Depends on the kind of answer: labels, numbers, code or text | The G6 components that address that kind (below), which the meta-harness can try as candidates |
| `NOT_COMPUTE_MATCHED` | The gain came from extra model calls | A cheaper variant |
| A proposer with nothing new | The declared options are used up | Run `mh.diagnose()`, or declare more options |

`mh.diagnose()` (over MCP: `metaharness_diagnose`) runs the active version on your dev examples, sorts the failures
into those root causes, and maps each one to a remedy. `mh.propose(RecoveryStrategy(workflow))` turns that diagnosis
into candidates (labelled examples, a declared step, the working prompt or a retry) for the gate to judge. Each remedy names who acts: **candidate** (the meta-harness can
propose it, and the gate and a person still decide) or **person** (only you can). It never reads a held-out example,
and it changes nothing.

| Wrong answers that are… | G6 components the remedies use |
|---|---|
| Labels (intents, categories, verdicts) | `context`: labelled examples of similar inputs, `Profile(exemplars=8)`; a second look at a label the model over-uses, `steps.second_look`; `reasoning`: a vote at equal compute; `grounding`: a check of a claim against its source text |
| Numbers | A prompt that asks for the working; `reasoning`: a vote |
| Code | Run the example tests the task shows, out of process, and repair, `steps.repair_with_tests(rounds=3)`; `g6.metaharness.coding` for repositories; `cegis` for sketches with holes |
| Text | `grounding.check_claims` against the task's sources; `epistemic_integrity.adjudicate` |

Labelled examples come from `Workflow(examples=...)`, your own labelled data, or from the dev split when you declare
none. They are part of what you confirm, a held-out example can never be one, and a case never sees itself. The
built-in steps (`g6.metaharness.steps`) are declared by you in `Workflow(pipeline_steps=...)`. The meta-harness then
tries them like any other option. Each extra model call is counted, so the gate compares them at equal compute.

## What it can change, and what it never can

| It can change (the profile) | It can never change (the workflow and G6) |
|---|---|
| Which of your declared models runs each step, and their budgets | Your checks, the oracle, the held-out examples |
| The prompt, learned hints and their layout | The evidence rules and the approval rules |
| How many of your labelled examples to show (`exemplars`) | Which examples you declared, and the held-out ones |
| Extra checks (added, never removed) and optional pipeline steps | The stop, the gates, its own evaluator |
| A compiled distillate that replaces a model call, once admitted on held-out examples | The tools or actions your agent may take |
| Your own code files, as a reviewed overlay it runs in a copy | G6's own source code |
| A small LoRA adapter for a local model (optional `lora` extra) | Your base model's weights |

## Use it from Python

```python
from g6.harness import Check
from g6.metaharness import Model, Suite, SuiteCase, Workflow, adapt, SearchStrategy

workflow = Workflow(
    name="unit-answers", task="Answer with a number and its unit: {input}",
    checks=(Check("names a unit", lambda out: any(u in out for u in ("kg", "m", "s"))),),
    oracle=lambda out, expected: out.strip() == expected,          # the only code that reads an answer
    suite=Suite(dev=dev_cases, held_out=held_out_cases,
                provenance={"dev": "caller", "held_out": "independent"}),
    models={"local": Model(my_llm)},
)
mh = adapt(workflow)
mh.confirm(accepted_by="Ada")                       # a person freezes the workflow
mh.baseline()                                       # where you start, with a power estimate
found = mh.propose(SearchStrategy({"prompt": ["", "Always give the SI unit."]}))
proposal = found["proposals"][0]
report = mh.evaluate(proposal)["report"]
decision = mh.decide(report, proposal)              # recommend, refuse or block
decision.get("recovery")                            # if not recommended: why, and what to do next
mh.diagnose()                                       # root causes on dev, and the components that address them
mh.activate(decision["decision"], accepted_by="Ada")
mh.run("How heavy is a litre of water?")
```

`g6.metaharness.Builder` walks you through a new use case: six questions about your objectives in a fixed order,
each action your agent can take classified and confirmed by you, and held-out examples labelled by where they came
from. A held-out file is sealed; a change to it voids every evaluation against it.

## Use it from your agent (MCP)

The `g6-mcp` server adds seven tools: `metaharness_inspect`, `metaharness_propose`, `metaharness_evaluate`,
`metaharness_decide`, `metaharness_diagnose`, `metaharness_approve` and `metaharness_rollback`, for the workflows you
declare in a Python file named by `G6_METAHARNESS`. Your agent can propose, measure, read decisions and diagnose. It
relays each recovery plan to you. It cannot approve: approval asks you directly through your client, and the session
that proposed a change can never approve it.

## What you need

A task you repeat, checks you trust, at least 30 examples kept aside with known answers, your model or models, and a
budget. Without held-out answers G6 can measure, but it will not recommend a change, and it says why. By default the
gate needs at least 30 held-out cases, at least 5 of them where the base and the candidate differ, and a win by the
exact sign test, so a small gain needs more examples than that; `mh.baseline()` gives a power estimate before you
start, and a refusal says how many more examples it needs.

If your task's text names something high-stakes (a payment, a deletion, a medical question), G6's intake holds each
run for a person. Accept it once, by name, on the workflow: `Workflow(..., accept_risky="Ada")`. It is part of what
you confirm, so changing it needs a new confirmation, and it never raises a result's label.
