# SPDX-License-Identifier: Apache-2.0
"""g6.optimisation — solve linear, integer, constraint, routing, flow and scheduling problems with OR-Tools and non-linear ones with SciPy, and check every answer.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the OR-Tools one.

Capability label: ``implemented``.

    from g6.optimisation import solve_lp, solve_max_flow
    r = solve_lp({"doors": {}, "windows": {}},
                 [{"terms": {"doors": 1}, "ub": 4}, {"terms": {"windows": 2}, "ub": 12},
                  {"terms": {"doors": 3, "windows": 2}, "ub": 18}],
                 {"doors": 3, "windows": 5}, sense="maximise")
    r.objective, r.solution["values"], r.completion_state   # (36.0, {"doors": 2.0, "windows": 6.0}, "qualified-draft")
    r.certificate["duals"], r.check["certificate"]          # ([0.0, 1.5, 1.0], "duals")
    f = solve_max_flow([{"from": "s", "to": "t", "capacity": 3}], "s", "t")
    f.objective, f.solution["min_cut"]                       # (3.0, ["s"])

What it does, in process on OR-Tools (``pip install 'g6[optim]'``; imported only when an op runs):

* ``solve_lp``, a linear programme on GLOP and ``solve_mip``, a mixed-integer one on SCIP, both through MathOpt;
  ``solve_cp``, a constraint model on CP-SAT (integer variables, linear constraints, all-different groups, an
  optional linear objective);
* ``solve_knapsack`` on the knapsack solver, any number of weight dimensions; ``solve_job_shop`` on CP-SAT's interval
  variables, minimising the makespan;
* ``solve_assignment`` (linear sum assignment, square or rectangular, with forbidden pairs), ``solve_max_flow`` and
  ``solve_min_cost_flow`` on ``ortools.graph``; ``solve_routing`` on the routing library, with optional capacities and
  time windows.

And on SciPy (``pip install 'g6[optim-nonlinear]'``), for a non-linear objective over a box (merged from the archive's
``adapt_optimisation`` and ``adapt_pygad``; :mod:`g6.optimisation.nonlinear`):

* ``minimise``, a local method (L-BFGS-B, Nelder-Mead or Powell) from several starts, its answer called a local
  minimum, the different minima the starts found counted, the first-order condition checked;
* ``global_search``, differential evolution (integer variables, and discrete choices through a ``table``, included)
  or a backend registered with ``register_backend``, its answer called the best point found.

Their objective is a JSON expression tree read by G6's own interpreter, never code; the returned point is re-evaluated
by it and its bounds checked, and a search that misreports its value is refused with
``G6_E_OPTIM_CERTIFICATE_REFUTED``. Neither is ever called optimal.

Inputs are declarative data: a linear expression is ``{variable: coefficient}``, a constraint its terms plus bounds, a
network a list of arcs. Nothing a caller sends is run as code. Each op has size limits and a time limit of at most 60
seconds, each refused with its own code.

Every answer is checked in pure Python (:mod:`g6.optimisation.checks`) before it is reported, and an answer the check
contradicts is refused with ``G6_E_OPTIM_CERTIFICATE_REFUTED``. Where optimality can be shown without trusting the
solver, it is: a linear optimum by its duals, a maximum flow by a cut of equal capacity, an assignment or a minimum-cost
flow by its residual network having no negative cycle. A linear programme with no optimum is shown infeasible (the
least-violation programme's duals) or unbounded (a feasible point and an improving ray). An integer or constraint
optimum rests on the solver's bound, reported with the gap; a feasible answer not proven optimal is a
``qualified-draft`` whose card names the gap, and routes are never called optimal without proof. Infeasible,
unbounded, out of time with no answer: ``blocked-escalated``, each with its own ``G6_E_OPTIM_*`` code.

The best state is ``qualified-draft``. A solver's answer on your model is not a proof a person has inspected, so
nothing here is ``verified``. The model is yours: if it does not say what you mean, neither does the answer.
``OptimisationBlock`` makes every op callable over MCP (``g6_invoke``); none runs code, so none is sensitive.

Not ported from the archive's ``google_or_mcp``, and why:

* ``run_code``, which executed caller Python in process; and the formula strings for objectives and constraints;
* the pure-Python stand-ins for routing (nearest neighbour), knapsack (dynamic programming), flow (augmenting paths)
  and scheduling (greedy list scheduling), and the grid-search and greedy fallbacks used when OR-Tools was missing:
  every op now runs on OR-Tools itself, and refuses without it;
* the model store (save, get, list, update, delete, import, export), comparisons, search and visualisation, which need
  persistence or did no solving; and sensitivity analysis and shadow prices by perturbing a grid search, replaced by
  GLOP's own duals and reduced costs, checked;
* the ``pywraplp`` interface, whose GLOP presolve reported an unbounded programme as infeasible, and the stale
  install hints.
"""
from g6.optimisation import checks
from g6.optimisation.combinatorial import solve_job_shop, solve_knapsack
from g6.optimisation.envelope import Refusal, finish
from g6.optimisation.networks import solve_assignment, solve_max_flow, solve_min_cost_flow, solve_routing
from g6.optimisation.nonlinear import global_search, minimise, register_backend
from g6.optimisation.optimisation_block import OptimisationBlock, OptimisationInput
from g6.optimisation.programmes import solve_cp, solve_lp, solve_mip
from g6.optimisation.schema import OptimResult

__g6_component__ = True
__g6_block__ = "OptimisationBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()  # no op runs code, writes a file or reaches the network
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("rule_breaking_plan", "unproven_optimum", "wrong_optimiser")
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"optim": ("ortools",), "optim-nonlinear": ("scipy",)}

__all__ = ["OptimResult", "OptimisationBlock", "OptimisationInput", "Refusal", "checks", "finish", "global_search",
           "minimise", "register_backend", "solve_assignment", "solve_cp", "solve_job_shop", "solve_knapsack",
           "solve_lp", "solve_max_flow", "solve_min_cost_flow", "solve_mip", "solve_routing"]
