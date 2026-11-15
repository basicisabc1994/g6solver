# G6 0.3.0: patch review, consent and the improvement loop over MCP

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

This version is not yet tagged or published: no tag, nothing on PyPI. Merging, tagging, publishing and deploying the
site are the owner's steps. The full preparation note is [v0.3.0.md](v0.3.0.md); the notes for 0.2.0 are
[v0.2.0.md](v0.2.0.md).

## What ships

The MCP server is the main surface. It goes from 20 tools to 56; the core library keeps 38 components.

- **Patch review.** A coding agent calls `g6_review_patch` before it says a Python change is done. G6 runs the tests
  that check each stated requirement inside an operating-system boundary, compares the changed tests, test helpers
  and test-runner files with the base commit for the signs of weakening it knows how to find, runs the other tests
  that touch the changed modules, and returns one outcome: test integrity compromised, defect detected, insufficient
  evidence, or supported within scope. `g6 setup --client claude --allow patch_review.review` (or `--client codex`)
  registers the installation with the client, installs the `using-g6` skill for it and allows patch review.
- **Consent before any cloud model** (`g6_cloud_grant`, `g6_cloud_grants`, `g6_cloud_revoke`, `g6_disclosures`). No
  user data reaches a cloud model unless the person granted it, per source and per provider, in the client's own
  dialog. Without a grant the work stays local, or the result is withheld. One check covers every path that can reach
  a model: G6's own model calls, `g6_invoke` and `g6 invoke`, the Python API `g6.invoke`, the cognitive bridge, and the
  self-improvement loop's Claude Code and Codex sessions. A disclosure record says what was sent.
- **The self-improvement loop, over MCP** (`g6_improve_*`). A person agrees in the client's dialog to each start,
  enrolment, resume and demotion; status only reads, and the stop needs no dialog. A frozen file that changes, a
  provider refusal, a missing consent grant or a record that cannot be written stops the loop, kept as a safety stop
  for a person to resume.
- **Allowances** (`g6_allowances`): both providers' quota windows and advisory pacing. Unknown, stale or unbound
  readings never recommend more use.
- **Behaviour questions** (`g6_confirm_behaviour`): one question about a task, answered in the client's dialog. A
  confirmed answer becomes a check that every later patch review of the task runs.
- **Missions and review** (`g6_mission_*`, `g6_premortem`, `g6_review_until_clean`): a coding task carried to an
  evidenced result, with the reviewer loop that ends clean or says why not.
- **The Context Engine** (`g6_knowledge_*`): knowledge search over folders the person names in `G6_KNOWLEDGE_ROOTS`.
  Adding a source asks the person to accept a preview in the dialog.
- **The workflow record** (`g6_feedback`, `g6_suggestion_decide`, `g6_workflow_*`): off unless
  `G6_WORKFLOW_RECORD=on` is set where the server is registered.
- **Routing**: the `using-g6` skill routes to every tool that ships.

Merging stays the person's decision.

## What does not ship

- **V9 and V10**, the studies of patch review: both are registered, and neither has run. Nothing here says how often
  patch review finds a defect, or whether it shortens a person's work.
- **The study's execution and capture code**: excluded, and tested unreachable.
- **Section 15** (intent to formal property): its tool is not built, and its must-close findings stay with it.
- **Section 14 slices S6 and S7.**
- **Venture**, the third work mode.
- **The continuous integration check** that would run patch review on a pull request.

## Not yet measured

These parts ship switched on, and their measurement has not run. A passing test establishes only the behaviour it
covers; none of these is claimed to improve outcomes: the self-improvement loop, allowance pacing against real
provider windows, consent in a real client's dialog across Claude Code and Codex, the `using-g6` routing, the Context
Engine's retrieval quality, the workflow record's suggestions, and behaviour questions with real people.

## Limits

[v0.3.0-known-limits.md](v0.3.0-known-limits.md) lists every reproduced finding that ships as a known limit. Each one
fails in the safe direction (G6 refuses, withholds or reports less), needs code already running in G6's own process,
is a narrow platform edge ending in an error, is a narrowed race, or needs a certificate-bound file. The limits beyond
that list are in [v0.3.0.md](v0.3.0.md). These still hold from 0.2.0:

- **Isolation depends on the platform.** Linux confines the code G6 runs with bubblewrap (install the `bubblewrap`
  package); Windows with an AppContainer, after the one-time access grant `g6 doctor` prints. Patch review needs an
  operating-system boundary (Linux with bubblewrap, Windows after the one-time access grant): allowing unconfined
  runs does not make it run, and on macOS it refuses. macOS has no boundary G6 can use; elsewhere in the library, G6
  refuses to run untrusted code there unless the person allows it once with `g6 setup`, and code allowed that way
  runs with the person's own rights.
- **The patch runs where its tests run.** The patch's own code runs in pytest's own process, in the repository's own
  environment (its `.venv`, which the agent can change): a patch written to defeat the checks can make them report a
  pass, and G6 does not catch that.
- **G6's own environment.** Install G6 into an environment of its own made with `python -m venv --copies`, then
  `pip install ".[all]"` into it, then `g6 setup`: on Linux, G6 cannot yet tell its own Python from a project
  environment linked to the same one, and refuses to review then (`G6_E_REPO_PYTHON_INVALID`, before anything
  runs).
- **Python first.** Patch review runs pytest on Python repositories only.
- **Files git ignores.** G6 does not compare files git ignores, so an untracked, git-ignored file the tests could load
  puts test integrity in doubt. The reply names each such file once; if they belong to the project, commit them before
  the change under review, otherwise remove them or move them out of the repository, then review again.
- **What an outcome means.** Supported within scope means every stated requirement passed its stated checks; it is
  not proof that the patch does everything the task needs. The comparison for weakened tests is static and cannot
  show that the tests are as strong as before.
- **A trusted host.** Local settings assume one: an agent with the account's unrestricted shell can change them.
