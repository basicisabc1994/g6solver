# SPDX-License-Identifier: Apache-2.0
"""g6.navigator — describe one operation from its contract, and check a plan of operation calls before anything runs.

g6.navigator — give one op's card from its parameter contract and its component, and check a plan of op calls
against the registry, the contracts and the authority rule before anything runs (audit 016 §11).

Capability label: ``implemented``.

    from g6.core.discovery import find                 # which component: the library's one ranker
    from g6.navigator import card, check_plan, run_plan
    card("workforce", "run_tests")      # purpose, maturity, authority, required/optional params, side effect, example
    steps = [{"component": "workforce", "op": "run_tests",
              "args": {"repo": "/path/to/repo", "tests": ["tests/test_x.py::test_x"]}}]
    report = check_plan(steps)          # every problem with its step and a G6_E_* code; "qualified-draft" at best
    report.valid, report.issues
    result = run_plan(steps, executor=my_executor)   # judged by check_plan first; with no executor nothing runs

``card`` reads the op's ``ParamContract`` (``g6.workforce.capabilities``; the contracts the library ships unless the
caller passes others), the component's own ``help`` line for the op and the maturity it declares; it is None for an op
that neither a contract nor the component's block names. ``ops_of`` lists the contracted and the block-listed ops, the
same inventory ``check_plan`` resolves against. ``check_plan`` checks that each step's component is registered, that
its op exists and is not sensitive, that its args fit the contract, that its declared side effect is within an optional
``max_effect``, and the authority rule of ``g6.navigator.authority``: an A0 op is refused, and an op that can act (or
declares no side effect) needs A2. The default authority reading is the library's own table, which measures only the
workforce bench's ops, so every other op is refused until the caller passes a reading it trusts. Steps take static
args: nothing checks that one step's output can feed the next. ``run_plan`` calls the injected executor (for example
``get_registry().invoke``) for each step in order and stops at the first failure. Nothing here says ``verified``: a
static check and a run of unchecked steps are both drafts. Whether these cards help an agent reach a correct call
faster than a flat listing has not been measured (audit 016 §6).

Not ported from the archive's ``navigator`` (audit 016), and why:

* ``op_catalog.find`` and the block's ``nav_discover`` / ``nav_search`` / ``nav_recommend``: ``g6.core.discovery`` is
  the library's one ranker over one inventory, and its ``describe`` covers a component (NAV-005, NAV-006, NAV-007);
* ``census_levels.json``, its source qualification and the level words: a 5,142-line snapshot, 99% unqualified, with
  host paths (NAV-011). Readiness is ``g6.workforce.capabilities``'s probe ladder; authority binds to contract digests;
* ``op_purposes.json`` (purposes belong in component metadata), ``_venture_twins``, ``_heavy``, ``reach`` and
  ``display_tier`` (hosted-plan wording);
* ``door_authority``'s hosted refusals, claim capping and catalogue answers: the rule is ``g6.navigator.authority``
  (proposed for ``g6.core``), and nothing here emits a ``verified`` to cap;
* ``effect_checker.py``: its (io, cost, auth, fail) inputs came from 16 hand-written clusters, not from the ops
  (NAV-003, NAV-004); an op's effect is its contract's ``side_effect``;
* every ``navigator_mcp`` module: the AST-scan registry and cluster map, routing, fallback chains and circuit breakers
  nothing fed, and the state machine (NAV-013); caller-declared agency levels and tier filters (NAV-012); the MCP
  "lifecycle" that launched nothing behind a billing gate and pricing upsell, with per-account pools (NAV-008,
  NAV-015); ``nav_self_heal`` (NAV-002); the paid Claude CLI spawn (NAV-009); the hard-coded graph, TF-IDF claims and
  stats (NAV-010); introspection with its constant maturity (NAV-014); delegation, orchestration, store and schema;
* ``patterns.py``, ``skills.py``, ``skill/`` and ``scripts/mcp_registry.json``: catalogues and a stdio server.
"""
from g6.navigator.authority import ACTS_BELOW_A2, NOT_CLEARED, Finding, judge, shipped_authority
from g6.navigator.catalog import CARD_CHARS, card, contract_for, ops_of, purpose
from g6.navigator.plan import Issue, PlanReport, PlanResult, StepCheck, StepRun, check_plan, run_plan

__g6_component__ = True
__g6_maturity__ = "beta"  # no block: Python API only
__g6_requires__: tuple[str, ...] = ("workforce",)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("plan_fails_midway",)
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["ACTS_BELOW_A2", "CARD_CHARS", "NOT_CLEARED", "Finding", "Issue", "PlanReport", "PlanResult", "StepCheck",
           "StepRun", "card", "check_plan", "contract_for", "judge", "ops_of", "purpose", "run_plan",
           "shipped_authority"]
