<!-- The site FAQ (web/content/faq.md) with links to these docs; tests/site/test_docs_copy.py keeps the two the same. -->
# FAQ

Today's language models already hold the right kind of general intelligence. What they lack is a dependable way to
put it to work, and G6 is built to be that way.
{: .why }

**Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.**

## Why G6

### Why does G6 exist?
To turn the intelligence AI models already have into reliable, useful work.

Models can invent facts, lose context, make logical errors and miss gaps in their own understanding. Strong benchmark
scores do not always translate into strong performance on your tasks. Learning from experience is limited, context is
finite, and the most capable models are often expensive.

G6 tackles these limits through the harness around the model: the system that manages context and memory, checks
reasoning, supports learning and controls actions. The goal is to make existing intelligence work better in practice.

### Which limits does it answer, and how?
- **Making things up:** each claim is checked against your sources, and every result is labelled for what its
  evidence supports.
- **Forgetting, and short context:** each call gets just what it needs, marked with its source, and lessons from
  past runs are kept as hints you approve.
- **No learning on the job:** G6 learns in the harness, so your model never changes and nothing it knew is lost.
- **Logic and exact work:** solvers, a proof checker, algebra, rule engines and an optimiser do the exact parts, and
  their answers are checked.
- **Benchmarks that do not carry over:** every change is judged only on your own held-out examples.
- **Unsafe actions and self-improvement:** actions are gated, a stop switch halts new work, and no change goes live
  without evidence and your approval.
- **Cost:** a frontier model engineers the harness once, then a small model on your own computer runs inside it.

The [home page](index.md) sets out each limit with the components that answer it.

### What is G6?
A batteries-included framework for harness self-engineering: it lets the AI system you already run engineer itself
for your work. It has two parts.

- **The meta-harness** is how G6 learns. It finds where your task goes wrong, proposes changes to how it runs, tests
  each one on cases it has never seen, and keeps a change only when it wins and you approve. It can never change the
  checks that judge it, which is what makes its self-modification safe.
- **The components** are a prebuilt library of AI primitives for capability extension and failure engineering. Each
  extends what a model can do or closes one way it fails, and they layer like slices of Swiss cheese.

G6 brings the tools, loops, prompts and code, the higher-order functions that compose them, and integrations with
other systems. You use it through its MCP server (`g6-mcp`, the main surface), as a Python library, from the `g6`
command line, or as a ready-made [bundle](bundles/index.md).

### Where do the components come from?
From studying how AI agents fail. Some 250 million tokens of agents' reasoning and failure traces were reviewed by
hand to find the primitives that real work keeps needing. The aim is a complete set: a periodic table of
intelligence. Each component is one element, listed in the [catalogue](components.md) with the failure it guards
against.

### How is G6 different from other AI tools?
It is a new kind of system. A model predicts text. An agent runs a model in a loop someone wrote by hand. Most AI
frameworks hand you parts and leave the engineering to you. G6 does the engineering itself, and learns in a different
way from today's machine learning:

- **Explicitly, not implicitly.** Training a model shifts millions of numbers no one can read. G6 learns through the
  harness, a structure written out in full: each change is a prompt, a check, a step or a piece of code you can read,
  test and roll back.
- **From its own experiments.** It runs experiments on itself, on cases held out from it, and acts on what it finds.
- **With a model of itself.** Every component declares what it does and needs, and every way G6 runs a task is
  written down, so it can see its own parts and change them.
- **From plain words.** With a coding agent, you can teach it in your own language: the agent turns what you say into
  changes, and G6 tests each one before you approve it.

That makes its learning closer to a person's. With its checks and safety mechanisms, it is built for deploying AI
agents in the real world, on complex, open-ended work that needs human-level reasoning and memory over time.

### What can I do with it that I could not before?
Build a specialist from the agent you already run and your own data, without training a model of your own: G6
engineers the harness for your task on your examples, and your model's weights never change. Then try to cut what
repeated work costs: pay a frontier model once to engineer the harness, and run a small model on your own computer
inside it, with no fee per call. Whether that saves anything on your work is for your own held-out examples to
show.

### How far can it go on one task?
Give it a task you can check and enough examples with known answers, and nothing in its design caps how far it can go
on that task. It can engineer prompts, checks, pipelines and code, and even train a model of its own, such as a small
adapter for a local model, keeping each step only when it wins on cases held out from it. Many of its internals rest
on established mathematics and computer science, set out in [the working theory](research.md). How far it gets
on your work depends on your task and the data you have.

### What is G6 not?
It is not a model and not an agent: it never does the task for you. It is not autonomous: its gates advise, and the
risky decisions go to a person. It is not a security boundary on its own. It is not a guarantee: it says what the
evidence supports and nothing more. And it is not AGI.

## The basics

### Is G6 free? Is there a paid plan or an account?
It is free, and all of it is open source under the Apache License 2.0, including for commercial use. There is no paid
plan, no hosted service and no account.

### Do I need to be a programmer?
No. Use a [bundle](bundles/index.md): it opens a private page in your browser. Downloads come with the first release.
Developers and coding agents use the library directly.

### Which AI model does it use?
Yours. A free local model through Ollama, or your own Anthropic, OpenAI or OpenRouter key. G6 calls no model unless
you choose one, and never picks a paid provider for you. Many components, and 6 of the 10 bundles that work today,
need no model at all.

### Does my data leave my computer?
Only what you send to the model you chose. G6 has no telemetry and no server of ours. Everything it stores is in one
folder, `$G6_HOME` (by default `.g6` in your home folder).

## Results and labels

### What do `verified`, `qualified-draft` and `blocked-escalated` mean?
`verified`: every required check passed on independent evidence. `qualified-draft`: usable, but something is
unproven, and the result names it. `blocked-escalated`: not safe to rely on; the result says what is needed, such as
a source, a fact or a person's decision.

### Why are most results `qualified-draft`?
Because `verified` needs independent evidence: a check the producer did not write, frozen before the work, that G6 has
seen fail a planted wrong answer. A check you or your agent wrote is not independent, and a component checking work it
was handed cannot see the world. So components give their own work `qualified-draft` at best. That is deliberate.

### Can G6 make a result look more certain than it is?
It is built not to. No step raises a label. When results are combined, the overall label is the weakest one. Over
MCP, a reply with no state is a `qualified-draft`, and nothing is `verified` without evidence.

## Components and bundles

### What is a component?
A small package that does one job, such as `grounding` (check claims against sources) or `action_gating` (allow, ask
a person, or block). Each works on its own and declares any other component it imports. There are 34 in the core
harness, and 60 extensions are in the library, each in a family for one kind of work; the
[catalogue](components.md) is generated from the code. The two tables below list every one.

### Which components are in the core harness?

| Component | What it does | Over MCP | Ops refused over MCP |
|---|---|---|---|
| `abstraction` | Learns simple patterns from the results of a costly search, each checked against real evaluations, and suggests which tries to skip | Python-only | — |
| `action_gating` | Before an agent acts, decides whether it may go ahead, must ask a person first, or is blocked | MCP | `budget_check`, `budget_policy`, `budget_record` |
| `action_logic` | Rules from research on how complex decisions fail: act in small steps, watch for early warnings, and change course when the evidence turns | MCP | none |
| `algebra` | Does exact algebra and calculus for you, such as simplifying a formula, solving an equation, or finding a derivative, an integral or the inverse of a matrix, and checks each answer a second, independent way before handing it over | MCP | `sage_evaluate`, `sage_version` |
| `causal` | Works out from your diagram of what causes what whether data can show the effect of one thing on another and which variables to control for, then estimates the effect and tries to knock it down | MCP | none |
| `cegis` | Fills the gaps in a code template, then hunts for an input that breaks it, and repeats until none is left in the range you set | Python-only | — |
| `coding_agent_gate` | Checks a coding agent's work before it acts and again before it says the job is done | MCP | none |
| `context` | Packs just the files and notes a model call needs, within a size limit, each marked with where it came from | Python-only | — |
| `csf` | Works out an upper limit on how likely an agent you describe is to reach an unsafe state, and checks rules it must always keep | Python-only | — |
| `datalog` | Works out everything that follows from your facts and rules, such as who reports to whom or what a change can reach, and shows how each answer was derived | MCP | `assert_facts`, `reset` |
| `debate` | Has named roles, such as a proposer and a critic, argue a question in turn, and reports how far they agree | Python-only | — |
| `distillation` | Checks a small function that stands in for repeated model calls: it runs it in a bounded child process and tests it on new cases before anyone relies on it | Python-only | — |
| `epistemic_integrity` | Grades the evidence behind each claim and holds back a high-stakes conclusion the evidence cannot carry | MCP | none |
| `evidence` | Gives the final verdict on a piece of work: verified, a qualified draft, or blocked, with every gap named | Python-only | — |
| `formal` | Settles small logic questions exactly with a solver, and labels each answer for what it is | MCP | none |
| `goal_engine` | Holds the subtasks you declare as a tree and checks them against what your executor reports | Python-only | — |
| `graph_reasoning` | Works out how true each label on a network is, such as who counts as popular or which link can be trusted, as a range from false to true, from your facts and rules, and shows how each range was reached | MCP | none |
| `grounding` | Finds the passages in your sources behind a claim and says how well they support it | MCP | none |
| `guards` | Flags a fixed list of English phrasings of prompt injection; a paraphrase passes | Python-only | — |
| `intent_engine` | Writes down what a task must achieve, what counts as failure and what is out of scope, and lists what is still missing | Python-only | — |
| `invariants` | Checks that every result's label matches the evidence attached to it | Python-only | — |
| `lean` | Has Lean, a proof assistant, check a proof you wrote of a theorem you name, and confirms that it leaves no step unproved, assumes nothing beyond the standard rules and proves exactly the statement you give | MCP | `check_proof`, `lean_version`, `prove` |
| `learning` | Learns hints from an agent's failures, and keeps one only when side-by-side runs show it helps | Python-only | — |
| `metaharness` | Tunes how G6 runs a task you repeat: it proposes changes, tests each one on cases it has not seen, and keeps one only when you approve | Python-only | — |
| `model_checking` | Checks a system you describe as states and the steps between them, such as a protocol or a workflow: it finds a run of steps that breaks a rule or gets stuck, or shows the rule always holds | MCP | `sany_parse`, `tlc_check` |
| `navigator` | Checks a plan of G6 calls against each component's rules before anything runs | Python-only | — |
| `optimisation` | Finds the best plan within your limits, such as routes, schedules, assignments or a budget, and checks each answer before handing it over | MCP | none |
| `prolog` | Runs a question against a Prolog program you give it, with SWI-Prolog on your computer, accepting only plain facts, rules and a short list of standard operations, and checks every answer a second time before handing it over; it is not a sandbox, so run programs you do not trust inside your own | MCP | `check_program`, `prove`, `query`, `swipl_version` |
| `reasoning` | Budgets, vote counting and side-by-side tests for multi-step reasoning, so a gain is measured, not assumed | Python-only | — |
| `reliability_pipeline` | Takes a task in, checks it can be done, and names how it could fail before work starts | Python-only | — |
| `safety_engine` | Hard limits for one run: a stop switch, allowed actions, and ceilings on steps, tool calls, files changed, time and cost; it also spots tests that were weakened | Python-only | — |
| `self_training` | Decides whether a proposed change to the harness may go live; safety rules and failed tests can only make that decision stricter | Python-only | — |
| `solver` | Runs a goal through a fixed sequence of 15 problem-solving steps that you supply, and reports where it stopped | Python-only | — |
| `workforce` | Fixes what done means before a coding task starts, has a person confirm it, then checks the agent's change against it | Python-only | — |

### Which extensions are in the library?
The last column links the certificate G6 issued for each extension that works in a vetted area, such as clinical or
scientific work (see the next answer).

| Extension | Family | What it does | Over MCP | Ops refused over MCP | Certificate |
|---|---|---|---|---|---|
| `adapt_audio` | Media | Edit, level, clean and measure sound in WAV files, each result measured again and never clipped silently. | MCP | `transcode` | — |
| `adapt_blender` | Media | Build, light, render and export 3D scenes with your Blender, each change read back and each render inspected. | MCP | every op but `session_new`, `session_delete` | — |
| `adapt_comfyui` | Media | Make and edit pictures on the ComfyUI server you run, each one fetched into G6's media folder and inspected. | MCP | every op | — |
| `adapt_diagrams` | Media | Write checked Mermaid, DOT, PlantUML or ASCII diagrams, and draw DOT with Graphviz. | MCP | `render_dot` | — |
| `adapt_ffmpeg` | Media | Convert, cut, join, resize, filter, subtitle and level video and sound with your ffmpeg, every result probed from disk before it is returned. | MCP | every op | — |
| `adapt_generative_art` | Media | Make procedural pictures, vector drawings and heightmap meshes from seeded techniques. | MCP | none | — |
| `adapt_image` | Media | Edit, convert and measure pictures, and generate them through the media family's backends, local first, each one inspected. | MCP | `generate`, `generate_paid` | — |
| `adapt_synth` | Media | Make tones, notes, chords, scales, melodies, noise and drum patterns, each one measured against what was asked. | MCP | none | — |
| `adapt_visualisation` | Media | Chart the rows you give, and show the file holds every row and only the values given. | MCP | none | — |
| `adapt_voice` | Media | Speak text and transcribe speech, refusing a transcript that is empty or at an impossible speaking rate; a wrong one of a plausible length passes. | MCP | `speak`, `speak_paid`, `transcribe`, `voices` | — |
| `adapt_automl` | Machine learning | Search estimators and settings within a budget, and score the choice on rows it held out. | MCP | none | [certificate](https://github.com/basicisabc1994/g6solver/blob/main/src/g6/ml/adapt_automl/certificate/CERTIFICATE.md) |
| `adapt_bayesian` | Machine learning | Exact Bayesian updates and checked posterior sampling: expose hidden uncertainty. | MCP | `sample_pymc` | [certificate](https://github.com/basicisabc1994/g6solver/blob/main/src/g6/ml/adapt_bayesian/certificate/CERTIFICATE.md) |
| `adapt_pandas` | Machine learning | Read a table you give by one stated rule, and say where it mixes kinds, looks missing or repeats. | MCP | none | — |
| `adapt_pytorch` | Machine learning | Train a small neural network on rows it holds out itself, seeded, and stopped on a broken loss. | MCP | `forget` | — |
| `adapt_sklearn` | Machine learning | Fit and score a scikit-learn model on rows it holds out itself, after checking for leakage. | MCP | none | [certificate](https://github.com/basicisabc1994/g6solver/blob/main/src/g6/ml/adapt_sklearn/certificate/CERTIFICATE.md) |
| `agent_autogen` | Agent runtimes | Run an AutoGen team (round robin, selector or swarm) on the model you pass in, stopped at a message limit, every tool call decided by G6's gate. | MCP | `run_team` | — |
| `agent_claude` | Agent runtimes | Hand a coding task to Anthropic's Claude Code, which you installed: read tools only unless you grant edits, never its shell or the web. | MCP | `available`, `run` | — |
| `agent_codex` | Agent runtimes | Hand a coding task to OpenAI's Codex, the coding agent you installed: read-only unless you grant writes, in the folder you name, under a deadline. | MCP | `available`, `run` | — |
| `agent_langgraph` | Agent runtimes | Run a LangGraph workflow that saves its state after every step, so a run that is stopped or crashes resumes where it was. | MCP | none | — |
| `agent_nanoclaw` | Agent runtimes | A durable mailbox between agents: each reader pages through every message for it, and one it did not acknowledge comes back after a crash. | MCP | `delete_group` | — |
| `agent_runtime` | Agent runtimes | Run any agent under one set of rules: declared tools and permissions, capped limits, G6's stop, and the action gate before each outward call. | MCP | none | — |
| `agent_smolagents` | Agent runtimes | Run a smolagents tool-calling or code agent; its code runs in a separate, time-limited process with Python's network calls blocked. | MCP | `run` | — |
| `ctx_colbert` | Knowledge and retrieval | Rank one call's passages by meaning, say which method ranked them, and refuse word matching when meaning is required. | MCP | none | — |
| `ctx_contexthub` | Knowledge and retrieval | Keep a documentation registry you name, each document with when it was read and a time to live, and refuse a copy past it. | MCP | `sync` | — |
| `ctx_elastic` | Knowledge and retrieval | Search the Elasticsearch you run over fields its mapping holds, so a query that cannot match is refused rather than answered with no hits. | MCP | `count`, `get_doc`, `mapping`, `search` | — |
| `ctx_search` | Knowledge and retrieval | Search the web through the SearXNG you run, or Brave when you approve it, and tell an engine that failed from one that found nothing. | MCP | `search` | — |
| `knowledge_chunker` | Knowledge and retrieval | Cut a document into passages with exact offsets and ids bound to their source, and check a citation against it. | MCP | none | — |
| `knowledge_converter` | Knowledge and retrieval | Turn PDFs, web pages, Markdown and text into a document that says what converted it and what could not be read. | MCP | none | — |
| `knowledge_fetcher` | Knowledge and retrieval | Fetch pages and searches (arXiv, OAI-PMH, OpenAlex, Semantic Scholar) through G6's checked HTTP, and say exactly why a fetch failed. | MCP | `fetch` | — |
| `knowledge_indexer` | Knowledge and retrieval | Keep passages in a searchable index under a storage budget you set, and search them with each hit's source and a score on a stated scale. | MCP | `remove` | — |
| `knowledge_manifest` | Knowledge and retrieval | Keep the sources and passages an agent cites, and check a passage against its source as read again. | MCP | none | — |
| `ctx_ace` | Memory | Keep an agent's notes and find them again by relevance, each read back as data with its source, writer and age. | MCP | `approve`, `forget` | — |
| `ctx_cognee` | Memory | Add text to the Cognee server you run and follow the links its knowledge graph draws between facts, each result cited and untrusted. | MCP | every op | — |
| `australian_legal_corpus` | Sources | Search Australian law on your own computer, each passage with its jurisdiction, link and collection date, never as current law. | MCP | `fetch`, `remove` | — |
| `ctx_geo` | Sources | Answer location questions from a map service, with its status read on every reply, so a refused key or a used-up quota is never "no results". | MCP | every op but `backends` | — |
| `ctx_git` | Sources | Read and change git repositories inside folders you declare, with every call logged and every change since a point reported, whoever made it. | MCP | every op but `roots` | — |
| `ctx_langextract` | Sources | Pull named fields out of a text, each value with its offsets in the text or refused, the whole text read. | MCP | none | — |
| `ctx_library_mapper` | Sources | Find candidate libraries in PyPI, GitHub and Hugging Face, their declared licences looked up against yours. | MCP | `search_candidates` | — |
| `ctx_vision` | Sources | Show a picture to an image-capable model you give it, record its account as a claim, and cross-check its reading of text with Tesseract. | MCP | `extract_text` | — |
| `align_prompt_library` | Workspaces | Keeps every version of a prompt, spec or test set by the SHA-256 of its content, so a result can cite the exact version it used. | MCP | `put`, `retire`, `rollback` | — |
| `shared_workspace` | Workspaces | Lets several agents share one workspace folder without overwriting each other's work: the first write wins and a stale one is refused. | MCP | `write` | — |
| `workspace_manager` | Workspaces | Gives each agent task its own workspace folder and checks every path as part of each read and write, so nothing lands outside it. | MCP | `backup`, `cleanup`, `restore`, `write` | — |
| `adapt_django` | Code | Generate Django source without Django; check full projects in a separate, time-limited process, not a security boundary. | MCP | `run_checks`, `write_files` | — |
| `adapt_healing` | Code | Repair a failing step of Python code, keeping a patch only if it passes held-back tests its proposer never saw. | MCP | `heal` | — |
| `adapt_library_synthesizer` | Code | Draft Python from a paper spec and check independently supplied examples. | MCP | every op but `backend_capabilities` | — |
| `adapt_webpage` | Code | Build web pages and design tokens; check syntax, declared contrast and optional overflow. | MCP | `run_checks`, `write_files` | — |
| `component_creator` | Code | Scaffold a new G6 component in the library's own shape, and check a package against the library's standard before anyone calls it done. | MCP | `render` | — |
| `design_governance` | Code | Check a codebase's structure for import cycles, layer breaches and pass-through wrappers, and which cycles a change introduced. | MCP | none | — |
| `meta_programming` | Code | Rename and extract Python code through its syntax tree and scopes, each edit checked against the tree it should give, comments kept. | MCP | `run_tests` | — |
| `swe_diagnostics` | Code | Show a coding agent an unfamiliar repository before it changes it: its layout, build, tests and files, and what a change touches. | MCP | `patch_check` | — |
| `adapt_physical_ai` | Embodied and simulation | Check a robot's motion plan against its world and its own limits, segment by segment, before anything moves. | MCP | none | [certificate](https://github.com/basicisabc1994/g6solver/blob/main/src/g6/embodied/adapt_physical_ai/certificate/CERTIFICATE.md) |
| `cog_arch_actr` | Cognitive architectures | Rank an agent's memories by recency, frequency and context with ACT-R's equations, refusing a recall below the retrieval threshold. | MCP | none | — |
| `cog_arch_aixi` | Cognitive architectures | Run a bounded MC-AIXI-CTW agent on a registered environment and judge it by its regret against a random policy on the same seed. | MCP | `run_episode` | — |
| `cog_arch_gps` | Cognitive architectures | Plan a sequence of steps to a goal by means-ends analysis, every plan replayed against its operators before it is offered. | MCP | none | — |
| `decision_making` | Cognitive architectures | Standard, seeded decision rules for what to try next and how many retries to allow, each answer with its working and the same on every run. | MCP | none | — |
| `duty_of_care` | Professional workflows | Matches a fixed list of English crisis phrases; a paraphrase or another language passes. A match goes to a person. | MCP | none | [certificate](https://github.com/basicisabc1994/g6solver/blob/main/src/g6/jobs/duty_of_care/certificate/CERTIFICATE.md) |
| `adapt_rq` | Integrations | Background JSON jobs on an operator-owned Redis queue, with registered functions and sensitive operations. | MCP | every op | — |
| `database` | Integrations | Query and change a database the server's operator declares, reads on a connection that cannot write, changes dry-run first. | MCP | `execute` | — |
| `hat_orchestrator` | Integrations | Coordinate declared team capabilities and claims; operator ranks never authorise real-world actions. | MCP | every op but `check_sync` | — |
| `llm_router` | Integrations | Pick a language model for a kind of task from a committed table, local first, with an ordered fallback chain and dated cost estimates. | MCP | `delete_model`, `fetch_openrouter_catalogue`, `list_local_models`, `pull_model` | — |

### What is a component's certificate?
Every extension in a vetted area ships with the certificate G6 issued for it: a record of the checks G6 ran on its
exact code, committed beside the code as `certificate/CERTIFICATE.md` (and `certificate.json`) in the component's
folder. It is not listed in the library until its certificate is current; `g6 describe` returns it, and every result
the component gives cites it. For each rule the code applies, it records:

| Part | What it holds |
|---|---|
| The source | the source the rule comes from, its clause and the clause's SHA-256 hash |
| Known values | the source's own worked examples, run through the code as known-value cases |
| A second route | an independent implementation of the rule that never imports the component, compared with it on every case |
| Falsification | seeded inputs across the rule's bounds, and seeded defects in the code that each check must catch |
| A second reader | a different model's reading of every rule against its source and the code |
| Not checked | what was not checked, in plain words; never empty |

For a source whose text may not be published, such as an all-rights-reserved book, the certificate cites it and keeps
the SHA-256 of the quoted passage, not the passage. A certificate says what was checked and what was not checked. It
never says the code is correct, and it raises no result's label. No qualified person has reviewed these components.

### How do components compose?
You can call them one by one, or let `g6.harness` compose them. A harness run takes a standard (a goal and its
checks) through intake, plan, execute, adjudicate and remember. The evidence issuer writes a certificate; the
run's label is a qualified draft at best, because the harness does not re-run your checks itself.
`navigator.check_plan` checks a plan of component calls before anything runs.

### How mature are the components?
All are implemented and tested. Three have been through a study registered before it ran: grounding's claim
checking, the action gate and the meta-harness (see [Research](research.md)). None has been studied in real use.

### Which components can I call over MCP?
Every extension in the library can be called over MCP, as can 14 of the 34 core components; the other 20 are
Python-only for now. The tables above say which.

An op a component marks sensitive, because it runs a program on your computer, reaches the network, spends money,
deletes what it kept or changes your machine, is refused over MCP unless the person who runs the server lists it in
`G6_MCP_SENSITIVE`. The tables name each one under "Ops refused over MCP". From Python, they run like any other op.

### What is a bundle?
A ready-made harness for one problem, such as checking a coding agent's "fixed it", reviewing a contract or checking
GST. There are 11: 10 work today, and the compliance gap check is built and awaits its certificate. A bundle is listed
only with a test that runs it end to end offline.

## Safety

### How do I stop G6?
Set the `G6_STOP` environment variable, or put a file named `STOP` in your G6 folder. While either is there, the
harness, the coding-mission door, the safety engine, the operator loop and the improvement cycle start no new work. It
is a check, not a preemption: work already running finishes unless it polls, and code that never asks is not stopped.

### Will G6 take an action on its own?
No. `action_gating` decides; it never executes anything. An irreversible or external action without a verification
plan is blocked, and one without its coordination context goes to a person.

### Does G6 change itself?
Not by default. Learned hints (`g6.learning`) are used only after a person accepts them. The `self_training`
improvement cycle is off unless you enable it. It applies a change by itself only if you also enable applying, the
promotion gate rates the change low-risk, and you supply a rollback. A change that touches protected code (the gate,
the evaluators, the safety rules) is blocked.

### Is G6 a sandbox?
Not one to rely on alone. It runs tests and candidate code in bounded child processes, and only inside an
operating-system boundary: on Linux, bubblewrap (install the `bubblewrap` package), with a private loopback and no
other network, and only its own run folder writable; on Windows, once you have run the one-time access grant that
`g6 doctor` prints, an AppContainer: no network, and only its own run folder writable. `g6 doctor` says which you have
and what your computer needs. Where no operating-system boundary can confine it (macOS, Linux without bubblewrap, and Windows
before the one-time access grant), G6 refuses to run untrusted code for you (a patch, a generated script, a
repository's tests) unless you allow it once with g6 setup. Code you allow then has your own rights: it can read your
key file ~/.g6/.env and your other files, and its network guard works inside Python only. You are responsible for what
you run. Do not use G6 as the only security boundary in front of a real executor.

## The meta-harness

### What is the meta-harness?
The part of G6 that learns: it tunes G6 to a task you repeat, using G6's own checks and gates (`g6.metaharness`). The
other part is the components it draws on. [How it works](metaharness.md).

### Does G6 now improve itself automatically?
No. It proposes and tests changes, and nothing goes live until you approve it. The only thing it does by itself is
move back to a version you approved before, when the version in use falls behind it on live runs.

### What can it change, and what can it never change?
It can change which of your models runs each step, budgets, the prompt, learned hints, how many of your labelled
examples to show, extra checks (added, never removed), optional pipeline steps, an admitted compiled shortcut for a
step, your own code files (as an overlay it runs in a copy) and an optional LoRA adapter. It can never change your checks, the oracle, the held-out examples, the
approval rules, the stop, the code that judges a change, the actions your agent may take, or G6's own source.

### Why does a person approve every change?
Because a change can make results worse, and a measured win on examples is not the same as a win on your work. The
system that proposes a change must not also be the one that accepts it.

### How does it decide that a change is better?
It runs the old and new versions on the same held-out examples, at equal compute, and needs a clear win that more
compute alone does not explain: at least 30 examples, an exact sign test, costs within 5%, and no rise in answers it
wrongly let through. Your checks and your oracle decide each outcome; an AI judge never does.

### What happens when tuning finds nothing?
It tells you why, and what to try next. Every refusal carries a recovery plan, and `mh.diagnose()` (over MCP:
`metaharness_diagnose`) sorts your dev failures into root causes:
- too few held-out examples to confirm a real gain, with an estimate of how many would;
- runs held at intake, which only you can accept;
- answers rejected by a check;
- wrong answers of a given kind.

It maps each cause to the G6 components that address it, such as labelled examples for confused labels, or a
test-and-repair loop for code. It marks which steps the meta-harness can try itself and which only you can take. It
never reads a held-out example.

### What do I need to provide?
A task you repeat, checks you trust, about 30 or more examples kept aside with known answers, your model or models, and
a budget. Without held-out answers it can measure, but it will not recommend a change, and it says why.

### Does it train or fine-tune a model?
Your base model never changes. As an option (`pip install ".[lora]"` from a copy of the code), it can train a small LoRA adapter for a local
model you choose. The adapter is a change like any other: tested on your held-out examples, and used only after you
approve it.

### Can it learn from my live runs?
Only if you switch that on. What it learns is still a proposal that you approve, and a live run never counts as
held-out evidence.

### Can my coding agent use it? Can my agent approve a change?
Your agent can propose, measure and read decisions over MCP. It cannot approve: `metaharness_approve` asks you
directly through your client, even in the session that proposed the change. G6 records the name you type as given;
it does not check who you are.

### Can I share a tuned setup?
Yes, as a profile pack. Whoever imports it gets a candidate that must win on their own held-out examples first; the
evidence the pack claims is shown and ignored.

### Has it been shown to help?
Yes, in a worked example on one local model: on some public task sets it lifted the model by a wide margin within
minutes, and on others it found nothing it could prove and changed nothing. Where self-tuning found nothing, G6's
prebuilt components still helped, which is why G6 offers both; they are alternatives, chosen per task on your own
held-out examples. Results will vary with your task and the data you have. The figures, and how to read them, are on
[Research](research.md).

### What if my task mentions something high-stakes?
G6's intake holds a task whose text names something high-stakes, such as a payment or a deletion, until a person
accepts it. For a workflow, you accept it once, by name, as part of what you confirm (`accept_risky`). Changing that
needs a new confirmation, and it never raises a result's label.

## The project

### How can I help?
See [Contribute](contribute.md). An independent re-run of a published study, or a run of one that was registered and
never run, is the most valuable contribution.

### How do I report a security problem?
Privately, not in a public issue. See [Contribute](contribute.md#security).
