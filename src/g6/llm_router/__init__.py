# SPDX-License-Identifier: Apache-2.0
"""g6.llm_router — pick a language model for a kind of task from a committed table, local first, with an ordered fallback chain and dated cost estimates.

An extension in the integrations family (``__g6_family__``), not part of the core harness. The owner, 2026-10-20: the
earlier router ported into the library, without its hardware fit.

Capability label: ``implemented``.

    from g6.llm_router import TaskRouter, advice
    r = advice.route("code_generation")
    r.chain, r.completion_state                     # local Ollama models only, best committed score first
    c = advice.estimate_cost("openrouter/anthropic/claude-opus-4.7", 1_000_000, 200_000)
    c.estimated_usd, c.prices_as_of, c.price_age_days  # (10.0, "2026-08-16", its age today); c.stale_price after 30
    llm = TaskRouter().caller_for("summarization")  # a FallbackCaller over g6.core.llm's Ollama callers
    # llm.complete(system, text), then llm.answered and llm.failures say which model answered and why others failed

What it is:

* a committed table of 47 models (``data/model_profiles.json``, read with ``importlib.resources``): 18 local Ollama
  models and 29 on OpenRouter, each with scores for 16 cognitive tasks, its modalities, context window and prices in
  US dollars per million tokens. The prices are those OpenRouter listed on 2026-08-16; the scores were assigned by hand
  in the earlier router and have not been measured by G6. A price of -1 is variable or unknown and is never shown as a
  number;
* :mod:`~g6.llm_router.advice`: the tasks, the models ranked for a task with the reason for each rank, a route with an
  ordered fallback chain drawn only from the providers you allow (Ollama alone by default; any that ``g6.core.llm``
  builds), a supervision chain by cost tier in which each model can check the one below it, a model's profile and a
  cost estimate. Every answer that carries a price says how old it is, and one older than 30 days is flagged stale.
  These read only the table, so they are callable over MCP (``g6_invoke``);
* :class:`TaskRouter`: an ``LLMCaller`` for a task, a ``g6.core.llm.FallbackCaller`` over ``g6.core.llm``'s callers
  for the route, which moves to the next model only when one fails, and records why. The router never handles a key:
  ``g6.core.llm`` builds each caller and reads its key;
* :mod:`~g6.llm_router.outcomes`, the learning signal (merged from the earlier agent router): how a model did on a
  task, recorded by the caller (``record_outcome``) or from a ``FallbackCaller``'s failures, kept under the G6 home and
  counted (``outcome_stats``). Outcomes are claims G6 never checks: a route asked to use them tilts each committed score
  by at most 0.05 (a seeded Thompson draw, after at least five outcomes), never adds a model or a provider, and never
  changes its label;
* :mod:`~g6.llm_router.providers`: list, pull and delete local Ollama models (through ``g6.core.outside``, on this
  machine only), and fetch OpenRouter's live catalogue and prices (through ``g6.core.http``), compared with the table
  and never written into it. These reach the network or change the machine, so they are sensitive: run from Python,
  and over MCP only when the person who runs the server lists them in ``G6_MCP_SENSITIVE``.

The best state is ``qualified-draft``. A ranking from hand-assigned scores, or a price from a dated table, is not
evidence a person has inspected, so nothing here is ``verified``. ``LLMRouterBlock`` makes the ops callable through
the registry.

Not ported from the archive's ``llm_router``, and why:

* the hardware fit (llmfit), its bundled third-party code and every probe of the machine's memory and graphics card:
  the owner ruled it is not needed;
* litellm and requests (``g6.core.http`` and ``g6.core.outside`` make the few calls left); the completion, budget,
  billing, spend and key-custody code, which served a hosted service G6 no longer runs; reading a project's ``.env``
  files and writing keys into the environment (keys now come only from the person's key file, :mod:`g6.core.keys`);
* the command-line model callers and the other model runtimes, which the agents family of extensions covers;
* the model-driven re-rank of the ranking, so that every MCP op reads only committed data; the cross-package calls,
  patterns, skills, both MCP servers, the completion that answered without a model and the deprecated aliases;
* the archive components' tool-complexity mapping (it named components this library does not have), and the accuracy
  estimate behind ``select_optimal_model``;
* from the earlier agent router: its task-type matrix and review rules (they routed this project's own development
  work to components outside the library), its model re-rank and its scaffold.
"""
from g6.llm_router import advice, outcomes, providers
from g6.llm_router.envelope import RouterResult
from g6.llm_router.llm_router_block import SENSITIVE_OPS, LLMRouterBlock, LLMRouterInput
from g6.llm_router.outcomes import OutcomeStore
from g6.llm_router.registry import (
    CognitiveTask,
    CognitiveTaskRegistry,
    ModelProfile,
    UseCase,
    default_registry,
    table,
)
from g6.llm_router.router import TaskRouter
from g6.llm_router.supervision import ModelTier, SupervisionPolicy

__g6_component__ = True
__g6_block__ = "LLMRouterBlock"
__g6_family__ = "integrations"  # an extension in the library, not part of the core harness
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they reach the network or change the machine
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unchosen_model", "stale_price", "poor_fit_route")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (providers.OLLAMA,)

__all__ = ["CognitiveTask", "CognitiveTaskRegistry", "LLMRouterBlock", "LLMRouterInput", "ModelProfile", "ModelTier",
           "OutcomeStore", "RouterResult", "SupervisionPolicy", "TaskRouter", "UseCase", "advice", "default_registry",
           "outcomes", "providers", "table"]
