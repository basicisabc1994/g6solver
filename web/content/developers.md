# Developer quickstart

**Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.**

G6 is a Python library (3.12 or 3.13). Every component works on its own and declares any other component it uses.
`g6.harness` is the one place they are composed: a standard (a goal and its checks) runs through intake, plan,
execute, adjudicate and remember.

```bash
# From a clone of the repository (G6 is not on PyPI):
pip install .               # core: pydantic + returns only
pip install ".[all]"        # optional extras: MCP server, Anthropic SDK, abstraction tools, Z3
g6 doctor                   # read-only check of the installation
g6 setup --client claude --allow patch_review.review    # or --client codex (run it yourself)
```

Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows before the one-time
access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a repository's tests) unless you
allow it once with g6 setup. Code you allow then has your own rights: it can read your key file ~/.g6/.env and your
other files, and its network guard works inside Python only. You are responsible for what you run. `g6 doctor` says whether that code runs confined on your computer: on Linux it needs
bubblewrap (the `bubblewrap` package), and on Windows it prints the one-time commands that grant the access. Run
`g6 setup` once yourself to make the choice for this computer.

## Run a task through the harness

```python
from g6.harness import Check, Standard, run

standard = Standard(
    goal="Summarise the refund policy in one sentence",
    checks=(Check("states the 14-day refund window", lambda out: "14 days" in out,
                  known_bad=("Refunds take 30 days.",), known_good="Refunds take 14 days."),),
)
result = run(standard, executor=lambda contract: "Refunds are issued within 14 days of a return.")
print(result["completion_state"])   # qualified-draft: the check passed, but you wrote it, so it is not independent
```

The checks are hashed before the executor runs. Each check must fail its `known_bad` outputs and pass its
`known_good` one, or it is reported as broken. A failed check is remembered for that task family under `$G6_HOME`,
and a later standard that drops it is refused until the check is back or a person waives it.

## Use a component directly

```python
from g6.action_gating import evaluate_action

ev = evaluate_action({"action_type": "delete_file", "target_backend": "local", "target_resource": "notes.txt"})
print(ev.gate_decision.decision, ev.completion_state)
```

```python
from g6.self_training import HarnessPatch, PatchEvalReport, decision_matches, govern_patch_promotion

patch = HarnessPatch(agent_id="proposer", target_surfaces=["documentation"], touched_paths=["docs/x.md"],
                     diff="--- a/docs/x.md\n+++ b/docs/x.md\n@@ -1 +1 @@\n-old\n+new\n")
decision = govern_patch_promotion(
    patch,
    PatchEvalReport(targeted_failure_fixed=True, regression_pass_rate=1.0, target_metric_improvement=0.05,
                    evaluator_confidence=0.9, independent_eval=True, evaluator_id="reviewer"),
    autonomous_low_risk=True,                           # opt in: low-risk changes may go ahead without a person
)
print(decision.decision, decision.reliability_label)   # promote_auto qualified-draft
assert decision_matches(decision, patch)                # apply only the change that was decided on
```

Without `autonomous_low_risk=True`, every change waits for a person: the same call returns `require_approval`.

## Components at a glance

The [catalogue](components.md) is generated from the code, so it always matches what you install.

| Area | Components |
|---|---|
| Evidence and claims | `grounding`, `epistemic_integrity`, `evidence`, `invariants` |
| Actions and runtime safety | `action_gating`, `action_logic`, `safety_engine`, `guards`, `goal_engine`, `csf` |
| Coding agents | `coding_agent_gate`, `workforce` |
| Tasks, plans and context | `intent_engine`, `reliability_pipeline`, `solver`, `navigator`, `context` |
| Reasoning, logic and search | `reasoning`, `debate`, `formal`, `cegis`, `abstraction` |
| Governed learning | `learning`, `self_training`, `distillation` |

A component with an MCP block also has a command-line surface, and the [catalogue](components.md) says which ones do. The others
are Python-only for now: `g6 describe` shows no ops for them, and `g6 invoke` answers `G6_E_NO_BLOCK`. `workforce` has
no block but has MCP tools of its own, below.

Outside the components: `g6.core.stop` (the stop switch), `g6.core.snapshots` (`g6 state`), `g6.integrity` (file
hashes against a pinned manifest), `g6.triage` (Python error triage) and `g6.operator` (a budgeted loop with a
simulated executor and an approval queue a person decides). In `g6.research`, the evidence kernel is an audit view
over the evidence issuer's records, with no flag; governed adaptation is off unless `G6_ECHA_RESEARCH=1`, and the
mission bridge unless `G6_MISSION_BRIDGE=1`.

## Use it from your coding agent (MCP)

```bash
g6 setup --client claude --allow patch_review.review    # or --client codex; give --client twice for both
```

Setup registers this installation with the client, with this installation's Python, started with `-P` so that the
packages in the project's folder never replace G6's own, installs the `using-g6` skill for it, allows patch review, and ends by showing the first review call. If the client already has an entry called
`g6`, setup stops; remove it with `claude mcp remove --scope user g6` (or `codex mcp remove g6`) and run setup again.
For another client, register `g6-mcp` and give it `G6_MCP_SENSITIVE=patch_review.review` in its environment.

40 MCP tools: six discovery tools, `g6_help`, `g6_components`, `g6_find`, `g6_describe`, `g6_invoke` and `g6_compose`;
`g6_allowances` for both providers' quota windows and advisory pacing; `g6_review_patch`;
`g6_review_until_clean`; `g6_premortem` and six `g6_mission_*` tools for a coding mission; `g6_confirm_behaviour`, one behaviour question for the person; six `g6_improve_*` tools for G6's self-improvement loop;
thirteen `metaharness_*` tools for [tuning](#tune-g6-to-your-workflow); and four sharing tools,
`g6_cloud_grant`, `g6_cloud_revoke`, `g6_cloud_grants` and `g6_disclosures` ([cloud sharing consent](#cloud-sharing-consent)). The agent finds a component
by describing its task, reads what the component can do and how mature it is, then calls one op. Every reply carries
`completion_state`, a `warning_card` with a `G6_E_*` code, `evidence`, `request_id` and `run_id`. An op a component
marks sensitive is refused over MCP unless the person who runs the server lists it in `G6_MCP_SENSITIVE`.

`g6_review_patch` (allowed with `patch_review.review`) assesses a Python patch against the requirements you state, each
with the tests that check it, in test runs inside an operating-system boundary. It returns one outcome: test integrity
compromised, defect detected, insufficient evidence, or supported within scope. Supported within scope means every
stated requirement passed its stated checks, not that the patch does everything the task needs. Each requirement gets
its own evidence, and merging always needs a separate approval. It returns a job: poll `metaharness_job`. The reply's
`assessment.report` is the review as Markdown, for the agent to show the person verbatim: what G6 reproduced first,
then what is not established, then what the person decides. The reply is compact; its `full_assessment` names the file
in G6's state folder that holds every field, and `metaharness_job` with `detail="full"` returns them all. The `using-g6` skill tells the agent to fix what G6
reproduced, add each earlier reproduction as a stated check, and review again, for at most three rounds.

`g6_review_until_clean` (allowed the same way) runs one step of G6's completion loop, for a client the plugin's Stop hook
cannot reach. It also needs `G6_LOOP_CRITIC=codex` in the person's key file, as it spends their own Codex allowance. Another model
family reads the patch without the agent's reasoning and must state a rule, reasons and a test. If that test fails, the reply
says continue, with the text for the agent, which may reject the finding. A fresh review reads each repair, at most three
repairs. Otherwise the reply says stop, with the report: what was found, what was fixed and what stayed open. It returns a
job: poll `metaharness_job`. A review that reproduces nothing is not evidence the work is right, only that this reviewer found
no failure it could show, and the loop has been measured on seven training bugs only.

A coding mission holds a change to a standard fixed before the work. `g6_premortem` proposes it, with no model call and
nothing run: the intent contract, the requirements with their stated checks or marked not established, the competing
ways the task can fail and at most three questions. `g6_mission_create` freezes the standard from the commit a clean
repository stands at; `g6_mission_clarify` answers the one question an objective about retries or duplicates asks;
`g6_mission_confirm` has the person confirm the standard; the agent changes the one permitted file, and
`g6_mission_submit` checks it: `g6_review_patch`'s assessment in the working tree with the frozen tests as its stated
checks, then the mission's own bench on its frozen copy, so a patch that gets that far runs the tests twice. A reproduced
defect, or a sign the tests cannot be believed, holds the patch back: it uses an attempt, and the mission cannot close
on it. Insufficient evidence blocks nothing, and the mission's reply, status and certificate say so. The mission's
repair ladder decides what comes next: three attempts, then a focused brief, then the person. The agent repairs and G6 never
writes code. `g6_mission_status` and `g6_mission_certificate` read the mission. Allow the tools that run code with
`workforce.mission`, as in `g6 setup --client claude --allow patch_review.review,workforce.mission`.

What a person allows and confirms goes through the client's own dialog (MCP elicitation) and nowhere else. No argument
confirms a standard, an agent cannot answer for the person, and there is no terminal fallback: a decline, a cancel or a
client without a dialog leaves the mission unconfirmed, and an unconfirmed mission takes no patch. The name the person
types is recorded as given; it is not an authenticated identity. Merging is the person's separate decision. An `R3`
mission's patch approval is not over MCP yet: its status names the terminal command for the person.

`g6_confirm_behaviour` asks the person one behaviour question about a task, in the client's own dialog, where the task may not decide the behaviour. The agent gives candidates (a call, two competing outcomes, a quote of the task and repository evidence), or a finished review of the same task and working tree whose `differential.v1` instances G6 saw the base commit and the patch disagree on; G6 takes only candidates whose two outcomes compete, picks one by a fixed priority and says why, and coverage stays unknown. The form shows the exact input, outcome A and outcome B, the cited scope, and the options neither, the premise is wrong, the task already says and skip, with a text box; a rule, when one is offered, is shown in full before it can be confirmed. One question per task by default, kept across calls under `G6_HOME`; the person allows more in the form, or sets `G6_QUESTIONS_PER_TASK` where the server is registered (0, or a value G6 cannot read, for none). G6 keeps the store's head beside it and refuses a store that is damaged, cut back, replaced or copied from another task, never reading it as empty; it does not defend against a process running as the same user that rewrites the store and its head together. The answer comes from the dialog only, and the name is recorded as given, not authenticated. What the person confirmed is checked on each later `g6_review_patch` of the same repository and task, as one `example.v1` check: a failure is a defect, and the report shows the question, the answer and the check's result apart from model results.

`g6_improve_enrol`, `g6_improve_start`, `g6_improve_resume`, `g6_improve_demote`, `g6_improve_stop` and `g6_improve_status`
control G6's self-improvement loop on a folder. Enrolling, starting, lifting a pause and taking a generation back need
`metaharness.improve`, and the person agrees in the client's dialog, as above, and nowhere else: the loop's command line
records none of them. Starting starts a
process of its own that outlives the session, works within the week's pacing rule and writes a daily digest under the
folder. Stopping needs no permission and no dialog: it writes a local `STOP` file, kills nothing, and says whether the
runner has gone yet. Status only reads. What the loop adopts holds in its own workspace: it pushes nothing. The runner's command carries on a
start the person agreed to and is refused otherwise, and a stop ends that start, so only a new agreement brings it back.
That is a check and not a boundary: a process with your rights can reach the same code.

## Choose a model

Components that call a model take an `LLMCaller`. Pick one explicitly — G6 never guesses a paid provider:

| `G6_LLM_PROVIDER` | Notes |
|---|---|
| `none` (default) | Calls fail with a named error; pass your own caller. |
| `ollama` | Local and free. `OLLAMA_BASE_URL`, `G6_LLM_MODEL` (default `gpt-oss:20b`). |
| `anthropic` | Official SDK (`g6[anthropic]`), default model `claude-opus-5-5`. |
| `openai` / `openrouter` | OpenAI-compatible; `G6_LLM_MODEL` required. |

`G6_DISABLE_LLM` (any value) turns every model call off.

Keys go in your key file, `$G6_HOME/.env` (by default `~/.g6/.env`): one `NAME=value` per line, written by you and
never by G6. G6 reads `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and `OPENROUTER_API_KEY` only from that file, never from
the environment or a project's `.env`, so a key there is your approval for G6 to use it. The settings above may sit in
the same file; the environment overrides them. `g6 doctor` names what the file holds without showing a value, and a
snapshot never copies it.

```python
from g6.grounding import TextRetriever, check_claim

r = TextRetriever.from_texts({"policy.md": "Refunds are issued within 14 days of a return."})
print(check_claim("Refunds are issued within 30 days", r).status)   # conflict
```

## Stop it

Set `G6_STOP` to any value, or create a file named `STOP` in `$G6_HOME`. The harness, the workforce door, the safety
engine, the operator loop and the self-training cycle check it and start no new work. It is a check, not a
preemption.

## Tune G6 to your workflow

`g6.metaharness` (`implemented`) proposes changes to a workflow's profile (model per step, budgets,
prompt, hints, extra checks, pipeline steps, admitted distillates, a code overlay, an optional LoRA adapter), tests each
on held-out cases at equal compute through `g6.harness`, and activates nothing without a person's approval. When the
gate refuses, the reply carries `recovery` (root causes and remedies, each marked as a candidate the meta-harness can try
or a decision for a person), and `mh.diagnose()` maps the dev failures to the G6 components that address them. It is
built on `g6.reasoning`'s sign test, `g6.learning`'s hints, `g6.context`, `g6.self_training`'s patch gate and the
hash-chained ledger and vault in `g6.core`. Over MCP: `metaharness_*` tools, where approval always asks the person. See
[Tune it to your work](metaharness.md). The studies run with it are on the [Research](research.md#the-meta-harness)
page, as a worked example.

## Cloud sharing consent

The unbound model adapters and MCP session transport use one source/provider check at send time. A repository is a source; task text and other
caller inputs have separate scopes when their origin is not the repository. A grant is given only in the client's
dialog, and the dialog explains that sent data cannot be recalled. Unavailable, declined or cancelled dialogs grant
nothing. A local loopback model needs no cloud grant. Claude Code and Codex sign-ins are cloud providers.

- `g6_cloud_grant(source, provider)` asks in the client dialog; no tool parameter carries the person's answer.
- `g6_cloud_revoke(source, provider)` stops later sharing without another dialog.
- `g6_cloud_grants()` lists active grants, their times and attribution hashes.
- `g6_disclosures()` reads attempted disclosures: source/file/passage ids, hashes, provider and time, never text.

Providers include `anthropic:claude_code`, `openai:codex`, `anthropic:api`, `openai:api`, `openrouter:api`, and an
`endpoint:<sha256>` identity for a configured custom endpoint. Sources may be canonical repository paths or the opaque
ids a producer records. Use an absolute path or `./relative/path` for a repository; a plain name is an opaque source id. Grants are in the hash-chained `cloud_consent/events.jsonl` under `G6_HOME`, protected by an OS
lock and a durable head checkpoint. Revocation waits for an admitted in-flight call to finish; it cannot recall that
call. Retries made as a new caller invocation are checked again. A checkpoint mismatch denies sharing; do not delete
or truncate the ledger to recover it, since that could discard a revocation or attempted disclosure.

Producer contracts live in `g6.core.consent_declarations`: every MCP reply, agent adapter and model
caller declares possible source classes. The closed classes are `client_request`, `client_repository`,
`g6_operational`, and `source:<scope>`. A declaration adds to any recorded origins carried by the
content; it never replaces them. Explicitly unrecorded origins remain withheld.
`g6_operational` covers G6's own ids, codes, counts, states, digests, times and fixed program text;
it excludes files, outside paths, authored text and retained content.

Retained and implicit sources have named scopes: `missions`, `workflows`, `improvement`, `jobs`,
`completion_history`, `host_configuration`, `component_catalogue`, `component_data`, `consent_metadata`,
`agent_environment`, and generic `model_inputs`.
`component_catalogue` covers installed extension names, package descriptions and skill files, including rankings derived from them.
`component_data` includes indexes, corpora and component IO outside the client request;
`jobs` includes retained asynchronous results. Grant and disclosure listings use `consent_metadata`,
since scope labels can contain text supplied in earlier calls. A provider needs that scope grant to read them. A scope grant covers that scope's possible content,
and only the named provider. Narrower repository and exact-input grants remain supported.

The inventory tests derive registered tools and caller classes from code and fail on undeclared
additions. Planted-content tests capture cloud replies and requests with fakes. Declarations are
trusted host code, checked by tests; neither result fields nor client arguments supply a contract.
The frozen MCP packet is admitted at transport handoff under the sharing lock.

Only the exact argument bytes supplied in the current request are client-supplied content.
A repository, file, directory or retained-record handle grants nothing it points to. Repository
reads need that repository's grant, including for the same client's provider. CLI agents, scouts,
reviewers and proposers keep their prompt's sources and need grants for every directory exposed
to their file tools. Retained job results keep their original sources, including through compaction.
CLI permissions can also read outside that workspace or load user configuration. Those reads
require an `agent_environment` grant to the agent's provider, including for the same client.
The K reviewer records a withheld review as its own cause and ends `untested`.

Recipients are classified from caller class identities and configured endpoints, following G6's
known wrappers. A provider's own SDK client is that provider. Host wrappers that transform
requests may use `g6.core.consent_callers.CallerWrapper` and delegate through `inner`.
SDK wrappers use `SDKClientWrapper` to expose their actual SDK client. Synthetic host clients can implement
the explicit `SDKClient` endpoint contract; an arbitrary object with a `base_url` attribute is not one.
A foreign opaque wrapper remains `cloud:unknown`; no grant covers it.

Certificate-bound raw provider/library exports still need the transport admission change offered
for an issuance window. Per-retry SDK hooks and automatic first-use dialog solicitation at raw
model boundaries remain on that offer. Full coverage of those raw exports is not claimed here.
