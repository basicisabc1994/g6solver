# Adaptation Through Harness Engineering: Kernel Spec and Theorems

> **Status: design rationale.** The proofs are not machine-checked, and the design is not validated. Nothing here is evidence that G6 works; where this document and the library's own documentation differ, the library's documentation describes what G6 does.

Oct 18, 2026

## Abstract and reading guide

The result: a seven-component kernel that wraps any existing agent harness, routes between frozen LLMs, and rewrites its own harness code, with a proof that it never violates its invariants and converges at the minimax-optimal rate to a Pareto-stationary point of the reward/cost frontier. What is *not* proved, and cannot be: that any fixed harness is globally optimal, or that the learned frontier is the true one when the reward signal is a proxy.

The design principle is **adaptation through harness engineering**: the models are static oracles, so every bit of adaptation lives in the layer around them. That layer is the only thing that can learn, and this document specifies the smallest version of it that can learn *safely*.

Three kinds of claim appear, marked so they are not confused:

- **Theorem**: proved under stated assumptions; the proof follows the statement.
- **Proposition**: follows from a cited result (Rice, Papadimitriou–Tsitsiklis, Lai–Robbins, Désidéri, Ghadimi–Lan) with the reduction given.
- **Design claim**: a dominance argument that justifies a kernel component; sound as an inequality, but its premises are empirical.

Reading order for someone short on time: Section 2 (the kernel), Section 3 (the invariants), Theorem 7.1 (nothing unsafe ever ships), Theorem 8.2 (the convergence result), Section 10 (what is left open). Sections 1 and 6 carry the formal machinery and the impossibility results that bound what any such system can claim.

## 1. Formal setting

Everything below is defined relative to a fixed family of oracles, a fixed host harness, and a task distribution; the kernel is the only object that changes.

**Oracles.** A finite set M = {M\_1, …, M\_m} of frozen stochastic maps from prompts to completions. Each M\_j has an unknown conditional error kernel ε\_j(c, p) = P(output is wrong | context c, prompt p) and a known price vector per token. Nothing about M\_j is assumed differentiable or introspectable; it is sampled only.

**Host harness.** A program H (the existing agent loop the kernel wraps) exposing a small interface: it emits *decision points* — moments where it is about to call a model, call a tool, compact context, spawn a subagent, or halt — and accepts an override at each. The kernel is correct for any H satisfying this interface; it never reads H's internals.

**Kernel.** A pair (θ, κ): θ ∈ Θ ⊂ R^d, a continuous parameter vector controlling decision-point overrides and model routing; κ ∈ K, a *code state* (the kernel's own source, as an abstract syntax tree) subject to rewriting. The composed system is written H\[θ, κ\].

**Trajectory and objectives.** Running H\[θ, κ\] on a task x ∼ D yields a trajectory τ, a terminal reward R(τ) ∈ \[0, 1\], and a cost vector:

```latex
C(\tau) = \big(C_{\mathrm{tok}}(\tau),\ C_{\mathrm{wall}}(\tau),\ C_{\$}(\tau),\ C_{\mathrm{risk}}(\tau)\big) \in \mathbb{R}^4_{\ge 0}
```

C\_risk is the expected loss from irreversible side effects, defined as the sum over irreversible actions a in τ of P(a is wrong) × L(a) for a loss scale L(a) declared by the tool registry.

**The objective vector.** For a kernel state (θ, κ), define the (k+1)-vector of expectations:

```latex
F(\theta,\kappa) = \big(J(\theta,\kappa),\ -C_1(\theta,\kappa),\ \dots,\ -C_k(\theta,\kappa)\big), \qquad J = \mathbb{E}_{x\sim D,\ \tau\sim H[\theta,\kappa](x)}[R(\tau)]
```

A state (θ, κ) **Pareto-dominates** (θ′, κ′) if F(θ, κ) ≥ F(θ′, κ′) componentwise with at least one strict inequality. The **Pareto frontier** P is the set of non-dominated states. (θ, κ) is **Pareto-stationary** if no direction v ∈ R^d has v·∇\_θ F\_i > 0 for every i, i.e. zero lies in the convex hull of the gradients {∇\_θ F\_i}.

**Regret.** Against a comparator state (θ\*, κ\*) ∈ P and a scalarization λ in the simplex, the T-episode scalarized regret is:

```latex
\mathrm{Reg}_T(\lambda) = \sum_{t=1}^{T} \lambda^{\top}\big(F(\theta^*,\kappa^*) - F(\theta_t,\kappa_t)\big)
```

The kernel is **asymptotically Pareto-efficient** if sup over λ of Reg\_T(λ) is o(T).

**Invariant predicate.** A fixed, kernel-independent predicate Inv: K × Θ → {0, 1} written by the operator (Section 3). Only states with Inv = 1 are permitted to run on live tasks. Inv is the one object in this document that the kernel may read but never write.

Two conventions used throughout: the per-call error rate of any oracle is bounded away from zero, ε\_j ≥ ε\_min > 0 (no oracle is perfect); and an *independent* check of an output has error correlation ρ with the producer, where ρ depends on how much context the checker shares with the producer, ρ → 1 for a same-context self-check and ρ → 0 for a disjoint-context check.

## 2. Kernel specification

The kernel is seven components and a three-hook interface; each component is justified by naming the property that is lost without it, which is also the minimality argument.

&#91;embedded content: kernel architecture · 7 components, 3 hooks, frozen oracles\]

The host calls hooks at its five kinds of decision point and receives overrides; the ledger feeds both learners; only the gate can promote into the store, and the store is what the interposer runs. Shaded components are the protected region κ\_P that no rewrite may touch.

### 2.1 Host interface (the only coupling)

The host harness H must expose exactly three hooks and one declaration. Nothing else about H is assumed.

| Hook | When H calls it | What the kernel may return |
| --- | --- | --- |
| `before_decision(dp)` | Before every model call, tool call, compaction, spawn, or halt | `pass`, or an override: a different oracle, a different compute budget, a verification request, a block |
| `after_outcome(dp, obs)` | After the action completes | Nothing; the kernel records |
| `end_episode(R, C)` | When the task ends | Nothing; the kernel records and may schedule a learning step |
| `declare_tools(registry)` | Once, at startup | Each tool tagged idempotent / reversible / irreversible, with a loss scale L for irreversible ones |

A host that calls the hooks is *kernel-compatible*. Hosts that do not (most existing harnesses) are wrapped by a shim that intercepts their model-client and tool-executor objects, which is where the decision points already are. The shim is host-specific; the kernel is not.

### 2.2 The seven components

| Component | Function | Property lost without it |
| --- | --- | --- |
| K1 Interposer | Receives hooks; applies θ-controlled overrides at decision points | No control surface; θ has nothing to act on |
| K2 Router | Chooses which oracle M\_j serves each model call, as a contextual bandit over (task features, step features) | Pinned to one model; cannot exploit cost/quality spread across oracles |
| K3 Ledger | Append-only log of every decision, override, observation, R, C, with the (θ, κ) hash that produced it | No gradient signal; no audit trail; no counterfactual estimation |
| K4 Optimizer | Stochastic multi-gradient step on θ from ledger samples (Section 8) | θ is static; no differentiable adaptation |
| K5 Rewriter | Proposes code edits κ → κ′ to the kernel's own non-protected regions, generated by an oracle given the ledger's failure traces | Only parameters adapt; structural improvements (new heuristics, new tools, new compaction logic) are impossible |
| K6 Gate | Checks Inv on every proposal; runs shadow and canary evaluation; promotes or rolls back (Section 4). Contains the context-isolated verifier | Unsafe code can run; regressions are not caught; no guarantee survives |
| K7 Store | Versioned (θ\_t, κ\_t) history with a promotion log; the rollback target | No recovery from a bad promotion; no reproducibility |

K3 and K7 are commonly one database; they are listed separately because K3 is written on every episode and K7 only on promotion, and the safety proofs depend on K7 being append-only under a separate write path.

### 2.3 Protected and open regions of κ

The code state κ is partitioned at install time into a **protected region** κ\_P (K6 Gate, the Inv checker, K7 Store's write path, the hook signatures) and an **open region** κ\_O (everything else: routing features, compaction heuristics, verification prompts, budget schedules, tool wrappers). K5 may propose edits only to κ\_O. This is not a convention; it is enforced by K6 rejecting any proposal whose diff touches κ\_P, and K6 is in κ\_P. The circularity is deliberate and is what Theorem 7.3 relies on.

### 2.4 The parameter vector θ

θ collects every continuous or relaxable choice the interposer makes. The minimal set:

| Parameter | Controls | Range |
| --- | --- | --- |
| θ\_route | Router's logits over oracles, per feature bucket | R^(m × b) |
| θ\_budget | Compute budget per step as a function of estimated difficulty | R^+ |
| θ\_verify | Probability of requesting isolated verification before an action, by side-effect class | \[0, 1\]^3 |
| θ\_compact | Context-length threshold at which compaction is requested | N |
| θ\_spawn | Threshold on subtask separability score above which a subagent is requested | \[0, 1\] |
| θ\_halt | Confidence threshold for accepting a halt | \[0, 1\] |
| θ\_gate | Irreversible-action confidence bound δ (lower-bounded by Inv, never raised past it) | \[0, δ\_max\] |

Discrete choices are made stochastically through a policy π\_θ with a differentiable log-density, which is what makes the gradient in Section 8 well-defined without any assumption on the oracles.

## 3. Invariants

Inv is the conjunction of seven predicates on a candidate state (θ′, κ′); a proposal that fails any one is rejected before it runs on a single live task. Each is decidable by construction, which is the point: Section 6 shows the properties we actually want (correctness, optimality) are undecidable, so Inv is deliberately a set of *conservative decidable proxies* for them.

| Invariant | Statement | How K6 decides it |
| --- | --- | --- |
| I1 Protected region | diff(κ, κ′) ∩ κ\_P = ∅ | AST diff against the install-time partition; syntactic |
| I2 Interface fidelity | κ′ exports exactly the four hooks of Section 2.1 with unchanged signatures | Type check against the frozen interface |
| I3 Side-effect monotonicity | Every irreversible tool call in κ′ passes through the gate with δ′ ≤ δ\_max; κ′ introduces no new path to an irreversible tool that bypasses it | Static taint analysis from tool-registry irreversible entries to call sites; the gate call must dominate every such call site in the control-flow graph |
| I4 Budget boundedness | Every loop in κ′ that can issue a model or tool call carries a budget counter from θ\_budget, and the counter is decremented on every iteration | Static: each cycle in the call graph containing a model/tool call must contain a decrement of a budget variable |
| I5 Ledger completeness | Every hook invocation in κ′ writes to K3 before returning | Static: each hook's return sites are dominated by a ledger write |
| I6 Termination of the kernel's own code | κ′'s non-hook functions are structurally recursive or carry a ranking function declared in the proposal | Termination checker on the kernel code only (not on the host, not on tasks) |
| I7 Determinism of replay | Given the same ledger record, κ′ reproduces the same decision sequence | Replay the last N ledger episodes through κ′ in shadow mode; decisions must match the recorded ones bit-for-bit where κ′ did not intend a change, and differ only at sites named in the proposal |

Three remarks on what Inv is and is not.

**Inv is sound, not complete.** A rejected proposal may be perfectly safe; an accepted one is guaranteed to satisfy I1–I7 and nothing else. Theorem 7.1 establishes that satisfying I1–I7 is sufficient for the safety properties we care about (no irreversible action without gate approval, bounded resource use, full auditability, recoverability). It does not establish that the accepted code is *good*; that is the job of the shadow and canary stages in Section 4.

**Inv is operator-owned.** The operator may tighten any predicate (lower δ\_max, add a tool to the irreversible class, require a longer replay window) at any time. The operator may not loosen I1 or I2 without reinstalling the kernel, because those two are what make the other five enforceable.

**Inv is cheap.** I1, I2, I5 are linear in the size of κ′. I3, I4 are dominance queries on the control-flow graph, near-linear. I6 is the only one with superlinear worst case, and it runs on kernel code only, which is small by design. I7 costs N replays, which is why N is an operator parameter (default 64) and why K3 stores enough to replay.

## 4. Safe self-modification protocol

A code change reaches live traffic only after six stages, each of which can only reject or pass; nothing in the pipeline can skip a stage, because the stage order is in κ\_P.

&#91;embedded content: self-modification protocol · 6 stages, 3 exits, 1 loop\]

A proposal that fails the invariant check, fails to dominate in shadow, or regresses in canary exits to the right; every exit and every promotion is appended to the rewriter's conditioning, so the loop never repeats a rejected change blindly.

### 4.1 Stages

1. **Propose.** K5 samples failure traces from K3 (episodes with low R or high C relative to the running frontier), gives them to an oracle with the current κ\_O and the instruction to produce a diff plus a *claim*: which decision sites it intends to change, and a predicted direction for each objective. The claim is data for stage 5, not a promise.
2. **Check Inv.** K6 evaluates I1–I7 on (θ, κ′). Any failure ends the proposal; the failure code is logged so K5's next proposal is conditioned on it.
3. **Shadow.** κ′ runs alongside κ on the next S live episodes, receiving the same hooks and recording what it *would* have done; its overrides are not applied. This yields paired samples (decision\_κ, decision\_κ′) at every decision point and an unbiased off-policy estimate of F(θ, κ′) via importance weighting on the sites where the two differ (Proposition 7.4 gives the condition under which the estimate is valid).
4. **Canary.** If the shadow estimate does not Pareto-dominate the current state on the operator's scalarization λ by at least margin η, reject. Otherwise κ′ is applied to a fraction f of live episodes (default 5%), with θ\_gate pinned to its tightest value for the duration, so a canary cannot raise risk even in principle.
5. **Promote or roll back.** After the canary window, a one-sided test at level α on each objective: promote if every objective is within η of the shadow estimate and none has regressed by more than ε\_reg; else roll back to κ. K7 records the decision, both estimates, and the ledger range. Rollback is a pointer move in K7; the old κ was never deleted.
6. **Reflect.** The promotion or rejection, with its evidence, is appended to K5's conditioning set, so proposals improve over time. This is the only learning K5 does; it does not fine-tune anything.

### 4.2 Why this order

Inv before shadow: static checks are cheap and catch the class of changes that shadow mode cannot (a bypass of the gate behaves identically in shadow mode, since shadow applies no overrides).

Shadow before canary: shadow costs nothing in live outcome; canary costs f × (regression) if κ′ is bad. Shadow filters so that canaries are rarely bad, and Theorem 7.2 bounds the cost of the ones that are.

Canary with θ\_gate pinned: this is what lets Theorem 7.1 hold *during* evaluation and not only after promotion. The canary can be worse on tokens, wall-clock and dollars; it cannot be worse on risk.

### 4.3 Two things the protocol refuses to do

It never promotes on shadow evidence alone. Shadow is off-policy; it observes what κ′ would do at κ's decision points, but κ′'s own trajectory would reach different decision points. The canary is the only on-policy evidence.

It never lets K5 propose a change to the protocol itself, to Inv, or to K7's write path. Theorem 7.3 is the statement that this restriction is self-enforcing: the kernel can improve everything about how it acts, and nothing about how it is judged.

## 5. Model routing

Routing between frozen oracles is a contextual bandit whose arms are the oracles and whose reward is a scalarized (quality, cost) signal; the kernel treats it this way because the oracles cannot be improved, only selected.

**Setup.** At each model call the router observes a feature vector φ ∈ R^b (task type, step index, current context length, side-effect class of the pending action, difficulty estimate from the last verification) and picks an arm j ∈ {1, …, m}. It receives, after the step, a per-call reward r\_j(φ) = u(outcome) − λ\_cost · price\_j(tokens), where u is a cheap local quality signal (verifier pass, tool success, no retry) and λ\_cost is the operator's cost weight. The terminal R is credited back to the calls on the trajectory by the optimizer, not the router; the router runs on local signal so that it can learn within an episode.

**Policy.** π\_route(j | φ) = softmax over j of θ\_route\[j\]·φ, with an exploration floor: every arm has probability at least γ / m. This is what makes the gradient estimator in Section 8 unbiased and what gives Proposition 8.4 its rate.

**Two constraints the router inherits from Inv.** It cannot route an irreversible-class decision to an oracle whose empirical error on that class, as estimated from K3, exceeds δ\_max; this is the one place the bandit is clipped. And a newly added oracle enters at exploration-floor probability only, until K3 holds at least n\_min observations for it.

**What routing buys.** When the oracles differ in cost by a factor of c and the cheaper one has error ε\_cheap ≤ ε\_dear + Δ on some feature bucket, routing that bucket to the cheaper oracle improves C\_$ by a factor approaching c on that bucket and worsens J by at most Δ × (share of the bucket). Whether that is a Pareto improvement depends on λ; the router does not decide λ, it finds the frontier for whatever λ it is handed. With isolated verification (θ\_verify > 0), the effective error of the cheap oracle on verified steps is ε\_cheap × ε\_verifier, which is usually below ε\_dear unverified. This is the mechanism by which a kernel over cheap models plus a verifier dominates an expensive model alone on the cost axes while matching it on quality, and it is a dominance claim with an empirical premise, not a theorem.

**Adding and removing oracles.** Oracles are registered in K7 with their price vector and a feature-independent prior error. Removing one (deprecation) sets its probability to zero and renormalizes; the regret bound in Section 8 absorbs this as a bounded drift event. Adding one is a change to θ\_route's dimension, handled by zero-padding and the exploration floor; it is not a code change and never goes through K5.

## 6. Theorems I: impossibility and necessity

No fixed harness is optimal, and four independent obstructions prevent one from ever being; what survives is a family of dominance results that fix the *shape* of any non-dominated kernel.

### Theorem 6.1 (No distribution-free optimal harness)

*Statement.* Let H\_1, H\_2 be two kernel-compatible harnesses that differ in at least one decision rule. Then there exist task distributions D\_1, D\_2 such that H\_1 Pareto-dominates H\_2 on D\_1 and H\_2 Pareto-dominates H\_1 on D\_2. In particular no harness is Pareto-optimal for all D.

*Proof.* Since H\_1 and H\_2 differ, there is a history h at which they take different actions a\_1 ≠ a\_2. Construct a task x\_1 whose reward is 1 if the action at h is a\_1 and 0 otherwise, with all costs equal; construct x\_2 symmetrically. Let D\_i be the point mass on x\_i. Then J(H\_1) = 1 > 0 = J(H\_2) on D\_1 with equal costs, so H\_1 dominates; and symmetrically on D\_2. The construction is legitimate because the definition of a task puts no restriction on how R depends on the trajectory. □

*Remark.* This is the No Free Lunch theorem specialized to harnesses. It is why the kernel is parameterized by D through the ledger rather than fixed at design time.

### Proposition 6.2 (Halting is undecidable; verification is heuristic)

*Statement.* There is no algorithm that, given a task specification s in a Turing-complete language and a candidate output o, decides whether o satisfies s. Consequently the halt decision in any harness, and the verifier in K6, are necessarily sound-or-complete but not both.

*Reduction.* Take s to be "o is a program that halts on all inputs." Deciding satisfaction decides the universal halting problem, which is Π\_2-complete. Even restricting to s of the form "o is a program with property P" for any non-trivial semantic P, Rice's theorem gives undecidability. □

*Consequence for the spec.* I6 (termination of the kernel's own code) is decidable only because it is restricted to structurally recursive functions or ones with a declared ranking function; this is a syntactic subclass, not the general case. The verifier reduces error but cannot eliminate it, which is why ε\_min > 0 is a standing assumption.

### Proposition 6.3 (Optimal control is intractable even with full knowledge)

*Statement.* Even if D and every ε\_j(c, p) were known exactly, computing the optimal decision rule for a finite-horizon episode is PSPACE-hard in the size of the task description.

*Reduction.* The agent-plus-environment system is a POMDP: the hidden state is the environment's true state (unknown to the agent) and the oracle's internal correctness on each call. Papadimitriou and Tsitsiklis (1987) show that finite-horizon POMDP policy optimization is PSPACE-complete; the harness problem contains this as the special case with one oracle and deterministic tools. □

*Consequence.* K4 does not compute the optimal θ; it does gradient ascent toward a stationary point. Section 8 characterizes what it reaches.

### Proposition 6.4 (Regret lower bound for learning an unknown oracle)

*Statement.* Any kernel that routes between m ≥ 2 oracles with unknown error kernels incurs expected scalarized regret Ω(√(mT)) over T calls, for some configuration of the error kernels.

*Reduction.* Fix φ; the router is then a stochastic bandit with m arms and bounded rewards. The Auer–Cesa-Bianchi–Freund–Schapire minimax lower bound gives Ω(√(mT)) for any algorithm. A kernel that achieved o(√T) would contradict it. □

*Consequence.* The best a kernel can be is *rate-optimal*: regret Θ(√T). It cannot be optimal from the first episode, and "self-optimizing" means approaching the frontier, not being on it.

### Theorem 6.5 (Linear scalarization misses non-convex frontier points)

*Statement.* If the Pareto frontier P of F over Θ is not convex (as a set in objective space), there exist points of P that are not the maximizer of λ·F for any λ in the simplex.

*Proof.* Let p ∈ P lie on a concave segment of the frontier: there exist p\_1, p\_2 ∈ P and t ∈ (0, 1) with t p\_1 + (1 − t) p\_2 ≥ p componentwise, strictly in some coordinate. For any λ ≥ 0, λ·(t p\_1 + (1 − t) p\_2) ≥ λ·p, so max(λ·p\_1, λ·p\_2) ≥ λ·p, with strict inequality unless λ is orthogonal to the difference, in which case p is not the unique maximizer either. So no λ selects p. □

*Consequence.* K4 uses the multiple-gradient direction (Désidéri) or a Chebyshev scalarization, never a fixed linear λ, because the harness frontier has threshold-induced non-convexities (verification on/off, spawn/no-spawn).

### Design claim 6.6 (Dominance of context-isolated verification)

*Statement.* Let an action have probability of error ε, loss on error L, and let an isolated verifier cost c\_v and have its own error ε\_v with correlation ρ to the producer. Adding verification is a Pareto improvement whenever

```latex
c_v \;<\; \varepsilon\,L\,\big(1 - \varepsilon_v\big)\big(1 - \rho\big)
```

*Argument.* Without verification, expected loss is εL. With it, an error is caught with probability (1 − ε\_v)(1 − ρ), so expected loss is εL(1 − (1 − ε\_v)(1 − ρ)) + c\_v. The second is smaller than the first exactly when the inequality holds. Risk and reward improve; tokens and dollars worsen by c\_v; whether that is dominance depends on whether c\_v is counted in C or in L. For irreversible actions L is large by definition and the inequality holds for any reasonable c\_v. □

*Why isolation.* For a same-context self-check ρ → 1 and the right-hand side → 0; verification is then never worth its cost. This is why K6's verifier is given the spec and the output but not the producer's trace.

### Design claim 6.7 (Dominance of prefix caching and of the exploration floor)

*Prefix caching.* If an oracle's cached-prefix price per token is strictly below its uncached price and output distribution is unchanged (which the oracle providers guarantee), then placing all immutable content before mutable content weakly improves C\_$ and C\_wall and changes nothing else. This is the only unconditional Pareto improvement in the document.

*Exploration floor.* A router with zero probability on some arm cannot distinguish "this arm is bad" from "this arm was never tried," so its regret against a distribution where that arm is best is linear in T. Any sublinear-regret router therefore has full support. □

## 7. Theorems II: safety of self-modification

The kernel can change any amount of its own open code and still never take an ungated irreversible action, never exceed its budget, never lose auditability, and never lose the ability to roll back; the proofs are inductions on the promotion sequence with Inv as the inductive hypothesis.

### Theorem 7.1 (Invariant preservation along every promotion chain)

*Statement.* Let κ\_0 be the installed kernel with Inv(θ\_0, κ\_0) = 1, and let κ\_0, κ\_1, …, κ\_n be any sequence of states where each κ\_{i+1} was promoted from κ\_i by the Section 4 protocol. Then Inv(θ\_i, κ\_i) = 1 for all i, and on every live episode run under any κ\_i (including canary episodes), (a) every irreversible tool call was preceded by a gate check at confidence at least 1 − δ\_max; (b) the total number of model and tool calls was at most the budget declared by θ\_budget; (c) every decision was written to K3; (d) κ\_{i−1} is recoverable from K7.

*Proof.* By induction on i. Base case: Inv(θ\_0, κ\_0) = 1 by installation. Inductive step: κ\_{i+1} is promoted only if stage 2 of the protocol returned Inv(θ\_i, κ\_{i+1}) = 1, and stage 2 is in κ\_P, which by I1 applied to κ\_{i+1} is unchanged from κ\_0. So the check run was the original check, and Inv(θ\_i, κ\_{i+1}) = 1. Properties (a)–(d) now follow from the individual invariants on κ\_{i+1}: (a) from I3, since every irreversible call site is dominated by the gate in the control-flow graph, and the gate's threshold is θ\_gate ≤ δ\_max by I3's bound; (b) from I4, since every cycle containing a call decrements a budget counter, so the number of calls is at most the initial counter value; (c) from I5; (d) from the fact that K7's write path is in κ\_P and is append-only, so κ\_i was written on promotion and never overwritten. For canary episodes, κ\_{i+1} runs with Inv already verified and θ\_gate pinned to its minimum, so (a)–(d) hold with (a) strengthened. □

*What the theorem does not say.* It does not say κ\_i is better than κ\_{i−1}, or correct, or that the gate's confidence estimate is calibrated. Calibration is an empirical property of the oracle used for confidence; the theorem guarantees the gate is *consulted*, not that it is *right*. The operator sets δ\_max knowing this.

### Theorem 7.2 (Bounded regression per promotion)

*Statement.* Let the canary run on a fraction f of N episodes with a one-sided test at level α and regression tolerance ε\_reg on each of the k+1 objectives. Then for each objective i, the expected regression of the promoted state relative to the prior state satisfies E\[F\_i(κ\_i) − F\_i(κ\_{i+1})\] ≤ ε\_reg + σ\_i √(2 log(1/α) / (fN)) with probability at least 1 − (k+1)α, where σ\_i is the per-episode standard deviation of objective i. The total cost of a canary that is ultimately rolled back is at most fN × max\_i regression\_i.

*Proof.* The canary's estimate of F\_i(κ\_{i+1}) is a mean of fN i.i.d. bounded samples (independence across episodes follows from the i.i.d. task draw; boundedness from R ∈ \[0, 1\] and the budget bound of Theorem 7.1(b), which bounds every cost coordinate). Hoeffding's inequality gives P(|estimate − F\_i| > t) ≤ 2 exp(−2fN t² / range²); setting the right side to α and solving gives the deviation term. Promotion requires the estimate to be within ε\_reg of the prior, so the true regression is at most ε\_reg plus the deviation, simultaneously for all k+1 objectives by a union bound. The rolled-back cost bound is immediate: the canary ran on fN episodes and each regressed by at most the maximum. □

*Consequence.* The operator controls the safety-speed tradeoff through (f, N, α, ε\_reg). Smaller f means cheaper bad canaries and slower promotion; the theorem makes the price of each setting explicit.

### Theorem 7.3 (No reachable state disables the gate)

*Statement.* There is no sequence of proposals, accepted or rejected, after which the Section 4 protocol runs with a modified stage 2, a modified Inv, a modified κ\_P partition, or a modified K7 write path.

*Proof.* Suppose for contradiction that some κ\_n in a promotion chain has a modified κ\_P. Let κ\_j be the first such state. Then diff(κ\_{j−1}, κ\_j) ∩ κ\_P ≠ ∅, so I1 fails on κ\_j. But κ\_j was promoted, so stage 2 returned Inv = 1 on it. Stage 2 is the Inv checker of κ\_{j−1}, which by minimality of j has unmodified κ\_P, so it is the installed checker, which correctly evaluates I1 and returns 0. Contradiction. Rejected proposals never run and so cannot modify anything. □

*Remark on self-reference.* The argument is not circular in the vicious sense: it relies on the checker being correct *once*, at installation, and shows that this correctness is inherited. The one way to defeat it is to modify κ\_P from outside the protocol (a human editing files on disk), which is the operator's prerogative and is exactly the reinstallation path mentioned in Section 3.

### Proposition 7.4 (Validity of the shadow estimate)

*Statement.* The shadow stage's importance-weighted estimate of F(θ, κ′) is unbiased if (a) κ′ differs from κ only at decision sites where κ's policy π\_θ has full support (every action κ′ might take has positive probability under κ), and (b) the trajectories of κ and κ′ coincide up to the first differing decision. Under (a) and (b) the estimator variance is bounded by range² × max importance weight, which is at most 1/γ\_min where γ\_min is the exploration floor.

*Proof.* This is the standard inverse-propensity argument. Condition (b) makes the shadow's observation at each decision point an observation at a state κ′ would actually have reached, so it is on the right distribution of states up to that point. Condition (a) makes the importance weight π\_{κ′}(a | h) / π\_κ(a | h) finite. Unbiasedness follows from E\_κ\[w(a) g(a)\] = E\_{κ′}\[g(a)\] for any g. The variance bound follows from the weights being at most 1/γ\_min, which the exploration floor of Section 5 guarantees for routing decisions and θ\_verify > 0 guarantees for verification decisions. □

*Why this is a proposition and not a theorem.* Condition (b) fails after the first divergent decision: κ′ would have reached a different state, which the shadow cannot observe. The estimate is therefore unbiased only for the *first* changed decision per episode and is a heuristic for later ones. This is the formal reason the protocol requires a canary and does not promote on shadow evidence alone.

### Theorem 7.5 (Liveness: the protocol cannot deadlock on a good proposal)

*Statement.* If a proposal κ′ satisfies Inv and strictly Pareto-dominates κ by margin at least 2η on every objective, then it is promoted with probability at least 1 − (k+1)α − (k+1) exp(−2 fN η² / range²).

*Proof.* Stage 2 passes by hypothesis. Stage 4 requires the shadow estimate to dominate by η; by Hoeffding on S shadow samples, the estimate is within η of the truth (which dominates by 2η) with probability at least 1 − (k+1) exp(−2Sη²/range²), so the canary is reached. Stage 5 requires every objective within η of the shadow estimate and no regression beyond ε\_reg; since the truth dominates, there is no regression, and the canary estimate is within η of the truth with the stated Hoeffding probability. Union bound over objectives and stages gives the result (the S-sample term is absorbed into the stated bound for S ≥ fN). □

*Consequence.* The protocol is not a ratchet that can only reject. A sufficiently good proposal gets through with high probability, and the probability is a computable function of the operator's settings.

## 8. Theorems III: convergence to the Pareto frontier

Under six named conditions, the optimizer converges to a Pareto-stationary point at rate O(1/√T) and the routed kernel achieves scalarized regret O(√T), matching the lower bound of Proposition 6.4; convexity upgrades stationary to optimal, and its absence is the single reason the guarantee is local.

### Conditions

| Code | Condition | Why it is needed | What fails without it |
| --- | --- | --- | --- |
| A1 Stationarity | D and every ε\_j are fixed, or drift with total variation Δ\_T = o(T) | The frontier is a fixed target | An irreducible tracking term Ω(Δ\_T) is added to regret |
| A2 Smoothness | Each F\_i(·, κ) is L-smooth in θ, i.e. ∇\_θ F\_i is L-Lipschitz | Standard for descent-method convergence | Step-size analysis breaks; no rate |
| A3 Unbiased gradients | For each i there is an estimator g\_i with E\[g\_i\] = ∇\_θ F\_i and E‖g\_i − ∇F\_i‖² ≤ σ² | Stochastic approximation requires it | Bias accumulates; convergence to the wrong point |
| A4 Exploration | π\_θ has full support with floor γ\_min > 0 on every discrete choice | Makes A3 achievable via the policy gradient theorem; bounds importance weights | Gradient variance unbounded; some frontier points unreachable |
| A5 Bounded proxy gap | The reward actually observed, R̂, satisfies ‖E\[R̂\] − E\[R\]‖ ≤ ζ uniformly over Θ | Optimization sees R̂, not R | Convergence to the frontier of the wrong objective (Goodhart) |
| A6 Convexity (optional) | Each F\_i is concave in θ | Turns Pareto-stationary into Pareto-optimal | Only local Pareto-stationarity is guaranteed |

### Lemma 8.1 (A3 and A4 hold for the kernel's policy)

*Statement.* Under A4, the REINFORCE estimator on ledger samples, g\_i = Σ\_t ∇\_θ log π\_θ(a\_t | h\_t) · (F\_i-sample − b), is unbiased for ∇\_θ F\_i with variance at most (range² · T\_max²) / γ\_min, where T\_max is the budget bound of Theorem 7.1(b), for any baseline b independent of the actions.

*Proof.* This is the policy gradient theorem (Sutton et al. 1999) applied to the kernel's decision policy. The oracles and tools enter only through the trajectory distribution, which π\_θ influences through its actions; no gradient through the oracle is needed, which is why their non-differentiability is irrelevant. Unbiasedness: E\[∇ log π(a|h) · b\] = b · ∇ Σ\_a π(a|h) = b · ∇1 = 0. Variance: each of at most T\_max terms has ‖∇ log π‖ ≤ 1/γ\_min by the exploration floor and a bounded return factor, so the squared norm is at most (T\_max · range / γ\_min)². □

### Theorem 8.2 (Convergence of stochastic multi-gradient descent)

*Statement.* Assume A1 (exact stationarity), A2, A3, A4. Let K4 at each step compute the Désidéri direction d\_t = argmin over d in the convex hull of {g\_1, …, g\_{k+1}} of ‖d‖, and update θ\_{t+1} = θ\_t + η\_t d\_t with η\_t = c/√t. Then

```latex
\min_{t \le T}\ \mathbb{E}\big[\mathrm{dist}\big(0,\ \mathrm{conv}\{\nabla_\theta F_i(\theta_t)\}\big)^2\big] \;\le\; \frac{C_0 + C_1 \sigma^2 \log T}{\sqrt{T}}
```

for constants C\_0, C\_1 depending on L, c, and the initial suboptimality. In particular θ\_t converges in expectation to a Pareto-stationary point.

*Proof.* The minimum-norm element of the convex hull of the gradients is, by the KKT conditions of that quadratic program, a direction d with d·∇F\_i ≥ ‖d‖² for every i (Désidéri 2012, Lemma 2.1): it is a common ascent direction, and ‖d‖ = 0 exactly at Pareto-stationary points. Apply L-smoothness to each F\_i along the step: F\_i(θ\_{t+1}) ≥ F\_i(θ\_t) + η\_t ∇F\_i·d̂\_t − (L/2) η\_t² ‖d̂\_t‖², where d̂\_t is the stochastic direction. Taking expectations and using A3 to replace the stochastic direction by its mean plus a variance term σ², then summing over t with η\_t = c/√t, gives Σ\_t η\_t E‖d\_t‖² ≤ C\_0 + C\_1 σ² Σ η\_t², and Σ\_{t ≤ T} η\_t² = O(log T) while Σ\_{t ≤ T} η\_t = Ω(√T). Dividing yields the stated bound on the minimum. This is the Ghadimi–Lan (2013) nonconvex SGD rate applied to the scalar surrogate ψ(θ) = min\_i F\_i(θ) − F\_i(θ\_0), whose stationary points are exactly the Pareto-stationary points of F (the surrogate is used only in the analysis; K4 never computes it). □

### Corollary 8.3 (Convexity gives Pareto optimality)

*Statement.* Under A6 in addition, every Pareto-stationary point is Pareto-optimal, so θ\_t converges in expectation to a point of P.

*Proof.* Suppose θ is Pareto-stationary but dominated by θ′. By concavity of each F\_i, F\_i(θ′) ≤ F\_i(θ) + ∇F\_i(θ)·(θ′ − θ), so dominance gives ∇F\_i(θ)·(θ′ − θ) ≥ 0 for all i with strict inequality for some i. Then v = θ′ − θ is a common weak-ascent direction with strict ascent for some objective, contradicting the stationarity condition that 0 is in the convex hull of the gradients (if 0 = Σ μ\_i ∇F\_i with μ in the simplex, then 0 = Σ μ\_i ∇F\_i·v > 0 for any such v with μ\_i > 0 on the strict coordinate; if μ\_i = 0 there, perturb μ). □

*Caveat.* A6 fails for the actual kernel: θ\_verify and θ\_spawn induce threshold effects, and routing logits make J non-concave. The practical remedy is restarts over θ combined with hypervolume ascent (Emmerich–Deutz), which recovers frontier coverage in the limit but with no finite-time rate. This is the gap between what the theorems give and what one would want, and it is a real gap.

### Proposition 8.4 (Regret of the router)

*Statement.* Under A1 and A4, with θ\_route updated by the Section 8.2 scheme restricted to the routing coordinates, or equivalently by EXP4 over the m arms with features φ, the router's scalarized regret against the best fixed routing policy in the class satisfies E\[Reg\_T\] = O(√(mT log m)) per feature bucket. This matches Proposition 6.4 up to the logarithmic factor; the router is rate-optimal.

*Reduction.* With φ fixed, the router is an adversarial m-armed bandit with bounded reward r\_j(φ) and the exploration floor gives the mixing needed for EXP3-type analysis (Auer, Cesa-Bianchi, Freund, Schapire 2002). The per-bucket bound sums over b buckets to O(b √(mT log m)). □

### Theorem 8.5 (Composite guarantee for the whole kernel)

*Statement.* Under A1–A5, the kernel (θ\_t, κ\_t) produced by running K4 at every episode and the Section 4 protocol whenever K5 proposes satisfies, for every scalarization λ in the simplex,

```latex
\mathrm{Reg}_T(\lambda) \;\le\; O\!\big(\sqrt{T}\,\log T\big) \;+\; \zeta\,T \;+\; n_{\mathrm{prom}}\,\big(\varepsilon_{\mathrm{reg}} + \text{deviation}\big)\,T_{\mathrm{canary}} \;+\; \Omega(\Delta_T)
```

where n\_prom is the number of promotions, T\_canary the canary window, and the comparator is the best state in the local frontier reachable from κ\_0 under A6-free descent.

*Proof.* The first term is Theorem 8.2 scalarized by λ, using that λ·F is L-smooth with the same constant and that the Désidéri direction is an ascent direction for every λ·F simultaneously. The second term is A5: the optimizer converges to within ζ of the true frontier because it optimizes R̂, and the gap accumulates linearly. The third is Theorem 7.2 summed over promotions: each promotion can regress by at most ε\_reg plus deviation, for at most T\_canary episodes before rollback or confirmation. The fourth is the drift term when A1 holds only approximately. Each term is additive because the four effects are independent sources of suboptimality acting on disjoint episodes or disjoint coordinates. □

*Reading the bound.* The √T term is the unavoidable price of learning (Proposition 6.4). The ζT term says that a proxy reward puts a floor on achievable regret that no amount of learning removes, which is the formal content of Goodhart's law and the reason the isolated verifier belongs in the kernel rather than being optional: it is the mechanism that keeps ζ small by periodically re-anchoring R̂ to an independent judgment. The promotion term is linear in the number of code changes, so the kernel is incentivized to make few, large, well-evidenced changes rather than many small ones, which is the intended behavior.

### Theorem 8.6 (Differentiable approach is the strongest achievable notion)

*Statement.* No kernel satisfying the Section 1 setting can achieve Reg\_T(λ) = o(√T) for all D and all oracle families; and under A1–A5, the Section 8 kernel achieves O(√T log T). Therefore "asymptotically Pareto-efficient at the minimax rate" is the strongest optimality property any such kernel can have, and this kernel has it.

*Proof.* The first clause is Proposition 6.4. The second is Theorem 8.5 with ζ = 0 and Δ\_T = 0 and finitely many promotions. □

*What this means for the question that motivated the document.* Provable optimality in the sense of "this harness is on the frontier" is impossible (Section 6). Provable optimality in the sense of "this harness approaches the frontier as fast as any harness could" is possible, and the conditions under which it holds are A1–A5, each of which is a checkable operational requirement rather than an article of faith.

## 9. Minimal reference implementation

The whole kernel is six data types and five functions, with the protected region fitting in a few hundred lines; everything else lives in κ\_O and is the kernel's to rewrite.

### 9.1 Data types

```python
# --- protected region κ_P: frozen at install, never touched by K5 ---

@dataclass(frozen=True)
class DecisionPoint:
    kind: Literal["model", "tool", "compact", "spawn", "halt"]
    features: np.ndarray          # φ, built by κ_O's featurizer
    side_effect: Literal["idempotent", "reversible", "irreversible"]
    loss_scale: float             # L, from the tool registry; 0 unless irreversible
    default: Any                  # what the host would do if the kernel passes

@dataclass(frozen=True)
class Override:
    oracle: int | None = None
    budget: int | None = None
    verify: bool = False
    block: bool = False           # only the gate may set this

@dataclass(frozen=True)
class LedgerRecord:               # K3 row; append-only
    episode: int
    step: int
    dp: DecisionPoint
    action: Any
    log_prob: float               # log π_θ(action | dp), for the gradient
    override: Override
    obs: Any
    theta_hash: str
    kappa_hash: str

@dataclass(frozen=True)
class EpisodeEnd:
    episode: int
    reward: float                 # R̂ ∈ [0, 1]
    cost: np.ndarray              # (tok, wall, usd, risk)

@dataclass(frozen=True)
class Proposal:                   # K5 output
    diff: AstDiff                 # restricted to κ_O by I1
    claim_sites: set[str]         # decision sites the change intends to alter
    predicted_direction: np.ndarray   # sign vector over objectives

@dataclass(frozen=True)
class StoreEntry:                 # K7 row; append-only
    version: int
    theta: np.ndarray
    kappa_hash: str
    parent: int | None
    promoted_at: int              # episode
    evidence: dict                # shadow + canary estimates, test stats
```

### 9.2 The five functions in κ\_P

```python
def check_inv(theta, kappa_prime, kappa_P, registry, ledger, N=64) -> tuple[bool, str]:
    # I1..I7 in order; return (False, code) on first failure
    if ast_diff(kappa_current, kappa_prime) & kappa_P:           return False, "I1"
    if not conforms(kappa_prime, HOOK_INTERFACE):                 return False, "I2"
    cfg = control_flow_graph(kappa_prime)
    for site in irreversible_call_sites(cfg, registry):
        if not dominates(cfg, GATE_CALL, site):                   return False, "I3"
    for cycle in cycles_with_calls(cfg):
        if not contains_budget_decrement(cycle):                  return False, "I4"
    for hook in HOOKS:
        if not every_return_dominated_by(cfg, hook, LEDGER_WRITE): return False, "I5"
    if not terminates(kappa_prime.non_hook_functions):           return False, "I6"
    if not replay_matches(kappa_prime, ledger.last(N), claim_sites): return False, "I7"
    return True, "ok"

def gate(dp: DecisionPoint, confidence: float, theta_gate: float, delta_max: float) -> bool:
    # the ONLY path to an irreversible action; I3 guarantees every call site routes here
    if dp.side_effect != "irreversible": return True
    return confidence >= 1.0 - min(theta_gate, delta_max)

def isolated_verify(spec, output, oracle) -> tuple[bool, float]:
    # fresh context: spec + output only; no producer trace, no shared history (ρ → 0)
    return oracle.judge(context=[spec, output])

def promote_or_rollback(store, candidate, shadow_est, canary_est, prior_est,
                        eta, eps_reg, alpha) -> StoreEntry:
    for i in range(len(prior_est)):
        if canary_est[i] < shadow_est[i] - eta:            return store.head()   # reject
        if canary_est[i] < prior_est[i] - eps_reg:          return store.head()   # regressed
        if not one_sided_test(canary_samples[i], prior_est[i], alpha): return store.head()
    return store.append(candidate, evidence={...})            # append-only; old head stays

def store_write(store, entry):
    # append-only; the only writer; separate storage path from the ledger
    assert entry.version == store.head().version + 1
    store.rows.append(entry)
```

### 9.3 The learning loop in κ\_O

```python
# --- open region κ_O: K5 may rewrite any of this, subject to Inv ---

def before_decision(dp):
    budget.decrement()                                    # I4
    phi = featurize(dp)                                   # rewritable
    action, logp = policy.sample(theta, dp.kind, phi)     # π_θ with floor γ_min
    ov = Override(oracle=action.oracle, budget=action.budget, verify=action.verify)
    if dp.side_effect == "irreversible":
        ok, conf = isolated_verify(dp.spec, dp.default, oracles[action.oracle])
        ov = replace(ov, block=not gate(dp, conf, theta.gate, DELTA_MAX))
    ledger.append(LedgerRecord(..., log_prob=logp, override=ov))   # I5
    return ov

def end_episode(R, C):
    ledger.append(EpisodeEnd(...))
    if ledger.since_last_update() >= BATCH:
        grads = [reinforce(ledger.batch(), objective=i) for i in range(1 + len(C))]
        d = min_norm_in_convex_hull(grads)                # Désidéri direction
        theta = project(theta + step(t) * d)              # θ_gate clipped to [0, DELTA_MAX]
    if ledger.since_last_proposal() >= PROPOSE_EVERY:
        schedule(propose_and_evaluate)

def propose_and_evaluate():
    traces = ledger.worst_k(K, scalarization=LAMBDA)
    prop = rewriter_oracle.propose(kappa_O, traces, history=store.proposal_log())
    ok, code = check_inv(theta, apply(kappa, prop.diff), KAPPA_P, registry, ledger)
    if not ok: return store.log_rejection(prop, code)
    shadow_est = shadow_run(prop, episodes=S)             # Prop. 7.4: valid at first changed site
    if not dominates_by(shadow_est, current_est, LAMBDA, ETA): return store.log_rejection(prop, "shadow")
    canary_est, samples = canary_run(prop, fraction=F, episodes=N, gate_pinned=True)
    promote_or_rollback(store, prop, shadow_est, canary_est, current_est, ETA, EPS_REG, ALPHA)
```

### 9.4 Integration with an existing harness

| Host pattern | Where the decision points already are | Shim |
| --- | --- | --- |
| Harness with a pluggable model client (most SDK-based agents) | The client's `complete()` or `messages.create()` call | Wrap the client; call `before_decision(kind="model")` before, `after_outcome` after; route by swapping the underlying client per the override |
| Harness with a tool executor or registry | The executor's `run(tool, args)` | Wrap the executor; tag tools from the registry declaration; `before_decision(kind="tool")` with the side-effect class; honor `block` |
| Harness with explicit compaction or summarization | The function that rewrites the context | Wrap it; `before_decision(kind="compact")`; the override may raise or lower the threshold |
| Harness with subagent spawning | The spawn call | Wrap it; `before_decision(kind="spawn")` |
| Harness with a final-answer or done signal | The return from the main loop | `before_decision(kind="halt")`; the override may request verification first |
| Harness exposing none of the above (a monolithic loop) | The HTTP layer to the model provider | Proxy the provider endpoint; only `model` decision points are available; tool and halt control are lost but routing and budget still work |

The last row is the degenerate case: even a harness with no hooks at all gets model routing and budget control through a provider proxy, which is enough for Proposition 8.4 to apply. Full guarantees (Theorems 7.1, 8.5) need the tool executor wrapped, since I3 is about tool calls.

### 9.5 Operator settings and their defaults

| Setting | Default | Governs |
| --- | --- | --- |
| δ\_max | 0.01 | Hard ceiling on irreversible-action error tolerance (Theorem 7.1a) |
| γ\_min | 0.05 / m | Exploration floor (Lemma 8.1, Prop. 7.4) |
| N (replay window) | 64 episodes | I7 cost and sensitivity |
| S (shadow episodes) | 200 | Shadow estimate precision |
| f, N\_canary | 0.05, 400 | Canary cost and precision (Theorem 7.2) |
| α, η, ε\_reg | 0.05, 0.02, 0.01 | Promotion test (Theorems 7.2, 7.5) |
| λ | operator's | Scalarization for ranking and for proposal selection; never used for the gradient direction (Theorem 6.5) |
| BATCH, PROPOSE\_EVERY | 32, 500 | How often θ updates; how often K5 is consulted |

## 10. Open problems and the limits of the guarantees

Five things the theorems do not deliver, each with what would be needed to close it.

**Global rather than local Pareto optimality.** Theorem 8.2 gives Pareto-stationarity; Corollary 8.3 needs concavity that the real kernel lacks. Restarts plus hypervolume ascent recover coverage asymptotically with no rate. A rate would need either a structural result about the harness frontier (for instance that its non-convexities are finitely many and of bounded depth) or a different optimizer class. Neither is known.

**Calibration of the gate.** Theorem 7.1 proves the gate is always consulted; it proves nothing about whether the confidence it is given is accurate. A miscalibrated verifier that reports 0.99 on every output defeats the gate while satisfying every invariant. The remedy is periodic calibration of the verifier oracle against ground truth, which is an operator process outside the kernel and the one dependency that cannot be made self-enforcing: a kernel that calibrated its own judge would be grading itself.

**The proxy gap ζ.** Theorem 8.5's ζT term is irreducible by learning. It shrinks only through better reward measurement: more frequent ground-truth audits, a verifier with lower correlation to the producer, or a task distribution where R̂ = R by construction (tests that fully specify the task). The kernel can report its estimate of ζ from the audit stream but cannot lower it on its own.

**Shadow validity past the first divergence.** Proposition 7.4 is honest that the shadow estimate is unbiased only at the first changed decision per episode. A model-based estimate (a learned simulator of what κ′ would have encountered) would extend it, at the cost of a new source of bias. Whether that trade is worth making is an empirical question the document does not settle.

**Minimality of the kernel.** Section 2 argues each component is necessary by naming what is lost without it. That is a necessity argument, not a proof that seven is the minimum under every reasonable merging. K3 and K7 could be one store with two write paths; K4 and K5 could be one learner with a continuous and a discrete mode. The functional partition is what the proofs use; the physical partition is an implementation choice.

**What is left to the operator**, stated once: the choice of λ, the setting of δ\_max, the tool registry's side-effect classes and loss scales, the verifier's calibration, and the decision to reinstall when κ\_P needs to change. Everything else the kernel does for itself, and the theorems above are the contract for how safely it does so.

Implementation, builder pipeline and the composed-system proofs: Addendum: implementation and builder pipeline
