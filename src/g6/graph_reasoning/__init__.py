# SPDX-License-Identifier: Apache-2.0
"""g6.graph_reasoning — work out the truth interval of each label on a graph's nodes and edges from facts and rules, and check every interval before it is reported.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the annotated logic
over graphs, in the style of PyReason.

Capability label: ``implemented``.

    from g6.graph_reasoning import explain, query, reason
    social = '''
        friend(alice, bob). friend(bob, carol). friend(carol, dave) : [0.9, 1].
        popular(dave).
        popular(x) : [0.7, 1] <- friend(x, y) : [0.9, 1], popular(y) : [0.7, 1].
    '''
    r = query(social, "popular(alice)")
    r.answer["bounds"], r.answer["status"]                  # ([0.7, 1.0], "between")
    r.completion_state, r.warning_card["code"]              # ("qualified-draft", "G6_E_GRAPH_REASONING_ONE_ENGINE")
    explain(social, "popular(alice)").explanation["derivation"]["by"][0]["from"]
    # [{"atom": "friend(alice, bob)", ...}, {"atom": "popular(bob)", ...}], each down to the facts

What it does, in process:

* reads annotated logic over a graph with its own parser (:mod:`g6.graph_reasoning.language`): nodes and directed
  edges, facts that give a label on a node or an edge a truth interval ``[lower, upper]`` within ``[0, 1]``
  (``[1, 1]`` true, ``[0, 0]`` false, ``[0, 1]`` unknown), node rules and edge rules, clause thresholds (``at least
  2``, ``at least 50%``) and annotation functions from a fixed set (``average``, ``average_lower``, ``maximum``,
  ``minimum``, ``product``), never caller code. The graph and facts may also come as JSON. A model that does not parse,
  an interval with its lower bound above its upper bound or outside ``[0, 1]``, a label with two arities, a label
  nothing gives, a vacuous clause, an unsafe rule, an annotation function or a percentage threshold reading a label
  that depends on its own head, and models over the size and time limits (at most 60 seconds) are each refused with
  their own ``G6_E_GRAPH_REASONING_*`` code, the line and the column;
* computes one static fixpoint, with no timesteps, on a pure-Python engine over exact rational intervals
  (:mod:`g6.graph_reasoning.engine`): every rule that fires intersects its head's interval with the rule's, stratum by
  stratum, until nothing changes, recording each step as its certificate. A step that would leave an interval empty is
  a conflict, blocked with ``G6_E_GRAPH_REASONING_CONFLICT`` and both sides' derivations; it is never resolved
  silently (PyReason, by default, resets such an atom to ``[0, 1]`` and freezes it);
* checks the interpretation in pure Python before any answer is built from it (:mod:`g6.graph_reasoning.checks`, its
  own matcher): closure (applying every rule once more tightens nothing), support (every interval comes from a fact or
  a rule application whose body held, at intervals established at a lower rank, so nothing rests on itself) and
  soundness (every interval within ``[0, 1]`` and not empty). An interpretation the check contradicts is refused with
  ``G6_E_GRAPH_REASONING_CERTIFICATE_REFUTED``;
* with PyReason importable, runs the same model through PyReason too (:mod:`g6.graph_reasoning.pyreason_engine`,
  imported only when an op runs), when the model is one PyReason evaluates as G6 does, and compares every interval
  within 1e-9 (``G6_E_GRAPH_REASONING_ENGINES_DISAGREE`` otherwise). PyReason 3.7.0, its latest release, does not
  import on this library's Python with its dependencies (it needs ``pkg_resources``, gone from setuptools 81, and its
  numba code does not compile on Python 3.12), so it is not an extra; without it the card says only the pure-Python
  engine ran, and why.

Ops: ``reason`` (the interpretation, bounded), ``query`` (one atom's interval and the steps that set it), ``explain``
(its derivation down to the facts, or why it stayed unknown) and ``check`` (the parse, safety, stratification and size
report, nothing evaluated).

The best state is ``qualified-draft``. The interpretation is what your graph and rules say, not a proof a person has
inspected, so nothing here is ``verified``. ``GraphReasoningBlock`` makes every op callable over MCP (``g6_invoke``);
none runs code, so none is sensitive.

Not ported from the archive's ``pyreason_mcp``, and why:

* PyReason as it was wired in: the earlier version never called it (an import only decided whether to print a
  fallback notice), through a shim that put a stand-in ``pkg_resources`` into ``sys.modules`` and patched
  ``yaml.safe_load`` for the whole process. Neither is ported; PyReason is used only when it imports as it is;
* its pure-Python engine: one floating-point confidence per label rather than an interval, rules read by regular
  expressions that kept any unreadable text as a rule head, a fixed 0.5 threshold that hid lower confidences from the
  answer, and a "temporal" mode that re-ran the same fixpoint per step. The engine here is new, and every answer it
  gives is checked;
* ``run_code``, which executed caller Python, and the graph store (create, save, get, list, update, delete, compare,
  validate) with its in-memory cache and standalone server, which need persistence and are not reasoning;
* PyReason's timesteps, rule delays (``<-1``), facts over time ranges, static facts and rules, inconsistent-predicate
  pairs, closed-world predicates, head functions, edge inference, ground rules and comparison clauses: G6 computes one
  static fixpoint over a fixed graph, and refuses what it does not read rather than ignoring it.
"""
from g6.graph_reasoning import checks
from g6.graph_reasoning.envelope import Refusal, finish
from g6.graph_reasoning.graph_reasoning_block import GraphReasoningBlock, GraphReasoningInput
from g6.graph_reasoning.language import Model, parse_atom, parse_model
from g6.graph_reasoning.ops import check_model, explain, query, reason
from g6.graph_reasoning.schema import GraphReasoningResult

__g6_component__ = True
__g6_block__ = "GraphReasoningBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()  # no op runs code, writes a file or reaches the network
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("overstated_certainty", "contradictory_rules")
__g6_introduces__: tuple[str, ...] = ()

__all__ = ["GraphReasoningBlock", "GraphReasoningInput", "GraphReasoningResult", "Model", "Refusal", "check_model",
           "checks", "explain", "finish", "parse_atom", "parse_model", "query", "reason"]
