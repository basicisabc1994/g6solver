# The full lifecycle with G6

Each stage of a coding agent's work is paired with its superpowers skill, its G6 step, and the principle from G6's
essays (`/essays/`) that it puts into practice. The superpowers skills say how to work; G6 checks the work at each
point, independently of the agent doing it.

| Stage | Superpowers skill | G6 step | Principle (essay) |
|---|---|---|---|
| 1. Understand the request | `brainstorming` | Write the intent contract (`g6.intent_engine`: goal, success and failure criteria, non-goals, checkpoints). Its elicitor lists what is still missing; ask the person for it | Know your incompetence boundary, and ask (*Metacognition*) |
| 2. Premortem | `brainstorming`, `writing-plans` | `g6.reliability_pipeline.premortem.premortem_failure_mode_questions()` gives the premortem's questions, one per failure mode; name a guard for each in the plan. `g6.reliability_pipeline.relevant(objective)` adds the curated failure-mode skills that match the task, each with the decision to ask the person. High stakes (payments, health, safety, legal) mean a person reviews the plan | Defence in depth; name the hazards before building (*Alignment Is Engineering*) |
| 3. Plan | `writing-plans` | If the plan calls G6 components, `g6.navigator.check_plan(steps)` checks every step against the components' contracts before anything runs | Move deliberately and verify things (*Move Fast and Break Things*) |
| 4. Isolate | `using-git-worktrees` | For a coding mission with a standard of done a person confirms: `g6.workforce` (`create_coding_mission`, `confirm`). The standard is frozen before the work starts | Bounded operation (*Alignment Is Engineering*) |
| 5. Test first | `test-driven-development` | The failing test is the frozen standard's first check. Tests the agent writes are its own evidence; the mission's frozen tests are the independent ones | The generator proposes; the verifier disposes (*Hallucination Is a Design Problem*) |
| 6. Build and debug | `executing-plans`, `subagent-driven-development`, `systematic-debugging` | Before each risky step: `coding_agent_gate.preflight`. Exact sub-problems go to `formal`, `algebra`, `optimisation`, `datalog`, `causal`, `model_checking` or `lean`. Outward actions go through `action_gating.evaluate` | Hand exact work to exact tools (*Two Types of Thinking*; *Formal Verification*) |
| 7. Check claims | `verification-before-completion` | `coding_agent_gate.verify_answer` on the drafted report. `grounding.check_claims` for any claim about the outside world | Provenance for every claim, and calibrated confidence (*Epistemology of AI*) |
| 8. Review | `requesting-code-review`, `receiving-code-review` | A reviewer who is not the author; their review is evidence of kind `review` with a named reviewer. The certificate for a mission (`g6.workforce.certificate`) states what each check supports | Augmentation, not replacement: a person decides what is irreversible (*The Human-AI Team*) |
| 9. Finish | `finishing-a-development-branch` | Report the label G6 gave (`qualified-draft` unless an independent check passed), every gap named, nothing upgraded | Honest measurement (*Why Benchmarks Lie*) |
| 10. Learn | `writing-skills` (for lessons worth a skill) | When a task repeats:<br>• declare it as a `g6.metaharness` workflow (dev and held-out cases), and let the meta-gate judge changes; a person activates them;<br>• hints learned from failures go through `g6.learning` (paired runs, held-out evidence, a person accepts);<br>• repeated model calls that a small tested function can replace go through `g6.distillation` | Additive, inspectable learning a person approves (*Learning Without Forgetting*; *Self-Improvement Without Self-Destruction*; *Economics of Intelligence*) |

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
