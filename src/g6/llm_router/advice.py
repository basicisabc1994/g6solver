# SPDX-License-Identifier: Apache-2.0
"""What the committed table says, as labelled answers: the tasks, the models ranked for one, a route with its fallback
chain, a supervision chain, a model's profile and a dated cost estimate.

Every function here reads only ``data/model_profiles.json``, and, where it is given one or asked to, the outcome store
under the G6 home (:mod:`~g6.llm_router.outcomes`): none reaches the network or calls a model, so all are callable over
MCP. Each returns a :class:`~g6.llm_router.envelope.RouterResult`, ``qualified-draft`` at best, whose
card says what the answer rests on: task scores assigned by hand (not measured by G6) and prices as of the table's
date. A price that is variable or unknown is given as ``None``, never as a number, and no cost is estimated from it.
Every answer that carries a price also says how old it is (``price_age_days``, counted from today's date in UTC); one
older than :data:`STALE_AFTER_DAYS`, or whose age cannot be worked out, is ``stale_price`` and its card is
``G6_E_ROUTER_STALE_PRICE``, so a cost is never quoted from an out-of-date price without saying so.

The providers are those ``g6.core.llm`` builds (:data:`KNOWN_PROVIDERS`); the committed table holds Ollama and
OpenRouter models, and a registry of your own (``CognitiveTaskRegistry.from_dict``) can add profiles for the others.

``route`` is local first: by default it considers only Ollama, and it never draws a model from a provider you did not
allow. Given an outcome store, it lets the outcomes callers recorded tilt its order by the store's bounded rule, and says
so; ``record_outcome`` records one, as the caller's report, and ``outcome_stats`` counts them, ranking nothing.
``models_for_task`` and ``supervision_chain`` call nothing, so by default they consider every provider in the
table and say, for each model, whether it needs a paid API.
"""
from __future__ import annotations

import math
import secrets
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from g6.core.llm import PROVIDERS
from g6.llm_router.envelope import (
    COMMITTED_TABLE,
    COST_ESTIMATE,
    INPUT_INVALID,
    NO_MODEL,
    NO_SUPERVISOR,
    PRICE_UNKNOWN,
    RECORDED_OUTCOMES,
    STALE_PRICE,
    STORE_FAILED,
    UNKNOWN_MODEL,
    RouterResult,
    answer,
    refuse,
)
from g6.llm_router.registry import (
    TASK_TO_USE_CASE,
    CognitiveTask,
    CognitiveTaskRegistry,
    ModelProfile,
    default_registry,
    table,
)
from g6.llm_router.outcomes import MIN_OUTCOMES, OUTCOME_WEIGHT, RULE, STORE, OutcomeStore, OutcomeStoreError, tilts
from g6.llm_router.supervision import ModelTier, Specialist, SupervisionPolicy, get_tier

#: The providers ``g6.core.llm`` builds a caller for; the committed table holds models of the first and the last.
KNOWN_PROVIDERS: tuple[str, ...] = PROVIDERS
#: A price older than this many days is stale: the answer still gives it, under a stale-price warning.
STALE_AFTER_DAYS = 30
#: What ``route`` and ``TaskRouter`` allow unless told otherwise: the local provider only.
LOCAL_ONLY = ("ollama",)
RANKING = ("Ranked by the committed task score, highest first; a tie goes to a local model, then to the lower known "
           "price, then by name.")
ROUTE_ORDER = "Local models first, then by the committed task score, highest first; ties as in models_for_task."
_SOURCE = "g6.llm_router data/model_profiles.json"
_SCORES = "assigned by hand in an earlier router; not measured by G6"
_TRY = "Try the top models on examples of your own task before relying on the ranking, and check current prices."


def _date() -> str:
    return table()["prices_as_of"]


def _today() -> date:
    return datetime.now(UTC).date()


def _freshness() -> dict[str, Any]:
    """How old the committed prices are; stale when older than :data:`STALE_AFTER_DAYS`, or when the age cannot be
    worked out or is negative (a clock or a table that is wrong is not taken as fresh)."""
    try:
        age: int | None = (_today() - date.fromisoformat(_date())).days
    except (TypeError, ValueError):
        age = None
    stale = age is None or not 0 <= age <= STALE_AFTER_DAYS
    return {"price_age_days": age, "stale_price": stale, "stale_after_days": STALE_AFTER_DAYS}


def _priced(operation: str, card: str, message: str, action: str, *, evidence: dict[str, Any],
            **fields: Any) -> RouterResult:
    """An answer that carries a price: with the price's age, and under a stale-price card when it is stale."""
    fresh = _freshness()
    if fresh["stale_price"]:
        age = fresh["price_age_days"]
        said = (f"The age of the committed prices (as of {_date()}) could not be worked out" if age is None else
                f"The committed prices are dated {_date()}, after today's date, so the clock or the table is wrong"
                if age < 0 else f"The committed prices are {age} days old (as of {_date()}), older than the "
                f"{STALE_AFTER_DAYS} days G6 takes a price as current")
        card, message = STALE_PRICE, f"{said}, so every price and cost here may be out of date. {message}"
        action = "Check the provider's current prices before relying on any price or cost here. " + action
    return answer(operation, card, message, action, evidence={**evidence, **fresh}, **fresh, **fields)


def _evidence(**extra: Any) -> dict[str, Any]:
    return {"kind": "committed_table", "source": _SOURCE, "prices_as_of": _date(), "scores": _SCORES,
            "network": False, **extra}


def _table_words() -> str:
    return (f"the task scores were assigned by hand and are not measured by G6, and the prices are as of "
            f"{_date()}")


def _ids(request_id: str, run_id: str) -> dict[str, str]:
    return {"request_id": request_id, "run_id": run_id}


def _task(task: CognitiveTask | str | None) -> CognitiveTask | None:
    try:
        return CognitiveTask(task)
    except ValueError:
        return None


def _bad_task(operation: str, task: Any, ids: dict[str, str], **fields: Any) -> RouterResult:
    names = ", ".join(t.value for t in CognitiveTask)
    return refuse(operation, INPUT_INVALID, f"{task!r} is not a cognitive task; use one of: {names}",
                  evidence=_evidence(), **ids, **fields)


def _bad_providers(providers: Iterable[str] | None) -> str:
    unknown = sorted(set(providers or ()) - set(KNOWN_PROVIDERS))
    return f"unknown provider {', '.join(unknown)}; use {' or '.join(KNOWN_PROVIDERS)}" if unknown else ""


def _where(providers: Iterable[str] | None, local_only: bool = False) -> str:
    where = "any provider" if providers is None else "the providers " + ", ".join(providers)
    return where + (", local models only" if local_only else "")


def _total(prof: ModelProfile) -> float:
    prompt, completion = prof.prices()
    return prompt + completion if prompt is not None and completion is not None else math.inf


def _score(prof: ModelProfile, task: CognitiveTask) -> float:
    return prof.task_scores.get(task.value, 0.0)


def _rank_key(prof: ModelProfile, task: CognitiveTask) -> tuple:
    return -_score(prof, task), 0 if prof.is_local else 1, _total(prof), prof.model_id


def _price_words(prof: ModelProfile) -> str:
    if prof.is_local:
        return "local (Ollama), no per-token price"
    prompt, completion = prof.prices()
    if prompt is None or completion is None:
        return "price variable or unknown"
    if prompt == 0 and completion == 0:
        return f"listed free on {prof.provider} as of {_date()}"
    return f"${prompt:g} in and ${completion:g} out per million tokens as of {_date()}"


def _row(prof: ModelProfile, task: CognitiveTask, rank: int) -> dict[str, Any]:
    prompt, completion = prof.prices()
    return {"rank": rank, "model_id": prof.model_id, "display_name": prof.display_name, "provider": prof.provider,
            "is_local": prof.is_local, "score": _score(prof, task), "prompt_usd_per_million": prompt,
            "completion_usd_per_million": completion, "price_known": prof.price_known,
            "context_window": prof.context_window,
            "reason": f"score {_score(prof, task):g} for {task.value} in the committed table; {_price_words(prof)}"}


def _scored(reg: CognitiveTaskRegistry, task: CognitiveTask, min_score: float = 0.0) -> list[ModelProfile]:
    return [reg.get_profile(mid) for mid, _ in reg.models_for_task(task, min_score=min_score)]


def list_tasks(*, registry: CognitiveTaskRegistry | None = None, request_id: str = "", run_id: str = "") -> RouterResult:
    """Every cognitive task, its kind of work (use case) and how many models in the table have a score for it."""
    reg = registry or default_registry()
    tasks = [{"task": t.value, "use_case": TASK_TO_USE_CASE[t].value, "models": len(reg.models_for_task(t))}
             for t in CognitiveTask]
    return answer("list_tasks", COMMITTED_TABLE, f"The cognitive tasks of G6's committed model table; "
                  f"{_table_words()}.", _TRY, evidence=_evidence(), tasks=tasks, count=len(tasks),
                  prices_as_of=_date(), **_ids(request_id, run_id))


def models_for_task(task: CognitiveTask | str, *, providers: Iterable[str] | None = None, local_only: bool = False,
                    min_score: float = 0.0, limit: int = 10, registry: CognitiveTaskRegistry | None = None,
                    request_id: str = "", run_id: str = "") -> RouterResult:
    """The models with a score for ``task``, ranked (:data:`RANKING`), each with the reason for its rank."""
    ids, t = _ids(request_id, run_id), _task(task)
    provs = None if providers is None else tuple(providers)
    if t is None:
        return _bad_task("models_for_task", task, ids, models=[])
    problem = _bad_providers(provs) or ("" if 0 <= min_score <= 1 else "min_score must be between 0 and 1") or (
        "" if limit >= 1 else "limit must be at least 1")
    if problem:
        return refuse("models_for_task", INPUT_INVALID, problem, evidence=_evidence(), models=[], **ids)
    reg = (registry or default_registry()).filtered(providers=provs, local_only=local_only)
    found = sorted(_scored(reg, t, min_score), key=lambda p: _rank_key(p, t))
    filters = {"providers": list(provs) if provs is not None else None, "local_only": local_only,
               "min_score": min_score}
    if not found:
        return refuse("models_for_task", NO_MODEL, f"no model in the committed table has a score of at least "
                      f"{min_score:g} for {t.value} among {_where(provs, local_only)}",
                      evidence=_evidence(task=t.value, filters=filters), task=t.value, models=[], **ids)
    rows = [_row(p, t, i + 1) for i, p in enumerate(found[:limit])]
    return _priced("models_for_task", COMMITTED_TABLE, f"Ranked from G6's committed model table: {_table_words()}. "
                   "The ranking is not verified.", _TRY, evidence=_evidence(task=t.value, filters=filters),
                   task=t.value, models=rows, considered=len(found), ranking=RANKING, prices_as_of=_date(), **ids)


def _tilted(found: list[ModelProfile], t: CognitiveTask, store: OutcomeStore, seed: int | None,
            local_first: bool) -> tuple[list[ModelProfile], dict[str, Any], dict[str, dict[str, Any]], str]:
    """``found`` reordered by the store's bounded rule; what the evidence says; each model's recorded counts and
    tilt; and the sentence the card adds."""
    seed = secrets.randbelow(2**32) if seed is None else seed
    info: dict[str, Any] = {"kind": "recorded_outcomes", "checked": False, "store": STORE, "rule": RULE, "seed": seed,
                            "min_outcomes": MIN_OUTCOMES, "weight": OUTCOME_WEIGHT,
                            "source": "reported by callers, or a FallbackCaller's failures; G6 checked none of them"}
    try:
        counts = store.counts(t.value, [p.model_id for p in found])
    except OutcomeStoreError as exc:
        return found, {**info, "applied": False, "outcomes": None, "reason": str(exc)}, {}, (
            " The outcome store could not be read, so the committed order stands.")
    tilt, total = tilts(counts, seed=seed), sum(s + f for s, f in counts.values())
    applied = total >= MIN_OUTCOMES
    lead = (lambda p: 0 if p.is_local else 1) if local_first else (lambda p: 0)
    found = sorted(found, key=lambda p: (lead(p), -(_score(p, t) + tilt[p.model_id]), *_rank_key(p, t)[1:]))
    per = {m: {"recorded": {"successes": s, "failures": f}, "tilt": round(tilt[m], 6)} for m, (s, f) in counts.items()}
    said = (f" {total} recorded outcomes, which callers reported and G6 did not check, tilted each committed score by "
            f"at most {OUTCOME_WEIGHT / 2:g}." if applied else
            f" {total} recorded outcomes, fewer than {MIN_OUTCOMES} and not checked by G6, tilted nothing.")
    return found, {**info, "applied": applied, "outcomes": total}, per, said


def route(task: CognitiveTask | str, *, providers: Iterable[str] = LOCAL_ONLY, fallbacks: int = 2,
          installed: Iterable[str] | None = None, local_first: bool = True,
          registry: CognitiveTaskRegistry | None = None, outcomes: OutcomeStore | None = None,
          seed: int | None = None, request_id: str = "", run_id: str = "") -> RouterResult:
    """One model for ``task`` and up to ``fallbacks`` more, in order, drawn only from ``providers`` (Ollama unless
    told otherwise). ``installed`` (local model names) keeps to the local models you have. Reads only the table (and,
    given ``outcomes``, the outcomes recorded there, under their bounded rule with ``seed``, chosen and recorded when
    not given): it does not check that a model is installed or reachable."""
    ids, t, provs = _ids(request_id, run_id), _task(task), tuple(providers)
    empty: dict[str, Any] = {"model": None, "fallbacks": [], "chain": [], "providers": list(provs)}
    if t is None:
        return _bad_task("route", task, ids, **empty)
    problem = _bad_providers(provs) or ("" if provs else "allow at least one provider") or (
        "" if fallbacks >= 0 else "fallbacks must be 0 or more")
    if problem:
        return refuse("route", INPUT_INVALID, problem, evidence=_evidence(), **empty, **ids)
    base = registry or default_registry()
    not_considered = dict(sorted(Counter(p.provider for p in _scored(base, t) if p.provider not in provs).items()))
    names = None if installed is None else list(installed)
    reg = base.filtered(providers=provs, installed=names)
    key = (lambda p: (0 if p.is_local else 1, *_rank_key(p, t))) if local_first else (lambda p: _rank_key(p, t))
    found = sorted(_scored(reg, t), key=key)
    evidence = _evidence(task=t.value, providers=list(provs), installed=names, local_first=local_first)
    if not found:
        kept = " installed" if names is not None else ""
        return refuse("route", NO_MODEL, f"no{kept} model in the committed table has a score for {t.value} among "
                      f"{_where(provs)}", evidence=evidence, task=t.value, not_considered=not_considered, **empty,
                      **ids)
    per: dict[str, dict[str, Any]] = {}
    said = ""
    if outcomes is not None and found:
        found, evidence["recorded_outcomes"], per, said = _tilted(found, t, outcomes, seed, local_first)
    rows = [{**_row(p, t, i + 1), **per.get(p.model_id, {})} for i, p in enumerate(found[:1 + fallbacks])]
    return _priced(
        "route", COMMITTED_TABLE,
        f"Chosen from G6's committed model table among the providers you allowed ({', '.join(provs)}): "
        f"{_table_words()}. Whether each model is installed or reachable was not checked here, and the route is not "
        f"verified.{said}",
        "Call the chain through TaskRouter, which tries each model in order and records why any failed; pass "
        "installed to keep to the local models you have.",
        evidence=evidence, task=t.value, providers=list(provs), model=rows[0], fallbacks=rows[1:],
        chain=[r["model_id"] for r in rows], order=(ROUTE_ORDER if local_first else RANKING) + (
            " Each score was first tilted by the recorded outcomes, by at most 0.05."
            if evidence.get("recorded_outcomes", {}).get("applied") else ""),
        not_considered=not_considered, prices_as_of=_date(), **ids)


def supervision_chain(task: CognitiveTask | str, *, providers: Iterable[str] | None = None,
                      specialists: Iterable[Specialist] | None = None, registry: CognitiveTaskRegistry | None = None,
                      request_id: str = "", run_id: str = "") -> RouterResult:
    """From the cheapest, best-scoring worker for ``task`` up: each model can supervise the one before it."""
    ids, t = _ids(request_id, run_id), _task(task)
    provs = None if providers is None else tuple(providers)
    if t is None:
        return _bad_task("supervision_chain", task, ids, chain=[])
    problem = _bad_providers(provs)
    if problem:
        return refuse("supervision_chain", INPUT_INVALID, problem, evidence=_evidence(), chain=[], **ids)
    reg = (registry or default_registry()).filtered(providers=provs)
    policy = SupervisionPolicy(reg, specialists=specialists)
    chain = policy.get_supervision_chain(t)
    links = []
    for i, mid in enumerate(chain):
        tier = get_tier(mid, reg)
        link = {"model_id": mid, "tier": tier.name, "requires_paid_api": tier >= ModelTier.COST_PAID_CHEAP,
                "role": "worker" if i == 0 else "supervisor", "specialist": False,
                "reason": f"the worker: the best score for {t.value} at the cheapest cost tier among {_where(provs)}"}
        if i:
            found = policy.get_supervisor(chain[i - 1], t, exclude=chain[:i])
            link["specialist"] = bool(found and found.specialist_override)
            why = (f"the table's specialist for {t.value}" if link["specialist"]
                   else f"a higher cost tier and at least its score for {t.value}" if tier > get_tier(chain[i - 1], reg)
                   else f"the same cost tier, with more complex tool use and a higher score for {t.value}")
            link["reason"] = f"supervises {chain[i - 1]}: {why}"
        links.append(link)
    paid = any(link["requires_paid_api"] for link in links)
    evidence = _evidence(task=t.value, providers=list(provs) if provs is not None else None)
    if not chain:
        return refuse("supervision_chain", NO_MODEL, f"no model in the committed table among {_where(provs)}",
                      evidence=evidence, task=t.value, chain=[], requires_paid_api=False, **ids)
    if len(chain) < 2:
        return refuse("supervision_chain", NO_SUPERVISOR, f"no model among {_where(provs)} can supervise {chain[0]} "
                      f"for {t.value}", evidence=evidence, task=t.value, chain=links, requires_paid_api=paid, **ids)
    return _priced(
        "supervision_chain", COMMITTED_TABLE,
        f"From G6's committed model table: cost tiers follow its prices and {_table_words()}. How often a supervisor "
        "catches its worker's mistakes has not been measured, so the chain is not verified.",
        "Check that each supervisor really catches the worker's mistakes on examples of your own task.",
        evidence=evidence, task=t.value, chain=links, requires_paid_api=paid, prices_as_of=_date(), **ids)


def model_profile(model: str, *, registry: CognitiveTaskRegistry | None = None, request_id: str = "",
                  run_id: str = "") -> RouterResult:
    """One model's committed profile, with the tasks it has a score for, highest first."""
    ids, reg = _ids(request_id, run_id), registry or default_registry()
    prof = reg.get_profile(model)
    if prof is None:
        return refuse("model_profile", UNKNOWN_MODEL, f"{model!r} is not in the committed model table",
                      evidence=_evidence(), model_id=model, **ids)
    profile = {k: v for k, v in prof.to_dict().items() if k != "task_scores"}
    profile["pricing_prompt_per_m"], profile["pricing_completion_per_m"] = prof.prices()
    profile["price_known"] = prof.price_known
    tasks = [{"task": t.value, "score": s} for t, s in reg.tasks_for_model(model)]
    return _priced("model_profile", COMMITTED_TABLE, f"A profile from G6's committed model table: {_table_words()}. "
                   "It is not verified.", "Check the provider's current figures before relying on one.",
                   evidence=_evidence(model_id=model), model_id=model, profile=profile, tasks=tasks,
                   prices_as_of=_date(), **ids)


def estimate_cost(model: str, input_tokens: int, output_tokens: int, *, registry: CognitiveTaskRegistry | None = None,
                  request_id: str = "", run_id: str = "") -> RouterResult:
    """What ``input_tokens`` in and ``output_tokens`` out would cost on ``model`` at the committed prices, with their
    date. Never more than a ``qualified-draft``; a variable or unknown price gives no estimate."""
    ids, reg = _ids(request_id, run_id), registry or default_registry()
    fields: dict[str, Any] = {"model_id": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
                              "estimated_usd": None, "prompt_usd_per_million": None,
                              "completion_usd_per_million": None, "prices_as_of": _date()}
    evidence = _evidence(model_id=model, input_tokens=input_tokens, output_tokens=output_tokens)
    counts = (input_tokens, output_tokens)
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts):
        return refuse("estimate_cost", INPUT_INVALID, "input_tokens and output_tokens must be whole numbers, 0 or more",
                      evidence=evidence, **fields, **ids)
    prof = reg.get_profile(model)
    if prof is None:
        return refuse("estimate_cost", UNKNOWN_MODEL, f"{model!r} is not in the committed model table",
                      evidence=evidence, **fields, **ids)
    prompt, completion = prof.prices()
    if prompt is None or completion is None:
        return refuse("estimate_cost", PRICE_UNKNOWN, f"the committed table has no fixed price for {model}: it is "
                      "variable or unknown, so no estimate is given", evidence=evidence, **fields, **ids)
    usd = round(input_tokens * prompt / 1_000_000 + output_tokens * completion / 1_000_000, 10)
    note = ("A local model has no per-token price; your hardware and electricity are not counted. " if prof.is_local
            else f"{prof.provider} listed this endpoint as free on {_date()}; free endpoints are often rate-limited "
                 "or withdrawn. " if prompt == 0 and completion == 0 else "")
    return _priced(
        "estimate_cost", COST_ESTIMATE,
        f"Estimated from G6's committed price table as of {_date()}: {input_tokens:,} input tokens at ${prompt:g} and "
        f"{output_tokens:,} output tokens at ${completion:g} per million. {note}Prices change and providers count "
        "tokens their own way, so this is an estimate and is not verified.",
        "Check the provider's current price before relying on the estimate.",
        evidence=evidence, **{**fields, "estimated_usd": usd, "prompt_usd_per_million": prompt,
                              "completion_usd_per_million": completion}, **ids)


def _recorded_evidence(**extra: Any) -> dict[str, Any]:
    return {"kind": "recorded_outcomes", "checked": False, "store": STORE, "network": False, **extra}


def record_outcome(task: CognitiveTask | str, model: str, success: bool, *, latency_ms: float = 0.0,
                   cost_usd: float = 0.0, reviewer_verdict: str = "", human_correction: str = "",
                   outcomes: OutcomeStore | None = None, registry: CognitiveTaskRegistry | None = None,
                   request_id: str = "", run_id: str = "") -> RouterResult:
    """Record how ``model`` did on ``task``, as your report, in the outcome store (default: the one under the G6
    home). G6 checks none of it; it can tilt a route only through the store's bounded rule."""
    ids, t = _ids(request_id, run_id), _task(task)
    fields: dict[str, Any] = {"model_id": model, "success": bool(success), "recorded_id": None}
    if t is None:
        return _bad_task("record_outcome", task, ids, **fields)
    if (registry or default_registry()).get_profile(model) is None:
        return refuse("record_outcome", UNKNOWN_MODEL, f"{model!r} is not in the committed model table",
                      evidence=_recorded_evidence(), task=t.value, **fields, **ids)
    store = outcomes or OutcomeStore()
    try:
        row = store.record(t.value, model, success, latency_ms=latency_ms, cost_usd=cost_usd,
                           reviewer_verdict=reviewer_verdict, human_correction=human_correction)
        successes, failures = store.counts(t.value, [model])[model]
    except OutcomeStoreError as exc:
        return refuse("record_outcome", STORE_FAILED, str(exc), evidence=_recorded_evidence(), task=t.value,
                      **fields, **ids)
    return answer(
        "record_outcome", RECORDED_OUTCOMES,
        f"Recorded as your report: {model} {'succeeded' if success else 'failed'} at {t.value}. G6 did not check it; "
        f"it can tilt this model's score for {t.value} by at most {OUTCOME_WEIGHT / 2:g}, and only in a route that "
        "asks to use recorded outcomes.",
        "Record outcomes you observed yourself; a route asked to use them says how many it read.",
        evidence=_recorded_evidence(recorded_id=row), task=t.value, **{**fields, "recorded_id": row},
        successes=successes, failures=failures, rule=RULE, **ids)


def outcome_stats(task: CognitiveTask | str, *, outcomes: OutcomeStore | None = None, request_id: str = "",
                  run_id: str = "") -> RouterResult:
    """The outcomes recorded for ``task``, per model: how many, how many succeeded, how many came from a
    FallbackCaller, and how often a reviewer's verdict disagreed with the reported success or a person corrected one.
    It ranks nothing."""
    ids, t = _ids(request_id, run_id), _task(task)
    if t is None:
        return _bad_task("outcome_stats", task, ids, models=[])
    try:
        models = (outcomes or OutcomeStore()).stats(t.value)
    except OutcomeStoreError as exc:
        return refuse("outcome_stats", STORE_FAILED, str(exc), evidence=_recorded_evidence(), task=t.value,
                      models=[], **ids)
    total = {k: sum(m[k] for m in models) for k in ("outcomes", "with_verdict", "verdict_disagreements",
                                                     "human_corrections")}
    total["verdict_disagreement_rate"] = (total["verdict_disagreements"] / total["with_verdict"]
                                          if total["with_verdict"] else None)
    total["human_correction_rate"] = total["human_corrections"] / total["outcomes"] if total["outcomes"] else None
    return answer(
        "outcome_stats", RECORDED_OUTCOMES,
        f"{total['outcomes']} outcomes recorded for {t.value}, as callers reported them; G6 checked none of them, and "
        "these counts rank nothing.",
        "Read a high disagreement or correction rate as a model to try on your own examples, not as a measurement.",
        evidence=_recorded_evidence(outcomes=total["outcomes"]), task=t.value, models=models, totals=total, **ids)


__all__ = ["KNOWN_PROVIDERS", "LOCAL_ONLY", "RANKING", "ROUTE_ORDER", "STALE_AFTER_DAYS", "estimate_cost", "list_tasks",
           "model_profile", "models_for_task", "outcome_stats", "record_outcome", "route", "supervision_chain"]
