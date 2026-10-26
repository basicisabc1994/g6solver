---
name: g6-safety-engine
description: Use when an agent loop or a tool-calling door needs trusted per-run limits (steps, tool calls, shell commands, files, lines, retries, wall time, privilege, cost) checked before each action, with a kill switch the planner cannot reset, or when a change touches tests, conftest.py or test configuration and you must know whether it weakened them before trusting a green run.
---

# G6 safety engine

Trusted runtime ceilings for one run, and a test-weakening check. `PolicyEngine.authorize_action` checks the kill
switch, the ceilings, guard verdicts, a validator quorum, the tool allow-list, forbidden paths and tests before a
final answer before an action runs, and returns `ALLOW`, `BLOCK` or `ESCALATE`. `detect_test_weakening` compares the tests and their configuration
before and after a change by reading the files; it runs nothing.

## When to use it

- Building an agent loop or a tool door: one `RuntimePolicy` from trusted configuration, one `RunState` per run,
  `authorize_action` before every call, then `record_execution` and `record_cost` after it, and `record_tests`
  after each test run the door itself ran.
- An agent's change touches tests and you are about to trust "the tests pass": `detect_test_weakening` on the
  files before and after, or on two snapshot directories.
- A run must stop: trip its kill switch.

## When not to use it

- Whether one outward action should go ahead, given its reach and reversibility: `action_gating`.
- Flagging injection or tampering phrasing in text: `guards`, then pass its verdict here.
- Whether the change itself is right: run the tests; nothing here runs them.
- An operating-system sandbox: this is not one.

## Python API

- `detect_test_weakening`: compare test files, every `conftest.py` and the pytest and CI configuration before and after a change; returns a `TestWeakeningReport`.
- `read_snapshot`: read a directory into the path-to-text mapping `detect_test_weakening` compares.
- `TestWeakeningReport`: `weakened`, `reasons`, `checked`, `not_checked`, `completion_state` and `to_envelope()`.
- `policy_engine.PolicyEngine`: `authorize_action(action, state, policy, guard_verdicts, validator_votes)`, the pre-action decision.
- `schema.RuntimePolicy`: the frozen, trusted ceilings, allow-list, forbidden paths and quorum settings for one run.
- `schema.ActionRequest`: one proposed tool call, untrusted data: tool, arguments, files touched, privilege, shell flag, estimated lines, cost ceiling, and `is_final` for the call that delivers the final answer.
- `schema.AuthorizationDecision`: the decision, its reason, `permitted_action` (set only on `ALLOW`) and the `policy_checks` that ran.
- `schema.ValidatorVote`: one validator's vote and its declared fault domain.
- `state.RunState`: the run's counters, its kill switch and its policy; `record_tests(passed)` records the latest test run.
- `ceilings.exceeds_ceiling`: the first ceiling an action would breach, projected before it runs.
- `kill_switch.KillSwitch`: a run-scoped stop that trips one way; `KillSwitch.with_reset_token()` returns a switch and the only token that resets it, and a switch made without one never resets.
- `quorum.quorum_satisfied`: whether the votes clear the policy's quorum.

## Reading the result

`authorize_action` checks in this order and stops at the first refusal: the run's kill switch and G6's process-wide
stop; the ceilings (tool calls, steps, shell commands, files modified, lines changed, retries, runtime, privilege,
cost); a guard `BLOCK` verdict, which also trips the kill switch; a guard `ESCALATE`; the quorum, when
`require_quorum` is on; the allow-list, enforced whenever it is not empty; forbidden paths, matched on normalised,
case-folded spellings; and, when `require_tests_before_final` is on (the default), an `is_final` action before the run
has recorded a passing test run (`record_tests(passed=True)`; only a boolean `True` counts). Every decision that gets
past the ceilings is one step of `max_steps`, whether it is allowed or refused, so a planner that keeps proposing
refused actions stops too. The decision is not a token: the code that runs the action must obey it.

A weakening report is `blocked-escalated` when `weakened`, else `qualified-draft`, never `verified`. Its envelope's
warning code is `G6_E_TEST_WEAKENING_DETECTED`, `G6_E_TEST_WEAKENING_STATIC_ONLY` (nothing found in what it
compared) or `G6_E_TEST_WEAKENING_NOTHING_COMPARED`. `not_checked` always lists what a static scan cannot see.

## Alone and composed

From Python only: `from g6.safety_engine import detect_test_weakening`, and the policy pieces from their modules
(`g6.safety_engine.policy_engine`, `.schema`, `.state`). It has no block and no MCP op.

Composed:

- `workforce`: its single call door authorises every call with `PolicyEngine` against the worker's `RuntimePolicy`,
  and reads a cost-ceiling refusal as `refused:budget`; its patch checks run `detect_test_weakening` on the original
  repository against the changed copy.
- `guards`: pass `PatternGuard` verdicts as `guard_verdicts`; the engine, not the guard, decides.

## Failure modes it catches

- `runaway_run` (a run that keeps going past its limits): every ceiling is projected before the action runs, from
  the frozen `RuntimePolicy` the caller passes (load it from trusted configuration before the run; the engine cannot
  tell where it came from); a negative cost cannot lower the projection; the step ceiling counts refused proposals
  as well as allowed ones; the kill switch trips one way, and `reset` refuses anything but the token handed to the
  switch's owner, which the switch does not keep.
- `test_weakening` (an agent weakening tests to make them pass): it reports a deleted test file, a removed, renamed
  or neutralised check, fewer tests, an early return, an added skip, xfail, `importorskip`, `pytestmark`, mock or
  autouse fixture, a module-level patch, any code change to a `conftest.py` in any directory, and any change to the
  pytest, test-runner or CI configuration.

## Blind spots

- Ceilings bound only what passes through a door that asks and obeys; it does not observe an action's real effects.
- The counters move only when the caller records them, and the shell, files, lines and privilege axes read the
  action's declared fields. The workforce door leaves those four axes inert, and nothing there records a retry.
- The reset token is a capability, not a security boundary: code in the same process can still write a switch's
  private fields directly (the `guards` source scan flags a write to its `_tripped` flag).
- The engine checks whatever `policy` it is passed and never compares it with `state.policy`.
- The tests-before-final check holds only an action the caller marks `is_final`, and `record_tests` is the word of
  the code that calls it; the workforce door marks no call final, so there it never fires.
- The quorum counts declared fault domains; it proves nothing about the validators' independence.
- The weakening check is static: whether the tests pass, or are collected at all, is not established; a check whose
  text is unchanged but proves less (a trivial fixture, `assert x or True`) is not seen; test files outside pytest's
  default names and options from the command line or plugins are not read.
- It declares no failure mode of its own.

## What it needs

Nothing outside G6: pure standard-library Python (the weakening check parses files with `ast`, `tomllib` and
`configparser`). Nothing is run.

## Examples

```python
from g6.safety_engine import detect_test_weakening
from g6.safety_engine.ceilings import COST_CEILING
from g6.safety_engine.policy_engine import PolicyEngine
from g6.safety_engine.schema import ActionRequest, RuntimePolicy
from g6.safety_engine.state import RunState

policy = RuntimePolicy(max_cost_cents=50, allowed_tools=frozenset({"search", "write"}),
                       forbidden_paths=frozenset({"secrets/"}))
state = RunState(policy=policy, run_id="run-1")
engine = PolicyEngine()
first = engine.authorize_action(ActionRequest(tool_name="search", cost_ceiling_cents=30), state, policy)
assert first.decision == "ALLOW" and first.permitted_action is not None
state.record_execution(first.permitted_action)
state.record_cost(30)
second = engine.authorize_action(ActionRequest(tool_name="search", cost_ceiling_cents=30), state, policy)
assert second.decision == "BLOCK" and COST_CEILING in second.reason  # 30 spent + 30 more > 50
assert engine.authorize_action(ActionRequest(tool_name="shell"), state, policy).decision == "BLOCK"
assert engine.authorize_action(ActionRequest(tool_name="write", files_touched=("SECRETS/key.pem",)),
                               state, policy).decision == "BLOCK"  # forbidden, whatever the spelling
state.kill_switch.trip("stopped by the operator")
assert engine.authorize_action(ActionRequest(tool_name="search"), state, policy).decision == "BLOCK"

before = {"tests/test_calc.py": "from calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"}
after = {"tests/test_calc.py": "import pytest\nfrom calc import add\n\n\n@pytest.mark.skip\n"
                               "def test_add():\n    assert add(2, 2) == 4\n"}
report = detect_test_weakening(before, after)
assert report.weakened and report.completion_state == "blocked-escalated"
assert report.to_envelope()["warning_card"]["code"] == "G6_E_TEST_WEAKENING_DETECTED"
```

## The four open problems

- **Learning signals**: Not yet: it writes nothing itself. Each decision names its reason and the `policy_checks`
  that ran, and the weakening report's envelope carries a `G6_E_*` code and its reasons, records a caller can keep;
  the workforce door files a cost refusal as `refused:budget`.
- **Evaluators the model cannot influence**: the policy is a frozen `RuntimePolicy` the caller passes (load it from
  trusted configuration before the run; the engine cannot tell where it came from); the kill switch resets only with
  the token its owner was given; the weakening check reads the file text it is given (G6 reads the files itself when given
  two directories), so the agent whose change it judges cannot argue with it. Not yet: the code is not
  digest-pinned.
- **Transfer by failure mode**: it is bound to `runaway_run` and `test_weakening`, not to a task: the ceilings are
  counters any loop has, and the weakening rules read pytest's structure in any Python repository (only Python and
  pytest). Not yet: catch rates on unseen task families.
- **Enough held-out cases**: Not yet: its catch and false-alarm rates are not judged by G6's sequential test; its
  tests are fixed regressions, with null tests showing the older, weaker weakening detector fails them; the ceiling
  and policy tests have none.
