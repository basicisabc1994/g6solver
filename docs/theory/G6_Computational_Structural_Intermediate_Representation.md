# A Computational Structural Intermediate Representation for Compositional, Evidence-Constrained and Self-Improving AI Agents

Version 0.3: rebuilt from version 0.2 for publication; the argument is unchanged.

*A Resource-Yoneda, Evidence-Constrained Harness Adaptation and Adaptive Harness Intelligence research synthesis,
based on the G6 research programme. 11 October 2026.*

## Abstract and epistemic status

We propose a unified *Computational Structural Intermediate Representation* (CSIR) for AI agents, informed by the
Resource-Yoneda theory developed in a Positive SUBSET SUM research programme, a theory of *Evidence-Constrained Harness
Adaptation* (ECHA), and the Adaptive Harness Intelligence (AHI) architecture of G6. The proposed central object is not
a base language model or a catalogue of tools; it is a typed, resource- and evidence-enriched, partially observable
computational system whose strategy can construct, execute, verify, and experimentally improve compositions of
computational capabilities. Computational operations and evidence-producing procedures are different kinds of
morphisms. Both may change what an agent can accomplish, but they confer different forms of power: executing a
solution, finding an observation, rejecting a false claim, or constructing a defensible justification. The separation
is essential to avoid the *self-validation paradox*.

The framework makes precise the distinctions among (a) new semantic access, (b) algorithmic resource improvements,
(c) acquired executable competence, (d) independently justified learning claims, and (e) general intelligence measured
across tasks. It contains several elementary or conditional propositions, along with novel **proposed** interface
definitions and conjectures, but it does not establish an unrestricted model of intelligence, a theorem that G6 is
generally intelligent, a universal verifier, a proof of P versus NP (the open question whether every problem whose
answers can be checked in polynomial time can also be solved in polynomial time), or improved empirical performance of
the G6 platform. A reference implementation and 16 toy tests accompany this manuscript. The tests cover the restricted
semantics only.

**Claim labels used throughout:** `D` = definition, `P` = mathematical proposition proved here under explicit
assumptions, `C` = conditional theorem requiring implementation assumptions, `H` = falsifiable hypothesis, `E` =
empirical result from a cited repository report, `U` = presently unverified. These are not grades of scientific
novelty. Novelty remains subject to independent prior-art review.

## 1. Research objective and the two-sided validation paradox

The foundational question is:

> How can an agent make rational use of an expanding collection of computational capabilities, improve that
> collection through experience, and issue conclusions whose certainty never exceeds independently warranted evidence?

A conventional agent chooses model prompts and tools, then returns a final answer. This can be extremely useful
without yielding a mathematical account of why a composition was chosen, what information it loses, what class of
algorithm it realizes, what resources it consumes, or which claims it establishes. G6's existing infrastructure
contains task standards, source retrieval, execution, routing, monitors, learned probes, approval gates and result
envelopes. Its historically demonstrated gap has been the missing or partly activated *objective-to-evidence* control
plane, not absence of named components. The October 11, 2026 `MissionIR` (mission intermediate representation)
implementation, now `g6.research.mission_bridge`, is an important new research-only bridge, but its own architecture
decision record (ADR) and fixture reports explicitly deny that a passing graph or evidence tag establishes real-world
correctness.

The **self-validation paradox** has three distinct targets. (i) A system may know whether one task was performed
correctly under a narrow externally specified predicate. (ii) It may estimate whether adding a safeguard improves a
specified population of future tasks. (iii) It generally cannot know that it will solve *all arbitrary future
problems* correctly. Passing finite evaluations establishes neither (iii) nor a distribution-free version of (ii).
This formulation follows G6's [ECHA specification](G6_Evidence_Constrained_Harness_Adaptation_Spec_v0.1.md) and
[validation-paradox essay](G6_Self_Validation_Paradox_Resolution.md). It suggests that systems must be allowed to learn
from incomplete evidence while **prohibiting unsupported promotion**.

CSIR addresses the space of admissible computational plans. ECHA addresses the admissibility of evidence and
conclusions. AHI governs how the set of available plans is experimentally enlarged. G6 is the current implementation
target. Their logical relationship is:

> Task standard → CSIR planning → execution/observation → ECHA adjudication → governed adaptation.

The causal order is not strictly linear: observing a failed check can reopen planning; determining that evidence is
inaccessible can trigger a search for a new observation; and budget constraints can justify escalation rather than
continuation. Such loops must remain bounded and logged.

## 2. Computational objects and typed, effectful primitives

### 2.1 Task specifications

**Definition D1 (mission).** A mission is a tuple

$$T=(x,S,\rho,B,\Pi,\mathcal{V}),$$

where $x$ is the problem and authorised input data; $S$ is a versioned collection of predicates and acceptance
standards; $\rho$ contains mission-wide consequences and the maximum risk of downstream uses; $B$ is a finite resource
budget, possibly with unknown components; $\Pi$ is a capability/permissions policy; and $\mathcal{V}$ names admissible
verification and approval authority. A human instruction may be incomplete; the compiled standard is a **declared,
frozen interpretation** and not automatically a true or complete description of the human's intention.

We write $S'\succeq S$ when every mandatory predicate and obligation of $S$ remains mandatory under $S'$. The relation
is defined over executable obligations, not wording or an apparent scalar risk label. Refinement may strengthen the
standard; an agent must not silently weaken it.

### 2.2 Computational states

**Definition D2 (agent state).** Let

$$\Sigma=(W,X,M,H,E,L,R,V),$$

where $W$ is the actual environment state (partially or wholly hidden), $X$ is the agent's working representation,
$M$ is persistent memory and retrieved knowledge, $H$ is the versioned harness graph, $E$ the admissible evidence
store, $L$ a provenance/activation ledger, $R$ the consumed and remaining resources, and $V$ the immutable
verification/authority policy under the current approval epoch. The agent sees an observation $o$ of $W$, not
necessarily the world itself.

A computational component should not be assigned the type `input -> output` alone. At minimum it carries input/output
schemas, preconditions, side-effect class, source/owner, required authority, resource profile, postcondition
candidates, evidence requirements and failure modes. Two components with the same interface may differ in exact
semantics, runtime cost, data egress and suitability for a particular risk level.

### 2.3 Probabilistic operational semantics

**Definition D3 (primitive).** A typed primitive $f:A\rightsquigarrow B$ is represented by the record

$$f=(A,B,\mathrm{Pre}_f,\mathrm{Eff}_f,K_f,c_f,\mathrm{Fail}_f,\mathrm{Prov}_f).$$

$K_f$ is a stochastic transition kernel from an admissible state and input to a distribution on output, observations,
trace additions and the explicit terminal outcomes `ok`, `fail`, `unknown`, `blocked` or `timeout`. A deterministic
function is a special case. $c_f$ is a **resource vector**, not merely monetary price:

$$c_f=(t,s,\text{tokens},\text{currency},\text{network},\text{energy},\text{review time},\ldots).$$

Unknown cost bounds retain the value `unknown`; they cannot be coerced to zero. Costs of an unexecuted branch are not
automatically incurred. For a serial path, any claimed upper bound must cover all executed calls, conditional reruns
and reviewer/verification effort. Parallel operations require an explicit wall-clock makespan/resource-sharing model
and cannot always be costed by scalar addition.

Side effects are from a policy-ordered vocabulary such as `read`, `local_write`, `external`, `irreversible`. An
external operation may additionally carry sensitive-data egress obligations. Data labelled medical, privileged,
personal, third-party, or confidential cannot be passed to an external model merely because a compatible JSON
(JavaScript Object Notation) schema exists.

### 2.4 The computational category

Let $\mathcal{C}_{S,\Pi}$ be a typed multicategory or effectful category indexed by standard $S$ and policy $\Pi$:
objects are abstract states and admissible interfaces; unary and multi-input morphisms are allowed computational
primitives and their finite legal compositions; tensor/product denotes permitted independent ownership or parallel
combination **only when resource and effect separation is justified**. This construction is a research definition;
the category laws require the chosen notion of programs, quotient and equivalence to satisfy associativity and
identity (or an appropriate weaker structure).

The **forgetful projection** $U:\mathcal{C}_{S,\Pi}^{\mathrm{evidence}}\to\mathcal{C}_{S,\Pi}^{\mathrm{compute}}$
discards evidence annotations while retaining computational effects. It is generally not reversible: two agents can
produce the same output with different support for correctness. An independent evaluator may produce no new solution
but materially increase justified knowledge.

## 3. The CSIR grammar and the role of resource structure

### 3.1 Primitive alphabet

The initial vocabulary retains the original eight-class CSIR alphabet:

| Letter | Primitive family | Operational role in an AI harness |
|---|---|---|
| E | Expose | Reveal latent structural information, compile a task, inspect a source |
| T | Transform | Change coordinates, schemas, units or problem representations |
| R | Represent | Generate multiple encodings/candidates, factor or lift a problem |
| Q | Quotient | Compress a state while preserving declared observables |
| A | Access | Retrieve, stream, query, sample or index information |
| C | Compose | Merge dependencies, parallelise legal branches, reconcile interfaces |
| D | Decode | Recover a decision, certificate, artefact or answer from intermediate state |
| K | Control | Route, branch, recurse, stop, budget, select reviewer or request clarification |

For the applied agent theory it is useful to introduce **cross-cutting effect annotations**, rather than claiming all
agent behaviour is adequately described by those eight letters: `M` for persistent memory, `V` for verification and
evidence, `L` for learning/adaptation, `G` for governance/authority, and `X` for externally effectful execution. A
particular G6 component may legitimately implement several tags. The canonical identity of a component is its *actual
operations and contracts*, not its name or assigned category.

### 3.2 Grammar

A minimal initial syntax (typed pseudocode) is

$$p ::= \mathrm{id}\mid f\mid p_2\circ p_1\mid p_1\oplus p_2\mid \mathrm{branch}(q,p_1,p_2)\mid \mathrm{verify}_{v}(p)\mid \mathrm{observe}_{a}(p)\mid \mathrm{stop}(r).$$

The operation $\oplus$ denotes an *explicitly typed* alternative or parallel product, whose trace and cost semantics
must be declared. It is **not** an implicit licence to duplicate linear resources, reuse patient information across
tenants, or pass unchecked intermediate results across authority boundaries. Later fragments add bounded recursion,
learning transitions and actor interaction. A compilation judgement has the form

$$\Gamma;S;\Pi;B \vdash p:A\leadsto B \mathbin{!} \varepsilon \triangleright C_p,$$

where $\varepsilon$ is the effect/provenance obligation and $C_p$ is a conservative (possibly unknown) resource
envelope. Failure of this judgement causes refusal or qualification; the type checker does not invent missing proofs
of preconditions.

### 3.3 The description-length / access / decoding distinction

A key result of the SUBSET SUM programme is conceptual: there is a difference between a representation's semantic
compactness, the cost of *accessing* that representation, the cost of *decoding* its answer, and the cost of
discovering the representation. For agentic systems, this becomes

$$\Omega(p,T)=(\Omega_{\mathrm{semantic}},\Omega_{\mathrm{access}},\Omega_{\mathrm{decode}},\Omega_{\mathrm{interaction}},\Omega_{\mathrm{evidence}},\Omega_{\mathrm{meta}},\Omega_{\mathrm{governance}}).$$

These are **candidate currencies**, not a proved conserved scalar. An agent may cheaply summarise a large legal case
while losing the decisive exception in an obscure regulation; it may cheaply query a multimodal model while acquiring
no admissible clinical-grade evidence; it may generate a compact engineering optimization objective whose constraints
cannot be validated. A measure of token compression is therefore neither a proof of semantic sufficiency nor a measure
of downstream trustworthiness.

### 3.4 Resource frontiers

Fix a grammar $\mathcal{G}$, task family $\mathcal{T}$, admissible verifiers and an explicit outcome criterion. Let
$R_{\mathcal{G}}(\mathcal{T})$ be the set of achievable resource/assurance vectors of valid programs. Its
Pareto-minimal portion is the *resource frontier*; a candidate can improve latency while worsening cost or
verification coverage. Resource comparisons are meaningful only relative to the same task standards, permission
policy and outcome criterion. Merely claiming that two workflows produce the same answer cannot establish equal
reliability.

## 4. Resource-Yoneda and contextual equivalence

### 4.1 Exact relative equivalence

For a type $A$ and a fixed family $\mathcal{H}$ of admissible downstream observations, define

$$x\equiv_{\mathcal{H}} y \iff \forall h\in\mathcal{H},\quad h(x)=h(y).$$

**Proposition P1 (canonical quotient, elementary).** $A/{\equiv_{\mathcal{H}}}$ is the coarsest partition that
preserves all observations in $\mathcal{H}$. It is a compositional quotient if the observation family is closed under
the chosen continuation grammar.

*Proof.* Every equivalence class groups exactly states all observables identify. Any partition preserving all $h$
cannot identify two states distinguished by some $h$; consequently it refines the quotient. Closure of contexts makes
the equivalence stable under legal continuations. QED (quod erat demonstrandum: which was to be shown).

The quotient depends on $\mathcal{H}$. The Yoneda lemma does not produce runtime lower bounds; the resource enrichment
and completeness of the probe category are separate work. A sampled catalogue of G6 tools cannot establish
equivalence under *all* possible external contexts.

### 4.2 Worked SUBSET SUM theorem

Let positive integers $a_i$ index finite set $I$. Consider $S\subseteq U\subseteq I$, with future completion
$T\subseteq V$, where $U\cap V=\emptyset$. Let $\sigma(S)=\sum_{i\in S}a_i$. Permit all observations

$$O_{T,t}(S)=\mathbf{1}\bigl[\sigma(S)+\sigma(T)=t\bigr]$$

for every $T\subseteq V$ and **arbitrary integer target** $t$.

**Proposition P2 (typed sum quotient).** Under exactly these contexts,

$$S\equiv S'\quad\Longleftrightarrow\quad \sigma(S)=\sigma(S').$$

*Proof.* Equal partial sums give identical outcomes under every completion. Conversely, take $T=\emptyset$ and
$t=\sigma(S)$; if $\sigma(S)\ne\sigma(S')$, one accepts and the other rejects. QED.

This is the elementary core of the typed full-abstraction note on SUBSET SUM listed in the references. Additional
graded-sum equality under a typed semiring grammar requires the exact observation and closure assumptions given in
that note. **Counterexample to over-generalisation:** if the future may ask whether item $i$ was used, two subsets
with equal sums may be distinguished; if ownership overlaps, a scalar sum no longer preserves all compatibility
information. Thus a G6 context-compression operator must state which predicates and provenance it is preserving.

### 4.3 Resource-conditioned equivalence

Write $x\equiv_r y$ when no admissible context costing at most $r$ distinguishes them. A reversible coordinate
transform of bounded cost preserves such distinguishability relations up to an explicitly proven resource distortion,
not in general at exactly equal costs. Resource-constrained equivalence must distinguish **semantic non-novelty** from
**algorithmic efficiency**: a new primitive may preserve all information yet execute dramatically faster. Conversely,
a primitive may expose genuinely new evidence without directly solving the original computational problem.

## 5. ECHA: the semantics of evidence and truth claims

### 5.1 The point of a truth theory

ECHA is not a metaphysical oracle. It is a logic and governance framework for when a finite, real system is justified
in asserting a specified predicate. A `Claim` should name
$(S,\varphi,\text{scope},\text{population},\text{versions},\text{oracle assumptions})$. An `EvidenceRecord` should
name origin, method, independence, positive/negative controls, errors and applicability. The final completion state
remains a policy decision above the evidence ledger; it must not be treated as a mathematical theorem of the system's
output.

A safe implementation must distinguish `process` (the action ran), `heuristic_review` (someone/model judged the
action), `partial_falsifier` (a certain prohibited property was detected), `independent_executable` (a scoped check
with actual external control), `formal_checked` (the specific proposition was checked by an identified kernel), and
`no_evidence`.

The October 11 `MissionIR` claim bundle implements a useful subset but explicitly reports two trust gaps: a caller can
assert a sealed-fixture digest without live authority verification, and the formal certificate is checked for
structure/identity rather than rechecked by a kernel. These are not theoretical nitpicks; they constrain what the
package's word `verified` can safely mean in any real deployment.

### 5.2 Belnap-style evidence pairs

**Definition D4 (four-state predicate evidence).** Relative to a predicate $\varphi$, let $e^+\in\lbrace 0,1\rbrace$
mean that at least one applicable admissible support exists; let $e^-\in\lbrace 0,1\rbrace$ mean that an applicable
admissible refutation exists. The four evidence states are

$$\mathbb{B}_4=\lbrace (0,0),(1,0),(0,1),(1,1)\rbrace$$

with interpretations `neither`, `supported only`, `refuted only`, and `conflicted`. The **knowledge order** is
coordinatewise inclusion. Accumulating independent evidence can move upward in this order, but **the policy decision
to issue a verified answer is not monotone**: a later valid counterexample can turn a previously supported predicate
into a conflict. Reconciliation and human review must not erase the contradiction silently.

### 5.3 Evidence cannot be manufactured by repeated reflection

Let $W_0,W_1$ be equally likely underlying worlds requiring opposite binary answers. For an admissible observation
policy $\pi$, let $P_0^\pi,P_1^\pi$ be its resulting observable transcript distributions.

**Proposition P3 (observability bound).** Every non-abstaining binary verifier observing the transcript of $\pi$ has
error at least

$$\frac{1-\mathrm{TV}(P_0^\pi,P_1^\pi)}{2},$$

where $\mathrm{TV}$ is the total variation distance between the two transcript distributions.

*Proof.* The optimal binary acceptance region for equal priors is the likelihood-ratio test. Its maximum success
probability is $(1+\mathrm{TV})/2$; error is the complement. QED. For deterministic identical transcript
distributions, error is exactly $1/2$. A bound against **all** permitted policies requires taking the appropriate
supremum over policies; showing three heuristics fail is not such a proof.

This establishes that repeating a model's analysis with no new observable information cannot overcome a genuine
indistinguishability barrier. A new external measurement may alter the transcript distributions and reduce Bayes risk,
but it costs resources and may carry new authority and privacy obligations.

### 5.4 Falsification is asymmetric

**Proposition P4 (scoped counterexample).** If a universal task requirement is $\forall x\in D:P(x)$ and an
independently established witness $x_0\in D$ demonstrates $\neg P(x_0)$, then the requirement is false. In contrast,
checking any finite proper subset of an unrestricted $D$ need not establish the universal requirement.

The same logic explains why a scaled SUBSET SUM instance is useful for metamorphic testing. If $F(A,t)$ is a correct
decision solver, then for each positive integer $k$, $F(A,t)=F(kA,kt)$. A violated identity refutes the solver on at
least one query, while agreement does not establish that either result is correct. This rule must be evaluated
against a correctly specified transformation; arbitrary "metamorphic" heuristics are not themselves sound oracles.

### 5.5 Resource and evidence dual optimisation

At each step an intelligent controller should evaluate both the cost of computing a candidate and the cost of
obtaining admissible evidence for it. This yields a **proposed two-frontier optimisation problem**:

$$\min_{p,q}\ (C_{\mathrm{solve}}(p),C_{\mathrm{certify}}(q),R_{\mathrm{escape}}(p,q))$$

subject to task predicates, permissions, mandatory checks, available authority and release policy. The quantities are
not assumed scalar-equivalent, and no universal optimal oracle is postulated. In high-stakes tasks, cheaper answer
generation may be less useful than cheap independent falsification or cheaper escalation to an expert.

## 6. Compositional refinement and the trust boundary

### 6.1 Concrete/abstract relationship

Let each abstract primitive $\hat f$ denote a set of permitted traces, including errors, timeouts, costs and effects.
Let $\gamma(\hat f)$ be its concrete interpretation and $\mathrm{Exec}(f)$ its actual implementation traces.
**Assumption A1 (local refinement)** says $\mathrm{Exec}(f)\subseteq\gamma(\hat f)$ for every admitted primitive
and environment under its precondition. This assumption is a proof obligation, not a consequence of a directory being
registered or an `infer()` method returning an object.

Other obligations are: A2 (`composition faithful`, including non-happy paths); A3 (`preconditions, authority and
mission standards preserved`); A4 (`conservative budget composition`); A5 (`evidence authority checked and not
writable by the candidate author`); A6 (`no hidden shared-state/race side effect outside the grammar`).

**Conditional Theorem C1 (serial compositional soundness).** If A1–A6 hold, any finite well-typed serial composition
of primitives preserves the asserted abstract trace overapproximation, resource envelope and admissible evidence
policy.

*Proof.* Induction on the number of steps. For one step use A1. Compose the induction witness with another admitted
primitive. A2 ensures concrete traces are included in sequential relational composition; A3 eliminates illegal
transitions or represents them as explicit stops; A4 extends resource bounds; A5 prevents unsupported claim elevation;
A6 ensures no unmodelled transition invalidates the reasoning. QED **conditional on A1–A6**. This is ordinary-style
compositional refinement; scientific novelty must lie beyond restating it.

*Counterexample.* If a large language model (LLM) generating an engineering report also writes its own "independent"
test verdict into the same evidence store, A5 fails. Typed input/output compatibility does not save the theorem.
Likewise, a nominal read-only API (application programming interface) that secretly calls an external service violates
A1/A6 even if its response schema is correct.

### 6.2 Proof obligations for parallelism, recursion and adaptation

For parallel branches one must model effects, interleavings, conflicts, cancellation and resource contention. Branch
outputs may be reconciled only by valid domain-specific merge operators. For recursion one needs a ranking function,
fuel bound or a deliberately unbounded cost result. AHI's harness update is not an ordinary morphism: it changes the
primitive collection, routing policy and perhaps attack surface. Treat it as a **versioned meta-transition** with
separate promotion authority and immutable standard/oracle scope during a given evaluation.

## 7. Failure engineering as a structural calculus

### 7.1 Failure classes and activation

Every significant failure receives a proposed causal class: wrong/incomplete standard, generation-capability wall,
wrong algorithm/representation, missing operation, unreachable operation, reachable but never invoked, invoked but
ineffective, insufficient observation, compromised/correlated evaluator, harmful repair, budget/environment failure,
or shift/contamination/reward gaming.

These classes determine different repairs. A `generation wall` calls for a new computational primitive or stronger
solver; `activation failure` calls for router/plan changes; `evidence insufficiency` calls for a new
observation/independent check or explicit abstention; `incorrect standard` calls for human clarification and
freezing, not optimizing the current plan harder. A generic "retry with deeper thought" cannot be the default for all
these conditions.

The **activation funnel** for each primitive $f$ is

$$\mathrm{present}(f)\Rightarrow\mathrm{reachable}(f)\Rightarrow\mathrm{invoked}(f)\Rightarrow\mathrm{effective}(f).$$

These are not interchangeable assertions. `effective` requires an independently justified change to a predicate or
cost outcome in a suitable matched comparison, not a log stating the tool executed. G6's source describes a maturity
endpoint `measured_suitable` not attained by registration alone.

### 7.2 Exact residual failure probability

Let $F$ be a specified failure class with pre-guard probability $p_F$. Let $M_i$ denote that guard $i$ misses it.
Then

$$R_F=p_F\Pr(M_1\cap\cdots\cap M_k\mid F)=p_F\prod_{i=1}^k \Pr(M_i\mid F,M_1,\ldots,M_{i-1}).$$

**Proposition P5.** This identity is exact by the conditional probability chain rule; no independence is required.
Multiplying marginal miss rates $\prod_i\Pr(M_i\mid F)$ is unjustified without the relevant conditional-independence
assumptions. Therefore a collection of near-identical LLM reviewers cannot automatically be credited as independent
layers of protection.

### 7.3 Evidence-directed value of information

Let a controller state $s$ contain posterior beliefs over failure classes and oracle capabilities. A permitted probe
$a$ may generate observation $o$. Define the value of information (VOI) of the probe as

$$\mathrm{VOI}(a\mid s)=\mathbb{E}_o\left[\max_d\mathbb{E}\lbrace U(d,W)\mid s,o\rbrace\right]-\max_d\mathbb{E}\lbrace U(d,W)\mid s\rbrace-c(a).$$

This is an *ideal* decision criterion and requires calibrated beliefs; an LLM's arbitrary score is not an expected
value of information. Mandatory safety and evidence checks remain hard constraints even if a heuristic VOI score is
negative. An evidence-gap diagnosis should generate requests for particular tests, external sources, clarifications or
authoritative reviewers that can plausibly distinguish currently confounded alternatives.

### 7.4 Failure engineering must alter search

The characteristic G6 result would be a policy that learns **where an attack failed in CSIR coordinates** (e.g.
candidate generation, inaccessible state, interaction width, expensive decoder, false verifier, high transfer cost),
searches for an alternative structural primitive, and rejects a merely cosmetic encoding shift. The measurable
consequence is fewer unproductive calls, more independently caught defects and improved held-out acceptance at
controlled resource cost. This is a hypothesis, not a proven current property of the live system.

## 8. Capability closure, novelty and computational Galois programme

### 8.1 Semantic and resource closure

For a primitive collection $\mathcal{P}$ and declared grammar $\mathcal{G}$, define
$\mathrm{Cl}_{\mathcal{G}}(\mathcal{P})$ to contain the finite programs formed by legal operations of
$\mathcal{G}$. Their **semantic quotient** identifies programs indistinguishable by admissible contexts; their
**resource quotient** additionally accounts for simulation costs and preserved effects.

A new primitive $f$ is:

1. *semantically novel relative to $\mathcal{G}$* if it produces a distinction no old-grammar program can produce
   under declared observations;
2. *resource novel* if it computes an otherwise obtainable result with strictly improved measured or proved resource
   frontier;
3. *epistemically novel* if it provides a new independent, discriminating observation or checked certificate, even
   when computation's output is unchanged;
4. *operationally novel* if it newly makes a previously unreachable capability available under admissible policy,
   without necessarily changing theoretical expressivity.

These definitions prevent G6 from crediting a registry expansion, an extra prompt, or a coordinate rewrite as a
fundamental capability increase automatically.

### 8.2 Separation witness

**Proposition P6 (nondefinability witness).** Suppose $\equiv_{\mathcal{H}}$ is a congruence for old grammar
$\mathcal{G}$. If $x\equiv_{\mathcal{H}}y$ but for a candidate primitive $f$ and old admissible continuation $h$ one
has $h(f(x))\ne h(f(y))$, then $f$ is not definable by an old-grammar program preserving the same typed observations.

*Proof.* If $f$ were definable, congruence would make the two transformed states indistinguishable by every
admissible $h$, contradicting the witness. QED. The converse is not established by failure to find a witness; a
finite sample cannot certify grammar completeness.

### 8.3 Finite atlas primal-dual theorem

Let a finite complete computational atlas have states $V$, transitions $E$, nonnegative costs $c(e)$, and terminal
solved set $F$. Let $d(v)$ be minimum path cost to $F$, with unreachable states assigned infinity. A potential $\Phi$
satisfying $\Phi(u)-\Phi(v)\le c(u,v)$ and $\Phi(f)\le 0$ for terminal $f$ is a lower bound on path cost.

**Proposition P7.** For finite states with finite distance to a terminal, the maximum such potential at start $s$
equals $d(s)$.

*Proof.* Telescope inequalities along any path for the lower bound; take $\Phi(v)=d(v)$, which is feasible by the
Bellman optimality inequality. QED.

This is a standard shortest-path dual result. Its use as an *explanatory obstruction calculus* is worth studying but
does not imply that a G6 primitive catalogue is complete for all polynomial-time algorithms. Resource-Yoneda global
completeness is a major open goal requiring a resource-preserving compiler and effective normal forms.

### 8.4 The SUBSET SUM conservation programme

The SUBSET SUM programme's 2026-10-08 checkpoint identifies restricted findings about quotient pushdown, static
provenance, access/decoder trade-offs, representation interaction, basis/width uncertainty, and a contextually minimal
exact-sum quotient. The shared insight for G6 is a **conservation hypothesis**: apparent simplification in one
representational coordinate may move work into access, decoding, interaction, certification, memory or discovery.
This is an explanatory programme and **not** a theorem that all algorithmic speedups or all intelligent behaviours
conserve a single hidden quantity.

A global P versus NP consequence would require a class of CSIR programs complete for deterministic polynomial time, a
resource-faithful compiler, and a valid superpolynomial obstruction across that class. None is established by the
restricted agent theory.

## 9. AHI: verified acquisition and safe self-improvement

### 9.1 Versioned policy state

**Definition D5 (harness).** A harness is

$$H_n=(\mathcal{P}_n,\mathcal{G}_n,\pi_n,M_n,V_n,\Gamma),$$

where $\pi_n$ selects algorithms, tools, observations, budgets and stopping actions; $M_n$ stores admissible prior
experiences; $V_n$ names the independent validation mechanisms; and $\Gamma$ is the external approval policy. The
system can propose an update

$$\widetilde H_{n+1}=\mathcal{A}(H_n,\mathcal{D}_n),$$

but can only deploy $H_{n+1}=\Gamma(H_n,\widetilde H_{n+1},\mathcal{E}_n)$ when the independent experimental
evaluation and mandatory safeguards permit it. The agent must not rewrite its hidden oracle, task standard, or
promotion threshold as part of the candidate it is grading.

Three timescales correspond to AHI: T1 intratask repair, T2 intertask reusable capability acquisition, and T3
meta-adaptation of the *acquisition* policy. Meta-adaptation is research-only until T2 transfer is established.

### 9.2 The learning estimand

For a fixed reference distribution $D$, frozen task standard family and a normalised utility $U$, let

$$P_D(n)=\mathbb{E}_{T\sim D}[U(T,H_n)],\qquad G_D(n,k)=\frac{P_D(n+k)-P_D(n)}{k}.$$

This is a proposed learning-curve measure, not a proof that performance improves monotonically or that the system
converges to general intelligence. Safety-critical requirements remain constraints outside the average. Failure rates
should be reported per *independent task or repository cluster*, not treating repeated attempts on the same bug as
independent draws.

### 9.3 Error of the evaluator

Let $S_i(T)$ be real binary task satisfaction for harness $i$ and $V_i(T)$ its evaluator result. If externally
justified bounds $\Pr(V_i\ne S_i)\le\epsilon_i$ hold on the target population, then

$$\left|\mathbb{E}[V_i]-\mathbb{E}[S_i]\right|\le\epsilon_i.$$

**Proposition P8 (evaluator-error bound).** For the true improvement $\Delta_S=\mathbb{E}[S_1-S_0]$ and evaluated
improvement $\Delta_V=\mathbb{E}[V_1-V_0]$,

$$\Delta_S\ge \Delta_V-\epsilon_1-\epsilon_0.$$

*Proof.* Apply the triangle inequality to each evaluator-satisfaction discrepancy. QED. A finite-sample confidence
penalty must be added separately; if $\epsilon_i$ is unknown, this formula does **not** identify a positive true
improvement.

For $n$ independent paired task clusters and differences $X_i\in[-1,1]$, Hoeffding gives the conservative one-sided
lower bound $\mathbb{E}[X]\ge\bar X-\sqrt{2\log(1/\alpha)/n}$ with confidence $1-\alpha$, provided the candidate and
analysis were frozen before examining the test outcomes. This bound is often extremely loose; selection bias, shared
repositories and holdout reuse invalidate the naive inference.

### 9.4 A finite-sample no-free-lunch limitation

**Proposition P9 (indistinguishable-training limitation).** Without assumptions connecting observed training
experiences to future evaluation distribution, no update rule using only finite past observations guarantees strict
improvement across *all* possible future task distributions.

*Proof sketch.* Choose two future worlds with identical training data and opposed correct outputs on a never-observed
task. The update rule must make the same choice in both; whichever behaviour it selects is not uniformly better in
both worlds. QED. The proposition is intentionally qualitative and does not assert a particular numeric lower bound
for a restricted structured distribution.

### 9.5 Certified learning event

A **verified learning event** (VLE) is an engineering object with a fixed baseline miss, independent defect witness,
scoped diagnosis, candidate learned executable method, negative and positive controls, frozen validation and unseen
transfer. Only a repeated, properly powered held-out comparison warrants a population-level learning advantage. One
event shows the mechanism can operate; it does not prove that G6 has human-level self-improvement.

## 10. A joint CSIR–ECHA obstruction and capability discovery calculus

The central new proposal is to represent a task's *capability deficit* as two indexed relations:

$$\mathrm{CanSolve}(\mathcal{P},S,B)\quad\text{and}\quad\mathrm{CanJustify}(\mathcal{P},\mathcal{V},S,B).$$

The first concerns existence of an admissible program meeting task predicates; the second concerns available checked
evidence for those predicates. These can diverge dramatically. A capable image generator may produce an accurate
medical interpretation without a trustworthy assessor; a verifier may show that a proposed code patch is wrong while
being unable to produce a correct patch; a SUBSET SUM solver may find YES witnesses but fail to certify NO answers
efficiently.

A metacognitive planner should maintain a **hypothesis set** of deficit causes:

$$\mathcal{O}_T=\lbrace \text{compute},\text{access},\text{decode},\text{interface},\text{observation},\text{oracle},\text{authority},\text{resource},\text{standard}\rbrace.$$

For each cause it should propose a distinct intervention with a measurable predicted consequence: new primitive,
changed representation, alternative decoder, better source, independent oracle, changed route, human clarification,
or qualified abstention. The self-improving system then learns *which kinds of intervention worked* on matched future
contexts. This connects the SUBSET SUM programme's algorithmic atlas to ECHA without claiming that ECHA supplies
universal ground truth.

**Hypothesis H1 (diagnostic advantage):** Given equal resources and candidate tools, explicit CSIR cause
classification improves the correctness of resource/capability selection over language-only plans.

**Hypothesis H2 (evidence advantage):** Adding ECHA evidence typing reduces unsupported `verified` outcomes on tasks
with observable defects, at acceptable cost, compared with self-review/consensus.

**Hypothesis H3 (adaptive advantage):** A learned policy over typed capability deficits improves held-out verified
utility relative to both static G6 and a memory-only system.

**Hypothesis H4 (cross-domain transfer):** A skill trained to recognise inaccessible source evidence or unsound
verifier provenance transfers from repository patches to a different domain better than task-identifier matching
alone.

These hypotheses are mutually distinguishable. Their failure modes can be diagnosed separately.

## 11. G6 mapping and the current implementation bridge

### 11.1 Atlas coverage and limits

The companion machine-readable atlas (not published with this document) assigns each component in the component
library an initial CSIR role and evidence obligation. The CSIR roles are **hypotheses inferred from names and targeted
source inspections**. This is not a source-level, operation-by-operation certification of the component library or a
guarantee that every module works.

The primary consolidation should not produce one competing micro-theory per component. The finite observed vocabulary
should be compressed into *capability clusters* representing input requirements, effects, observations,
transformations, costs, failure conditions and reuse conditions. Within each cluster, multiple components may be
redundant, complementary or independently necessary. Full equivalence tests need live inputs and a declared
observation grammar.

### 11.2 Real code paths to reuse

The G6 implementation as of 11 October 2026 includes:

- `g6.research.mission_bridge` (`ir`, `checks`): a **default-off** research MissionIR with typed artifact edges,
  effects, standards and resource screening, designed to lower onto the existing workforce plan rules
  (`g6.workforce.plans`), not to be a second runtime.
- `g6.research.mission_bridge.bundle`: mapping of evidence to the existing ECHA kernel's claim rules; refuses some bad
  formal-certificate and process-evidence promotions, but reports live authority gaps.
- A preregistered five-arm mission comparison harness and 35 evaluation tasks, **built and tested according to its
  own report, not yet run on a model**.
- `g6.research.echa`: research-only ECHA records, claim checker, ledger, access checks, default off.
- `g6.workforce` modules `acceptance`, `coding`, `capabilities`, `plans`, `service`, `runtime`, `scheduler`,
  `coding_learning`: existing operational mission, risk, routing and learning system.
- `g6.reliability_pipeline` and `g6.mcp`: general-purpose agent and exposed surfaces; the earlier activation audit
  found important control decisions still left to the external calling agent.

The MissionIR ADR is explicit: it implements **static graph checking**, recorded-value replay, evidence bundling and
experiment plumbing; it does not yet realise arbitrary operational multi-domain workflows nor a verified
general-purpose agent. Any attempt to build another MissionIR package would be redundant.

### 11.3 A serious trust-boundary blocker

The existing mission claim bundle's synthetic tests mark executable evidence as independent when the caller supplies
a `sealed_fixture` digest. Its own report says the bundle does not confirm the fixture's isolation from the producer.
A future production gate must verify the evaluator's custody and permissions, not trust an unverified caller claim;
otherwise the exact theorem's crucial independence hypothesis fails. The same applies to formal-check certificates: a
well-formed SHA-256 (Secure Hash Algorithm, 256-bit) digest is evidence of a string's formatting, not evidence that a
proof was checked. A verified theorem requires a trusted checker invocation and binding to the precise statement and
artifact.

### 11.4 Three operational certificates

Define three independently reported outputs:

1. **Execution receipt:** which tools ran, parameters/artifact hashes, budgets, side effects, approvals and failures;
   it certifies recorded process facts only under ledger integrity assumptions.
2. **Evidence statement:** each material predicate, source, test/falsifier, independence rationale,
   supported/refuted/conflicted/unknown status and limits; it establishes scoped support according to declared
   evaluator assumptions.
3. **Formal certificate:** an actual kernel-checked proof of a clearly specified proposition and its
   checker/axiom/encoding hashes. Most legal, medical and engineering claims will not have one.

A final mission envelope can state `verified`, `qualified-draft` or `blocked-escalated` according to the fixed release
policy, but must name the exact predicates verified and gaps outstanding. The first two outputs cannot be laundered
into the third.

## 12. Worked mission compositions

### 12.1 Medico-legal claim analysis

**Input.** A user requests an assessment of a disputed injury claim, provides privileged documents and diagnostic
images, and asks for a balanced evidence-based report. Relevant standards include jurisdiction, expert-qualification
rules, privacy and consent, evidentiary scope, and whether the result is preliminary versus a formal expert opinion.

**CSIR sequence:** `E` freeze the claim and jurisdictional standard; `A` search governing authorities and
user-provided records; `Q` select context snippets while retaining complete citation/provenance; `C` legal specialist
analyses elements of the cause of action; `T` standardise chronology and quantities; `A` select an authorised
qualified image-analysis pathway (possibly human specialist, not automatically an external model); `V` verify image
provenance/consent, citations, contradictory facts and calculations; `R` draft alternative plausible causation
theories; `D` assemble argument, exhibits and uncertainty list; `G` obtain prescribed approvals.

**Failure-seam examples:** untrusted web instructions contaminate planning; retrieved authority is superseded; claim
is in the wrong jurisdiction; clinical image egress lacks consent; specialist model provides an opinion but not
clinical-grade validity; agreement among similar LLM reviewers is falsely treated as independent evidence; an
important negative finding was discarded during context compression. Each seam has a specific detection or abstention
requirement. In a consequential setting the result should ordinarily remain a *qualified draft* pending qualified
medical/legal review; a G6 output is not itself an expert medico-legal certificate.

### 12.2 Engineering design and procurement

**Input.** A user requests a design, optimisation, computer-aided design (CAD)/mesh illustrations, bill of materials,
construction sequence and compliance argument under a budget.

**CSIR sequence:** `E` clarify load cases, tolerances, site jurisdiction and safety class; `A` retrieve applicable
standards; `T` compile units and formulas; `C` engineer/formal-methods solver constructs exact constraints; `K`
choose an appropriate optimisation model and solver; `V` check dimensional consistency, infeasibility certificates
where available and numerical tolerances; `R` produce engineering alternatives; `X` generate Blender/mesh
visualisations; `V` distinguish *illustrative geometry* from validated manufacturable CAD and test mesh integrity; `A`
use logistics/market tools for current sourcing with explicit quoted prices and dates; `D` prepare report, procedures
and risk register; `G` qualified engineer signs off high-consequence use.

**Failure-seam examples:** physically impossible design due to missing load cases, arbitrary unit transforms, misuse
of solver statuses, relying on an image as structural proof, unavailable plugin or invented prices, wrong
jurisdictional code, over-budget plan, or side-effect execution without approval. Independent numerical/reference
checks and third-party professional review are distinct obligations.

### 12.3 SUBSET SUM algorithm-discovery mission

**Input.** An infinite family of positive SUBSET SUM instances; objective is to find a faster algorithm or a
restricted-model lower-bound certificate.

**CSIR sequence:** identify which exact/numerical/average-case variant is claimed; classify existing dynamic
programming (DP), meet-in-the-middle (MITM), representation and convolution primitives; define admissible
transformations, access interfaces and decoder complexity; identify whether a candidate is a coordinate rewrite or
moves the resource frontier; test on finite exact-reference cases, metamorphic scaling and adversarial instances;
request a proof of correctness and runtime; check formal certificates where tractable; retire incorrect conjectures
without claiming universal impossibility.

The strongest success test is **not** "the LLM invented something plausible." It is a new result with an
independently audited proof or reproducible new algorithm with correctness justified for its stated family, compared
against existing prior art. A restricted lower bound cannot be promoted to a P versus NP claim absent compiler
completeness.

## 13. Operational criteria for increasing generality and human-level performance

There is no known universal scalar measure of intelligence that makes architecture alone sufficient evidence of
human-level intelligence. We therefore propose **operational** dimensions of generality, all tied to unexposed tasks:
domain breadth, novelty/out-of-distribution transfer, learning rate per independent experience, external help
required, error calibration, ability to distinguish observation failure from computation failure, cost at comparable
quality, non-regression/retention, and safe autonomy under real permissions. Benchmarks must contain different task
structures and sources; a single high aggregate hides weak tails.

**Hypothesis H5 (self-improving generality; stated as a conjecture):** Under environments with sufficiently rich
independent feedback, an agent that can acquire reusable primitives and structural routing policies may improve its
coverage of new task distributions faster than a fixed-composition system at equal cost. This is not a theorem that
improvement is unbounded, guaranteed, recursive without limit, or will reach human-level intelligence. Tasks without
adequate feedback or with noncomputable/expensive verification remain hard.

The strongest form of recursive self-improvement would require the acquisition process itself to be improved over
time. Even if experiments find positive cross-domain learning gradients, this provides local empirical evidence, not
an absolute guarantee of an accelerating positive gradient. The governance architecture must not treat increasing
self-reference as increasing trust.

## 14. Experimental plan with independent attribution

The proposal has three stages which answer different scientific questions:

**Stage A: static explanatory ability.** On frozen SUBSET SUM and coding tasks, compare a minimal baseline planner
with the same model/tools plus (i) typed capability constraints and (ii) CSIR obstruction diagnoses. Grade exact
equivalence classifications, known separation witnesses, invalid tool/permission catches, cost/latency and solved
tasks. Use held-out algorithm families for transfer. A theorem may be proved in the restricted calculus; source-level
G6 activation remains a different hypothesis.

**Stage B: ECHA evaluation integrity.** Introduce independent negative controls, oracle provenance, held-out
acceptance tests and real reviewer boundaries. Contrast `process-only`, `LLM-judged`, `ECHA-typed` and true
independent checks. Report verified false accepts, abstentions, unresolved evidentiary gaps and cost. The October 9
ECHA Stage A historical study found **two** distinct false-accept tasks against a preregistered threshold of eight:
that study does not justify a learned guard or a claimed learning uplift.

**Stage C: adaptive capability acquisition.** Against untouched task populations, compare (A) base LLM, (B) static
G6, (C) memory-only G6, (D) static CSIR planner with ECHA, and (E) CSIR/ECHA with governed executable skill
acquisition; the distinction between D and E must be explicit. Candidate generation and evaluation should be run in
separate authority domains with hidden fixtures inaccessible to the adaptive system. Include an always-on oracle
baseline to distinguish *learning which check to invoke* from merely installing a check. Reuse the existing five-arm
mission comparison protocol where appropriate, but its announced five-arm static comparison does not already include
full adaptive learning.

Preregister expected cost, numbers of independent repositories/task families, exclusion reasons, statistical test and
stopping rules. Record whether candidate success on one task is generation success, verification success or simply
learned policy activation. An absent independent evaluator should yield `unknown`; an insufficient failure population
should yield `inconclusive`, not a search for post-hoc easy benchmarks.

## 15. Formalisation and implementation roadmap

A companion implementation handoff (not published with this document) specifies detailed checkpoints. Prioritise:

- **F0 — Pin/merge source truth:** current source revision, MissionIR ADR, recorded ECHA decisions, contract
  vocabulary and exact intended research scope. Do not duplicate the mission bridge or an independent evidence ledger.
- **F1 — Formal core:** define types, effects, trace inclusion, four-state evidence and certificate provenance.
  Machine-check the *restricted* serial refinement theorem and typed-sum quotient, or record why the chosen proof
  assistant encoding remains incomplete.
- **F2 — Interface refinement:** prove source/sink compositional contracts for two real component operations and one
  independent oracle, including failure and timeout behaviours.
- **F3 — Oracle authority:** implement a stronger externally controlled evaluator boundary, certificate checking and
  exact provenance before relying on the word `independent`.
- **F4 — Active mission:** deploy no production changes; run a synthetic medico-legal and engineering
  multi-component mission in research mode, with adverse inputs and honest qualified/blocked outputs.
- **F5 — Comparative study:** run the preregistered mission comparison on fixed task identifiers and model budgets,
  then separately design adaptive studies once valid baseline failure opportunities exist.
- **F6 — Publication:** split theory, systems and empirical results into independently supported manuscripts;
  perform specialist prior-art and mathematical audits and publish negative findings.

### 15.1 Proof-obligation ledger: first twelve gates

Each gate is a proof obligation (PO).

| ID | Obligation | Status | Falsifier / completion test |
|---|---|---|---|
| PO-01 | Type/effect composition well defined | Open formalisation | Illegal resource sharing or egress accepted by interpreter |
| PO-02 | Serial refinement C1 | Conditional proof on paper | Concrete trace outside abstract transition |
| PO-03 | Four-state evidence operations and contradiction policy | Definition, not implemented universally | Conflict silently becomes `verified` |
| PO-04 | Typed-sum quotient under exact declared contexts | Elementary paper proof; toy exhaustive tests | Equal sums distinguished by an allowed context |
| PO-05 | ECHA oracle authority enforced outside agent process | Unproved for live G6 | Learner can write fixture/evaluator result |
| PO-06 | Formal certificate truly kernel checked | Unproved for live G6 | Digest-only pseudo-proof accepted |
| PO-07 | Runtime budget bound for multi-agent graphs | Unproved | Retries/parallel calls exceed ceiling unaccounted |
| PO-08 | Resource-Yoneda closure effective/complete | Open research | New admissible program outside asserted normal form |
| PO-09 | New-capability separation witness | Theorem schema; discovery open | Claimed witness violates type or continuation assumptions |
| PO-10 | ECHA false acceptance reduction | Not demonstrated broadly | Frozen held-out comparison no gain or more misses |
| PO-11 | Transferable learned component | Not demonstrated | Guard succeeds only on training function/signature |
| PO-12 | Artificial general intelligence (AGI)/human-level generality claim | Not established | Broad out-of-distribution tasks, calibration/learning/cost failures |

## 16. Relation to established work, novelty and limitations

The conceptual foundations are not novel in isolation. Contextual equivalence and syntactic congruence relate to
Myhill–Nerode and program semantics; categorical embeddings relate to the Yoneda lemma; resource accounting and
effects relate to graded/effect systems; contracts and compositional verification have established formal methods;
the observation bound is classical decision theory; and the self-improvement search/evaluation loop has substantial
prior art.

In particular, DreamCoder learns compositional program abstractions and search strategies [R1]. FunSearch couples LLM
program proposals to executable evaluators [R2]. AlphaEvolve similarly uses automated scoring to improve algorithms
[R3]. Darwin Gödel Machine empirically self-modifies agent code under benchmark evaluation [R4]. Quantitative
multi-agent assume–guarantee verification is an explicit published research direction [R5], as is machine-readable
evidence/audit probing of agents [R6]. The US National Institute of Standards and Technology (NIST) has reported both
prompt-injection/hijacking risks and evaluation gaming [R7–R8].

The **candidate** contribution is the integrated treatment of (i) resource-indexed structural algorithm equivalence
and obstruction; (ii) evidence-indexed claim admissibility and oracle limitations; (iii) typed real-world capabilities
with side effects/authority; and (iv) causal, transfer-validated acquisition of executable components. To be
publishable as a technical advance, this synthesis must produce at least one new theorem, new mechanism or
statistically credible capability advantage **over these baselines**, not merely an elegant description of G6. The
claim is best framed as a falsifiable research programme until these tests are completed.

Further limitations: task specifications are incomplete; human reviewers err; tool outputs can be prompt-injected;
versioned provenance cannot attest the truth of initial statements; evidence may be intentionally suppressed; scarce
evaluations cannot bound extremely rare severe failures; open-world processes change between calls; stochastic-model
performance shifts with provider versions; resource price changes; and no finite component atlas can honestly claim
to represent arbitrary unbounded intelligence without a separate completeness argument.

## References and source provenance

### Primary material from the G6 research programme

- `HANDOFF_resource_yoneda_computational_solvability_2026-10-08.md` and its checkpoint TeX/PDF (not published):
  restricted computational-solvability theory and open compiler questions.
- `typed_arithmetic_full_abstraction_subset_sum_v0_1.md` (not published): typed sum/context equivalence and graded
  semiring closure.
- `G6_Adaptive_Harness_Intelligence_Theory_v0.1.md` (not published): adaptation, learning gradient, observability
  limits and metacognition.
- [G6 Evidence-Constrained Harness Adaptation specification, v0.1](G6_Evidence_Constrained_Harness_Adaptation_Spec_v0.1.md);
  [The validation paradox](G6_Self_Validation_Paradox_Resolution.md): independent evidence/partial falsification and
  learning protocols.
- `G6_INTEGRATED_RESEARCH_AUDIT.md` (10 October 2026, not published): mission wiring, CSIR/ECHA audit and source
  mappings.
- Repository checkpoint (11 October 2026): the mission intermediate representation ADR; the mission claim evidence
  report; the ECHA Stage A results (not published).

### External related work (verified public references)

- [R1] Ellis et al. (2021), DreamCoder: *Bootstrapping Inductive Program Synthesis with Wake-Sleep Library Learning*,
  PLDI (Programming Language Design and Implementation). https://doi.org/10.1145/3453483.3454080
- [R2] Romera-Paredes et al. (2024), *Mathematical discoveries from program search with large language models*,
  Nature. https://doi.org/10.1038/s41586-023-06924-6
- [R3] Google DeepMind (2025), *AlphaEvolve*, product/research overview.
  https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/
- [R4] Zhang et al. (2025, revised 2026), *Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents*.
  https://arxiv.org/abs/2505.22954
- [R5] Dewes and Dimitrova (2025), *Contract-based Design and Verification of Multi-Agent Systems with Quantitative
  Temporal Requirements*, AAAI (Association for the Advancement of Artificial Intelligence) Conference.
  https://doi.org/10.1609/aaai.v39i22.34480
- [R6] NIST (2026), *Building Evaluation Probes into Agentic AI*.
  https://www.nist.gov/programs-projects/building-evaluation-probes-agentic-ai
- [R7] NIST (2025), *Strengthening AI Agent Hijacking Evaluations*.
  https://www.nist.gov/news-events/news/2025/01/technical-blog-strengthening-ai-agent-hijacking-evaluations
- [R8] NIST (2025), *Cheating on AI Agent Evaluations*. https://www.nist.gov/caissi/cheating-ai-agent-evaluations
