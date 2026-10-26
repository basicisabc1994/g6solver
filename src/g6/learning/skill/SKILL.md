---
name: g6-learning
description: Use when an agent or prompt keeps failing the same way on tasks you can score, and you want to learn short prompt hints from those failures, keep only the ones that win on held-out paired runs, and switch each one on only when a person accepts its recommendation.
---

# G6 learning

`g6.learning` learns prompt hints ("theories") from an agent's failures and credits each one only from paired runs of
the same task with and without it. `promote` recommends a hint only on held-out paired evidence; `build_addendum`
serves only theories made active through `TheoryStore.activate`, which decides again from the evidence the decision
carries and refuses an empty or `auto:` name.

## When to use it

- The same kind of failure keeps coming back (an empty answer, the wrong format, a partial answer, tool errors) on
  tasks with a known answer or a rubric.
- You are about to add a "lesson" to a prompt and want to know whether it helps, not only whether it sounds right.
- You want to measure a frozen set of hints against the raw agent on new tasks (`compare_arms`).
- You want to move learned hints to another store without them arriving switched on (`export_bundle`,
  `import_bundle`).

## When not to use it

- You want to tune a whole workflow (which model runs each step, the prompt, extra checks, pipeline steps), not only
  hints: use `g6.metaharness`, which keeps its hints in this package's `TheoryStore`.
- The failure is a regression in one function of one repository: `g6.workforce` (its opt-in `coding_learning`) keeps
  such probes as theories here.
- You have no expected answers or rubrics: nothing can be scored, and the run is refused
  (`G6_E_LEARNING_EXPECTED_MISSING`).
- One failure to fix now: fix it. This needs repeated, scorable cases.

## Python API

- `load_seed_heuristics`: loads 16 first-party hints into a store as unmeasured candidates, ids
  `seed:<scope>:<name>`, so each scope of one database has its own. Loading twice into a scope refuses; nothing
  learned is overwritten. Non-empty condition keys match any present metadata key.

- `run_learning`: runs a strategy's theories with and without each on every training instance its trigger matches,
  then the raw agent against the frozen harness on a disjoint held-out set; returns a `LearningReport`; `proposer`
  names who asked.
- `compare_arms`: the raw agent and the agent with the given theories on every instance of one dataset; returns an
  `ArmComparison`.
- `promote`: reads one candidate's `HeldOutEvidence` and returns a `PromotionDecision`: `promote` or `refuse`, with a
  `G6_E_LEARNING_*` code and reasons.
- `TheoryStore`: one scope's candidate, active and retired theories in SQLite at an explicit path (or `":memory:"`);
  `activate(decision, accepted_by=...)` is the only way a theory becomes active. It runs `promote` again on the
  evidence the `PromotionDecision` carries and this store's theory, under the stricter of the decision's policy and
  the store's `promotion_policy` (default `PromotionPolicy()`), so a decision cannot lower its own bar. It refuses a
  decision that does not follow (`G6_E_LEARNING_DECISION_UNFOUNDED`; one built by hand carries no evidence), an
  empty or `auto:` name (`G6_E_LEARNING_ACCEPTANCE_REQUIRED`) and its proposer (`G6_E_LEARNING_APPROVER_IS_PROPOSER`).
- `build_addendum`: the hint text for a task from the store's `active` theories only.
- `render_addendum`: hint text from the given theories whose `trigger` matches the task's metadata.
- `T3Strategy`: proposes a fixed template hint for each failure code seen and not yet covered in the store's scope.
- `Dataset`: named instances with a `split`; a held-out split (`holdout`, `validation`, `test`, `frozen`) is refused as
  training data.
- `TaskInstance`: one task: `input`, `expected_output`, `instance_id` and `metadata` (`score_mode`,
  `score_threshold`, `rubric`).
- `Theory`: one hint: `failure_code`, `instruction`, `trigger`, `status` and its paired counts.
- `LearningReport`: the cycles, the frozen `harness`, the held-out comparison `holdout`, each harness theory's
  `evidence`, `completion_state` and `to_envelope()`.
- `ArmComparison`: paired outcomes on the same instances: `wins`, `losses`, `ties`, `lift` and `p_one_sided`.
- `HeldOutEvidence`: one theory's paired held-out outcomes, bound to its id and text digest.
- `PromotionPolicy`: `min_pairs` (10), `min_discordant` (5) and `alpha` (0.05).
- `PromotionDecision`: the recommendation, its code, reasons, counts, the `evidence` it was decided on and
  `to_envelope()`.
- `sign_test`: the one-sided exact sign test.
- `score_output`: deterministic scoring (`normalized_exact`, `contains`, `token_f1`).
- `score_instance`: scores one output against its instance; rubric instances need an injected judge.
- `DefaultClassifier`: heuristic failure codes for a failed result, with no model call.
- `FAILURE_CODES`: the codes it uses (`NO_OUTPUT`, `WRONG_FORMAT`, `PARTIAL_MATCH`, `TIMEOUT`, `TOOL_ERROR`,
  `REASONING_DEPTH`, `DOMAIN_MISMATCH`).
- `export_bundle`: writes theories and artifacts to a folder with an optional `ReviewRecord` and a content digest
  over all three; refuses risky text.
- `import_bundle`: reads a bundle as candidates only; a theory file, an artifact file or a review that no longer
  matches the manifest's digest is refused, and so is a blank review. The digest is unkeyed: it catches an accidental
  or partial edit, not a rewrite that also recomputes it. A bundle an earlier version of G6 made (format
  `g6.learning.bundle/1`) is refused with `G6_E_LEARNING_BUNDLE_OLD_FORMAT`: export it again.
- `ReviewRecord`: who reviewed a bundle, when, and its scope; blank fields are refused.
- `DatasetBuilder`: datasets from JSONL, and seeded splits.
- `JsonlObserver`: appends each run event as one JSON line to a path you give.

## Alone and composed

Alone, from Python only (no block, so no `g6_invoke` op):
`from g6.learning import Dataset, TaskInstance, TheoryStore, T3Strategy, run_learning, promote, build_addendum`. The
agent is any callable from prompt to text or a `g6.core.llm` caller (in tests, `FakeLLM`); a judge for rubric
instances is injected the same way.

Composed:

- `g6.metaharness`: `adapt(workflow, theories=store)` gives the meta-harness a store; its `HintStrategy` runs
  `T3Strategy` over the dev failures, adds each new hint as a candidate theory, and proposes a profile that names it in
  `Profile.hints`. The meta-gate then judges that profile on its own held-out split. The theory stays a candidate
  here, yet goes into live prompts once a profile naming it is activated.
- `g6.workforce`: `coding_learning` keeps a learned probe as a `Theory` in a `TheoryStore` and validates it with
  `promote` on labels from outside the probe.
- `g6.llm_router`: `TaskRouter().caller_for(task)` returns a caller you can pass as the agent.

## Failure modes it catches

- `repeated_mistake` (repeating the same mistake): each failed result is classified (`DefaultClassifier`), each
  uncovered failure code gets a candidate hint, and the next cycle measures against the harness that kept the hints
  that won. Once a hint is activated, `build_addendum` gives it to later prompts whose task its trigger matches.
- `unhelpful_lesson` (a lesson that sounds right and does not help): a hint is credited only by pairs whose two runs
  disagree, so a hint added while the agent already passes earns nothing. `promote` refuses evidence that is not
  bound to the theory's id and text, that includes training instances, that has too few pairs, or whose wins do not
  beat losses by the exact sign test.
- `unranked_heuristics` (useful heuristics forgotten or never ranked): seed hints are candidates ranked only by
  paired credit and held-out promotion, like learned hints; selection earns no credit.

## Reading the result

- A finished run is `qualified-draft` with `G6_E_LEARNING_UNVERIFIED_MEASUREMENT`; a refusal or an early stop (budget,
  a failing agent) is `blocked-escalated` with its own code. Nothing here returns `verified`.
- `report.holdout` pairs the raw agent with the frozen harness on the held-out set; `report.evidence[theory_id]` is what
  `promote` reads. A `promote` recommendation is still not active: `TheoryStore.activate` makes it so, under the
  name it is given (never an `auto:` name). An agent hands the decision to the person and never calls it itself.

## Blind spots

- Condition keys test presence, not truth or applicability; empty keys apply universally. Store failures propagate.

- The hints are fixed first-party texts: a run learns which help, not new
  text. A failure the classifier labels wrongly gets the wrong hint.
- `activate` checks a decision against the evidence it carries, not that evidence against the world: held-out pairs
  built by hand that pass `promote`'s rules at the store's bar activate a theory. It refuses an empty or `auto:` name
  and a theory's proposer, but both are names its callers give: it cannot tell a person's from one an agent typed.
- The held-out tasks, their answers and any judge are the caller's, so the evidence is only as independent as the
  caller made it.
- Overlap between training and held-out sets is checked by instance id and exact input text; a reworded duplicate is
  not caught.
- Rubric scoring depends on an injected judge, which can be wrong; a judge reply that does not parse fails the
  instance.

## What it needs

Nothing outside G6: it runs on the standard library and SQLite, with the agent and any judge injected by the caller.

## Examples

A hint that the agent needs is learned on training tasks, wins on held-out pairs, is recommended, and reaches the
hint text only after a person accepts it.

```python
import re

from g6.learning import Dataset, T3Strategy, TaskInstance, TheoryStore, build_addendum, promote, run_learning


def tasks(prefix, n, split):
    return Dataset(prefix, [TaskInstance(f"[{prefix}#{i}] what is {i} + {i}?", str(2 * i), f"{prefix}{i}")
                            for i in range(n)], split=split)


def agent(prompt):   # answers in the right format only when the format hint is in the prompt
    a, b = re.search(r"what is (\d+) \+ (\d+)", prompt).groups()
    return str(int(a) + int(b)) if "[WRONG_FORMAT]" in prompt else "the answer is a number"


with TheoryStore(":memory:", scope="sums") as store:
    report = run_learning(T3Strategy(), tasks("t", 10, "train"), agent, holdout=tasks("h", 10, "holdout"),
                          store=store)
    assert report.completion_state == "qualified-draft"
    assert (report.holdout.wins, report.holdout.losses) == (10, 0)
    [theory] = report.harness
    decision = promote(store.get(theory.theory_id), report.evidence[theory.theory_id])
    assert (decision.recommendation, decision.completion_state) == ("promote", "qualified-draft")
    assert build_addendum(store).text == ""            # recommended is not active
    store.activate(decision, accepted_by="Ada")        # a person accepts it
    assert "[WRONG_FORMAT]" in build_addendum(store).text
```

## The four open problems

- **Learning signals**: every training paired outcome is appended to the store's history (`paired_outcomes`) with
  its run id; held-out pairs are returned in `report.evidence`, not stored. Observers (`JsonlObserver`,
  `CallbackObserver`) receive each cycle, credit and finish event. Not yet: nothing is written to G6's shared run
  ledger.
- **Evaluators the model cannot influence**: scoring is G6's code and reads only the instance's own answer; the agent
  never sees `expected_output`; `promote` reads the evidence content, not the agent's claims. Rubric instances rely
  on an injected judge, which may be a model.
- **Transfer by failure mode**: a theory is keyed by a failure code and a `trigger`, not by a task, and bundles carry
  theories to another store as candidates with no recorded evidence (`promote` reads whatever held-out evidence the
  caller passes). Not yet: no catch rate measured on task families a theory was not built on.
- **Enough held-out cases**: `promote` needs at least 10 distinct held-out pairs, 5 where the runs disagree, and a
  one-sided exact sign test at 0.05; a refusal says how many pairs it had and how many the policy needs. Not yet: no
  sequential test that says how many more cases would settle it.
