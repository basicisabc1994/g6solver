# SPDX-License-Identifier: Apache-2.0
"""Exact Bayesian updates and checked posterior sampling: expose hidden uncertainty.

Beta-binomial, gamma-Poisson and normal means with known noise; exact model
evidence, prior sensitivity and posterior predictive checks. Seeded MH needs no
extra. Optional PyMC runs only as G6's fixed child, gated and bounded, with its
draws checked by G6. Best state qualified-draft; built, awaiting review.
"""
from g6.ml.adapt_bayesian.block import AdaptBayesianBlock, BayesianInput
from g6.ml.adapt_bayesian.pymc_child import COMPILER

__g6_component__ = True
__g6_block__ = "AdaptBayesianBlock"
__g6_family__ = "ml"
__g6_sensitive_ops__ = ("sample_pymc",)
__g6_requires__ = ("action_gating",)
__g6_mitigates__ = ("hidden_uncertainty",)
__g6_introduces__ = ("overstated_certainty",)
__g6_extras__ = {"bayes": ("pymc", "arviz", "pytensor")}
__g6_outside__ = (COMPILER,)
__g6_vetting__ = {
    "kinds": ("scientific_statistical",),
    "register": "g6/ml/adapt_bayesian/data/source_register.json",
    "independent_pass": "g6/ml/adapt_bayesian/data/rule_review.json",
    "signed_review": None,
}

__all__ = ["AdaptBayesianBlock", "BayesianInput"]