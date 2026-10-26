# Theoretical Foundations

G6 is not another large language model (LLM) wrapper. It is a combinatorial optimisation engine that solves the problem of *which tools to call, in what order, under what resource bounds* — optimising for learning, reasoning, and memory in the pursuit of goal-directed, multi-objective, non-stationary objectives that deliver real-world economic value under real-world resource constraints. This page explains the mathematical and philosophical foundations that make this possible.

## 1. The Physical Symbol System Hypothesis Revisited

In 1976, Newell and Simon articulated the Physical Symbol System Hypothesis (PSSH): a physical symbol system has the necessary and sufficient means for general intelligent action.[^1] Intelligence, on this view, is symbol manipulation under logical rules — not pattern matching over statistical distributions. Their General Problem Solver (GPS) demonstrated that even simple search over a space of symbolic operators could solve non-trivial problems by means-ends analysis.[^2]

The connectionist revolution of the 2010s appeared to refute this. Deep learning achieved superhuman performance on tasks that symbolic AI had struggled with for decades — vision, speech, translation — by learning statistical regularities from data rather than manipulating symbols. The field largely abandoned PSSH in favour of the scaling hypothesis: intelligence is a function of compute, data, and parameters.[^3]

G6 argues that this was a false dichotomy. The correct synthesis is:

- **LLMs as perception**: Transformers excel at mapping unstructured language into structured intermediate representations — code, schemas, plans, constraints. This is a perceptual capability, not a reasoning one.
- **Symbolic search as reasoning**: Once the problem is represented symbolically, intelligence is *search over a combinatorial space of solutions* — exactly what Newell and Simon described, and exactly what G6 implements.

The key analogy: conventional AI maps language → tokens → language. G6 maps language → intermediate representations → *symbolic abstractions that are searched, verified, and composed* — not mapped back to language. This is the paradigm shift.

G6 solves problems by searching over combinatorial spaces of resource-bounded tool calls, not by predicting the next token. Every `AIBlock` in the system is a node in this search space. Every pipeline is a path through it. Every goal decomposition is a branch-and-bound operation over it.

<details>
<summary>Formal: The Physical Symbol System Hypothesis</summary>

**Definition (Newell & Simon, 1976).** A *physical symbol system* consists of a set of entities called *symbols*, which are physical patterns that can occur as components of *symbol structures* (expressions). The system has a set of *processes* that operate on expressions to produce other expressions: creation, modification, reproduction, and destruction. A physical symbol system has the *necessary and sufficient means for general intelligent action*.[^1]

**Contrast with connectionism.** The connectionist hypothesis holds that intelligence emerges from the learned weights of a neural network — that the statistical structure of the training distribution *is* the knowledge. PSSH holds that intelligence requires explicit symbol structures that can be inspected, composed, and verified. G6's position is that both are needed: neural perception feeds symbolic reasoning, and symbolic reasoning constrains neural generation.

**Grounding.** Newell and Simon's GPS (1961) demonstrated means-ends analysis: decomposing a goal into subgoals, identifying the difference between the current state and the goal state, and searching for operators to reduce the difference.[^2] G6's `goal_engine` component implements precisely this architecture — but over the component library as its search space, under explicit resource bounds.

</details>

## 2. Mathematical Foundations — Category Theory as the Language of Composition

G6's component model is not an ad hoc plugin system. It is grounded in category theory — the mathematics of composition itself. Yoneda and sheaf machinery serve as design lenses, and as formal-method tools available as extensions.

### The Yoneda Lemma: Learning by Interaction

The Yoneda lemma states that an object in a category is completely determined by the totality of morphisms into (or out of) it — that is, by its *relationships* with all other objects, not by its internal structure.[^4] This is not merely an abstract curiosity. It is the mathematical formalisation of a design principle at the heart of G6.

G6 learns component behaviour by observing how components *interact* — what inputs they accept, what outputs they produce, how they compose with other components in pipelines, and how they perform under resource constraints. The current evidence supports using Yoneda to reason about typed component relationships in the component registry.

This is why G6 can compose novel pipelines from existing components without retraining. This is an illustrative analogy rather than a wired guarantee that the component registry is constructed as a Yoneda embedding or that every pipeline receives an automatic unique global-consistency guarantee.

<details>
<summary>Formal: The Yoneda Embedding</summary>

**Theorem (Yoneda Lemma).** Let **C** be a locally small category, let *F* : **C**<sup>op</sup> → **Set** be a presheaf, and let *c* be an object of **C**. Then there is a natural bijection:

Nat(**C**(−, *c*), *F*) ≅ *F*(*c*)

between the set of natural transformations from the representable presheaf **C**(−, *c*) to *F*, and the set *F*(*c*).[^4]

**The Yoneda Embedding.** The functor *y* : **C** → **Set**<sup>**C**<sup>op</sup></sup> defined by *y*(*c*) = **C**(−, *c*) is fully faithful. This means that **C** embeds into its presheaf category without loss of information — every object is completely determined by its morphisms.[^5]

**Application to G6.** Yoneda tooling, available as an extension, can be used to reason about typed component relationships. Current source evidence does not show that the component registry is constructed as a Yoneda embedding.

</details>

### The Curry-Howard Isomorphism: Programs as Proofs

The Curry-Howard isomorphism establishes a precise correspondence between logical propositions and types, and between proofs and programs: every well-typed program is simultaneously a proof of its type signature viewed as a logical proposition.[^6]

G6's formal verification components — CEGIS (counterexample-guided inductive synthesis), Z3, DPLL (the Davis–Putnam–Logemann–Loveland procedure for satisfiability) — exploit this correspondence directly. When CEGIS synthesises a program that satisfies a specification, it has simultaneously constructed a proof that the specification is satisfiable. When Z3 checks a safety constraint, it is checking that a logical proposition (the constraint) has a proof (a satisfying assignment). This is not a metaphor — it is the Curry-Howard isomorphism at work.

This is also why G6 is a *solver*, not a predictor. A solver searches for a proof (a program that satisfies a specification). A predictor estimates the probability of the next token. These are fundamentally different computational operations.

<details>
<summary>Formal: The Curry-Howard Correspondence</summary>

| Logic | Type Theory | G6 Component |
|-------|-------------|--------------|
| Proposition | Type | `AIBlock` type signature |
| Proof | Program (term) | Verified pipeline |
| Implication *A* ⇒ *B* | Function type *A* → *B* | Pipeline step |
| Conjunction *A* ∧ *B* | Product type (*A*, *B*) | Parallel composition |
| Disjunction *A* ∨ *B* | Sum type *A* \| *B* | Branch / fallback |
| Falsehood ⊥ | Empty type (Void) | `Result.fail()` |
| *Ex falso quodlibet* | `absurd : Void → a` | Error propagation |

**Source.** Milewski, B. (2019). *Category Theory for Programmers*, §9.6.[^6] Sørensen, M. H., & Urzyczyn, P. (2006). *Lectures on the Curry-Howard Isomorphism*.[^7]

</details>

### Sheaves: Local-to-Global Consistency

A sheaf is a presheaf that satisfies a *gluing condition*: if you have compatible local data on overlapping regions, there exists a unique global datum that restricts to each local piece.[^8] This is mathematically analogous to how G6 composes local component outputs into globally consistent pipeline results.

Each component in a G6 pipeline operates on a local portion of the problem. The pipeline framework enforces that local results are *compatible* — that types align, resource bounds are respected, and intermediate representations compose without contradiction. Sheaf-checking tooling, available as an extension, can check selected composition consistency cases, but this remains an illustrative analogy rather than an automatic guarantee for every pipeline.

The connection to self-attention in transformers is suggestive. Self-attention aggregates local context (individual token representations) into global representations (contextualised embeddings) by computing compatibility scores (attention weights) that enforce a form of local-to-global coherence. Sheaf theory provides the mathematical framework for understanding *why* this works — and what it means for it to fail.

<details>
<summary>Formal: The Sheaf Condition</summary>

**Definition.** Let *X* be a topological space and *O* : **Open**(*X*)<sup>op</sup> → **Set** a presheaf. Given an open set *U* ⊆ *X* and a cover {*V*₁, …, *Vₙ*} of *U*, the **sheaf condition** requires:[^8]

Given sections *aᵢ* ∈ *O*(*Vᵢ*) such that *aᵢ*|<sub>*Vᵢ* ∩ *Vⱼ*</sub> = *aⱼ*|<sub>*Vᵢ* ∩ *Vⱼ*</sub> for all *i*, *j* (the **compatibility condition**), there exists a unique section *a* ∈ *O*(*U*) such that *a*|<sub>*Vᵢ*</sub> = *aᵢ* for all *i* (the **gluing condition**).

**Application to G6.** Sheaf-checking tooling, available as an extension, can check selected composition consistency cases. Current source evidence does not show that every pipeline receives an automatic unique global-consistency guarantee.

</details>

## 3. The Solver Paradigm — Combinatorial Optimisation as Intelligence

This is the central theoretical claim of G6: **intelligence, in the context of AI tool use, is a combinatorial optimisation problem**.

The problem G6 solves is this: given a goal (potentially multi-objective, potentially non-stationary), a set of resource-bounded tools (the component library, which agents reach through MCP, the Model Context Protocol), and real-world constraints (token budgets, latency requirements, cost limits, safety bounds), find the *optimal sequence of tool calls* that maximises learning, reasoning, and memory while minimising resource consumption — and do so under uncertainty, with feedback, continuously.

This is not a language modelling problem. It is a combinatorial optimisation problem with the following structure:

- **Decision variables**: which tools to call, in what order, with what parameters
- **Objective functions**: learning gain, reasoning quality, memory consolidation, economic value (multi-objective)
- **Constraints**: token budgets (`ResourceBounds`), latency limits, safety gates (CSF, the Computational Safety Framework), cost ceilings
- **Non-stationarity**: the objective landscape shifts as the system learns, as the user's goals evolve, and as the external environment changes

G6's self-optimisation cluster (multi-armed bandits + Pareto frontiers) actively engineers this optimisation. The multi-armed bandit (MAB) treats each tool-call decision as a bandit arm, balancing exploration (trying new tool combinations) against exploitation (reusing known-good sequences). The Pareto frontier tracks the trade-off surface between competing objectives (quality vs. cost vs. latency).

This is why G6 uses formal methods as first-class citizens — SAT (Boolean satisfiability) solvers, SMT (satisfiability modulo theories) checkers, CEGIS — rather than treating them as optional add-ons. In combinatorial optimisation, checking and searching are closely related: a search proposes candidate solutions and a check accepts or rejects them, but checking that a candidate satisfies the constraints is usually much cheaper than searching for one. G6's formal methods components are not bolted-on safety features — they are integral to the solver.

<details>
<summary>Formal: The G6 Optimisation Problem</summary>

**Definition.** Let **T** = {*t*₁, …, *tₙ*} be the set of available tools (AIBlocks), each with resource cost *c*(*tᵢ*) and output type *τ*(*tᵢ*). Let **G** = {*g*₁, …, *gₖ*} be a set of objective functions (learning, reasoning, memory, economic value). Let **R** be a resource bound (tokens, time, cost). The G6 optimisation problem is:

max<sub>**σ** ∈ **Σ**</sub> **f**(**σ**) = (*g*₁(**σ**), …, *gₖ*(**σ**))

subject to: Σ *c*(*σᵢ*) ≤ **R**, safety(**σ**) ≤ ε, type(**σ**) is well-formed

where **σ** = (*σ*₁, …, *σₘ*) is a sequence of tool calls drawn from **T**, **Σ** is the set of all feasible sequences, and ε is the CSF safety bound.

This is a *constrained multi-objective combinatorial optimisation problem* over a non-stationary landscape — one of the hardest classes of optimisation problems, and precisely the class that G6 is engineered to solve.

</details>

## 4. Reflexivity — An AI with a Mathematical Model of Itself

G6 has a property that is rare among AI systems: genuine reflexivity. The system has a formal model of itself that it can query, compose, and modify.

- The **component registry** provides a queryable catalogue of the system's own capabilities — what it can do, what types each component accepts and produces, and how components compose.
- A **metaprogramming extension** can parse, analyse, and generate G6's own code — including the code of other components. It can extract function signatures, rename variables, and generate new dataclasses, classes, and functions.
- The **`cegis` component** can synthesise new components from specifications — a program that satisfies a formal spec, produced by counterexample-guided search.
- A **component-scaffolding extension** can scaffold entirely new components from descriptions.

This creates a genuine self-referential loop: the system can inspect itself, reason about its own structure, and modify itself using the same formal methods it applies to external problems. This is not self-modification in the dangerous, unbounded sense — every modification is subject to CSF safety gates, formal verification, and human approval. But it is reflexivity in the precise mathematical sense.

<details>
<summary>Formal: Second-Order Cybernetics and Self-Reference</summary>

Von Foerster distinguished first-order cybernetics — the cybernetics of *observed* systems — from second-order cybernetics — the cybernetics of *observing* systems.[^9] In first-order cybernetics, the observer is outside the system. In second-order cybernetics, the observer is *part of* the system — the system includes a model of itself.

G6 is a second-order cybernetic system. The component registry is the system's model of itself. The metaprogramming extension is the mechanism by which the system observes and modifies that model. The CSF safety framework is the constraint that prevents self-modification from diverging.

This connects to Varela's calculus for self-reference[^10] and to the broader tradition of autopoietic systems — systems that produce and maintain themselves. G6 can scaffold and validate candidate components under registry and safety checks, but this remains a human-reviewed engineering workflow rather than a fully autonomous production integration loop.

</details>

## 5. Scaling Laws and the Compression Conjecture

Neural scaling laws demonstrate that LLM performance follows power-law relationships with compute, data, and parameters — but with structurally diminishing returns.[^3] Each doubling of investment yields a smaller increment of capability. The Chinchilla corrections showed the industry was even optimising along the wrong axis.[^11] Agent scaling laws introduce additional dynamics for multi-agent systems.[^12] And Floridi's conjecture raises philosophical questions about the limits of machine intelligence grounded purely in statistical learning.[^13]

G6's response to the scaling ceiling is not "scale harder" but "compress smarter." The system implements two types of algorithmic compression:

- **Type I compression**: recognise a previously solved problem and retrieve the deterministic algorithm, bypassing the LLM entirely. Cost: near-zero. Correctness: inherited from the original synthesis check (the algorithm was verified when first synthesised; retrieval does not re-verify it).
- **Type II compression**: decompose a novel problem into sub-problems, some of which have cached solutions. Only the genuinely novel sub-problems require fresh LLM calls.

This is the *compression conjecture*: intelligence density (useful reasoning per unit of resource) increases not by scaling compute but by *converting statistical inference into deterministic algorithms*. Every time G6 solves a problem and caches the verified algorithm, it permanently reduces the cost of that class of problem from O(LLM-call) to O(lookup).

The self-optimisation cluster (MAB + Pareto) optimises tool-call sequences against three measured objectives: latency, cost, and quality. It does not compute or optimise a compression ratio — compression is a property of the hyperdistillation design, not a quantity the optimiser targets.

<details>
<summary>Formal: Intelligence Density and Compression Ratio</summary>

**Definition.** Let *V*(**σ**) be the value produced by tool-call sequence **σ**, and let *C*(**σ**) be the resource cost. The *intelligence density* of **σ** is:

*D*(**σ**) = *V*(**σ**) / *C*(**σ**)

**Compression ratio.** Let *C*<sub>LLM</sub> be the cost of solving a problem via fresh LLM inference, and *C*<sub>cached</sub> be the cost via cached algorithm retrieval. The compression ratio is:

*ρ* = *C*<sub>LLM</sub> / *C*<sub>cached</sub>

For Type I compression, *ρ* → ∞ (cached cost approaches zero). For Type II compression, *ρ* depends on the fraction of sub-problems with cached solutions. *ρ* is descriptive in this model, not an optimisation target: the self-optimisation cluster does not compute or maximise it — its reward is latency, cost, and quality.

</details>

## 6. Grounding and Failure Engineering — Reducing Hallucination

LLMs learn statistical co-occurrence, not grounded meaning. Harnad's symbol grounding problem (1990) identified this deficit: symbols in a formal system are meaningless unless grounded in something outside the system — sensory experience, verified facts, or logical structure.[^14] LLM hallucination is a direct consequence of this: the model generates statistically plausible but factually ungrounded text because it has no mechanism for checking its outputs against reality.

G6 addresses grounding through three mechanisms:

1. **Symbolic knowledge base**: curated corpora spanning epistemology, cognition, category theory, formal methods, cybernetics, and domain-specific knowledge. This is not web scraping — it is curated, domain-specific factual grounding that the system can query via semantic search.
2. **Formal verification**: claims that can be formalised are routed to SAT solvers, SMT checkers, CEGIS, or domain-specific validators where a backend is available. Such an output is grounded by construction in the property that was actually checked — not in the authenticity of any retrieved source.
3. **Cybernetic failure loops**: a self-healing extension implements a detect → diagnose → heal → learn loop with persistent healing history. When a component fails, the system captures context, generates a diagnosis, applies a patch, and learns from the failure — closing the loop between error and correction.

This is grounding in the epistemological sense: G6's outputs are justified not by statistical confidence but by evidential support — retrieval from curated corpora, formal proofs, or empirical feedback from execution.[^15]

<details>
<summary>Formal: Epistemological Grounding</summary>

The epistemological distinction is between **internalism** (justification depends on the agent's internal states) and **externalism** (justification depends on the reliability of the process that produced the belief).[^15]

LLMs are internalist in the worst way: their "justification" is the internal activation pattern that produced the output, which may or may not correspond to reality. G6 adopts a *reliabilist externalist* stance: an output is justified if and only if the process that produced it is reliable — and reliability is established by formal verification or by empirical feedback from execution, with corpus retrieval supplying evidential support whose source authenticity G6 does not itself prove.

This connects to the broader programme of **safety engineering**. The CSF (Computational Safety Framework) enforces safety bounds on every tool call, with hazard priors configured per operation type (LLM call: 0.05, file write: 0.01, code execute: 0.10, external API: 0.08). These hazard probabilities are hardcoded heuristic inputs, not yet calibrated, so the epsilon bound is only as strong as the configured hazard model, correlation assumption, and enforcement path. Safety is not a bolt-on — it is a constraint in the optimisation problem that G6 solves.[^16]

</details>

## 7. Human-AI Teaming as Primary Optimisation Target

G6 is not optimised for autonomous operation. It is optimised for *human-AI teaming* — and this is a deliberate design choice grounded in coactive design theory.[^17]

Coactive design (Johnson et al.) identifies three properties essential for effective human-AI teaming:

- **Observability**: the human can see what the AI is doing. Every G6 component is an explicit `AIBlock` with typed inputs, outputs, and state — no hidden layers, no black-box reasoning chains. The self-optimisation cluster provides telemetry: workflow traces, step timings, costs, quality scores.
- **Predictability**: the human can anticipate what the AI will do. Resource bounds, safety gates, and typed pipelines make G6's behaviour inspectable. The CSF framework checks each action's configured hazard prior against a configurable ε budget, so the bound is explicit and auditable rather than a matter of model confidence. Its strength depends on the configured hazard model — today's priors are hardcoded heuristics, not yet calibrated — so treat it as a structured risk budget, not a proof.
- **Directability**: the human can redirect the AI at any time. Alignment specs, the prompt library, and CSF safety gates all serve the teaming relationship. The human-in-the-loop is not a safety bolt-on — it is the primary mode.

The optimisation target is not "maximise autonomous capability" but "maximise the value of the human-AI team." This means that G6 actively trades off autonomous performance for interpretability, predictability, and human control. A system that produces a slightly better result but cannot explain *why* — or cannot be redirected when the human disagrees — is worse, not better, in the G6 framework.

Trust calibration is central: the system must help the human develop an accurate mental model of its capabilities and limitations, avoiding both over-trust (the human defers to the AI when they shouldn't) and under-trust (the human overrides the AI when the AI is correct).[^18]

<details>
<summary>Formal: Coactive Design Principles</summary>

Johnson et al. define coactive design as a method for engineering human-AI systems that treats interdependence as the primary design target, not independence.[^17]

**Interdependence analysis.** For each task, identify: (1) what the human needs from the AI, (2) what the AI needs from the human, (3) what the human needs to observe about the AI's state, (4) what the AI needs to observe about the human's state. The system is designed to satisfy all four dependencies.

**Trust.** Trust in human-AI teaming depends on (1) the AI agent's performance, (2) transparency of behaviour, and (3) consistency of outcomes.[^18] G6's typed interfaces, resource bounds, and safety gates provide all three.

</details>

## 8. Closing the Loop — Observability, Experiential Learning, and Self-Integration

G6 is not a static system. It is a cybernetic loop — a system that observes its own operation, learns from its own execution, and modifies itself based on what it learns.

**Observability.** The self-optimisation cluster provides telemetry that the system uses to improve itself: which tool-call sequences produced the most value, which pipelines failed and why, where resource budgets were under- or over-allocated.

**Experiential learning.** Unlike conventional AI that only learns during training, G6 learns from its own execution. The experience loop — execute → observe → update → refine — creates continuous improvement without retraining. Every goal execution is a training example for the combinatorial optimiser. Every failure is a negative example that shifts the MAB's exploration strategy.

**Self-integration of research.** The `grounding` component and knowledge base can ingest new papers, updating the system's theoretical foundations. Component-scaffolding and integration extensions can scaffold and validate candidate components from specifications, but turning that work into a running production component still requires human review.

This is the connection to Maturana and Varela's autopoiesis: a system that produces and maintains itself, whose organisation is invariant even as its structure changes.[^10] G6's component model is the invariant organisation. The specific components, their implementations, and their learned parameters are the variable structure. The system maintains itself by continuously optimising, healing, and extending its own components — within the safety bounds that prevent self-modification from diverging.

<details>
<summary>Formal: Autopoiesis and Cybernetic Loops</summary>

**Autopoiesis (Maturana & Varela, 1980).** An autopoietic system is a network of processes that continuously regenerates the network of processes that produced it.[^10] The system's organisation (the pattern of relationships between components) is maintained even as its structure (the specific components and their states) changes.

**Cybernetic loop.** Wiener defined cybernetics as the study of circular causal systems — systems where effectors are connected to sensors that act back on the effectors.[^19] G6 implements this at multiple levels: the experience loop (execute → observe → update → refine), the healing loop (detect → diagnose → heal → learn), and the optimisation loop (explore → evaluate → exploit → compress).

**Second-order cybernetics.** Von Foerster's distinction: first-order cybernetics studies observed systems; second-order cybernetics studies observing systems.[^9] G6 is a second-order system — it observes itself and uses those observations to modify its own behaviour.

</details>

## 9. Practopoiesis and the T0–T3 Hierarchy

The cybernetic loops described above — experience, healing, optimisation — are instances of a more general principle. Danko Nikolić's theory of **practopoiesis** (from Greek: *poiesis* of *praxis* — "creation of action") proposes that biological intelligence organises itself through nested layers of adaptation, each creating the conditions for the next.[^20]

The central concept is the **adaptive traverse**: a transition from general cybernetic knowledge to specific action. The number of traverses determines a system's total adaptive capability:

| Type | Traverses | Description | AI Analogy |
|------|-----------|-------------|------------|
| **T0** | 0 | No adaptation — fixed mechanism | Tool-calling with no learning |
| **T1** | 1 | Single flexible store — learns but rules are fixed | Harness engineering (one-time optimisation) |
| **T2** | 2 | Two flexible stores — parameter learning with fixed learning rules | Continuous resampling / fine-tuning |
| **T3** | 3 | Three flexible stores — learns how to learn via **anapoiesis** | Structured theory building (G6) |

**Anapoiesis** is the process unique to T3 systems: general, abstract knowledge stored at one level is reconstructed into specific, situation-appropriate knowledge at a higher level. This is why T3 systems exhibit discontinuous improvement — the system doesn't just learn parameters; it learns *better rules for learning parameters*, enabling transfer to genuinely novel domains.

G6 implements the T0–T3 hierarchy explicitly:

- **T0**: baseline tool-calling — the model invokes G6 components with no iteration
- **T1**: the self-training loop runs once — a human analyses failures and rewrites the harness
- **T2**: the self-training loop runs continuously with data augmentation and feedback
- **T3**: the system formulates explicit symbolic models of the problem domain (via formal methods, and extensions for specification alignment and skill evolution), enabling hypothesis-driven improvement and transfer

This connects autopoiesis (Section 8) to a testable hierarchy: the number of nested self-modifying loops determines the system's adaptive ceiling. Most current AI systems — including frontier agent frameworks — operate at T0 or T1. G6 is designed to support all four levels.[^21]

## Unified Agentic Theory Document

The theoretical foundations above are supported by a longer mathematical treatment:

- [G6 Unified Mathematical Theory of Agentic Intelligence](G6_Unified_Theory_of_Agentic_Intelligence.md) formalises G6 through self-correction loops, theory compression, verification, and empirical calibration from March-May 2026 benchmark runs.

## Conclusion

This page argues that G6 can address open problems in AI by returning to first principles with modern mathematical tools:

- **PSSH + combinatorial search**: intelligence is search over symbolic structures, with LLMs providing the perceptual layer that maps language into those structures
- **Category theory**: the Yoneda lemma, Curry-Howard isomorphism, and sheaf theory provide the mathematical foundations for component composition, formal verification, and local-to-global consistency
- **Combinatorial optimisation**: the core problem is optimising resource-bounded tool calls for learning, reasoning, and memory under multi-objective, non-stationary conditions — a solver problem, not a prediction problem
- **Cybernetic self-regulation**: reflexivity, experiential learning, and autopoietic self-maintenance create a system that improves continuously under formal safety constraints
- **Practopoiesis**: the T0–T3 hierarchy provides a testable classification of adaptive capability, grounding G6's self-training in biological principles of nested adaptation
- **Grounded knowledge**: symbolic knowledge bases, formal verification, and failure engineering reduce hallucination by grounding outputs in retrieved evidence rather than statistical confidence
- **Human-AI teaming**: the primary optimisation target is the value of the human-AI team, not autonomous performance

It also argues that this is more than an incremental improvement on the LLM paradigm: an approach grounded in combinatorial optimisation, symbolic reasoning, and cybernetic feedback, using LLMs as a powerful but bounded component within a larger mathematical architecture.

---

## References

[^1]: Newell, A., & Simon, H. A. (1976). Computer science as empirical inquiry: Symbols and search. *Communications of the ACM*, 19(3), 113–126. — Articulates the Physical Symbol System Hypothesis: a physical symbol system has the necessary and sufficient means for general intelligent action.

[^2]: Newell, A., & Simon, H. A. (1961). GPS, a program that simulates human thought. In E. A. Feigenbaum & J. Feldman (Eds.), *Computers and Thought* (pp. 279–293). McGraw-Hill. — Demonstrates means-ends analysis as a general problem-solving strategy, using search over symbolic operators.

[^3]: Kaplan, J., McCandlish, S., Henighan, T., Brown, T. B., Chess, B., Child, R., Gray, S., Radford, A., Wu, J., & Amodei, D. (2020). [*Scaling laws for neural language models*](https://arxiv.org/abs/2001.08361). arXiv. — Establishes power-law relationships between model performance and compute, data, and parameter count.

[^4]: Riehl, E. (2016). *Category Theory in Context* (§2.2). Cambridge University Press. — Presents the Yoneda lemma and embedding, proving that objects are fully characterised by their morphisms.

[^5]: Mac Lane, S. (1998). *Categories for the Working Mathematician* (2nd ed., §III.2). Springer. — The standard reference for the Yoneda lemma, natural transformations, and representable functors.

[^6]: Milewski, B. (2019). *Category Theory for Programmers* (§9.6). Blurb. — Accessible treatment of the Curry-Howard isomorphism: types as propositions, programs as proofs, function types as logical implication.

[^7]: Sørensen, M. H., & Urzyczyn, P. (2006). *Lectures on the Curry-Howard Isomorphism*. Studies in Logic, Vol. 149. Elsevier. — Comprehensive treatment of the correspondence between logic and type theory.

[^8]: Spivak, D. I. (2014). *Category Theory for the Sciences* (§5.2). MIT Press. — Presents the sheaf condition and gluing axiom.

[^9]: von Foerster, H. (2003). *Understanding Understanding: Essays on Cybernetics and Cognition* (Ch. 14). Springer. — Distinguishes first-order cybernetics (observed systems) from second-order cybernetics (observing systems).

[^10]: Maturana, H. R., & Varela, F. J. (1980). *Autopoiesis and Cognition: The Realization of the Living*. D. Reidel. — Defines autopoiesis: self-producing systems whose organisation is invariant even as their structure changes. See also Varela, F. J. (1975). A calculus for self-reference. *International Journal of General Systems*, 2(1), 5–24.

[^11]: Hoffmann, J., Borgeaud, S., Mensch, A., et al. (2022). [*Training compute-optimal large language models*](https://arxiv.org/abs/2203.15556). arXiv. — The "Chinchilla" paper; demonstrates that models should be trained on more data than previously assumed for a given compute budget.

[^12]: Chen, S., et al. (2024). [*Scaling laws for multi-agent systems*](https://arxiv.org/abs/2512.08296) (arXiv identifier as cited). arXiv. — Demonstrates that multi-agent systems exhibit their own scaling dynamics distinct from single-model scaling.

[^13]: Floridi, L. (2025). [On the philosophical limits of artificial intelligence](https://link.springer.com/article/10.1007/s13347-025-00927-z). *Philosophy & Technology*, 38(1). — Raises philosophical questions about the limits of machine intelligence grounded in statistical learning.

[^14]: Harnad, S. (1990). The symbol grounding problem. *Physica D*, 42(1–3), 335–346. — Identifies the grounding problem: symbols in a formal system are meaningless unless connected to non-symbolic (sensory) representations.

[^15]: Audi, R. (2010). *Epistemology: A Contemporary Introduction to the Theory of Knowledge* (3rd ed.). Routledge. — Surveys internalist and externalist theories of justification, including reliabilism.

[^16]: Russell, S., & Norvig, P. (2010). *Artificial Intelligence: A Modern Approach* (3rd ed., §14.7). Prentice Hall. — On uncertain reasoning and how certainty-factor systems can yield "disastrously incorrect degrees of belief."

[^17]: Johnson, M., Bradshaw, J. M., Feltovich, P. J., Jonker, C. M., van Riemsdijk, M. B., & Sierhuis, M. (2014). Coactive design: Designing support for interdependence in joint activity. *Journal of Human-Robot Interaction*, 3(1), 43–69. — Defines coactive design as engineering human-AI systems around interdependence, not independence.

[^18]: Johnson, M., Bradshaw, J. M., Feltovich, P. J., Jonker, C. M., van Riemsdijk, M. B., & Sierhuis, M. (2004). Ten challenges for making automation a "team player" in joint human-agent activity. *IEEE Intelligent Systems*, 19(6), 91–95. — Identifies trust calibration, observability, and directability as essential for effective human-AI teaming.

[^19]: Wiener, N. (1948). *Cybernetics: Or Control and Communication in the Animal and the Machine*. MIT Press. — Foundational text defining cybernetics as the study of circular causal systems.

[^20]: Nikolić, D. (2015). [Practopoiesis: Or how life fosters a mind](https://www.danko-nikolic.com/practopoiesis/). *Journal of Theoretical Biology*, 373, 40–61. — Proposes that adaptive systems are organised through nested layers of adaptation (adaptive traverses), with each level creating the conditions for the next.

[^21]: Nikolić, D. (2024). [Distinction between T2 and T3 systems](https://www.danko-nikolic.com/distinction-between-t2-and-t3-systems/). — Distinguishes parameter-learning systems (T2, fixed learning rules) from theory-building systems (T3, learning rules that improve themselves via anapoiesis).
