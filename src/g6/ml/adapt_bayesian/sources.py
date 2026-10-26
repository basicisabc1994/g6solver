# SPDX-License-Identifier: Apache-2.0
"""Offline source register validation, rule references and review-age findings.

Corpus hashes identify owner-held files, never bundled text. Web hashes identify
exact recorded clauses supplied by the owner. No network request or review is
performed here. The generic weekly byte-fetch monitor cannot compare these two
hash scopes directly; the register records that limitation explicitly.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Literal

from g6.ml._types import INPUT_INVALID, MLError

FIELDS = ("id", "issuing_body", "title", "edition", "published", "jurisdiction", "url", "licence", "retrieved",
          "sha256", "clause", "section", "review_months", "hash_scope")


@dataclass(frozen=True)
class Rule:
    """One rule. ``source`` is a register entry's id, ``"g6"`` for a guard on a threshold of G6's own, or the caller's
    input; ``effect`` is what a guard (``own``) can do to an answer: refuse, escalate, lower_label or note; ``tier``
    is what its certificate checks and ``implements`` the function that applies it (``path:function``)."""

    rule: str
    kind: Literal["register", "own", "caller"]
    source: str
    effect: str = ""
    tier: str = ""
    implements: str = ""


RULES = {
    "beta": Rule("conjugate beta update: a' = a + the successes, b' = b + the failures", "register", "bolstad-14",
                 tier="computes", implements="g6/ml/adapt_bayesian/exact.py:_beta_posterior"),
    "moments": Rule("the posterior's mean, variance, standard deviation and mode, for the beta, gamma and normal "
                    "posteriors", "register", "bolstad-14", tier="computes",
                    implements="g6/ml/adapt_bayesian/exact.py:summary"),
    "credible_interval": Rule("the posterior's median and equal-tailed credible interval, from its quantiles", "register",
                              "bolstad-14", tier="computes", implements="g6/ml/adapt_bayesian/exact.py:quantile"),
    "gamma": Rule("gamma shape-rate update and explicit positive-uniform prior", "register", "bolstad-17",
                  tier="computes", implements="g6/ml/adapt_bayesian/exact.py:_gamma_posterior"),
    "normal": Rule("normal precision update with separately known likelihood noise", "register", "bolstad-20",
                   tier="computes", implements="g6/ml/adapt_bayesian/exact.py:_normal_posterior"),
    "beta_cdf": Rule("regularised beta CDF, symmetry and continued fraction (at most 10,000 terms to a relative "
                     "tolerance of 2e-14, with a Lentz nonzero floor of 1e-300; refused when it does not converge)",
                     "register", "dlmf-beta", tier="property", implements="g6/core/special.py:beta_cdf"),
    "gamma_cdf": Rule("regularised gamma series and continued fraction (at most 10,000 terms to a relative tolerance "
                      "of 2e-14; refused when it does not converge)", "register", "dlmf-gamma", tier="property",
                      implements="g6/core/special.py:gamma_cdf"),
    "barber_example": Rule("flat prior, two heads and eight tails", "register", "barber-beta", tier="computes",
                           implements="g6/core/special.py:beta_cdf"),
    "evidence": Rule("marginal likelihood from normalised prior and likelihood, in its beta-binomial form (the "
                     "gamma-Poisson and normal forms are not checked here)", "register", "barber-evidence",
                     tier="property", implements="g6/ml/adapt_bayesian/exact.py:_beta_evidence"),
    "mh": Rule("symmetric random-walk Metropolis-Hastings accept/reject", "register", "barber-mh", tier="property",
               implements="g6/ml/adapt_bayesian/sampling.py:mh"),
    "convergence": Rule("split, ranks, folding, bulk and tail ESS; R-hat <1.01 and ESS >400", "register", "vehtari-2021",
                        tier="property", implements="g6/ml/adapt_bayesian/diagnostics.py:assess"),
    "prediction": Rule("posterior mixture for prediction", "register", "barber-prediction", tier="property",
                       implements="g6/ml/adapt_bayesian/predictive.py:replicated"),
    "prior_shift": Rule("warn if an alternative prior moves the posterior mean more than one baseline posterior sd (a "
                        "G6 descriptive warning; a small shift is not evidence a prior is justified)", "own", "g6",
                        "note", tier="guard", implements="g6/ml/adapt_bayesian/exact.py:prior_shift"),
    "misfit": Rule("warn if the observed statistic lies outside the stated predictive interval (a G6 warning; no "
                   "calibrated hypothesis test or assurance of fit); refused instead for an unregistered statistic, "
                   "more than 2,000,000 replicated observations, a variance of fewer than two observations or a "
                   "non-finite observed statistic", "own", "g6", "note", tier="guard",
                   implements="g6/ml/adapt_bayesian/predictive.py:check"),
    "inputs": Rule("model, prior, noise, counts, credible mass and alternative priors, within the typed input's ranges "
                   "(positive parameters .01 to 10,000; means within 1,000,000 of zero; binomial trials at most "
                   "10,000; at most 10 alternatives; mass .5 to .999; seed 0 to 2**32-1; 8 to 4,000 draws, 2 to 8 "
                   "chains (default 4), 0 to 4,000 warmup; a proposal sd multiplier 1e-12 to 100 (default 2.4); at "
                   "most 120 seconds (default 30); 50 to 1,000 predictive replicates and at most 2 million replicated "
                   "observations and Poisson arrivals; dataset columns limited to the declared target and features, "
                   "text to 10,000 characters a cell and 5,000,000 in all, checked before extraction or hashing; the "
                   "PyMC child within the family's 1,536 MB and 32 MB output caps), refused beyond them", "caller",
                   "BayesianInput"),
    "numerics": Rule("a quantile is the root of its CDF found by at most 200 bisections to a residual of 1e-12 in CDF "
                     "units (where the density is small the quantile itself can be further off); when they do not "
                     "reach it the calculation is refused, never answered approximately (a gamma quantile's bracket "
                     "is doubled at most 127 times)", "own", "g6", "refuse", tier="guard",
                     implements="g6/core/special.py:bisect_quantile"),
    "bounds": Rule("1 to 20,000 observations, each a finite number of magnitude at most 1,000,000 and, for a count "
                   "model, an integer count in the model's support whose total is at most 1,000,000; anything else "
                   "is refused, as are neither or both of observations and a dataset, and a dataset row with an "
                   "undeclared column or text beyond the family's limits", "own", "g6", "refuse", tier="guard",
                   implements="g6/ml/adapt_bayesian/models.py:observations"),
    "sampler_metadata": Rule("finite in-support draws, complete chains with no exact duplicates, and integer zero PyMC "
                             "divergences, or the sample is refused; it is refused too when R-hat is not below 1.01 "
                             "or the bulk or tail ESS is not above 400 (rule convergence's thresholds)", "own", "g6",
                             "refuse", tier="guard",
                             implements="g6/ml/adapt_bayesian/sampling.py:checked"),
}


def register() -> dict:
    text = resources.files("g6.ml.adapt_bayesian").joinpath("data/source_register.json").read_text(encoding="utf-8")
    return json.loads(text)


def entries() -> dict:
    return {e["id"]: e for e in register()["entries"]}


def register_problems() -> list[str]:
    out = []
    try:
        raw = register()["entries"]
        found = {e["id"]: e for e in raw}
        if len(found) != len(raw):
            out.append("duplicate source id")
        for name, e in found.items():
            if any(e.get(k) in (None, "", []) for k in FIELDS):
                out.append(f"{name}: required register field missing")
            if not re.fullmatch(r"[0-9a-f]{64}", str(e.get("sha256", ""))):
                out.append(f"{name}: malformed SHA-256")
            dt.date.fromisoformat(e["retrieved"])
            if not isinstance(e["review_months"], int) or e["review_months"] < 1:
                out.append(f"{name}: invalid review window")
            if e["hash_scope"] == "clause_utf8":
                if hashlib.sha256(e["clause"].encode("utf-8")).hexdigest() != e["sha256"]:
                    out.append(f"{name}: recorded clause hash differs")
            elif e["hash_scope"] == "corpus_file":
                if hashlib.sha256(e["clause"].encode("utf-8")).hexdigest() != e.get("clause_sha256"):
                    out.append(f"{name}: recorded clause hash differs")
                # A withheld source (all rights reserved) names no corpus file: its held copy's SHA-256 identifies it.
                file_name = str(e.get("file_name", ""))
                if (not file_name and e.get("withheld") is not True) or "/" in file_name or "\\" in file_name:
                    out.append(f"{name}: corpus source needs a file name unless withheld, never a local path")
            elif e["hash_scope"] == "retrieved_bytes":  # page or file bytes held at issuance, hashed whole
                if hashlib.sha256(e["clause"].encode("utf-8")).hexdigest() != e.get("clause_sha256"):
                    out.append(f"{name}: recorded clause hash differs")
                if not str(e.get("url", "")).startswith("https://"):
                    out.append(f"{name}: a retrieved source names the address its bytes came from")
            else:
                out.append(f"{name}: unknown hash scope")
        for name, rule in RULES.items():
            if rule.kind == "register" and rule.source not in found:
                out.append(f"{name}: source {rule.source} missing")
            if rule.kind == "own" and not rule.effect:
                out.append(f"{name}: own rule must state its effect")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        out.append(f"source register unreadable: {type(exc).__name__}")
    return out


def stale_sources(today: dt.date | None = None) -> list[dict]:
    today = today or dt.date.today()
    stale = []
    for e in entries().values():
        day = dt.date.fromisoformat(e["retrieved"])
        months = day.year*12 + day.month-1 + e["review_months"]
        year, month = months//12, months % 12 + 1
        due = dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
        if today > due:
            stale.append({"finding": "stale_source", "source": e["id"], "review_due": due.isoformat()})
    return stale


def ensure() -> list[dict]:
    problems = register_problems()
    if problems:
        raise MLError(INPUT_INVALID, "cannot assess: source register is incomplete or inconsistent", [
            {"kind": "source_register", "problems": problems}])
    return [{"kind": "bayesian_sources", "sources": sorted(entries()), "rules": list(RULES),
             "stale_sources": stale_sources(), "review_status": "built, awaiting review",
             "monitoring": register()["monitoring"]}]