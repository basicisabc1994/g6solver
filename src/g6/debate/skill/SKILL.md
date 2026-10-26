---
name: g6-debate
description: Use when an agent is about to accept one model's first answer to a judgement question (a plan, a design trade-off, a go or no-go) and wants role-named critics to push on it in turn, with dissent surfaced and an honest reading of how far the turns agree, which is never verification.
---

# G6 debate

`g6.debate` has role-named agents (proposer, critic, devil's advocate, synthesiser, verifier) answer a question in turn
through one injected model caller, then reads how far their final turns agree: a lexical floor computed by code, and
optionally one more model call that judges agreement by meaning. Neither reading is verification.

## When to use it

- You are about to act on one model's first answer to a question with no single right answer (adopt a migration plan,
  pick an architecture) and want a critic and a devil's advocate to challenge it first.
- You want the dissenting positions written down beside the dominant one.
- You want to see whether role-prompted turns converge before you spend more effort on a direction.

## When not to use it

- A claim that must rest on sources: `grounding` (`check_claims`) or `epistemic_integrity` (`adjudicate`).
- An answer that must be exact: `formal`, `algebra` or `optimisation`.
- A coding agent's answer before "done": `coding_agent_gate` (`verify_answer`).
- You only want the most common of several samples: `reasoning.self_consistency` at the same number of calls is the
  cheaper baseline, and no benefit of a debate over it has been shown.

## Python API

- `run_debate`: runs `rounds` x `agents` turns in order, one `llm.complete` call each; each prompt holds the question,
  the context and the earlier usable turns. Limits are checked before any call.
- `DebateAgent`: a unique name and an `AgentRole`; the role is only words in the prompt.
- `AgentRole`: `PROPOSER`, `CRITIC`, `DEVIL_ADVOCATE`, `SYNTHESISER`, `VERIFIER`.
- `Transcript`: the question, the context and the `Exchange`s.
- `Exchange`: one turn; `usable` only for a non-empty model reply. A failed turn is kept as `degraded` with its
  `error` and an empty response, never replaced by canned text.
- `overlap_agreement`: the lexical floor over each agent's last usable turn, one vote per agent.
- `OverlapResult`: `level` (one of `LEVELS`: none, weak, moderate, strong), `overlap_fraction` (a share of agents, not
  a confidence), `dominant`, `dissent`, `voters`, `ignored_turns`, `to_envelope()`.
- `judge_agreement`: one more model call reading agreement by meaning; the floor is always computed beside it.
- `JudgeResult`: `level`, `floor`, `judge` (`applied`, `degraded` or `not_run`), `diverged_from_floor`, `rationale`,
  `to_envelope()`.
- `is_mock`: True when a transcript has no turn or any turn is not clean model output.
- `MAX_ROUNDS`: at most 3 rounds.
- `MAX_AGENTS`: at most 5 agents.

## Reading the result

The best state is `qualified-draft`; with fewer than two agents giving a usable turn it is `blocked-escalated`
(`G6_E_DEBATE_NO_CONTENT`), and the judge is not asked. The warning card says what the reading rests on:
`G6_E_DEBATE_LEXICAL_ONLY`, `G6_E_DEBATE_MODEL_AGREEMENT` (one model's reading of agreement among samples of one
model), `G6_E_DEBATE_JUDGE_DIVERGED` (judge and floor disagree: read both) or `G6_E_DEBATE_JUDGE_FAILED` (the floor
stands). The state is computed by code; a `completion_state` or `confidence` in the judge's reply is never read. Bad
input raises `ValueError` with `G6_E_DEBATE_INPUT`, `G6_E_DEBATE_LIMIT` or `G6_E_NO_MODEL` before any call.

## Alone and composed

It has no block, so over MCP `g6_invoke("debate", ...)` answers `G6_E_NO_BLOCK`; `g6_describe("debate")` serves this
skill. From Python: `from g6.debate import AgentRole, DebateAgent, overlap_agreement, run_debate`.

Composed:

- Pass a `reasoning.MeteredCaller` as `llm` so a `Budget` caps the calls.
- To find out whether a debate helps on your questions, run it as a `reasoning.compare` arm (returning rounds x agents
  calls, plus one for a judge) against `reasoning.self_consistency` at the same number of calls.
- Send the dominant position's factual claims to `grounding` or `epistemic_integrity` before relying on them.

## Failure modes it catches

- `unchallenged_answer` (taking one model's first answer unchallenged): each agent answers in the role you give it
  with the earlier usable turns in its prompt, so with a critic or devil's advocate among the agents the first answer
  meets a challenge before anyone acts on it; `dissent` keeps the positions outside the largest agreeing group, and a
  judge reading that differs from the floor is flagged.

## Blind spots

- `excessive_token_use`: a debate costs rounds x agents model calls (up to 15) plus one for a judge, and each prompt
  grows with the transcript. Nothing measures whether that buys a better answer than one call.
- The agents are not independent voters: one model, and each turn sees the earlier ones, so agreement can be
  conformity rather than confirmation.
- The lexical floor misses paraphrase and reads opposing stances that share topic words as agreement (the package's
  own notes record it right on only 4 of 13 labelled development cases); its level cut-offs were never calibrated.
- The judge is a model reading model output.
- No timeout: the caller you pass owns it.

## What it needs

Nothing outside G6: it is pure Python on the standard library. Model calls go only through the `g6.core.llm` caller you
pass (`FakeLLM` in tests).

## Examples

Two agents, two rounds, four calls; the floor and the judge both read agreement, and a model that returns nothing
leaves no usable turn, which blocks:

```python
import json

from g6.core.llm import FakeLLM
from g6.debate import AgentRole, DebateAgent, judge_agreement, overlap_agreement, run_debate

replies = {"Ada": "Adopt the migration plan: the migration plan reduces downtime.",
           "Bo": "The migration plan reduces downtime, so adopt the migration plan."}


def as_agent(system, user):
    return next(text for name, text in replies.items() if system.startswith(f"You are {name},"))


agents = [DebateAgent("Ada", AgentRole.PROPOSER), DebateAgent("Bo", AgentRole.CRITIC)]
llm = FakeLLM(as_agent)
transcript = run_debate("Should we adopt the migration plan?", agents, 2, llm)
assert len(llm.calls) == 4 and len(transcript.exchanges) == 4

floor = overlap_agreement(transcript)
assert floor.voters == 2 and floor.completion_state == "qualified-draft"

judge = FakeLLM(default=json.dumps({"agreement_level": "strong", "dominant_position": "adopt the plan",
                                    "dissenting_views": [], "rationale": "Both back the plan."}))
judged = judge_agreement(transcript, judge)
assert judged.judge == "applied" and judged.completion_state == "qualified-draft"

silent = run_debate("Should we adopt the migration plan?", agents, 1, FakeLLM(default=""))
assert all(turn.degraded and turn.response == "" for turn in silent.exchanges)
assert overlap_agreement(silent).to_envelope()["completion_state"] == "blocked-escalated"
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing to a run ledger. `to_envelope()` gives the code and the evidence
  (`debate_overlap`, `debate_judge`) a caller can record as a labelled incident.
- **Evaluators the model cannot influence**: the floor, the vote count and the state are code, and the judge's own
  state or confidence is never read. But the turns and the judge are the model, so the agreement it reports is not an
  evaluator the model cannot influence; pair it with an outside check.
- **Transfer by failure mode**: the challenge structure is bound to the failure mode (an unchallenged first answer),
  not to a topic. Not yet: no catch rate has been measured on any task family.
- **Enough held-out cases**: Not yet: nothing has measured whether a debate answers better than one call or than
  self-consistency at equal calls. `reasoning.compare` (at least 30 paired cases, exact sign test) is the gate to use.
