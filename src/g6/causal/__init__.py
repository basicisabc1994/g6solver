# SPDX-License-Identifier: Apache-2.0
"""g6.causal — work out from a causal graph whether an effect can be estimated and how, estimate it with DoWhy, and try to knock the estimate down.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the DoWhy one.

Capability label: ``implemented``.

    from g6.causal import adjustment_sets, estimate_effect, identify
    graph = "digraph { Z -> T; Z -> Y; T -> Y }"
    adjustment_sets(graph, "T", "Y").answer["sets"]          # [["Z"]]
    identify(graph, "T", "Y").answer["estimand"]              # "E[Y | do(T=t)] = sum over Z of E[Y | T=t, Z] * P(Z)"
    r = estimate_effect(graph, rows, "T", "Y")                # rows: [{"Z": 0.3, "T": 1.2, "Y": 2.9}, ...]
    r.estimate, r.confidence_interval, r.completion_state   # (2.01, [1.96, 2.05], "qualified-draft")
    [x["passed"] for x in r.refutations]                      # [True, True, True]

What it does, in process:

* reads a causal graph, as JSON or a strict subset of DOT, with G6's own parser (:mod:`g6.causal.graph`); cycles,
  self-loops, undeclared names and graphs over the size limits are refused, each with its own ``G6_E_CAUSAL_*`` code;
* answers graph questions with exact algorithms in pure Python (:mod:`g6.causal.criteria`), needing no extra:
  ``d_separated``, ``backdoor_paths``, ``adjustment_sets`` (every minimal set to control for), ``instruments`` and
  ``identify`` (backdoor, frontdoor or instrumental variable, the estimand written out, or why the effect is not
  identifiable). With DoWhy installed (``pip install 'g6[causal]'``, imported only when an op runs) DoWhy answers too,
  and the two must agree (:mod:`g6.causal.checks`), or the answer is refused with ``G6_E_CAUSAL_ENGINES_DISAGREE``:
  DoWhy 0.14 names an unobserved parent of the treatment an instrument, and that is refused. Without DoWhy the card says
  only the pure-Python check ran;
* ``estimate_effect`` estimates the effect with DoWhy from inline rows (never a file) by linear regression,
  propensity-score stratification or weighting, an instrumental variable or the frontdoor, with its confidence interval.
  For linear regression G6 recomputes the estimate by least squares on its own (``G6_E_CAUSAL_CERTIFICATE_REFUTED``
  if it differs); DoWhy's placebo, random-common-cause and data-subset refuters then try to knock it down, with fixed
  seeds, judged by G6 against the confidence interval (``G6_E_CAUSAL_REFUTED``, keeping every number). A refuter
  that did not test the estimate is never counted as a pass: DoWhy 0.14 cannot run the random common cause on an
  instrumental-variable estimate, and on the frontdoor neither the placebo nor the random common cause reaches the
  two stages, which G6 sees because the effect comes back exactly the estimate (``REFUTATION_INCOMPLETE``). Turning
  refutation off is said on the card. Without DoWhy, estimation refuses and says what to install.

The best state is ``qualified-draft``. Every answer rests on the causal graph, which data cannot confirm, so nothing
here is ``verified``. ``CausalBlock`` makes every op callable over MCP (``g6_invoke``); none runs code, so none is
sensitive.

Not ported from the archive's ``dowhy_mcp``, and why:

* ``run_code``, which executed caller Python in process;
* its stand-in for DoWhy: it never called DoWhy (``import dowhy`` only decided whether to print a fallback notice, and
  the fallback was not flagged), and did the work with its own DOT parser, path search, least squares and t-test. The
  graph algorithms here are new and exact, and every estimate is DoWhy's, checked;
* the counterfactual, do-calculus, sensitivity, mediation and estimator-comparison ops, which computed on that stand-in;
  DoWhy's other estimators and refuters, which G6 does not check yet; and DoWhy's complete identification algorithm;
* the model store (create, save, get, list, update, delete), data loading from files, synthetic data, summaries,
  search, graph export and visualisation, which need files or persistence and are not inference.
"""
from g6.causal import checks, criteria
from g6.causal.causal_block import CausalBlock, CausalInput
from g6.causal.envelope import Refusal, finish
from g6.causal.estimation import estimate_effect
from g6.causal.graph import CausalGraph, read_graph
from g6.causal.ops import adjustment_sets, backdoor_paths, d_separated, identify, instruments
from g6.causal.schema import CausalResult

__g6_component__ = True
__g6_block__ = "CausalBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()  # no op runs code, writes a file or reaches the network
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("correlation_as_cause", "wrong_adjustment")
__g6_introduces__: tuple[str, ...] = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"causal": ("dowhy",)}

__all__ = ["CausalBlock", "CausalGraph", "CausalInput", "CausalResult", "Refusal", "adjustment_sets",
           "backdoor_paths", "checks", "criteria", "d_separated", "estimate_effect", "finish", "identify",
           "instruments", "read_graph"]
