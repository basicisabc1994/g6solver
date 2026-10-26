# The Validation Paradox: How Can G6 Learn to Solve Problems When It Cannot Reliably Determine Whether It Has Solved Them?

The validation paradox is, I think, the most fundamental unresolved problem in Adaptive Harness Intelligence (AHI).

It is not simply an implementation difficulty. It is an epistemological problem concerning the relationship between intelligence, learning, verification, and the availability of reliable information.

The objection can be stated as follows:

> If G6 already possesses a reliable procedure for deciding whether its answers are correct, then perhaps it has already solved the hardest part of the problem. But if it does not possess such a procedure, how can it reliably learn from failures, decide which improvements work, or know that it has become more intelligent?

This is a genuine obstacle, and it cannot be eliminated merely by adding more agents, reflection, consensus, or increasingly sophisticated evaluators.

My proposed resolution is to separate three things that we have partly conflated:

1. Validating a particular result: Can we establish that this specific answer satisfies a known requirement?
2. Validating a learning intervention: Can we establish that a new tool, test, algorithm, or routing rule improves performance on a measurable class of tasks?
3. Validating general competence: Can we establish that the entire system will reliably solve arbitrary future problems?

The first is sometimes straightforward, the second can often be investigated experimentally, and the third is generally impossible to establish without substantial restrictions and assumptions.

The important discovery is that G6 does not need to solve the third problem before it can make scientifically valid progress on the second.

That gives us a workable path.

## 1. Why the paradox does not invalidate harness learning

The crucial distinction is between finding a solution and checking a proposed solution.

For example, consider a difficult mathematical problem. Finding a proof may take years, but once a formal proof has been constructed, checking each inference against a small trusted proof kernel may be comparatively straightforward.

Similarly, a coding agent may struggle to discover the correct implementation, but an independently specified executable test can reject a particular incorrect implementation.

There are limitations. Not every task has an efficient correctness certificate; an incorrect or incomplete specification can undermine verification; and some failures may be invisible to every tool the system currently possesses.

Nevertheless, G6 can exploit cases in which evaluation is more tractable than generation, and use expensive independent evaluation selectively when it is not.

This suggests the following principle:

$$\boxed{ \text{The learning process need not possess a universal solver.} }$$

It needs enough trustworthy feedback about the consequences of particular interventions to distinguish useful learning from ineffective or harmful changes.

That is a much weaker—and more practically attainable—requirement.

But there is a catch: if that feedback is itself unreliable or systematically blind to the failures we care about, the system may simply learn to satisfy an imperfect evaluator.

Therefore, a trustworthy G6 must explicitly represent the limits of its evaluators and never silently promote success against a test into proof of unrestricted correctness.

## 2. The practical solution: build a ladder of verification oracles

There is already a well-established name for part of this problem in software engineering: the test oracle problem—how to determine whether a program's output is correct when the expected answer is unknown or expensive to obtain. The literature recognises specifications, models, metamorphic relations, and human judgement as different, imperfect approaches to this problem.

I would make oracle quality a first-class part of G6.

| Level                          | Evidence available                                                           | What G6 can establish                                                |
| ------------------------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| A — Formal certificate         | Independently checked proof, exhaustive verification within defined bounds   | Correctness relative to the formal specification and trusted checker |
| B — Trusted executable oracle  | Independently validated reference implementation or decisive acceptance test | Correctness on the evaluated instances and conditions                |
| C — Partial oracle             | Invariants, metamorphic properties, regression tests, known counterexamples  | Particular failures or violated requirements                         |
| D — Fallible evaluator         | LLM review, human judgement, heuristic quality scores                        | Evidence supporting a judgement, with unresolved uncertainty         |
| E — No discriminating evidence | Neither a usable oracle nor an applicable independent check                  | No justified correctness conclusion                                  |

The levels are not an absolute ranking of all conceivable verification techniques. Their purpose is to make explicit what a particular source of evidence actually establishes.

For example, a formal proof of the wrong specification may be less useful than an independently validated integration test addressing the customer's actual requirements.

### The critical insight: detecting a defect does not require solving the entire task

Suppose an agent modifies a repository of 100,000 lines of code.

One cannot know whether the entire modified system is correct.

However, one may still know with certainty that the task prohibited modifications to an authentication function and that the agent changed it.

That is a genuine defect one can identify without solving the complete software-engineering problem.

One may also establish that the program fails to compile, breaches a declared API contract, mishandles a particular boundary input, or violates a security invariant.

These are partial falsification oracles.

They do not prove that passing answers are correct, but they can conclusively reject certain incorrect answers.

This gives G6 a different initial learning objective:

> Learn to discover and eliminate identifiable defects, without treating the absence of detected defects as proof of correctness.

This is more achievable than trying to learn an infallible answer judge.

It also aligns directly with G6's Swiss-cheese architecture. Each component can address a defined failure class without claiming to cover every possible failure.

## 3. How do we validate the validators without infinite regress?

This is the next obvious question.

If G6 learns a new test, how do we know the test is good? Do we need another test to verify that test, and another to verify the second test?

We cannot resolve that regress by adding an unlimited chain of LLM reviewers.

Instead, we have to introduce explicit, limited trust anchors and independently designed negative controls.

Consider a learned component intended to detect unauthorised modifications to helper functions.

We can establish that this component actually works on certain cases by deliberately introducing a known unauthorised modification and confirming that the component rejects it.

Then deliberately introduce an authorised modification and confirm that it is not incorrectly rejected.

The injected defect supplies the ground truth for that narrow test.

We do not need to know whether the entire modified repository is correct to establish that the safeguard detects the particular violation.

This is the logic behind mutation testing and controlled fault injection.

It is also why G6's earlier R-1 observation holds: a benchmark comparison that cannot fail on any delivered patch is not evidence that G6 actively checks the adequacy of its tests.

An effective learned safeguard should have an independently constructed negative control capable of making it fail.

However, passing those controls establishes only that the safeguard detects the tested failures—not that it will detect all possible failures.

The independence and limitations of these validation mechanisms must therefore be recorded.

### The role of metamorphic testing

There is a particularly useful technique for cases where the exact correct output is unknown.

Metamorphic testing checks relationships between outputs rather than requiring the correct answer for every input. This is an established method of partially addressing the oracle problem.

For example, let $F(A,t)$ be a solver that decides whether a positive Subset Sum instance has a solution.

For any positive integer $k$:

$$F(A,t)=F(kA,kt).$$

Multiplying every number and the target by the same positive integer must preserve the answer.

Therefore, if a proposed solver returns different answers for the original and scaled instances, it must be wrong on at least one of them.

But if it returns the same answer for both, that tells us nothing conclusive about whether the common answer is correct.

The important structure is:

$$\boxed{ \text{Violation of a valid invariant} \Rightarrow \text{Evidence of incorrectness} }$$

while

$$\boxed{ \text{No observed violation} \not\Rightarrow \text{Correctness}. }$$

That asymmetry is extremely valuable.

It gives G6 a way to learn useful falsifiers even when it cannot construct a general-purpose solver.

## 4. A small mathematical result clarifies exactly what we can claim

Let $S(H,T)\in\lbrace 0,1\rbrace$ indicate whether harness $H$ genuinely satisfies the task's standard on task $T$.

Let $V(H,T)\in\lbrace 0,1\rbrace$ be the judgement of an external evaluator.

Suppose the evaluator's disagreement with actual correctness is bounded by $\epsilon$, over the task distribution being studied:

$$P\big(V(H,T)\neq S(H,T)\big)\leq\epsilon.$$

Then:

$$\left| \mathbb E[V(H,T)]-\mathbb E[S(H,T)] \right|\leq\epsilon.$$

This follows directly because the two binary quantities can differ only when the evaluator is mistaken.

Now compare the original harness $H_0$ with an adaptive harness $H_1$.

If the evaluator reports an improvement $\widehat{\Delta}$, the true performance improvement satisfies

$$\boxed{ \Delta_{\mathrm{true}}\geq \widehat{\Delta}-(\epsilon_0+\epsilon_1). }$$

This is a conservative population-level bound; finite-sample uncertainty must also be accounted for.

The implication is striking.

Imagine G6 appears to improve verified success by five percentage points, but we can only bound the evaluator's error at four percentage points for each harness.

We cannot establish that the true improvement is positive.

Conversely, if the evaluator is genuinely trustworthy to a sufficiently tight bound, a large enough measured improvement can establish a positive benefit even though the evaluator is not perfect.

The crucial point is that $\epsilon$ cannot be invented or estimated using the same unvalidated evaluator. It needs independent evidence, and the error rate may change when G6 adapts specifically to the evaluator.

This gives our theory a precise scientific boundary:

> Harness improvement is identifiable only to the extent that the available evaluation process supplies sufficiently informative, independently justified evidence.

When the oracle is weak, our conclusions must be correspondingly qualified.

When the oracle is absent, an improvement in actual correctness may be unidentifiable.

There is no general mathematical trick that removes this limitation. We must either improve the evidence, restrict the task class, or weaken the claim.

## 5. The experiment I would actually run for G6

I would stop trying to establish G6's entire correctness capability in a single experiment.

Instead, I would construct a controlled environment in which we deliberately know more than the agent, while ensuring that the agent cannot access that privileged knowledge.

We can then test whether G6 learns useful computational mechanisms from experience.

- **A. Independent ground truth:** historical bugs, confirmed defects, hidden acceptance tests, or deliberately injected faults.
- **Two arms on the same ground truth:**
  - **Static G6:** no learning across episodes.
  - **Adaptive G6:** learns safeguards from independent feedback.
- **B. Fresh unseen tasks:** same underlying model, controlled budgets, frozen hidden evaluator; measure actual transferable improvement.
- **Measured:** defects caught, false rejections, success, cost, learned-component activation.

Here is a concrete protocol.

| Phase                 | What we do                                                                                                           | What it establishes                             |
| --------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 1. Construct tasks    | Collect real historical repository bugs with independently confirmed fixes, plus controlled fault-injection examples | A source of known failure evidence              |
| 2. Separate data      | Create distinct learning, development, and hidden evaluation sets; split by repository where possible                | Protection against memorisation                 |
| 3. Establish baseline | Run the unchanged model and static G6 with recorded resource budgets                                                 | Initial capability and verification performance |
| 4. Adapt              | Allow G6 to learn executable guards, tests, and component-selection policies from learning-set failures              | Whether the learning machinery operates         |
| 5. Freeze             | Lock the harness and evaluate on previously unseen tasks using an independent grader                                 | Whether learning transfers                      |
| 6. Repeat             | Evaluate after successive learning batches                                                                           | The learning curve and its stability            |

Initially, I would aim for a pilot involving several distinct repositories and a few dozen well-validated tasks. That would demonstrate feasibility, not provide definitive statistical evidence of broad improvements.

We should also run a memory-only control: a system that remembers textual lessons but cannot create executable safeguards or modify its tool-selection policy.

Otherwise, we may mistake conventional experience recall for the proposed AHI mechanism.

### What counts as success?

Imagine G6 experiences a hidden compatibility failure.

It subsequently learns to extract relevant API contracts and construct targeted tests before making similar changes.

On a new repository, without having seen its hidden test cases, it invokes its newly acquired safeguard and catches a previously unseen compatibility problem that static G6 misses.

If this repeats across independent tasks while maintaining acceptable false-positive rates and cost, we have evidence of transferable learning.

We do not need to establish the correctness of every possible API modification to demonstrate that the particular learned capability is effective.

This is the practical escape from the paradox: evaluate the causal effect of the acquired capability on independently assessable failures, rather than demand global correctness of the entire agent.

An earlier G6 experiment is a useful starting point. The collateral-edit detector appeared to catch failures missed by the base model, but we have not yet shown that an adaptive learner discovered, generalised, and deployed that detector as a result of experience.

That is the missing experiment.

## 6. What about genuinely difficult problems without any known solution?

This is where the concern becomes more serious.

Consider our work on Subset Sum and difficult algorithmic representations.

For a YES instance, a proposed subset is a readily checkable certificate:

$$\sum_{i\in I}a_i=t.$$

For a NO instance, verifying that no such subset exists may itself require substantial computation unless a suitable independently checkable certificate or structural argument is available.

There is no general guarantee that a short, efficient NO certificate exists for every instance.

So how can we test a system attempting to discover more efficient Subset Sum algorithms?

We can investigate different levels of the claim.

| Claim                                                                  | Possible validation                                                           |
| ---------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Algorithm solves small instances                                       | Exact enumeration or dynamic programming as a trusted reference               |
| Algorithm solves a specified restricted family                         | Exhaustive bounded testing, established reference procedures, or formal proof |
| Representation preserves solutions                                     | Check witnesses, test reductions, seek counterexamples, prove equivalence     |
| Algorithm is efficient on difficult instances                          | Benchmark runtime and correctness against independently established answers   |
| Algorithm solves every positive Subset Sum instance in polynomial time | Requires a general mathematical proof or an equivalent rigorous justification |

The last claim cannot be validated merely by passing a finite collection of instances.

But the first four can reveal meaningful progress or refute incorrect conjectures.

We can construct adversarial instances, test necessary conditions, discover invariants, identify counterexamples, and narrow the remaining theoretical obstruction.

This is closely related to counterexample-guided inductive synthesis (CEGIS), which iterates between proposing a candidate solution and finding counterexamples. When the verifier is complete for the formal specification, this can produce rigorous guarantees; when checking is incomplete, the conclusions must be correspondingly limited.

The G6 learning process could adopt a more general, explicitly partial version of this approach.

The essential distinction is:

$$\text{No counterexample found} \neq \text{No counterexample exists}.$$

For an open mathematical problem, there may be no available path beyond developing new mathematics.

For an engineering workflow, partial guarantees and measured reductions in particular failure risks can still have considerable practical value.

## 7. The harder remaining problem: making weak supervision useful

What if the system becomes substantially better at solving tasks than the humans or evaluators supervising it?

This is closely related to the weak-to-strong generalisation and scalable oversight problems.

OpenAI's 2024 research demonstrated that stronger models can sometimes learn useful behaviour from substantially weaker supervisors, although important performance and reliability limitations remain.

This suggests that perfect labels may not always be necessary for useful learning.

However, it does not remove the need for trustworthy independent evaluation when claiming improvement.

For G6, I would distinguish two processes.

Learning with imperfect evidence: The agent may propose new skills based on uncertain diagnoses, heuristic feedback, experimental results, or partial observations. That is permissible within the research environment.

Promoting a learned capability: The agent must provide evidence satisfying the relevant deployment standard before the new component is trusted in consequential workflows.

In other words:

$$\boxed{ \text{Learning may be speculative. Deployment must be justified.} }$$

This is how we avoid turning uncertain experiences into increasingly confident but potentially false operational beliefs.

It also prevents a dangerous form of recursive self-improvement: the learner must not gradually modify its own evaluator until the evaluator becomes easier to satisfy.

NIST has highlighted how tool-using agents can exploit evaluation environments, demonstrating why benchmark isolation and evaluation integrity matter.

For G6, the independent evaluation environment should be inaccessible to the adapting agent. Critical standards and promotion authority should not be modifiable through ordinary self-improvement.

## 8. A principle I would add to the formal theory

I would introduce Evidence-Constrained Harness Adaptation as a foundational principle of AHI.

Let $\mathcal E$ denote the evidence available to support a proposed modification $H\rightarrow H'$.

Let $\mathcal C(\mathcal E)$ be the set of claims justified by that evidence.

Then the system may promote an improvement claim $c$ only when

$$\boxed{ c\in\mathcal C(\mathcal E). }$$

The challenge, of course, is giving $\mathcal C$ a rigorous interpretation through specified proof rules, statistical assumptions, evaluator calibration, or independently established properties.

For example:

- Catching all 20 deliberately injected defects justifies a claim about those 20 defects.
- Catching previously unseen defects in a preregistered experiment can justify a statistical claim about the sampled task distribution.
- Proving a property in Lean can justify a theorem relative to its formal statement, axioms, and trusted checking infrastructure.
- Having three LLM reviewers agree does not establish correctness, especially when their errors may be correlated.

The system should therefore store not merely what it believes it learned, but the evidence and assumptions under which the learning claim is valid.

That could make G6's experiential memory substantially more useful than a conventional database of successful prompts and failed attempts.

It would become a growing, evidence-indexed library of computational capabilities, validated conditions of applicability, known limitations, and counterexamples.

## 9. The next practical move

I would give G6's coding agent one sharply bounded objective:

Demonstrate one complete, independently verified learning event.

For a single well-defined defect family, establish that G6 can encounter a failure, diagnose it, propose a reusable executable safeguard, pass an independent negative-control test, and subsequently activate the safeguard successfully on a previously unseen repository.

Do not require this first experiment to prove that G6 is generally more intelligent.

Do not require the system to construct an infallible correctness evaluator.

Do require evidence that the acquired safeguard improves a measured outcome relative to an unchanged control, with enough repetitions to distinguish a genuine effect from chance.

Once that works, move from individual learning events to the learning gradient across many failure classes.

Only after that would I invest heavily in meta-learning—the system learning which experiments, verification tools, or capability-acquisition strategies are most productive.

## The conclusion

The validation paradox is real, and there is no universal method of escaping it for arbitrary open-ended tasks with no trustworthy feedback.

But it does not prevent G6 from developing a rigorous science of adaptive harness learning.

The path forward is to build outward from narrow, independently verifiable properties; test whether experience produces transferable computational improvements; quantify what the available evidence establishes; and explicitly mark what remains unknown.

I would express the central principle this way:

> An adaptive intelligence need not know the correct solution to every problem. It must learn to distinguish what its current methods can establish, what they can falsify, what remains uncertain, and which new computational or observational capabilities would reduce that uncertainty.

That last point may be the deepest part of the theory.

The ultimate metacognitive capability is not simply recognising that an answer might be wrong. It is recognising why the existing computational system is incapable of resolving that uncertainty, and learning what new capability would be needed to make progress.

We cannot guarantee that such a capability always exists or can be discovered efficiently. But we can test whether G6 becomes progressively better at finding and using those capabilities in domains where independent evaluation is possible.

That is a defensible research programme, a practical engineering strategy, and a way to make progress without pretending that we have already solved the verification problem itself.