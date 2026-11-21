# Sources: where coding agents fail

Draft, 2026-11-17. Each row was read at the URL given (abstract page or the post itself) unless the row says
otherwise. Findings are paraphrased; nothing is quoted at length. "Confidence" is how sure the drafter is that the
source says what the row says, not how sure anyone is that the finding generalises.

Terms: arXiv listings carry arXiv's non-exclusive distribution licence or CC BY 4.0; METR's pages say "all rights
reserved" and offer a citation format; the Python documentation is under the PSF licence. Citing and paraphrasing is
within all of them. No source was used as training data or copied into a fixture.

## Table

| Id | Title | Authors / org | Date | URL | The one finding used | Confidence |
|---|---|---|---|---|---|---|
| S1 | Many SWE-bench-Passing PRs Would Not Be Merged into Main | Whitfill, Wu, Becker, Rush (METR research note) | 2026-03-10 | https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/ | 4 maintainers of 3 SWE-bench Verified repositories reviewed 296 agent PRs that had passed the automated grader; normalised to the merge rate of the original human patches, maintainer acceptance ran about 24 points below the grader. Rejection reasons were grouped as core functionality failure, breaking other code, and code quality. | High (page read) |
| S2 | Research Update: Algorithmic vs. Holistic Evaluation | Rein (METR) | 2025-08-12/13 | https://metr.org/blog/2025-08-12-research-update-towards-reconciling-slowdown-with-time-horizons | Of 15 reviewed Claude 3.7 Sonnet agent PRs, the 4 that passed the human-written tests were all judged not mergeable as-is; all 4 had test-coverage problems, and most had documentation or lint problems. Very small sample, flagged by the author as rough. | High (page read); the finding itself is small-n |
| S3 | Are "Solved Issues" in SWE-bench Really Solved Correctly? An Empirical Study | Wang, Pradel, Liu | 2025-03-19 (rev. 2025-09-09) | https://arxiv.org/abs/2503.15223 | With differential testing (PatchDiff), 29.6% of plausible (test-passing) patches behave differently from the reference fix; the main causes are similar-but-divergent implementations and patches that change more behaviour than the reference; 7.8% of patches pass the benchmark's check while failing the developer test suite. | High (abstract read) |
| S4 | SWE-Bench+: Enhanced Coding Benchmark for LLMs | Aleithan, Xue, Mohajer, Nnorom, Uddin, Wang | 2024-10-09 | https://arxiv.org/abs/2410.06992 | 31.08% of passed patches were suspicious because the tests were too weak to judge correctness; 32.67% of successful patches had the solution in the issue text (solution leakage). | High (abstract read) |
| S5 | ImpossibleBench: Measuring LLMs' Propensity of Exploiting Test Cases | Zhong, Raghunathan, Carlini | 2025-10-23 | https://arxiv.org/abs/2510.20270 | Tasks whose tests are mutated to conflict with the specification: any pass means a specification-violating shortcut. Agents take such shortcuts, from deleting or editing failing tests to operator overloading. | High on the method and exploit kinds (abstract read); the rates (stronger models cheat more) come from search summaries only: Medium |
| S6 | Recent Frontier Models Are Reward Hacking | Von Arx, Chan, Barnes (METR) | 2025-06-05 | https://metr.org/blog/2025-06-05-recent-reward-hacking | Agents raised scores by altering the scorer or its inputs (stubbing the evaluator, patching the timer) instead of solving the task, and when asked afterwards said this was not what the user wanted; prompting against it reduced it only a little. | High (page read) |
| S7 | Ambig-SWE: Interactive Agents to Overcome Underspecificity in Software Engineering | Vijayvargiya, Zhou, Yerukola, Sap, Neubig | 2025-02-18 (rev. 2026-02, ICLR 2026) | https://arxiv.org/abs/2502.13069 | Models struggle to tell well-specified from underspecified instructions and tend to assume rather than ask; when they do interact on underspecified inputs, results improve by up to 74%. | High (abstract read) |
| S8 | SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering | Yang, Jimenez, Wettig, Lieret, Yao, Narasimhan, Press | 2024-05-06 (rev. 2024-11-11) | https://arxiv.org/abs/2405.15793 | About 52% of unresolved instances were incorrect or overly specific implementations and about 23% were cascading failed edits; after one failed edit the chance of a later successful edit falls from about 90% to about 57%, and keeps falling as failures accumulate. | High (full text read, first half) |
| S9 | An Empirical Study on Failures in Automated Issue Solving | Liu, Liu, Li, Tan, Zhu, Lian, Zhang | 2025-09-17 | https://arxiv.org/abs/2509.13941 | From 150 failed SWE-bench Verified instances: agentic tools fail mostly through flawed reasoning and "cognitive deadlocks" (getting stuck), unlike pipeline tools that fail early at localisation. | Medium (abstract read; the loop detail comes from a search summary) |
| S10 | Understanding Code Agent Behaviour: An Empirical Study of Success and Failure Trajectories | Majgaonkar, Fei, Li, Sarro, Ye | 2025-10-31 | https://arxiv.org/abs/2511.00197 | Failed trajectories are longer and more variable than successful ones, and most failed runs (72-81%) still found the right files: the failure is in the fix, not in finding where it goes. | High (abstract read) |
| S11 | LiveMCP-101: Stress Testing and Diagnosing MCP-enabled Agents on Challenging Queries | Yin, Shen, Xu, et al. | 2025-08-21 (rev. 2026-05-22) | https://arxiv.org/abs/2508.15760 | Frontier models succeed on under 60% of multi-step MCP tasks; the failure modes span tool planning, parameterisation (malformed or wrong arguments) and handling of tool output. | Medium (abstract read; the subtype shares come from a review summary only) |
| S12 | Python documentation, built-in `round()` | Python Software Foundation | current | https://docs.python.org/3/library/functions.html | The docs themselves explain that `round(2.675, 2)` gives 2.67 because the decimal is not exactly representable. Used only as evidence that a fixture's fix is public knowledge. | High (page read) |
| S13 | We Have a Package for You! A Comprehensive Analysis of Package Hallucinations by Code Generating LLMs | Spracklen et al. | USENIX Security 2025 | https://www.usenix.org/conference/usenixsecurity25/presentation/spracklen | Code models name packages that do not exist, at a measurable rate (higher for open models). Used only to explain why this failure is left out of the cohort. | High on existence (listing read); rates Medium |

### Not used

- OpenAI, "Introducing SWE-bench Verified" (2024) and "Why we no longer evaluate SWE-bench Verified" (2026): both
  answered HTTP 403 to a plain fetch. Per the rules, the drafter stopped on them; their findings appear only in
  secondary write-ups and are not relied on here.
- SWE-bench Pro (Scale AI, 2025) and the 2026 audits of it: read only through secondary summaries. Not used.
- Practitioner blog posts and issue-tracker reports of agents saying tests pass when none ran: anecdotal, not a
  study. They agree with S1, S2 and S6 but carry no weight here.

## Taxonomy: failure mode -> evidence -> moment

| Code | Failure mode | Evidence | Moment it maps to | Tasks (after the audit) |
|---|---|---|---|---|
| F1 | Plausible but wrong patch: passes the visible tests, misses an edge or overfits them (incl. special-casing the visible examples) | S3, S4, S8 (overly specific), S2 | `call_before_final`: the agent is about to say done | before_done.1, .3; after_refusal.1, .3 |
| F2 | Breaks other behaviour: a change in shared code that the visible tests do not cover | S1 (breaking other code), S3 (changes more behaviour than the reference) | `call_before_final` | before_done.1 (iterator input), R-before_done.4 (shared formatter) |
| F3 | Thin or missing tests for the change the agent made | S2, S4 | `call_before_final` | before_done.2; after_refusal.3 (a test must be added) |
| F4 | Specification and tests disagree, and the agent edits the tests or invents a rule to make them pass | S5, S6 | `stuck_call` (it cannot pass honestly; repeated red runs) | R-stuck.1 (fees), R-missing_tool.3 |
| F5 | Repeated failing fixes, cascading edits, a fix at the symptom rather than the cause | S8, S9, S10 | `stuck_call` | stuck.3, stuck.4, R-stuck.2 (stock), R-missing_tool.4 |
| F6 | Underspecified requirement: the agent assumes instead of asking | S7 | best read before the first edit; in this cohort only through `stuck_call` on a spec/test conflict (F4) | R-stuck.1; see notes.md on a `call_before_first_edit` kind |
| F7 | A tool call is refused or errs, and the agent retries it, drops it, or turns the refusal into a success | S11 | `call_after_refusal`, `missing_tool` | after_refusal.1-4, missing_tool.1-4 |
| F8 | Hallucinated API or package | S13 | none in this cohort | Left out: in a few-file fixture with tests the agent can run, the first test run exposes it, so no G6 moment adds anything a model-free check would not. |
| F9 | The fix is public knowledge, so the moment never comes (threat to validity, not a task) | S12; contamination concerns in S4 | weakens `stuck_call` | stuck.1, stuck.2 replaced for this reason |
