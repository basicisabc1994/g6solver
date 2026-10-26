# SPDX-License-Identifier: Apache-2.0
"""Why G6 exists, as the home page tells it (owner, 2026-10-20): language models already hold the right kind of general
intelligence, and their limits come from how they are built, not from what they know. So G6 keeps the model and
answers each limit in the harness around it. Each limit names the core components that answer it (the home page's
lattice lights them as the limit scrolls past) and the essay that goes deeper, so a test can hold the copy to the
registry and the page table."""
from __future__ import annotations

# (key, the limit, why it happens, what G6 does, the core components that do it, the essay's URL name or None)
LIMITS: tuple[dict, ...] = tuple(
    {"key": key, "title": title, "cause": cause, "answer": answer, "parts": parts, "essay": essay}
    for key, title, cause, answer, parts, essay in (
        ("hallucination", "They make things up",
         "A model writes the likeliest words, true or not.",
         "G6 checks each claim against your sources and labels every result for what the evidence supports.",
         ("grounding", "epistemic_integrity", "evidence"), "hallucination_is_design"),
        ("memory", "They forget, and read only so much",
         "Nothing carries over between conversations, and the context window is finite.",
         "G6 gives each call just what it needs, marked with its source, and keeps lessons as hints you approve.",
         ("context", "learning"), "context_window_is_not_memory"),
        ("learning", "They cannot learn on the job",
         "What a model knows is fixed in its weights, and retraining can erase it.",
         "G6 learns in the harness instead, so your model never changes and forgets nothing.",
         ("metaharness", "learning", "self_training"), "learning_without_forgetting"),
        ("reasoning", "They slip on logic and exact work",
         "Matching patterns makes a wrong sum read as right.",
         "G6 hands exact work to solvers, a proof checker, algebra and rule engines, and checks what they return.",
         ("formal", "lean", "algebra", "datalog", "model_checking", "optimisation"), "formal_verification_safety_net"),
        ("algorithms", "They redo routine work from scratch",
         "Every call solves the problem afresh, at full cost.",
         "G6 synthesises programs, and swaps repeated calls for small functions that pass tests on new cases.",
         ("cegis", "distillation"), "algorithms_missing_intelligence"),
        ("benchmarks", "They ace benchmarks, then stumble at work",
         "Public tests reward what is easy to score, and may be in the training data.",
         "G6 judges every change on your own held-out examples, never on a public score.",
         ("metaharness", "reasoning"), "why_benchmarks_lie"),
        ("metacognition", "They do not know what they do not know",
         "To a model, a guess and a fact sound the same.",
         "Every G6 result says what was checked: verified, a qualified draft, or blocked with the reason.",
         ("epistemic_integrity", "evidence", "invariants"), "metacognition_missing_skill"),
        ("done", "They say it is done when it is not",
         "Nothing inside an agent checks its own finish.",
         "G6 fixes what done means before work starts, and holds the agent's report to the evidence.",
         ("coding_agent_gate", "workforce", "safety_engine"), None),
        ("safety", "They are unsafe to let act, or improve themselves",
         "A model has no brakes, and could rewrite its own checks.",
         "G6 gates every action, keeps a stop switch, and puts no change live without evidence and your approval.",
         ("action_gating", "safety_engine", "guards", "self_training"), "self_improvement_safety"),
        ("cost", "The best models cost the most",
         "Frontier models charge for every call, and most work repeats.",
         "A frontier model engineers the harness once; a small model on your own computer then runs it, with no fee "
         "per call.",
         ("metaharness", "distillation"), "economics_of_intelligence"),
    ))


def essay_path(name: str) -> str:
    return f"/essays/{name.replace('_', '-')}/"
