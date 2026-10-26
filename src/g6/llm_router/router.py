# SPDX-License-Identifier: Apache-2.0
"""``TaskRouter``: an :class:`~g6.core.llm.LLMCaller` for a cognitive task, from the providers you allow.

:meth:`TaskRouter.caller_for` routes the task (:func:`g6.llm_router.advice.route`) and returns a
:class:`~g6.core.llm.FallbackCaller` over ``g6.core.llm`` callers for the route's chain, in order. It allows only
Ollama unless you name more providers (any that ``g6.core.llm`` builds), so a paid provider is reached only if you list
it; and a model whose provider you did not allow is never in the chain. Each caller is built by
:func:`g6.core.llm.provider_from_env` from the provider and model the route names: the router never reads, holds or
names a key. When the caller is built, ``g6.core.llm`` reads keys only from the person's key file
(:mod:`g6.core.keys`) and the Ollama address from the environment; a project's ``.env`` is never read and nothing is
written into the environment. A paid model with no key in that file fails by name, without sending anything
(``g6.core.llm`` sees to that), and the chain moves on.

Given an :class:`~g6.llm_router.outcomes.OutcomeStore`, the route reads the outcomes recorded there under their bounded
rule; after a call, ``store.record_fallback(task, caller, accepted=...)`` records what the caller saw.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from g6.core.llm import FallbackCaller, LLMCaller, LLMError, provider_from_env
from g6.llm_router.advice import KNOWN_PROVIDERS, LOCAL_ONLY, route
from g6.llm_router.envelope import RouterResult
from g6.llm_router.outcomes import OutcomeStore
from g6.llm_router.registry import CognitiveTask, CognitiveTaskRegistry


def build_caller(provider: str, model_id: str, env: Mapping[str, str] | None = None) -> LLMCaller:
    """The ``g6.core.llm`` caller for one model of the table (``"ollama/llama3.1:8b"``); ``env`` replaces the
    environment ``g6.core.llm`` reads (default: the environment itself)."""
    model = model_id.split("/", 1)[1] if model_id.startswith(f"{provider}/") else model_id
    return provider_from_env(None if env is None else dict(env), provider=provider, model=model)


class TaskRouter:
    """Route a cognitive task to a model and its fallbacks, and build the caller that tries them in order.

    ``providers`` (default: Ollama only), ``fallbacks``, ``installed``, ``local_first``, ``outcomes`` and ``seed`` are
    passed to :func:`~g6.llm_router.advice.route`. ``env`` replaces the environment for building callers (default: read
    when :meth:`caller_for` runs); ``build(provider, model_id)`` replaces the builder, for tests.
    """

    def __init__(self, providers: Iterable[str] = LOCAL_ONLY, *, fallbacks: int = 2,
                 installed: Iterable[str] | None = None, local_first: bool = True,
                 registry: CognitiveTaskRegistry | None = None, env: Mapping[str, str] | None = None,
                 build: Callable[[str, str], LLMCaller] | None = None, outcomes: OutcomeStore | None = None,
                 seed: int | None = None) -> None:
        self.providers = tuple(providers)
        unknown = sorted(set(self.providers) - set(KNOWN_PROVIDERS))
        if unknown:
            raise ValueError(f"unknown provider {', '.join(unknown)}; use {' or '.join(KNOWN_PROVIDERS)}")
        if not self.providers:
            raise ValueError("allow at least one provider")
        self.fallbacks, self.local_first = fallbacks, local_first
        self.installed = None if installed is None else tuple(installed)
        self._registry, self._env, self._build = registry, env, build
        self.outcomes, self.seed = outcomes, seed

    def route(self, task: CognitiveTask | str) -> RouterResult:
        """The route for ``task``: its model, fallbacks and chain, labelled (see :func:`~g6.llm_router.advice.route`)."""
        return route(task, providers=self.providers, fallbacks=self.fallbacks, installed=self.installed,
                     local_first=self.local_first, registry=self._registry, outcomes=self.outcomes, seed=self.seed)

    def caller_for(self, task: CognitiveTask | str) -> FallbackCaller:
        """A :class:`~g6.core.llm.FallbackCaller` over the route's chain. Raises :class:`~g6.core.llm.LLMError` with
        the route's code when there is no route."""
        found = self.route(task)
        if found.completion_state != "qualified-draft":
            raise LLMError(f"{found.code}: {found.reason}")
        chain = [(row["provider"], row["model_id"]) for row in (found.model, *found.fallbacks)]
        callers = [self._build(p, m) if self._build else build_caller(p, m, self._env) for p, m in chain]
        return FallbackCaller(callers, names=[m for _, m in chain])


__all__ = ["TaskRouter", "build_caller"]
