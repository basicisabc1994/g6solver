# G6 0.2.0: patch review for Claude Code and Codex

Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

## What ships

- **Patch review.** A coding agent calls `g6_review_patch` before it says a Python change is done. G6 runs the tests
  that check each stated requirement inside an operating-system boundary, compares the changed tests, test helpers
  and test-runner files with the base commit for the signs of weakening it knows how to find, runs the other tests
  that touch the changed modules, and returns one outcome: test integrity compromised, defect detected, insufficient
  evidence, or supported within scope. The reply's readable report (`assessment.report`) puts what G6 reproduced
  first, then what is not established, then what the person decides. Over MCP the reply is compact, so that it fits
  in a client's tool result; the full assessment is saved in G6's state folder. A caller may also give proposed
  checks and template instances; neither ever passes a requirement.
- **Setup for both clients.** `g6 setup --client claude --allow patch_review.review` (or `--client codex`) registers
  the installation with the client, installs the `using-g6` skill for it, allows patch review, and ends by showing the
  first review call. When the client already has an entry called `g6`, setup stops and says how to replace it.
- **The skill.** `using-g6` tells the agent to review a Python patch before saying the task is done, to state the
  task as numbered requirements with their checks, to show the report verbatim, and to repair: fix what G6
  reproduced, add each earlier reproduction as a stated check, review again, and stop after three rounds, when a round
  changes nothing, or when the report says only the person can decide. It never calls a patch verified, and never
  merges.
- **The rest of the library**, as the README's status table describes it.

Merging stays the person's decision.

## Fixed before release

- **A security fix, found by a worked example.** `g6 setup` registered G6's MCP server as `python -c ...`. Claude
  Code and Codex start the server in the project's folder, and `-c` puts that folder first on Python's import path.
  So a project whose top-level package had the name of one of G6's dependencies replaced that dependency inside G6's
  own server: in pydantic's repository the server did not start, and in starlette's repository it imported and ran
  the repository's `starlette`, outside any boundary, with the person's rights. Setup now starts the server with
  `python -P`, which keeps the project's folder off the import path. An entry made before this fix keeps the old
  command: remove it with `claude mcp remove --scope user g6` (or `codex mcp remove g6`) and run `g6 setup` again.
- **No network request when the server starts.** FastMCP, the library G6's MCP server is built on, asked PyPI for
  its latest version each time the server started, and read its settings from a `.env` file in the folder the server
  started in, the project's, where a line could name a class for G6's server to import and build. G6's server now
  turns the version check off and reads no such file; what a person sets in the environment
  (`FASTMCP_CHECK_FOR_UPDATES`, `FASTMCP_ENV_FILE`) is kept.
- **A meta-harness workflow a project ships is never confirmed for the person.** G6 finds workflows in the project's
  folder, and a workflow's confirmation lived in its own files there, so a project could ship one confirmed under any
  name, and G6 ran it. A confirmation now counts only when G6 recorded it in its own state folder when a person
  confirmed; and the tools that run a workflow's code over MCP are refused unless the person allows them in
  `G6_MCP_SENSITIVE` (`metaharness.run`, `metaharness.evaluate_start`, `metaharness.evaluate`).
- **A false alarm on pdm-backend's build output, found by a worked example.** pdm-backend's editable install writes a
  git-ignored `.pdm-build/` folder with a `.pth` file in it, which Python never reads from there, and G6 counted it as
  a file that could change the tests. G6 now leaves `.pdm-build/` out of its scan of ignored files, as it does
  `build/` and `dist/`.

## What does not ship

- **G6's own model calls in patch review.** Patch review makes no model call unless the caller turns on
  `search={"propose": true}`. Elsewhere in the library, a step that needs a model uses the provider the person chose,
  in their key file or with `G6_LLM_PROVIDER`; none is chosen by default. The proposer that asks a model for checks
  is in the code and off by default, and the launch skill does not use it. It has had one development smoke run and
  no study, so nothing is claimed for it.
- **V9 and V10**, the studies of patch review: both are registered, and neither has run. Nothing here says how often
  patch review finds a defect, or whether it shortens a person's work.
- **The plugins and hooks** for Claude Code and Codex.
- **Venture**, the third work mode.
- **The continuous integration check** that would run patch review on a pull request.

## Limits

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
  puts test integrity in doubt. A `.py` file named like any module in the repository counts, wherever it is, so a
  data fixture called `a.py` can raise it. The reply names each such file once; if they belong to the project, commit
  them before the change under review, otherwise remove them or move them out of the repository, then review again.
  A file the patch itself adds under an ignored path, with `git add -f`, is part of the change and is judged as one.
- **What an outcome means.** Supported within scope means every stated requirement passed its stated checks; it is
  not proof that the patch does everything the task needs. The comparison for weakened tests is static and cannot
  show that the tests are as strong as before.
- **A trusted host.** Local settings assume one: an agent with the account's unrestricted shell can change them.
