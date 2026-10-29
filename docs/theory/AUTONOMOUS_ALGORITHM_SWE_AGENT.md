# Autonomous G6 Algorithm Design + Complexity/SWE agent

The programmable agent is *not* the verifier. Production execution requires:
- a frozen `DesignMission` with explicit intent, scope, snapshot, tests and compute limits, and a person's approval of it: a `run_approval` item in the operator's queue, decided by someone other than the requester and bound to the mission's `fingerprint`;
- a `ProposalAgent.propose(AgentBrief) -> PatchProposal | None` implementation (your coding LLM adapter can implement this);
- a host-owned `RepoTestsRunner` wired to `g6.workforce.repo_tests.run` with its established mutant-on-copy test path;
- an operating-system boundary: every run asks for one (`require_isolation`), so a run no backend would confine is refused before it starts; bubblewrap on Linux and AppContainer on Windows both count, read from confine's record of the run;
- no direct modification of original tests or source repository by this controller.

The hard-coded resource planner models abstract operations, RAM and scratch; it
selects CPU and can *propose* an external-memory alternative. It cannot supply
an external-sort kernel or a GPU driver. GPU is considered only for a host-checked
kernel ID, and cost estimates still need empirical calibration. The system
distinguishes polynomial, pseudo-polynomial, exponential, and unmodeled algorithms.
Unknown or costly candidates are rejected; an algorithm-selection proposal is
not a proof of an asymptotic bound.

`run_agent` first creates a CIR model, computes an impact cone, performs a
bounded typed algorithm search (or SWE-only choice), generates a fusion check
plan and cost ranking, establishes a baseline through the independent runner,
then loops over propose -> AST validation -> confined test -> feedback/repair.
Proposals can only replace one source Python file in a copied repository; the
original is untouched. Duplicate proposals, changes to scope and test drift
are refused. Frozen tests are necessary but not automatically sufficient to
establish algorithmic correctness; an accepted candidate is at most
`qualified-draft`, requiring human review.

The current interface is intentionally pure Python rather than a second
general planner or MCP host. It can be called from a coding mission or a
CLI/MCP adapter after the host has approved the exact repository scope and
execution. G6's `coding_agent_gate.verify_answer` is reused, and a hash-chained
`ChainLedger` under the G6 home (`algorithm_design/missions.jsonl`) records each
run's decisions, its approver and its baseline's receipt, every entry prefixed
with the run's id; a second writer is refused as `ledger_conflict`.

Important remaining gaps:
- A concrete LLM/coding-agent adapter must supply proposals; no model is
  installed, implicitly called, or entitled to hidden test data by this module.
  The engineer this release ships, `NoEngineer`, refuses before any test runs.
- Resource estimates are planning heuristics. Full bit complexity, I/O costs,
  machine-specific benchmarks, parallel algorithms and accelerator kernels
  need separately checked implementations.
- Multi-file refactors, formal theorem proving, complete may-alias analysis
  and automated proof checking are not built.
- The runner baseline policy accepts a known failing baseline for a repair,
  but requires all selected frozen tests to pass on the candidate.
- `run_agent` is *not* a production merge gate. No review or approval is
  inferred from a passing suite.

Run unit tests: `python -m pytest tests/algorithm_design/test_autonomous.py`,
`tests/algorithm_design/test_mission_trust.py` and `tests/complexity/test_cir_fusion.py`,
each in its own process; these use fake host runners.
`tests/algorithm_design/test_mission_confined.py` runs a mission for real under
bubblewrap on Linux (it skips elsewhere).
