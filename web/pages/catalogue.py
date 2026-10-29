# SPDX-License-Identifier: Apache-2.0
"""G6's components as the site draws them, read from the component registry, so the pages cannot drift from the code.

``components()`` is what each component declares about itself: its name, maturity, the first line of its docstring and
the other components it requires, with ``PLAIN``'s words for someone new to G6. The home page's lattice and the
components page are drawn from it; ``core_groups()`` shows the formal methods engines together there.

A registered package inside a family package (``g6.<family>.<name>``), or one that declares its family
(``__g6_family__ = "integrations"``), is an extension, not part of the core harness: ``components()`` leaves it out,
and ``library_extensions()`` lists it under its family (the extension list, ``pages.extensions``, describes it from
its own package), unless it is built in a vetted area and still waiting for its certificate (``awaiting_review()``).
"""
from __future__ import annotations

from functools import lru_cache

from pages import extensions

# In plain words, for someone new to G6: what each core component is called, what it does for you, and the failure it
# guards against (owner, 2026-10-19: the hover labels must make sense to a new user). A test fails when a registered
# core component has no entry or an entry names no core component.
PLAIN: dict[str, tuple[str, str, str]] = {
    "abstraction": ("Search shortcuts",
                    "Learns simple patterns from the results of a costly search, each checked against real "
                    "evaluations, and suggests which tries to skip",
                    "spending a budget on blind trial and error"),
    "action_gating": ("The action gate",
                      "Before an agent acts, decides whether it may go ahead, must ask a person first, or is blocked",
                      "an agent taking a harmful or irreversible action nobody approved"),
    "action_logic": ("Rules for acting safely",
                     "Rules from research on how complex decisions fail: act in small steps, watch for early "
                     "warnings, and change course when the evidence turns",
                     "big unchecked moves and ignored warnings"),
    "algebra": ("The algebra checker",
                "Does exact algebra and calculus for you, such as simplifying a formula, solving an equation, or "
                "finding a derivative, an integral or the inverse of a matrix, and checks each answer a second, "
                "independent way before handing it over",
                "a wrong formula, root or integral stated with confidence"),
    "algorithm_design": ("The algorithm designer",
                         "Suggests textbook algorithms that fit the problem you describe, estimates what each would "
                         "cost within your limits, and runs a fixed set of standard algorithms on your data, checking "
                         "each answer a second way",
                         "reimplementing a method that already exists, or a plan called the best when nobody proved "
                         "it"),
    "causal": ("The cause-and-effect checker",
               "Works out from your diagram of what causes what whether data can show the effect of one thing on "
               "another and which variables to control for, then estimates the effect and tries to knock it down",
               "taking a correlation for a cause, or controlling for the wrong variables"),
    "cegis": ("Program synthesis",
              "Fills the gaps in a code template, then hunts for an input that breaks it, and repeats until none is "
              "left in the range you set",
              "generated code that only works on the examples it was shown"),
    "coding_agent_gate": ("The coding-agent check",
                          "Checks a coding agent's work before it acts and again before it says the job is done",
                          "an agent reporting a fix that the evidence does not back up"),
    "complexity": ("The change-impact map",
                   "Reads a Python package's source without running it, maps its modules, shared state and side "
                   "effects, and lists everything a change could reach and what it must keep",
                   "a change that alters behaviour outside the part it was meant to touch"),
    "context": ("The context builder",
                "Packs just the files and notes a model call needs, within a size limit, each marked with where it "
                "came from",
                "a model lost in too much context, or obeying text it should only read"),
    "csf": ("Safety bounds",
            "Works out an upper limit on how likely an agent you describe is to reach an unsafe state, and checks "
            "rules it must always keep",
            "unsafe outcomes nobody worked out in advance"),
    "datalog": ("The rule engine",
                "Works out everything that follows from your facts and rules, such as who reports to whom or what a "
                "change can reach, and shows how each answer was derived",
                "a conclusion your rules do not support, or one they imply that goes unnoticed"),
    "debate": ("Structured debate",
               "Has named roles, such as a proposer and a critic, argue a question in turn, and reports how far they "
               "agree",
               "taking one model's first answer unchallenged"),
    "distillation": ("Tested shortcuts",
                     "Checks a small function that stands in for repeated model calls: it runs it in a bounded child "
                     "process and tests it on new cases before anyone relies on it",
                     "a shortcut that breaks on cases it has not seen"),
    "epistemic_integrity": ("The claim judge",
                            "Grades the evidence behind each claim and holds back a high-stakes conclusion the evidence "
                            "cannot carry",
                            "confident conclusions built on weak evidence"),
    "evidence": ("Certificates",
                 "Gives the final verdict on a piece of work: verified, a qualified draft, or blocked, with every gap "
                 "named",
                 "work called verified when the evidence does not support it"),
    "formal": ("The logic checker",
               "Settles small logic questions exactly with a solver, and labels each answer for what it is",
               "reasoning slips a model would state with confidence"),
    "goal_engine": ("Goal plans",
                    "Holds the subtasks you declare as a tree and checks them against what your executor reports",
                    "long tasks that drift from the goal or overrun their budget"),
    "graph_reasoning": ("The network reasoner",
                        "Works out how true each label on a network is, such as who counts as popular or which link "
                        "can be trusted, as a range from false to true, from your facts and rules, and shows how each "
                        "range was reached",
                        "a conclusion stated with more certainty than the facts give, or rules that contradict each "
                        "other without anyone noticing"),
    "grounding": ("The source check",
                  "Finds the passages in your sources behind a claim and says how well they support it",
                  "made-up facts and quotes"),
    "guards": ("Tamper scanners",
               "Flags a fixed list of English phrasings of prompt injection; a paraphrase passes",
               "prompt injection in the listed wordings only, as a tripwire, and tampering with safeguards"),
    "intent_engine": ("The intent contract",
                      "Writes down what a task must achieve, what counts as failure and what is out of scope, and lists "
                      "what is still missing",
                      "solving the wrong problem"),
    "invariants": ("Honest labels",
                   "Checks that every result's label matches the evidence attached to it",
                   "results that overstate what was checked"),
    "lean": ("The proof checker",
             "Has Lean, a proof assistant, check a proof you wrote of a theorem you name, and confirms that it leaves "
             "no step unproved, assumes nothing beyond the standard rules and proves exactly the statement you give",
             "a proof that only looks finished, or one that proves a different statement from the one you meant"),
    "learning": ("Learned hints",
                 "Learns hints from an agent's failures, and keeps one only when side-by-side runs show it helps",
                 "repeating the same mistake, or lessons that sound right and do not help"),
    "metaharness": ("The self-tuning loop",
                    "Tunes how G6 runs a task you repeat: it proposes changes, tests each one on cases it has not seen, "
                    "and keeps one only when you approve",
                    "an AI setup that keeps making the same mistakes on your work"),
    "model_checking": ("The state machine checker",
                       "Checks a system you describe as states and the steps between them, such as a protocol or a "
                       "workflow: it finds a run of steps that breaks a rule or gets stuck, or shows the rule always "
                       "holds",
                       "a design that fails only in a rare order of events nobody tried"),
    "navigator": ("The plan checker",
                  "Checks a plan of G6 calls against each component's rules before anything runs",
                  "a plan that fails halfway through"),
    "optimisation": ("The optimiser",
                     "Finds the best plan within your limits, such as routes, schedules, assignments or a budget, and "
                     "checks each answer before handing it over",
                     "a plan that breaks one of your rules, or is called the best when nobody proved it"),
    "prolog": ("The Prolog runner",
               "Runs a question against a Prolog program you give it, with SWI-Prolog on your computer, accepting only "
               "plain facts, rules and a short list of standard operations, and checks every answer a second time "
               "before handing it over; it is not a sandbox, so run programs you do not trust inside your own",
               "a conclusion your rules do not support, or one they imply that goes unnoticed"),
    "reasoning": ("Fair comparisons",
                  "Budgets, vote counting and side-by-side tests for multi-step reasoning, so a gain is measured, not "
                  "assumed",
                  "a claimed gain that extra compute or luck explains"),
    "reliability_pipeline": ("Intake and premortem",
                             "Takes a task in, checks it can be done, and names how it could fail before work starts",
                             "starting work that was set up to fail"),
    "safety_engine": ("Run limits",
                      "Hard limits for one run: a stop switch, allowed actions, and ceilings on steps, tool calls, "
                      "files changed, time and cost; it also spots tests that were weakened",
                      "runaway runs, and agents that weaken tests to pass them"),
    "self_maintenance": ("The maintenance planner",
                         "Finds the stage of a measured workflow that limits it most and picks the next fix that raises "
                         "it most within a token budget, without running anything",
                         "effort spent on low-value tasks first"),
    "self_training": ("Change approval",
                      "Decides whether a proposed change to the harness may go live; safety rules and failed tests "
                      "can only make that decision stricter",
                      "a self-change that slips past its own checks"),
    "solver": ("The step-by-step solver",
               "Runs a goal through a fixed sequence of 15 problem-solving steps that you supply, and reports where "
               "it stopped",
               "skipped steps and unchecked answers"),
    "workforce": ("Coding missions",
                  "Fixes what done means before a coding task starts, has a person confirm it, then checks the agent's "
                  "change against it",
                  "moving the goalposts after the work is done"),
}

# The home page's periodic table of the core components (owner, 2026-10-20: "a periodic table of intelligence"): each
# one in the single column that says what it is for. A test fails when a registered core component is in no column, or
# in two, or a column names one that is not registered.
TABLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Learn", ("metaharness", "learning", "distillation", "abstraction", "cegis", "self_maintenance")),
    ("Know", ("context", "grounding", "causal", "graph_reasoning", "complexity")),
    ("Reason", ("formal", "lean", "algebra", "datalog", "prolog", "model_checking", "optimisation", "reasoning",
                "solver", "debate", "algorithm_design")),
    ("Plan", ("intent_engine", "reliability_pipeline", "goal_engine", "navigator", "workforce")),
    ("Act safely", ("action_gating", "action_logic", "safety_engine", "guards", "csf", "coding_agent_gate",
                    "self_training")),
    ("Report", ("evidence", "invariants", "epistemic_integrity")),
)


def table() -> list[tuple[str, list[dict]]]:
    """``TABLE`` with each element's short name ("Source check": the plain title without its "The") and what it does."""
    def short(title: str) -> str:
        title = title.removeprefix("The ")
        return title[:1].upper() + title[1:]
    return [(column, [{"name": n, "short": short(PLAIN[n][0]), "plain": PLAIN[n][1]} for n in names])
            for column, names in TABLE]


def _family(reg, name: str) -> str | None:
    return reg.refs[name].family or getattr(reg.get_module(name), "__g6_family__", None)


@lru_cache(maxsize=1)
def _rows() -> tuple[dict, ...]:
    from g6.core.registry import get_registry

    reg = get_registry()
    names = sorted(n for n in reg.names() if not _family(reg, n))
    rows = []
    for name in names:
        info, mod = reg.describe(name), reg.get_module(name)
        doc = ((mod.__doc__ if mod else "") or "").strip().splitlines()
        summary = doc[0].strip() if doc else ""
        if summary.startswith(reg.refs[name].module):          # "g6.formal — decide small logic questions ..."
            summary = summary.split("—", 1)[-1].strip()
        title, plain, guards = PLAIN[name]
        rows.append({"name": name, "maturity": info.get("maturity") or "unlabelled", "summary": summary.rstrip("."),
                     "title": title, "plain": plain, "guards": guards,
                     "requires": sorted(r for r in getattr(mod, "__g6_requires__", ()) if r in names),
                     "mcp": bool(getattr(mod, "__g6_block__", None))})
    return tuple(rows)


def components() -> list[dict]:
    """Every registered core component, sorted by name (a fresh copy of each row); extensions are not among them."""
    return [dict(r, requires=list(r["requires"])) for r in _rows()]


# The formal methods engines, shown together on the components page (owner, 2026-10-20: the formal family is a key
# feature). ``prolog`` is listed once its package is registered; a name not registered is left out, never invented.
FORMAL: tuple[str, ...] = ("formal", "algebra", "lean", "model_checking", "datalog", "cegis", "csf", "causal",
                           "optimisation", "graph_reasoning", "prolog")
# COPY: the two groups of the core harness list.
CORE_GROUPS = (
    {"key": "formal-methods", "title": "Formal methods",
     "lede": "Logic, proof, algebra, rules, state machines, optimisation and cause and effect, each settled by a solver, "
             "a prover or a rule engine rather than by a model's guess."},
    {"key": "checks-and-gates", "title": "Checks, gates and tuning",
     "lede": "The rest of the core harness: what checks, gates, grounds and tunes an agent's work."},
)


def core_groups() -> list[dict]:
    """The core harness in two groups: the formal methods engines in ``FORMAL``'s order, then the rest by name."""
    rows = components()
    by_name = {r["name"]: r for r in rows}
    formal = [by_name[n] for n in FORMAL if n in by_name]
    rest = [r for r in rows if r["name"] not in FORMAL]
    return [dict(CORE_GROUPS[0], items=formal), dict(CORE_GROUPS[1], items=rest)]


@lru_cache(maxsize=1)
def _extension_rows() -> tuple[dict, ...]:
    from g6.core.registry import get_registry

    reg = get_registry()
    rows = []
    for name in sorted(reg.names()):
        if _family(reg, name):
            info = reg.describe(name)
            waiting = extensions._awaiting(info["package"], extensions.vetting_required(name))
            rows.append({"name": name, "family": _family(reg, name), "package": info["package"],
                         "maturity": info.get("maturity") or "unlabelled", "mcp": bool(info.get("block")),
                         "sensitive_ops": tuple(info.get("sensitive_ops") or ()), "awaiting_review": waiting})
    return tuple(rows)


def family_members() -> list[dict]:
    """Every registered package that declares a family, sorted by name (copies): the extensions built so far."""
    return [dict(r, sensitive_ops=list(r["sensitive_ops"])) for r in _extension_rows()]


def library_extensions() -> list[dict]:
    """The extensions in the library: the family members not waiting for their certificate (``g6.core.vetting``)."""
    return [r for r in family_members() if not r["awaiting_review"]]


def awaiting_review() -> list[dict]:
    """The family members built in a vetted area whose certificate is not current: never counted as in the library."""
    return [r for r in family_members() if r["awaiting_review"]]


def lattice() -> dict:
    """The home page's lattice: every core component, with the meta-harness at its centre."""
    return {"centre": "metaharness", "nodes": components()}
