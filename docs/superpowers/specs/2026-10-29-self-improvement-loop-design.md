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
  permission stops. For now a daily digest stands in for it, local only (section 11).
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
  local folder, halts the loop at its next model call. Removing it resumes the loop, except that a runner it ended needs a
  person's start again (section 11). A `loop:stop` label on the pinned
  status issue also halts it, and the loop never removes that label itself.
- Three failed cycles in a row pause the loop and post a status comment saying why. A failed cycle is a fault, not a
  candidate that lost (section 11).
- The loop writes a heartbeat. A scheduled task on the owner's machine restarts it after a reboot (section 11 has the
  command; the owner registers it).

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
  tick at a time; refusal when the evaluator, the envelope's own code, a frozen case or a sealed byte changed, as a tick
  starts and again last thing before an adoption is written. The adoption is written only if the digests the tick read
  before it developed anything are the ones on disk at that last check, which runs with the adoption's row already made
  and under the lock that writes it, sealed bytes first and digests after; the row carries those digests, and a change is
  a refusal of its own in the cycle's row, which the next tick refuses again. `ShadowHarness.adopt` is the one function that
  writes an adoption. That last check is the harness's own, given it when the loop binds it (`_still_frozen`, the one
  reading and comparison the loop calls too), and only its own success value, a `Held`, lets the write through; a caller's
  check can only add a refusal, and a harness built with no check adopts nothing. One rule codes a cycle (`_coded`): every
  status that held, the cycle's own end among them, is kept in the row's `statuses`, and the first in `RANKED` gives its
  code: a rollback (it has happened, and the log counts generations by it), then a frozen change, then the safety stops
  (sealed breach, unmetered caller, classifier stop, barred caller), then the tick's reservation spent. A safety stop a
  frozen change outranks still pauses the loop, since the pause reads every status and is read before the frozen digests.
  A cycle that adopts nothing ends on the same check, so a change is `G6_E_LOOP_FROZEN_CHANGED` whether or not a candidate
  won, and whatever else held, a cycle that raised past its own guard included (`fault`, held to the tick's reading, or to
  the enrolment when it read none); a wait or a stop met at a model call writes no cycle, and the next tick's reading
  refuses the change. Every safety stop a cycle met is kept on the tick the moment its work ends, before it settles or
  does anything else that can fail, and no tick leaves with it held there alone. A tick has two ways out: it returns,
  having written the row that carries the stop, or having stopped the folder for a row the log would not take; or it
  raises, and on its way out writes the pause a restart reads (`_keep`). So a failure at any step after the stop was
  met, a row or none, then a restart, still pauses: the fault's own reading of the records refusing or raising, the
  runner's hand-off of a fault that never reached its row, and a tick nobody hands to a fault among them. The fault's
  row, when one is written, carries the stop too, and only what no row carries yet, so no status is said by two rows.
  What the handle holds of a tick is dropped as the next tick begins, before the lock is asked for, so a pass that fails
  before its tick's body runs says nothing of an earlier tick. Tests built from the code fail each call after the cycle,
  and each call on the fault's ways out, in turn. A pause the log will not take from a tick on its way out stops the folder at
  once, as a safety stop in a cycle the log would not take does, as a `STOP` a restarted handle reads, and a lost
  cycle's hand-off reads what the tick met itself, never what its caller passed. An interrupt or an exit that lands
  inside a write on the way out is held the same way: inside the row's write, the pause is written; inside the pause's,
  the stop; inside the stop's own, the stop is asked for once more; then it goes on up. Three in a row, one in each,
  leave only this process holding it. A pause that cannot be written after a
  row that was is left, as before: the row pauses as surely. A generation the cycle's harness made, an adoption or the monitor's
  rollback, is read from the chain of adoptions for every row the tick writes (`_made`), never from what the tick held
  when something after it raised, so the row of a cycle that failed after one keeps it, a rollback ranked first as on any
  row; a failed cycle that had adopted stays coded as failed, with the adoption among its statuses. The judge is pinned by files listed whole, never by following imports: the verdict function selected, by
  its file and qualified name, each check by its file and name, every regular file of any suffix under the roots that
  hold the verdict and the checks (the folders they are in, or the `roots` a domain declares), data files and archives as
  much as source, and every module loaded from anywhere outside those, G6 and the installation, by its bytes. A verdict or
  check in no such place, or a link or junction anywhere under a root, is refused at enrolment
  (`G6_E_LOOP_JUDGE_UNPINNED`), and one made later is a change at the next reading; no root, nor what a link under one
  points at, may be the loop's folder or lie around it, by path or by the identity the file system gives a folder. The
  judge is given exactly what was identified: an answer is admitted only as `None`, a `bool`, an `int`, a finite `float`,
  a `str`, a `list` or a `dict` with `str` keys, each of exactly that type, as a tree no deeper than 64; its encoding keeps
  dict order and float bytes, its identity is that encoding's digest, and enrolment builds the answer each judge reads by
  decoding it, a fresh tree that shares nothing with what the domain holds. Anything else, and anything that raises while
  it is read, is refused at enrolment by case id (`G6_E_LOOP_ANSWER_UNIDENTIFIABLE`); such a value keeps an identity of
  its own kind (framed type, value and state, no pickle or `repr`), so a cut's digest still says when it changes.

  **The freeze's threat model is honest drift (the owner's ruling, 2026-11-05).** A candidate cannot write the judge's
  files: proposers edit a view and candidates run confined. What the freeze guards against is a person or another process
  editing what judges in ordinary ways during a cycle, not code written to evade the check. So a person declares as a root
  every folder the judge reads or loads code from. What cannot be read is never a digest: one reader
  (`g6/core/reading.py`) takes every byte the freeze stands on and lists every folder it stands on, the sealed split's
  (`g6/core/vault.py`), G6's own code and the judging code the harness pins (`g6.integrity.build_manifest`), and the
  roots', and any failure, a locked file, a file another process holds without sharing, a folder that cannot be listed, a
  file gone mid-listing or an error of any other kind, raises. Each root is listed once, by the reader's own lister: it
  takes names from a folder's listing and asks each entry what it is by a stat that raises, never by a predicate that
  answers no for an entry it could not ask, the listing's own entries' among them (the standard library's walk reads an
  entry whose kind it cannot ask as no folder, and is nowhere on this path). A folder the listing goes into is listed
  whole or raises, and is asked again before it is gone into: one changed meanwhile is unreadable. A junction is a folder
  to that stat, so the sealed split and the manifest list what it holds under its name, as before, and the loop's own
  listings name it as a link and stay out. Every stat, open and listing of a listed entry is of the very entry its names
  spell (`exact`, the one rule for a name the platform rereads): Windows opens a last name `nul` as its null device and
  drops dots and spaces from the end of a name, so a path can answer for another entry. The rule asks the platform how
  it reads the path, hands the path back as given where it reads it as itself, and otherwise hands back the form it
  does not reread, so a folder named `nul` under a judge's root is gone into and a file named `nul` is pinned by its own
  bytes. A root is a caller's name, read as the platform reads it: a root spelled with a name the platform rereads is
  unreadable. An entry has one answer: the lister hands on what its stat found with each name, and a later ask that finds
  another entry (a folder where a file was, another file, a junction where a folder was) is unreadable, for the files
  the listing takes, for the loop's own link check, and before the walk goes into a folder. A test finds every stat,
  open and listing on this path from the code and holds each to the rule, and crosses every reader with such names as
  files and as folders. The manifest's patterns choose among the files of that one listing, as `Path.glob` reads them
  on Python 3.12, never by listing again; an error of any kind while listing or asking, one that is no `OSError` among
  them, is unreadable. The freeze's reading has one boundary that makes anything
  else that keeps it from being made, a domain's own code raising included, unreadable too, and a test built from the code
  lists every read and listing the freeze reaches and finds none outside the reader, no predicate that answers no, no
  function that lists twice, and no handler that catches less than every error but two that stand for no pinned byte; a
  second test counts every folder each digest lists and finds none listed twice; a third finds every function in that
  code that lists a folder and crosses each with every kind of entry (a folder that cannot be listed or asked, an entry
  gone since its folder was listed, a folder changed before it was gone into, a listing whose entries answer nothing but
  their names). G6's own code is listed once for the
  envelope; the judging code's digest, which the harness reads on its own, lists it once more. An unreadable reading refuses
  enrolment (`G6_E_LOOP_JUDGE_UNPINNED`) and every tick and adoption as a frozen change naming what could not be read,
  while it stands; nothing raises past them, so a cycle's last check keeps every status the cycle held, a safety stop
  among them. A folder under a judge's root that cannot be listed is in reach of the loop's folder as a link there would
  be, so the loop is not built on it and writes nothing, and the walk keeps every link it found past it. Under
  `__pycache__` only the cache the interpreter writes for a source file under the root is left out, by its own rule: the
  name `importlib.util.cache_from_source` gives at each optimisation level, in the `__pycache__` beside the source, the
  same whatever cache prefix the reading process has; every other file there is pinned, a file shaped like a cache with
  no source and another interpreter's cache among them. Every name the file system gives is pinned, one that is no text (a
  lone surrogate, as Windows allows) included. A declared root is checked as given, so one that is
  itself a link or junction refuses. Left out, as limits and not findings: a module imported from an archive outside the
  roots, a file run with `runpy` outside them, a plugin loaded with `importlib.util` outside them and kept out of
  `sys.modules`, a named (alternate data) stream of a file under a root, which ordinary reading never reaches and only
  code that spells its name does, while Windows and sync tools write named streams on files nobody changes (the owner's
  freeze threat model, follow-up review 1), a step's source text that `inspect` cannot read, which it cannot tell from
  none at all (a step's file under a root is pinned by its bytes), and a timestamp-valid bytecode cache replaced by hand (the
  runner runs with `-B` and a fresh cache prefix, so a cache under a root is not what it loads), as well as an edit in the
  moment after the last read and before the write, bytes changed and put back between the two reads, and state that lives
  in no file: a module's globals or a class's attributes changed as it runs, a closure's cells, a callable object's
  attributes. Each proposer, and the
  domain shown the measured view, reads a copy of its own; a sealed-split guard inside the process (limits below); a loop
  folder that may not overlap what judges it.
- **Adoption with no person, in shadow only** (`g6/metaharness/shadow.py`). What the gate recommends becomes the loop's
  head in a separate ledger, so the loop compounds, and the monitor rolls it back if it gets worse. A person's profile
  store never moves, `activate` and live runs are refused, and each row carries the loop's `auto:` name. Nothing goes
  outward: no push, pull request, merge or tag.
- **The runner** (`g6/metaharness/runner.py`, 2026-11-01): `python -P -m g6.metaharness.loop run <folder>` repeats a stop
  check, a tick and a wait, with no sleep over a minute. It holds an operating-system lock for its life, writes its
  heartbeat, survives a tick that raises and restarts safely, since all it needs is in the folder's records. A loop with
  nothing to try rests, 15 minutes doubling to a day, and the digest says so. The owner wanted it essentially hands-off,
  reviewed daily, working through the week. It still has no live engineers: section 3 holds those back.
- **Three MCP tools** (`g6/mcp/improve.py`): `g6_improve_start` follows the usual allow rule (`metaharness.improve`) and
  asks the person in the client's dialog; `g6_improve_stop` writes the `STOP` file and kills nothing; `g6_improve_status`
  only reads.
- **A daily digest** (`g6/self_maintenance/digest.py`): one local Markdown page a day under the folder's `digests`, under
  60 lines, with what lost beside what won. It replaces 6.9's weekly digest for now, and pushes nothing.
- **A cycle's research record:** four optional fields, `structure` (a regularity found in the failing cases), `resource`
  (what can be exploited), `obstruction` (why it does not hold generally) and `composed_from` (the earlier cycles it
  builds on). They stay empty when a proposer supplies none, the digest shows them, and the next tick's proposer reads
  them, with none of what the gate decided.
- **Numbered generations:** each row of `adoptions.jsonl` reads as a generation, in that one chain with no second ledger:
  its parent, the profile's digest, the change in plain words, the held-out figures before and after, and those four
  fields. A rollback is a generation too, so the lineage shows losses as well as gains. Status and the digest show the
  current one and the recent ones. These are the loop's own numbers, measured on the splits they name. They have nothing
  to do with G6's version, tags or releases, which stay the owner's.
- **What each tool reports of the account** (`g6/self_maintenance/usage.py`, checked by running them on 2026-11-01):
  `codex app-server` (0.165.0) answers `account/rateLimits/read` with no model call, so the loop gives way to Codex's
  weekly percentage and its limits. Codex says this protocol may change, so one field that makes no sense, a NaN or a
  figure off its scale among them, loses the whole reply. A reply that does not read, a read that fails and a reply with no
  week in it each leave the last good figure standing until a good read replaces it or its reset passes, with what is
  charged meanwhile on top, because the owner uses this account too and the meter must not read low for want of a tool
  (the coordinator's ruling, 2026-11-01). Past its reset a figure counts for nothing, though a limit it came with stands to
  its own end, and the tool is read at once, not up to 15 minutes later, so the old week's use never holds back the new week's
  first tick. A tool that has not caught up can still answer with a reset already gone: that figure is last week's, so it is
  left out and the meter is on its own count until a read says otherwise. That figure is held in memory, so a restart whose
  first read fails
  starts on the meter's own count. Claude Code (2.1.316) lists no such command, so Claude's meter stays on model calls.

**How to run it.** A folder holds `settings.json`. A tool given no folder uses the one beside G6's state folder, `~/.g6-improve` by
default, because the MCP server, the completion loop and test runs all write in the state folder and nothing but the loop writes
here. It stood inside the state folder until 2026-11-02, and no real run had used it: a folder there is now only reached by name,
stop included. An allowance is in the meter's unit, model calls, and counts as an estimate until `"estimated": false`. `reserve_share` defaults to the owner's 20%, and `"read_account": false` turns off
the Codex read:

```json
{"domain_file": "<a Python file outside this folder>",
 "windows": [{"provider": "claude", "allowance": 5000, "week_start": "2026-11-02T00:00:00+00:00"}]}
```

The domain file is the person's own, as `G6_METAHARNESS` names one, and lies outside the folder, which the loop writes and
what judges it must never be in. It defines `LOOP = {"domain": ..., "proposer": ...}`, with `method`, `seed`,
`audit_per_tick` and `eval_calls` where they differ from the defaults. All of a folder's calls, its domain's and its
proposer's, are billed to one provider, because the callers are the domain's and nothing in the loop can see where they go
or send them elsewhere. With one window that is the window. With more, `LOOP` names it in `provider`, and without that
nothing runs (`G6_E_LOOP_NO_BUDGET`). A tick reserves only on that provider and waits at its limit however much another
window holds, so a second subscription's allowance is used by a second folder whose domain's callers go there. A host caller is
checked against that statement, so a folder that names one provider and signs in to another is refused with
`G6_E_LOOP_PROVIDER_MISMATCH`, the proposer included. A local caller is on nobody's week and fits any. Nothing else is read from
either file, so neither
can reach `person_free_adoption_outside_shadow`. Enrolling pins the domain file's digest, so an edit is a changed frozen
item: `g6_improve_start`, a restart and lifting a pause each refuse it before it can run, and what runs is the bytes that
were checked. The pin holds that file, not what it imports. Then, at a terminal, a person enrols it and types the domain's
name back:

```
python -P -m g6.metaharness.loop enrol <folder> --as "Your name"
```

`g6_improve_start` then starts the runner; lifting a pause is `resume`, with the same arguments, also at a terminal.

**Run it from a pinned install.** The envelope's digest covers the code a run imports, so with G6 installed in editable mode, a
builder's edit, or a branch switched in that checkout, stopped every tick with `G6_E_LOOP_FROZEN_CHANGED` until a person enrolled
again. So a folder can name the interpreter its runner uses, and should for any run that matters:

```json
{"python": "<a venv's python>", "domain_file": "...", "windows": ["..."]}
```

The choices here are the smallest that do it (designer's call). `python` is one more setting, so the dialog shows it, the start's
digest binds it, and a restart holds to it. It must be a full path to a file, or nothing is asked (`G6_E_LOOP_BAD_SETTINGS`). The
runner then starts with `-I` and not `-P`, because `-P` leaves `PYTHONPATH` standing, and with `PYTHONPATH` set the same venv
imported a working tree: `-I` was run and imports the venv's own G6. With no `python` nothing changes, and the dialog says an
edit to that install stops the ticks. The digest now covers where G6 is installed as well as its bytes, so a person enrols with
the pinned Python, or every tick is refused, and a working tree that holds the same bytes cannot stand in for the install.

The person makes that venv and keeps it at a fixed commit, with a plain install: a venv holding G6 in editable mode follows its
checkout, whatever interpreter runs it, and nothing here checks that. It does not stop a person, or a builder with the owner's
rights, editing the venv itself. On Windows such a runner is also a child of the venv's launcher (see the runner that ended).

This was run once, on the fake domain: a scratch venv with a plain install of this tree, enrolled from that Python, started from a
server whose own G6 was the working tree, with `PYTHONPATH` on that tree. The two digests differed, and the tick adopted, which a
runner that had imported the working tree could not have done.

**A week is the provider's.** `week_start` is typed by a person, and nothing held it to the provider's week, so the loop could treat
a tick as crossing a reset that had not happened, or spend hard just after a reset while it thought it was day 4. Where a tool reports
when its week ends, that now sets the week whatever the setting says: the day, the ceiling, when a reservation ends and when the
next ceiling rises all follow it, and the digest's account line says when it ends. `settings.json` is left as it is, so nothing
needs a new start. The week still open keeps the ledger book it began with, so what was charged in it, and a tick under way when
it moves, stay against the ceiling, and the week after opens a book of its own. The week moves in memory, so a restart moves it again
at its first good read, and until then is on the setting. That read finds the same book unless the setting's own week has turned
over in between, and then the account's figure is what holds what came before. Claude's tool reports no week, so its `week_start`
stands, and a person sets it from what `/usage` shows.

**A daily reviewer can take a generation back.** Until this, the only lever on an adoption that looked wrong was the stop:

```
python -P -m g6.metaharness.loop demote <folder> <generation> --as "Your name"
```

`<generation>` is the one the digest shows as the head, so a head that moved on since is refused rather than taken back unread.
It goes back one version, as the monitor may, and the loop runs on from there. The domain's name is typed back, as for `enrol`,
and a changed domain file refuses it before it runs. The row is a rollback in the same chain with the person's name on it, which
the lineage and the digest show as theirs, and it carries no figures, since nothing was measured. A rollback is now the one row
in that chain that can name a person. An adoption still cannot. The candidate can be proposed again: nothing yet keeps a generation
a person took back from coming back through the gate.

**Only a person's start lets it run.** `run` carries on a start that stands: of the starts and stops on record, the last is
a start a person agreed to in the client's dialog. With none and no stop in force it is refused, with
`G6_E_LOOP_NOT_STARTED`, before any of the folder's code runs, so a shell is no way round the dialog; a stop in force refuses
it with that stop's own code, never in other words. A stop ends the start for good: every stop through the tool is
first a line in `stops.log`, the journal of stops, a file no lock stands in front of, flushed to disk before its `STOP` file
is written, and a start records the journal it saw, by count and digest, so a restart or removing the `STOP` file by hand
brings nothing back until a person agrees again.

**One answer on whether a stop is in force.** `stop_in_force` gives it, and a start, its dialog, the runner's preflight and
gate, a tick's guard, status and the digest all take it; nothing else decides, and nothing else opens these files. The
reading is a value: it opens the `STOP` file, the journal and the records once each, and everything else, the decision, the
last start with any stop on record after it, the lift fields, who enrolled, any pause and the records themselves, comes from
those bytes in memory, under one digest of all of them. No argument skips any part of it, and every older reader
(`stop_standing`, `stop_seen`, `stop_reason`, `stop_to_lift`, `stops_asked`) is a field of that one full reading. A test
lists, from the code, every place G6 joins a path to these files or opens the records, and each is the reading, a writer,
or the question of whether a folder is a loop at all. Each file is read from one open handle, and its bytes stand only when
the file that handle holds, known by the handle itself (on Windows its volume and file index), is the regular file that
stands at the path once they are read, with as many bytes as were read; a file replaced or changed while it was read is
read again. A file is absent only when its open fails with not-found and absence is decided from the entries themselves,
by `lstat`, which follows no link: nothing at all is at the path, and the nearest entry above it is a folder that can be
listed. So a link, a junction or any other reparse point at the path, broken or not, is unreadable, as is a broken one
above it, an entry that cannot be looked at, a path under a regular file, which Windows reports as not found, and a file
that is a link or kept changing while it was read. A stop stands when the `STOP` file is not the one the last start lifted, when the journal's bytes are not the
ones it bound (bytes decide, never a count of lines, so a line a crash cut short is a stop), when a stop on record follows
that start, which it revokes until a person starts again, and with no start on record, when the journal holds any bytes at
all. Anything it cannot read or use, any of the three files, records that do not check out, or a start's lift fields of the
wrong shape, null included (one check, where the row is written and where it is read), is a stop in force with its own
code, `G6_E_LOOP_STOP_UNREADABLE`, never a value such as a count of -1 or nothing to lift, and never a raised error: a tick
ends at its next request, the runner refuses to run and its heartbeat carries that code, status says which file, and a
dialog or a start starts nothing. That holds for any failure at all, not a list of them: in the reading, in noting a stop,
in the tick's writer reload and every other stop path, a handler catches every exception, and a test built from the code
finds none that names a narrower list. Noting a stop decides absence by the reading's own rule, so a directory at the
records' path is said in the heartbeat as a stop not noted. The stop is honoured before anything else: the runner's
preflight ends on any stop in force, with its code, before its settings are read, and the dialog and a start give an
unreadable stop's code before any other refusal. One function makes every refusal of the dialog and the start, from the
reading it stands on: `G6_E_LOOP_STOP_UNREADABLE` whenever that reading could not be made, otherwise the refusal's own
code, and either way any stop in force is named (`stop_code`); a test lists every return of the dialog and the start from
the code and finds no refusal made anywhere else. Every statement of the dialog and the start that touches a file runs
inside its one handler, which a test also checks from the code, and the settings are read and checked by one function,
whose failures, however they fail, are settings to fix. The stop is made the same way: one handler, with only the folder
as given outside it, and a check of whether a folder is a loop's that cannot be made, however it fails, never keeps the
stop from being written there; a folder that cannot be found, or a stop that cannot be written, says so
(`G6_E_LOOP_STOP_FAILED`). A tick keeps the start it parsed against the digest of the very bytes it parsed, taken in
the same reading, and parses again only when the bytes read before a request differ. A dialog's terms carry its reading's
whole digest and the settings, and the dialog is shown only if all it stands on, checked again once it is made, gives the
same terms. A start records and runs on the settings it read before its locks, which the terms must match, so a file
changed after is one the runner's gate refuses; under the tick
lock and the stop lock it checks everything again on a fresh reading and refuses if its digest is not the one the dialog
showed, then writes its row; the stop lock is the one a stop's writer holds until its journal line and its `STOP` file are both on disk,
and a start refused there writes nothing. A stop that waited a second for that lock writes without it (the owner's
ruling), and its journal line can be read before its own flush is done, so the start flushes the journal to disk itself,
through a handle of its own, before its row binds it: no start lifts a line the disk may yet lose. It keeps that handle
open until its row is written and binds the file's identity (its volume and file id) with its bytes: when the reading
the start's terms stand on found a journal, that reading, the file it flushed and the reading it checks before its row
must all be that one file, or it writes nothing. A reading that found none binds the empty journal, so a journal made
after it is a stop that stands, and the start says so. Held open, the file keeps its id, and on Windows it cannot be renamed or removed until the start lets go,
so a journal renamed away and made again with the same bytes, never flushed, is never bound. Journal lines carry no
token, so a line a crash took back could be written again, the same bytes, by a later stop that the start's binding would
then not see. The row goes onto the approved reading itself: once the writer has opened the records and the journal is
flushed, a reading must be exactly the approved one before anything is appended,
and the ledger appends only onto the head it opened, so a crash or a failed write after the append can leave a start only
on approved bytes. A reading once the row is written must find those bytes with that one row after them, and on every way
out of the append, an exception included, anything else that landed is revoked by a stop row (a `STOP` file when even that
will not take); nothing is spawned.

**Where a row ends, and in what words.** The records, the journal, the `STOP` file and the log of refusals hold to one rule
for where a row ends: at its newline, or, for a last row with none, at the end of the file, which is how the ledger reads its
own file. So every append starts on a fresh line: a last row with no newline, which the ledger accepts and a dialog can show,
is ended before the next row is added, and a start's check of what landed holds to the same rule, so its row is never
joined onto the approved last row. The ledger itself is bound and unchanged; its writer ends the last row before each
append. Every write on a stop path makes bytes of text by one rule that cannot fail, UTF-8 with what it cannot carry (a lone
surrogate) spelled out with a backslash, so no caller's reason keeps a stop, its row, a refusal or a heartbeat from being
written, and every reader reads plain UTF-8 back. Tests list, from the code, every write in the envelope and every place
text becomes bytes on a stop path.

**One ending for the runner.** Every way a run ends goes through one function: a stop at the preflight, the gate, a stop a
pass reads, this process's own cannot-record stop, a folder that cannot be opened or held, or anything raised before the
hand-off to the running loop. A stop is noted in the records by a writer that never raises (what it could not note, the
heartbeat says). A stop in force only because a file cannot be read is gone with the repair of that file, and a row the
records would not take, or that a tick holding them kept out, was never there, so such a stop is also written through the
stop's own writer, which needs no tick lock, naming the code that ended the run (what it could not write, the heartbeat
says: `stop_not_written`). A tick's guard, its cut-off at a model call and a start that a stop met once its row was
written do the same. So a start ended on a stop stays ended until a person starts again through the dialog, whatever
became of the stopped row and once the file is repaired. A stop asked for needs nothing more, since it stands in the
stop's own readable files or on record, nor does the runner's own cannot-record stop, written through the same writer
when it was made; and records that show the start already ended on record need nothing either, so ending again on a folder
that stays unreadable adds no line once its start is ended. What this cannot cover: an ending that dies before its first
write lands, with the file repaired before any restart; an ending whose writes, the row, the journal and the `STOP` file,
all fail; and a `STOP` file a person wrote, whose row could not be written, removed by hand. A test lists every way a
start ends on a stop from the code. Then the day's page is written, the running loop's own or one from the reading of the folder; a page that
cannot be made from the records gives way to a plain one that says how the run ended and its code, never the old page left
standing, and a page that cannot be written even so is said in the heartbeat (`page_not_written`). Only then comes the
terminal heartbeat, with the code and reason of the reading that ended the run. A test lists every way `run` and a pass
return, and that every exception before the hand-off ends there too. Only the runner lock's holder writes the folder's
page and heartbeat: the preflight's reading comes first, and a stop it finds ends the run once the lock is held; the
starting heartbeat is written holding the lock for that moment; every ending is made inside the lock, never once it is let
go; and a person's command at a terminal writes no heartbeat. A test lists every page and heartbeat write from the code.
Only the lock's own contention is another runner, and that runner writes no page and no heartbeat over the live one's: on
Windows the lock is taken with LockFileEx, whose ERROR_LOCK_VIOLATION alone is another holder (the C runtime gives a held
lock and a denied access one errno), and on POSIX flock's EWOULDBLOCK (EAGAIN), or EACCES where Python builds flock on
fcntl. A lock that cannot be taken for any other reason, an I/O error, a denied access or an error never seen, a link
among them, raises as it is and is this runner's own ending (`G6_E_LOOP_CANNOT_OPEN`) with its page and heartbeat, since
no holder can be shown; so is anything that fails once the lock is held, the loop's opening included. Whether a runner is
alive is never read from a lock that cannot be tried: a start refuses, a stop says it cannot be read, and status says so
beside the stop. Status takes the one reading first and reports the stop in every reply; everything else it reads runs
inside its one handler, and what cannot be read or used, a record's field among them, is said beside the stop, never
raised.

An agreement binds what the dialog showed: the settings, the Python file, who enrolled it, the stop state as that one reading
saw it, and any pause. If any of that moves while the dialog is open, nothing starts and the person is asked again. A stop that
dies between its two writes leaves only its journal line, which every reader honours, and the next dialog says a stop stands
in `stops.log` with no file for it, rather than a start taking in a stop nobody read. A start never moves
or deletes `STOP`. It records the digest, the length and the modification time of the file the person agreed to lift, the time
first, and `STOP` stands unless all three still match, since bytes alone cannot say a person wrote again. So a stop has no
moment in which it can be lost: a crash at any point leaves the file as it was, or with the new stop in it. What was added
after the lifted bytes is the stop in force, and the next dialog names it. Any other change leaves the whole file standing,
an identical rewrite or a touch among them, and so do a time that cannot be read and a start with no time on record. A lifted
`STOP` stays on disk, and a person clears a stale one by deleting it. Each stop through the tool writes a token of its own into
the file, so it never repeats bytes already lifted, and it stands with no record having to be written. Two writes are not
seen: the same bytes on a file system whose time is coarse enough for a rewrite to land on the same tick, and a writer that
restores the old time on purpose. The start records the settings it agreed to, and a restart refuses others with `G6_E_LOOP_SETTINGS_CHANGED`, so changing `settings.json`, a
real allowance included, needs a new `g6_improve_start` even with no stop in between. A runner already alive keeps what it
read.

All of this is a check and not a boundary. A process with the owner's rights can reach this code or write the records, which
are hash-chained and not signed.

**A stop lands at the next model call, not the next tick (the owner's ruling, 2026-11-01).** A stop reaching the next call
matters more than the one evaluation it may cost. Each request of a tick asks `stop_in_force` and G6's own stop before it
goes out, so a stop that died before its file was written lands too, and a request already sent comes back first, and that can take as long as its caller allows. Nothing here ends one.
The tick then ends with no cycle: what ran is charged, nothing is scored or adopted, and the runner ends a second later. The
candidates it was working on race again after the next start, as after a limit. The reply and the day's digest say how many
requests it had sent, and a `STOP` file's note in the log carries the same under `cut_off`.

An evaluation a `STOP` file cuts off costs nothing of the split: no report was written, so it is not one of the five, as with a
limit, and the digest says it was dropped. G6's process-wide stop (`g6.core.stop`) costs one: `evaluate` checks that stop itself
before each case and keeps what it has as a partial report, which is recorded, and that code judges, so it stays. The cycle's
reason then starts by saying the evaluation stopped part way and still spent one of the split's five. A model call is the only
place a tick looks, so a domain's own long step with no model call runs on until the next one.

**Restart after a reboot.** The owner runs this once, in their own terminal, with the Python that has G6 installed and the
loop's folder (`pythonw.exe`, so no console window opens that could be closed by accident). `/SC ONLOGON` runs as the owner
when they sign in, with no stored password:

```
schtasks /Create /TN "G6 improvement loop" /SC ONLOGON /RL LIMITED /TR "\"<python folder>\pythonw.exe\" -P -m g6.metaharness.loop run \"<folder>\""
```

To take it away: `schtasks /Delete /TN "G6 improvement loop" /F`. A `STOP` file holds across a reboot: the task's runner
ends at once until a person lifts the stop through `g6_improve_start`. A second runner on the folder is refused, so the
task and the tool cannot both hold it.

The task only carries on a start a person agreed to, so before the first `g6_improve_start`, and after any stop, it does
nothing. That task covers a reboot and nothing else. To also bring back a runner that ended some other way (see below), use this in
its place, at the cost of a Python start every 15 minutes that does nothing while a runner is alive:

```
schtasks /Create /TN "G6 improvement loop" /SC MINUTE /MO 15 /RL LIMITED /TR "\"<python folder>\pythonw.exe\" -P -m g6.metaharness.loop run \"<folder>\""
```

**A tick that crosses a weekly reset stops there.** It reserved in the week that ended, and a new week's first ceiling is
its lowest, so the request gate refuses anything at or past the reset, retries included. A request already under way stays
in the week it was sent in. The tick ends blocked with `G6_E_LOOP_TICK_BUDGET_SPENT` and adopts nothing, and the next tick
reserves under the new week's ceiling. A `Retry-After` resend is held to the same line, and a wait that would reach the reset
is not taken. That check comes before the wait, the only boundary a resend has in `g6/core/llm.py`, which is bound, so a wakeup
that runs late can send one resend just past the reset, charged to the week that reserved it. No check closes this: a request
checked at the last moment can still arrive after the reset. An issuance window can narrow it to that.

**An unchanged head costs nothing to measure.** The head's run on the tuning cases is kept for each head, case set and
budget, and every proposer of every cycle reads that one view, so a tick on an unchanged head spends nothing there. A view
with a run the model failed measured the provider, so the next tick measures again. The view is held in memory, so a
restart measures once more. Only the shadow harness keeps one: `api.py`, which `JUDGING_PATTERNS` covers, and `propose.py`,
which `ENVELOPE_PATTERNS` covers, did not change, so `MetaHarness.propose` and `diagnose` still run the head on each call. A
model that drifts is seen by the monitor and the gate, which run the head on their own cases, and not by this view.

**What a tick reserves.** The most it can call, sized by what the pool gives its limits to spend on: the audits, one
measurement, the profiles each proposer can try on the tuning cases up to its dev budget, a race on the selection cases, and
an evaluation of the held-out cases up to `eval_calls`. A run's calls are what the models declare, or, on G6's own executor
with no step of the domain's, one call and the retries a profile can hold. At the example allowance of 5000 with the 20%
reserve and `Method`'s defaults, a domain that declares one call a run reserves 818 on a pool of 86, so it can start on day
1, and 1876 on a pool of 240, day 2. One that leaves a retry in its space reserves 1684 and 3166, days 2 and 4. A domain with
its own factory and `Model`'s default of eight calls a run reserves 6548 on a pool of 86, which no day of that week allows,
so it waits with `G6_E_LOOP_BUDGET_WAIT` until it declares less, a smaller `method` or a larger allowance. A tick still stops
at what it reserved. Retries inside a caller are not foreseen in this figure, so blank replies or a busy endpoint can end a
tick there. On `FakeLLM`, a week of one-call ticks on 86 cases at that allowance used 4402 of the 5000.

**A provider's limit is a wait.** A request that fails at a limit ends the tick there. The tick charges what it sent, gives
the rest of its reservation back and replies `G6_E_LOOP_BUDGET_WAIT` with the reset in `wait_until`. It is no cycle, so it
writes no result and never counts towards the pause, and an evaluation it cut off is not one of the split's five, since
nothing read it. The next tick waits for the reset, spending nothing. The reset is written to the constraint log as a row of
its own, no cycle, the moment the tick meets it and before it settles, whatever else the cycle met, so a restart waits it out
too and sends nothing, whatever failed after; only if that row cannot be written does a restart's first request find the
limit again. This process is told of the limit before that row is written, so whatever the row's write fails with, an
interrupt among them, the process that met the limit sends nothing more into it. The next tick to propose takes up what was left: the candidates proposed since the last
tuning run and not beaten in a race race again in its first cycle, and the reservation counts them. They are read from the
run ledger, so a restart loses none, and they are run again from the start of their race, on the same head only.

A limit is read only from what a model cannot write (`g6/self_maintenance/usage.py`), because no caller marks one, and
`g6/core/llm.py` and `g6/core/host_models.py` are bound: the start of the text a request raised, which is G6's own code or
status, and a host program's own fields. A server's body comes after that start and never decides it, and neither do the
words a host prints. The built-in HTTP callers and the Anthropic SDK are read reliably: a wait a server asked for is taken as
asked, and every other limit is looked at again after 15 minutes, for one refused request a look. Claude Code 2.1.316 was read
from the text of its program and has not been seen at a real limit. Its `api_error_status` of 429 and `api_error` of
`usage_limit_reached` are read as JSON fields of an error result, where a model's quotes are escaped. They land in the 300
characters its caller keeps only because the program writes them after its `usage` record, which no one promises, and a
notice with neither field reads as a failed case.

**Since 2026-11-05 each host's own output is read whole, by the caller the loop watches, before the bound caller cuts it.** As a
request goes out, the Claude Code or Codex caller really sending it becomes a `WatchedClaude` or `WatchedCodex`, subclasses outside
`g6/core`. `WatchedClaude` reads Claude Code's JSON result: on an error result, `api_error_status` 429 or `api_error`
`usage_limit_reached` is a limit, and `api_error_status` 529 is its model overloaded, wherever the program writes them.
`WatchedCodex` runs `codex exec --json` and reads only Codex's own events, `error` and `turn.failed`: Codex's `codex_error_info`
(`usage_limit_exceeded`, `server_overloaded`, `cyber_policy` and the rest), or where an event carries none, the program's own
fixed message at its start. A `turn.failed`, or an `error` of no kind G6 knows, is a failed request whatever the exit status or the
last-message file say. A model's words are in item events, inside JSON strings, and never read. What either reads goes to the
tick as G6's own code (`G6_E_MODEL_REFUSED`, `G6_E_LLM_RATE_LIMITED`, `G6_E_MODEL_AT_CAPACITY`) before any wrapper sees it, and for
a watched caller nothing else decides: the older text pattern is never applied to what it raises, its error stream included. A limit
or a model at capacity ends the tick as a wait: nothing is scored for either arm, no evaluation of the split's five is spent, and the
limit is in the log before the tick's charges are settled, so a restart waits it out. It is looked at again after 15 minutes, and
Codex's `account/rateLimits/read` holds the ticks after that. The account is still read once more just before a held-out
evaluation. What Codex prints without `--json`, as the plain caller runs it, is text and is never read; a host caller the loop does
not watch (one called from inside another caller, say) still reads a limit as a failed case. No recorded file on the machine held
`codex exec --json` output, so which of `codex_error_info` and the message an exec event carries was not seen, and both are read.

**Who pays is read from the caller's type.** `Model.paid` was the only guard on the rule that the loop never spends API credits,
and a caller with a key left it at its default and passed. Now, for each declared model and the proposer, seen through G6's
wrappers: `ClaudeCodeCaller` and `CodexCaller` are that provider's subscription, a subclass included; `AnthropicCaller`, which finds
a key by itself, and `OpenAICompatibleCaller` with a key are paid and refused with `G6_E_LOOP_PAID_MODEL`; `OllamaCaller`, a keyless
HTTP caller and `FakeLLM` bill nobody; and any other type is refused with `G6_E_LOOP_UNKNOWN_CALLER`, because nothing can say who
pays for it. A caller of a known type is judged as itself, whatever it holds. This is checked at enrolment and before each tick, and
`paid=True` still refuses. A wrapper of a domain's own passes if it holds its caller as `_inner`, as G6's do.

Enrolment can only read what a domain declares, so a wrapper can declare one caller and send through another. So the same rule is
held to what goes out: as each request of a tick is noted, the caller really sending it is read, and one that is paid, unknown or on
another provider is stopped before anything is sent, with a cycle on the record and a pause. Nothing in the bound files hands that
caller over, so it is read from the stack above the note, the first object there, which a test holds for each built-in caller.
A plain function that notes a request has no object, and is taken as what the model declares. A caller of a known type that sends
by some other means, or a subclass of one that overrides how it sends, is not seen: this is a check and not a boundary.
`g6/core/host_models.py` joined `ENVELOPE_PATTERNS`, since this rests on its classes.

**A classifier or permission stop is an outcome of its own.** Section 3 says the loop records such a stop, reports it and never
retries it, and until 2026-11-02 nothing did any of that: a refusal read as a failed case, and the next tick sent the same thing
again. Now a refusal ends the tick at once, and nothing more goes out of it. The cycle is `blocked` with
`G6_E_LOOP_CLASSIFIER_STOP` and a `classifier_stop` field that holds what was said and a digest of the request, never its text.
Nothing is adopted, an evaluation it cut off is not one of the split's five, and the day's digest has a line for it. The loop
then pauses until a person has read it and resumes, because the owner's rule for such a stop is to hold and ask, and any other
tick would rephrase it: a proposer's next prompt on the same case is a new request. After a resume the exact request is still
never sent again, across restarts, and reads as a failed case. Only the exact request is known: a changed prompt is a new one,
and only the pause stands between a refusal and that.

**A reply of unknown outcome is no such stop, and is recorded as what it is (2026-11-12).** A host reply G6 could not read whole,
or whose result states no status it recognises, holds its inputs as a refusal does: the tick ends, the loop pauses for a person,
and the same inputs are never sent again, across restarts. No classifier said anything, so the cycle is `blocked` with
`G6_E_LOOP_HOST_UNKNOWN` and an `unknown_hold` field (what was said and a digest of the request), the day's digest has its own
line for it, and a later send of the held inputs is turned away as held, not as refused. A refusal a provider stated of the same
request outranks the hold.

**What a refused request is (2026-11-06).** It has one identity, of what was sent, and no alias of what a wrapper was asked. For a
host caller that is the digest of the exact arguments and input its program is started with, a file G6 wrote for it standing as
its bytes and a path in the call's own folder as its place there (`usage.request_identity`), taken at the last step the bound file
gives a subclass before the program starts: `_command`, whose output `host_models._run` receives unchanged, or `_launch` for a
session of a caller's own (`ToolSession`). Only the building of the program's environment lies between that step and the start.
For any other caller it is the digest of the system text and request its own frame names, joined as before so refusals kept
before still count, and with no two pairs sharing one. Every place a request can leave passes one check (`_Requests.admit`),
tick or no tick: as a call begins, as each request is noted, as a `Retry-After` resend is made, and where a host program is about
to start. There a request already refused is stopped, and one identical to a request still out waits for what becomes of that one,
so a request let in before a refusal of the same bytes was kept cannot leave after it. A stop, records of refusals that cannot be
read, or a refusal this process could not keep refuses every later request of the process, between ticks too. What a call's tick
latched decides when the call comes back, whichever way, so a wrapper that catches a limit or a refusal cannot make it an answer.

**One record per send (2026-11-06, second review).** What is known of a send lived in shared places that nested and concurrent
calls overwrote: a flag and a variable on the call, a table that evicted entries still pending, an in-flight owner a nested call
replaced, an identity taken before the wait. Now `admit` makes one immutable record for each send (`usage.Send`) once it may
leave, after any wait and with the stop read again after it, from the bytes as they are then: its identity, whether its caller
is watched, its call and thread. A send takes its one place there, so a reservation of one sends one. The record travels with the
send to where its outcome is read, by the call's own folder for a host program, and is released only by that outcome; nothing
pending is evicted, and each send is its own entry among those out. A refusal is kept under the refused send's own record, and
how a failure is read comes from the record of the send that raised it, found from the failure's own frames, since a model call
that is not a host program has no boundary at its end. Every limit a tick met is in the log before its charges are settled,
whatever ended it. Any failure to write a refusal latches the stop, and the log keeps lone surrogates as they are. A test built
from the code lists what the request path writes per request, and fails on anything that is not a send's record.

**Admission is one step under its lock (2026-11-06, third review).** The stop and the host program's files were still read
before the admission lock, so a stop written, or a file changed, at that boundary was missed: the same check-then-act a second
time. Now `admit` takes the lock first, and reads there the bytes the send will carry, the stop, the latches and what was
refused, decides, and makes the send's record before it lets go; a test built from the code fails on any of those read outside
the locked block. A `Retry-After` resend is a send like any other, with its own identity checked against what was refused and
its own record. A send nested in a metered call is let out, and charged, once, by the innermost call on the same meter, and a
call's end charges only notes that had no boundary at all, a child's totals. Every refusal row in `refused.log` begins on a line
of its own, after a row a crash cut short too, and the reader keeps every whole row around a torn one.

**One admission and one record for the process (2026-11-06, the refusals follow-up).** Counting a send had findings in every
round: a resend charged by each call around it, a child's totals charged by each call around them, an identical send on another
thread of the same call let out beside the first, a nested host send that passed the reservation of the meter around it, a refused
note that hid a child's totals. Each repair covered one more case of the same fault: each metered call counted for itself and
decided for itself whether a send was its own. Now every model request in the process, nested or not, on any thread, a resend
included, passes one admission (`_Requests.admit`). Under one lock it reads every meter around the send, which are those of the
metered calls open where it is made, with their stops, latches and refused requests; it takes exactly one place on each, and makes
the send's record before the send goes. The innermost call lets each send in, for all of them. A meter keeps no count: what it has
taken, and what it has out, are views of the one record of sends (`_Record`), which only admission and the end of a call charge.
A call's children's totals, which pass no boundary, are settled once as the call ends, into that record and onto every meter
around the call, and each call around it counts them as settled; a note refused at its boundary was never counted, so it offsets
nothing. What a provider said of a send goes to every meter its record took a place on. A tick closes in one step under the lock,
which keeps what it latched. Its outcome, the limits it writes, in the order they were met, and the charges it settles are all
read from that, so a refusal or a limit is either in what the tick ends on or was met once it had ended. Each limit row names
the tick that met it: a tick's limits stand until the furthest of them ends, whatever order they came in, and a later tick's
replace them, as the latest always did. A death while a refusal is being kept stays a death, and is not read as the refusal it
was keeping. Whether a request was refused is read from the store at
every admission, so every handle of the loop, in this process or another, refuses it. A send waits for an identical one out on
another thread whose own outcome releases it: a watched one, or one of another call. An unwatched send of the same call has no
outcome before the call ends, so one beside it on another thread does not wait and both go out, each charged; and a send made
inside one that is out never waits for it. Tests built from the code list every place a request can leave, every writer of the
record, and the one step that closes a tick.

**What only the process knows, and what was sent (2026-11-08, review 1 of the refusals follow-up).** A refusal no file would
keep latched its stop on the one handle that met it, so another open handle of the same folder sent the refused request again.
And a request was known by the frames of an object only, so a function that sent one fixed request behind a wrapper whose input
varied gave the refused bytes a new identity each time. Now what only this process knows of a folder is the process's, kept for
the folder in the one record (`_Folder`), whichever handle met it and however the folder's path is spelled, links followed: each
request refused there, the cannot-record stop, and the count of cycles in a row its log would not take. Every handle of the
folder, one made after it too, reads it at every admission under the admission lock, and only the process's end lifts the stop.
Each handle reads the log's limits before it reserves a tick, so a limit another handle met holds it too. A restart has none of
this, only what the folder's files hold: the `STOP` the stop asked for, `refused.log`, or a cycle that holds the refusal. If all
of them refused their write, a restart can send the refused request again. A send is known by what the innermost frame names as a
system and a user, among the sender's own frames and the functions and methods it called, never a wrapper's further out; a
failure is matched to its send the same way. A request no such frame names as text is not sent, with
`G6_E_LOOP_UNNAMED_REQUEST`, which in a tick ends it and holds the loop for a person. Tests built from the code list every
attribute a handle sets with where it is kept, and every place G6 notes a request with how what it sends is known, and send
through each kind of caller the loop accepts.

No failure of the records can undo this. A refused request is kept as it happens, by its identity, in `refused.log`, a file no lock
stands in front of, flushed to the disk before any other thread can see the refusal, before any record of the tick is written and before the
failed request is asked anything else (whether it met a limit, which reads the clock), and this process holds it too, for good. A handle reads `refused.log` and the cycles' records as it is built, before its first
request. So a crash before its cycle is
written, or a log that will not take that cycle, cannot send it again. A tick that raises after the refusal, before its row,
writes the pause a person must lift on its way out, so however it fails and whoever handles that, the loop stays paused. A cycle
the log will not take cannot carry the pause, so it ends the
run at the first, as three lost cycles do: `STOP` and `stops.log` hold it, the reason says what was refused, and only a person's
start goes on. If `refused.log`, the log and `STOP` all refuse a write, this process still sends nothing more, and a restart then
has nothing to go on. A `refused.log` that cannot be read stops the tick with `G6_E_LOOP_RECORDS_UNREADABLE`, before anything is
reserved, since nothing could say what must not be sent.

What can read one is limited. `G6_E_MODEL_REFUSED` at the start of an error is G6's own code. For Claude Code, `host_models` keeps
only the last 300 characters of what was printed, and a refusal's `stop_reason` is written ahead of the long `usage` record, so
nothing in that error can show one. So the Claude Code caller really sending a request, whatever holds it, becomes a `WatchedClaude`
as it sends, a subclass outside `g6/core` as `ToolSession` is, which reads `stop_reason`, `api_error` and `permission_denials` from
the whole result as parsed JSON, where a model's words stay inside the string `result`. What it reads goes straight to the tick, so
a wrapper of a domain's own that catches failed requests cannot retry a refusal or hide it. This was read from the text of Claude
Code 2.1.316 and has not been seen at a real refusal, and it leans on two private names of a bound file, which tests hold. That
program can also retry a refusal itself on a fallback model, if one is configured: G6 passes none, and a person's own settings are
outside this. A Codex refusal is read the same way, by `WatchedCodex`, from Codex's own events (`cyber_policy`, `bio_policy`,
`misalignment_policy_violation`, `too_many_denials`, or "This content was flagged for possible ..."), and is kept and never sent
again as Claude Code's is. A refusal that comes back once its tick has closed, or with no tick open, is kept all the same, and
one `refused.log` will not take stops the process and asks for a `STOP`, since only this process would hold it. The repair that
lets both subclasses go is a code of their own from those callers and `--json` in `CodexCaller`, at an issuance window.

So that nothing retries by itself, `EmptyRetry` and `FallbackCaller` are refused around a declared model or the proposer, with
`G6_E_LOOP_RETRY_MODEL`, at enrolment and before each tick. `provider_from_env` wraps its callers in `EmptyRetry`, so a domain
builds its caller bare: `ClaudeCodeCaller()`, not that. A blank reply is then a failed case, unless a profile's own `retries` asks
for another try. A retry of a domain's own of any other failure is not seen, though each attempt is charged.

**One backend to a model.** A `FallbackCaller` that holds two backends is refused at enrolment, for a declared model or the
proposer, with `G6_E_LOOP_FALLBACK_MODEL`, seen through `EmptyRetry` and `TimingLLMCaller`. It answers from the next backend
when one fails, for any reason, so a scored run could say nothing of which model answered, a paired comparison would compare
backends, and a limit on one would pass unseen. A wrapper of a domain's own that holds a fallback under another name is not
seen.

These repairs changed files under `ENVELOPE_PATTERNS`, so a folder enrolled before them is refused with
`G6_E_LOOP_FROZEN_CHANGED` until a person enrols again.

**A log that will not take a cycle.** The streak that pauses the loop is read from the log, so a cycle the log refuses
leaves that streak nothing to read. These cycles are counted in memory instead, and one that is written starts the count
again. Until three in a row, status shows the count under `pause` with `G6_E_LOOP_CANNOT_RECORD`, the digest lists it
under its decisions and stops, and the run goes on, because a log held for a moment should not end it. At three the loop
writes the reason to `STOP` and `stops.log`, which are other files, and the run ends. Status then shows `running: false`,
the reason under `stop`, and a last heartbeat in state `stopped`, and the digest's last page says "The runner ended" and
why. A restart stays closed until a person has freed the log and agreed to `g6_improve_start`. If `STOP` cannot be written
either, the run still ends, though nothing then closes a restart, which could spend up to three more ticks. That needs
the log and `STOP` unwritable and `operator.db`, in the same folder, still writable, since a tick reserves there first.
The lost cycles' use is on the meter and their results are on record nowhere. An adoption one of them made stands in
`adoptions.jsonl`, where the chain ahead of the log is read as the crash between two writes.

**What the two test domains showed.** Each ran with `FakeLLM` only and a planted weakness, one a coding-shaped domain,
the other V6b's GSM8K task, check and exact-answer oracle over generated word problems (the dataset is not in the
repository). The same code adopted a real gain on both, built on it across ticks, and scored better on sealed cases the
test ran itself. This shows the machinery works end to end. It does not show that G6 improves anything real: the
weakness was planted and the models are stubs.

**The loop gates on success and model calls, and only reports wall clock (the owner's ruling of 2026-11-01).** This replaces
an item that stood open here. Those tests run on a clock that steps by a fixed amount. Two runs of the real detached runner
on the same fake domain used the real clock, and the gate refused the planted gain both times on `wall_clock_s`, whose
default objective holds the upper bound on the increase to 5% of the base: 0.6 ms against 0.06 ms with an instant stub, and
1.6 ms against 1.1 ms with a stub taking 20 ms a call. So a gain that cost no more was refused on timing noise, and a real
model's latency varies more than either. Each refusal spends one of the split's five evaluations, so a loop on a real model
could have spent its split on noise and then waited for a person, with generation 0.

The workflow the loop builds now passes `objectives` holding success and model calls only, and model calls keep their 5%
limit. `default_objectives`, the gate and the evaluator did not change, so a workflow a person runs still gates on wall
clock. Wall clock is still measured and still reaches the gate's `measurements`. Each generation carries it under
`measured.not_gated`, and its line in the digest says "reported and not gated", so a candidate that really is slower can
be adopted, and a person sees that it was.

The workflow's digest includes its objectives, so it changes, and any folder enrolled before this must enrol again. Until
it does, a tick there is refused with `G6_E_LOOP_FROZEN_CHANGED`, since the envelope changed too, and the gate would block
it in any case with `G6_E_META_STANDARD_CHANGED`.

Afterwards a real detached runner on the same domain and stubs reached generation 1 on the real clock, in 2 s and 14 s, on
82 held-out cases, 0.29 before and 0.93 after. In both of those runs wall clock happened to fall, so they do not show the
ruling made the difference. The test on a real clock does: it was refused on `wall_clock_s` before the change and adopts
after it. All of this is a worked example with a planted weakness and stub models, and it says nothing of a real model.

**One runner ended with no explanation (2026-11-01, open).** Of seven real detached runs that day, six ended only when
asked, one of them after eight minutes with no parent. One ended within 20 seconds of starting: its log was empty, it wrote
no last heartbeat or digest line, and SQLite's working files were left behind, which a clean stop never leaves. So something
outside ended the process, and what did was not found. It did not come back in the four runs after it, on the same code
with the same work going on beside it. Status read it correctly as not running. Nothing restarts such a runner until the
next scheduled start, which is what the 15-minute task above is for.

The cause is still not known. What is now on record, so a next one can be read rather than guessed at:
- a `starting` heartbeat with the process's number, before the lock, so a run that dies at once leaves word that it ran;
- `in_job` on every heartbeat, from Windows's `IsProcessInJob`;
- the exit code, in `G6_E_LOOP_START_FAILED`'s message and under `return_code`, for a runner that ends while the start watches.
  A death after the start replied is past that watch, and what is left is a last heartbeat that is not `stopped`.

What was measured on this machine on 2026-11-01. On Windows a venv's `python.exe` is a launcher: `Popen`'s process is not the
interpreter, which runs as its child inside a job (`in_job` true), and ending the launcher alone ended the interpreter within a
second, with exit code 1 and nothing written. The same child started from the base interpreter had one process and no job. So
a runner started from a venv stands on a second process, and an end like that would leave what was seen that day. That is a
mechanism that fits, not a finding of what happened: no run has yet been seen ending that way. The start's watch reads a
heartbeat newer than the spawn and past opening, because neither the lock, which a runner holds while it refuses, nor the
process's number could say. That watch reads the heartbeat ten times a second, and a reader holding that file refuses the
writer's swap: 25 of 600 beats on this machine, against none of 300 with no reader. A beat lost that way left a runner reading
as still starting for a whole nap, so a beat now tries its swap five times over a tenth of a second. On the fake domain, before
that repair one of six real starts through `g6.mcp.improve` lost its first beat so, and after it eight of eight were past opening
in 0.6 to 1.2 seconds. A stop took about a second during a tick and about 59 seconds at rest, and none of the fourteen died.
Fourteen is too few to say the death is gone, and these ran from a shell, not a client's server.

**What remains:**
- **The rest of P2:** the cleaner (a precondition of pushing anything), the records branch and public digest (these need
  O3, the App), model routing (6.7), a `STOP` on `main` and the `loop:stop` label, the yield measurement of 6.3,
  subordinating work beyond failure traces, and merging `BudgetBook` into the operator's ledger.
- **O1:** real token usage needs the relay of section 3. Until then the meter's unit is model calls, and only Codex's
  account figure reaches it.
- **P3 onward:** the mission, code changes, draft pull requests and evidence packages, the evaluator in a read-only
  checkout outside the loop's process, fresh held-out splits drafted by the loop, issues (P4), the north-star study
  (P5), and method changes through `method_rca`, which need `compare_methods` and a person.

**What the sealed guard does not do.** It is a check inside one process, not a boundary. It refuses a Python-level open
or memory map of a sealed file, by path or by descriptor, and it looks for a sealed file held open as a tick starts and
ends. A read through a handle opened before the tick cannot be stopped: a read emits no audit event, so the guard can
only notice the handle, and only if it is still open at one of those two moments. It knows nothing of what was read
before the tick, of a mapping or buffer that outlives its file, or of a child process or native code. So a breach means
the split may have been read, and no breach does not mean it was not. Sealing that holds needs an operating-system
boundary, which is P3's mission container, and P5's reports state the limit under O2.

**Rulings made while building, each with its cost if wrong:**

| Ruling | Cost if wrong |
|---|---|
| **For the owner: one person step stays.** A person enrols a domain, and again for each fresh held-out split. Someone must say what "better" means, and it cannot be the thing judged | The loop stops after five gated candidates per split until a person acts. This is the distance left from "no intervention" |
| **For the owner: `person_free_adoption_outside_shadow`, off.** With it on, this phase still moves nothing: no path out exists | None today. Wiring it in P3 is a separate decision, and the store's rules would need changing, since they refuse any `auto:` name |
| The tick lives in `g6.metaharness`, not `g6.self_maintenance`, because the layer contract forbids that import | Section 3's `python -P -m g6.self_maintenance` cannot hold; the entry is `python -P -m g6.metaharness.loop`. Cheap |
| Adoption is a subclass with its own ledger, not `activate` with a made-up name and not a change to the store | `ShadowHarness` overrides a private method, so a refactor of `MetaHarness._head` can break it. Tests would catch that |
| One workspace for all adoptions, so held-out uses carry over | None found. A fresh workspace per adoption would have let a split be spent for ever |
| "Sealed" means section 6.3's pool. The gate scores a seeded cut of the development pool, as 6.5 says | If the owner meant the gate to score the sealed split, one line changes, and the study's pool would be spent |
| "Independent" means four checkable facts: answers not by the caller or a model, not a model's opinion, a source file a digest can pin, and a non-answer rejected | `answers_by` is a declaration. A dishonest adapter can claim it. The adapter is host code, not something a candidate can change |
| Shares of the pool: 25% tuning, 15% selection, 35% held-out, 25% monitor, by a seeded hash per case or group, so a case keeps its side as the pool changes and a fresh split can stand beside what proposers saw. A hash that would leave a side under its minimum (30 held-out, 5 monitor, one each for the others) gives way to counts along the same seeded order, so a pool of 86 gives held-out at least 30 whatever the seed, and groups then move only as far as the minimums need, so a grouped pool meets them whenever intact groups can | A pool under 85 cases can be refused, by seed, and so can one whose groups cannot meet the minimums at all. A pool cut by counts can move a case at the edge of a side when it changes, so a person refreshing a small split may be refused with `G6_E_LOOP_SPLIT_REUSED` and need more new cases. Other shares may suit real work better; changing them is a re-cut a person must accept |
| The monitor uses its own split, the judge's verdict on both versions, the pairs that disagree, and each case once per adoption | It needs at least 18 disagreeing pairs. It catches a collapse, not a modest regression: a change 10% worse stays. The gate remains the main defence |
| **The owner, 2026-11-01:** a failed cycle is a fault: blocked, error or sealed breach. A candidate that lost is a result, so no winner, no constraint, a refusal and a rolled-back adoption never pause. Unreadable records and a changed frozen item still stop every tick until a person acts. Waits are not cycles | Adopting and rolling back in turn no longer pauses. Each round uses a held-out evaluation, so five rounds stop it until a person gives a fresh split, and the budget it spends in between is charged like any other. Results with nothing to try rest instead: see the runner below |
| A sealed breach pauses at once, and a tick that finds a sealed file already held open does not start | One resume per sign. See the guard's limit below |
| Re-enrolment refuses held-out cases that proposers saw or a spent split held, known by id and by a hash of the input. This is stricter than the meta-harness, whose limit counts per digest | A small domain can run out of cases, and a person must find new ones. A reworded input is a new hash, and a case of a bug already seen (`meta["group"]`) is not checked |
| The envelope digest covers the loop's files and what they use to spend, seal, pin and propose, and a test holds their imports to it | It is a tripwire, not custody: the code that compares it is covered by it, so an edit there can skip the comparison. The read-only checkout of 6.5 is what would hold |
| `OperatorStore.settle` gained `metered`: the host's count sets the charge, up or down. A run's report still cannot lower one | If a domain's executor bypasses the declared model callers, its calls go uncounted and the ledger undercharges |
| A tick is metered at `g6.core.model_calls`, plus `Retry-After` resends, and stops at what it reserved: each noted request takes its place in the count at the boundary where it goes out, under one lock, so a built-in retry, a fallback or requests running at once cannot pass the reservation. A caller that answers with no request noted is refused and charged the tick's worst case | A caller that notes one request and retries unnoted looks like one request, and so do the Anthropic SDK's own retries. Only a provider's usage record shows those, which is O1's relay. A `Retry-After` resend is admitted the same way, at the only boundary it has: `g6/core/llm.py` adds it to the open records of `waits` just before it waits and sends again, and the tick's record refuses there, by the reservation and by the week, whichever caller sent it. That record stands in `g6.core.llm._WAITS`, a private name of a bound file, and a test holds it to that. A busy endpoint can end a tick at its reservation, which is a blocked cycle on the record and in no failure streak: it stopped where the loop told it to, and it compared nothing, so it neither counts nor ends a streak, and the faults on each side of it are still in a row. What bounds such ticks is the pacing rule. The gate on a request is an observer of `counting`, whose docstring says observers do not raise: that file is bound, so the sentence stays until an issuance window |
| An account reading carries when it was read. What the loop charges after that goes on top of it until the next reading, and a charge before it may be in it. A reading with no date is read as the larger of the two figures | A figure that lags the provider's own count leaves a gap nothing here can see. A read that fails keeps a limit still ahead, in memory only, so a restart whose first read fails has forgotten it. It forgets a figure short of a limit, and the meter is on its own count until the next read, 15 minutes at most |
| The ceiling is checked inside the reserving transaction, on the ledger as it stands, and pending reservations go on top of the account's figure | The account's figure is read just before that transaction, so use elsewhere in between is unseen until the next tick |
| A tick charges one provider: the first with room | Real work may split across providers. Harmless until O1 |
| `BudgetBook` stays for now, on the person-approved `run_one_cycle` only. The loop does not use it | Two ledgers exist until it merges, against section 6.6 |
| `g6_improve_start` asks for `metaharness.improve`, so a host that lists `metaharness` allows it too, and the dialog still asks | A host that allowed `metaharness` for workflows has allowed this at the host's level. The dialog is what stands between. Cheap to split into a component of its own |
| A person enrols and lifts a pause at a terminal, by typing the domain's name back, as in `g6.workforce.decide`. It is a convenience check, not proof, and there is no MCP tool for either | An agent with a console can type. The same is true of `decide`, and of a Python call |
| Start lifts a `STOP` file by recording its digest, length and modification time, once the person has agreed and been shown its reason, and never moves or deletes it. A reboot lifts nothing | A stop someone else set is lifted by whoever agrees in the dialog, and the log keeps who. A lifted file stays on disk, so each look at it reads the records, and records that do not read leave it standing. Restoring a backup that changes the file's time stands as a stop. A rewrite of the same bytes on a file system too coarse to give it a new time, and a writer that restores the old time on purpose, are not seen |
| A tick compares the log with the chain of adoptions, and refuses with the records code when the log saw a generation the chain does not hold. Only adoptions and rollbacks count, and a chain ahead of the log is the crash between the two writes | Both files cut together leave nothing to compare. The check keeps a number from being used twice; it does not bring a lost generation back, and a person must inspect the folder |
| A stop through the tool is a line in `stops.log`, with no lock, flushed to disk before its file is written, and a start records that journal by count and digest. The `STOP` file holds a stop by itself, so the journal has two uses: a restart after a person has removed that file with no note of the stop in the records, and a stop that died between its two writes, which leaves only its line. Every reader takes that as the stop it is through `stop_in_force`: a tick in flight and a waiting runner at the next model call or the next pass, status, and the next dialog. A journal or `STOP` file that cannot be read is a stop in force (`G6_E_LOOP_STOP_UNREADABLE`), and a start refuses it. A stop is also noted in the records, best effort | A line that cannot be written leaves only the `STOP` file, so a restart misses that stop if the file is then removed by hand. A stop that cannot change the `STOP` file reports `G6_E_LOOP_STOP_FAILED`, since the file it could not change may be lifted. A `STOP` file written by hand is not counted: it stops while it is there, and is noted when a runner or a restart sees it, so one written and removed with nothing running leaves no trace. Deleting `stops.log` closes the start rather than opening it |
| Refusals do not rest the runner, since a refusal tried something. No winner, no constraint and idle do | A run of refusals is bounded only by the split's five evaluations |
| Status shows use from the runner's last heartbeat, with its time, because opening the ledger would write to it | With no runner alive the figures are as old as that heartbeat, and with none ever there are none |
| Each provider's day is its own, and the runner wakes for the next rise of any ceiling, or the end of a reported limit. It wakes even if the rise is too small for a tick | One refused tick a day, which spends nothing |
| A rollback's before and after are the gate's held-out figures read the other way, with what the monitor saw beside them | They are not fresh: nothing measures a held-out split at a rollback |
| Earlier findings shown to a proposer carry no outcome, since which candidate the gate took is the held-out split's | A proposer cannot learn directly from what lost. It sees only what earlier proposers said |

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
