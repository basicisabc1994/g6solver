# Addendum: implementation and builder pipeline

> **Status: design rationale.** The proofs are not machine-checked, and the design is not validated. Nothing here is evidence that G6 works; where this document and the library's own documentation differ, the library's documentation describes what G6 does.

Oct 18, 2026

## A0. What this addendum adds

Three artifacts turn the kernel of the main tab into something a coding agent can apply to an arbitrary existing system: a Python wrapper `adapt(system, objectives, oracles)` that is a higher-order function from any system plus any harness to a kernelized agentic system; a builder pipeline (eight phases, each a prompt step followed by a code gate) that a coding agent follows to produce that wrapper for a system it has never seen; and four further theorems showing that the composed system inherits every guarantee of Sections 7–8 and adds soundness over the wrapped system.

The word *optimal* keeps the meaning fixed in Section 8: not on the frontier, but approaching it at the minimax rate, with a certificate that states exactly where the proof stops and the empirical estimate begins. The builder's last phase emits that certificate, and Theorem A.4 is the proof that the certificate is itself sound.

Two design decisions govern everything below. First, the boundary between the agent and the wrapped system is a single object, the adapter, and the adapter's side-effect declarations live in the protected region κ\_P; this is what lets Theorem A.1 say that the agent can reach no state of the system the operator did not declare reachable. Second, every extension point is a Protocol in the open region κ\_O, so a specialization for a domain goes through the same six-stage protocol as a rewrite the kernel proposes to itself; there is one safety path, not two.

## A1. Modelling an arbitrary system

Any system S the agent is to operate is modelled as a labelled transition system with observers; the adapter is the only object that touches it, and its declarations are the data the safety theorems run on.

**Definition A1.1 (System).** S = (Σ, A\_S, →, O\_S, σ\_0): a state space Σ, a finite set of named capabilities A\_S, a transition relation → ⊆ Σ × A\_S × Args × Σ, a set of observers O\_S: Σ → Obs, and an initial state σ\_0. Nothing is assumed about Σ being enumerable or → being deterministic. A REST API, a CLI, a database, a robot controller, a spreadsheet, and a human-in-the-loop approval queue are all instances.

**Definition A1.2 (Capability manifest).** For each a ∈ A\_S the adapter declares a record:

| Field | Type | Meaning | Who sets it |
| --- | --- | --- | --- |
| `name` | str | Stable identifier | Builder, from the system's surface |
| `signature` | JSON schema | Argument types the oracle must produce | Builder, from the surface |
| `effect` | idempotent / reversible / irreversible / unknown | Side-effect class | Builder proposes; a human confirms every non-idempotent class |
| `loss_scale` | float ≥ 0 | L in Section 1; 0 for idempotent | Human, for irreversible; 0 otherwise |
| `revert` | callable or None | Inverse action, required if `effect` = reversible | Builder; tested in Phase 2 |
| `observers` | list\[str\] | Which observers reveal this capability's effect | Builder |
| `cost_model` | callable(args) → R^k | Expected contribution to each cost coordinate | Builder; refined by the ledger |

The conservative rule that makes this deployable anywhere: `unknown` is treated as `irreversible` with `loss_scale` = L\_max. A system with poorly understood capabilities is therefore usable from the first minute, at the price of every unknown call being gated; the builder's job is then to earn back permissiveness by establishing classes, not to assume it.

**Definition A1.3 (Adapter boundary).** The adapter is the unique component that holds a reference to S. It exposes exactly: `capabilities()` returning the manifest, `invoke(name, args)` performing one transition, `observe()` returning the current observer readings, and `reset()` where the system supports it. The agentic system reaches S only through `invoke`, and `invoke` is a call site in the control-flow graph that I3 recognizes as a tool call. This is the formal link between the main tab's invariants and the wrapped system.

**Definition A1.4 (Composed system).** Given S with adapter Ad, a host harness H (or the built-in minimal loop when none is supplied), oracles M, and an objective spec Ω (Section A2), the composed system is

```latex
\mathcal{A} = \mathrm{adapt}(S, \Omega, M, H) = H[\theta, \kappa] \circ \mathrm{Ad}(S)
```

with θ\_0 the default parameters of Section 9.5 and κ\_0 the installed kernel plus Ad's manifest in κ\_P. Theorem A.1 is about what states of S the composed system can reach; Theorem A.3 is about which objective vector it converges toward.

## A2. Objectives

An objective spec Ω is a list of measurable quantities, each marked as something to maximize, minimize, or hold within a bound, plus a reference point; the kernel's objective vector F is read off Ω mechanically, so the quality of what the agent optimizes is exactly the quality of Ω.

### A2.1 Schema

```python
@dataclass(frozen=True)
class Objective:
    name: str                              # "on_time_delivery_rate"
    measure: str                           # expression over observers + ledger: "mean(obs.delivered_on_time)"
    direction: Literal["max", "min", "bound"]
    bound: tuple[float | None, float | None] = (None, None)   # for direction="bound": (lo, hi)
    unit: str = ""
    weight: float | None = None            # used only for ranking proposals (λ); never for the gradient
    reference: float | None = None         # z* for Chebyshev scalarization; the "good enough" level
    provenance: Literal["specified", "inferred", "default"] = "specified"
    confirmed: bool = False                # inferred objectives must be confirmed before use in promotion

@dataclass(frozen=True)
class ObjectiveSpec:
    objectives: list[Objective]
    horizon: int                           # episodes over which measures are averaged
    risk_loss_max: float                   # L_max for unknown capabilities
    delta_max: float = 0.01                # Section 9.5
```

### A2.2 How Ω becomes F

Each `max` objective is a coordinate of F as written; each `min` objective enters negated; each `bound` objective enters as a *constraint*, handled by projecting θ onto the set where the bound's ledger estimate holds, not by adding it to the gradient. The four kernel costs (tokens, wall-clock, dollars, risk) are always present as `min` objectives with provenance `default`, so the frontier the agent approaches is never a quality-only frontier. Weights, where given, define λ for ranking proposals and for the certificate's hypervolume reference; they do not enter the Désidéri direction (Theorem 6.5).

Reference points z\* are what make Chebyshev scalarization usable when the user cannot give weights: the kernel then ranks proposals by max\_i (z\*\_i − F\_i) / scale\_i, which reaches non-convex frontier points and asks the user only "what level would you be satisfied with" rather than "how many dollars is a percentage point of quality worth."

### A2.3 Elicitation

When Ω is not supplied, the builder infers it through a fixed question set, in this order, stopping when the schema is complete. Every inferred objective is written with `provenance="inferred"`, `confirmed=False`, and is shown back to the user in the words of their own system before any promotion decision uses it.

| Question | What it fills | Why it is asked this way |
| --- | --- | --- |
| What does a good outcome of one run look like, in terms you already measure? | `max` objectives, `measure`, `unit` | Anchors on existing observers; avoids inventing metrics |
| What is the one number that, if it went wrong, you would stop the system? | `bound` objectives, `risk_loss_max` | Surfaces hard constraints before soft preferences |
| Which actions, once taken, cannot be undone? What does one such mistake cost? | Capability `effect`, `loss_scale` | Humans are reliable on irreversibility and poor on weights |
| What level of each measure would you call good enough? | `reference` | Reference points are easier to state than tradeoffs |
| How much are you willing to spend per run, in money and in time? | Bounds on C\_$ and C\_wall | Turns the default cost objectives into constraints the user owns |
| If two runs differ only in A and B, which would you prefer? (asked at most three times, on observed pairs) | `weight` | Pairwise comparisons on real pairs beat abstract weights; three suffice to orient λ |

The builder never asks for a weight on an objective the user has not confirmed exists, and never asks the irreversibility question of the oracle instead of the human. Those two refusals are what Theorem A.4 needs to call the certificate's "specified" column trustworthy.

### A2.4 What a real-world multi-objective problem looks like in Ω

A supply-chain scheduler might carry: `max` fill rate (observer), `min` total freight cost (observer), `bound` on stock-outs per week ≤ 2, `min` on the four kernel costs, references of 0.97 fill and current freight cost, irreversible capabilities `place_purchase_order` (L = order value) and `cancel_shipment` (L = penalty clause), and a reversible `reschedule` with a declared `revert`. That is seven coordinates in F, one projection constraint, two gated capabilities, and no weights. The kernel can run on it immediately, and the frontier it reports is the frontier of that problem.

## A3. Python package: layout and the `adapt()` wrapper

The package is one protected directory the builder never edits after install, one open directory the kernel rewrites, and one public function; everything a user or a coding agent touches is the public function and the adapter.

```
harness_kernel/
  __init__.py              # exports adapt, ObjectiveSpec, Capability, SystemAdapter
  protected/               # κ_P — hashed at install; I1 rejects any diff here
    invariants.py          #   check_inv: I1..I7 (Section 9.2)
    gate.py                #   gate(), the only path to an irreversible invoke
    verify.py              #   isolated_verify(): fresh-context judge
    store.py               #   append-only versions; promote_or_rollback
    manifest.py            #   Capability declarations, frozen after confirm_manifest()
    hooks.py               #   the four hook signatures (I2)
    protocol.py            #   the six-stage self-modification protocol (Section 4)
  open/                    # κ_O — rewritable by K5 under Inv
    featurize.py           #   φ from decision points; default + plugin slots
    policy.py              #   π_θ with exploration floor
    router.py              #   contextual bandit over oracles
    optimizer.py           #   REINFORCE + Désidéri direction
    rewriter.py            #   proposal generation from ledger failures
    compact.py             #   context-management heuristics
    budget.py              #   per-step compute schedule
  ledger.py                # K3; append-only; separate file/DB from store
  wrap.py                  # adapt(); shim builders for common host shapes
  minimal_loop.py          # built-in host when none supplied
  plugins.py               # Protocols + registry (Section A5)
  builder/                 # the coding-agent pipeline (Sections A6–A8)
    phases.py, prompts/, gates.py, certificate.py
```

### A3.1 `adapt()` as a higher-order function

`adapt` takes a system (through its adapter), an objective spec, a list of oracles, and optionally an existing harness, and returns a callable agentic system. The existing harness is treated as a function of two arguments, a model client and a tool list, that returns a runnable loop; this is the shape nearly every agent framework reduces to, and the shim builders handle the three common deviations.

```python
HostFactory = Callable[[ModelClient, list[Tool]], Runnable]

def adapt(
    system: SystemAdapter,
    objectives: ObjectiveSpec,
    oracles: list[Oracle],
    host: HostFactory | None = None,
    plugins: Iterable[Plugin] = (),
    settings: Settings = Settings(),
) -> AgenticSystem:
    manifest  = confirm_manifest(system.capabilities())        # protected: freezes effect/loss_scale
    kernel    = Kernel.install(objectives, manifest, oracles, settings)   # hashes κ_P, writes store v0
    for p in plugins:
        kernel.register(p)                                     # goes through Inv like any κ_O change
    tools     = [kernel.wrap_tool(c, system.invoke) for c in manifest]    # invoke sites now gate-dominated
    client    = kernel.wrap_client(oracles)                    # every model call → before_decision("model")
    runnable  = (host or minimal_loop)(client, tools)
    return AgenticSystem(runnable, kernel, system)
```

Three properties of this function matter for the proofs. `confirm_manifest` is in κ\_P and refuses to run unless every non-idempotent capability carries `confirmed_by` a human identity; that is Theorem A.1's premise. `wrap_tool` is the only constructor of a tool the host can see, so every path from the host to `system.invoke` passes through a kernel-owned call site, which is what makes I3 checkable on the composed system rather than only on the kernel. `wrap_client` is the only model client the host receives, so routing and budget apply even to a host that exposes no other hooks.

### A3.2 What `AgenticSystem` exposes

| Method | Purpose |
| --- | --- |
| `run(task) -> Outcome` | One episode; records to the ledger; triggers learning per Section 9.3 |
| `frontier() -> FrontierEstimate` | Current estimate of F at the live state plus the archive of non-dominated states seen, with CIs |
| `certificate() -> Certificate` | The Section A11 document, regenerated from the ledger and store |
| `propose_now()` | Force a K5 proposal cycle (for staging) |
| `pin(version)` | Operator rollback to any stored version |
| `extend(plugin)` | Register a plugin; same path as `register` at install |
| `reinstall(new_protected)` | The only way to change κ\_P; requires a fresh install hash and re-runs confirm\_manifest |

## A4. Core code

The code below is the load-bearing part of the package: the adapter protocol, the kernel's wrapping of tools and clients, the interposer, the optimizer step, and the protected gate and store. Section 9 of the main tab already gives `check_inv`, `isolated_verify` and `promote_or_rollback`; they are not repeated.

### A4.1 Adapter protocol and manifest (protected)

```python
# harness_kernel/protected/manifest.py
Effect = Literal["idempotent", "reversible", "irreversible", "unknown"]

@dataclass(frozen=True)
class Capability:
    name: str
    signature: dict                         # JSON schema for args
    effect: Effect
    loss_scale: float = 0.0
    revert: Callable[[dict, Any], None] | None = None
    observers: tuple[str, ...] = ()
    cost_model: Callable[[dict], np.ndarray] | None = None
    confirmed_by: str | None = None         # human identity; required unless idempotent

class SystemAdapter(Protocol):
    def capabilities(self) -> list[Capability]: ...
    def invoke(self, name: str, args: dict) -> Any: ...
    def observe(self) -> dict[str, float]: ...
    def reset(self) -> None: ...            # may raise NotSupported

def confirm_manifest(caps: list[Capability], L_max: float) -> tuple[Capability, ...]:
    out = []
    for c in caps:
        if c.effect == "unknown":
            c = replace(c, effect="irreversible", loss_scale=L_max)      # conservative default
        if c.effect != "idempotent" and not c.confirmed_by:
            raise UnconfirmedCapability(c.name)                            # Theorem A.1 premise
        if c.effect == "reversible" and c.revert is None:
            raise MissingRevert(c.name)
        out.append(c)
    return tuple(out)                        # frozen; hashed into κ_P
```

### A4.2 Kernel wrapping of tools and clients

```python
# harness_kernel/wrap.py
class Kernel:
    @classmethod
    def install(cls, objectives, manifest, oracles, settings):
        k = cls(objectives, manifest, oracles, settings)
        k.kappa_P_hash = hash_tree(PROTECTED_DIR) ^ hash(manifest)
        k.store.append(StoreEntry(version=0, theta=settings.theta0(len(oracles)),
                                  kappa_hash=k.kappa_hash(), parent=None, promoted_at=0, evidence={}))
        ok, code = check_inv(k.theta, k.kappa(), k.kappa_P, manifest, k.ledger, N=0)
        assert ok, f"initial state violates {code}"
        return k

    def wrap_tool(self, cap: Capability, invoke):
        def tool(**args):
            dp = DecisionPoint(kind="tool", features=self.featurize(cap, args),
                               side_effect=cap.effect, loss_scale=cap.loss_scale,
                               default=(cap.name, args))
            ov = self.before_decision(dp)                         # may set block / verify
            if ov.block:
                return Blocked(cap.name, reason="gate")           # host sees a tool error, not a side effect
            if cap.cost_model: self.budget.charge(cap.cost_model(args))
            result = invoke(cap.name, args)                        # the ONLY call into the system
            self.after_outcome(dp, result)
            return result
        tool.__name__, tool.__doc__ = cap.name, json.dumps(cap.signature)
        return tool

    def wrap_client(self, oracles):
        kernel = self
        class RoutedClient:
            def complete(self, messages, **kw):
                dp = DecisionPoint(kind="model", features=kernel.featurize_model(messages),
                                   side_effect="idempotent", loss_scale=0.0, default=None)
                ov = kernel.before_decision(dp)
                msgs = kernel.compact(messages) if len_tokens(messages) > kernel.theta.compact else messages
                out = oracles[ov.oracle].complete(msgs, max_tokens=ov.budget, **kw)
                kernel.after_outcome(dp, out)
                return out
        return RoutedClient()
```

### A4.3 Interposer and learning step (open)

```python
# harness_kernel/open/policy.py — rewritable under Inv
def before_decision(self, dp):
    self.budget.decrement()                                             # I4
    phi = dp.features
    a, logp = self.policy.sample(self.theta, dp.kind, phi, floor=self.settings.gamma_min)
    ov = Override(oracle=a.oracle, budget=a.budget, verify=a.verify)
    if dp.side_effect == "irreversible" or a.verify:
        spec = self.current_spec()                                        # task statement + manifest entry
        ok, conf = isolated_verify(spec, dp.default, self.oracles[self.theta.verifier_oracle])
        if dp.side_effect == "irreversible":
            ov = replace(ov, block=not gate(dp, conf, self.theta.gate, self.settings.delta_max))
    self.ledger.append(LedgerRecord(self.ep, self.step, dp, a, logp, ov, None,
                                    self.theta_hash(), self.kappa_hash()))                     # I5
    return ov

# harness_kernel/open/optimizer.py
def learning_step(self):
    batch = self.ledger.last_episodes(self.settings.BATCH)
    F_hat = self.objectives.evaluate(batch)                             # (J, -C_1, ..., -C_k), per episode
    grads = []
    for i in range(F_hat.shape[1]):
        b = F_hat[:, i].mean()                                            # baseline
        g = sum(rec.grad_logp(self.theta) * (F_hat[rec.ep, i] - b) for rec in batch.records)
        grads.append(g / len(batch))
    d = min_norm_convex_hull(grads)                                       # Désidéri direction
    theta_new = self.theta + self.settings.step(self.t) * d
    theta_new = self.objectives.project_bounds(theta_new, batch)         # 'bound' objectives
    theta_new.gate = min(theta_new.gate, self.settings.delta_max)         # never loosen past Inv
    self.theta = theta_new
    self.t += 1
    return float(np.linalg.norm(d))                                       # stationarity signal
```

### A4.4 Gate, store, and the protocol driver (protected)

```python
# harness_kernel/protected/gate.py
def gate(dp, confidence, theta_gate, delta_max) -> bool:
    if dp.side_effect != "irreversible": return True
    return confidence >= 1.0 - min(theta_gate, delta_max)

# harness_kernel/protected/protocol.py
def run_proposal(k, prop):
    ok, code = check_inv(k.theta, apply(k.kappa(), prop.diff), k.kappa_P, k.manifest, k.ledger, k.settings.N)
    if not ok:                        return k.store.log_rejection(prop, code)
    sh = shadow_run(k, prop, k.settings.S)
    if not dominates_by(sh, k.current_est(), k.objectives.ranking(), k.settings.eta):
                                      return k.store.log_rejection(prop, "shadow")
    with k.gate_pinned():             # θ_gate := 0 for the window
        ca, samples = canary_run(k, prop, k.settings.f, k.settings.N_canary)
    return promote_or_rollback(k.store, prop, sh, ca, k.current_est(), samples,
                               k.settings.eta, k.settings.eps_reg, k.settings.alpha)
```

The one subtlety in A4.3 is that `isolated_verify` is called on `dp.default`, the action the host *proposed*, before it runs; the verifier sees the task statement and the manifest entry for the capability, and nothing from the host's reasoning trace. That is the ρ → 0 condition of Design claim 6.6 realized in code, and it is why the kernel can judge a host it cannot read.

## A5. Extension and specialisation

Every way of specialising the kernel for a domain is a plugin implementing one of seven Protocols, registered into κ\_O and admitted through the same six-stage protocol as a self-proposed rewrite; the single rule that decides where a change belongs is whether it changes how the agent *acts* or how it is *judged*.

### A5.1 The acting / judging rule

| A change to… | Changes… | Lives in | Admitted by |
| --- | --- | --- | --- |
| Features, routing, compaction, budgets, verification prompts, proposal strategy, tool wrappers that add retries or caching | How the agent acts | κ\_O | Plugin registration → Inv → shadow → canary |
| The invariants, the gate threshold ceiling, the verifier's isolation, the manifest's effect classes, the promotion statistics, the store's write path | How the agent is judged | κ\_P | `reinstall()` with a human-confirmed manifest; never by the kernel, never by a plugin |

The rule is enforced, not advised: a plugin whose import graph reaches `harness_kernel.protected` for anything other than reading the manifest fails I1 at registration.

### A5.2 Plugin protocols

```python
# harness_kernel/plugins.py
class Featurizer(Protocol):            # domain features for φ
    def features(self, dp: DecisionPoint, obs: dict) -> np.ndarray: ...

class Compactor(Protocol):             # context management
    def compact(self, messages: list, budget_tokens: int) -> list: ...

class Verifier(Protocol):              # a domain judge: a simulator, a test suite, a schema check
    def judge(self, spec: Spec, proposed_action: Any) -> tuple[bool, float]: ...
    # The kernel composes it with isolated_verify as an AND; it can lower confidence, never raise it above the oracle's.

class Scalarizer(Protocol):            # how proposals are ranked (λ or Chebyshev); never the gradient
    def rank(self, F_candidates: np.ndarray, spec: ObjectiveSpec) -> np.ndarray: ...

class ProposalStrategy(Protocol):      # how K5 picks traces and frames the rewrite request
    def select_traces(self, ledger: Ledger, k: int) -> list[Trace]: ...
    def frame(self, kappa_O: Source, traces: list[Trace], history: list[Decision]) -> Prompt: ...

class BudgetSchedule(Protocol):        # compute per step as a function of difficulty estimate
    def budget(self, difficulty: float, remaining: int) -> int: ...

class ToolMiddleware(Protocol):        # wraps invoke; may retry, cache, rate-limit; may NOT change effect class
    def around(self, cap: Capability, invoke: Callable, args: dict) -> Any: ...

@dataclass(frozen=True)
class Plugin:
    name: str
    kind: Literal["featurizer", "compactor", "verifier", "scalarizer", "proposal", "budget", "middleware"]
    impl: object
    source: Source                     # the code; Inv runs on it
    claim: str                         # what it is for; recorded in the store
```

### A5.3 Registration path

```python
def register(self, p: Plugin):
    diff = self.kappa_O.diff_with_plugin(p)                  # the plugin is a κ_O change
    prop = Proposal(diff=diff, claim_sites=p.impl.touches(), predicted_direction=None)
    return run_proposal(self, prop)                          # Section A4.4: Inv → shadow → canary → promote
```

A plugin that only adds a Featurizer passes shadow trivially (it changes no decision until θ learns to use the new coordinates) and is promoted; the learning that follows is ordinary K4 work. A plugin that adds a Verifier must dominate in shadow, which it does when Design claim 6.6's inequality holds for the domain; the kernel is in effect checking that the simulator is worth its cost before trusting it. A ToolMiddleware that tried to reclassify a capability would fail I3 because the effect class is read from the frozen manifest, not from the middleware.

### A5.4 Composition of plugins

Several plugins of the same kind compose by a fixed rule per kind: Featurizers concatenate; Compactors chain in registration order; Verifiers AND their verdicts and take the minimum confidence; Scalarizers are exclusive (the latest registered wins, and the change is logged); ProposalStrategies are sampled by θ (another arm for the bandit); BudgetSchedules take the minimum; ToolMiddlewares nest in registration order around `invoke`. Each rule is monotone in the safety direction, which is what Theorem A.2 uses.

## A6. Builder pipeline

A coding agent converts an existing system into a kernelized agentic system by running eight phases, each of which is a prompt step that produces a candidate and a code gate that must pass before the next phase starts; the gates are deterministic programs, so the agent's judgment is used to propose and never to approve.

The hybrid is deliberate. Prompts do what oracles are good at: reading an unfamiliar API, drafting an adapter, naming plausible objectives, writing a test suite. Code does what oracles are not trusted for: type-checking the adapter against the Protocol, running idempotence tests, verifying Inv, computing confidence intervals, and refusing to mark anything "proved" that was not. A phase can be retried by the agent any number of times; it cannot be skipped.

| Phase | Prompt step produces | Code gate requires | Artifact |
| --- | --- | --- | --- |
| P0 Elicit objectives | Draft `ObjectiveSpec` from the user's answers (Section A2.3) or from the user's supplied spec | Schema-valid; every `max`/`min` objective has a computable `measure` over declared observers; every `inferred` objective shown to the user and confirmed or dropped | `objectives.json` |
| P1 Discover surface | Candidate capability list from OpenAPI/CLI help/SDK introspection/source, with proposed `effect` per capability | Every capability callable in a sandbox; every non-idempotent `effect` confirmed by a human; unknowns defaulted to irreversible | `manifest.json` (unconfirmed → confirmed) |
| P2 Generate adapter | `SystemAdapter` implementation, `revert` for reversibles, `cost_model` where cost is predictable | Type-checks against the Protocol; idempotents pass a repeat-invocation test; reversibles pass invoke→revert→observe round-trip; irreversibles are never invoked in the test | `adapter.py` + test report |
| P3 Compose | Chosen host (existing harness via shim, or minimal loop) and `adapt(...)` call | `confirm_manifest` passes; `check_inv` passes on (θ\_0, κ\_0); κ\_P hash recorded | `system.py`, store v0 |
| P4 Baseline | Task suite generated from the objectives and the manifest (sampled, with held-out split) | Suite covers every `max`/`min` objective with at least n\_min tasks; baseline F estimated with CI from held-out; A5 proxy gap estimated where ground truth exists | `tasks/`, `baseline.json` |
| P5 Learn in staging | Nothing; this phase is code only, running K4 and K5 against the suite with the system in a sandbox or replay mode | Stationarity: ‖d\_t‖ below τ for W consecutive windows, or budget exhausted; every promotion passed the full protocol | `store` v1…vn, `frontier.json` |
| P6 Certify | Narrative sections of the certificate (what the system does, what each objective means) | Every claim in the certificate is tagged proved / empirical / assumed by `certificate.py` from the store and ledger; no tag can be set by the prompt step | `certificate.md` |
| P7 Deploy | Runbook and handover notes | Canary on real traffic at fraction f with gate pinned; promotion test as in Theorem 7.2; rollback rehearsed once | `runbook.md`, live store head |

The gates P1 and P2 are where the pipeline refuses to be fully automatic: a human confirms every irreversible capability and its loss scale. The builder prompts for this with the question of Section A2.3 and blocks until it is answered. This is not a limitation to be engineered away; Proposition A.5 shows that a system whose irreversible effects are not declared cannot be made safe by any kernel, so the confirmation is the one input that must come from outside.

Total human input, for a system with c capabilities of which i are non-idempotent: the Section A2.3 answers (six questions), i confirmations with loss scales, and one approval at P7. Everything else is the agent's.

## A7. Pipeline at a glance

&#91;embedded content: builder pipeline · 8 phases, 8 gates, 3 human touchpoints\]

The prompt steps propose; the gates decide. P0, P1 and P7 are where the pipeline stops for a person: objectives in their own terms, confirmation of every irreversible capability, and approval to go live.

## A8. Prompt templates and code gates

Each phase is a pair: a prompt the builder sends to its oracle with the phase's inputs attached, and a gate function that takes the oracle's output and returns pass or a structured failure the next attempt is conditioned on. The templates are short because the inputs carry the detail; the gates are complete because they are the proof obligations.

### P0 — elicit objectives

```markdown
You are drafting an ObjectiveSpec for an agent that will operate the system described in
<system_description>. The user answered these questions: <answers>.
Produce a JSON ObjectiveSpec. Rules: every objective's `measure` must be an expression over the
observers listed in <observers>; mark anything the user did not state explicitly as provenance
"inferred"; do not assign weights; do not invent observers; include a `reference` only when the
user named a level. Output JSON only.
```

```python
def gate_P0(spec_json, observers, user_confirm) -> GateResult:
    spec = ObjectiveSpec.parse(spec_json)                              # schema
    for o in spec.objectives:
        if not measure_compiles(o.measure, observers): return fail("P0.measure", o.name)
        if o.weight is not None:                        return fail("P0.weight_assigned", o.name)
        if o.provenance == "inferred" and not user_confirm(o):          # shown in the user's words
            spec = spec.without(o)
    if not any(o.direction == "max" for o in spec.objectives): return fail("P0.no_success_measure")
    return ok(spec)
```

### P1 — discover the surface

```markdown
List every operation the system in <surface_dump> exposes. For each give: a stable name, a JSON
schema for its arguments, which observers in <observers> would reveal its effect, and your best
guess of its side-effect class with one sentence of evidence. Guess "unknown" when unsure.
Never guess "idempotent" for anything that writes, sends, pays, deletes, or schedules.
```

```python
def gate_P1(caps, sandbox, human) -> GateResult:
    for c in caps:
        if not sandbox.callable(c.name): return fail("P1.not_callable", c.name)
        if c.effect == "unknown": c = replace(c, effect="irreversible", loss_scale=L_MAX)
        if c.effect != "idempotent":
            answer = human.confirm(c)              # blocks; returns (effect, loss_scale, identity)
            c = replace(c, effect=answer.effect, loss_scale=answer.loss_scale, confirmed_by=answer.identity)
    return ok(caps)
```

### P2 — generate the adapter

```markdown
Implement `SystemAdapter` for the manifest in <manifest.json> against the client library in
<client_docs>. For every capability marked reversible, implement `revert`. For every capability
with a predictable cost, implement `cost_model` returning (tokens, seconds, usd, risk). Do not
change any `effect` or `loss_scale`. Do not call any irreversible capability in tests.
```

```python
def gate_P2(adapter_src, manifest, sandbox) -> GateResult:
    mod = sandbox.load(adapter_src)
    if not typecheck(mod.Adapter, SystemAdapter): return fail("P2.protocol")
    if manifest_hash(mod.Adapter().capabilities()) != manifest_hash(manifest): return fail("P2.manifest_drift")
    ad = mod.Adapter()
    for c in manifest:
        if c.effect == "idempotent":
            s0 = ad.observe(); ad.invoke(c.name, sample(c.signature)); s1 = ad.observe()
            ad.invoke(c.name, sample(c.signature)); s2 = ad.observe()
            if s1 != s2: return fail("P2.not_idempotent", c.name)
        elif c.effect == "reversible":
            s0 = ad.observe(); args = sample(c.signature); r = ad.invoke(c.name, args)
            c.revert(args, r)
            if ad.observe() != s0: return fail("P2.revert_incomplete", c.name)
        # irreversible: never invoked here
    return ok(mod)
```

### P3 — compose

No prompt; the builder chooses the host by inspecting the user's existing harness for the shapes in Section 9.4 and emits the `adapt(...)` call. Gate: `confirm_manifest` and `check_inv` as in A4.2 `Kernel.install`.

### P4 — baseline suite

```markdown
Write N tasks for the agent operating <system_description>, each a natural-language instruction
with a machine-checkable success condition over <observers>. Cover every objective in
<objectives.json> at least n_min times. Include tasks where the right answer is to refuse or to
ask (no capability should be invoked). Mark each task with the objectives it exercises.
```

```python
def gate_P4(tasks, spec, system, n_min) -> GateResult:
    cover = Counter(o for t in tasks for o in t.exercises)
    for o in spec.objectives:
        if o.direction != "bound" and cover[o.name] < n_min: return fail("P4.coverage", o.name)
    train, held = split(tasks, 0.7)
    F0, ci = estimate_F(system, held)                                   # Hoeffding CI, Theorem 7.2
    zeta = proxy_gap(system, held.with_ground_truth())                  # A5 where available
    return ok(BaselineReport(F0, ci, zeta, coverage=cover))
```

### P5 — learn in staging

Code only:

```python
def phase_P5(system, tasks, settings) -> FrontierReport:
    norms = deque(maxlen=settings.W)
    while system.kernel.budget_remaining():
        for t in tasks.sample(settings.BATCH): system.run(t)
        norms.append(system.kernel.learning_step())                     # Section A4.3
        if system.kernel.ledger.since_last_proposal() >= settings.PROPOSE_EVERY:
            system.propose_now()                                         # full six-stage protocol
        if len(norms) == settings.W and max(norms) < settings.tau: break  # Pareto-stationary (Thm 8.2)
    return system.frontier()
```

### P6 — certify

```markdown
Write the narrative sections of a certificate for the agentic system described by
<objectives.json>, <manifest.json>, <frontier.json>. Explain each objective and each gated
capability in plain language. Do not state that anything is proved, optimal, or safe; those
words are inserted by the certificate generator from evidence you cannot see.
```

```python
def gate_P6(narrative, store, ledger, spec) -> Certificate:
    cert = Certificate.from_evidence(store, ledger, spec)              # tags every claim; Theorem A.4
    cert.narrative = strip_claim_words(narrative)                       # removes proved/optimal/safe/guaranteed
    return cert
```

### P7 — deploy

Code only: `canary_run` on live traffic as in A4.4 with `gate_pinned`, then `promote_or_rollback`; the runbook is generated from the store's history and the settings table of Section 9.5.

## A9. Theorems IV: the composed system

The composed system reaches only declared states of the wrapped system, stays safe under any sequence of plugins and rewrites, converges toward the frontier of the user's own objectives at the Section 8 rate, and emits a certificate whose every "proved" tag is justified; the proofs reuse Sections 7–8 with the adapter manifest as the new piece of κ\_P.

### Theorem A.1 (Soundness over the wrapped system)

*Statement.* Let A = adapt(S, Ω, M, H) with confirmed manifest C. Then for every live episode and every state σ of S reached during it: (a) σ is reachable from σ\_0 in S using only capabilities in C; (b) every transition labelled by an irreversible capability was preceded by a `gate` call that returned true at confidence at least 1 − δ\_max; (c) every transition labelled by a reversible capability has a `revert` recorded in the ledger that was tested in P2; (d) no transition was labelled by a capability with `effect = unknown`.

*Proof.* (a) The host holds no reference to S; it holds only tools returned by `wrap_tool`, each closing over `system.invoke` with a fixed name from C. The adapter's `invoke` is the unique call into S (Definition A1.3). So every transition the composed system causes is labelled by a name in C. (b) `wrap_tool` constructs the only call sites to `invoke`, and each is preceded by `before_decision`, which for `side_effect = irreversible` calls `gate` and blocks on false; `side_effect` is read from the frozen manifest entry, which is in κ\_P. By Theorem 7.1(a) applied to the composed κ (whose protected region now includes C), this holds across every promotion. (c) `confirm_manifest` raises on a reversible without `revert`; gate P2 tests the round trip; the ledger records every tool decision by I5. (d) `confirm_manifest` rewrites every `unknown` to `irreversible` before the manifest is frozen, so no `unknown` entry exists at runtime. □

*What it does not say.* It does not say the agent reaches only *desirable* states; a confirmed irreversible capability can still be invoked wrongly with probability at most δ\_max per call. The theorem is that the operator's declaration, not the oracle's judgment, bounds what can happen.

### Theorem A.2 (Safety under extension)

*Statement.* For any sequence of plugin registrations and self-proposed rewrites, interleaved arbitrarily, every promoted state satisfies Inv, and the plugin composition rules of A5.4 preserve properties (a)–(d) of Theorem A.1.

*Proof.* A plugin registration is a Proposal whose diff lies in κ\_O by construction of `register` (A5.3); if its import graph reaches κ\_P other than to read the manifest, I1 rejects it. It then follows the same six stages as any rewrite, so Theorem 7.1's induction covers it: the promotion chain is a single sequence of Inv-satisfying states regardless of which proposals were plugins. For the composition rules: Featurizer concatenation changes φ only, which affects no invariant; Compactor chaining changes messages only; Verifier AND-with-minimum can only lower confidence, so any `gate` that returns true after a plugin would have returned true without it, preserving (b) in the conservative direction; Scalarizer replacement changes ranking only, which Theorem 6.5's consequence confines to proposal selection; ProposalStrategy sampling adds an arm to a bandit with an exploration floor, preserving A4; BudgetSchedule minimum preserves I4's bound; ToolMiddleware nesting wraps `invoke` but reads `effect` from the frozen manifest, so I3's taint analysis still finds the gate dominating every irreversible call site. □

### Theorem A.3 (Optimality transfer)

*Statement.* Let Ω have k\_max maximize objectives, k\_min minimize objectives, k\_b bound objectives, and the four default costs, and let F\_Ω be the resulting objective vector of Section A2.2. Under A1–A5 of Section 8, applied to F\_Ω, the composed system's scalarized regret against the local frontier of F\_Ω satisfies the bound of Theorem 8.5, and in addition every bound objective's ledger estimate violates its bound by at most ε\_reg plus the Theorem 7.2 deviation term, with probability at least 1 − k\_b α.

*Proof.* F\_Ω is a vector of expectations of bounded functions of the trajectory (R̂ ∈ \[0, 1\] for success measures; costs bounded by the I4 budget bound; observer-derived measures bounded by their declared ranges), so Lemma 8.1 gives unbiased gradients and Theorem 8.2 gives convergence to a Pareto-stationary point of the unconstrained coordinates. Bound objectives are handled by `project_bounds` in A4.3, which projects θ onto the set where the batch estimate of each bound holds; projected stochastic gradient methods retain the Ghadimi–Lan rate when the constraint set is closed and the projection is exact (Ghadimi, Lan and Zhang 2016), which holds here because the estimate is a mean of bounded samples and the feasible set in θ is closed by continuity of the policy. The violation bound follows from the same Hoeffding argument as Theorem 7.2 applied to each bound objective's estimate over the batch. Theorem 8.5's other terms are unchanged, since nothing in the adapter alters the oracle, the drift, or the proxy gap. □

*Consequence for "Pareto optimal across any given task."* The frontier the system approaches is the frontier of F\_Ω for the task distribution D the suite and the live traffic define. Change Ω or D and the frontier moves; the system tracks the move at the A1 drift rate. The guarantee is per (Ω, D), not universal, and Theorem 6.1 says that is the only kind there is.

### Theorem A.4 (Correctness of the boundary certificate)

*Statement.* Let the certificate be generated by `Certificate.from_evidence` (A11). Every claim tagged *proved* is a theorem of this document instantiated with values read from the store and settings; every claim tagged *empirical* carries a confidence interval computed from ledger samples at the stated level; every claim tagged *assumed* names a condition from Section 8's table or Section A2.3's confirmations that was not verified by code. No claim carries a tag it was not entitled to, and the prompt step of P6 cannot change any tag.

*Proof.* By inspection of the generator, which is in κ\_P and so unchanged from install (Theorem 7.3). It emits exactly three kinds of lines: instantiations of Theorems 7.1, 7.2, 7.3, 7.5, A.1, A.2 with parameters (δ\_max, f, N, α, ε\_reg, k) substituted, tagged *proved*, each of which is a theorem whose hypotheses are checked by code (Inv passes, manifest confirmed, settings in range); interval estimates of F\_Ω, the hypervolume dominated by the archive, ζ where ground truth exists, and the stationarity norm, each tagged *empirical* with the Hoeffding width at level α; and the A1–A5 conditions plus each `confirmed_by` entry, tagged *assumed*, with the identity or the reason the condition is not code-checkable. Gate P6 passes the narrative through `strip_claim_words`, which removes the tag vocabulary from oracle-written text, so the only occurrences of *proved*, *optimal*, *safe*, *guaranteed* in the certificate are the generator's. □

*The boundary of optimality, stated by the certificate.* The certificate's last section is fixed text instantiated with numbers: the local frontier reached, its hypervolume with CI; the regret bound O(√T log T) + ζT with ζ's estimate; and for each stronger claim a reader might want, the obstruction from Section 6 that blocks it (global frontier: Corollary 8.3's convexity failure; zero regret: Proposition 6.4; correctness of outputs: Proposition 6.2; universality across tasks: Theorem 6.1). This is what "prove the boundary of optimality" means operationally: not a proof that the boundary is where it is, but a proof of why no proof can move it further.

### Proposition A.5 (Generality and its one hard limit)

*Statement.* (a) Any system expressible as Definition A1.1 with at least one observer admits an adapter for which Theorems A.1–A.4 hold. (b) A system with an irreversible effect reachable through a capability not in C, or through a capability in C whose effect is misdeclared as idempotent or reversible, admits no kernel satisfying Theorem A.1(b) for that effect. (c) A system with no observers admits an adapter for which Theorem A.1 holds but F\_Ω contains only the four default costs, so Theorem A.3 is vacuous for quality.

*Proof.* (a) Construct the adapter with every capability marked `unknown`; `confirm_manifest` makes them all irreversible with L\_max; every call is gated; Theorems A.1–A.4 hold trivially (the system is safe and slow, and P1–P2 then earn back permissiveness by establishing classes). (b) The kernel observes S only through `observe` and acts only through `invoke`; an effect outside C is invisible to I3's taint analysis, and a misdeclared effect is read as declared. No static or dynamic check inside the kernel can detect either, since both are consistent with every observation the kernel can make. (c) With no observers, no `max`/`min` measure compiles (gate P0 fails for them), leaving the defaults. □

*Consequence.* The human confirmation at P1 is the irreducible input. Everything else degrades gracefully; this does not.

## A10. Worked example: a scheduling service

An existing REST scheduling service with five endpoints becomes a kernelized agent in about thirty lines of user code; everything below the `adapt` call is the package's.

```python
from harness_kernel import adapt, Capability, SystemAdapter, ObjectiveSpec, Objective
import sched_client as sc                        # the existing system's SDK

class SchedAdapter(SystemAdapter):
    def capabilities(self):
        return [
            Capability("list_jobs",   sc.schemas.list_jobs,   "idempotent"),
            Capability("get_capacity", sc.schemas.capacity,   "idempotent"),
            Capability("reschedule",  sc.schemas.reschedule,  "reversible",
                       revert=lambda args, r: sc.reschedule(args["job"], r["previous_slot"]),
                       confirmed_by="ops-lead@example"),
            Capability("cancel_job",  sc.schemas.cancel,      "irreversible", loss_scale=2500.0,
                       confirmed_by="ops-lead@example"),
            Capability("notify_customer", sc.schemas.notify,  "irreversible", loss_scale=400.0,
                       confirmed_by="ops-lead@example"),
        ]
    def invoke(self, name, args):  return getattr(sc, name)(**args)
    def observe(self):             return sc.metrics()      # {"on_time_rate": .., "idle_hours": .., "complaints": ..}
    def reset(self):               raise NotSupported

spec = ObjectiveSpec(
    objectives=[
        Objective("on_time_rate",  "mean(obs.on_time_rate)", "max", unit="ratio", reference=0.95),
        Objective("idle_hours",    "sum(obs.idle_hours)",    "min", unit="h"),
        Objective("complaints",    "sum(obs.complaints)",    "bound", bound=(None, 3)),
    ],
    horizon=200, risk_loss_max=5000.0,
)

agent = adapt(SchedAdapter(), spec, oracles=[cheap_model, strong_model],
              host=my_existing_react_loop)          # or None for the built-in loop

agent.run("Clear tomorrow's backlog without pushing any job past its SLA")
print(agent.frontier())
print(agent.certificate().summary())
```

What happens on that first `run`: every `cancel_job` or `notify_customer` the host proposes is verified in a fresh context against the task statement and gated at 1 − δ\_max; `reschedule` is logged with its `revert`; the two oracles are routed by a bandit that starts uniform; and after 32 episodes the first gradient step moves θ. After 500 episodes the first rewrite proposal is drafted from the worst traces, checked against I1–I7, shadowed, canaried on 5% of runs with the gate pinned, and promoted or rolled back. The operator's total involvement was the three `confirmed_by` entries and the two loss scales.

What the certificate says after staging, in the generator's own tags: *proved* that no cancellation or notification ran ungated, that resource use stayed within budget, that every version is recoverable; *empirical* the on-time rate interval, the idle-hours interval, the hypervolume gained over baseline, and the complaints estimate against its bound of 3; *assumed* that the task suite's distribution matches live traffic (A1), that the verifier oracle is calibrated, and that ops-lead's loss scales are right.

## A11. Deliverables and the certificate format

The builder leaves behind seven files; the certificate is the one a reader who was not present should open first, and its structure is fixed so that two certificates from two systems can be compared line by line.

| File | Produced in | Contents |
| --- | --- | --- |
| `objectives.json` | P0 | The confirmed ObjectiveSpec with provenance per objective |
| `manifest.json` | P1 | Capabilities with effect, loss scale, and `confirmed_by` identities |
| `adapter.py` | P2 | The SystemAdapter implementation and its test report |
| `system.py` | P3 | The `adapt(...)` composition, host choice, plugin list, install hash of κ\_P |
| `tasks/` + `baseline.json` | P4 | Task suite with held-out split; baseline F with CIs; ζ estimate |
| `frontier.json` | P5 | Archive of non-dominated states with F estimates, hypervolume, stationarity trace, promotion log |
| `certificate.md` | P6 | The tagged claims document below |
| `runbook.md` | P7 | Settings, rollback procedure, how to reinstall κ\_P, how to add an oracle or a plugin |

### Certificate structure

```markdown
# Certificate: <system name>, store head v<n>, <as-of date>

## 1. What the system does            (narrative; claim words stripped)
## 2. Objectives                       (one row per objective: measure, direction, reference, provenance)
## 3. Capabilities                     (one row per capability: effect, loss scale, confirmed_by)

## 4. Proved
- [Thm 7.1a / A.1b] No irreversible capability ran ungated; δ_max = 0.01.                    PROVED
- [Thm 7.1b]        Calls per episode ≤ <budget>.                                            PROVED
- [Thm 7.3]         Protected region unchanged since install; hash <h>.                      PROVED
- [Thm 7.2]         Each of <n> promotions regressed ≤ ε_reg + <dev> per objective, p ≥ 1−kα. PROVED
- [Thm A.2]         <m> plugins admitted through the full protocol.                          PROVED

## 5. Empirical  (level α = 0.05, Hoeffding)
- on_time_rate ∈ [0.941, 0.958]  (baseline [0.902, 0.921])
- idle_hours   ∈ [38.1, 44.6]    (baseline [61.0, 70.2])
- complaints   ∈ [1.2, 2.7]      bound ≤ 3
- hypervolume vs. reference point: 0.413 → 0.571
- stationarity ‖d_t‖ over last W windows: max 0.018 (τ = 0.02)
- proxy gap ζ (on <k> ground-truth tasks): [0.00, 0.03]

## 6. Assumed
- A1: live traffic ~ task suite distribution.                      (not code-checkable)
- Verifier oracle calibration.                                      (external audit; last <date>)
- Loss scales: cancel_job 2500, notify_customer 400.                (confirmed_by ops-lead@example)

## 7. Boundary of optimality
The state at v<n> is Pareto-stationary for F_Ω on the task suite (Thm 8.2). It is not shown to be
globally Pareto-optimal: F_Ω is non-concave in θ (Cor. 8.3 caveat). Regret against the local
frontier is O(√T log T) + ζT (Thm 8.5); no kernel achieves o(√T) (Prop. 6.4). Output correctness
is not certified (Prop. 6.2). The frontier is specific to (Ω, D) (Thm 6.1).
```

Sections 4–7 are written by `Certificate.from_evidence` only. Section 1 is the oracle's, after `strip_claim_words`. A certificate whose sections 4–7 were edited by hand no longer satisfies Theorem A.4, and the generator detects this on regeneration by hashing those sections against the store.
