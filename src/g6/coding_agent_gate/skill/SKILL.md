---
name: g6-coding-agent-gate
description: Use when a coding agent is about to act on a risky plan (a deploy, auth or security, credentials, a data migration, payment, medical or legal logic, skipping or disabling a check) or is about to report coding work as done, and the plan or the drafted answer must be checked against the evidence the agent actually has.
---

# G6 coding-agent gate

Deterministic checks a coding agent runs at two points: `preflight` before it acts on a plan, and `verify_answer`
before it reports work as done. It counts only evidence with the structure its kind needs, returns a verdict with
coded findings, and calls no model.

## When to use it

- You are about to change deployment, auth or security, credentials, a data migration, payments, medical or legal
  logic, or run `rm -rf` or a force push: `preflight`.
- The quickest route is to skip a test, disable a check, use `--no-verify` or loosen an assertion: `preflight` names
  it and blocks.
- You are about to write "fixed", "all tests pass" or "verified": `verify_answer`, with the run behind it.
- The answer makes repository-specific claims ("the retry lives in `src/auth.py`"): list them in `claims` with the
  files you read. Claims need at least one counted `file` item; the gate does not match a claim to its file.

## When not to use it

- The action leaves the repository (sends, pays, posts, deletes an account): `action_gating`.
- A claim must rest on documents: `grounding`; a high-stakes conclusion graded against its evidence:
  `epistemic_integrity`.
- Whether a change weakened the tests, read from the files themselves: `safety_engine.detect_test_weakening` (Python).
- A standard of done a person confirms first, with tests G6 runs itself: a `workforce` coding mission.
- A trivial edit with nothing to claim: nothing.

## Ops

- `preflight`: check a task and a proposed action before acting; verdict `block`, `needs_review`, `needs_evidence` or `proceed_with_caution`.
- `verify_answer`: check a drafted answer before reporting done; verdict `block`, `needs_review`, `revise` or `pass`.

## Reading the result

Read `verdict`, `completion_state`, `warning_card.code` (`G6_E_CODING_AGENT_GATE_<VERDICT>`), `findings` (each
`code`, `severity`, `message`), `missing_evidence`, `evidence_rejected` (each with its reason) and `risk_tier`. With
no `task` the op is refused with `G6_E_CODING_AGENT_GATE_TASK_REQUIRED`.

- `block`: do not do it. A finding names the shortcut (a skipped test, a disabled check, a tautological or removed
  assertion in a diff) or the missing `task_contract`; say so and give the alternative that keeps every check active.
- `needs_review`: high-risk work needs evidence of kind `review` naming a `reviewer` other than the `author`.
  Supply `author` to both ops; protected-path reviews additionally require host-observed evidence.
- `needs_evidence`: collect what `missing_evidence` lists. A proposed action is a plan, never evidence.
- `proceed_with_caution`: act, with a targeted check of the risk the findings name.
- `revise`: fix the draft. A claim needs `{"kind": "file", "ref": "<path>"}` for the file you read; a readiness word
  needs a passing run, and on your own evidence alone the answer must also say it is unverified.
- `pass`: report it with its uncertainty. `block` and `needs_review` read `blocked-escalated`; every other verdict
  reads `qualified-draft`; nothing here is `verified`.

Evidence counts only with structure: `file` a path, `test` the command and its `exit_code`, `log` non-empty `output`,
`diff` unified-diff `output` with an `@@` hunk (every diff's text is scanned, counted or not), `review` a `ref`
and a `reviewer`. Placeholders (`none`, `TODO`, `not run`, "tests pass") are rejected. `verifier` is
`{"status": "pass|fail|not_run", "exit_code": 0}`, and a non-zero exit code is a failure whatever the status says.
A declared `risk_tier` can raise the computed tier, never lower it, and an unknown word reads `high`. Everything sent over MCP is `observed_by: "caller"`: the
gate checks structure; it does not open a file, run a test or check that a reviewer exists. Declare only what you
ran or read.

For protected changes, supply a host-owned `ProtectedPathsPolicy(protected=("rules/**",), exemptions=...)`
to the block constructor, or `protected_paths_policy=` to either Python gate, and pass `touched_paths`.
Policy and exemption objects forbid unknown fields. Supplying paths without a policy blocks
(`G6_E_PROTECTED_POLICY_MISSING`); a configured policy without paths, ambiguous paths or evaluator failures
blocks (`G6_E_PROTECTED_POLICY_FAILED`). Existing text-only calls remain available.

Paths are repository-relative and case-folded; globs and directory prefixes match with path boundaries. Unknown
paths remain `unclassified`. `PathExemption(pattern=..., expires_at=...)` requires a timezone; an expired exemption
no longer exempts. A protected match needs `Evidence(kind="review", ref=path, reviewer=..., output=...,
observed_by="g6")`, with a named author and a different reviewer. Missing review produces `needs_review` and
`G6_E_PROTECTED_REVIEW_REQUIRED`. Only trusted host code can provide that provenance; MCP accepts caller claims
only, so a reviewer string or approval flag cannot release the hold. A review must reference each exact matched path.

## Alone and composed

Alone, over MCP (the `g6-mcp` server: `pip install "g6[mcp] @ git+https://github.com/basicisabc1994/g6solver"`, then `claude mcp add g6 -- g6-mcp`):
`g6_invoke("coding_agent_gate", "verify_answer", params_json)`; `g6_describe("coding_agent_gate")` returns its ops
and this skill. From Python:
`from g6.coding_agent_gate import preflight, verify_answer, Evidence, VerifierResult`; `scan_diff(diff)` and
`shortcut_hits(text)` are its two detectors on their own.

Composed:

- `g6.coding_agent_gate.review.tighten(decision, llm)` adds an opt-in model reviewer through an injected
  `g6.core.llm` caller. It can only make a decision stricter; a failed call leaves it as it was.
- `metaharness`: `g6.metaharness.coding` offers `scan_diff` as the optional `no-shortcut` check of a coding
  workflow, so, in a profile that adds it, a change whose diff shows a shortcut `scan_diff` reads fails that run.
- `safety_engine`: `detect_test_weakening` reads the test files before and after a change; this gate sees only
  the diff text you send. Run both on a change that touches tests.

## Failure modes it catches

- `hallucinated_success` (an agent reporting work as done that the evidence does not back up): `verify_answer`
  passes only with at least one counted item, or a verifier result with an exit code, and no finding. A readiness
  claim without a passing run, claims without any counted file, a failing exit code or a verifier `fail` each forces
  `revise`; a shortcut in the answer or the diff forces `block`.

- `unapproved_protected_change`: supplied path policies require independent host-observed review; missing policy, invalid paths and policy evaluation failure block; expired exemptions do not bypass review.

## Blind spots

- Ordinary evidence is caller-declared. Protected-path review requires host provenance, but the host remains responsible for authenticating the reviewer, checking the review and binding it to the current change.
- Paths are lexical: no filesystem reads, symlink resolution, diff discovery or review revision tracking. Hosts must supply all changed paths; omitted paths can hide protected changes.
- The text shortcut detector is a tripwire fitted to known phrasings, and a fresh paraphrase passes it; silence proves
  nothing.
- `scan_diff` reads a diff's structure. Semantic shortcuts (a test body made to return True, a snapshot pinned to
  wrong output) are out of its reach.
- Risk terms are words: a risky change described in other words reads `medium`, not `high`.
- It is advisory unless the caller obeys the verdict, and its effect on agent behaviour has not been measured.
- It declares no failure mode of its own.

## What it needs

Nothing outside G6: pure Python, with pydantic for the MCP input. No model, unless you call `review.tighten` with
your own caller.

## Examples

```g6-example op=verify_answer
{"task": "Fix the off-by-one in paginate()", "answer": "Changed range(n) to range(n + 1) in paginate(); the result is unverified beyond the run below.", "task_contract": "paginate(10, 3) returns 4 pages; existing tests stay green", "evidence": [{"kind": "file", "ref": "src/page.py"}, {"kind": "test", "ref": "pytest -q tests/test_page.py", "output": "3 passed", "exit_code": 0}], "claims": ["paginate() is in src/page.py"]}
```

The reply is `pass`, `qualified-draft`, with `evidence_basis` `caller-declared`: no rule objected, and the evidence
is the caller's word.

```g6-example op=preflight
{"task": "Deploy the auth change to production", "proposed_action": "skip the flaky login test and deploy", "risk_tier": "high"}
```

The reply is `block`: the plan skips a test, and high-risk work with no named review would be `needs_review` anyway.

```python
from g6.coding_agent_gate import Evidence, preflight, scan_diff, verify_answer
from g6.coding_agent_gate.review import tighten
from g6.core.llm import FakeLLM

plan = preflight("Fix the flaky login test", proposed_action="skip the flaky test and push with --no-verify")
assert plan.verdict == "block" and plan.completion_state == "blocked-escalated"

done = verify_answer(
    "Fix the off-by-one in paginate()", "Changed range(n) to range(n + 1) in paginate().",
    task_contract="paginate(10, 3) returns 4 pages; existing tests stay green",
    evidence=[Evidence("file", "src/page.py"), Evidence("test", "pytest -q tests/test_page.py", exit_code=0)],
)
assert (done.verdict, done.completion_state, done.evidence_basis) == ("pass", "qualified-draft", "caller-declared")

placeholder = verify_answer("Fix the off-by-one in paginate()", "Fixed. All tests pass.",
                            task_contract="paginate(10, 3) returns 4 pages", evidence=[Evidence("test", "none")])
assert placeholder.verdict == "revise" and placeholder.rejected  # a placeholder is not evidence

assert scan_diff("@@ -1,2 +1,2 @@\n-    assert total == 3\n+    assert True\n")  # found from the diff's structure

stricter = tighten(done, FakeLLM(default='{"verdict": "revise", "finding": "n=0 is untested"}'))
assert stricter.verdict == "revise"  # the opt-in model reviewer can only tighten
```

## The four open problems

- **Learning signals**: Not yet: the gate writes nothing. Each finding carries a stable `code` and the envelope's
  evidence lists `finding_codes`, so a caller can log them as labelled incidents. Inside a meta-harness coding
  workflow, a run that fails the `no-shortcut` check is recorded with that failed check.
- **Evaluators the model cannot influence**: its rules are fixed code with no model call, and the optional reviewer
  can only tighten. The agent being checked cannot change the rules through a call, but it supplies the evidence they
  read, which is why a pass stays `qualified-draft`. Not yet: the gate's code is not digest-pinned (the meta-harness
  pins the source of its `no-shortcut` wrapper, not of `scan_diff`).
- **Transfer by failure mode**: the checks are bound to `hallucinated_success`, not to a task: evidence structure
  looks the same in any repository. Fresh paraphrases of a shortcut pass the text tripwire, while the diff scan
  catches those that show in a diff (the package's tests pin five). Not yet: no catch rate on unseen task families.
- **Enough held-out cases**: Not yet: its catch and false-alarm rates are not judged by G6's sequential test; its
  tests are fixed regressions and survival cases.
