---
name: g6-llm-router
description: Use when you are about to pick a language model for a kind of task (code generation, summarisation, review, extraction and others), want a local-first choice with an ordered fallback chain drawn only from providers you allow, want to record how a model did so later routes lean away from one that keeps failing, or need a cost estimate that says which dated price it rests on and whether that price is out of date.
---

# G6 LLM router

`g6.llm_router` picks a language model for a cognitive task from a committed table of 47 models (18 local Ollama
models, 29 on OpenRouter), local first, with an ordered fallback chain and dated cost estimates. `TaskRouter` turns a
route into a `g6.core.llm` caller that tries each model in order. Outcomes you record are kept and, within a stated
bound, taken into later routes. It is an extension in the integrations family; its best state is `qualified-draft`.

## When to use it

- You are about to choose which model runs a task and want the choice, and why, written down.
- You want a paid provider reached only if you allowed it, and a local model by default.
- A model in your chain may be missing or down, and you want the next one tried with the reason recorded.
- You are about to quote a cost and need the price's date and age, a warning when it is old, or no number when the
  price is unknown.
- You want a cheaper worker model and, above it, models the table ranks able to check its work.
- A model keeps failing a kind of task for you, and you want that recorded and weighed in later routes.

## When not to use it

- Measuring which model is better on your own task: the scores were assigned by hand, not measured by G6; use
  `g6.metaharness` on your held-out cases.
- Choosing models for high-stakes work without trying them on your own examples first.
- Calling one model you have already chosen: use a `g6.core.llm` caller (`G6_LLM_PROVIDER=anthropic|openai`).
- Prices you will bill on: check the provider's current price.

## Ops

- `list_tasks`: the cognitive tasks a model can be ranked for, with how many models in the table score each.
- `models_for_task`: the models ranked for a `task` (`providers`, `local_only`, `min_score`, `limit`), each with its
  provider, price and the reason for its rank.
- `route`: one model and an ordered fallback chain for a `task`, drawn only from the `providers` you allow (Ollama
  alone by default; any of `ollama`, `anthropic`, `openai`, `openrouter`); `fallbacks`, `installed`, `local_first`
  shape it; `use_outcomes` (with an optional `seed`) applies the recorded outcomes by the bounded rule below.
- `supervision_chain`: from the cheapest worker up, each model able to check the one below it (at least its score
  for the task, and a higher cost tier or the same tier and a higher score; a higher tier alone is not enough), and
  which steps need a paid API.
- `model_profile`: one `model`'s committed profile, with the tasks it has a score for.
- `estimate_cost`: an estimate for `model`, `input_tokens` and `output_tokens` from the committed prices; a variable
  or unknown price gives no estimate.
- `record_outcome`: record how a `model` did on a `task` (`success`; optionally `latency_ms`, `cost_usd`,
  `reviewer_verdict`, `human_correction`), as your report, in the store under the G6 home. G6 checks none of it.
- `outcome_stats`: the outcomes recorded for a `task`, per model, with how often a reviewer's verdict disagreed with
  the reported success and how many a person corrected; it ranks nothing.
- `list_local_models` (sensitive: it reaches the Ollama server on this machine): the models installed there.
- `pull_model` (sensitive: it downloads a model into your local Ollama, changing the machine).
- `delete_model` (sensitive: it removes a model from your local Ollama, changing the machine).
- `fetch_openrouter_catalogue` (sensitive: it reaches OpenRouter over the internet): live models and prices compared
  with the committed table, which it never changes.

Every answer that carries a price has `prices_as_of`, `price_age_days` and `stale_price`. A price older than 30 days,
or whose age cannot be worked out, is stale: the answer still gives it, under the card `G6_E_ROUTER_STALE_PRICE`.

The bounded rule: recorded outcomes count only in a route that asks, once at least 5 are recorded among its
candidates. Each committed score then moves by 0.1 x (theta - 0.5), theta drawn from Beta(successes + 1, failures + 1)
with a seed the reply records, so by at most 0.05 whatever is reported. They never add a model, a provider or an
uninstalled model, local models stay first, and the evidence says `checked: false`.

## Alone and composed

Alone over MCP: `g6_invoke("llm_router", "route", '{"task": "code_generation"}')`. The four sensitive ops are refused
over MCP (`G6_E_SENSITIVE_COMPONENT_DENIED`) unless the person who runs the server lists them in `G6_MCP_SENSITIVE`;
never edit that setting yourself. From Python: `from g6.llm_router import OutcomeStore, TaskRouter, advice, providers`.

Composed:

- `TaskRouter(providers).caller_for(task)` returns a `g6.core.llm` `FallbackCaller` over the route; pass it to any
  component that takes a caller (a `g6.metaharness` `Model(caller)`, the agent of `g6.learning.run_learning`).
  `g6.core.llm` builds each caller and reads its key; the router never handles one, and a paid model with no key
  fails by name without sending anything.
- Learning from calls: `TaskRouter(outcomes=store)`; after a call, `store.record_fallback(task, caller, accepted=...)`
  records each model that raised as a failure (G6 saw it) and the one that answered only as you judge it.
- Models the table lacks: add profiles to a registry of your own (`CognitiveTaskRegistry.from_dict`) for any provider
  `g6.core.llm` builds, and pass it as `registry`.
- `providers.list_local_models` gives the `installed` names for `route`; `fetch_openrouter_catalogue` says whether a
  committed price has changed.

## Failure modes it catches

- `unchosen_model` (a task sent to a paid or unsuitable model nobody chose): routes draw only from the providers you
  allow, Ollama alone unless you name more; each pick carries its provider, price and the reason for its rank.
- `stale_price` (a cost quoted from a price that is unknown or out of date): every price carries its date and age, one
  older than 30 days is flagged under `G6_E_ROUTER_STALE_PRICE`, and an unknown price is `None` and gives no estimate
  (`G6_E_ROUTER_PRICE_UNKNOWN`).
- `poor_fit_route` (a task sent to a model that is a poor fit for it): outcomes recorded for a task are kept and
  counted, and a route that asks lets a model that keeps failing drop below a close rival; the bound leaves models far
  apart in the table's order.

## Reading the result

- An answer is `qualified-draft`; its card says what it rests on: `G6_E_ROUTER_COMMITTED_TABLE`,
  `G6_E_ROUTER_COST_ESTIMATE`, `G6_E_ROUTER_LIVE_ANSWER`, `G6_E_ROUTER_RECORDED_OUTCOMES`, or
  `G6_E_ROUTER_STALE_PRICE` when the prices are old.
- A refusal is `blocked-escalated`: `G6_E_ROUTER_NO_MODEL`, `G6_E_ROUTER_UNKNOWN_MODEL`, `G6_E_ROUTER_INPUT_INVALID`,
  `G6_E_ROUTER_NO_SUPERVISOR`, `G6_E_ROUTER_PROVIDER_UNREACHABLE`, `G6_E_ROUTER_STORE_FAILED` (a route then keeps the
  committed order and says so), `G6_E_OUTSIDE_PROGRAM_MISSING` (Ollama is not running, with the install steps),
  `G6_E_OUTSIDE_SERVICE_NOT_LOCAL` and `G6_E_HTTP_REFUSED`. Over MCP the reply's `ok` can be true while
  `completion_state` is `blocked-escalated`: read the state.

## Blind spots

- The task scores were assigned by hand in an earlier router; a top rank is a starting point, not evidence.
- The prices are those OpenRouter listed on 2026-08-16; no op refreshes the table, so they stay stale until it changes.
- A route does not check that a model is installed or reachable; the caller finds out when it calls.
- How often a supervisor catches its worker's mistakes has not been measured, and nothing here checks an answer.
- Recorded outcomes are claims: anyone who can call `record_outcome` can move a route within the bound. Verdict words
  are read coarsely; one it does not know is not counted.
- The Ollama ops reach only an Ollama on this machine.

## What it needs

- **Ollama**, for the Ollama ops and the local models routed to by default: G6 finds it at `http://127.0.0.1:11434/`
  or where `OLLAMA_BASE_URL` points on this machine. Install it from ollama.com and start it (`ollama serve`);
  without it those ops refuse with the install steps.
- A key, only for a paid provider you allow, in your key file `~/.g6/.env` (a line such as `OPENROUTER_API_KEY=...`,
  or `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` for your own profiles). You write that file yourself; G6 reads keys
  only from it, never from the environment, and `g6 doctor` names what it finds there without showing a value.
- `fetch_openrouter_catalogue` needs an internet connection; it sends no key. Recorded outcomes live in
  `llm_router/outcomes.sqlite` under the G6 home.

## Examples

The route for code generation, from local models only, with the date and age of the prices it rests on.

```g6-example op=route
{"task": "code_generation"}
```

A cost estimate; the reply gives `estimated_usd`, `prices_as_of`, `price_age_days` and `stale_price`.

```g6-example op=estimate_cost
{"model": "openrouter/anthropic/claude-opus-4.7", "input_tokens": 1000000, "output_tokens": 200000}
```

The three best local models for summarisation.

```g6-example op=models_for_task
{"task": "summarization", "local_only": true, "limit": 3}
```

```g6-example op=list_tasks
{}
```

Who checks a code generator's work, up to the premium tier.

```g6-example op=supervision_chain
{"task": "code_generation"}
```

```g6-example op=model_profile
{"model": "ollama/qwen2.5:7b"}
```

Record that a local model did a summary badly, read what is recorded, and route with the outcomes taken in.

```g6-example op=record_outcome
{"task": "summarization", "model": "ollama/llama3.1:70b", "success": false, "reviewer_verdict": "rejected"}
```

```g6-example op=outcome_stats
{"task": "summarization"}
```

```g6-example op=route
{"task": "summarization", "use_outcomes": true, "seed": 7}
```

From Python, a route becomes a caller; stand-in callers take the models' place, so nothing reaches the network.

```python
from g6.core.llm import FakeLLM, LLMError
from g6.llm_router import TaskRouter, advice

route = advice.route("summarization")
assert route.completion_state == "qualified-draft" and all(m.startswith("ollama/") for m in route.chain)
cost = advice.estimate_cost("openrouter/anthropic/claude-opus-4.7", 1_000_000, 200_000)
assert cost.prices_as_of and cost.price_age_days is not None
assert cost.stale_price == (cost.warning_card["code"] == "G6_E_ROUTER_STALE_PRICE")


class NotPulled:
    def complete(self, system, user, *, max_tokens=1024):
        raise LLMError("the model is not pulled")


first = route.chain[0]
llm = TaskRouter(build=lambda provider, model_id: NotPulled() if model_id == first
                 else FakeLLM(default="a summary")).caller_for("summarization")
assert llm.complete("Summarise.", "some text") == "a summary"
assert llm.answered == route.chain[1] and llm.failures[0][0] == first
```

## The four open problems

- **Learning signals**: outcomes from your reports and a `FallbackCaller`'s failures are kept under the G6 home and
  tilt later routes by a bounded, seeded rule. Not yet: the committed scores never change, and nothing checks that a
  reported outcome is true.
- **Evaluators the model cannot influence**: the table ops read only committed data and call no model; recorded
  outcomes are callers' claims, held to a bound. Not yet: nothing evaluates a model's answers.
- **Transfer by failure mode**: the guards are general: any model from a provider you did not allow is kept out, any
  price past its age is flagged. Not yet: a route is chosen per cognitive task.
- **Enough held-out cases**: Not yet: no ranking here rests on held-out cases; compare models on your own with
  `g6.metaharness` before relying on one.