# SPDX-License-Identifier: Apache-2.0
"""g6.cogarch.cog_arch_gps — plan a sequence of steps to a goal by means-ends analysis, every plan replayed against its operators before it is offered.

An extension in the cogarch family (standard library only). ``solve`` is the General Problem Solver's means-ends
analysis over STRIPS operators (each with preconditions, an add list and a delete list): goals in the order given,
every conjunction checked again once reached, so a goal a later step undoes is caught, and other goal orders tried
when one fails. Every plan is then replayed against the declared operators, step by step, and a plan whose replay
fails is refused with the failing step. ``validate_plan`` is that replay on its own, for a plan from anywhere, a
model's included. Every search is bounded by depth, nodes, goal orders and time.

    from g6.cogarch.cog_arch_gps import CogArchGpsBlock, CogArchGpsInput
    ops = [{"name": "boil", "add_list": ["kettle_boiled"]},
           {"name": "pour", "preconditions": ["kettle_boiled"], "add_list": ["tea_made"]}]
    out = CogArchGpsBlock().infer(CogArchGpsInput(op="solve", goals=["tea_made"], operators=ops)).unwrap()
    out["plan"]   # ["boil", "pour"], replayed: every precondition held and the goal holds at the end

``encode`` asks a model (a ``g6.core.llm`` caller given to the block from Python) to write a problem from a
description; it is parsed into the operator schema and offered only with a plan that passed the replay. Operators are
passed in, or kept by name under G6's state folder (``keep_operators``). No op is sensitive: none reaches the network
or acts outside G6, and a kept set is written only under G6's own state folder and never overwritten.

Not ported from the archive's cog_arch_gps, and why: the "verified" label on a model's encoding (GPS-01); a solve
that answered "Solved" without replaying its plan, and an engine without the final goal check (GPS-02); goals held in
a set, so the plan depended on string hashing (GPS-03); the realtime bridge, the HAT gate and the analogy search,
which connected, gated and found nothing (GPS-04); the episodic store whose failure notes silently removed operators
(GPS-05); the claude CLI model door, the home-folder database, the second MCP server and the self-descriptions that
said verified (GPS-06); the linear-programming fallback, which g6.optimisation already provides (GPS-07); and the
scaffold.
"""
from g6.cogarch.cog_arch_gps import engine
from g6.cogarch.cog_arch_gps.block import SENSITIVE_OPS, CogArchGpsBlock, CogArchGpsInput, OperatorSpec, Problem

__g6_component__ = True
__g6_block__ = "CogArchGpsBlock"
__g6_model__ = "caller"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_family__ = "cogarch"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # nothing reaches the network or acts outside G6
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("no_plan_to_goal", "unchecked_plan")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "CogArchGpsBlock", "CogArchGpsInput", "OperatorSpec", "Problem", "engine"]
