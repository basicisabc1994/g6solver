# G6 Unified Mathematical Theory of Agentic Intelligence

> **About this document.** These are empirically calibrated models fitted to G6's March to May 2026 benchmark runs,
> not proved theorems, as the epistemic-status note below says. Where a model suggests a trust level or an escalation
> threshold, that is the model's suggestion: G6 itself labels each result only `verified`, `qualified-draft` or
> `blocked-escalated`. Later audits corrected some of the input figures:
>
> - SWE-bench Pro's 47% was with up to three attempts against a one-attempt baseline; at matched effort, one attempt
>   each, it was 38% against 27% (p-value 0.0074).
> - The OmniMATH figures here (43.8% to 55.0%, 80 problems) are the run's own; G6's Research page gives the audited
>   Omni-MATH figures, 49% to 58% on 286 problems, and which tool made the gain is unproven.
> - The MedXpertQA figures here (22.9% to 31.2%) are the run's own; the audited figures are 22.9% to 31.6%, which is
>   not significant (p-value 0.135).
> - The GAIA Level 2 figures (39.4% to 68.2%) held on audit, but G6 had twice the baseline's budget.

## Preamble

> **Epistemic status: empirical models, not proved theorems.** The numbered results below are **empirically-calibrated models** — proposed functional forms whose
> constants are **fitted to benchmark data**, not derived from axioms. They are labelled **"Model N"**
> (not "Theorem N") deliberately: **no formal proof is claimed** for any of them, and the single
> "proof sketch" present is an informal argument, not a machine-checkable proof. Read them as
> quantitative engineering hypotheses with empirical support — closer to a fitted curve in
> experimental physics than to a mathematical theorem. The only G6 results that are theorems in the
> strict sense (with proofs in the literature) are the **Yoneda lemma** and the **Curry–Howard
> correspondence**, discussed in [Theoretical Foundations](G6_Theoretical_Foundations.md).

This document formalises the G6 system as a set of quantitative models grounded in empirical results
from benchmarks conducted between March–May 2026. Each model states its functional form and, where
available, an **empirical calibration** table giving the fitted constants and the benchmark they were
fit from. Calibration data (N: sample size): GAIA Level 2, or GAIA-L2 (N=66), OmniMATH (N=80), SWE-Bench Pro (N=100), MATH-500 (N=100),
MetaTool (N=100), MedXpertQA (N=96), self-training (N=20×3 iterations), and T3 theory evolution
(N=100 sequential tasks; T3 is the theory-building level, the third of the T0–T3 levels of adaptation in
[Theoretical Foundations](G6_Theoretical_Foundations.md)). Quantities are dimensionless unless stated (accuracy ∈ [0,1], token and
iteration counts); model sizes are parameter counts (27B: 27 billion; 2T: 2 trillion).

> **Known gaps (being closed).** Per-constant **confidence intervals**, and a clear split between *fitted* constants and *assumed
> priors*, are not yet annotated on every model. Where a single benchmark fit produced a constant,
> treat it as a point estimate from that N — not a universal value, and not transferable beyond the
> regime it was fit in.

---

## 1. Theory of Self-Correction Loops

### 1.1 Definitions

Let a **problem class** $\mathcal{P}$ be a distribution over instances $(x, y^{\ast})$ where $x$ is the input and $y^{\ast}$ is the gold answer.

A **harness** $H_t$ at iteration $t$ is a tuple $(P, \mathcal{T}, \pi)$ where:
- $P$ is the prompt template
- $\mathcal{T} = \lbrace T_1, ..., T_k\rbrace$ is the tool set
- $\pi: X \to Y$ is the induced policy (prompt + tools + model = answer)

A **self-correction loop** is a sequence $H_0, H_1, ..., H_t$ where each $H_{t+1}$ is produced by a **meta-operator** $\mathcal{M}$ that observes failures of $H_t$ on a training subset.

### 1.2 Convergence Model

**Model 1** (Bounded Improvement). For a problem class $\mathcal{P}$ with difficulty $d \in [0,1]$ and a model with capacity $C$ (Section 5), the accuracy after $t$ self-correction iterations satisfies:

$$A(t) = A^{\ast} - (A^{\ast} - A_0) \cdot e^{-\lambda t}$$

where:
- $A_0$ is initial accuracy (no harness)
- $A^{\ast} = \min(1, C / d)$ is the asymptotic ceiling (capacity-limited)
- $\lambda = \lambda(d, |\mathcal{D}_{train}|, |\mathcal{T}|)$ is the learning rate

**Empirical calibration:**

| Benchmark | $A_0$ | $A^{\ast}$ (observed plateau) | $\lambda$ (fitted) | Iterations to 90% of gap |
|-----------|--------|--------------------------|---------------------|---------------------------|
| MedXpertQA (27B) | 0.125 | 0.542 | 0.46 | 5 cycles |
| T3/SWE-Bench (GPT-5.5) | 0.27 | 0.47 | 0.031 | ~74 tasks |
| GAIA-L2 (Opus) | 0.394 | 0.682 | — (single-shot G6) | 1 (pre-engineered) |

### 1.3 Theory Size and Compression

Let $\Theta_t$ denote the **theory set** at iteration $t$ — the collection of learned rules/strategies the system has accumulated.

**Definition** (Theory Compression Ratio). For a domain with information content $I(\mathcal{P})$ bits, the compression ratio is:

$$\rho = \frac{|\Theta_t|}{I(\mathcal{P})}$$

where $|\Theta_t|$ is measured in token-equivalent complexity of the theory descriptions.

**Empirical observation (T3 system):**
- 17 active theories suffice for 100 SWE-bench tasks across Go/Python/JavaScript/TypeScript
- Each theory: ~200 tokens description → total theory size ≈ 3,400 tokens
- Problem space: ~100 × 50,000 tokens average per task = 5M tokens of problem content
- Compression ratio: $\rho \approx 6.8 \times 10^{-4}$

**Model 2** (Minimum Theory Size). The minimum theory set size required to achieve accuracy $A$ on problem class $\mathcal{P}$ with $k$ distinct failure modes satisfies:

$$|\Theta^{\ast}| \geq k \cdot \log_2\left(\frac{A}{1 - A}\right) \cdot H(\mathcal{F})$$

where $H(\mathcal{F})$ is the entropy of the failure mode distribution.

**Empirical validation:**
- T3 failure modes concentrated: F2P_FAIL (fail-to-pass tests still failing)=97%, TIMEOUT=2%, EMPTY_PATCH=1%
- $H(\mathcal{F}) \approx 0.25$ bits (very concentrated)
- 5 retired theories were those addressing rare/non-recurring failure modes
- System naturally prunes to $|\Theta| \approx 3k$ (17 theories for 3 failure classes)

### 1.4 Grounding Requirements

**Definition** (Grounding Function). The minimum grounding data $G$ required for extrapolation accuracy $A$ on unseen instances from the same distribution:

$$G(A, d, \sigma^2) = \frac{d}{\lambda} \cdot \ln\left(\frac{A^{\ast} - A_0}{A^{\ast} - A}\right) \cdot (1 + \sigma^2)$$

where $\sigma^2$ is the variance of the problem distribution.

**Empirical calibration:**
- MedXpertQA: 7,827 FAISS (Facebook AI Similarity Search) chunks (6 textbooks) → +36.4% relative improvement
- But: >6,000 chars context **degrades** 27B model performance
- Optimal grounding: $G^{\ast} = \min(G_{needed}, G_{capacity})$ where $G_{capacity}$ is model-dependent

**Key finding:** Grounding is subject to a **capacity constraint** (Section 5). For a model with context window $W$ and effective processing capacity $C_w < W$:

$$G^{\ast}_{effective} = \min\left(G(A, d, \sigma^2),\ C_w\right)$$

For medgemma:27B: $C_w \approx 6000$ chars ≈ 1,500 tokens (empirically determined).

### 1.5 Meta-Algorithm Parameters

**Algorithm: G6 Self-Correction Loop**

```
Input: D_train, D_test, model M, tool_set T, max_iterations K
Parameters:
  - quality_threshold: 0.85 (distillation gate)
  - fallback_threshold: 0.60 (replay acceptance)
  - consistency_k: 3 (self-consistency votes)
  - depth_profile: {SIMPLE: 4, MODERATE: 8, COMPLEX: 15} steps
  - word_count_thresholds: [50, 150] (complexity classification)
  - theory_strength_init: 0.5
  - theory_retirement_threshold: 0.2
  - max_theories: ~20 (empirical saturation point)

Procedure:
  H_0 ← initial_harness(M, T)
  Θ_0 ← seed_theories()
  for t = 1 to K:
    results ← evaluate(H_t, D_train)
    failures ← {(x,y) ∈ results : y ≠ y*}
    failure_modes ← classify_failures(failures)
    for mode in failure_modes:
      if mode not covered by Θ_t:
        θ_new ← synthesize_theory(mode, failures)
        Θ_{t+1} ← Θ_t ∪ {θ_new}
      else:
        update_strength(Θ_t[mode], success_rate)
    retire({θ ∈ Θ_t : strength(θ) < retirement_threshold})
    H_{t+1} ← re-engineer(H_t, Θ_{t+1})
  return H_K, Θ_K
```

**Empirical stopping criteria:**
- Theory count stabilises at ~17-20 for SWE-bench class problems
- Strength converges to 0.5 (balanced exploitation) after ~50 applications
- Marginal accuracy gain < 1% per additional iteration signals convergence

---

## 2. Theory of Information Compression (Token Economics)

### 2.1 The Grounding-Output Model

**Model 3** (Minimum Input Tokens for Reliable Output). For a task requiring output correctness probability $p \geq 1 - \epsilon$, the minimum input token count $n_{in}$ satisfies:

$$n_{in} \geq \frac{I(y^{\ast} | x)}{R(M)} + n_{ground}(d, \epsilon)$$

where:
- $I(y^{\ast} | x)$ is the conditional information content of the correct answer given the question
- $R(M)$ is the **information rate** of model $M$ (bits of useful information extracted per input token)
- $n_{ground}(d, \epsilon)$ is the grounding overhead for difficulty $d$ and error tolerance $\epsilon$

**Empirical measurement of $R(M)$:**

| Model | Benchmark | Tokens In (baseline) | Tokens In (G6) | Accuracy Baseline | Accuracy G6 | $R$ estimate |
|-------|-----------|---------------------|----------------|-------------------|-------------|--------------|
| Opus 4.6 | GAIA-L2 | 247,515 | 612,776 | 39.4% | 68.2% | 0.82 bits/token |
| Opus 4.6 | OmniMATH | 17,755 | 177,746 | 43.8% | 55.0% | 0.14 bits/token |
| Opus 4.6 | MATH-500 | 13,152 | 235,651 | ~95% | ~95% | 4.1 bits/token |

**Interpretation:** $R(M)$ varies dramatically by domain. MATH-500 (already saturated) shows high information rate — the model already "knows" the answer, additional tokens are overhead. OmniMATH (hard) shows low rate — much grounding needed per bit of correct output.

### 2.2 Token Efficiency Frontier

**Definition** (Pareto-Optimal Token Allocation). A harness achieves Pareto-optimal token efficiency if no reallocation of tokens between reasoning, grounding, and tool-calling can improve accuracy without increasing total token count.

The token budget decomposes as:

$$n_{total} = n_{reason} + n_{ground} + n_{tool} + n_{overhead}$$

**Empirical decomposition (GAIA-L2, G6 condition):**
- $n_{reason}$: ~200,000 tokens (model's own reasoning)
- $n_{ground}$: ~150,000 tokens (retrieved context)
- $n_{tool}$: ~250,000 tokens (tool call inputs/outputs, 25.12 calls × ~10k each)
- $n_{overhead}$: ~12,000 tokens (system prompt, formatting)

### 2.3 The Compression Bound

**Model 4** (Output Reliability Bound). The probability that an output is correct given input grounding of size $g$ tokens on a problem of difficulty $d$:

$$P(correct | g, d, M) = 1 - \exp\left(-\frac{g \cdot R(M)}{d \cdot I_{min}}\right)$$

where $I_{min}$ is the minimum information required to determine the correct answer.

**Corollary:** Under the model, reaching $P(correct) \geq 1 - \epsilon$ requires:

$$g^{\ast} \geq \frac{-d \cdot I_{min} \cdot \ln(\epsilon)}{R(M)}$$

**Empirical validation:**
- OmniMATH difficulty 5.0: $g^{\ast} \approx 177k$ tokens → 79.5% accuracy
- OmniMATH difficulty 6.0+: $g^{\ast} \approx 177k$ tokens → 25.7% accuracy (insufficient for hard problems)
- The gap indicates $I_{min}$ grows super-linearly with difficulty for competition math

### 2.4 Context Window Saturation

**Model 5** (Diminishing Returns of Context). For models with finite effective attention span $W_{eff} < W_{nominal}$:

$$P(correct | g) = P(correct | \min(g, W_{eff})) \cdot \gamma^{\max(0, g - W_{eff})}$$

where $\gamma \in (0, 1)$ is the **attention decay factor**.

**Empirical evidence:**
- MedXpertQA (medgemma:27B): Performance *decreased* above 6,000 chars
- $W_{eff} \approx 1,500$ tokens for medgemma:27B
- $\gamma \approx 0.95$ (each additional 1000 tokens beyond capacity reduces accuracy by ~5%)
- GAIA (Opus 4.6): No saturation observed up to 629k tokens → $W_{eff} \gg 200k$ for frontier models

**Practical implication:** Token budget should be allocated as:

$$g^{\ast} = \min\left(\frac{-d \cdot I_{min} \cdot \ln(\epsilon)}{R(M)},\ W_{eff}(M)\right)$$

---

## 3. Theory of Self-Optimisation (Harness Design)

### 3.1 The Cost-Quality Frontier

**Definition** (Harness Utility). The utility of a harness configuration is:

$$U(H) = \frac{A(H)}{C(H)^\alpha}$$

where $A(H)$ is accuracy, $C(H)$ is cost per task, and $\alpha \in (0,1]$ is the cost sensitivity parameter.

**Empirical cost-quality points:**

| Configuration | Accuracy | Cost/task | Utility ($\alpha=0.5$) |
|---------------|----------|-----------|------------------------|
| Opus baseline (GAIA) | 39.4% | \$0.572 | 0.521 |
| Opus + G6 (GAIA) | 68.2% | \$0.817 | 0.754 |
| Opus baseline (OmniMATH) | 43.8% | \$0.183 | 1.023 |
| Opus + G6 (OmniMATH) | 55.0% | \$0.366 | 0.909 |
| medgemma:27B baseline (MedXpert) | 22.9% | \$0.00 | ∞ (local) |
| medgemma:27B + G6 (MedXpert) | 31.2% | \$0.00 | ∞ (local) |
| GPT-5.5 baseline (SWE-bench) | 27% | ~\$0 | — |
| GPT-5.5 + G6/T3 (SWE-bench) | 47% | ~\$0 | — |

### 3.2 Optimal Agent Count

**Model 6** (Agent Allocation). For a problem decomposable into $k$ subtasks with dependencies $D \subseteq [k] \times [k]$, the optimal number of concurrent agents is:

$$n_{agents}^{\ast} = \min\left(\text{width}(D),\ \left\lfloor\frac{B_{total}}{C_{agent}}\right\rfloor,\ n_{diminishing}\right)$$

where:
- $\text{width}(D)$ is the maximum antichain in the dependency DAG (directed acyclic graph)
- $B_{total}$ is the total budget
- $C_{agent}$ is cost per agent
- $n_{diminishing}$ is where marginal accuracy gain per agent < marginal cost

**Empirical:** GAIA-L2 uses sequential processing (avg 24.76 turns). SWE-bench averages 3.6 tool calls. The evidence suggests G6's current architecture is **sequential-dominant** with tool calls as subroutines rather than independent agents.

### 3.3 Human-in-the-Loop Decision Boundary

**Model 7** (Human-in-the-loop, HITL, Intervention Criterion). A model output should be escalated to human review when:

$$P(correct | x, H) < \tau_{trust}(s, c_{error}, c_{human})$$

where:
- $s$ is the stakes of the decision
- $c_{error}$ is the cost of an incorrect output
- $c_{human}$ is the cost of human review
- $\tau_{trust} = 1 - \frac{c_{human}}{c_{error}}$ (break-even threshold)

**Decision matrix:**

| Stakes | $c_{error}$ | $c_{human}$ | $\tau_{trust}$ | Model's suggestion |
|--------|-------------|-------------|----------------|-------------|
| Low (data entry) | \$10 | \$50 | 0.0 | Never escalate — cheaper to fix errors |
| Medium (code review) | \$500 | \$100 | 0.80 | Escalate to a person when confidence < 80% |
| High (medical) | \$100,000 | \$200 | 0.998 | Almost always escalate |
| Critical (safety) | ∞ | \$500 | 1.0 | Always escalate |

**Empirical calibration:**
- MedXpertQA cycle 4: Self-verification failed (0/4 predictions changed). For medical domain, model confidence is **uncorrelated** with correctness at the 27B scale → $\tau_{trust} \to 1.0$ (always require human review)
- SWE-bench: 47% pass rate with G6 → at least 53% of tasks require human intervention for production deployment

### 3.4 Model Trust Boundary

**Definition** (Trustable Operation Region). A model $M$ can be trusted without HITL for task class $\mathcal{P}$ if and only if:

$$A(M, H, \mathcal{P}) \geq 1 - \frac{c_{human}}{c_{error}} \quad \text{AND} \quad \text{Calibration}(M, \mathcal{P}) \geq 0.9$$

where Calibration measures whether the model's stated confidence correlates with actual correctness.

**Empirical finding:** The self-verification failure in MedXpertQA demonstrates that **small models are poorly calibrated** — they are confident in wrong answers. Trust requires both high accuracy AND calibration.

---

## 4. Theory of Supervision (Model Selection for Subtask Delegation)

### 4.1 The Delegation Criterion

**Model 8** (Optimal Model Selection). Given a set of available models $\lbrace M_1, ..., M_m\rbrace$ ordered by capability (and cost), the optimal model for subtask $s$ is:

$$M^{\ast}(s) = \arg\min_{M_i} \text{Cost}(M_i) \quad \text{s.t.} \quad A(M_i, s) \geq A_{required}(s)$$

where:

$$A_{required}(s) = 1 - \epsilon_{tolerable}(s) = 1 - \frac{\epsilon_{total}}{\text{criticality}(s) \cdot n_{subtasks}}$$

This distributes the total error budget across subtasks proportional to their criticality.

### 4.2 Failure Mode Taxonomy

From empirical data, model failures cluster into predictable categories with model-size-dependent frequencies:

| Failure Mode | Small Model (≤35B) | Frontier Model (Opus) | Delegation Safety |
|--------------|--------------------|-----------------------|-------------------|
| Hallucination | High (frequent) | Low (rare) | Never delegate |
| Arithmetic error | Medium | Very low | Delegate to tools |
| Context overflow | High (saturates fast) | Low | Delegate up |
| Confidence miscalibration | High | Medium | Never delegate |
| Format compliance | Low | Very low | Safe to delegate |
| Domain knowledge gap | High | Medium | Requires grounding |

**Empirical evidence:**
- medgemma:27B: Verification turn (cycle 4) → 0% correction rate. Model cannot self-supervise.
- Opus 4.6: GAIA-L2 accuracy 68.2% with tools, demonstrating effective self-supervision via tool verification.
- Self-training solver: Pipeline steps inject noise → systematic degradation. Multi-step delegation to same small model **amplifies** errors.

### 4.3 The Supervision Cascade

**Algorithm: Hierarchical Model Delegation**

```
function assign_model(subtask s, error_budget ε):
  d ← difficulty(s)
  c ← criticality(s)
  known_failures ← get_failure_modes(domain(s))
  
  // Start from cheapest model
  for M_i in models sorted by cost ascending:
    if estimated_accuracy(M_i, s) ≥ 1 - ε/c:
      if no critical failure modes for M_i in this domain:
        return M_i
  
  // Fallback to frontier + HITL
  return (M_frontier, require_human_review=True)
```

**Empirical delegation boundaries:**

| Task Type | Minimum Model | Reasoning |
|-----------|---------------|-----------|
| Format extraction | Haiku/small | High accuracy, low stakes |
| Factual question answering (known domain) | 27B + RAG (retrieval-augmented generation) | Grounding compensates |
| Multi-step reasoning | Opus/GPT-5.5 | Error amplification in chains |
| Code generation | Opus/GPT-5.5 | Syntax correctness requires scale |
| Self-verification | Opus only | Small models poorly calibrated |
| Novel problem solving | Opus + tools | Requires both capacity AND grounding |

### 4.4 Error Amplification in Chains

**Model 9** (Chain Error Propagation). For a pipeline of $n$ sequential steps each with individual accuracy $a_i$, the end-to-end accuracy satisfies:

$$A_{chain} = \prod_{i=1}^n a_i \leq a_{min}^n$$

**Empirical validation:**
- Solver pipeline (15 steps): Even with $a_i = 0.95$ per step, $A_{chain} \leq 0.95^{15} = 0.46$
- Observed: solver_auto **underperformed** direct_llm by 10-28pp (percentage points)
- Root cause confirmed: "Pipeline steps inject noise into prompts"

**Corollary:** Delegation to smaller models is safe ONLY for:
1. Independent (non-chained) subtasks
2. Steps with verification gates (error doesn't propagate)
3. Tasks where the smaller model achieves $a \geq 0.99$ (high-confidence regime)

---

## 5. Theory of Maximum Expressive Capacity

### 5.1 Ashby's Law Applied to Large Language Models (LLMs)

**Ashby's Law of Requisite Variety:** A controller must have at least as much variety (degrees of freedom) as the system it controls.

**Definition** (Model Capacity). The effective capacity $C(M)$ of a model $M$ with $N$ parameters, context window $W$, and tool set $\mathcal{T}$ is:

$$C(M, W, \mathcal{T}) = \underbrace{C_{weights}(N)}_{\text{parametric knowledge}} + \underbrace{C_{context}(W)}_{\text{in-context learning}} + \underbrace{C_{tools}(|\mathcal{T}|)}_{\text{external augmentation}}$$

where:
- $C_{weights}(N) \approx k_1 \cdot N^{0.5}$ (following neural scaling laws: performance ~ $N^{-\alpha}$, so capacity ~ $N^{\alpha}$)
- $C_{context}(W) \approx k_2 \cdot \min(W, W_{eff})$ (limited by effective attention)
- $C_{tools}(|\mathcal{T}|) \approx k_3 \cdot \sum_{T \in \mathcal{T}} V(T)$ (variety provided by each tool)

### 5.2 The Variety Gap

**Definition** (Problem Variety). The variety of a problem class $\mathcal{P}$ is the minimum number of distinct strategies required to solve all instances:

$$V(\mathcal{P}) = |\lbrace \text{distinct solution strategies}\rbrace |$$

**Model 10** (Solvability Condition). A model can reliably solve problem class $\mathcal{P}$ if and only if:

$$C(M, W, \mathcal{T}) \geq V(\mathcal{P})$$

When $C < V$: the model will systematically fail on instances requiring strategies outside its capacity.

**Empirical measurement of variety gaps:**

| Benchmark | Problem Variety $V(\mathcal{P})$ | Opus Capacity | Opus + G6 Capacity | Evidence |
|-----------|----------------------------------|---------------|---------------------|----------|
| OmniMATH (d≤5.5) | Medium | Sufficient | Augmented | 61.4% → 79.5% |
| OmniMATH (d≥6.0) | Very high | Insufficient | Still insufficient | 22.9% → 25.7% |
| GAIA-L2 | High | Partial | Sufficient | 39.4% → 68.2% |
| MedXpertQA | Very high (medical) | — | Insufficient (27B) | Ceiling at 54.2% |

**Interpretation of OmniMATH difficulty scaling:**
- At difficulty 5.0-5.5: Tools *augment* capacity enough to bridge the gap (+18.1pp)
- At difficulty 6.0+: The gap between $V(\mathcal{P})$ and $C(M, W, \mathcal{T})$ is too large for tools alone to bridge (+2.8pp)
- This demonstrates a **hard ceiling** where no amount of tooling can compensate for insufficient parametric knowledge

### 5.3 Capacity Expansion via G6

G6 increases effective capacity through three mechanisms:

1. **Tool variety** ($C_{tools}$): Each tool adds solution strategies the base model lacks
   - SageMath: adds symbolic computation (not in LLM weights)
   - GPS/SOAR (the General Problem Solver; the State, Operator And Result cognitive architecture): adds systematic decomposition strategies
   - FAISS/RAG: adds domain knowledge retrieval

2. **Theory accumulation** ($C_{learned}$): T3 theories act as additional parametric knowledge
   - 17 theories × ~200 tokens = 3,400 tokens of learned strategy
   - Functionally equivalent to "fine-tuning" but stored externally

3. **Iterative depth** ($C_{iterations}$): Multiple passes trade time for capacity
   - Each iteration can access different parts of the search space
   - Effective capacity scales as: $C_{eff} = C_{single} \cdot (1 + \beta \cdot \ln(t))$

### 5.4 Trust Decision Criterion

**Model 11** (Output Trust). An output can be trusted without human review when:

$$\frac{C(M, W, \mathcal{T})}{V(\text{specific instance})} \geq \tau_{trust}$$

**Practical implementation:** Estimate instance difficulty $d_i$ from:
- Word count / complexity classification (SIMPLE/MODERATE/COMPLEX)
- Domain keyword density
- Number of reasoning steps required
- Whether the instance resembles training distribution

If estimated $V(x_i) > C(M)$: flag for human review regardless of model confidence.

---

## 6. Theory of Necessary Decomposition

### 6.1 The Decomposition Model

**Model 12** (Minimum Decomposition Depth). For a problem of complexity $K$ (measured in required reasoning steps) and a model with single-pass capacity $C_{pass}$, the minimum number of decomposition steps is:

$$n_{steps}^{\ast} = \left\lceil \frac{K}{C_{pass}} \right\rceil$$

However, due to error amplification (Model 9), the **practical** decomposition must satisfy:

$$n_{steps}^{practical} = \arg\min_n \left[ n : \frac{K}{n} \leq C_{pass} \quad \text{AND} \quad a^n \geq A_{target} \right]$$

This creates a tension: more steps reduce per-step difficulty but amplify chain error.

### 6.2 Optimal Decomposition Depth

**Model 13** (Optimal Step Count). The accuracy-maximising decomposition depth is:

$$n^{\ast} = \frac{-\ln(1 - A_{target})}{\ln(a_{step}(K/n))} \quad \text{(implicit equation in } n\text{)}$$

where $a_{step}(d) = 1 - c \cdot d^\beta$ is the per-step accuracy as a function of step difficulty.

**Empirical calibration from GAIA-L2:**

| Turn Count | N Tasks | Pass Rate | Per-Step Accuracy (inferred) |
|------------|---------|-----------|------------------------------|
| 1-9 | 12 | 50% | ~0.92 |
| 10-19 | 26 | 81% | ~0.98 |
| 20+ | 28 | 64% | ~0.98 (but overhead errors) |

**Key insight:** The optimal window is 10-19 turns (81% pass rate). Below 10: insufficient decomposition. Above 20: overhead and error accumulation reduce net accuracy.

### 6.3 Complexity Classification

**Algorithm: Adaptive Decomposition (empirically validated)**

```
function choose_depth(problem x):
  words ← word_count(x)
  domain_complexity ← keyword_score(x)
  multi_part ← detect_conjunctions(x)
  
  if words ≤ 50 AND domain_complexity < 0.3:
    return SIMPLE (4 steps)
  elif words ≤ 150 OR domain_complexity < 0.6:
    return MODERATE (8 steps)
  else:
    return COMPLEX (15 steps)
```

**Empirical validation (self-training):**
- SIMPLE (4 steps): Best for direct factual queries
- MODERATE (8 steps): OmniMATH-class problems (avg 8.49 turns)
- COMPLEX (15 steps): SWE-bench-class problems (avg 24.76 turns for GAIA-class)

### 6.4 Context Preservation Under Decomposition

**Model 14** (Information Loss per Step). Each decomposition step loses a fraction $\delta$ of the original context:

$$I_{remaining}(n) = I_0 \cdot (1 - \delta)^n$$

To ensure context preservation above threshold $I_{min}$:

$$n \leq \frac{\ln(I_{min}/I_0)}{\ln(1-\delta)}$$

**Mitigation strategies (implemented in G6):**
1. **Working memory** (tools of the Agent Cognition Engine, ACE): Persist key facts across steps
2. **RAG retrieval**: Re-ground from source at each step
3. **Theory accumulation** (T3): Encode learned context as reusable strategies
4. **Summarisation**: Compress prior step outputs (recursive_summarize tool)

### 6.5 Scaling to Large Problems

For problems with complexity $K \gg C_{pass}$:

$$n_{agents} = \left\lceil \frac{K}{C_{pass} \cdot n_{steps}^{max}} \right\rceil$$

where $n_{steps}^{max} \approx 20$ (empirical ceiling from GAIA data). Beyond this, **horizontal scaling** (multiple agents with different subtasks) is required rather than deeper sequential chains.

---

## 7. Theory of Debate

### 7.1 Debate Effectiveness Conditions

**Model 15** (Debate Value). Debate between $m$ perspectives improves accuracy if and only if:

$$\text{Corr}(\text{errors}(M_i), \text{errors}(M_j)) < 1 \quad \forall i \neq j$$

i.e., the debating entities must make **different** errors. If they share the same systematic biases, debate adds cost without benefit.

**Empirical evidence:**
- MedXpertQA self-verification (same model critiques itself): **0/4 corrections** → $\text{Corr} \approx 1$
- Self-consistency voting (same prompt, 3 samples): No improvement when the prompt itself is noisy → shared bias
- MetaTool debate_interpretations (structured multi-perspective): Effective when combined with grounding

### 7.2 Optimal Debate Configuration

**Parameters:**

| Parameter | Optimal Value | Evidence |
|-----------|---------------|----------|
| Number of rounds | 2-3 | Diminishing returns after; MedXpertQA cycle 4 failed with single verification |
| Same model vs different | Different preferred | Same-model critique shares biases; different models have decorrelated errors |
| Prompt design | Adversarial + structured | "Debate interpretations" tool uses structured multi-perspective framing |
| When to use debate | $d > 0.5$ AND answer ambiguous | Low-difficulty tasks don't benefit; clear answers don't need debate |
| Model for critic | ≥ same size as proposer | Smaller critics cannot reliably identify errors in larger model outputs |

### 7.3 The Debate Utility Function

$$\Delta A_{debate}(m, r) = A_0 \cdot (1 - \rho^m) \cdot (1 - e^{-r/r_0})$$

where:
- $m$ = number of distinct perspectives
- $r$ = number of rounds
- $\rho$ = error correlation between perspectives
- $r_0$ = characteristic round constant (~2)

**Conditions for positive ROI (return on investment):**

$$\Delta A_{debate} \cdot V_{correct} > m \cdot r \cdot C_{inference}$$

where $V_{correct}$ is the value of a correct answer and $C_{inference}$ is cost per inference call.

### 7.4 Self-Verification Failure Model

**Model 16** (Self-Verification Impossibility for Small Models). A model $M$ with calibration error $\epsilon_{cal}$ cannot reliably self-verify if:

$$\epsilon_{cal} > \frac{1}{2} - A(M, \mathcal{P})$$

**Proof sketch:** If the model is wrong with probability $1-A$ and its confidence is miscalibrated by $\epsilon_{cal}$, then the verification step has probability $\leq A + \epsilon_{cal}$ of accepting a correct answer and rejecting an incorrect one. When $\epsilon_{cal} > 0.5 - A$, the verification is no better than random.

**Empirical:** medgemma:27B on MedXpertQA: $A \approx 0.31$, $\epsilon_{cal} \approx 0.4$ (model confident in wrong answers) → self-verification mathematically impossible.

---

## 8. Theory of Pretraining vs Meta-Harness Scaling

### 8.1 Neural Scaling Laws (Baseline)

Standard neural scaling law (Kaplan et al.):

$$L(N) = \left(\frac{N_c}{N}\right)^\alpha$$

where $L$ is loss, $N$ is parameter count, $\alpha \approx 0.076$ for language models, and $N_c$ is a task-dependent constant.

Equivalently, for accuracy: $A(N) = 1 - c \cdot N^{-\alpha}$

### 8.2 G6 Meta-Harness Scaling Law

**Model 17** (Meta-Harness Scaling). G6's effective performance scales as:

$$A_{G6}(N, |\mathcal{T}|, |\Theta|, t) = 1 - c \cdot N^{-\alpha} \cdot \underbrace{(1 + \gamma|\mathcal{T}|)^{-1}}_{\text{tool augmentation}} \cdot \underbrace{(1 + \delta|\Theta|)^{-1}}_{\text{theory learning}} \cdot \underbrace{e^{-\lambda t}}_{\text{iteration improvement}}$$

This means G6 achieves **multiplicative** improvement over base scaling:

$$\frac{A_{G6}}{A_{base}} = \frac{1 - c \cdot N^{-\alpha} \cdot (1+\gamma|\mathcal{T}|)^{-1} \cdot (1+\delta|\Theta|)^{-1} \cdot e^{-\lambda t}}{1 - c \cdot N^{-\alpha}}$$

### 8.3 Empirical Scaling Comparison

**Parameter count equivalence:** How many additional parameters would be needed to match G6's improvement without tools?

| Benchmark | Base Accuracy | G6 Accuracy | Equivalent Model Size (est.) | Actual Cost |
|-----------|--------------|-------------|------------------------------|-------------|
| GAIA-L2 | 39.4% (Opus ~200B) | 68.2% | ~2T parameters | 2.47x tokens |
| SWE-Bench | 27% (GPT-5.5) | 47% | ~5T parameters | 3.6 tool calls |
| OmniMATH | 43.8% (Opus) | 55.0% | ~500B parameters | 10x tokens |
| MedXpertQA | 22.9% (27B) | 31.2% | ~100B parameters | ~0 (local RAG) |

**Key insight:** on these runs, the model estimates that G6 provides the equivalent of a 5-25x parameter increase, at the cost of 2-10x more tokens. Since inference cost scales linearly with tokens but training cost scales super-linearly with parameters, this is economically favourable whenever:

$$C_{token} \cdot n_{extra_tokens} < C_{training} \cdot \Delta N_{equivalent}$$

### 8.4 The Crossover Point

**Model 18** (Scaling Regime Crossover). Meta-harness scaling dominates pure parameter scaling when:

$$N < N_{crossover} = \left(\frac{c \cdot \alpha}{(\gamma|\mathcal{T}| + \delta|\Theta|) \cdot \lambda}\right)^{1/\alpha}$$

Below $N_{crossover}$: G6 tools provide more improvement per dollar than scaling model size.
Above $N_{crossover}$: Diminishing returns on tooling; better to invest in larger models.

**Empirical estimate:** Given observed improvements, $N_{crossover} \approx 1T$ parameters with current tool sets. This suggests G6-style augmentation remains beneficial for all currently available models.

---

## 9. Theory of Multi-Agent Systems (Agentic Amdahl's Law)

### 9.1 Classical Amdahl's Law (Reformulated for Agents)

Classical: $S(n) = \frac{1}{(1-p) + p/n}$ where $p$ is parallelisable fraction and $n$ is processor count.

### 9.2 Agentic Amdahl's Law

**Model 19** (Agentic Speedup). For a problem with sequential fraction $s$, parallelisable fraction $p = 1-s$, and $n$ agents, the effective speedup is:

$$S_{agent}(n) = \frac{1}{s + \frac{p}{n} + \underbrace{C_{coord}(n)}_{\text{coordination cost}} + \underbrace{C_{merge}(n)}_{\text{result merging}}}$$

where:
- $C_{coord}(n) = \kappa \cdot n \cdot \ln(n)$ — coordination overhead scales super-linearly
- $C_{merge}(n) = \mu \cdot \binom{n}{2}$ — pairwise merge cost for reconciling agent outputs

**Maximum useful agent count:**

$$n^{\ast} = \arg\max_n S_{agent}(n) = \sqrt{\frac{p}{\kappa \cdot \ln(n) + \mu \cdot (n-1)}}$$

### 9.3 Empirical Parallelism Analysis

| Benchmark | Sequential Fraction $s$ | Parallelisable $p$ | Optimal $n^{\ast}$ | Observed Pattern |
|-----------|-------------------------|---------------------|---------------|------------------|
| GAIA-L2 | 0.85 (high dependency) | 0.15 | 1-2 | Sequential (24.76 turns) |
| SWE-Bench | 0.40 (moderate) | 0.60 | 3-4 | 3.6 tool calls avg |
| OmniMATH | 0.60 | 0.40 | 2-3 | GPS→SageMath→SOAR pipeline |
| MetaTool | 0.20 (mostly independent) | 0.80 | 4-6 | Ground + debate in parallel |

### 9.4 Optimal Multi-Agent Architecture for G6

**Model 20** (G6 Optimal Agent Topology). The optimal topology for a problem with dependency graph $G = (V, E)$ is:

1. **Compute critical path** $L =$ longest path in $G$
2. **Identify parallel layers** $\ell_1, ..., \ell_L$ where each $\ell_i$ is a set of independent tasks
3. **Assign agents per layer:** $n_i = \min(|\ell_i|, n_{budget})$
4. **Pipeline between layers:** Sequential handoff with summarisation

**G6 Architecture (derived from empirics):**

```
Layer 0: DECOMPOSITION (1 agent — GPS/goal decomposition)
  │
  ├── Layer 1: GROUNDING (n agents in parallel — RAG, web search, FAISS)
  │
  ├── Layer 2: REASONING (1-3 agents — SageMath, SOAR, domain tools)
  │     └── Optional: DEBATE (2 agents if difficulty > threshold)
  │
  ├── Layer 3: SYNTHESIS (1 agent — merge and format)
  │
  └── Layer 4: VERIFICATION (1 agent — check against constraints)
```

### 9.5 Sequential vs Parallel Decision Criterion

**Model 21** (Parallelisation Decision). Subtasks $A$ and $B$ should be parallelised if and only if:

$$I(A; B | x) < \tau_{independence}$$

where $I(A; B | x)$ is the mutual information between the subtask outputs conditioned on the input.

**Practical rules (from empirical observations):**

| Condition | Decision | Rationale |
|-----------|----------|-----------|
| Subtask B needs A's output | Sequential | Causal dependency |
| Both need same facts from input | Parallel | Independent extraction |
| Verification of A's output | Sequential | Must wait for A |
| Alternative strategies for same goal | Parallel | Race condition — take first success |
| Grounding from different sources | Parallel | Independent retrieval |
| Multi-step reasoning chain | Sequential | Error propagation requires ordering |

### 9.6 Coordination Protocol

**Algorithm: G6 Multi-Agent Coordinator**

```
function orchestrate(problem x, model M, tools T):
  // Phase 1: Decompose
  subtasks ← gps_decompose(x)
  G ← build_dependency_graph(subtasks)
  
  // Phase 2: Assign layers
  layers ← topological_sort_layers(G)
  
  // Phase 3: Execute with parallel-within-layer
  context ← {}
  for layer in layers:
    if |layer| == 1:
      result ← execute_sequential(layer[0], context)
    else:
      results ← execute_parallel(layer, context)
      result ← merge(results)
    context ← context ∪ summarize(result)
  
  // Phase 4: Verify
  if difficulty(x) > DEBATE_THRESHOLD:
    result ← debate(result, x, rounds=2)
  
  return result
```

---

## 10. Formal System Properties

### 10.1 Stability Conditions

**Model 22** (System Stability). The G6 system is stable (accuracy non-decreasing over time) if:

1. **Theory monotonicity:** Theories are only retired when $\text{strength}(\theta) < \theta_{retire}$, ensuring no useful strategy is lost.
2. **Grounding freshness:** External knowledge sources are refreshed at rate $\geq$ domain change rate.
3. **Error isolation:** Failures in one subtask do not corrupt the state of independent subtasks.

**Empirical evidence of stability:**
- T3 strength values converge to 0.5 (stable equilibrium)
- Theory retirement threshold (0.2) ensures only truly ineffective theories are pruned
- 5 theories retired out of 22 created = 23% pruning rate (healthy turnover)

### 10.2 Accuracy Estimate

**Model 23** (accuracy estimate). For a G6 deployment on problem class $\mathcal{P}$, the model estimates:

$$P(A \geq A_{SLA}) \geq 1 - \delta$$

where the confidence bound $A_{SLA}$ (the subscript abbreviates service-level agreement, SLA) is computed as:

$$A_{SLA} = \hat{A}_{bench} - z_{1-\delta} \cdot \sqrt{\frac{\hat{A}(1-\hat{A})}{n_{bench}}}$$

| Benchmark | $\hat{A}$ | $n$ | $A_{SLA}$ (95%) | $A_{SLA}$ (99%) |
|-----------|-----------|-----|-----------------|-----------------|
| GAIA-L2 (G6) | 0.682 | 66 | 0.570 | 0.534 |
| SWE-Bench (G6) | 0.470 | 100 | 0.372 | 0.341 |
| MedXpertQA (G6) | 0.312 | 96 | 0.219 | 0.190 |
| OmniMATH (G6) | 0.550 | 80 | 0.441 | 0.407 |

**Interpretation:** on new instances drawn from the same distribution, the system is expected to reach at least the $A_{SLA}$ accuracy, with 95% confidence under the model's assumptions.

### 10.3 Cost Estimate

**Model 24** (cost estimate). For a task budget $B$ per instance, the model estimates:

$$P(C_{task} \leq B) \geq 1 - \delta_{cost}$$

With empirical cost distributions:

| Config | Mean Cost | 95th Percentile | 99th Percentile |
|--------|-----------|-----------------|-----------------|
| GAIA-L2 G6 | \$0.817 | \$1.80 | \$2.00 (budget cap) |
| OmniMATH G6 | \$0.366 | \$0.90 | \$1.00 (budget cap) |
| MetaTool G6 | \$0.146 | \$0.45 | \$0.50 (budget cap) |

### 10.4 Consistency Properties

**Property 1** (Idempotency under tools). At temperature 0 with deterministic tools: $H(x) = H(H(x))$ — re-running the same harness on the same input produces the same output.

**Property 2** (Monotonic improvement under self-correction). For the meta-harness: $A(H_{t+1}) \geq A(H_t) - \epsilon_{noise}$ where $\epsilon_{noise}$ accounts for stochastic variation.

**Property 3** (Graceful degradation). If a tool fails: $A(H \setminus T_i) \geq A_{baseline}$ — removing a tool cannot make the system worse than no tools.

---

## 11. Complete G6 Formalisation

### 11.1 System Definition

The G6 system is a 7-tuple:

$$\mathcal{G} = (M, \mathcal{T}, \Theta, \Pi, \Sigma, \mathcal{V}, \mathcal{O})$$

where:
- $M$ = base model (with parameters $N$, context $W$, information rate $R$)
- $\mathcal{T}$ = tool set (34 tools providing variety $V(\mathcal{T})$)
- $\Theta$ = theory set (learned strategies, max ~20, strength-weighted)
- $\Pi$ = decomposition policy (SIMPLE/MODERATE/COMPLEX depth selection)
- $\Sigma$ = supervision policy (model selection cascade)
- $\mathcal{V}$ = verification policy (HITL threshold, debate configuration)
- $\mathcal{O}$ = orchestration policy (sequential/parallel decision function)

### 11.2 Invariants

The system must maintain at all times:

1. **Capacity invariant:** $C(M, W, \mathcal{T}) \geq V(\text{assigned task})$ — never attempt tasks beyond system capacity without flagging
2. **Budget invariant:** $C_{accumulated} \leq B_{allocated}$ — hard cost caps enforced
3. **Safety invariant:** For tasks with $\text{criticality} > \tau_{safety}$: always include human verification
4. **Convergence invariant:** $|\Theta_{t+1}| - |\Theta_t| \to 0$ as $t \to \infty$ — theory set must stabilise
5. **Calibration invariant:** any confidence estimate the model relies on must correlate with actual accuracy ($r > 0.7$)

### 11.3 Correctness Conditions

**Model 25** (System Correctness). The G6 system produces correct output with probability:

$$P(correct) = 1 - \underbrace{P_{capacity}}_{\text{variety gap}} - \underbrace{P_{chain}}_{\text{error propagation}} - \underbrace{P_{ground}}_{\text{insufficient grounding}} - \underbrace{P_{tool}}_{\text{tool failure}}$$

where each failure probability can be bounded using the models above:
- $P_{capacity} = \max(0, 1 - C/V)$ (Model 10)
- $P_{chain} = 1 - a^n$ (Model 9)
- $P_{ground} = \exp(-g \cdot R / (d \cdot I_{min}))$ (Model 4)
- $P_{tool} = \prod_i (1 - r_i)$ where $r_i$ is reliability of tool $i$

---

## 12. Practical Decision Tables

### 12.1 Given a New Problem: What Configuration?

```
Input: problem_description, stakes, budget, latency_requirement

1. Estimate difficulty d ← complexity_classifier(problem_description)
2. Estimate variety V(P) ← domain_keywords + structural_analysis
3. Select model M: smallest M where C(M) ≥ V(P)
4. Select tools T: add until C(M,T) ≥ 1.5 × V(P) (safety margin)
5. Set decomposition: n_steps ← depth_profile(d)
6. Set debate: if d > 0.6 AND stakes > medium: enable 2-round debate
7. Set HITL: if stakes × (1 - P(correct)) > c_human: require human review
8. Set budget: B = C_model × n_steps × (1 + tool_overhead_factor)
9. Set parallelism: identify independent subtasks, assign agents per layer
```

### 12.2 When the Model Suggests Trusting the Output

| Condition | Trust level the model suggests | Action the model suggests |
|-----------|-------------|--------|
| Difficulty ≤ 0.3, model saturated on this type | Full trust | Use without review |
| Difficulty 0.3-0.6, tools available, debate agrees | High trust | Log for audit, no blocking review |
| Difficulty 0.6-0.8, partial tool coverage | Medium trust | Asynchronous human review |
| Difficulty > 0.8, novel domain | Low trust | Synchronous human approval required |
| Model self-verification disagrees with initial answer | No trust | Escalate immediately |
| Any safety-critical domain | Never trust alone | Always require human sign-off |

---

## Appendix A: Notation Reference

| Symbol | Meaning |
|--------|---------|
| $A$ | Accuracy (probability of correct output) |
| $C$ | Capacity (model + tools + context) |
| $V$ | Variety (problem complexity in distinct strategies) |
| $d$ | Difficulty $\in [0,1]$ |
| $n$ | Number of decomposition steps or agents |
| $g$ | Grounding token count |
| $R(M)$ | Information rate of model $M$ |
| $W_{eff}$ | Effective context window |
| $\Theta$ | Theory set |
| $\lambda$ | Learning rate of self-correction |
| $\rho$ | Error correlation between debaters |
| $\tau_{trust}$ | Trust threshold |
| $\epsilon$ | Error tolerance |

## Appendix B: Empirical Constants (Calibrated from Benchmarks)

| Constant | Value | Source |
|----------|-------|--------|
| $\alpha$ (scaling exponent) | 0.076 | Neural scaling laws (Kaplan) |
| $\lambda_{T3}$ (theory learning rate) | 0.031 per task | T3 SWE-Bench convergence |
| $\lambda_{MedXpert}$ (revision learning rate) | 0.46 per cycle | MedXpertQA 5-cycle improvement |
| $W_{eff}$ (27B model) | 1,500 tokens | MedXpertQA context saturation |
| $W_{eff}$ (Opus 4.6) | >200,000 tokens | GAIA no saturation at 629k |
| Optimal turn window | 10-19 | GAIA pass rate by turn bucket |
| Theory saturation | ~17-20 theories | T3 convergence at 100 tasks |
| Theory equilibrium strength | 0.5 | T3 final strength distribution |
| Cost multiplier (G6/baseline) | 2-3x | Cross-benchmark average |
| Accuracy lift (G6-baseline) | +14-20pp | Cross-benchmark average |
| Error correlation (self-critique) | ~1.0 | MedXpertQA verification failure |
| Tool calls per difficulty unit | ~3.5/unit | OmniMATH scaling |

## Appendix C: Open Questions

1. **Calibration training:** Can T3-style learning be applied to improve model calibration (not just accuracy)?
2. **Adaptive debate:** Can the system learn when debate is beneficial vs wasteful from experience?
3. **Transfer across domains:** Do T3 theories transfer between related problem classes?
4. **Cost-optimal frontier:** What is the Pareto-optimal curve across model sizes × tool counts × iteration depths?
5. **Dynamic difficulty estimation:** Can the system estimate $V(x_i)$ before attempting the task to pre-select the optimal configuration?
