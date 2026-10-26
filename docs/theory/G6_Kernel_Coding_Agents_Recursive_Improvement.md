# Coding agents: a recursive-improvement harness

> **Status: design rationale.** The proofs are not machine-checked, and the design is not validated. Nothing here is evidence that G6 works; where this document and the library's own documentation differ, the library's documentation describes what G6 does.

Oct 18, 2026

## C0. What changes for coding agents

Coding is the one domain where the kernel's three hardest requirements are nearly free: every workspace write is reversible through git, the verifier can be a mechanical test run with zero correlation to the producer, and the system the agent operates (a repository plus a toolchain) is one the harness can wholly contain in a sandbox. Those three facts tighten every bound in Sections 7–8 and make one new thing possible: the coding agent can be the kernel's rewriter, improving the harness it runs under, with the recursion provably a monotone ratchet rather than an open loop.

The design here sits on top of an *arbitrary* coding agent: a CLI, an SDK, an IDE extension, or a cloud service run by its vendor. It does not need the agent's hooks, source, or cooperation, because it interposes at the environment rather than at the agent: the harness owns the shell the agent runs commands in, the filesystem it writes to, the network it reaches, and the model endpoint it calls. The agent sees an ordinary workspace; the kernel sees every decision point.

Recursive improvement is then two repositories under one kernel. The user's repository is the task; the harness's own repository holds κ\_O (routing features, compaction, verification prompts, budget schedules, command classifiers, skills and memory files). Improvement tickets are generated from ledger failures, the coding agent works them under the *current promoted* harness in a worktree of the harness repo, and its pull request is a Proposal that passes Inv, shadow and canary before merge. Theorem C.5 is the statement that the candidate harness never participates in judging itself; Theorem C.4 is that the archive's hypervolume is non-decreasing with high probability per promotion and converges; Proposition C.6 is that the improvement rate is bounded by the statistics the operator sets, so the recursion is fast exactly as fast as the evidence allows and no faster.

*Optimal* keeps the Section 8 meaning. What is new is quantitative: because tests give ρ = 0 and an estimable ε\_v, the proxy gap ζ in Theorem 8.5 becomes a measurable quantity (mutation score) rather than an assumption, which is the difference between a certificate that says "assumed" and one that says "empirical" on the line that matters most. Sections C11–C14 add the cost side: a distillation ladder that moves repeated workflows off frontier LLM calls onto fewer calls, compiled code, a small local model, or a custom ML model, under the same protocol and with a provable cost floor.

## C1. The coding agent as a black box

Every coding agent in use reduces to one of four interface shapes, and all four can be interposed at the environment; the harness therefore assumes nothing about the agent except that it runs processes, touches files, opens sockets and calls a model.

| Shape | Examples of the shape | What the harness can see directly | What interposition adds |
| --- | --- | --- | --- |
| CLI with a prompt argument | `agent -p "task"` in a working directory | Exit code, stdout, final diff | Every command, file write, network call, model call |
| SDK or agent-loop library | Python/TS loop with pluggable model client and tool list | Section 9.4 hooks via shims | Same, plus the environment layer as defence in depth |
| IDE extension or desktop app | Runs the agent against a local workspace | The workspace | Shell, filesystem and network in the workspace's container; model endpoint via proxy settings |
| Vendor cloud service | Agent runs in the vendor's sandbox against a repo | PRs, CI results | Only the repository boundary: the PR is the proposal; CI is the verifier; pushes are gated at the remote |

### C1.1 Environment interposition

The agent runs inside a container the harness builds for each episode. Four seams are owned by the harness:

1. **Process execution.** `PATH` resolves to harness shims for every binary; `git`, `rm`, `curl`, `npm`, `pip`, `docker`, `kubectl`, `aws`, and the package managers and deploy tools in the manifest are shims that classify the invocation (Section C3), raise `before_decision(kind="tool")`, and either exec the real binary or return a blocked error. Unknown binaries are shimmed by a catch-all that classifies by effect on observable state. Where the platform supports it, a seccomp or sandbox profile makes the shims the only route; where it does not, the shim layer plus a read-only mount of everything outside the workspace is the fallback and the certificate says so.
2. **Filesystem.** The workspace is a git worktree at a known baseline commit; everything outside it is read-only. Writes inside are reversible (Theorem C.2); writes outside are impossible rather than gated.
3. **Network.** An egress proxy allows the model endpoint, the package registries in the manifest, and the git remote; every other destination is a decision point with effect `unknown`, which Section A1.2 makes `irreversible`.
4. **Model endpoint.** The agent is configured to call a local proxy; the proxy is `wrap_client` of A4.2, so routing, budget and compaction apply to an agent whose model calls the harness could not otherwise see.

The vendor-cloud-service row is the degenerate case: only seams 2 (at the repository) and 3 (at the remote) are available, so the harness is a PR gate and a CI verifier. That is still enough for Theorem A.1 on the repository and for Proposition 8.4 on routing between vendors; it is not enough for budget control inside an episode, and the certificate records the difference.

### C1.2 What the agent sees

Nothing unusual. A workspace, a shell, a model that answers. A blocked command returns a non-zero exit with a one-line reason the agent can read and act on ("blocked: `git push` to a protected branch requires confidence 0.99; current 0.91; run the tests or ask"), which turns the gate from a wall into a signal the agent learns to pre-empt. A model call that was routed to a cheaper oracle looks like a model call. The agent's own prompts, skills and memory files are part of κ\_O and are mounted into the workspace from the harness repository at the promoted version, which is how prompt-level improvements reach the agent without the agent knowing they changed.

## C2. Interposition at a glance

&#91;embedded content: environment interposition · 4 seams between any coding agent and the world\]

Every route from the agent to external state passes through a seam the harness owns; each seam raises a decision point and acts on the kernel's verdict. The agent's internals are never read.

## C3. Capability classes for coding

A coding agent's actions are shell commands, and a shell command's side-effect class is decided by where its effects land: inside the worktree (reversible by git), on the local toolchain cache (idempotent or reversible), outside the workspace (impossible), on a remote (irreversible unless the remote is harness-owned), or on the network (unknown). The classifier is in κ\_P because the classes are what the gate reads; its rules are data the operator can tighten.

| Class | Decided by | Examples | Effect | Revert |
| --- | --- | --- | --- | --- |
| Read | No write syscalls; no network | `cat`, `grep`, `ls`, `git log`, `git diff`, type-checkers in check mode | idempotent | none needed |
| Workspace write | Writes only under the worktree root | editors, formatters, `git add`, `git commit`, `git checkout -b`, test runners that write caches | reversible | `git reset --hard <baseline> && git clean -fdx` |
| Toolchain cache | Writes only under declared cache dirs | `npm install`, `pip install` into the venv, `cargo build` | reversible | restore cache snapshot or re-run from lockfile |
| Local destructive | Deletes or overwrites outside the worktree | `rm -rf ~`, writes to `/etc`, `sudo` | impossible | mount is read-only; the shim refuses before the gate |
| Remote write | Mutates a remote the harness does not own | `git push` to a shared branch, `gh pr merge`, `npm publish`, `docker push`, `terraform apply`, `kubectl apply` | irreversible, L declared per remote | none |
| Harness-owned remote | Mutates a remote the harness controls | `git push` to the agent's own fork or to a `harness/*` branch | reversible | force-reset the branch |
| Network, other | Any egress not in the manifest allowlist | `curl` to an unlisted host, webhooks, email, payments | unknown → irreversible, L = L\_max | none |

Three consequences shape the harness. First, the overwhelming majority of a coding agent's actions are Read or Workspace write, so the gate fires rarely and the agent runs at nearly the host's native speed; the irreversible class is small and well-defined. Second, `git` is the revert mechanism for the dominant class, so the kernel needs no per-tool `revert` implementations for editing: Theorem C.2 covers all of them at once. Third, the Remote write class is where the loss lives, and it is a short list the operator can confirm in minutes at Phase P1.

The classifier runs before the command executes, on the command line plus a dry-run where the tool supports one (`git push --dry-run`, `terraform plan`, `npm publish --dry-run`). Where no dry-run exists and the class is ambiguous (`make deploy`), the shim classifies by the Makefile target's transitive commands if it can resolve them, and as `unknown` if it cannot. Ambiguity resolves toward the gate, never away from it.

## C4. Objectives and the verification stack

For coding tasks the success measure can be mechanical, the verifier can be independent, and the proxy gap can be estimated; this section fixes F\_code and the verification stack that makes those three things true.

### C4.1 F\_code

| Objective | Measure | Direction | Source |
| --- | --- | --- | --- |
| Task success | Acceptance tests pass AND isolated review accepts | max | Test runner + Verifier |
| Regressions | Previously passing tests now failing | bound ≤ 0 | Test runner on the pre-existing suite |
| Type / lint cleanliness | Type-check and lint errors introduced | bound ≤ 0 (or min) | Toolchain in check mode |
| Diff churn | Lines changed beyond the minimal accepted diff | min | `git diff --numstat` against the merged result |
| Review cost | Reviewer-model tokens to accept | min | Verifier ledger |
| Tokens, wall-clock, dollars, risk | As in Section 1 | min | Kernel defaults |

Task success is a conjunction on purpose. Tests alone are gamed by tests the agent wrote; review alone is an oracle judging an oracle. Together, with the review in a fresh context, the false-success rate is the product of two nearly independent rates.

### C4.2 The verification stack, in order of cost

1. **Toolchain checks** (type-check, lint, build): mechanical, ρ = 0, ε\_v ≈ 0 for what they check. Always run; cost is seconds.
2. **Pre-existing test suite**: mechanical, ρ = 0, ε\_v = the suite's blind-spot rate. Always run; this is the regression bound's observer.
3. **Acceptance tests written by an isolated oracle** from the task statement alone, *before* the agent's diff is visible to it: ρ ≈ 0 because the test author never saw the implementation; ε\_v = the test author's own error, estimated from the tests' agreement with human-written tests on a held-out set. Run when the task has no acceptance tests of its own.
4. **Isolated review**: an oracle given the task statement and the diff, no trace. ρ ≈ 0 by construction (Design claim 6.6); ε\_v is the reviewer's miss rate, calibrated against human review on held-out PRs. Run on every irreversible action and with probability θ\_verify otherwise.
5. **Mutation testing** on the merged result: inject faults, measure how many the combined test suite catches. The kill rate is the empirical estimate of 1 − ε\_v for layers 2–3, and therefore of the proxy gap ζ in Theorem 8.5. Run per promotion, not per episode; it is the most expensive layer and the only one that measures the others.

### C4.3 Why this closes the proxy-gap assumption

In Section 8, A5 (bounded proxy gap) is listed as an assumption because in general nothing measures how far R̂ is from R. In coding, R̂ is "tests pass and review accepts," R is "the change is correct," and the gap is the probability that an incorrect change passes both. Mutation testing estimates the first factor directly, and reviewer calibration on held-out PRs estimates the second. The certificate therefore moves the A5 line from *assumed* to *empirical*, with an interval. Theorem C.3 states the resulting bound. This is the single largest tightening the coding specialisation buys, and it is why the recursive loop can be trusted to optimise what it measures: the measurement is itself under measurement.

## C5. Recursive improvement

The coding agent is the kernel's rewriter: it works improvement tickets against the harness's own repository, under the current promoted harness, and its pull request is a Proposal; the recursion is safe because the candidate never runs anything that judges it, and it is bounded because every promotion must earn its evidence.

### C5.1 Two repositories, one kernel

| Repository | Holds | Who writes | Governed by |
| --- | --- | --- | --- |
| User repo(s) | The tasks the agent is for | The agent, in episodes | The promoted harness, version v\_n |
| Harness repo, `open/` | κ\_O: featurizers, compactors, verification prompts, budget schedules, command-classifier *rules*, skills, memory files, proposal strategies | The agent, in improvement episodes; humans | The promoted harness v\_n, with I1 refusing any diff outside `open/` |
| Harness repo, `protected/` | κ\_P: invariants, gate, verifier isolation, store, manifest, classifier *engine*, protocol | Humans only, via `reinstall()` | Install hash; Theorem 7.3 |

The classifier appears twice on purpose: its rules (which binaries map to which class, which hosts are allowlisted) are data the agent may propose changes to, and those changes go through the full protocol; its engine (how rules are applied, the unknown-to-irreversible default, the dry-run logic) is protected. A proposal that reclassifies `git push` as reversible is a rules change; it passes I1, and then fails shadow and canary because the ledger shows pushes to shared branches that could not be reverted, so it is rejected on evidence rather than on syntax. A proposal that disables the unknown-default is an engine change and fails I1.

### C5.2 Improvement tickets

Every PROPOSE\_EVERY episodes, K5 reads the ledger's worst-k traces under the operator's ranking (Chebyshev against the references, or λ) and writes tickets, each a task statement in the same form as a user task: a title, the failing traces, the objectives it should move, and the files in `open/` it is allowed to touch. Tickets are typed by the kind of failure they came from:

| Failure pattern in the ledger | Ticket type | Typical `open/` target |
| --- | --- | --- |
| High tokens, success unchanged, long contexts | Compaction | `compact.py`, context-budget rules |
| Gate blocks that review later judged safe | Classifier or verifier precision | classifier rules, verification prompts |
| Routing to the dear oracle on steps the cheap one passes in shadow | Routing features | `featurize.py` |
| Repeated failure mode across tasks (same lint error, same API misuse) | Skill or memory | `skills/`, `memory/` files |
| Budget exhaustion before a halt | Budget schedule | `budget.py` |
| Proposals that keep failing the same stage | Proposal strategy | `rewriter.py`, ticket templates |

The last row is the second level of recursion: a ticket about how tickets are written. It is an ordinary ticket, in `open/`, under the same protocol.

### C5.3 The candidate never judges itself

Four rules, all enforced in κ\_P:

1. An improvement episode runs under the promoted harness v\_n, in a worktree of the harness repo at v\_n. The candidate v′ exists only as a diff until promotion.
2. Shadow runs v′ with overrides disabled, on user-repo episodes governed by v\_n. v′ observes; v\_n acts.
3. Canary runs v′ on a fraction f of user-repo episodes with the gate pinned to its minimum. It never runs v′ on improvement episodes: a candidate harness is never the harness under which the next candidate is drafted or evaluated.
4. The verifier oracle for a proposal's shadow and canary statistics is `isolated_verify` from κ\_P, called by v\_n. The proposal's own verification prompts, if it changed them, take effect only after promotion.

Theorem C.5 is the formal statement; the informal one is that the recursion has a strict generation order, and generation n+1 is judged entirely by generation n and by mechanical tests. This is what distinguishes the design from a self-modifying agent that evaluates its own edits in its own context, where ρ → 1 and Design claim 6.6 says the evaluation is worth nothing.

### C5.4 Three levels

| Level | What improves | Where it lives | How its value is measured |
| --- | --- | --- | --- |
| 0 | The agent's work on user repos | Episodes | F\_code per episode |
| 1 | The harness's acting components | `open/`, excluding `rewriter.py` and ticket templates | Hypervolume gained per promotion |
| 2 | How level-1 proposals are generated | `rewriter.py`, ticket templates, proposal strategies (bandit arms) | Promotion rate and hypervolume gain per proposal, per strategy |

There is no level 3 by construction: a change to how levels are judged is a change to κ\_P. Level 2's value is a bandit signal (Section 5), so the kernel learns which proposal strategies produce promoted changes and samples them more; that is the whole of the meta-learning, and it lives in the open region under the same guarantees as everything else.

## C6. The recursion at a glance

&#91;embedded content: recursive improvement · 8 stages, each annotated with which version runs it\]

The candidate v′ appears in only two stages, shadow and canary, and in neither does its output enter the promotion decision except as the thing being measured. Everything that judges runs at vₙ, whose protected region is the installed one.

## C7. Code

Five pieces specialise the package of Section A3 to coding: the sandbox builder, the command shim and classifier, the git-backed adapter, the model-endpoint proxy, and the improvement loop. Everything else is inherited.

### C7.1 Sandbox and shims (protected)

```python
# harness_kernel/coding/protected/sandbox.py
@dataclass(frozen=True)
class Sandbox:
    worktree: Path            # git worktree at baseline commit
    cache_dirs: tuple[Path, ...]
    allow_hosts: tuple[str, ...]
    model_proxy: str          # http://127.0.0.1:<port>

def build_sandbox(repo: Path, baseline: str, manifest, settings) -> Sandbox:
    wt = git_worktree_add(repo, baseline)
    shim_dir = install_shims(manifest.binaries(), target=wt / ".harness/bin")
    env = {"PATH": f"{shim_dir}:{settings.real_path}",
           "HTTPS_PROXY": settings.egress_proxy, "HTTP_PROXY": settings.egress_proxy,
           settings.model_env_var: f"http://127.0.0.1:{settings.model_port}"}
    mount_read_only_outside(wt, except_=manifest.cache_dirs())
    apply_sandbox_profile(settings.profile)        # seccomp / platform sandbox when available
    return Sandbox(wt, manifest.cache_dirs(), manifest.allow_hosts(), env[settings.model_env_var])

# .harness/bin/<binary>  — every shim is this script with BIN set
# #!/bin/sh
# exec harness-shim "$BIN" "$@"

def harness_shim(binary: str, argv: list[str]) -> int:
    cls = classify(binary, argv, cwd=Path.cwd(), rules=load_rules())      # C7.2; rules are κ_O data
    dp = DecisionPoint(kind="tool", features=featurize_cmd(binary, argv, cls),
                       side_effect=cls.effect, loss_scale=cls.loss_scale,
                       default=(binary, argv))
    ov = kernel.before_decision(dp)
    if ov.block:
        sys.stderr.write(f"blocked: {cls.reason}; confidence {ov.confidence:.2f} < {1-kernel.delta():.2f}\n")
        return 77
    rc = os.spawnvp(os.P_WAIT, real_binary(binary), [binary, *argv])
    kernel.after_outcome(dp, {"rc": rc, "observers": observe_toolchain()})
    return rc
```

### C7.2 Command classifier (engine protected; rules open)

```python
# harness_kernel/coding/protected/classify.py
@dataclass(frozen=True)
class Classification:
    effect: Effect; loss_scale: float; reason: str

def classify(binary, argv, cwd, rules) -> Classification:
    if (r := rules.exact_match(binary, argv)):
        return r                                                   # operator / promoted rule
    if binary == "git":
        if argv[:1] == ["push"]:
            remote, branch = resolve_push_target(argv, cwd)
            if rules.is_harness_owned(remote, branch): return Classification("reversible", 0, "push to owned branch")
            return Classification("irreversible", rules.loss(remote, branch), f"push to {remote}/{branch}")
        return Classification("reversible", 0, "git in worktree")
    if binary in rules.publishers:              # npm publish, docker push, terraform apply, kubectl apply ...
        return Classification("irreversible", rules.loss(binary), f"{binary} publishes")
    if touches_network(binary, argv) and not all_hosts_allowed(argv, rules.allow_hosts):
        return Classification("irreversible", rules.L_MAX, "unlisted network destination")
    if writes_outside(binary, argv, cwd, rules.worktree, rules.cache_dirs):
        return Classification("irreversible", rules.L_MAX, "write outside workspace")   # mount refuses anyway
    if writes_inside(binary, argv, cwd, rules.worktree, rules.cache_dirs):
        return Classification("reversible", 0, "workspace write")
    if (dry := rules.dry_run_for(binary, argv)) is not None:
        return classify_from_dry_run(run(dry))
    if is_known_readonly(binary, argv):
        return Classification("idempotent", 0, "read-only")
    return Classification("irreversible", rules.L_MAX, "unknown effect")  # the conservative default
```

### C7.3 Git-backed adapter

```python
# harness_kernel/coding/adapter.py
class RepoAdapter(SystemAdapter):
    def __init__(self, sandbox: Sandbox, baseline: str, toolchain: Toolchain):
        self.sb, self.baseline, self.tc = sandbox, baseline, toolchain
    def capabilities(self):
        # one capability per shimmed binary; effect decided per call by classify(), so declare the ceiling
        return [Capability(b, ARGV_SCHEMA, "unknown") for b in self.sb.binaries()] + [
            Capability("run_tests", {}, "idempotent"), Capability("typecheck", {}, "idempotent"),
            Capability("revert_workspace", {}, "idempotent")]
    def invoke(self, name, args):
        if name == "revert_workspace": return self.revert()
        return run_in_sandbox(self.sb, name, args)
    def observe(self):
        return {"tests_passing": self.tc.test_pass_rate(), "regressions": self.tc.regressions(self.baseline),
                "type_errors": self.tc.type_errors(), "lint_errors": self.tc.lint_errors(),
                "diff_lines": git_numstat(self.sb.worktree, self.baseline)}
    def revert(self):                                                  # Theorem C.2
        git(self.sb.worktree, "reset", "--hard", self.baseline); git(self.sb.worktree, "clean", "-fdx")
    def reset(self): self.revert()
```

### C7.4 Improvement loop

```python
# harness_kernel/coding/open/rewriter.py  — rewritable (level 2)
def write_tickets(ledger, k, ranking) -> list[Ticket]:
    traces = ledger.worst_k(k, ranking)
    return [Ticket(title=pattern.title, traces=group, objectives=pattern.moves,
                   allowed_paths=pattern.targets) for pattern, group in group_by_failure_pattern(traces)]

# harness_kernel/coding/protected/recursion.py — protected
def improvement_cycle(kernel, agent_cmd: list[str], harness_repo: Path):
    v_n = kernel.store.head()                                           # the judge
    for t in write_tickets(kernel.ledger, kernel.settings.K, kernel.objectives.ranking()):
        wt = build_sandbox(harness_repo, v_n.kappa_commit, kernel.manifest, kernel.settings)
        wt_rules = Rules.restrict_writes_to(wt, t.allowed_paths)        # anything else fails I1 later anyway
        run_agent(agent_cmd, task=t.as_prompt(), sandbox=wt, harness=v_n) # the agent, under v_n, drafts v'
        diff = git_diff(wt.worktree, v_n.kappa_commit)
        if not diff: continue
        prop = Proposal(diff=diff, claim_sites=t.allowed_paths, predicted_direction=t.direction)
        run_proposal(kernel, prop)                                        # Inv → shadow → canary → promote (A4.4)
        git_worktree_remove(wt)
    # promotion, if any, moved store.head(); the next cycle is judged by it, never by a candidate
```

The `restrict_writes_to` line is a convenience, not a safety mechanism: it keeps the agent focused. Safety comes from I1 on the resulting diff, which the agent cannot influence because the Inv checker is in `protected/` at v\_n.

## C8. Theorems V: coding agents and recursion

Six results: interposition makes the agent's internals irrelevant to safety; git makes the dominant action class reversible; mechanical tests make the proxy gap measurable; the recursion is a monotone ratchet in hypervolume; the candidate never influences its own judgment; and the improvement rate is bounded by the evidence settings. Together they are the "provably near-optimal" claim for a self-improving coding harness, with the same meaning of optimal as Theorem 8.6.

### Theorem C.1 (Interposition completeness)

*Statement.* Let the coding agent run in a sandbox built by `build_sandbox` with a sandbox profile that confines process execution to the shim `PATH`, file writes to the worktree and cache dirs, and egress to the proxy. Then every action of the agent with an effect on any state outside its own process memory passes through `before_decision`, and Theorem A.1 holds for the composed system regardless of the agent's source, prompts, tools or model.

*Proof.* Effects on external state occur only through process execution, file writes, or network sockets. Process execution resolves through `PATH`, which contains only shims; a shim calls `before_decision` before spawning. Direct `exec` of an absolute path outside the shim directory is blocked by the sandbox profile. File writes outside the worktree and cache dirs fail on a read-only mount; writes inside are the Workspace-write class, recorded by the shim of whatever process performed them (or, for the agent's own process writing directly, by a filesystem watcher that emits an `after_outcome` for the Workspace-write class, which is reversible and so needs no pre-gate). Network sockets reach only the egress proxy, which classifies the destination and raises a decision point for any host not in the allowlist. The agent's model calls go to the local proxy, which is `wrap_client`. No other channel to external state exists under the profile. The composed system therefore satisfies Definition A1.3's boundary condition with the sandbox as the adapter, and Theorem A.1 applies. □

*Where the profile is unavailable.* Without seccomp or a platform sandbox, direct `exec` of an absolute path bypasses the shims. The certificate then tags Theorem C.1 *assumed* with the reason, and the operator decides whether the agent is trusted not to do that. This is the one place the coding design's guarantee depends on the platform.

### Theorem C.2 (Git reversibility)

*Statement.* For any sequence of Workspace-write and Toolchain-cache actions from baseline commit b, `git reset --hard b && git clean -fdx` followed by cache restoration returns the worktree and caches to their state at b. Consequently every such action is `reversible` with a single shared `revert`, and the Section A1.2 requirement of a per-capability `revert` is met for the entire class at once.

*Proof.* The worktree's tracked content at b is determined by b's tree object; `reset --hard b` restores tracked files to it and discards index changes. `clean -fdx` removes every untracked and ignored file, so files created after b are gone. Caches outside the worktree are restored from the snapshot taken at `build_sandbox` (or rebuilt from the lockfile, which is tracked and so already restored). Git's object store is append-only, so b's tree is always available. Observers in `observe()` read only the worktree and caches, so they return their b-values. □

*Consequence.* The irreversible class for a coding agent is the Remote-write and unlisted-network set, typically under ten binaries. Theorem A.1(b)'s gate fires only on those; everything else runs ungated at native speed.

### Theorem C.3 (The verification advantage)

*Statement.* Let the acceptance verifier be the conjunction of (i) a test suite with mutation kill rate m, written without sight of the implementation, and (ii) an isolated reviewer with calibrated miss rate r. Then the proxy gap satisfies ζ ≤ (1 − m)·r + ε\_cal, where ε\_cal is the calibration interval width, and ζ is estimable from the ledger rather than assumed. Theorem 8.5's regret bound for the coding harness is therefore O(√T log T) + ((1 − m) r + ε\_cal) T, with every constant measured.

*Proof.* R̂ = 1 and R = 0 requires an incorrect change to pass both verifiers. Test author and reviewer never saw the implementation or each other's output, and the tests are mechanical, so the two failures are independent conditional on the change: P(both pass | incorrect) = P(tests pass | incorrect) · P(review accepts | incorrect). Mutation testing estimates the first as 1 − m (the share of injected faults not caught; faults are a proxy for incorrect changes, and the gap between the two is absorbed into ε\_cal by validating mutation score against held-out real bugs). Reviewer calibration on held-out PRs with known verdicts estimates the second as r with interval ε\_cal. So ζ = P(R̂ ≠ R) ≤ (1 − m) r + ε\_cal. Substituting into Theorem 8.5 gives the bound. □

*Why this matters.* With m = 0.85 and r = 0.10, ζ ≤ 0.015 + ε\_cal; the Goodhart term is small and, more importantly, known. In a domain without mechanical verifiers ζ is whatever the operator hopes it is.

### Theorem C.4 (The recursion is a monotone ratchet)

*Statement.* Let HV\_n be the hypervolume, relative to the operator's reference point, dominated by the archive of promoted states after n promotions. Under Theorem 7.2's settings, (a) HV\_{n+1} ≥ HV\_n − (k+1) ε\_reg with probability at least 1 − (k+1) α; (b) HV\_n is bounded above by the hypervolume of the objective box; (c) HV\_n converges almost surely; (d) the expected number of promotions that decrease HV\_n by more than (k+1) ε\_reg over N promotions is at most (k+1) α N.

*Proof.* (a) A promotion passes Theorem 7.2's test, so each objective regressed by at most ε\_reg plus the deviation term with the stated probability; the hypervolume of a point that moves by at most ε\_reg per coordinate changes by at most (k+1) ε\_reg times the box's facet area, which is normalised to 1 by the reference-point scaling. A promotion that does not regress adds a non-dominated point or replaces a dominated one, which cannot decrease the dominated volume. (b) Every objective is bounded (R ∈ \[0,1\], costs by the I4 budget bound, observers by declared ranges), so the box is bounded. (c) Define Y\_n = HV\_n + n(k+1)ε\_reg·(k+1)α; by (a) and (d) Y\_n is a bounded-increment submartingale after accounting for the expected false-promotion loss, and a submartingale bounded in L¹ converges a.s. (Doob). Subtracting the linear drift, HV\_n converges a.s. provided α and ε\_reg are set so the drift is summable, which the operator does by shrinking α with n (e.g. α\_n = α\_0 / n²) at the cost of longer canaries; with fixed α the conclusion is convergence in the Cesàro sense. (d) Each promotion independently fails to be a true improvement with probability at most (k+1)α by Theorem 7.2's union bound; summing over N promotions gives the expectation. □

*Reading it.* The recursion cannot drift downward over time except at a rate the operator chose with α and ε\_reg, and it cannot grow without bound because the objectives are bounded. It is a ratchet with a known slip rate. The usual worry about recursive self-improvement, that it has no fixed point or an unsafe one, is answered by (b) and Theorem 7.3 respectively: the fixed point exists and lies inside Inv.

### Theorem C.5 (No self-grading)

*Statement.* For every candidate harness v′ proposed under promoted version v\_n, every statistic used to decide v′'s promotion was computed by code in v\_n's protected region, on episodes governed by v\_n, with v′'s overrides either disabled (shadow) or applied only to user-repo episodes with the gate pinned (canary). In particular no component of v′ influenced the drafting, verification, or statistical evaluation of v′.

*Proof.* By the four rules of C5.3, each enforced in κ\_P: the improvement episode's sandbox is built at v\_n's commit (rule 1), so the agent drafting v′ runs v\_n's prompts, routing and verification; shadow applies no overrides (rule 2); canary never covers improvement episodes (rule 3); and the verifier is `isolated_verify` of κ\_P invoked by v\_n (rule 4). The promotion decision is `promote_or_rollback` in κ\_P, which reads only the ledger records those stages produced. v′'s code paths therefore ran only in contexts whose outputs were observed but not used for judgment (shadow) or used for user-task outcomes under a pinned gate (canary); none of v′'s code produced a number that entered `promote_or_rollback` except as the *subject* of measurement. Theorem 7.3 ensures κ\_P is v\_n's and not v′'s. □

*The strict generation order.* Generation n+1 is judged entirely by generation n plus mechanical tests. Since generation 0 is human-installed and Theorem 7.1's induction carries Inv forward, every generation is judged by a chain of judges that terminates in a human and never includes the judged.

### Proposition C.6 (Rate bound on recursive improvement)

*Statement.* A promotion requires at least S + fN episodes of evidence (shadow plus canary), and each promotion changes HV by at most the hypervolume of the objective box, call it 1 after normalisation. Therefore after T episodes, HV\_T − HV\_0 ≤ T / (S + fN), and the expected number of promotions is at most T / (S + fN). Independently, Theorem 8.5's regret bound continues to hold, so the recursion is rate-optimal in the sense of Theorem 8.6 and cannot exceed it.

*Proof.* Immediate from the protocol: stages 3 and 4 consume S and fN episodes respectively and run sequentially; a proposal cannot be promoted on fewer. The hypervolume increment per promotion is bounded by the box. The regret bound is unaffected because the protocol is the one analysed in Theorem 8.5. □

*Consequence.* There is no regime in which the recursion accelerates past what its own evidence requirements allow. An operator who wants faster improvement lowers S or N and pays in Theorem 7.2's deviation term; the trade is explicit and the certificate prints both sides.

## C9. What the recursion can and cannot do

The recursion improves everything about how the agent acts and nothing about how it is judged or what it is; the boundary is sharp and the certificate prints it.

| The recursion can… | Because | The recursion cannot… | Because |
| --- | --- | --- | --- |
| Rewrite its own routing, compaction, budgets, prompts, skills, memory, classifier rules, proposal strategies | All in `open/`; Theorem A.2 | Change the invariants, gate ceiling, verifier isolation, store, classifier engine, or protocol | All in `protected/`; Theorem 7.3 |
| Learn which oracle to use for which step | Bandit over oracles; Prop. 8.4 | Improve any oracle | Oracles are frozen; the kernel samples them |
| Reduce tokens, dollars, wall-clock and churn while holding success | Multi-gradient descent on F\_code; Theorem 8.2 | Exceed the frontier the frozen oracles define | Theorem 6.1, Prop. 6.3; the frontier is theirs |
| Lower the proxy gap by writing better tests and sharper review prompts | Those are `open/` and their effect is measured by mutation score; Theorem C.3 | Certify that any individual change is correct | Prop. 6.2 |
| Learn how to write better improvement tickets | Level 2; strategies are bandit arms | Create a level 3 | A change to judging is a change to κ\_P |
| Improve monotonically with a known slip rate | Theorem C.4 | Accelerate past its evidence requirements | Prop. C.6 |
| Run on any coding agent | Theorem C.1 | Run safely on a platform with no sandbox profile and an agent that `exec`s absolute paths | C.1's assumption; certificate says so |

### The boundary of optimality, for this design

After staging, the certificate's Section 7 reads, with numbers filled in: the promoted harness v\_n is Pareto-stationary for F\_code on the task suite (Theorem 8.2); it is not shown globally Pareto-optimal because F\_code is non-concave in θ (Corollary 8.3's caveat); regret against the local frontier is O(√T log T) + ζT with ζ ≤ (1 − m) r + ε\_cal *measured* (Theorem C.3), and no harness over these oracles achieves o(√T) (Proposition 6.4); the hypervolume sequence is non-decreasing with slip probability (k+1)α per promotion and converges (Theorem C.4); the candidate never judged itself (Theorem C.5); and the improvement rate is at most one promotion per S + fN episodes (Proposition C.6). Correctness of any single change is not certified (Proposition 6.2); the frontier is specific to the oracles, the task distribution and F\_code (Theorem 6.1).

That is the strongest set of claims this document knows how to prove about a self-improving coding harness. Each line names the theorem that grounds it and the obstruction that stops the next stronger claim, which is what "provably near-optimal" has meant throughout.

## C10. Worked example: an arbitrary CLI coding agent

A coding agent invoked as `agent --prompt "..."` in a directory, with no hooks and no SDK, becomes a kernelized, self-improving agent in the lines below; the recursion is bootstrapped by pointing the same loop at the harness repository.

```python
from harness_kernel.coding import (build_sandbox, RepoAdapter, Toolchain, coding_objectives,
                                   CodingManifest, improvement_cycle)
from harness_kernel import adapt

# 1. Manifest: the operator confirms the irreversible set once. Everything else is classified per call.
manifest = CodingManifest(
    publishers={"npm": 2000.0, "docker": 5000.0, "terraform": 20000.0, "kubectl": 20000.0},
    remotes={("origin", "main"): 10000.0, ("origin", "release/*"): 20000.0},
    harness_owned={("origin", "agent/*"), ("harness", "*")},
    allow_hosts={"registry.npmjs.org", "pypi.org", "github.com"},
    cache_dirs={"~/.npm", "~/.cache/pip"},
    confirmed_by="platform-lead@example",
)

# 2. Objectives: F_code with the operator's references; no weights.
spec = coding_objectives(success_reference=0.90, regressions_bound=0, churn_reference="baseline")

# 3. The agent, as a host: a function of (model client, tools) -> runnable. For a CLI agent the
#    client is reached through the proxy env var and the tools through the shimmed PATH, so the host
#    factory just runs the binary in the sandbox.
def cli_agent_host(client, tools):
    def run(task, sandbox):
        return subprocess.run(["agent", "--prompt", task], cwd=sandbox.worktree, env=sandbox.env)
    return run

# 4. Compose over the user repo.
sb     = build_sandbox(repo="/work/user-repo", baseline="main", manifest=manifest, settings=SETTINGS)
agent  = adapt(RepoAdapter(sb, "main", Toolchain.autodetect(sb)), spec,
               oracles=[cheap_model, strong_model], host=cli_agent_host)

# 5. Work. Every push to main is gated; every workspace write is reversible; routing starts uniform.
agent.run("Add rate limiting to the /upload endpoint; keep the existing tests green")

# 6. Recursion. The same agent, under the promoted harness, works tickets against the harness repo.
#    Runs on the operator's schedule; each cycle ends in zero or more promotions, each fully gated.
improvement_cycle(agent.kernel, agent_cmd=["agent", "--prompt"], harness_repo="/work/harness-repo")

print(agent.certificate().section(7))      # the boundary statement, with this run's numbers
```

What the first improvement cycle typically produces, from a cold start: a Compaction ticket (the agent re-reads large files; a `compact.py` rule to summarise files over N lines after first read), a Classifier-precision ticket (the gate blocked `git push origin agent/feature-x` because the harness-owned pattern was missing a case), and a Skill ticket (three tasks failed the same lint rule; a `skills/lint-rules.md` file). All three go through Inv, shadow and canary. The first two usually promote; the third promotes if the lint failures recur in the canary window and not otherwise, which is the protocol declining to learn from noise.

The operator's involvement, end to end: the `confirmed_by` manifest (one sitting), the three references in `coding_objectives`, and a decision at P7. The agent's involvement: everything else, including the next version of the harness it ran under.

## C11. Cost optimisation: the distillation ladder

Every pattern that recurs in the ledger is a candidate to move down a ladder of execution substrates with falling marginal cost and falling generality; the kernel moves a pattern down one rung only when shadow evidence shows the cheaper substrate agrees with the dearer one on that pattern within tolerance, keeps auditing it against the source at a floor rate, and moves it back up the moment the audit says the agreement has broken.

&#91;embedded content: distillation ladder · 5 rungs, admission down, audit-driven demotion up\]

A pattern descends one rung at a time on shadow and canary evidence; every rung below R0 keeps an audit stream against its source, and a stream that crosses τ sends the pattern back up without a proposal cycle.

### C11.1 The rungs

| Rung | Substrate | Replaces | Built from | Marginal cost per use | Generality |
| --- | --- | --- | --- | --- | --- |
| R0 | Frontier oracle, one call per step | nothing; the baseline | — | c\_0 (tokens × price) | any task |
| R1 | Fewer frontier calls | several R0 calls | prefix and semantic caching; step fusion (plan + act in one call); speculative cheap-model execution verified by tests; batching of independent steps | α c\_0, α typically 0.3–0.6 | any task; no quality loss when the fused call carries the same information |
| R2 | Compiled code | an LLM-driven tool sequence with stable structure | a parameterised script synthesised from ≥ n\_min verified traces of the same pattern; the oracle fills parameters or is bypassed when the input is recognised | ≈ 0 LLM; CPU only | that pattern's input class only |
| R3 | Small local model | frontier calls on a narrow recurring task | fine-tuned on (input, verified output) pairs from the ledger: commit messages, lint fixes, test naming, file-relevance ranking, routing itself | c\_3 ≪ c\_0, local inference | the task's input distribution as seen in the ledger |
| R4 | Custom ML model | a prediction the oracle was making implicitly | a classifier or regressor trained on ledger features: difficulty, flakiness, which files matter, which oracle will pass | ≈ 0 | one prediction, in-distribution |

The rungs are not exclusive. A single episode under a mature harness is R2 for its scaffolding (clone, branch, run tests), R4 for its context selection, R3 for its commit message, R1 for its planning, and R0 for the part of the task that is new. The kernel's job is to find, per pattern, the lowest rung whose evidence supports it.

### C11.2 Patterns

A pattern is an equivalence class of ledger traces under a normalisation: tool names kept, argument values replaced by their types and shapes, file paths by their extensions and directory depth, model outputs by their structural form (a diff touching k files, a shell command of class X). A pattern is **distillable** when the ledger holds at least n\_min traces of it with verified success (tests passed, review accepted, no later regression), when its normalised action sequence has edit distance below a threshold across those traces, and when its frequency weighted by R0 cost exceeds the cost of building and auditing a distillate. The miner runs with the K5 cycle and emits a new ticket type, **Distillation**, alongside those of C5.2; its target is a new file under `open/distillates/` and, for R3 and R4, a training job whose artefact is registered as an oracle arm or a plugin.

### C11.3 Admission

A distillate D for pattern P enters exactly as any other change: as a Proposal through Inv, shadow and canary (A4.4). Shadow has a specific meaning here. On every episode where P is recognised, both D and the source rung run; D's output is recorded and the source's is used. The statistic is the **agreement rate** a\_P = P(D's output leads to the same verified outcome as the source's) over at least S\_P shadow instances, with a Hoeffding interval. Admission requires a\_P ≥ 1 − τ\_P at the interval's lower end, where τ\_P is derived from the success bound in F\_code (Section C4.1): the sum over admitted patterns of freq(P) · τ\_P must not exceed the slack between the current success rate and its bound. Canary then runs D live on fraction f with the audit at its floor; promotion is the Section 7.2 test on the whole F\_code, so a distillate that saves tokens but lengthens wall-clock or raises churn is rejected on evidence.

Three rules, enforced in κ\_P, bound what distillation can touch: a distillate may replace a *producer* but never the isolated verifier or the gate; a distillate that proposes an irreversible action is still gated at 1 − δ\_max by `isolated_verify` from κ\_P, with the frontier oracle as judge; and every admitted distillate carries an **audit floor** p\_min > 0, the probability that a recognised instance of P is also run through the source rung for comparison. The audit floor is a new protected invariant for this specialisation, I8: no promoted state may hold a distillate with audit probability below p\_min, and θ\_audit is clipped to \[p\_min, 1\] in the optimizer like θ\_gate.

### C11.4 Drift and demotion

Audited instances produce a stream of agree/disagree observations per pattern. A protected sequential test (a Hoeffding-window test with window w and level α\_d, or a CUSUM at the operator's choice) watches each stream. When the lower confidence bound on disagreement exceeds τ\_P, the pattern is **demoted** one rung immediately, without a proposal cycle, because demotion is a return to a state that was previously promoted and is therefore already inside Inv. The demotion is logged, a re-distillation ticket is opened, and the audit stream resets. Theorem D.2 bounds how many episodes a drifted distillate survives and how much quality it costs before demotion.

### C11.5 What the ladder costs to run

Mining is a pass over the ledger per K5 cycle, linear in its size. Building an R2 script is one oracle call per candidate plus the shadow run. Training an R3 model is the one expensive step and is scheduled by the operator's budget; R4 models train in seconds. Auditing costs p\_min · c\_0 per recognised instance, which is the floor of Theorem D.5 and the price of knowing the distillate still works. The accounting is explicit so that the certificate can print the net saving with its interval rather than the gross.

## C12. Automated context engineering

Context is the largest token cost in coding and the one most under the harness's control; the kernel treats what enters the window as a decision with the same shadow-admission test as a distillate: a smaller context is admitted when the oracle reaches the same verified action with it as without it.

### C12.1 Mechanisms, by rung

| Mechanism | Rung | What it does | Admission evidence |
| --- | --- | --- | --- |
| Repository map | R2 | A symbol index and dependency graph built once per baseline commit, injected as a compact immutable prefix instead of raw file reads | Reads per episode fall; agreement on first action holds |
| Prefix layout | R1 | Immutable material (map, skills, manifest excerpt) first, mutable last, so provider prefix caching applies | Pure Pareto move (Design claim 6.7); no test needed |
| File-relevance ranker | R4 | A model trained on ledger pairs (task text, files the accepted diff touched) that chooses which files to pre-load and which reads to allow at full length | Agreement on the accepted diff's file set; tokens per success |
| Learned compaction threshold | θ | θ\_compact per task bucket, learned by K4 | Ordinary gradient evidence |
| Compaction policy | R2/R3 | Summarise a file after first full read; keep signatures and the lines the diff touches; drop tool output older than k steps unless referenced | Shadow: the oracle's next action unchanged under the compacted window |
| Episodic retrieval | R2 | For a task whose pattern matches a past success, inject that episode's accepted diff and test outcome as a worked example | Steps to first passing test fall; success unchanged |
| Context contracts | κ\_O | Per ticket type, a template fixing what the context contains and in what order; refined by K5 like any prompt | Promotion evidence per ticket type |

### C12.2 The sufficiency test

A context transformation c → c′ with |c′| < |c| is admitted for a pattern when, over S shadow instances, the oracle's chosen action under c′ leads to the same verified outcome as under c at rate ≥ 1 − τ\_ctx. This is the same statistic as C11.3's agreement rate with the "distillate" being the pruned context. The cost of the test is one extra oracle call per shadow instance, which is why context transformations are tested on the cheap oracle first and promoted to the dear one only if they hold there too. Transformations compose; the kernel tests the composition, not the parts, because sufficiency is not additive.

### C12.3 Why this is distillation

A context policy encodes what the oracle would otherwise discover by reading: the repository map is the compiled form of a hundred `ls` and `grep` calls; the relevance ranker is a model of which reads mattered; episodic retrieval is a cache of past reasoning. Each replaces oracle work with cheaper work and each is admitted by the same evidence, so Theorems D.1–D.5 apply to context transformations without change; they are rows in the same ledger of patterns and audits.

## C13. Code: miner, distillates, audit, context ranker

```python
# harness_kernel/coding/open/miner.py — rewritable (its thresholds are θ; its normaliser is κ_O)
def mine_patterns(ledger, n_min, max_edit, min_savings) -> list[PatternCandidate]:
    groups = defaultdict(list)
    for ep in ledger.successful_episodes():                     # tests passed, review accepted, no regression
        groups[normalise(ep.trace)].append(ep)                  # tool names, arg shapes, file kinds, output forms
    out = []
    for sig, eps in groups.items():
        if len(eps) < n_min or mean_edit_distance(eps) > max_edit: continue
        r0_cost = sum(e.cost.usd for e in eps) / len(eps)
        freq    = len(eps) / ledger.episode_count()
        for rung in (Rung.R1, Rung.R2, Rung.R3, Rung.R4):
            if rung.applies(sig) and freq * (r0_cost - rung.est_cost(sig)) > min_savings + rung.build_cost(sig):
                out.append(PatternCandidate(sig, eps, rung)); break
    return out

# harness_kernel/coding/open/distill.py — rewritable
def build_distillate(cand: PatternCandidate, oracle) -> Distillate:
    if cand.rung == Rung.R1:
        return FusedPrompt.from_traces(cand.episodes)                       # one call where there were k
    if cand.rung == Rung.R2:
        src = oracle.complete(SYNTHESISE_SCRIPT_PROMPT.format(traces=cand.episodes))
        return Script(src, recogniser=InputRecogniser.from_traces(cand.episodes))
    if cand.rung == Rung.R3:
        pairs = [(e.input_for(cand.sig), e.verified_output_for(cand.sig)) for e in cand.episodes]
        return LocalModel.finetune(base=SETTINGS.small_base, pairs=pairs)   # scheduled job; returns an oracle arm
    if cand.rung == Rung.R4:
        X, y = features_and_labels(cand.episodes, cand.sig)
        return Predictor(train_classifier(X, y), schema=cand.sig.prediction_schema)

# harness_kernel/coding/protected/audit.py — protected (I8 and demotion live here)
@dataclass
class AuditStream:
    pattern: Signature; rung: Rung; agree: int = 0; total: int = 0
    def observe(self, agreed: bool): self.agree += agreed; self.total += 1
    def drifted(self, tau, alpha_d, w) -> bool:
        if self.total < w: return False
        p_hat = 1 - self.agree / self.total
        lower = p_hat - math.sqrt(math.log(1 / alpha_d) / (2 * self.total))
        return lower > tau

def run_recognised(kernel, pattern, instance, distillate, source):
    audit = kernel.rng.random() < max(kernel.theta.audit[pattern], kernel.settings.p_min)   # I8 floor
    d_out = distillate(instance)
    if audit:
        s_out = source(instance)
        kernel.audits[pattern].observe(same_verified_outcome(d_out, s_out, instance))
        if kernel.audits[pattern].drifted(kernel.tau[pattern], kernel.settings.alpha_d, kernel.settings.w):
            kernel.ladder.demote(pattern)                        # back to a previously promoted state; no proposal needed
            kernel.tickets.open(Redistill(pattern))
        return s_out                                             # audited instances use the source's output
    return d_out

# harness_kernel/coding/open/context.py — rewritable; ranker is an R4 distillate
def assemble_context(task, repo_map, ranker, memory, theta):
    files = ranker.top_k(task, repo_map, k=theta.context_files)
    prior = memory.similar_success(task, n=1)
    return Context(prefix=[repo_map.compact(), skills.current(), manifest.excerpt()],   # immutable, cached
                   body=[task, *[f.signatures_or_full(theta) for f in files], *([prior] if prior else [])])

def sufficiency_shadow(kernel, pattern, transform, S):
    agree = 0
    for inst in kernel.ledger.shadow_instances(pattern, S):
        a_full  = kernel.oracle.next_action(inst.context)
        a_small = kernel.oracle.next_action(transform(inst.context))
        agree  += same_verified_outcome(a_small, a_full, inst)
    return agree / S
```

The `run_recognised` function is the whole of the runtime cost model: a recognised instance costs c\_D plus, with probability max(θ\_audit, p\_min), c\_0 as well. Everything above it is proposal-time work and everything below it is θ.

## C14. Theorems VI: distillation

Five results: a gated distillate is a Pareto move or a frontier point, never a hidden regression; a drifted distillate is caught within a bounded number of episodes at bounded cost; distillation cannot widen the irreversible surface; the ladder's rung assignment converges; and total LLM cost has a floor set by the audit rate and the non-repeated share of the workload, which the kernel approaches and cannot pass.

### Theorem D.1 (A gated distillate is a Pareto move or a frontier point)

*Statement.* Let pattern P have frequency φ\_P, source cost c\_0 per instance, distillate cost c\_D < c\_0, audit probability p ≥ p\_min, and shadow agreement a\_P ≥ 1 − τ\_P at the interval's lower end. After admission, the expected per-episode change in F satisfies: cost coordinate improves by φ\_P \[(1 − p) c\_0 − c\_D\]; success coordinate worsens by at most φ\_P (1 − p) τ\_P; all other coordinates unchanged in expectation. If τ\_P = 0 the move is a strict Pareto improvement. If τ\_P > 0 it is a Pareto improvement whenever the success bound in F\_code has slack ≥ φ\_P (1 − p) τ\_P, and otherwise it is rejected at canary.

*Proof.* On a recognised instance, with probability p the source runs and its output is used (cost c\_D + c\_0, outcome identical to the source's); with probability 1 − p only the distillate runs (cost c\_D, outcome differs from the source's with probability ≤ τ\_P). Expected cost per instance is c\_D + p c\_0 against c\_0 before, a saving of (1 − p) c\_0 − c\_D; expected success loss per instance is ≤ (1 − p) τ\_P. Multiply by φ\_P. Other coordinates are unchanged in expectation because the distillate replaces a producer and the verifier, gate and remaining steps are untouched (Theorem D.3). The success bound is a projection constraint (A2.2), so a loss exceeding the slack fails `project_bounds` or the canary test; a loss within it leaves every coordinate weakly improved or within the bound. □

*Remark on "Pareto optimal across tasks."* Distillation moves the whole archive along the cost axis without moving it along the success axis beyond the bound's slack. The frontier the certificate reports after distillation therefore dominates the frontier before it on cost at equal success, which is the precise sense in which the ladder improves the frontier rather than trading along it.

### Theorem D.2 (Bounded survival of a drifted distillate)

*Statement.* Suppose after admission the true disagreement rate of distillate D on pattern P rises to τ\_P + Δ for some Δ > 0, with D at audit probability p and the Hoeffding-window test at level α\_d. Then with probability ≥ 1 − α\_d, D is demoted within n\* audited instances where

```latex
n^* \;=\; \left\lceil \frac{2\ln(1/\alpha_d)}{\Delta^2} \right\rceil
```

and hence within n\*/p recognised instances in expectation; the expected excess success loss incurred before demotion is at most φ\_P (1 − p) Δ · n\*/p per unit of workload.

*Proof.* The test demotes when the lower confidence bound p̂ − √(ln(1/α\_d) / 2n) exceeds τ\_P. With true rate τ\_P + Δ, Hoeffding gives p̂ ≥ τ\_P + Δ − √(ln(1/α\_d)/2n) with probability ≥ 1 − α\_d, so the lower bound exceeds τ\_P once 2√(ln(1/α\_d)/2n) < Δ, i.e. n > 2 ln(1/α\_d)/Δ². Each recognised instance is audited with probability p, so n\* audits take n\*/p instances in expectation (geometric waiting). Before demotion, each unaudited instance loses at most Δ more success than at admission; there are (1 − p) n\*/p of them in expectation. □

*Consequence.* The operator's p\_min sets the exposure to drift directly: halving p\_min doubles the episodes a drifted distillate survives and the quality it costs. The certificate prints n\*/p\_min for each admitted pattern.

### Theorem D.3 (Distillation does not widen the irreversible surface)

*Statement.* For any set of admitted distillates at any rungs, properties (a)–(d) of Theorem A.1 hold, and every irreversible action proposed by a distillate is gated by `isolated_verify` from κ\_P with the frontier oracle as judge.

*Proof.* An R1 distillate is a prompt; it runs through `wrap_client` like any model call. An R2 script runs inside the sandbox, so every process, file write and socket it opens passes through the seams of Theorem C.1; a script that issues `git push` is classified and gated exactly as the agent would be. An R3 model is registered as an oracle arm (Section 5) and is subject to the Section 5 rule that no irreversible-class decision is routed to an oracle whose empirical error on that class exceeds δ\_max, which a freshly admitted arm fails by default until the ledger shows otherwise; and its proposals are gated regardless. An R4 predictor changes features or routing, never actions. The C11.3 rules in κ\_P forbid a distillate from replacing `isolated_verify` or `gate`, and Theorem 7.3 keeps κ\_P fixed. So the set of paths to an irreversible transition is unchanged and each still passes the gate. □

### Proposition D.4 (The ladder converges)

*Statement.* Under stationarity (A1), for each pattern P the rung assignment r\_P(t) ∈ {R0, …, R4} changes only finitely often almost surely, and the number of demotions over N admissions has expectation at most α N + α\_d N.

*Proof.* A move down requires a promotion (Theorem 7.2's test). A move up is a demotion, which under stationarity can occur only if the admission was a false positive (true disagreement already exceeded τ\_P; probability ≤ α by the canary test) or the drift test produced a false alarm (probability ≤ α\_d per window). Both have bounded probability per admission; summing gives the expectation. The rung is a bounded integer; a sequence with finitely many expected changes in each direction changes finitely often a.s. by Borel–Cantelli when α and α\_d are chosen summable in the admission index, and otherwise in the Cesàro sense, as in Theorem C.4. □

*Without stationarity.* Real drift produces real demotions, each followed by a re-distillation ticket. The ladder then tracks the workload at the A1 drift rate; the certificate's drift term absorbs it.

### Theorem D.5 (The cost floor)

*Statement.* Let ρ\_rep be the fraction of workload (weighted by R0 cost) that falls in distillable patterns, and let p\_min be the audit floor. Then for any state the kernel can reach, expected LLM cost per episode satisfies

```latex
C_{\mathrm{LLM}} \;\ge\; (1 - \rho_{\mathrm{rep}})\, c_0 \;+\; \rho_{\mathrm{rep}}\, p_{\min}\, c_0
```

and under A1–A5 the kernel's cost converges to within the Section 8 regret of this floor. No admissible change to κ\_O lowers the floor; only the operator can, by lowering p\_min (at the Theorem D.2 price) or by adding oracles that lower c\_0.

*Proof.* Work outside distillable patterns has no cheaper admissible substrate (by definition of distillable, the evidence for one does not exist), so it costs c\_0. Work inside them is audited at rate ≥ p\_min by I8, which is in κ\_P and so unchanged by any promotion (Theorem 7.3); each audit costs c\_0. Summing gives the floor. Convergence toward it follows because the cost coordinate of F is one of the objectives K4 descends (Theorem 8.2) and the ladder admits every distillate whose evidence passes (Theorem 7.5 liveness), so the gap between realised cost and the floor is the sum of patterns not yet distilled, which shrinks as the ledger grows. □

*What the floor says.* A harness over a workload that is 70% repeated, with p\_min = 0.05, cannot spend less than 33.5% of its undistilled LLM cost, and approaches that figure. A harness over a workload with nothing repeated saves nothing by distillation and the kernel will not try, because no pattern meets n\_min. The floor is a property of the workload and the operator's audit choice, not of the kernel's ingenuity, and the certificate reports ρ\_rep, p\_min, the floor, and the current distance to it.

### Boundary statement, extended

To the C9 boundary the distillation results add: the frontier after distillation dominates the frontier before it on cost at equal success (Theorem D.1); a drifted distillate costs at most φ\_P (1 − p) Δ n\*/p before it is caught (Theorem D.2); the irreversible surface is unchanged (Theorem D.3); the rung assignment converges under stationarity (Proposition D.4); and LLM cost cannot fall below (1 − ρ\_rep + ρ\_rep p\_min) c\_0 (Theorem D.5). What distillation cannot do: lower the audit floor, replace the verifier or the gate, improve an oracle, or make a non-repeated task cheaper than one frontier call.
