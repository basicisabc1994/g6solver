# The full lifecycle with G6

Each stage of a coding agent's work is paired with its superpowers skill, its G6 step, and the principle from G6's
essays (`/essays/`) that it puts into practice. The superpowers skills say how to work; G6 checks the work at each
point, independently of the agent doing it.

| Stage | Superpowers skill | G6 step | Principle (essay) |
|---|---|---|---|
| 1. Understand the request | `brainstorming` | Write the intent contract (`g6.intent_engine`: goal, success and failure criteria, non-goals, checkpoints). Its elicitor lists what is still missing; ask the person for it | Know your incompetence boundary, and ask (*Metacognition*) |
| 2. Premortem | `brainstorming`, `writing-plans` | `g6.reliability_pipeline.premortem.premortem_failure_mode_questions()` gives the premortem's questions, one per failure mode; name a guard for each in the plan. `g6.reliability_pipeline.relevant(objective)` adds the curated failure-mode skills that match the task, each with the decision to ask the person. High stakes (payments, health, safety, legal) mean a person reviews the plan | Defence in depth; name the hazards before building (*Alignment Is Engineering*) |
| 3. Plan | `writing-plans` | If the plan calls G6 components, `g6.navigator.check_plan(steps)` checks every step against the components' contracts before anything runs | Move deliberately and verify things (*Move Fast and Break Things*) |
| 4. Isolate | `using-git-worktrees` | For a coding mission with a standard of done a person confirms: `g6_mission_create` and `g6_mission_confirm` over MCP (below), or `g6.workforce` (`create_coding_mission`, `confirm`) from Python. The standard is frozen before the work starts | Bounded operation (*Alignment Is Engineering*) |
| 5. Test first | `test-driven-development` | The failing test is the frozen standard's first check. Tests the agent writes are its own evidence; the mission's frozen tests are the independent ones | The generator proposes; the verifier disposes (*Hallucination Is a Design Problem*) |
| 6. Build and debug | `executing-plans`, `subagent-driven-development`, `systematic-debugging` | Before each risky step: `coding_agent_gate.preflight`. Exact sub-problems go to `formal`, `algebra`, `optimisation`, `datalog`, `causal`, `model_checking` or `lean`. Outward actions go through `action_gating.evaluate` | Hand exact work to exact tools (*Two Types of Thinking*; *Formal Verification*) |
| 7. Check claims | `verification-before-completion` | `coding_agent_gate.verify_answer` on the drafted report. `grounding.check_claims` for any claim about the outside world | Provenance for every claim, and calibrated confidence (*Epistemology of AI*) |
| 8. Review | `requesting-code-review`, `receiving-code-review` | A reviewer who is not the author; their review is evidence of kind `review` with a named reviewer. The certificate for a mission (`g6.workforce.certificate`) states what each check supports | Augmentation, not replacement: a person decides what is irreversible (*The Human-AI Team*) |
| 9. Finish | `finishing-a-development-branch` | Report the label G6 gave (`qualified-draft` unless an independent check passed), every gap named, nothing upgraded | Honest measurement (*Why Benchmarks Lie*) |
| 10. Learn | `writing-skills` (for lessons worth a skill) | When a task repeats:<br>• declare it as a `g6.metaharness` workflow (dev and held-out cases), and let the meta-gate judge changes; a person activates them;<br>• hints learned from failures go through `g6.learning` (paired runs, held-out evidence, a person accepts);<br>• repeated model calls that a small tested function can replace go through `g6.distillation` | Additive, inspectable learning a person approves (*Learning Without Forgetting*; *Self-Improvement Without Self-Destruction*; *Economics of Intelligence*) |

## A coding mission, over MCP

Use one when what counts as done must be fixed before the work and a person must confirm it. `g6_premortem` proposes the
standard, the ways the task can fail and candidate questions, with no model call and nothing run; put one of them to the
person, through `g6_confirm_behaviour` (one question per task by default; the person may allow more). `g6_mission_create`
freezes the standard from the commit a clean repository stands at; an objective about retries or duplicates first
asks a question, which you answer with `g6_mission_clarify`. `g6_mission_confirm` asks the person in the client's own
dialog. Only the person can confirm: no argument does, you never answer for them, and there is no terminal fallback.
After a decline, a cancel or a client with no dialog, the mission stays unconfirmed and takes no patch: ask the person,
and offer no other way. The recorded name is not authenticated.

Then change only the permitted file and call `g6_mission_submit` with the folder. It reviews your working tree with the
frozen tests as its stated checks. A reproduced defect or a sign the tests cannot be believed holds the patch back: it
uses an attempt and the mission cannot close on it. Otherwise the mission judges the same text on its frozen copy.
Insufficient evidence blocks nothing, and the mission says so: tell the person. A change to a test is refused. Show
`assessment["report"]` as it is, and follow the reply's mission: three attempts, a repeated or non-improving patch
moves to a focused brief, then to the person. You repair; G6 never writes
code. Merging stays the person's separate decision. `g6_mission_status` and `g6_mission_certificate` read the mission;
the tools that change it return a job, so poll `metaharness_job`. An `R3` mission's patch approval is not over MCP yet:
its status names the terminal command for the person. The tools that run code need host permission workforce.mission,
which the person grants; never change permissions yourself.

## The review-until-clean loop, over MCP

Claude Code's plugin runs this loop from its Stop hook. With no such hook, and with `G6_LOOP_CRITIC=codex` in the person's key
file, call `g6_review_until_clean` when you finish, with the folder, the task and one session name for the task, then poll
`metaharness_job`. Repeat the same task on each call: another is refused. It needs the host permission
`patch_review.review`. Another model family reads your patch without your reasoning, and must state a rule, reasons and a
test. `continue` gives that finding when its test fails on your patch: check it against the task, then repair the cause, or
leave the code as it was, and call again, with `reason` in one line if you leave it: only a reason rejects a finding, and a
call with neither gets the same finding back. `wait` means another step is running, so wait for its result, or that your
reason was a question, which rejects nothing, so ask the person first. A fresh review reads each repair, at most 3 repairs. `stop` gives the report: show it as it is, and `ending` says how the run ended: clean,
fixed, contested, open or untested. A review that
reproduces nothing is not evidence the work is right, only that this reviewer found no failure it could show, and this loop
has been measured on seven training bugs only.

## Rules that hold at every stage

1. **The producer never certifies its own output.** That holds for the agent, a model and a G6 component alike. A
   `verified` label comes only from an independent check that could have failed.
2. **The size of the blast radius sets the gate.** Reversible, local work proceeds with checks. Irreversible,
   outward or high-stakes work waits for a person.
3. **Memory is not the context window.** Keep decisions, standards and evidence in files and in G6's stores, with
   their sources. Do not rely on a growing prompt.
4. **Measure on cases the tuner never saw.** Dev cases are for diagnosis; held-out cases are for the decision. A gain
   on the cases you looked at is not a gain.
5. **When G6 has no component for a need, say so.** Do it with ordinary tools and keep the same discipline. Note the
   gap; it may be an extension worth building.
