# Self-improvement loop, phase P1: integrating pull requests #2 to #7 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the owner's pull requests #2 to #7 onto `claude/self-improvement` by merge commits, make every new package a
conformant G6 component whose every operation answers through `g6_invoke`, fold each duplicate into one path, close the
trust gaps, and show it with green CI and an installed-MCP smoke test (the outside review's gates 1 and 5).

**Architecture:** Six merges in stack order (#2, #3, #4, #6, #5, #7). Each task merges one branch and does that branch's
conformance work, so every task ends with CI green. `algorithm_design`, `complexity` and `self_maintenance` become core
components with typed blocks. #4's two packages fold away: its prompts into one catalogue in `align_prompt_library`, its
engines into `metaharness`. Bare-bool approvals become operator `ActionQueue` decisions with a recorded approver.
Isolation is read from confine's record, receipts come from `repo_tests`, and live engineer paths stay inert behind
stable `G6_E_*` refusals. Nothing is deleted that P2 or P3 builds on.

**Tech Stack:** Python 3.12 and 3.13, pydantic v2 blocks on `g6.core.ai_block.AIBlock`, `returns`, pytest, ruff,
import-linter, fastmcp (stdio), Docker image `g6-a2-linux` (bubblewrap), GitHub Actions on GitHub Free.

**Spec:** `docs/superpowers/specs/2026-10-29-self-improvement-loop-design.md`: section 8's P1 row, section 7, section 3,
and Appendix A (gates 1 and 5).

## Global Constraints

- **Status line, verbatim, wherever a new or changed file states G6's status:** "Early alpha. Individual capabilities have
  been tested; general application-level improvements have not yet been established."
- **Wording:** never "verified" of a patch or of G6; never "experimental" (DECISIONS #36); results are "worked
  examples", never "proof of concept". Every new block is `@component_maturity("beta", reason=...)`; no module-level
  `__g6_maturity__` in a new package.
- **Certificate-bound files are never edited.** They are the `g6/<path>` entries of every
  `src/g6/**/certificate/certificate.json`, among them `g6/__init__.py`, `g6/core/registry.py`, `confine.py`, `llm.py`,
  `ledger.py`, `host_models.py`, `door.py`, `failure_modes.py`, `maturity.py`, `ai_block.py`, `reliability_envelope.py`,
  `storage.py`, `g6/epistemic_integrity/*`, `g6/action_gating/*` and `g6/workforce/component_certificate.py`. Importing
  them is fine. Check B, before every commit, prints nothing:
  ```powershell
  $bound = Get-ChildItem "$W\src" -Recurse -Filter certificate.json | Where-Object FullName -match '\\certificate\\certificate\.json$' |
    ForEach-Object { [regex]::Matches([System.IO.File]::ReadAllText($_.FullName), '"g6/([^":]+)') | ForEach-Object { "src/g6/" + $_.Groups[1].Value } } | Sort-Object -Unique
  git -C $W diff --name-only abd38ad HEAD | Where-Object { $p = $_; $bound | Where-Object { $p -eq $_ -or $p.StartsWith("$_/") } }
  ```
- **Tests first; never weaken an existing test.** This plan extends exactly three pinned lists, each in the task that
  needs it, keeping the equality assertion: the core count in `tests/site/test_home_page.py:31`, `OPS` and `SENSITIVE` in
  `tests/workspace/test_align_prompt_library.py`, and `_SHIPPED` in `tests/goal_engine/test_goal_engine_findings.py:472`.
  Tests that arrive with a pull request are adapted only where its API changes, keeping every assertion.
- **One test file per pytest process; never the whole suite in one process.** The full suite runs only in CI
  (`tools/suite_split.ps1`).
- **No model calls and no spend:** `g6.core.llm.FakeLLM` or a plain function stands in for every model; a test that
  reaches the door with no model sets `G6_DISABLE_LLM=1`.
- **No local user name, home folder or private path in any file.** The worktree is `$W`, temporary files live under
  `$env:TEMP`, and the environment under `$env:LOCALAPPDATA`; no command in a committed file names a real folder.
- **Surfaces:** MCP is the surface of record. No new `g6` command; the frozen CLI reaches new blocks only through the
  shared door (`g6 invoke`), with no CLI change.
- **Layer contract:** every new top-level package joins `source_modules` of the contract "the meta-harness is a layer on
  top" in `pyproject.toml`. Nothing outside `g6/metaharness` imports `g6.metaharness`.
- **Declarations of a new core component**, in its `__init__.py`: `__g6_component__ = True`, `__g6_block__`, a literal
  `__g6_sensitive_ops__`, `__g6_requires__` equal to what `tests/test_component_dependencies.py::_actual` computes,
  literal `__g6_mitigates__` and `__g6_introduces__` tuples of registered codes, `__g6_outside__ = ()` and
  `__g6_extras__ = {}`. Never `__g6_family__`: a family makes the site list it as an extension. Every `.py` file starts
  with `# SPDX-License-Identifier: Apache-2.0`.
- **Block replies** are built with `g6.core.reliability_envelope.envelope_fields` and `warning_card`, and carry
  `component`, `op`, `completion_state`, `warning_card`, `evidence`, `request_id` and `run_id`. A refusal is
  `Result.ok` of a `blocked-escalated` reply with its own `G6_E_*` code, never a raised error, so skill examples always
  return a `result`. No reply is ever `verified`. Anything a caller declares (an authority, an "independently observed"
  flag, an annotation) is returned under `evidence[0]["declared_by"] == "caller"` and never raises a state.
- **Variables:** `$W` is the worktree root (`git rev-parse --show-toplevel`); `$PY` is
  `"$env:LOCALAPPDATA\g6-p1-venv\Scripts\python.exe"`; `$MSG` is `"$env:TEMP\g6-p1-msg.txt"`. Run every command from
  `$W` (`Set-Location $W`). `T <file> [-k expr]` means `& $PY -m pytest -q -p no:cacheprovider <file> [-k expr]`.
- **Gates G(names)**, each its own process, run at the end of every task for the components it touches:
  `T tests/conformance/test_conformance.py -k "<names joined by ' or '>"`;
  `T tests/core/test_component_skills.py -k "<names>"`; `T tests/test_component_dependencies.py`;
  `& $PY tools/gen_catalog.py; & $PY tools/gen_failure_map.py; T tests/tools/test_catalog_current.py`;
  `T tests/metaharness/test_phase0_guardrails.py`; `T tests/core/test_component_summaries.py`;
  `T tests/site/test_home_page.py`; `T tests/site/test_faq_page.py`; `T tests/site/test_docs_copy.py`;
  `T tests/site/test_components_page.py`; `& "$env:LOCALAPPDATA\g6-p1-venv\Scripts\lint-imports.exe" --config pyproject.toml --no-cache`.
- **Commit routine C:** write the message with
  `[System.IO.File]::WriteAllText($MSG, $text, [System.Text.UTF8Encoding]::new($false))`, ending with the two trailer
  lines `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_016opK1M2VFbXVve6kiJtAxW`; then
  `git -C $W -c user.name=Claude -c user.email=noreply@anthropic.com commit -F $MSG`. A merge is
  `git -C $W -c user.name=Claude -c user.email=noreply@anthropic.com merge --no-ff -F $MSG <pinned sha>`, never a rebase.
  Every subject line except a task's last ends with `[skip ci]`.
- **Push routine P, after every commit:** `git -C $W diff --check HEAD~1 HEAD`; `& $PY -m ruff check <changed .py>`;
  check B; `T tests/test_no_local_paths.py`; `& $PY tools/export_public.py --scan . --public-ci`; then
  `git -C $W push origin claude/self-improvement`. After a task's last push, wait for its CI run with
  `gh run watch (gh run list -R basicisabc1994/g6solver -b claude/self-improvement -L 1 --json databaseId -q '.[0].databaseId') -R basicisabc1994/g6solver --exit-status`;
  the task is done only when it is green.
- **Docker routine D(file)**, for tests that run confined code; it starts and removes its own container and never stops
  one it did not start:
  ```powershell
  docker run --rm --network none --security-opt seccomp=unconfined --security-opt apparmor=unconfined `
    -e G6_EXPECT_BACKEND=bubblewrap -e PYTHONPATH=/repo/src -e PYTHONDONTWRITEBYTECODE=1 -e G6_HOME=/tmp/g6home `
    -v "${W}:/repo:ro" -w /repo g6-a2-linux python3 -m pytest -q -p no:cacheprovider <file>
  ```
- **Merge pins.** Merge exactly these commits; if `git rev-parse origin/<branch>` has moved, stop and report:
  #2 `796c930dd9` (feature/cir-algorithm-fusion-v01), #3 `6884ba5c33` (feature/autonomous-algorithm-swe-agent),
  #4 `87e8586e03` (feature/cognitive-bootstrap-eurisko-prompt-evolution), #6 `f2aba33fc3`
  (feature/metacognition-critical-thinking-prompts), #5 `4f24a57a38` (feature/goal-tree-proposal-engine), #7
  `e598bae39c` (feature/self-maintenance-control-plane-v01). Every pull request only adds files, and the files #2 and #3
  both add are identical, so no conflict is expected; if one appears, keep the release line's version of every file the
  pull request did not add, and report it.

## Review Focus

1. **PR #7's live-agent path.** `run_existing_agent` starts Codex or Claude with `writes=True` and network on. In P1 it
   must refuse with `G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF` before constructing any runtime or starting any process,
   even when a person approved the cycle; `run_one_cycle` passed it as `agent_runner` must return that refusal. Task 9
   pins both.
2. **Bool approvals.** `execution_approved`, `operator_enabled` and `promote(approved=True)` let a caller approve their
   own run. Every approval must become an `ActionQueue` decision by a person other than the requester, bound to exactly
   this mission or ticket. An unapproved item, an item approved for another binding, an `auto:` reviewer or the
   requester as reviewer is refused, and the approver's name is recorded. Tasks 4, 6 and 9 pin it.
3. **Isolation counting.** A run that confine's record says ran under bubblewrap must count as isolated, as must one
   under AppContainer. A run whose record names no backend (the person's `G6_ALLOW_UNCONFINED` opt-in) or has network
   on must not count, whatever the runner class says. Task 4 pins all four cases, and the bubblewrap one in Docker.
4. **Caller-declared facts over MCP.** An agent sends `authority: "person_confirmed"`, `independently_observed: true` or
   a state annotation. The reply keeps it as the caller's word (`declared_by: "caller"`): it may change which
   obligations count as normative, but never raises the state above `qualified-draft`. Tasks 1 and 9 pin it.
5. **Hostile sizes over MCP.** These include a `files` map of 4 MB or more, kernel parameters past each kernel's bound,
   more than 16 parameter keys, and `max_model_calls` of 101 or a model that always answers 40,001 characters. Each is
   refused with a stable code within its stated bound, never with a traceback or an unbounded run. Tasks 1, 2 and 8
   pin it.

---

### Task 1: Merge PR #2; `complexity` and `algorithm_design` become components

**Files:**
- Merge: `796c930dd9` (19 added files: `src/g6/algorithm_design/*`, `src/g6/complexity/*`,
  `src/g6/harness/verification_controller.py`, two docs, two tests)
- Create: `src/g6/complexity/inputs.py`, `src/g6/complexity/codes.py`, `src/g6/complexity/block.py`,
  `src/g6/complexity/skill/SKILL.md`
- Create: `src/g6/algorithm_design/codes.py`, `src/g6/algorithm_design/block.py`, `src/g6/algorithm_design/skill/SKILL.md`
- Modify: `src/g6/complexity/__init__.py`, `src/g6/algorithm_design/__init__.py` (docstring first line, declarations)
- Modify: `src/g6/algorithm_design/data/ontology.json`, `src/g6/algorithm_design/data/skiena_problem_index.json` (remove
  every `pdf_page` key; keep `section` and `printed_page`)
- Modify: `pyproject.toml` (layer contract `source_modules` += `"g6.algorithm_design"`, `"g6.complexity"`, sorted)
- Modify: `web/pages/catalogue.py` (`PLAIN`, `TABLE`), `web/content/faq.md` and `site/docs/faq.md` (core table rows
  and counts), `README.md:43`, `site/docs/index.md:45`, `site/docs/llms.txt` (36 components)
- Modify: `tests/site/test_home_page.py:31` (`len(rows) == 34` becomes `36`)
- Regenerate: `catalog/components.json`, `catalog/failure_modes.json`, `site/docs/components.md`
- Test: `tests/complexity/test_complexity_block.py`, `tests/algorithm_design/test_algorithm_design_block.py`

**Interfaces:**
- Produces, in `g6.complexity.inputs`: `RequirementIn` (`id`, `text`, `authority`, `source`, `kind`) and `ObligationIn`
  (`id`, `requirement_id`, `expectation`, `oracle_source`, `normative: bool`), pydantic with `extra="forbid"`;
  `intent_from(requirements: list[RequirementIn], obligations: list[ObligationIn]) -> FrozenIntent`;
  `MAX_TOTAL_CHARS = 4_000_000`; `files_problem(files: Mapping[str, str]) -> str` ("" or why the map is refused).
- Produces, in `g6.complexity.codes`: `INPUT_INVALID = "G6_E_COMPLEXITY_INPUT_INVALID"`,
  `ATLAS = "G6_E_COMPLEXITY_ATLAS"`, `IMPACT = "G6_E_COMPLEXITY_IMPACT"`, `SIMPLIFY = "G6_E_COMPLEXITY_SIMPLIFY"`.
- Produces `ComplexityBlock` with `ComplexityInput` (`op: Literal["atlas", "impact", "simplify", "ops", "help"]`;
  `files: dict[str(≤300), str(≤1_000_000)]` with at most 500 entries; `package: str(≤100)`; `requirements`,
  `obligations`; `environment_digest: str(≤128)`; `changed: list[str(≤300)]` with at most 100 entries;
  `state_annotations: dict[str, dict[str, str]]` with at most 200 entries; `request_id`, `run_id`).
- Produces, in `g6.algorithm_design.codes`: `INPUT_INVALID = "G6_E_ALGO_INPUT_INVALID"`,
  `CATALOGUE = "G6_E_ALGO_CATALOGUE"`, `ONTOLOGY = "G6_E_ALGO_ONTOLOGY"`, `PROPOSAL = "G6_E_ALGO_PROPOSAL"`,
  `NO_CANDIDATE = "G6_E_ALGO_NO_CANDIDATE"`, `UNKNOWN_CANDIDATE = "G6_E_ALGO_UNKNOWN_CANDIDATE"`.
- Produces `AlgorithmDesignBlock` with `AlgorithmDesignInput`: `op` lists `primitives`, `ontology`, `search`, `fuse`,
  `ops` and `help` (Task 2 adds `estimate`, `list_kernels`, `run_kernel`). Its fields: `representations` (≤16);
  `target`; `established_properties` (≤50); `constraints` (≤50); `problem_id`; `max_depth` (1 to 8, default 3);
  `max_expansions` (1 to 10,000, default 128); `max_candidates` (1 to 500, default 16); `limit` (1 to 100, default 20);
  `candidate_id`; and, for `fuse`, the complexity fields above.

- [ ] **Step 1: Set up and confirm the baseline.** Create the environment outside the worktree:
  `python -m venv "$env:LOCALAPPDATA\g6-p1-venv"; & $PY -m pip install -e "$W[dev,all,site]"`. Confirm that
  `git -C $W status --short` is empty, that `git -C $W merge-base --is-ancestor abd38ad HEAD` succeeds, and that
  gates G(goal_engine) pass before anything changes.
- [ ] **Step 2: Merge #2.** `git -C $W fetch origin feature/cir-algorithm-fusion-v01`; check the pin; run C with subject
  `integration: merge PR #2, CIR and algorithm design [skip ci]` as a merge of `796c930dd9`; then P.
- [ ] **Step 3: Write the failing block tests**

```python
# tests/complexity/test_complexity_block.py
FILES = {"shop/__init__.py": "", "shop/api.py": "from shop import state\ndef read():\n    return state.value\n",
         "shop/state.py": "value = 1\ndef update():\n    global value\n    value = 2\n"}
REQ = [{"id": "r1", "text": "Preserve authorization", "authority": "person_confirmed", "source": "person", "kind": "preserve"}]
OBL = [{"id": "o1", "requirement_id": "r1", "expectation": "No cross-account revocation", "oracle_source": "person", "normative": True}]
BASE = {"files": FILES, "package": "shop", "requirements": REQ, "obligations": OBL, "environment_digest": "env-v1"}

def test_atlas_answers_over_mcp_as_a_qualified_draft_bound_to_the_tree():
    reply = invoke_reply("complexity", "atlas", json.dumps(BASE))
    assert reply["ok"] and check_envelope(reply) == [] and reply["completion_state"] == "qualified-draft"
    r = reply["result"]
    assert (r["component"], r["op"], r["warning_card"]["code"]) == ("complexity", "atlas", "G6_E_COMPLEXITY_ATLAS")
    intent = intent_from([RequirementIn(**REQ[0])], [ObligationIn(**OBL[0])])
    assert r["fingerprint"] == build_atlas(FILES, package="shop", intent=intent, environment_digest="env-v1").fingerprint
    assert "shop.state" in r["components"] and r["complete"] is (not r["unknowns"])
    assert r["evidence"][0]["declared_by"] == "caller"          # Review Focus 4: the authority is the caller's word

def test_impact_names_the_reverse_cone_and_keeps_every_normative_obligation():
    r = invoke_reply("complexity", "impact", json.dumps({**BASE, "changed": ["shop.state"]}))["result"]
    assert "shop.api" in r["impacted"] and r["preservation_obligations"] == ["o1"] and r["parallel_safe"] is False

def test_simplify_proposes_only_for_state_the_caller_marked_avoidable():
    notes = {"shop.state.value": {"essentiality": "accidental_avoidable", "derivation": "read through api", "justification": "cache"}}
    r = invoke_reply("complexity", "simplify", json.dumps({**BASE, "state_annotations": notes}))["result"]
    assert [p["target"] for p in r["proposals"]] == ["shop.state.value"]
    assert {p["status"] for p in r["proposals"]} == {"proposal_only"} and "o1" in r["proposals"][0]["required_checks"]

@pytest.mark.parametrize("params", [{}, {**BASE, "files": {}}, {**BASE, "environment_digest": ""},
    {**BASE, "package": "not a package"},
    {**BASE, "files": {f"big/m{i}.py": "x" * 900_000 for i in range(5)}},            # Review Focus 5: 4.5 MB
    {**BASE, "requirements": [{**REQ[0], "authority": "model_hypothesis"}]}])        # a model cannot make it normative
@pytest.mark.parametrize("op", ["atlas", "impact", "simplify"])
def test_bad_input_is_refused_with_its_own_code(op, params):
    reply = invoke_reply("complexity", op, json.dumps(params))
    assert reply["completion_state"] == "blocked-escalated" and check_envelope(reply) == []
    assert reply["warning_card"]["code"] in ("G6_E_COMPLEXITY_INPUT_INVALID", "G6_E_INPUT_VALIDATION")

def test_it_is_a_registered_beta_block_that_runs_no_code():
    info = discovery.describe("complexity")
    assert (info["block"], info["maturity"], sorted(info["ops"]), info["sensitive_ops"]) == \
        ("ComplexityBlock", "beta", ["atlas", "impact", "simplify"], [])
    # and no file under src/g6/complexity imports subprocess, socket or importlib, or calls exec, eval or compile
```

```python
# tests/algorithm_design/test_algorithm_design_block.py
SEARCH = {"representations": ["sequence"], "target": "membership"}
BASE = {...}   # the same files, package, requirements, obligations and environment_digest as the complexity test

def test_primitives_lists_the_nine_catalogue_patterns():
    r = invoke_reply("algorithm_design", "primitives", "{}")["result"]
    assert len(r["primitives"]) == 9 and {"linear_scan", "binary_search", "dijkstra"} <= {p["id"] for p in r["primitives"]}
    assert r["warning_card"]["code"] == "G6_E_ALGO_CATALOGUE"

def test_ontology_is_metadata_and_never_assumes_a_precondition():
    got = invoke_reply("algorithm_design", "ontology", json.dumps({"problem_id": "interval_scheduling"}))["result"]
    assert {m["implementation_status"] for m in got["matches"]} == {"metadata_only"}
    assert any(m["status"] == "needs_preconditions" for m in got["matches"])

def test_search_proposes_candidates_only():
    r = invoke_reply("algorithm_design", "search", json.dumps({"representations": ["sequence"], "target": "membership"}))["result"]
    assert r["candidates"][0]["steps"] == ["linear_scan"] and {c["status"] for c in r["candidates"]} == {"candidate_only"}
    assert r["completion_state"] == "qualified-draft" and r["warning_card"]["code"] == "G6_E_ALGO_PROPOSAL"
    none = invoke_reply("algorithm_design", "search", json.dumps({"representations": ["weighted_graph"], "target": "shortest_paths"}))
    assert none["warning_card"]["code"] == "G6_E_ALGO_NO_CANDIDATE" and any("nonnegative_weights" in x for x in none["result"]["rejected"])

def test_fuse_binds_a_searched_candidate_to_the_frozen_intent_and_grants_nothing():
    found = invoke_reply("algorithm_design", "search", json.dumps(SEARCH))["result"]["candidates"][0]["id"]
    r = invoke_reply("algorithm_design", "fuse", json.dumps({**SEARCH, **BASE, "candidate_id": found, "changed": ["shop.state"]}))["result"]
    assert r["normative_obligations"] == ["o1"] and "o1" in r["check_plan"]
    assert r["approval"] is False and r["ready_for_unrestricted_action"] is False
    assert invoke_reply("algorithm_design", "fuse", json.dumps({**SEARCH, **BASE, "candidate_id": "nope"}))["warning_card"]["code"] == "G6_E_ALGO_UNKNOWN_CANDIDATE"

@pytest.mark.parametrize("op", ["ontology", "search", "fuse"])
def test_an_op_with_no_parameters_is_refused_with_its_own_code(op):
    assert invoke_reply("algorithm_design", op, "{}")["warning_card"]["code"] == "G6_E_ALGO_INPUT_INVALID"

def test_the_bundled_data_cites_sections_and_printed_pages_only():
    # counts unchanged (16 problems, 22 algorithms, 75 index entries); no "pdf_page" key anywhere, at any depth
```

- [ ] **Step 4: Run them to see them fail.** Run `T tests/complexity/test_complexity_block.py`, then `T tests/algorithm_design/test_algorithm_design_block.py`.
  Expected: FAIL with `G6_E_NO_BLOCK` (the packages declare no block).
- [ ] **Step 5: Implement.** Write `inputs.py`, `codes.py` and `block.py` for both packages, following
  `src/g6/workspace/align_prompt_library/block.py`: a `_OPS` description map, a `_RUN` dispatch map, and
  `@block_contract(...)` with `@component_maturity("beta", ...)`. `atlas`, `impact` and `simplify` call `build_atlas`,
  `impact_cone` and `propose_simplifications`. For `simplify`, first `dataclasses.replace` each annotated
  `StateVariable` with the caller's `essentiality`, `derivation` and `justification`. `fuse` runs `search_algorithms`,
  picks `candidate_id`, builds the atlas, takes the cone when `changed` is given, and calls `fuse`. Each `ValueError`
  from the IR becomes an `INPUT_INVALID` refusal.

  Declarations: `complexity` mitigates `("outside_scope", "unmanaged_complexity")`, introduces `("false_confidence",)`
  and requires `("design_governance",)`. `algorithm_design` mitigates `("reinvented_method", "unproven_optimum")`,
  introduces `("hidden_assumption",)` and requires `("complexity",)`.

  Docstring first lines:
  - "g6.complexity — maps a Python package's modules, state and effects from its source, without running it, and lists
    what a change can reach."
  - "g6.algorithm_design — suggests catalogued algorithms for a stated problem, estimates their cost, and runs a fixed set
    of algorithm kernels that check each answer."

  Each skill has the eight `g6.core.skills.SECTIONS` plus `## Ops`. The front matter is `name: g6-complexity` or
  `name: g6-algorithm-design`, with a description starting "Use when". Each op has a `g6-example` taken from the tests'
  parameters. The skill names every mitigated code under "Failure modes it catches" and every introduced code under
  "Blind spots", covers the four open problems, and stays within 1,800 words.
- [ ] **Step 6: Run the block tests.** Expected: PASS.
- [ ] **Step 7: Site and copy.** Add these `PLAIN` entries verbatim:
  - `"complexity": ("The change-impact map", "Reads a Python package's source without running it, maps its modules, shared state and side effects, and lists everything a change could reach and what it must keep", "a change that alters behaviour outside the part it was meant to touch")`
  - `"algorithm_design": ("The algorithm designer", "Suggests textbook algorithms that fit the problem you describe, estimates what each would cost within your limits, and runs a fixed set of standard algorithms on your data, checking each answer a second way", "reimplementing a method that already exists, or a plan called the best when nobody proved it")`

  Put `"algorithm_design"` in `TABLE`'s Reason column and `"complexity"` in its Know column. Add both FAQ core-table
  rows in registry order (`| \`algorithm_design\` | <PLAIN does> | MCP | none |`). Change the counts to "There are 36
  in the core harness" and "as can 16 of the 36 core components; the other 20 are Python-only", keeping
  `site/docs/faq.md` identical to `web/content/faq.md` modulo `test_docs_copy.LINKS`. Set "36 components" in
  `README.md`, `site/docs/index.md` and `site/docs/llms.txt`. Change `test_home_page.py:31` from 34 to 36, and
  regenerate the catalogue and the failure map.
- [ ] **Step 8: Gates.** Run G(algorithm_design, complexity). If `d6_find` ranks either component outside its limit for a
  code, add the code's title words to that package's docstring. Failing that, replace the code with one of:
  `wrong_optimiser` or `brittle_shortcut` for `algorithm_design`; `blind_repo_change` or `broke_existing_functionality`
  for `complexity`. Never add a waiver.
- [ ] **Step 9: Commit and push.** Run C with the subject `algorithm_design and complexity: components with typed blocks, reachable
  through g6_invoke (P1, PR #2)`, then P, and wait for CI.

### Task 2: Merge PR #3; one kernel path, cost estimates, honest names

**Files:**
- Merge: `6884ba5c33` (33 added files; those #2 also added are identical)
- Rename: `src/g6/algorithm_design/verified_kernels.py` to `checked_kernels.py` (`CertifiedResult` becomes
  `CheckedResult`); `tests/algorithm_design/test_verified_kernels.py` to `test_checked_kernels.py` (imports only);
  `docs/theory/VERIFIED_ALGORITHM_FUNCTIONALS.md` to `docs/theory/ALGORITHM_KERNELS.md`
- Modify: `src/g6/algorithm_design/invocation.py`, `kernels.py`, `block.py`, `codes.py`, `__init__.py`
  (`__g6_requires__`), `skill/SKILL.md`
- Modify: `docs/theory/AUTONOMOUS_ALGORITHM_SWE_AGENT.md` (drop "(experimental)" from the title, fix links),
  `docs/theory/ALGORITHM_DESIGN_KNOWLEDGE_BUNDLE.md` (links)
- Modify: `tests/algorithm_design/test_kernels.py` (calls go through `invoke`; assertions kept)
- Regenerate: the catalogue, `site/docs/components.md`
- Test: `tests/algorithm_design/test_algorithm_design_block.py` (added tests), `tests/algorithm_design/test_wording.py`

**Interfaces:**
- Consumes: Task 1's `AlgorithmDesignBlock`, `AlgorithmDesignInput` and `codes`.
- Produces: `invocation.invoke(algorithm_id: str, parameters: Mapping[str, Any]) -> checked_kernels.CheckedResult`, the
  one invoke path for all 22 kernels. The three in `kernels.py` (`interval_earliest_finish`, `subset_sum_dp`,
  `merge_sort`) are wrapped into `CheckedResult` with `complexity` set to "O(n log n)", "O(nW) pseudo-polynomial" and
  "O(n log n) comparisons". A parameter that is not plain data (None, bool, int, float, str, or lists and dicts of
  them, at most 8 deep) raises `ContractError("parameters must be declarative data")` before dispatch.
- Produces: `invocation.list_kernels() -> tuple[KernelDescriptor, ...]` (22, each with entrypoint
  `g6.algorithm_design.invocation.invoke`); `kernels.KernelUnavailable` becomes a subclass of
  `checked_kernels.ContractError`; `kernels.invoke_kernel` and `kernels.KERNELS` are removed.
- Produces: block ops `estimate` (fields `algorithms` (≤32), `dimensions` (≤8 keys, int 0..10**12), `device` with
  `DeviceBudget`'s fields), `list_kernels`, and `run_kernel` (fields `algorithm_id`, and `parameters` with at most 16
  keys). Codes: `ESTIMATE = "G6_E_ALGO_ESTIMATE"`, `KERNEL_CHECKED = "G6_E_ALGO_KERNEL_CHECKED"`,
  `KERNEL_REFUSED = "G6_E_ALGO_KERNEL_REFUSED"`. `search` gains typed primitives from
  `knowledge_bridge.catalog_plan(problem, problem_id=problem_id)` when `problem_id` is given.

- [ ] **Step 1: Merge #3.** Fetch, check the pin, run C as a merge with subject
  `integration: merge PR #3, autonomous algorithm design [skip ci]`, then P.
- [ ] **Step 2: Write the failing tests**

```python
# tests/algorithm_design/test_algorithm_design_block.py (added)
def test_list_kernels_names_all_22_through_one_entrypoint():
    r = invoke_reply("algorithm_design", "list_kernels", "{}")["result"]
    assert len(r["kernels"]) == 22 and {k["entrypoint"] for k in r["kernels"]} == {"g6.algorithm_design.invocation.invoke"}

@pytest.mark.parametrize("algorithm_id,parameters", KERNEL_EXAMPLES)   # 22 rows: test_checked_kernels' contract examples
def test_every_kernel_runs_over_mcp_and_says_how_its_answer_was_checked(algorithm_id, parameters):
    # with each example's arguments named by the kernel's signature, plus subset_sum_dp {"weights": [3, 7, 10],
    # "target": 13}, interval_earliest_finish {"intervals": [[0, 5], [0, 2], [2, 4], [4, 7]]} and merge_sort {"items": [3, 1, 2]}
    r = invoke_reply("algorithm_design", "run_kernel", json.dumps({"algorithm_id": algorithm_id, "parameters": parameters}))
    assert r["completion_state"] == "qualified-draft" and r["warning_card"]["code"] == "G6_E_ALGO_KERNEL_CHECKED"
    assert r["result"]["verification"] and r["result"]["algorithm_id"] == algorithm_id

@pytest.mark.parametrize("algorithm_id,parameters", [                  # Review Focus 5: one past each stated bound
    ("held_karp_tsp", {"distances": [[0] * 15 for _ in range(15)]}),
    ("subset_sum_mitm", {"weights": [1] * 33, "target": 3}),
    ("vertex_cover_fpt", {"n": 31, "edges": [], "k": 1}),
    ("no_such_kernel", {})])
def test_a_kernel_past_its_bound_is_refused_quickly(algorithm_id, parameters):
    started = time.monotonic()
    r = invoke_reply("algorithm_design", "run_kernel", json.dumps({"algorithm_id": algorithm_id, "parameters": parameters}))
    assert r["warning_card"]["code"] == "G6_E_ALGO_KERNEL_REFUSED" and time.monotonic() - started < 2

def test_more_than_16_parameter_keys_is_refused_at_the_door():
    params = {"algorithm_id": "bfs", "parameters": {f"k{i}": 0 for i in range(17)}}
    assert invoke_reply("algorithm_design", "run_kernel", json.dumps(params))["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"

def test_estimate_refuses_what_the_device_disallows_and_what_it_cannot_model():
    device = {"ram_bytes": 8_000_000, "scratch_bytes": 0, "max_operations": 1_000_000, "allow_pseudopolynomial": False}
    r = invoke_reply("algorithm_design", "estimate", json.dumps({"algorithms": ["knapsack_dp", "unknown_primitive"],
                                                                "dimensions": {"n": 100, "W": 1000}, "device": device}))["result"]
    by = {e["algorithm_id"]: e for e in r["estimates"]}
    assert not by["knapsack_dp"]["eligible"] and "pseudo_polynomial_disallowed" in by["knapsack_dp"]["reasons"]
    assert by["unknown_primitive"]["reasons"] == ["missing_hard_coded_cost_model"] and r["warning_card"]["code"] == "G6_E_ALGO_ESTIMATE"

def test_search_with_a_problem_id_adds_only_reviewed_typed_primitives():
    p = {"representations": ["intervals"], "target": "maximum_compatible_intervals",
         "established_properties": ["unit_reward", "intervals"], "problem_id": "interval_scheduling"}
    r = invoke_reply("algorithm_design", "search", json.dumps(p))["result"]
    assert any(c["steps"] == ["interval_earliest_finish"] for c in r["candidates"])

# tests/algorithm_design/test_wording.py
def test_no_file_claims_certified_or_verified_kernels():
    # no "certified host registry", "verified_kernels", "CertifiedResult" or "(experimental)" in src/g6/algorithm_design
    # or docs/theory/{ALGORITHM_KERNELS,AUTONOMOUS_ALGORITHM_SWE_AGENT,ALGORITHM_DESIGN_KNOWLEDGE_BUNDLE}.md, and no docs
    # file named VERIFIED_*
```

- [ ] **Step 3: Run them to see them fail.** Run `T tests/algorithm_design/test_algorithm_design_block.py`. Expected: FAIL (unknown
  ops; legacy kernels refused by `invoke`).
- [ ] **Step 4: Implement** the renames, the one `invoke` path, the three ops and the declarations: `algorithm_design`'s
  `__g6_requires__` becomes `("coding_agent_gate", "complexity", "harness", "workforce")`, or whatever
  `tests/test_component_dependencies.py` computes. Remove the message "certified host registry" in favour of
  "not one of G6's fixed kernels". Update the skill's Ops and add examples for the three ops.
- [ ] **Step 5: Run each test file.** Run `test_algorithm_design_block.py`, `test_wording.py`, `test_invocation.py`,
  `test_kernels.py`, `test_checked_kernels.py`, `test_knowledge_integration.py`, `test_autonomous.py` and
  `tests/complexity/test_cir_fusion.py`, one process each. Expected: PASS. `test_invocation.py` is unchanged and its
  `merge_sort` call with a lambda still raises `ContractError`.
- [ ] **Step 6: Gates.** Run G(algorithm_design, complexity).
- [ ] **Step 7: Commit and push.** Run C with the subject `algorithm_design: all 22 kernels through one invoke path, cost estimates, and names that claim
  nothing (P1, PR #3)`, then P, and wait for CI.

### Task 3: The experiment controller takes repo_tests' run records, not its own Receipt

**Files:**
- Modify: `src/g6/harness/verification_controller.py` (remove `Receipt`; add `RunObservation`, `from_run` and
  `receipt_matches`; `VerificationState.observations` becomes `tuple[RunObservation, ...]`)
- Modify: `src/g6/algorithm_design/autonomous.py` (`TestObservation` gains fields at its end; `run_agent` builds
  `RunObservation`)
- Modify: `tests/complexity/test_cir_fusion.py::test_verification_state_rejects_stale_and_unisolated_receipts` (moved to
  `RunObservation`, every assertion kept)
- Test: `tests/harness/test_verification_controller.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class RunObservation:
      experiment_id: str; artifact_digest: str; checker_digest: str; environment_digest: str
      outcome: Literal["pass", "fail", "timeout", "error", "skipped"]; backend: str | None; network: bool; receipt: str = ""
  def from_run(run: Mapping[str, Any], *, experiment_id: str, artifact_digest: str, checker_digest: str,
               environment_digest: str) -> RunObservation
  def receipt_matches(receipt: str, *, artifact_digest: str) -> bool
  def reduce_observation(state: VerificationState, experiment: Experiment, observed: RunObservation) -> VerificationState
  ```
  `from_run` reads `backend` and `network` from `run["isolation"]` and `receipt` from `run["receipt"]`, never from the
  caller. Its outcome maps "passed" to pass, "failed" to fail and "timed_out" to timeout; everything else is error.
  `receipt_matches` is
  `g6.workforce.receipt_store.check(receipt, artifact_digest=...)[0] == receipt_store.MATCHED`.
  `reduce_observation` refuses, as `f"{id}:{issue}"`, each of these issues: `template_not_allowed`,
  `budget_exhausted`, `stale_observation` (any digest differs), `isolation_unestablished` (no backend, or network
  on), `receipt_not_matched` (a receipt was given and does not match) and `unknown_outcome`. An observation with no
  receipt is kept and says so; this is a mutant's run, which `repo_tests` never receipts.
- Produces: `TestObservation` gains `receipt: str = ""`, `artifact_digest: str = ""` and `network: bool = False` at the
  end, so existing positional construction still works.

- [ ] **Step 1: Write the failing tests**

```python
# tests/harness/test_verification_controller.py
STATE = VerificationState("tree", "env", "checker", (Hypothesis("h", "regression", "high"),), 4)
E = Experiment("e", "t", ("h",), 1, allowed=True)

def _obs(**k): return RunObservation(**{"experiment_id": "e", "artifact_digest": "tree", "checker_digest": "checker",
    "environment_digest": "env", "outcome": "pass", "backend": "bubblewrap", "network": False, **k})

def test_stale_unconfined_and_networked_observations_are_refused():
    assert "stale_observation" in reduce_observation(STATE, E, _obs(artifact_digest="old")).refusals[0]
    assert "isolation_unestablished" in reduce_observation(STATE, E, _obs(backend=None)).refusals[0]
    assert "isolation_unestablished" in reduce_observation(STATE, E, _obs(network=True)).refusals[0]
    kept = reduce_observation(STATE, E, _obs(outcome="fail"))
    assert kept.observations and kept.hypotheses[0].state == "open" and next_experiment(kept, (E,)) is None

def test_a_receipt_counts_only_when_the_store_matches_it(monkeypatch):
    monkeypatch.setattr(receipt_store, "check", lambda r, *, artifact_digest: ("artifact_mismatch", None))
    assert "receipt_not_matched" in reduce_observation(STATE, E, _obs(receipt="abc")).refusals[0]
    monkeypatch.setattr(receipt_store, "check", lambda r, *, artifact_digest: (receipt_store.MATCHED, {}))
    assert reduce_observation(STATE, E, _obs(receipt="abc")).observations[0].receipt == "abc"

def test_from_run_reads_isolation_and_receipt_from_the_run_record_only():
    run = {"verdict": "passed", "isolation": {"backend": "appcontainer", "network": False}, "receipt": "r1"}
    o = from_run(run, experiment_id="e", artifact_digest="a", checker_digest="c", environment_digest="v")
    assert (o.backend, o.network, o.receipt, o.outcome) == ("appcontainer", False, "r1", "pass")
    assert from_run({"verdict": "changed_tracked_files", "isolation": {}}, experiment_id="e", artifact_digest="a",
                    checker_digest="c", environment_digest="v").outcome == "error"

def test_the_receipt_type_is_gone():
    assert not hasattr(verification_controller, "Receipt")
```

- [ ] **Step 2: Run it to see it fail.** Run `T tests/harness/test_verification_controller.py`. Expected: FAIL (`RunObservation`
  not defined).
- [ ] **Step 3: Implement** the controller changes. In `run_agent`, replace `Receipt(...)` with
  `RunObservation(experiment.id, patch.fingerprint, checker, environment, outcome, observation.backend if observation.isolated else None, observation.network, observation.receipt)`.
  Rewrite the fusion test's receipt case with `_obs`-style observations and the same three refusals and one acceptance.
- [ ] **Step 4: Run each test file.** Run `test_verification_controller.py`, `tests/complexity/test_cir_fusion.py` and
  `tests/algorithm_design/test_autonomous.py`. Expected: PASS.
- [ ] **Step 5: Gates and commit.** Run G(algorithm_design, complexity), then C with the subject `verification controller: observations
  come from repo_tests' run record and receipt store, never a caller's Receipt (P1)`, then P, and wait for CI.

### Task 4: The design mission's trust gaps: approval, isolation, receipts, a durable ledger, no engineer

**Files:**
- Modify: `src/g6/operator/queue.py` (internal run approvals)
- Modify: `src/g6/algorithm_design/autonomous.py`, `src/g6/algorithm_design/__init__.py` (`__g6_requires__` +=
  `"operator"`), `src/g6/algorithm_design/skill/SKILL.md` (a "Python API" note on the mission, as inert in P1)
- Modify: `tests/algorithm_design/test_autonomous.py`: `_mission()` takes an approval from a queue fixture instead of
  `approved=`, and the fake `Runner` gains `baseline`. Every assertion is kept.
- Test: `tests/operator/test_run_approval.py`, `tests/algorithm_design/test_mission_trust.py`,
  `tests/algorithm_design/test_mission_confined.py`

**Interfaces:**
- Produces, in `g6.operator.queue`: `RUN_APPROVAL = "run_approval"` (an internal type, never outward);
  `APPROVAL_REQUIRED = "G6_E_OPERATOR_APPROVAL_REQUIRED"`; `APPROVAL_MISMATCH = "G6_E_OPERATOR_APPROVAL_MISMATCH"`;
  `ActionQueue.request_run(venture_id: str, *, binding: str, summary: str, requested_by: str) -> dict[str, Any]` (a
  `proposed` item whose meta holds `binding`); and
  `ActionQueue.run_approver(venture_id: str, action_id: str, *, binding: str) -> str`. `run_approver` returns the name
  of the person whose decision approved exactly this binding, and otherwise raises `QueueError` whose message starts
  `[G6_E_OPERATOR_APPROVAL_REQUIRED]` or `[G6_E_OPERATOR_APPROVAL_MISMATCH]`. The existing `approve` rules apply
  unchanged: no drafter, no `auto:` reviewer.
- Produces, in `g6.algorithm_design.autonomous`:
  - `ApprovalRef(venture_id: str, action_id: str)`.
  - `DesignMission.approval: ApprovalRef | None = None` replaces `execution_approved`.
  - `DesignMission.fingerprint -> str`: the digest of the file map's digest, `package`, `source_root`, `target_path`,
    `tests`, the intent's fingerprint, the problem, `environment_digest`, `mode`, `max_attempts` and
    `timeout_per_run_s`. It never includes the repository's path.
  - `NoEngineer`, with `unavailable() -> "G6_E_ALGO_NO_ENGINEER"` and `propose(brief) -> None`.
  - `CandidateRunner.baseline(*, tests: tuple[str, ...], timeout_s: int) -> TestObservation`.
  - `RepoTestsRunner(mission, *, queue: ActionQueue)` checks `queue.run_approver` at construction and before every
    run. Every run passes `require_isolation=True`; the baseline runs in place with `receipt=True` and candidates run
    as `mutant=`. `isolated = bool(isolation.get("backend")) and not isolation.get("network")`, so any backend confine
    names counts. The process-wide `confine.required()` check is removed.
  - `AgentReport` gains `code: str = ""` (for every `blocked-escalated` report, `"G6_E_ALGO_" + reason.upper()`),
    `run_id: str = ""`, `approved_by: str = ""` and `receipts: tuple[str, ...] = ()`.
  - `run_agent(mission, producer, runner, *, queue: ActionQueue | None = None, ledger: ChainLedger | None = None) -> AgentReport`.
    The default ledger is `ChainLedger(state_path("algorithm_design", "missions.jsonl"))`, with its folder created.
    Every entry id is prefixed with `f"{run_id}:"`. A `LedgerError` from any append returns `blocked-escalated` with
    reason `ledger_conflict`.

  The checks run in this order, before any test runs: normative obligations, the choices, then
  `producer.unavailable()` (reason `no_engineer`), then the approval (reasons `execution_not_approved` and
  `approval_mismatch`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/operator/test_run_approval.py
def test_a_run_approval_is_a_persons_decision_bound_to_one_binding(queue, venture):
    item = queue.request_run(venture, binding="b1", summary="run tests for mission m", requested_by="g6:algorithm_design")
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_REQUIRED\]"): queue.run_approver(venture, item["action_id"], binding="b1")
    with pytest.raises(QueueError): queue.approve(venture, item["action_id"], reviewer="g6:algorithm_design")   # the requester
    with pytest.raises(QueueError): queue.approve(venture, item["action_id"], reviewer="auto:loop")
    queue.approve(venture, item["action_id"], reviewer="ana")
    assert queue.run_approver(venture, item["action_id"], binding="b1") == "ana"
    with pytest.raises(QueueError, match=r"^\[G6_E_OPERATOR_APPROVAL_MISMATCH\]"): queue.run_approver(venture, item["action_id"], binding="b2")
    with pytest.raises(QueueError): queue.mark_sent(venture, item["action_id"], reviewer="ana", channel="x")   # internal, never outward

# tests/algorithm_design/test_mission_trust.py
def test_no_engineer_refuses_before_any_test_runs():          # ProposalAgent with no real model: the inert refusal
    report = run_agent(approved_mission(), NoEngineer(), runner := Runner(["passed"]), queue=q, ledger=ChainLedger(tmp))
    assert (report.reason, report.code, runner.sources) == ("no_engineer", "G6_E_ALGO_NO_ENGINEER", [])

@pytest.mark.parametrize("approval", ["none", "proposed", "other_mission", "rejected"])   # Review Focus 2
def test_an_unapproved_or_misbound_mission_runs_nothing(approval):
    # reasons execution_not_approved (none, proposed, rejected) and approval_mismatch (other_mission); no brief, no run

def test_an_approved_mission_records_its_approver_in_a_durable_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("G6_HOME", str(tmp_path))
    first = run_agent(approved_mission(reviewer="ana"), Producer([...]), Runner([...]), queue=q)
    second = run_agent(approved_mission(reviewer="ana"), Producer([...]), Runner([...]), queue=q)
    led = ChainLedger(tmp_path / "algorithm_design" / "missions.jsonl")
    assert led.verify() == () and first.run_id != second.run_id and first.approved_by == "ana"
    assert {e["id"].split(":")[0] for e in led.entries()} == {first.run_id, second.run_id}

def test_a_second_writer_is_a_ledger_conflict_not_a_traceback(tmp_path):
    # a ChainLedger on the same file appends between run_agent's entries: reason ledger_conflict, code G6_E_ALGO_LEDGER_CONFLICT

@pytest.mark.parametrize("isolation,counted", [({"backend": "bubblewrap", "network": False}, True),     # Review Focus 3
    ({"backend": "appcontainer", "network": False}, True), ({"backend": None, "allowed_by": "G6_ALLOW_UNCONFINED"}, False),
    ({"backend": "bubblewrap", "network": True}, False)])
def test_isolation_is_read_from_confines_record(isolation, counted, git_repo, monkeypatch):
    calls = []
    monkeypatch.delenv("G6_REQUIRE_ISOLATION", raising=False)    # no longer consulted: the run asks for isolation itself
    monkeypatch.setattr(repo_tests, "run", lambda *a, **k: calls.append(k) or {"verdict": "passed", "completed": True,
        "isolation": isolation, "receipt": "", "source_before": "d", "python": "p", "interpreter_source": "g6"})
    obs = RepoTestsRunner(mission_on(git_repo), queue=q).run(SOURCE, expected_path=TARGET, tests=TESTS, timeout_s=10)
    assert obs.isolated is counted and calls and all(k["require_isolation"] is True for k in calls)

# tests/algorithm_design/test_mission_confined.py, run for real under bubblewrap (skips elsewhere, fails under D)
def test_a_confined_mission_receipts_its_baseline_and_not_its_mutants(bwrap_backend, tmp_path, monkeypatch):
    # tmp git repo app/logic.py and tests/test_logic.py; approved mission; a scripted producer that fixes the bug
    assert report.completion_state == "qualified-draft" and report.observations[0].backend == "bubblewrap"
    assert report.receipts == (report.observations[0].receipt,) and report.observations[0].receipt
    assert receipt_store.check(report.receipts[0], artifact_digest=report.observations[0].artifact_digest)[0] == receipt_store.MATCHED
    assert report.observations[-1].receipt == ""                 # a mutant is never the artifact under review
```

- [ ] **Step 2: Run them to see them fail.** Run each new file. Expected: FAIL (`request_run` and `NoEngineer` are not
  defined).
- [ ] **Step 3: Implement** the queue methods and the mission changes as the Interfaces say. In `run_agent`, a baseline
  with a receipt that `receipt_matches` rejects gives reason `baseline_receipt_not_matched`. `receipts` holds the
  baseline's receipt when it has one. The `approval` ledger entry records `action_id` and `reviewer`.
- [ ] **Step 4: Run each test file locally.** Run the three new files, `test_autonomous.py`, `test_cir_fusion.py` and
  `tests/operator/test_queue.py`. Expected: PASS; `test_mission_confined.py` SKIPS on Windows.
- [ ] **Step 5: Run in Docker.** Run `D(tests/algorithm_design/test_mission_confined.py)` and
  `D(tests/algorithm_design/test_mission_trust.py)`. Expected: PASS, with no skip.
- [ ] **Step 6: Gates and commit.** Run G(algorithm_design), then C with the subject `design mission: person-approved runs, isolation from
  confine's record, receipted baselines, a durable ledger, and an inert engineer (P1, PR #3)`, then P, and wait for
  CI.

### Task 5: The selector sits inside verification search, off by default

**Files:**
- Modify: `src/g6/harness/verification_controller.py` (`order_checks`), `src/g6/harness/verification_search.py`
  (`search(..., select: bool = False)`, one ordering line, a docstring note that Gate C, V11, must show it helps before
  any caller sets it)
- Test: `tests/harness/test_verification_controller.py` (added tests)

**Interfaces:**
- Consumes: Task 3's `next_experiment`, `Experiment` and `VerificationState`.
- Produces: `order_checks(checks: Sequence[Any], *, patch_only: Callable[[str], bool]) -> list[Any]`. Each check (with
  `.id`, `.requirement_id`, `.intent`) is an `Experiment` whose cost is 1 for a patch-only check and 2 otherwise. Each
  requirement is a `Hypothesis`, with severity "high" when any of its checks is `preserve` and "medium" otherwise. The
  order is repeated `next_experiment`, ties broken by id. `search` iterates `order_checks(...)` only when
  `select=True`. `patch_review` and `g6_review_patch` never pass `select`, and `search_config` gains no key, so every
  review reads exactly as before.

- [ ] **Step 1: Write the failing tests**

```python
def test_order_checks_puts_open_regressions_first_per_run():
    checks = [check("c1", "r1", "change"), check("c2", "r2", "preserve"), check("c3", "r3", "change")]
    assert [c.id for c in order_checks(checks, patch_only=lambda i: i == "c3")] == ["c2", "c3", "c1"]

def test_search_orders_only_when_asked(monkeypatch):
    seen = []
    monkeypatch.setattr(verification_controller, "order_checks", lambda checks, **k: seen.append(checks) or list(reversed(checks)))
    # search(..., select=False) runs the checks in the given order and never calls order_checks; select=True calls it once

def test_no_review_can_turn_the_selector_on():                # Gate C has not shown it helps
    assert "select" not in verification_search.search_config(None)
    with pytest.raises(ValueError): verification_search.search_config({"select": True})
    for f in ("src/g6/harness/patch_review.py", "src/g6/mcp/__init__.py"):
        assert "select=" not in (OSS / f).read_text(encoding="utf-8")
```

- [ ] **Step 2: Run it to see it fail.** Expected: FAIL (`order_checks` is not defined).
- [ ] **Step 3: Implement** `order_checks` and the `select` parameter.
- [ ] **Step 4: Run each test file.** Run `T tests/harness/test_verification_controller.py`,
  `T tests/harness/test_verification_templates.py` and `T tests/harness/test_proposed_checks.py`. Expected: PASS, with
  the default order unchanged (`test_verification_templates.py:750`).
- [ ] **Step 5: Run in Docker.** Run `D(tests/harness/test_proposed_checks.py)` and
  `D(tests/harness/test_verification_templates.py)`. Expected: PASS.
- [ ] **Step 6: Commit.** Run C with the subject `verification search: the experiment selector, off by default behind Gate C
  (P1, PR #2)`, then P, and wait for CI.

### Task 6: Merge PR #4; one prompt catalogue, and #4's engines in the meta-harness

**Files:**
- Merge: `87e8586e03` (adds `src/g6/cognitive_bootstrap/` and `src/g6/heuristic_discovery/`; #3's files it carries are
  older ancestors already merged)
- Create: `src/g6/workspace/align_prompt_library/catalogue.py`, `src/g6/workspace/align_prompt_library/cognitive_prompts.py`
- Modify: `src/g6/workspace/align_prompt_library/library.py` (extract `fill`; `Library.render` calls it with identical
  messages), `ops.py` (`catalogue`, `install_catalog`), `block.py` (two ops; `SENSITIVE_OPS` +=
  `"install_catalog"`; fields `mode`, `step`, `prefix`), `skill/SKILL.md`, `src/g6/workspace/_envelope.py` (codes)
- Create: `src/g6/metaharness/heuristics.py`, `src/g6/metaharness/bootstrap.py`
- Modify: `src/g6/metaharness/__init__.py` (`__g6_requires__` += `"align_prompt_library"`)
- Delete: `src/g6/cognitive_bootstrap/`, `src/g6/heuristic_discovery/`. Their content is relocated: `mutate` into
  `HeuristicStrategy`, the prompts into `cognitive_prompts.py`, and `bootstrap` into `metaharness.bootstrap`.
  `assess`, `promote` and `propose_prompt_revision` give way to the meta-harness's evaluate, decide, approve and
  `ModelStrategy`.
- Modify: `tests/workspace/test_align_prompt_library.py` (`OPS` += `"catalogue"`, `"install_catalog"`; `SENSITIVE` +=
  `"install_catalog"`)
- Modify: `web/content/faq.md` and `site/docs/faq.md` (the `align_prompt_library` row refuses `` `install_catalog` ``
  too)
- Test: `tests/workspace/test_prompt_catalogue.py`, `tests/metaharness/test_heuristics.py`,
  `tests/metaharness/test_bootstrap.py`

**Interfaces:**
- Produces, in `library.py`: `fill(content: str, values: Mapping[str, str] | None, *, label: str) -> tuple[str, list[str]]`,
  which returns the text and its placeholders and raises `WorkspaceRefused` with `LIBRARY_MISSING_VALUE`,
  `LIBRARY_EXTRA_VALUE`, `LIBRARY_INPUT_INVALID` or `RESOURCE_LIMIT`.
- Produces, in `catalogue.py`:
  ```python
  MODES = ("premortem", "mission", "venture"); STEPS = ("propose", "test", "gate", "approve")
  @dataclass(frozen=True)
  class CatalogueEntry: name: str; stage: str; mode: str; step: str; purpose: str; source: str; template: str  # .digest property
  ENTRIES: tuple[CatalogueEntry, ...]
  def entries(*, mode: str = "", step: str = "") -> tuple[CatalogueEntry, ...]
  def entry(name: str) -> CatalogueEntry                      # WorkspaceRefused(LIBRARY_NOT_FOUND)
  def render(name: str, values: Mapping[str, str]) -> dict    # name, template_digest, rendered_digest, text; through fill
  def install(*, prefix: str = "", path=None) -> dict         # per-entry stored / unchanged / refused
  ```
  The `ops` wrappers are `catalogue(*, mode="", step="", request_id="", run_id="")` and
  `install_catalog(prefix="", *, request_id="", run_id="", path=None)`. The codes are
  `LIBRARY_CATALOGUE = "G6_E_LIBRARY_CATALOGUE"`, `LIBRARY_NAME_TAKEN = "G6_E_LIBRARY_NAME_TAKEN"` and
  `LIBRARY_INSTALL_PARTIAL = "G6_E_LIBRARY_INSTALL_PARTIAL"`. Install stores an entry only where the name is free or
  its latest version was itself installed from the catalogue (`declared.status == "candidate_template"`); a user's own
  prompt under that name is refused with `NAME_TAKEN`, never overwritten. The declared metadata is `stage`, `mode`,
  `step`, `purpose`, `source`, `status: "candidate_template"` and `catalog_digest`.
- Produces `cognitive_prompts.ENTRIES`: #4's 14 prompts with `$x` turned into `{{x}}`, named `cb-` plus the stage key
  with hyphens, `stage` set to the key and `source` set to "cognitive bootstrap pull request #4". Their modes and
  steps:

  | step | premortem | mission |
  |---|---|---|
  | propose | cb-constraints, cb-first-principles, cb-heuristics, cb-self-assessment, cb-decompose, cb-theory-of-mind | cb-execute, cb-synthesize, cb-revise, cb-prompt-reflection |
  | test | cb-search-query | cb-critique, cb-evaluate |
  | approve | | cb-user-facing |

  No entry has step `gate`: a model's output never decides a gate.
- Produces, in `g6.metaharness.heuristics`: `OPERATORS` (PR #4's six, in its order) and `OPERATOR_TEXT`, verbatim:
  - `"regression"`: "Before you answer, check that your answer keeps every behaviour the task says must not change."
  - `"boundary"`: "Before you answer, try the smallest, the largest and the empty inputs the task allows."
  - `"counterexample"`: "Before you answer, look for one input that would make your answer wrong; if you find one, fix
    the answer."
  - `"metamorphic"`: "Before you answer, check that an input the task relates to this one gives a consistent answer."
  - `"decompose"`: "Split the task into steps and check each step's result before the next."

  `prompt_revision` has no text: proposing prompt revisions is `ModelStrategy`'s job. `HeuristicStrategy` is a
  `@dataclass(max_children: int = 4, max_generation: int = 3, name: str = "heuristics")` implementing
  `propose(view: ProposerView, runner: DevRunner) -> list[dict]`. Each candidate is the base profile with one unused
  operator's text appended to `prompt`, in `OPERATORS` order, at most `max_children` of them. It proposes nothing once
  the base already holds `max_generation` operator texts, or when a prompt would pass 8,000 characters.
- Produces, in `g6.metaharness.bootstrap`: `BootstrapResult` and
  `bootstrap(objective, *, thinker: Callable[[str, str], str], context="", frozen_constraints=(), max_subgoals=6, max_revisions=2)`.
  These keep PR #4's stages, bounds and parsing, but each prompt is `catalogue.render(f"cb-{stage}", values)` with only
  the template's own placeholders filled.

- [ ] **Step 1: Merge #4.** Fetch, check the pin, run C as a merge with subject
  `integration: merge PR #4, cognitive bootstrap and heuristic discovery [skip ci]`, then P.
- [ ] **Step 2: Write the failing tests**

```python
# tests/workspace/test_prompt_catalogue.py
def test_every_entry_is_tied_to_a_mode_and_step_and_none_decides_a_gate():
    assert len(catalogue.ENTRIES) == 14 and len({e.name for e in catalogue.ENTRIES}) == 14
    for e in catalogue.ENTRIES:
        assert e.mode in catalogue.MODES and e.step in catalogue.STEPS and e.step != "gate"
        assert "$" not in e.template and e.digest == digest_of(e.template)

def test_render_is_the_librarys_strict_one_pass_fill(monkeypatch):
    with pytest.raises(WorkspaceRefused) as missing: catalogue.render("cb-critique", {"candidate": "x"})
    assert missing.value.code == E.LIBRARY_MISSING_VALUE
    # an extra value gives LIBRARY_EXTRA_VALUE; a value holding "{{constraints}}" stays literal;
    # Library.render and catalogue.render both call library.fill (recorded through monkeypatch)

def test_install_is_sensitive_over_mcp_and_writes_nothing_when_refused(home):
    reply = invoke_reply("align_prompt_library", "install_catalog", "{}")
    assert reply["warning_card"]["code"] == "G6_E_SENSITIVE_COMPONENT_DENIED" and not Library().path.exists()

def test_install_when_allowed_stores_each_entry_once_and_never_takes_a_users_name(home):
    Library().put("prompt", "cb-critique", "my own critique prompt")
    allow = frozenset({"align_prompt_library.install_catalog"})
    first = invoke_reply("align_prompt_library", "install_catalog", "{}", allow_sensitive=allow)["result"]
    assert first["warning_card"]["code"] == "G6_E_LIBRARY_INSTALL_PARTIAL"
    assert [r["name"] for r in first["refused"]] == ["cb-critique"] and first["refused"][0]["code"] == "G6_E_LIBRARY_NAME_TAKEN"
    assert Library().get("cb-critique")["content"] == "my own critique prompt"
    again = invoke_reply("align_prompt_library", "install_catalog", "{}", allow_sensitive=allow)["result"]
    assert all(r["unchanged"] for r in again["stored"])

# tests/metaharness/test_heuristics.py
def test_each_candidate_adds_one_operator_text_in_order():  # base prompt "": four candidates, regression..metamorphic
def test_no_candidate_once_the_base_holds_max_generation_texts():
def test_its_candidates_are_meta_harness_proposals(tmp_path):   # tests/metaharness/_rig.py; adapt(...); confirm("Ada")
    proposal = mh.propose(HeuristicStrategy())["proposals"][0]
    assert (proposal.strategy, proposal.proposer) == ("heuristics", "auto:heuristics")
def test_the_module_keeps_no_gate_of_its_own():            # AST: no def assess or promote, no parameter named approved

# tests/metaharness/test_bootstrap.py
def test_bootstrap_walks_the_stages_in_order_with_strict_prompts():
    # thinker answers constraints '["c1", "c2"]', decompose '["s1", "s2"]', else "text"; max_revisions=2
    assert result.calls == 12 and result.constraints == ("frozen", "c1", "c2") and result.subgoals == ("s1", "s2")
    assert [s for s, _ in seen] == ["constraints", "first_principles", "heuristics", "self_assessment", "decompose",
        "execute", "execute", "synthesize", "critique", "revise", "critique", "revise"]
    assert all("{{" not in p for _, p in seen)
def test_bootstrap_bounds_are_kept():                       # max_subgoals 13 or objective "" raise ValueError; a non-str reply TypeError
```

- [ ] **Step 3: Run them to see them fail.** Run each new file. Expected: FAIL (module not found).
- [ ] **Step 4: Implement**, then delete the two folded packages. The block's `help` lists the new ops. The skill adds
  both to Ops, gives `catalogue` an example and calls `install_catalog` sensitive, and stays within 1,800 words.
- [ ] **Step 5: Run each test file.** Run the three new files, `tests/workspace/test_align_prompt_library.py`,
  `tests/workspace/test_review_regressions.py` and `tests/metaharness/test_phase2_proposers.py`. Expected: PASS.
- [ ] **Step 6: Gates.** Run G(align_prompt_library, metaharness). Confirm that
  `"cognitive_bootstrap" not in get_registry().names()` and `"heuristic_discovery" not in get_registry().names()`.
- [ ] **Step 7: Commit and push.** Run C with the subject `prompts: #4's prompts join one catalogue, installed only through the sensitive
  gate; its engines move into the meta-harness (P1, PR #4)`, then P, and wait for CI.

### Task 7: Merge PR #6; its 13 prompts join the same catalogue

**Files:**
- Merge: `f2aba33fc3` (adds `metacognition.py`, a doc and a test)
- Modify: `src/g6/workspace/align_prompt_library/metacognition.py` (`CATALOG` becomes `ENTRIES` of `CatalogueEntry`;
  remove `PromptDefinition`, `BY_NAME`, `get_prompt`, `render_prompt` and `install_catalog`, the duplicates of
  `catalogue`), `catalogue.py` (`ENTRIES = cognitive_prompts.ENTRIES + metacognition.ENTRIES`)
- Move and adapt: `tests/test_metacognition_prompt_catalog.py` becomes
  `tests/workspace/test_metacognition_prompts.py`, with an SPDX header. Its three tests keep their assertions against
  `catalogue.entry`, `catalogue.render` (expecting `WorkspaceRefused` with the missing or extra value code) and
  `catalogue.install(path=tmp_path / "catalog.jsonl")`.
- Modify: `tests/workspace/test_prompt_catalogue.py` (14 becomes 27), `docs/metacognition_prompt_catalog.md` (install
  is the sensitive op `install_catalog`)

**Interfaces:**
- Consumes: Task 6's `CatalogueEntry`, `ENTRIES`, `render`, `install` and `fill`.
- Produces: #6's modes and steps, with each entry's original stage kept in `stage`:

  | step | premortem | mission |
  |---|---|---|
  | propose | mc-constraints, mc-first-principles, mc-heuristics, mc-self-assess, mc-satisficing, mc-goal-decompose | mc-result-synthesis, pe-generate-system-prompt, pe-revise-system-prompt |
  | test | mc-research-needed | ct-critical-review, ct-independent-test-plan, mc-uncertainty-update |

- [ ] **Step 1: Merge #6.** Fetch, check the pin, run C as a merge with subject
  `integration: merge PR #6, metacognition prompts [skip ci]`, then P.
- [ ] **Step 2: Write the failing test.** In `test_prompt_catalogue.py`, assert 27 entries with unique names and both
  sources present. Add `test_metacognition_keeps_no_render_or_install_of_its_own`: none of `render_prompt`,
  `install_catalog` or `get_prompt` is an attribute of `metacognition`.
- [ ] **Step 3: Run it to see it fail.** Expected: FAIL (there are 14 entries).
- [ ] **Step 4: Implement** the conversion.
- [ ] **Step 5: Run each test file, then the gates.** Run `test_prompt_catalogue.py`, `test_metacognition_prompts.py`
  and `test_align_prompt_library.py`, then G(align_prompt_library). Expected: PASS.
- [ ] **Step 6: Commit and push.** Run C with the subject `prompts: #6's metacognition prompts join the one catalogue, each tied to its
  stage (P1, PR #6)`, then P, and wait for CI.

### Task 8: Merge PR #5; `goal_engine` gets a block for bounded goal-tree proposals

**Files:**
- Merge: `4f24a57a38` (adds `goal_engine/decomposition.py`, `goal_engine/goal_tree.py`, a doc and a test)
- Delete: `src/g6/goal_engine/goal_tree.py`, a re-export of `decomposition` and so a duplicate
- Create: `src/g6/goal_engine/block.py`
- Modify: `src/g6/goal_engine/codes.py`, `__init__.py`, `skill/SKILL.md`, `docs/goal_tree_proposals.md`
- Modify: `tests/goal_engine/test_goal_engine_findings.py:472` (`_SHIPPED` += `"block.py"`, `"decomposition.py"`)
- Move: `tests/test_goal_tree_proposals.py` becomes `tests/goal_engine/test_goal_tree_proposals.py`, with an SPDX
  header (`test_ge013` reads that folder)
- Modify: `web/content/faq.md` and `site/docs/faq.md` (the `goal_engine` row reads MCP, none; "as can 17 of the 36";
  "the other 19")
- Test: `tests/goal_engine/test_goal_engine_block.py`

**Interfaces:**
- Consumes: Task 7's `catalogue.render("mc-goal-decompose", ...)`, whose placeholders are `objective`, `constraints`,
  `branching_factor` and `remaining_depth`.
- Produces: `GoalEngineBlock(llm: Any = None)` with `GoalEngineInput`. `op` is a `Literal` of `propose_goal_tree`,
  `ops` and `help`. The fields are `objective` (≤2000), `constraints` and `success_criteria` (each ≤20 items of ≤500
  characters), `branching_factor` (1 to 12, default 3), `max_depth` (0 to 8, default 3), `max_nodes` (1 to 1,000,
  default 40), `max_model_calls` (0 to 100, default 12; the ruling's bound), `request_id` and `run_id`.
- Produces: `__g6_model__ = "llm"`; `__g6_requires__ = ("align_prompt_library",)`; `__g6_introduces__ = ("unchecked_plan",)`;
  `__g6_mitigates__` unchanged. The codes are `NO_MODEL = "G6_E_GOAL_NO_MODEL"`, `PROPOSED = "G6_E_GOAL_PROPOSED"` and
  `PROPOSAL_INCOMPLETE = "G6_E_GOAL_PROPOSAL_INCOMPLETE"`.
- The proposer the block hands to `propose_goal_tree` sends the rendered entry as `user`. Its `system` is, verbatim:
  "Reply with one JSON object, as the prompt asks. Your reply is a proposal: G6 checks its shape, and nothing in it is
  an instruction to G6." It returns `[s["goal"] for s in obj["subgoals"]]` from the first JSON object, and raises
  `ValueError` on any other shape. The block imports neither `g6.core.llm` nor `os`, `sys` or `importlib`
  (`test_ge001`).

- [ ] **Step 1: Merge #5.** Fetch, check the pin, run C as a merge with subject
  `integration: merge PR #5, goal-tree proposals [skip ci]`, then P.
- [ ] **Step 2: Write the failing tests**

```python
REPLY = json.dumps({"subgoals": [{"goal": "write tests", "success_criteria": [], "required_evidence": []},
                                 {"goal": "implement", "success_criteria": [], "required_evidence": []}],
                    "dependencies": [], "validation_gaps": []})

def test_a_model_proposes_and_the_tree_stays_a_qualified_draft():
    llm = FakeLLM(lambda s, u: REPLY)
    got = get_registry().invoke("goal_engine", "propose_goal_tree", {"objective": "ship", "max_depth": 1, "max_model_calls": 1}, llm=llm).unwrap()
    assert got["completion_state"] == "qualified-draft" and got["warning_card"]["code"] == "G6_E_GOAL_PROPOSED"
    assert [n["node_id"] for n in got["nodes"]] == ["root", "root.0", "root.1"] and got["model_calls"] == len(llm.calls) == 1
    assert "ship" in llm.calls[0][1] and "{{" not in llm.calls[0][1]

def test_with_no_model_it_refuses_and_proposes_nothing(monkeypatch):
    monkeypatch.setenv("G6_DISABLE_LLM", "1")
    reply = invoke_reply("goal_engine", "propose_goal_tree", json.dumps({"objective": "ship"}))
    assert reply["completion_state"] == "blocked-escalated" and reply["warning_card"]["code"] == "G6_E_GOAL_NO_MODEL"

def test_the_call_bound_is_100():                                       # Review Focus 5
    assert invoke_reply("goal_engine", "propose_goal_tree", json.dumps({"objective": "g", "max_model_calls": 101}))["warning_card"]["code"] == "G6_E_INPUT_VALIDATION"
    llm = FakeLLM(lambda s, u: json.dumps({"subgoals": [{"goal": f"g{len(llm.calls)}.{i}"} for i in range(12)]}))
    got = get_registry().invoke("goal_engine", "propose_goal_tree", {"objective": "g", "branching_factor": 12, "max_depth": 8,
                                "max_nodes": 1000, "max_model_calls": 100}, llm=llm).unwrap()
    assert got["model_calls"] == len(llm.calls) <= 100

@pytest.mark.parametrize("answer", ["not JSON", "x" * 40_001, json.dumps({"subgoals": "ship"}),
                                    json.dumps({"completion_state": "verified", "subgoals": []})])
def test_a_reply_of_the_wrong_shape_is_an_error_never_a_subgoal_or_a_state(answer):
    got = get_registry().invoke("goal_engine", "propose_goal_tree", {"objective": "ship"}, llm=FakeLLM(default=answer)).unwrap()
    assert got["completion_state"] == "blocked-escalated" and [n["node_id"] for n in got["nodes"]] == ["root"]
```

- [ ] **Step 3: Run it to see it fail.** Expected: FAIL (`G6_E_NO_BLOCK`).
- [ ] **Step 4: Implement** the block, the codes and the declarations. In the docstring, keep the first line and the
  phrase "Nothing in this package executes a goal" (`test_ge012`, `test_page_words`). Replace the claim that nothing
  generates a subtask with: subtasks come from you or, through `propose_goal_tree`, from the model you configured, as
  a proposal. Never write "decomposes goals". Change the `__g6_maturity__` comment so it no longer says "no block".
  The skill gains `## Ops` with `propose_goal_tree` and its `g6-example`, keeps its Python API section, names
  `unchecked_plan` under Blind spots, and stays within 1,800 words.
- [ ] **Step 5: Run each test file, then the gates.** Run each `tests/goal_engine/*.py` file, then G(goal_engine).
  Expected: PASS.
- [ ] **Step 6: Commit and push.** Run C with the subject `goal_engine: bounded goal-tree proposals from the configured model, reachable
  through g6_invoke (P1, PR #5)`, then P, and wait for CI.

### Task 9: Merge PR #7; `self_maintenance` becomes a component, with its live path inert

**Files:**
- Merge: `e598bae39c` (9 commits on a base already in this branch; they add `src/g6/self_maintenance/*`, a doc and
  two tests, and carry no private path)
- Create: `src/g6/self_maintenance/codes.py`, `src/g6/self_maintenance/skill/SKILL.md`
- Modify: `src/g6/self_maintenance/__init__.py` (docstring, declarations, `__g6_maturity__` removed), `block.py`,
  `controller.py`, `constraints.py` (drop `ConstraintAssessment.verified`, which is always False)
- Modify: `docs/theory/G6_SELF_MAINTENANCE_CONTROL_PLANE.md` (title without "(experimental)")
- Modify: `tests/self_maintenance/test_experience.py:16` (`consented_sessions` becomes `consenting_sessions`, the
  field's real name) and `tests/self_maintenance/test_kernel.py` (`operator_enabled=False` becomes no approval;
  assertions kept)
- Modify: `pyproject.toml` (the layer contract gains `"g6.self_maintenance"`); `web/pages/catalogue.py` (`PLAIN` and
  `TABLE`'s Learn column); both FAQ copies (a row, plus "There are 37", "18 of the 37", "the other 19"); "37
  components" in `README.md`, `site/docs/index.md` and `site/docs/llms.txt`; `tests/site/test_home_page.py:31` (36 to
  37)
- Test: `tests/self_maintenance/test_self_maintenance_block.py`, `tests/self_maintenance/test_controller_approval.py`

**Interfaces:**
- Consumes: Task 4's `ActionQueue.request_run` and `run_approver`.
- Produces, in `codes.py`: `INPUT = "G6_E_SELF_MAINTENANCE_INPUT"`, `CONSTRAINT = "G6_E_SELF_MAINTENANCE_CONSTRAINT"`,
  `PLAN = "G6_E_SELF_MAINTENANCE_PLAN"` and `LIVE_AGENT_OFF = "G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF"`;
  `SelfMaintenanceRefused(code: str, reason: str)`.
- Produces: the block keeps ops `identify_constraint` and `plan`, adds `request_id` and `run_id`, and answers `ops`
  and `help` separately with envelope replies.
- Produces: `run_one_cycle(..., queue: ActionQueue | None = None, venture_id: str = "", approval: str = "")` replaces
  `operator_enabled`. Its binding is `digest({"ticket": asdict(ticket), "provider": plan.provider, "day": day})`. With
  no approval the reason is `simulation_only`; a misbound approval gives `approval_mismatch`; the report gains
  `approved_by`.
- Produces: `run_existing_agent(...)` raises `SelfMaintenanceRefused(LIVE_AGENT_OFF, ...)` before any import of
  `g6.agents`. PR #7's body is kept, unreachable in P1, as `_run_existing_agent_live(...)` for P3.
  `run_one_cycle` turns that refusal into `blocked-escalated` with reason `live_agent_off`.
- Produces the declarations: mitigates `("low_value_first", "unchecked_self_change")`; introduces `("wrong_problem",)`;
  `__g6_requires__` exactly what the dependency test computes. That is expected to be `agents`, `agent_claude`,
  `agent_codex`, `action_gating`, `safety_engine` and `operator`, with `workforce` dropped because it is never
  imported. The summary: "g6.self_maintenance — names the constraint of a measured workflow and plans the next
  maintenance ticket within a token budget, running nothing." `BudgetBook` and `BudgetPolicy` are unchanged until P2
  merges them into the operator's ledger.
- Produces this `PLAIN` entry, verbatim:
  `"self_maintenance": ("The maintenance planner", "Finds the stage of a measured workflow that limits it most and picks the next fix that raises it most within a token budget, without running anything", "effort spent on low-value tasks first")`

- [ ] **Step 1: Merge #7.** Fetch, check the pin, run C as a merge with subject
  `integration: merge PR #7, self-maintenance control plane [skip ci]`, then P.
- [ ] **Step 2: Write the failing tests**

```python
# tests/self_maintenance/test_self_maintenance_block.py
STAGES = [{"name": "review", "attempted": 20, "succeeded": 10, "capacity_per_day": 5, "independently_observed": True},
          {"name": "coding", "attempted": 20, "succeeded": 19, "capacity_per_day": 20, "independently_observed": True}]

def test_a_callers_measurements_name_a_constraint_but_stay_the_callers_word():          # Review Focus 4
    r = invoke_reply("self_maintenance", "identify_constraint", json.dumps({"stages": STAGES}))
    assert r["completion_state"] == "qualified-draft" and r["warning_card"]["code"] == "G6_E_SELF_MAINTENANCE_CONSTRAINT"
    assert r["result"]["constraint"] == "review" and r["result"]["evidence"][0]["declared_by"] == "caller"

@pytest.mark.parametrize("op", ["identify_constraint", "plan"])
def test_no_parameters_is_refused_with_its_own_code(op):
    assert invoke_reply("self_maintenance", op, "{}")["warning_card"]["code"] == "G6_E_SELF_MAINTENANCE_INPUT"

def test_it_is_a_beta_block_that_never_says_experimental():
    info = discovery.describe("self_maintenance")
    assert (info["block"], info["maturity"], info["sensitive_ops"]) == ("SelfMaintenanceBlock", "beta", [])
    assert "experimental" not in (g6.self_maintenance.__doc__ + maturity_reason(SelfMaintenanceBlock)).lower()

# tests/self_maintenance/test_controller_approval.py
def test_the_live_agent_refuses_before_any_runtime_exists(monkeypatch):                  # Review Focus 1
    built = []
    monkeypatch.setattr("g6.agents.agent_codex.CodexRuntime", lambda *a, **k: built.append("codex"))
    monkeypatch.setattr("g6.agents.agent_claude.ClaudeCodeRuntime", lambda *a, **k: built.append("claude"))
    for provider in ("codex", "claude"):
        with pytest.raises(SelfMaintenanceRefused) as refused:
            run_existing_agent(provider=provider, workspace="w", goal="g", allowed_paths=(), deadline_s=1, dry_run=False)
        assert refused.value.code == "G6_E_SELF_MAINTENANCE_LIVE_AGENT_OFF"
    assert built == []

def test_an_approved_cycle_with_the_live_runner_is_refused_and_settles_its_reserve(queue, venture):
    report = run_one_cycle(..., agent_runner=run_existing_agent, queue=queue, venture_id=venture, approval=approved_for(ticket))
    assert (report.state, report.reason) == ("blocked-escalated", "live_agent_off") and book.usage(DAY)["unsettled"] == []

@pytest.mark.parametrize("approval", ["none", "proposed", "other_ticket"])               # Review Focus 2
def test_no_runner_or_checker_runs_without_a_matching_approval(approval):
    # reasons simulation_only (none, proposed) and approval_mismatch (other_ticket); calls == []

def test_an_approved_cycle_records_its_approver():   # fake runner and checker with receipts: candidate_checked_in_scope, approved_by "ana"
```

- [ ] **Step 3: Run them to see them fail.** Run each new file, then `T tests/self_maintenance/test_experience.py`.
  Expected: FAIL, because the declarations and approval are missing and the uncorrected test still reads
  `consented_sessions`.
- [ ] **Step 4: Implement**, including the skill. Under "Blind spots", the skill names `wrong_problem` and says that a
  caller's stage measurements are the caller's word.
- [ ] **Step 5: Run each test file, then the gates.** Run each `tests/self_maintenance/*.py` file and
  `tests/operator/test_run_approval.py`, then G(self_maintenance). Expected: PASS.
- [ ] **Step 6: Commit and push.** Run C with the subject `self_maintenance: a component with typed planning ops, person-approved cycles
  and an inert live agent (P1, PR #7)`, then P, and wait for CI.

### Task 10: Gate 5, the installed-MCP smoke test, and the P1 exit

**Files:**
- Create: `tools/mcp_smoke.py`, `tests/tools/test_mcp_smoke.py`
- Modify: `.github/workflows/ci.yml` (a job `mcp-smoke`)

**Interfaces:**
- Consumes: every block above.
- Produces, in `tools/mcp_smoke.py`:
  - `EXPECTED_OPS`:
    `{"algorithm_design": ["primitives", "ontology", "search", "estimate", "fuse", "list_kernels", "run_kernel"], "complexity": ["atlas", "impact", "simplify"], "goal_engine": ["propose_goal_tree"], "self_maintenance": ["identify_constraint", "plan"], "align_prompt_library": ["put", "get", "history", "search", "render", "retire", "rollback", "catalogue", "install_catalog"]}`
  - `check(call: Callable[[str, str, str], dict]) -> list[str]`, which returns the problems found. It describes each
    component and requires a block, maturity "beta" and exactly `EXPECTED_OPS`. It runs every `g6-example` of each
    component's skill: the reply must carry one of the three states, and its code must not be `G6_E_NO_BLOCK`. A
    sensitive op must be refused with `G6_E_SENSITIVE_COMPONENT_DENIED`.
  - `main(argv)`. `--server <command...>` starts that MCP server over stdio through `fastmcp.Client` with
    `StdioTransport`, setting `G6_DISABLE_LLM=1` and a temporary `G6_HOME`. With no `--server`, it starts the
    `g6-mcp` script found next to `sys.executable`. It exits 1 when `check` finds a problem.

- [ ] **Step 1: Write the failing test**

```python
def test_every_new_operation_is_its_components_own():
    assert {n: sorted(discovery.ops(n)) for n in mcp_smoke.EXPECTED_OPS} == {n: sorted(o) for n, o in mcp_smoke.EXPECTED_OPS.items()}

def test_every_new_operation_answers_through_g6_invoke_in_process(monkeypatch, tmp_path):     # the P1 exit
    monkeypatch.setenv("G6_HOME", str(tmp_path)); monkeypatch.setenv("G6_DISABLE_LLM", "1")
    assert mcp_smoke.check(lambda c, o, p: invoke_reply(c, o, p)) == []

def test_the_same_answers_come_over_a_real_stdio_server(tmp_path):
    done = subprocess.run([sys.executable, str(TOOLS / "mcp_smoke.py"), "--server", sys.executable, "-c",
                           "from g6.mcp import main; main()"], capture_output=True, text=True, timeout=300, cwd=tmp_path)
    assert done.returncode == 0, done.stdout + done.stderr
```

- [ ] **Step 2: Run it to see it fail.** Expected: FAIL (the tool does not exist).
- [ ] **Step 3: Implement** the tool. Add the CI job: `mcp-smoke`, with matrix `[ubuntu-latest, windows-latest]` and
  Python 3.12. It runs `python -m pip install ".[mcp]"` (a real wheel install, not editable), then
  `python "$GITHUB_WORKSPACE/tools/mcp_smoke.py"` with `working-directory: ${{ runner.temp }}`, so `import g6` comes
  from site-packages. It installs nothing else, holds no key and makes no model call.
- [ ] **Step 4: Run the test.** Run `T tests/tools/test_mcp_smoke.py`. Expected: PASS. Then run the tool against a wheel installed into a fresh
  environment: `python -m venv "$env:TEMP\g6-smoke"`;
  `& "$env:TEMP\g6-smoke\Scripts\python.exe" -m pip install "$W[mcp]"`; from `$env:TEMP`, run
  `& "$env:TEMP\g6-smoke\Scripts\python.exe" "$W\tools\mcp_smoke.py"`. Expected: exit 0. Afterwards remove
  `"$env:TEMP\g6-smoke"`.
- [ ] **Step 5: Run the final local checks**, one process each. Run `T tests/conformance/test_conformance.py` (every
  component and every waiver test), G(algorithm_design, complexity, goal_engine, self_maintenance,
  align_prompt_library, metaharness), `& $PY -m g6.cli doctor`, and D on
  `tests/algorithm_design/test_mission_confined.py`, `tests/harness/test_proposed_checks.py`,
  `tests/harness/test_verification_templates.py` and `tests/core/test_confine_bwrap_linux.py`. Expected: all PASS,
  with no Docker skip.
- [ ] **Step 6: Commit and push.** Run C with the subject `gate 5: an installed-MCP smoke test of every P1 operation, on Linux and Windows
  CI (P1)`, then P, and wait for CI. P1 is done when every job is green, including `mcp-smoke` on both platforms.
  Then report the final SHA to the coordinator: claude/self-improvement at that SHA supersedes pull requests #2 to #7,
  and the owner closes them.

## Self-Review

1. **Spec coverage.** Section 7's conformance list (block, failure modes, skill, SPDX, contract, catalogue, site entry,
   maturity, tests) is covered by Tasks 1, 2, 8 and 9 and Global Constraints. The rulings: the experiment selector is
   off by default with repo_tests receipts (Tasks 3 and 5). algorithm_design and complexity have blocks, with 22 kernels
   through one path (Tasks 1 and 2). The mission has confine isolation, receipts, a durable ledger and an inert engineer
   (Task 4). There is one catalogue tied to stages, installed through the sensitive gate, with #4's gate replaced by the
   meta-harness's (Tasks 6 and 7). goal_engine has a block bounded at 100 calls (Task 8). self_maintenance is catalogued,
   with a skill, an inert live path, the typo fixed, the requirement fixed and `BudgetBook` kept (Task 9). The merge
   order and merge commits are in Global Constraints. Exit criteria: the `g6_invoke` reachability check is in Task 10,
   gate 1 is Tasks 1 and 2, gate 5 is Task 10, and CI runs at every task's end. Left to later phases: the controller's
   live operation, the budget meter, the scheduler, the records branch, issues, the mission over MCP (B2), real
   engineers, multi-file patches, the reference task and the north-star study.
2. **Step scan.** Each code step names its file, signature and pinned values. Bodies appear only for exact copy: the
   `PLAIN` words, the operator texts, the system prompt and the summaries. The test steps give names and assertions.
3. **Type consistency.** These names match across tasks: `RunObservation`, `from_run` and `receipt_matches` (Tasks 3, 4
   and 5); `ActionQueue.request_run` and `run_approver` (Tasks 4 and 9); `CatalogueEntry`, `ENTRIES`, `render`, `install`
   and `fill` (Tasks 6, 7 and 8); `CheckedResult` (Task 2 onward); `EXPECTED_OPS`, which equals the ops each task adds.
4. **Review Focus.** Each of its five lines has a pinning test in its owning task: the live agent in Task 9; approvals
   in Tasks 4, 6 and 9; isolation in Task 4 and in Docker; caller-declared facts in Tasks 1 and 9; sizes in Tasks 1, 2
   and 8.
5. **Proportion.** By words the plan is 1.4 times the spec, which it implements only one phase of. A third of its lines
   are test sketches (names and assertions); it has no function bodies.

## Coordinator's rulings (2026-10-29). These override the tasks above where they differ.

The planner's 21 rulings are accepted, and so are its 12 notes where the pull requests or the code contradict the
design. Five more:

1. **Start from the newest release line.** Before Task 1, merge origin/claude/launch into claude/self-improvement
   (`--no-ff`, no rebase), so P1 builds on the launch's latest fixes, including the worked examples' W1 to W4. Merge
   it again before Task 10.
2. **Pinned lists are extended, never loosened.** This covers `goal_engine`'s audit of its files,
   `align_prompt_library`'s op and sensitive-op lists, the home page's count of core components, and
   `scope.search`. Each keeps its equality assertion, with exactly the new members added. The commit that extends
   one names it.
3. **`goal_engine`'s claim is reworded, not dropped.** Its docstring and audit say it "generates no subtask", and PR
   #5 changes that. The new text: the planner itself still generates no subtask; `propose_goal_tree` asks an
   injected proposer (a model the caller supplies, at most 100 calls) for subtasks; and its tree is a proposal until
   a person or a mission accepts it. The no-model-import test keeps its assertions, and the injected proposer keeps
   them true.
4. **#2's `VerificationState` is renamed `ExperimentState`.** That frees the name for B2's `VerificationState`
   (design 6.4), which is a different object. Only #2's own code and tests use the old name.
5. **The ontology's citations.** The two data files' `pdf_page` keys are stripped, as Task 1 has it. The 75 section
   titles copied from Skiena's book are replaced by short descriptions in G6's own words, keeping the author, year
   and section number as the citation. This follows the owner's rule of 2026-10-25: cite books, never quote them.
   Apply the same to any other book-derived text in the six pull requests, and list each file you changed.
