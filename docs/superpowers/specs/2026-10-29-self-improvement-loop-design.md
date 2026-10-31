# G6 improves itself: the self-improvement loop (design)

**Status:** Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

- **Written:** 2026-10-29, from a design conversation with the owner, who approved it ("happy with design, proceed").
- **Scope:** the long-term programme beside G6's north star. It never delays the first launch: launch work keeps
  first call on builders and reviews.
- **Labels:** where this document records a choice the owner made, it says "the owner". Where it records a call the
  designer made on the owner's behalf, it says "designer's call", so the owner can check those first.

## 1. Intent

Developers who use coding agents (Claude Code, Codex) want to hand them more of the work, and cannot, because they
cannot trust an agent's "done". G6 exists to make that trust earned: independent checks, confined execution,
reproducible findings, a repair loop, and evidence a person can audit.

This programme points G6 at itself:
- G6 runs continuously, uses Claude Code and Codex as its engineers, finds what most limits how well it helps
  developers, changes itself to remove that limit, proves each change with evidence it does not control, and offers
  the change as a draft pull request.
- The owner reviews evidence and merges. They are no longer in the loop.

Three things come out of it:
1. **A better product.** Patch review, the repair loop and the coding mission get measurably better week by week.
2. **A claim with a trail.** "G6 engineers itself" becomes a public record: each change G6 made to itself, the
   evidence it passed, and the before-and-after measurement.
3. **The north star.** A coding agent working with G6 exceeds the same agent working alone (section 5).

**Success, in the owner's words (paraphrased):** G6 has validated itself as a system, and has grown a real following
among developers because they can give their agents more autonomy and trust the results, so the quality of what
those agents produce is far higher.

## 2. Goals, non-goals, anti-goals

**Goals**
- G1. The loop runs unattended on the owner's machine, on the owner's Claude Code and Codex subscriptions, within the
  pacing rule (section 6.6).
- G2. It chooses what to improve by the theory of constraints, from measured failures (section 6.1).
- G3. Every change it proposes reaches the owner as a draft pull request with an evidence package that is quick to
  check: a truthful certificate, the measurement against frozen evaluators, an outside review, and the limits.
- G4. The north-star study (section 5) is registered, run, reported whatever it shows, and repeated on fresh tasks.
- G5. Open pull requests #2 to #7 are integrated into one engineering mission reachable over MCP, which closes the
  outside review's five gates (section 7).
- G6. Issues on g6solver are read, answered publicly and worked on by the loop (section 6.8).
- G7. Everything is public: the loop's code, its records, its studies and its results (section 6.9).

**Non-goals**
- A universal program synthesiser, or a general formal-verification platform (the owner's scope rule of 2026-10-28
  stands).
- Spending API credits. The registered studies V9 and V10 keep their own API caps and run as registered.
- Posting anywhere outside the g6solver repository, or touching any other project.
- Changing the first launch's scope or timing.

**Anti-goals: failure even if it "works"**
- Results that do not withstand professional scrutiny. Examples:
  - improvements G6 grades itself on;
  - gains measured on tasks the loop tuned on;
  - an oracle model's opinion counted as proof;
  - a run dropped or rerun to get a better number;
  - an evaluator the loop could edit.
- A system that fails in practice: demos that do not reproduce on someone else's machine, or a loop that stalls
  silently.
- A loop that "improves" by weakening tests, checks or rules.
- Public replies that overclaim, leak anything private, or can be steered by the text of an issue.
- A week's allowance used up in two days, or the owner's own work starved of usage.

## 3. Binding constraints (carried over; the loop cannot change them)

- **Certificate-bound files:** the files the five certificates bind (for example `g6/core/confine.py`,
  `g6/core/llm.py`, `g6/core/registry.py`) change only through code, then an independent scoped review, then frozen
  inputs, then normal issuance, then artifact checks. The loop can draft such a change as a pull request labelled
  `needs-issuance`. It never reissues a certificate, and it never counts such a change as accepted.
- **Owner-only steps:** merge, tags, releases, PyPI and the site deploy stay the owner's. The owner's merge is the
  Approve step for every change the loop makes.
- **Credentials:**
  - API keys only through `g6.core.keys`.
  - The subscriptions' sign-in files follow the same rule: no check G6 runs, and no agent session's tools, may read
    them.
  - Before any paid or subscription session, a confined check is shown unable to read them.
  - **How (the owner's decision O1, 2026-10-29: yes, on one condition):** the owner allows headless, autonomous use
    of their subscriptions in the loop "as long as claude and codex do not expose their api to external users". So:
    - the credential relay is reachable only from the loop's own containers on the owner's machine;
    - model work serves only G6's own engineering. An issue becomes work only once the loop has reproduced it as a
      G6 defect, and the loop never works on an issue author's own project or request;
    - issue-driven model work has caps per author and per day.

    The sign-in token never enters a container. The
    engineer's container reaches the network only through an egress proxy that adds the credential to requests to
    the provider's API host, and to no other host. The agent's shell therefore has nothing to read, and a published
    transcript cannot carry the token. It also avoids Codex's rotating refresh token being copied into containers,
    which could sign the owner's own Codex out.
  - The builder first shows that each command-line tool works this way. The owner then confirms that headless use
    of their subscriptions through such a proxy is acceptable under Anthropic's and OpenAI's terms. Until both, no
    live engineer run happens: P1 and P2 need none.
- **Evidence:**
  - Never synthesise evidence.
  - Never relabel old evidence as testing new bytes.
  - Never count an incidental block as detecting a defect.
  - Never choose a configuration after seeing evaluation data.
- **Data separation:**
  - No SWE-bench, SWE-bench Verified, Multimodal or Pro data in development.
  - Sealed tasks stay sealed (section 6.3).
- **Wording:**
  - The status line above appears wherever G6's status is stated.
  - Never "verified" of a patch or of G6.
  - Never "experimental".
  - Results are "worked examples", never "proof of concept".
- **Public history:** everything pushed is public. No user name, home folder, private path, key or held source
  (all-rights-reserved book text) is ever committed (section 6.9).
- **The surfaces:** MCP is the surface of record. The CLI and GUI are frozen, so the loop runs as
  `python -P -m g6.self_maintenance`, not as a new `g6` command (designer's call).
- **Classifier and permission stops:** when a safety classifier or a permission prompt stops an action, the loop
  stops that action. It records the stop and reports it in its digest. It never rephrases or retries the action.
- **Containers:** the loop never stops, kills or removes a container it did not start.
- **CI:** GitHub Free. The loop keeps at most two of its pull requests waiting on CI at once, and pushes only
  candidates that already passed its local checks in Docker (designer's call).

## 4. Who does what

| Step | Who | How |
|---|---|---|
| Propose | Claude Code or Codex, as the loop's engineer | A coding mission in a disposable container (section 6.4) |
| Test | G6, confined | Its own suites, the frozen development evaluator, patch review on the loop's own diff |
| Gate | G6's frozen rules | `EvidencePolicy` (alpha 0.05, max_evaluations 5, so threshold 0.01), the safety rules, a cross-family review |
| Approve | The owner | Merging the draft pull request. Nothing reaches developers before that |

- The loop's gate decides only what becomes a pull request. Only the owner's merge changes what developers install.
- Reviews are cross-family: a change written by Claude is reviewed by Codex, and the reverse (designer's call). The
  reviewer never wrote the change.

## 5. The north-star study (Gate B, V12)

The comparison a sceptical senior engineer accepts, confirmed by the owner:

> On fresh real bugs, a coding agent working with G6 delivers more fixes that the project maintainers' own tests
> accept, and claims "done" wrongly less often, than the same agent working alone, at the same budget.

- **Tasks:** the worked examples' selection rule, applied continuously:
  - permissive licence;
  - at least 10,000 stars or among the most downloaded on PyPI;
  - not a repository of any SWE-bench dataset;
  - a pytest suite that runs offline in a container;
  - an issue with a clear expected behaviour, closed by a merged pull request that changes source code and adds or
    changes a test, merged after the agent model's training cutoff and after the study's registration.

  New tasks appear every week, so the pool stays ahead of training data.
- **Ground truth:** the maintainers' later fix and tests, which the agents and G6 never see.
- **Arms:**
  - **Alone:** Claude Code (and, as a second family, Codex) working the agent's normal way, tests included.
  - **With G6:** the same agent with G6 over MCP and the using-g6 skill: requirements, checks, `g6_review_patch`,
    the report and the repair loop.
- **Budget matching:** the same tokens (all kinds) and turns per task in both arms. G6's tool calls count inside the
  budget. Matching in tokens rather than dollars lets the study run on subscriptions.
- **Primary measures, with 95% intervals:**
  - the share of tasks whose final patch passes the maintainers' tests and the repository's existing tests;
  - the share of tasks where the agent said "done" and the patch fails them.
- **Secondary measures:** tokens, minutes, and G6's findings, each classed as real or a false alarm.
- **Analysis:** paired by task, with an exact paired test (sign or McNemar) and a registered margin. The sample size
  comes from a pilot.
- **Protocol:**
  - The loop may draft the protocol. A reviewer from the other model family audits it.
  - The owner merges it, which registers it.
  - The loop runs it only after that merge, and publishes the results whatever they show.
- **Wording:** "exceeds the state of the art" appears in G6's copy only after a registered confirmatory study shows
  it, and then exactly as measured: which agents, which pool, which budget, which interval.
- **The loop's job is to raise this number.** It never touches the study's protocol, its sealed tasks or its scripts
  once the protocol is registered.

## 6. The loop

### 6.1 One cycle: theory of constraints over measured failures

1. **Measure.** Read the latest records (section 6.2).
2. **Identify the constraint.** Each development task where agent + G6 fell short gets one cause, from G6's reason
   codes and receipts. Examples:
   - G6 refused (environment, isolation, the repository's layout);
   - G6 accepted a defect;
   - a false alarm led the agent astray;
   - the repair loop stalled;
   - the budget ran out;
   - the task could not be reproduced;
   - beyond G6 (the agent could not fix it, and G6 could not help).

   The constraint is the cause G6 can change that costs the most fixes. The cause is read from reason codes first. A
   grade-A model (section 6.7) classifies only free-text failures, and it is checked by sampling. As in PR #7, the
   loop names no constraint when records are not independently observed.
3. **Exploit.** Try the cheapest changes first, within the meta-harness's editable schema: check selection, budgets,
   prompt variants, model routing. Evaluate them by replay (section 6.2).
4. **Subordinate.** Put all other work behind the constraint. Non-constraint tickets get only leftover budget.
5. **Elevate.** If exploiting is not enough, a coding mission changes G6's code (section 6.4).
6. **Gate, then open a draft pull request** with its evidence package (section 6.5).
7. **Repeat.** Re-measure. If the constraint moved, name the new one. Every identification is kept in a public
   constraint log, so the history of what limited G6, and what moved it, is part of the record.

### 6.2 Where the measurements come from

- **Replays (cheap, no model calls).** Agent patches recorded from earlier runs on development tasks, labelled by the
  maintainers' tests. G6's review is rerun on each recorded patch, measuring false acceptance, correct acceptance,
  actionable findings and refusals. Most gate decisions rest on replays.
- **Live runs (spend usage).** Agent + G6 on development tasks, end to end. These refresh the recordings and find
  causes replays cannot see, such as a stalled repair loop.
- **G6's own CI and tests,** and planted-defect and correct-patch controls, so a "no findings" result is checked.
- **Issues** (section 6.8). A user's report counts toward a constraint once the loop has reproduced it.
- **The worked examples' records.**

### 6.3 Development pool and sealed pool

- **The population** is every repository that meets the selection rule of section 5, not a fixed list. A
  deterministic query enumerates them on a recorded date, and the query is kept.
- **The split.** Each repository goes to the development side or the sealed side by a seeded hash of its name, fixed
  before any run. A repository that starts meeting the rule later is assigned by the same hash, so the split never
  needs redrawing (designer's call).
- **Repositories already seen** (those the worked examples used: pydantic, black, click, starlette, fastapi and
  attrs) go to the development side whatever their hash.
- **Yield.** The builder measures in P2 how many qualifying tasks each side yields per month. P5's sample size and
  schedule come from that figure, not from a guess.
- The loop develops and gates only on the development pool. The north-star study draws only from sealed
  repositories.
- Sealed tasks' text, fixes and tests sit in a vault the loop's processes cannot read. They are read only by the
  study's label step, after the arms' records are hashed and committed.
- A task the loop ever saw never enters a study. The study's draw excludes it by ID.
- **Custody (the owner's decision O2, 2026-10-29: no second account).** The study runner is a separate program,
  frozen at the study's registration. It runs under the owner's own user, as the loop does, so every study report
  states the limit below.
  - The study runner harvests and qualifies sealed tasks. The judgement "a clear expected behaviour" is made by a
    grade-A model under the registered rule, on a sample audited by the other model family. The runner then runs
    both arms: they need the issue text, the loop's development processes do not.
  - Every study report states the limit plainly: the vault was protected only from the loop's code, not from
    processes running as the same user.

### 6.4 Missions: the loop's engineers

This is the workforce coding mission, available over MCP (plan Revision 4, B1 and B2), with PR #3's design mission
folded into it:
- **Premortem.** The task's standard of done, competing failure hypotheses, and at most three questions. The loop
  answers its own questions only from evidence; otherwise they stay open in the pull request.
- **The engineer.** Claude Code or Codex, headless, in a disposable container. Writes go only inside the mission's
  copy. The network reaches only the model provider, through V7's egress proxy pattern. There are no other tool
  servers and no saved configuration.
- **Check, repair, stop.** `review_patch` with verification search; the repair ladder (three attempts, stop on a
  repeated or non-improving patch, then focused, then a person); the `VerificationState` the agent revises between
  rounds.
- **Multi-file patches.** The one-file limit in PR #3 goes.
- **Dogfooding.** G6 reviews the loop's own diff before the gate. That is a demonstration, not the gate's evidence:
  the gate rests on the frozen evaluator and the tests (section 6.5).

### 6.5 The gate and the evidence package

A candidate change becomes a draft pull request only when all of the following hold:
1. G6's suites pass in split groups under Docker bubblewrap. Agent-written code never runs on the owner's Windows
   host (designer's call). The Windows and macOS coverage comes from GitHub's CI on the pushed draft branch, and the
   pull request stays a draft until that run is green.
2. No bound file changed. If one did, it goes to a `needs-issuance` draft pull request and is never counted as
   accepted.
3. The frozen development evaluator shows the constraint's measure improved under `EvidencePolicy`, with no new false
   acceptance on the frozen adversarial cases and no worse correct acceptance.
4. G6's integrity checks on the diff find no weakened test, runner change or shortcut.
5. A reviewer from the other model family finds no blocking defect.

The evaluator's custody:
- The evaluator code and its frozen case manifest live in a read-only checkout pinned by hash, outside the
  candidate's write scope.
- The loop never imports candidate code into its own process.
- **What the evaluator scores** (designer's call): a held-out split of the development pool, cut by task with a
  seeded rule.
  - It is disjoint from the cases that exploiting and elevating tune on.
  - It is never mounted in a mission's container, so the engineers never see it.
  - `EvidencePolicy` allows five evaluations per held-out split (at least 30 cases, at least 5 discordant pairs).
- **After the fifth evaluation,** the loop drafts a fresh split from newly harvested development tasks, as a
  `protected-change` pull request. The owner's merge applies it, which is the person's confirmation the meta-harness
  requires. That costs the owner one merge per five gated candidates.
- **No candidate changes these:** the evaluator, the case manifest, registered protocols, the envelope (section 6.10)
  or CI workflows. The loop may DRAFT a change to any of them only as its own `protected-change` pull request, which
  evaluates nothing and is never part of a candidate. The owner's merge is the only way it applies.

**The evidence package** is the pull request body plus the files it links. It is built so the owner can check it in
minutes:
- the constraint, and why it was chosen (from the constraint log);
- the change, in plain words;
- **the certificate:** what was checked, by what, on exactly which bytes. That means the diff digest, the receipts of
  each confined run, the evaluator's identity and run receipt (`checked_by`), and the reviewer's record. Every field
  is filled from receipts, never by the agent that wrote the change. The certificate states its own limits;
- the measurement: before and after, with intervals and the sign test;
- what is still not established, and anything held back (section 6.9).

### 6.6 Budget and pacing: subscriptions, not dollars

The owner's rule:
- Use the full weekly allowance of each subscription, since it is paid for anyway.
- Pace it so continuous running lasts at least five days of seven.
- Stretch it further by sending simpler, non-critical work to cheaper or local models.

- **The meter.** For each provider (Claude Code, Codex), usage is read from each session's own result record (tokens
  by kind), and from what the command-line tools report about limits and reset times. Where a tool reports only that
  a limit was hit, the meter estimates the allowance from the first observed cap and marks it as an estimate. The
  builder verifies what each tool reports.
- **The pacing rule.** On day *d* of the provider's week:
  - the target use is the allowance × *d*/7;
  - the ceiling is the allowance × min(1, *d*/5);
  - in the last two days, the loop fills up to the whole allowance.

  A session starts only if what is already used plus that session's worst case stays under the ceiling. When a
  session window or the weekly cap is reached, the loop waits for the reset. It never falls back to API credits.
- **Whose usage counts (designer's call).** The whole account's, wherever the tools report it. The owner's own
  sessions and any builder sessions share the subscriptions, so their use counts against the ceiling and the loop
  gives way. Where only the loop's own sessions can be measured, the account-level figure is estimated from the
  limit and reset signals, and marked as an estimate.
- **The owner's reserve (designer's call).** 20% of each allowance is kept for the owner's own work until the last 24
  hours of the provider's week, then released to the loop. The share is a setting.
- **Host-model token usage.** It is read from the command-line tools' own session records by new code outside
  `g6/core`. `g6/core/host_models.py` discards usage today, and it is certificate-bound, so it is not edited.
- **The ledger.** One budget ledger: the operator's, extended with these provider windows. PR #7's `BudgetBook` is
  merged into it; it is not kept as a second ledger.

### 6.7 Model routing

Every task carries a grade, through the existing `llm_router` extension and the host-model providers:

| Grade | Work | Models |
|---|---|---|
| A, critical | Anything that becomes evidence, changes G6's code, decides a verdict, or is said in public (pull request text, issue replies) | The strongest Claude and Codex models |
| B, routine | Summarising runs, sorting failures, ranking tickets, drafting triage labels | Claude Haiku, Codex's smaller model |
| C, simple | Condensing logs, finding duplicates, embedding search | Local models on the owner's machine (Ollama: gpt-oss:20b, qwen2.5-coder:7b, nomic-embed-text) |

- A grade B or C output never counts as evidence.
- **Routing corrects itself (designer's call).** One in ten grade B or C outputs that feed a decision is redone at
  grade A. If they disagree more than 10% of the time over 20 samples, that task class moves up a grade.

### 6.8 Issues

The owner decided that the loop reads issues, answers them publicly and works on them by itself.

- **Read and sort.** New issues and comments, sorted at grade B into bug, question, feature request, security,
  duplicate or spam.
- **Reply.** Grade A, in public. Every reply carries a one-line disclosure:

  > Written by G6's automated maintenance loop (its code is in this repository); a maintainer reviews every change
  > before it merges.

  The rules for replies:
  - No promised dates.
  - No claim beyond the evidence, and never "verified".
  - Ask for a reproduction when one is missing.
  - Link the draft pull request once there is one.
  - At most one unprompted reply per issue per day.
  - Never argue. Spam is labelled and left.
- **Security reports.** Never discussed in public. The loop points to the repository's private vulnerability
  reporting, labels the issue, and flags it in the digest for the owner.
- **Work.**
  1. A bug report is reproduced first, as a failing check in a confined run.
  2. Only then does it become a ticket, ranked by the constraint controller.
  3. Its mission produces a draft pull request that says "Fixes #n". The issue closes when the owner merges it.
- **Issue text is untrusted data.** This is the defence against instructions planted in issues:
  - The reply drafter runs with no tools and no write access.
  - A mission made from an issue gets its standard of done from the loop's own reproduction, never from the issue's
    words.
  - Nothing in an issue can change the loop's configuration, budget, envelope or targets.
  - Missions never fetch links from issues.

### 6.9 Transparency: everything public, cleaned before it is pushed

- **Code.** All of the loop is in this repository under `src/g6`, with tests.
- **Records.** Pushed to a public branch, `g6-loop/records`, after every cycle, append-only:
  - the budget ledger;
  - the constraint log;
  - each cycle's decisions;
  - evidence packages;
  - study registrations and results;
  - issue actions;
  - the agents' session transcripts.

  A weekly digest goes in the same branch and in one comment on a pinned "Loop status" issue: what the loop did,
  usage against the pacing rule, the current constraint, pull requests ready for the owner, and any classifier or
  permission stops.
- **Cleaning.** Before anything is pushed, it passes:
  - the public export scan (`tools/export_public.py --scan`);
  - the local-path patterns of `tests/test_no_local_paths.py`;
  - a key and token pattern scan;
  - size limits.

  Anything that fails is held back locally and counted in the digest as "withheld", never pushed. G6's sanitizer
  today redacts only home-folder paths (a known open defect), so a stronger cleaner is a precondition of phase 2.

### 6.10 The envelope: what the loop may do, and what it never does

**May:**
- run missions in containers it starts;
- push branches named `g6-loop/*` and the records branch;
- open draft pull requests against the line of record;
- comment on, label and sort issues on g6solver;
- run development evaluations;
- draft study protocols as pull requests;
- use the subscriptions within the pacing rule, and the local models.

**Never:**
- merge;
- push to `main`, `master`, the release line or any branch outside `g6-loop/*`;
- tag, release, publish to PyPI or deploy the site;
- edit a certificate-bound file, except in a `needs-issuance` draft pull request;
- edit its evaluator, frozen cases, registered protocols, this envelope or CI workflows;
- spend API credits;
- read credential or key files except through G6's doors;
- touch the owner's pilot folders;
- stop a container it did not start;
- retry an action a classifier or permission prompt stopped;
- open anything on another repository.

**The loop's GitHub identity (the owner's decision O3, 2026-10-29: a GitHub App):**
- The loop acts as its own GitHub App, installed on g6solver only, not with the owner's login. Its permissions are
  contents, pull requests and issues (write), plus actions and checks (read). It has no `workflows` permission, so it
  can never change CI files, and no `administration` permission, so it can never change the repository's rules.
- Repository rulesets (available for public repositories on GitHub Free):
  - every branch outside `g6-loop/*` can be created, changed or deleted only by the owner, so only the owner merges
    into `main` and the release branches;
  - no tag can be moved or deleted, and only the owner creates tags.

  The envelope is then enforced by GitHub, not only by the loop's code. The rules go on after the App is installed
  and after the first launch.
- The identity's token is held like the sign-ins: never inside a container.
- Issue replies then appear as the loop, not as the owner.
- Until the owner sets this up, the loop pushes nothing (P1 needs no pushes from the loop; P2 needs this).

**Kill switch and health (designer's call):**
- A `STOP` file on `main`, which the loop's identity cannot change under the ruleset, or a `STOP` file in the loop's
  local folder, halts the loop at its next tick. Removing it resumes the loop. A `loop:stop` label on the pinned
  status issue also halts it, and the loop never removes that label itself.
- Three failed cycles in a row pause the loop and post a status comment saying why.
- The loop writes a heartbeat. A scheduled task on the owner's machine restarts it after a reboot.

## 7. Integrating pull requests #2 to #7

Every new package becomes a proper G6 component:
- a block class with typed operations (so `g6_invoke` works and `G6_E_NO_BLOCK` goes);
- declared failure modes (`__g6_mitigates__`);
- a skill (`skill/SKILL.md`);
- SPDX headers;
- an import-linter contract in `pyproject.toml`;
- a regenerated catalogue and a site catalogue entry;
- maturity per DECISIONS #36;
- tests.

None of the six pull requests touches a bound file, and blocks need no change to `registry.py` (which is bound), so
the certificates stay as they are.

**Branches and history (designer's call):**
- The owner's branches are brought into `claude/self-improvement` by merge commits, never by rebase. After that, the
  owner's pull requests are superseded.
- Everything reaches `main` by squash merge, the owner's practice since 2026-10-29. The integration branch's own
  history, including two older commits that carried a private path (on PR #1's and PR #7's branches), therefore
  never enters `main`.
- The integration branch never changes the first launch's content: launch is PR #1. The integration branch's pull
  request to `main` comes after PR #1's squash merge.
- Before every push, the integration's tree passes the public scan and `tests/test_no_local_paths.py`.

| PR | What it becomes | What it replaces or merges into | Gaps to close |
|---|---|---|---|
| #2 CIR and algorithm design | Two components. `algorithm_design`: search, fuse, the catalogue and ontology. `complexity`: atlas, impact cone, simplification proposals. The atlas feeds the premortem (which modules are risky) and the mission's standard of done | `verification_controller` becomes G6's experiment selector inside verification search (E1). Per Revision 4 it stays off by default until Gate C (V11) shows it helps. Its own `Receipt` type gives way to `repo_tests` receipts | No block; no conformance |
| #3 Autonomous algorithm design × SWE | Its design mission becomes the coding mission's algorithmic strategy. The 22 kernels are callable through the `algorithm_design` block, all through one `invoke` path (today 19 go through one and 3 through another) | Merges into the workforce coding mission and patch review; keeps no parallel mission | `ProposalAgent` gets real Claude Code and Codex implementations. Multi-file patches. Isolation read from confine's backend record (bubblewrap counts; today only AppContainer does). Receipts through `repo_tests`. A persistent ledger. Wording: "certified host registry" and `VERIFIED_*` file names |
| #4 Cognitive bootstrap and heuristic discovery | Its prompts join the one prompt catalogue. Heuristic proposals become meta-harness `Proposal`s | `mutate`/`assess`/`promote` give way to the meta-harness's propose, gate and promotion, so there is one gate | No tests yet; a yes/no `approved` flag; CI failures |
| #5 Goal trees | The premortem's breakdown of a task (B1) uses `propose_goal_tree` through the routed model, keeping its bound of 100 calls | Extends `goal_engine`, which gets a block | No block |
| #6 Metacognition and critical-thinking prompts | Entries in `align_prompt_library`, each tied to a pipeline stage (Propose, Test, Gate, Approve; premortem, mission, venture) | Its own `render_prompt` gives way to the library's `render` | `install_catalog` goes through the block's sensitive-operation gate. The meta-harness evaluates each prompt by whether using it raises the development measure |
| #7 Self-maintenance control plane | The loop's controller (section 6.1) and its MCP operations: constraint, plan, status, digest | `BudgetBook` merges into the operator's ledger (section 6.6). Approvals go through the operator's queue and the owner's merge. `UserSignal` is fed from issues (section 6.8) | The yes/no `operator_enabled` flag. Agents now run with writes and network on; that becomes the mission container's rules. The test typo (`consented_sessions`). `assess_improvement` never called. No scheduler. A budget in tokens per day rather than subscription windows |

**The outside review's five gates, and its reference task:**
1. **One MCP engineering component.** Discovery, cost estimates, validated kernel invocation, complexity analysis and
   synthesis proposals as typed MCP operations: the blocks above, plus the mission's tools.
2. **A real coding agent.** The mission's engineers are Claude Code and Codex. Patches can span several files.
3. **Prompt and metacognitive orchestration.** One catalogue, with each prompt tied to its stage. The meta-harness
   evaluates the prompts rather than just storing them.
4. **End-to-end verification: the reference task.** A coding agent, working through G6 over MCP, repairs a
   reproducible multi-file bug. The run must show:
   - bounded execution;
   - passing tests that are independently observed;
   - preserved behaviour;
   - an auditable decision trail;
   - no unapproved changes.

   In the same run, a planted incorrect patch is rejected. It is shown on Windows and in a Linux container.
5. **Integration and release testing.** Merge the branches onto the line of record, then: CI, installed-MCP smoke
   tests, and one reproducible demonstration on two platforms.

## 8. Phases (each test first; a fresh reviewer for each security-sensitive change; CI green)

| Phase | Delivers | Exit |
|---|---|---|
| P0, now | This design and its plan. No code on the launch branch | The plan is reviewed |
| P1, after the launch fix pass | The six pull requests integrated (section 7), duplicates removed, trust gaps closed | Every new operation callable through `g6_invoke`. The review's gates 1 and 5. CI green |
| P2, shadow | The controller, the budget meter and pacing, routing, the scheduler, the cleaner and the records branch. The loop measures, names constraints and plans, but pushes only records | One week within the pacing rule. Records public and clean. The digest posted |
| P3, self-changes | The mission over MCP with real engineers; the reference task; the first draft pull requests from the loop with evidence packages | The reference task passes on both platforms. At least one loop pull request a fresh reviewer finds sound |
| P4, issues | Reading, replies and work on issues | The reply rules hold on planted issues, including injection attempts |
| P5, the north star | The study's protocol is drafted, audited across families and merged by the owner, then run and reported | Results published, whatever they show. Repeated on fresh tasks |

- At most three builders run at once, launch builders included. Launch keeps first call.
- P1 starts when a builder slot frees after the launch fix pass. It works on its own branch and never changes launch
  content.
- **B2 belongs to this programme (designer's call).** B2 is the mission over MCP, the repair ladder and the
  `VerificationState` of plan Revision 4. Its build is P3's first part, owned by this plan's builder, so no two
  builders work in those modules.
- **Bound files.** A needed change to a certificate-bound file (such as the post-launch fixes to `confine.py`: the
  `/tmp` change and the linked-Python fix) waits for the one post-launch issuance window. That window is run by a
  supervised session with the held sources, never by the loop.
- **Dependencies on the owner's decisions** (section 9, all answered on 2026-10-29):
  - P1 needs none.
  - P2 needs O3, the App, installed.
  - P3's live runs need O1's relay, built and shown to work for each command-line tool.
  - P5 states O2's same-user limit.
- V9 and V10 continue as registered.

## 9. Calls

**Decided by the owner:**
- the north star and its measure;
- the owner's role (check the evidence, merge);
- the subscriptions and the pacing rule;
- cheaper and local models for simple work;
- the loop pushes branches and opens draft pull requests;
- public replies to issues, and working on them;
- everything public;
- integrating all six pull requests;
- the theory of constraints as the controller;
- launch first.

**Designer's calls (the owner may overrule):**
- the 20% owner's reserve;
- at most two loop pull requests waiting on CI;
- replay-based gate decisions;
- the routing check (one in ten sampled, over 10% disagreement);
- the disclosure line on replies;
- security reports kept out of public replies;
- the kill switch and the health pause;
- bound files only through `needs-issuance` pull requests;
- the experiment selector off by default until Gate C;
- `python -P -m g6.self_maintenance`, not a new CLI command;
- the records branch;
- cross-family review;
- the 24-hour release of the reserve.

**The owner's decisions, answered on 2026-10-29:**
- **O1, the sign-in custody: yes, on one condition.** The subscriptions may be used headless and autonomously,
  through the relay that adds the sign-in to requests (section 3), "as long as claude and codex do not expose their
  api to external users". Section 3 says how the loop keeps that condition.
- **O2, the study's custody: no second Windows account.** The study runner shares the owner's user, and every study
  report states the same-user limit (section 6.3).
- **O3, the loop's GitHub identity: a GitHub App,** created by the owner through GitHub's manifest flow, plus
  repository rulesets (section 6.10). The App's private key goes straight into a folder only the owner's user can
  read, never into a container. Until the App is installed, the loop pushes nothing.

**Left to the builder:**
- how each command-line tool reports usage and limits (checked by running it, not assumed);
- the scheduler mechanism on Windows;
- file layouts;
- the exact reason-code-to-cause table;
- the cleaner's patterns, beyond those named here.

## 10. What G6 may say, and when

- **"G6 engineers itself"** is backed by the public trail: the loop's pull requests merged after their evidence, and
  the constraint log. Copy states it as measured, for example "N changes G6 made to itself have been merged after
  independent checks".
- **"Exceeds the state of the art"**: only after the registered north-star study shows it, and exactly as measured.
- **Until then:** what was measured, with its limits, and the status line.

## 11. P2-shadow, as built (2026-10-31)

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been
established.

The owner added on 2026-10-31: the loop should work towards improvements with no intervention from them, on any
cognitive work, with programming as the first toy model. So P2 was built first as a local shadow, on any domain.

**What it delivers:**
- **A domain seam** (`g6/metaharness/domain.py`): a name, development cases, a sealed split, how a profile runs on a
  case, an independent judge, and failure records. Nothing in the loop imports or assumes coding, and a test holds it
  to that. A domain with no independent judge is refused, because self-grading is not improvement.
- **The tick** (`g6/metaharness/loop.py`), in the shape of section 6.1: measure, name the constraint, propose within
  the editable schema, gate under the unchanged `EvidencePolicy`, record. It runs on `tune`, the recovery, reflection
  and `FailureTraceStrategy` proposers, `identify_constraint` and the gate as they were. The constraint log is a local
  hash-chained file.
- **The envelope**, in code: a paced week per provider on the operator's ledger, the owner's reserve and the whole
  account counted (`pacing.py`); a `STOP` file, a heartbeat and a pause after three failed cycles (`envelope.py`); one
  tick at a time; refusal when the evaluator, the envelope's own code, a frozen case or a sealed byte changed; the
  sealed split shut in-process while the loop develops; a loop folder that may not overlap what judges it.
- **Adoption with no person, in shadow only** (`g6/metaharness/shadow.py`). What the gate recommends becomes the loop's
  head in a separate ledger, so the loop compounds, and the monitor rolls it back if it gets worse. A person's profile
  store never moves, `activate` and live runs are refused, and each row carries the loop's `auto:` name. Nothing goes
  outward: no push, pull request, merge or tag.

**What the two test domains showed.** Each ran with `FakeLLM` only and a planted weakness, one a coding-shaped domain,
the other V6b's GSM8K task, check and exact-answer oracle over generated word problems (the dataset is not in the
repository). The same code adopted a real gain on both, built on it across ticks, and scored better on sealed cases the
test ran itself. This shows the machinery works end to end. It does not show that G6 improves anything real: the
weakness was planted and the models are stubs.

**What remains:**
- **The rest of P2:** the scheduler, the cleaner (a precondition of pushing anything), the records branch and digest
  (these need O3, the App), model routing (6.7), a `STOP` on `main` and the `loop:stop` label, the yield measurement of
  6.3, subordinating work beyond failure traces, and merging `BudgetBook` into the operator's ledger.
- **O1:** real token usage needs the relay of section 3. Until then the meter's unit is model calls.
- **P3 onward:** the mission, code changes, draft pull requests and evidence packages, the evaluator in a read-only
  checkout outside the loop's process, fresh held-out splits drafted by the loop, issues (P4), the north-star study
  (P5), and method changes through `method_rca`, which need `compare_methods` and a person.

**Rulings made while building, each with its cost if wrong:**

| Ruling | Cost if wrong |
|---|---|
| **For the owner: one person step stays.** A person enrols a domain, and again for each fresh held-out split. Someone must say what "better" means, and it cannot be the thing judged | The loop stops after five gated candidates per split until a person acts. This is the distance left from "no intervention" |
| **For the owner: `person_free_adoption_outside_shadow`, off.** With it on, this phase still moves nothing: no path out exists | None today. Wiring it in P3 is a separate decision, and the store's rules would need changing, since they refuse any `auto:` name |
| The tick lives in `g6.metaharness`, not `g6.self_maintenance`, because the layer contract forbids that import | Section 3's `python -m g6.self_maintenance` cannot hold; the entry will be `python -m g6.metaharness.loop`. Cheap |
| Adoption is a subclass with its own ledger, not `activate` with a made-up name and not a change to the store | `ShadowHarness` overrides a private method, so a refactor of `MetaHarness._head` can break it. Tests would catch that |
| One workspace for all adoptions, so held-out uses carry over | None found. A fresh workspace per adoption would have let a split be spent for ever |
| "Sealed" means section 6.3's pool. The gate scores a seeded cut of the development pool, as 6.5 says | If the owner meant the gate to score the sealed split, one line changes, and the study's pool would be spent |
| "Independent" means four checkable facts: answers not by the caller or a model, not a model's opinion, a source file a digest can pin, and a non-answer rejected | `answers_by` is a declaration. A dishonest adapter can claim it. The adapter is host code, not something a candidate can change |
| Shares of the pool: 25% tuning, 15% selection, 35% held-out, 25% monitor, by a seeded hash per case | A pool under about 90 cases is refused. Other shares may suit real work better; changing them is a re-cut a person must accept |
| The monitor uses its own split, the judge's verdict on both versions, the pairs that disagree, and each case once per adoption | It needs at least 18 disagreeing pairs. It catches a collapse, not a modest regression: a change 10% worse stays. The gate remains the main defence |
| A failed cycle is refused, no winner, blocked, no constraint, rolled back, error or sealed breach. Waits are not cycles | A loop with nothing left it can improve pauses after three ticks and needs a person. The alternative was burning budget unseen |
| A sealed breach pauses at once, though nothing was read | One resume. The guard is in-process: a child process or native code is not stopped, so P3's container cannot rest on it |
| Re-enrolment refuses held-out cases that proposers saw or a spent split held. This is stricter than the meta-harness, whose limit counts per digest | A small domain can run out of cases, and a person must find new ones |
| `OperatorStore.settle` gained `metered`: the host's count sets the charge, up or down. A run's report still cannot lower one | If a domain's executor bypasses the declared model callers, its calls go uncounted and the ledger undercharges |
| A tick charges one provider: the first with room | Real work may split across providers. Harmless until O1 |
| `BudgetBook` stays for now, on the person-approved `run_one_cycle` only. The loop does not use it | Two ledgers exist until it merges, against section 6.6 |

## Appendix A. The outside review's points, as acceptance criteria

An outside review the owner shared on 2026-10-29, in plain words. Every point is an acceptance criterion of P1 or
P3.

1. **Three levels, kept apart:** implemented components, integrated capability, and capability reachable over MCP.
   Having an algorithm engine, a coding harness and an agent interface does not make an autonomous software
   engineer.
2. **What it found missing:**
   - the algorithm-design package has no G6 block, so `g6_invoke` refuses it with `G6_E_NO_BLOCK`;
   - its 22 algorithm functionals are reachable only from Python;
   - its design controller has no real model behind its proposal interface;
   - its coding and repair loop handles one Python file;
   - the prompt templates are not connected to any stage or evaluated;
   - formal correctness of arbitrary agent-written code is not established;
   - no full multi-file workflow has been shown.
3. **The five integration gates** in section 7: one MCP engineering component; a real coding agent, beyond one file;
   prompt and metacognitive orchestration, evaluated; end-to-end verification; integration and release testing,
   including installed-MCP smoke tests and a reproducible demonstration on two platforms.
4. **The reference task that defines completion,** in section 7, gate 4. A coding agent repairs a reproducible
   multi-file bug through G6 over MCP, and the run shows:
   - bounded execution;
   - passing tests that are independently observed;
   - preserved behaviour;
   - an auditable decision trail;
   - no unapproved changes.
5. **Three guarantees, never merged:**
   - **the correctness of a trusted algorithm,** under its stated assumptions and its trusted implementation or
     proof;
   - **the correctness of one execution,** backed by an independently checked result or certificate;
   - **the correctness of a whole generated application,** which needs a sufficiently complete specification and
     verification of the behaviour that matters. Passing tests alone never establish it.

   Copy and evidence packages say which of the three they offer.
6. **How to describe G6 until the reference task succeeds:** as a harness framework with substantial implemented
   capabilities, not a finished autonomous software engineer. Under DECISIONS #36 the word "experimental" is not
   used. The status line says the rest.
