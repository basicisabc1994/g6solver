# Autonomous G6 Algorithm Design + Complexity/SWE agent (experimental)

The programmable agent is *not* the verifier. Production execution requires:
- a frozen `DesignMission` with explicit intent, scope, snapshot, tests, compute limits, and execution approval;
- a `ProposalAgent.propose(AgentBrief) -> PatchProposal | None` implementation (your coding LLM adapter can implement this);
- a host-owned `RepoTestsRunner` wired to `g6.workforce.repo_tests.run` with its established mutant-on-copy test path;
- `G6_REQUIRE_ISOLATION=1` set **before** the host starts; on Linux/macOS today no backend is available and the run refuses;
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
execution. G6's `coding_agent_gate.verify_answer` is reused, and the
`ChainLedger` records decision lineage in memory. The existing workforce
database remains the authoritative repository for durable evidence if the
calling mission persists receipts.

Important remaining gaps:
- A concrete LLM/coding-agent adapter must supply proposals; no model is
  installed, implicitly called, or entitled to hidden test data by this module.
- Resource estimates are planning heuristics. Full bit complexity, I/O costs,
  machine-specific benchmarks, parallel algorithms and accelerator kernels
  need separately verified implementations.
- Multi-file refactors, formal theorem proving, complete may-alias analysis,
  automated proof checking and safe general-purpose execution on non-Windows
  platforms are not built.
- The runner baseline policy accepts a known failing baseline for a repair,
  but requires all selected frozen tests to pass on the candidate.
- `run_agent` is *not* a verified production merge gate. No review or approval
  is inferred from a passing suite.

Run unit tests: `python -m pytest tests/algorithm_design/test_autonomous.py
tests/complexity/test_cir_fusion.py`. These tests use fake host runners; actual
AppContainer tests must be run on a configured Windows host.
