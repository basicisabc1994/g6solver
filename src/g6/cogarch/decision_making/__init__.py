# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.decision_making — standard, seeded decision rules for what to try next and how many retries to allow, each answer with its working and the same on every run.

An extension in the cogarch family (standard library only): value of information (EVPI, the expected information
gain of a binary test, Gaussian expected improvement), expected utility, Thompson sampling and UCB1, finite-MDP value
iteration, the next experiment within a budget (``plan_next_action``), and how many retries each task gets within a
budget (``retry_budget``). Inputs are validated fail closed (no NaN, no negative cost, probabilities that sum to 1,
never normalised); every random draw comes from the caller's seed; budgets only tighten. The probabilities the caller
gives decide every answer: the block does not estimate them.

    from g6.cogarch.decision_making import DecisionMakingBlock, DecisionMakingInput
    out = DecisionMakingBlock().infer(DecisionMakingInput(op="retry_budget", budget=10, tasks=[
        {"task_id": "deploy", "value": 10, "cost": 2, "failures": 1}])).unwrap()
    out["result"]["retries"]   # {"deploy": 3}: chances 1/3, 1/4 and 1/5 are worth a cost of 2 against a value of 10

From Python only: ``UCT`` over a generative model (a callable), ``record_decision`` (a ledger the caller holds), and
the live ``BoundedAllocator`` and ``BudgetedBandit``. No op is sensitive: nothing reaches the network, writes a file
or acts outside G6.

Kept from the archive's decision_making whole, with: the retry rule its page promised and its code lacked
(DECISION_MAKING-01), fields no op has refused (DECISION_MAKING-03), the gain-only pick of the next experiment stated
in its op description (DECISION_MAKING-02), every reply in the library's envelope, value iteration and UCT bounded in
time, and its unconverged answers and its errors refused with a code.
"""
from g6.cogarch.decision_making.bandits import UCB1, BudgetedBandit, ThompsonBernoulli
from g6.cogarch.decision_making.block import SENSITIVE_OPS, DecisionMakingBlock, DecisionMakingInput
from g6.cogarch.decision_making.budget import BoundedAllocator
from g6.cogarch.decision_making.mcts import UCT, GenerativeModel
from g6.cogarch.decision_making.mdp import MDP, Unfinished, value_iteration
from g6.cogarch.decision_making.policies import ExperimentCandidate, PlanNextActionPolicy, record_decision
from g6.cogarch.decision_making.retry import RetryBudgetPolicy, RetryTask
from g6.cogarch.decision_making.stopping import VoIThresholdStop
from g6.cogarch.decision_making.utility import LinearUtility, UtilityModel, expected_utility
from g6.cogarch.decision_making.voi import (
    expected_improvement,
    expected_information_gain,
    expected_value_of_perfect_information,
)

__g6_component__ = True
__g6_block__ = "DecisionMakingBlock"
__g6_family__ = "cogarch"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # nothing reaches the network, writes a file or acts outside G6
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("ad_hoc_choice",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["MDP", "SENSITIVE_OPS", "UCB1", "UCT", "BoundedAllocator", "BudgetedBandit", "DecisionMakingBlock",
           "DecisionMakingInput", "ExperimentCandidate", "GenerativeModel", "LinearUtility", "PlanNextActionPolicy",
           "RetryBudgetPolicy", "RetryTask", "ThompsonBernoulli", "Unfinished", "UtilityModel", "VoIThresholdStop",
           "expected_improvement", "expected_information_gain", "expected_utility",
           "expected_value_of_perfect_information", "record_decision", "value_iteration"]
