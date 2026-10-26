# SPDX-License-Identifier: Apache-2.0
"""The theory behind G6, as the Research page lists it: each document in ``docs/theory/``, its full title with no
abbreviation, what it is, its key points as the document itself states them, and what it rests on (owner,
2026-10-19). A test checks that every file named here exists."""
from __future__ import annotations

REPO = "https://github.com/basicisabc1994/g6solver/blob/main/docs/theory/"

DOCUMENTS: tuple[dict, ...] = (
    {"file": "G6_Unified_Theory_of_Agentic_Intelligence.md",
     "title": "A unified mathematical theory of agentic intelligence",
     "what": "Twenty-five models of self-correction, token use, delegation, decomposition, debate and scaling, fitted "
             "to G6's benchmark runs of March to May 2026.",
     "points": ("Empirically calibrated models, not proved theorems.",
                "Accuracy rises towards a ceiling as the harness learns; the theory loop levels off at about 17 to 20 "
                "theories.",
                "Errors compound along a chain of steps, and a model checking its own work shares its own mistakes, "
                "so a small model cannot verify itself.",
                "Estimates how much a harness adds to a model, in accuracy and in tokens."),
     "basis": "Benchmark runs: GAIA, Omni-MATH, SWE-bench Pro, MATH-500, MetaTool and MedXpertQA."},
    {"file": "G6_Theoretical_Foundations.md",
     "title": "Theoretical foundations",
     "what": "An essay placing G6 in the history of AI: language models as perception, symbolic search as reasoning.",
     "points": ("Argues that intelligence is a combinatorial optimisation problem: models propose, search and "
                "checks decide.",
                "Uses category theory (the Yoneda lemma, sheaves) as an illustrative analogy, and the correspondence "
                "between proofs and programs.",
                "Proposes a compression conjecture: caching verified algorithms turns statistical guessing into "
                "deterministic lookup.",
                "Maps the four levels of adaptation in practopoiesis onto G6's self-training levels."),
     "basis": "Theory, drawing on the unified theory's benchmark runs."},
    {"file": "G6_Self_Validation_Paradox_Resolution.md",
     "title": "The validation paradox: how G6 can learn when it cannot always check its own answers",
     "what": "An essay arguing that G6 can learn from its failures without a universal checker of correctness.",
     "points": ("Separates checking one result, checking a learning step and checking general competence: progress "
                "on the second does not need the third.",
                "A five-level ladder of checks, from a formal certificate to no evidence that discriminates.",
                "The true gain is at least the measured gain minus the evaluator's errors: a 5-point gain measured "
                "with 4 points of evaluator error cannot be shown to be real.",
                "Learning may be speculative; deployment must be justified."),
     "basis": "Argument, drawing on an earlier G6 experiment with a detector of collateral edits."},
    {"file": "G6_Evidence_Constrained_Harness_Adaptation_Spec_v0.1.md",
     "title": "Evidence-constrained harness adaptation: an implementation specification",
     "what": "A build specification for the smallest loop in which G6 finds a confirmed defect, learns a safeguard it "
             "can run, and tests it on tasks it has not seen.",
     "points": ("No universal oracle of correctness: each standard and check is scoped, and G6 abstains, qualifies "
                "or escalates when evidence is missing.",
                "The milestone is one verified learning event: defect, diagnosis, safeguard, independent controls, "
                "transfer to an unseen repository, and a bounded claim.",
                "Five kinds of evidence, from a formal check to none that discriminates; finding no defect is not "
                "proof of correctness.",
                "The learner may never change the standards, hidden tests, evaluators or promotion rules."),
     "basis": "Designed from G6's earlier evidence experiments, 352 episodes per arm."},
    {"file": "G6_Computational_Structural_Intermediate_Representation.md",     # v0.3, rebuilt from the v0.2 PDF
     "title": "A computational structural intermediate representation for compositional, evidence-constrained and "
              "self-improving AI agents",
     "what": "A mathematical account of AI agents as typed systems that build, run, check and improve compositions of "
             "tools, keeping computing an answer apart from producing evidence about it.",
     "points": ("Keeps computation and evidence-producing checks apart, which it argues is essential to avoid the "
                "self-validation paradox.",
                "Proves, under stated assumptions, that reflecting without new information cannot break a genuine "
                "barrier, and that near-identical model reviewers are not independent layers of protection.",
                "Proposes, as hypotheses, a joint account of what an agent can solve and what it can justify."),
     "basis": "Theory, with a reference implementation and its tests."},
    {"file": "CSIR_ECHA_RESEARCH_PROTOCOL.md",
     "title": "An integrated research protocol: composition, evidence and learning",
     "what": "A proposal for experiments that measure separately what G6's typed mission graphs, its evidence policy "
             "and its adaptive learning each add.",
     "points": ("Three effects, each estimated on its own, with hypotheses and falsifiers written down in advance.",
                "Paired comparisons by task or repository; with scarce data, case-level results with declared "
                "uncertainty.",
                "An evaluator the system under test can change is a hard blocker."),
     "basis": "Proposal."},
)
