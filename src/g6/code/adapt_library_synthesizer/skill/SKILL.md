---
name: g6-adapt-library-synthesizer
description: Use when translating a paper's algorithm into a Python module and checking it against examples supplied independently by the caller.
---

# G6 adapt_library_synthesizer

Draft a spec with an injected model caller, build a Python module, and check independently supplied examples.

## When to use it

Use when a paper has enough detail to implement and a caller can supply worked examples or input/output cases.
Fetch an allowed paper URL, or pass its text directly. Review the extracted spec before implementation.

## When not to use it

Do not use without an oracle, to select arbitrary code from linked repositories, or to establish scientific
validity from a few examples. Implement small, clear algorithms by hand when that is easier to review.
No repository cloning, host pytest, report store or hidden model fallback is provided.

## Ops

- `backend_capabilities`: describes the python_module backend without a model or network.
- `extract_spec`: sensitive; extracts a schema-checked proposal from paper_text using an injected caller.
- `build`: sensitive; implements spec and generates tests from examples; kind is python_module.
- `run_checks`: sensitive; checks implementation.py against independently supplied examples in the sandbox.
- `fetch_paper`: sensitive; fetches url through the publication allow-list, then extracts text.
- `write_files`: sensitive; writes files to a destination under the person's declared roots.

Every sensitive op is dry-run by default.

## Alone and composed

From Python construct `AdaptLibrarySynthesizerBlock(caller=...)`, passing a `g6.core.llm.LLMCaller`, and use
`AdaptLibrarySynthesizerInput`. Over MCP its model ops are sensitive; when the server's owner allows them, a non-dry model
request uses the model the person set in `~/.g6/.env`, and with none it refuses honestly.
The registry/MCP name is `adapt_library_synthesizer`.

Compose fetch_paper (or supplied text), extract_spec, human review, build, run_checks, then write_files.
Model operations use only the injected caller and remain sensitive because that caller may be online or paid.
The member reads no keys and selects no provider. Local checks, reads and writes need no owner-authority claim.
No destination is assumed: the person declares roots with G6_CODE_CONFIG, and write_files needs a new or empty
destination beneath one.

An example supplies function, args, optional kwargs and expected. Values are JSON values, not expressions.
For a paper-derived example, also supply paper_quote and paper_text: G6 requires the quotation to occur verbatim.
That proves text presence only; a person must confirm that the example's interpretation and expected result agree.
No examples means `G6_E_SYNTH_NO_ORACLE` before a model call. Model-generated examples are not promoted to oracles.

A spec names inputs, outputs, steps, invariants, complexity and hyperparameters. The model returns implementation
and optionally smoke_tests. G6 records the caller and prompt identifier/input hash. It creates test_examples.py
deterministically from caller examples. Same-call model tests are recorded as smoke only and not run as evidence.
run_checks reconstructs invocations from caller examples; it sends only implementation.py and invocation inputs
into the sandbox. Expected values stay in G6 and are compared with the values returned by the child.
Request `files` cannot substitute its own test file for the oracle.

Trusted Python integrations register additional builder/checker pairs through `backends`. The built-in backend is
a single standard-library module. File maps allow at most 200 files, 1 MB per file and 8 MB total, with portable
relative paths. Names and counts are checked before encoding; file and total byte limits stop encoding at the
first exceeded limit, including dry-run writes. Paper downloads cap at 25 MiB, extracted text at 200,000 characters, and PDFs at 500 pages.
PDF parsing runs in the bounded sandbox too. Paper text and model output remain untrusted data.

## Failure modes it catches

- `unfaithful_method_code`: a wrong returned value for a supplied example blocks the result. Missing oracles,
  malformed specs, invalid Python and failed fetches also refuse.

## Blind spots

`wrong_problem`: a model may misinterpret the paper while fitting the examples.
`broke_existing_functionality`: integrating generated code can break callers outside the examples.
Passing a small example set does not establish general correctness, performance, numerical stability or fidelity
to every equation. Exact JSON equality is used; supply explicit expected rounding where relevant. PDFs may lose
equations/tables; image-only PDFs need a transcription. Text presence cannot authenticate a paper or its claims.

The sandbox is a resource bound, not an OS sandbox: same-user file access remains possible, and its socket guard
does not cover arbitrary native programs. Parent oracle comparison prevents a fabricated bare passed flag from
being evidence, but this is not an attestation system against hostile code.
Every result is `qualified-draft` at best. The first failed check is `blocked-escalated`; checks not run are listed.

## What it needs

Sensitive operations are dry-run by default. Set dry_run=false explicitly to execute from Python.
Over MCP the server operator must also allow the component or component.op in G6_MCP_SENSITIVE;
request approval or authority fields cannot grant access. Local writes, sandbox checks and network reads
do not consult the action gate. Model calls use only the injected caller. The action gate is for outward
acts such as sending, publishing and deploying, which these members do not provide.
A paid injected caller uses the person's key in their own `~/.g6/.env`, read through `g6.core.keys`, as approval to spend.

No outside program. Supply an injected `g6.core.llm` caller for extraction/build.
`g6[docs]` provides pypdf (BSD-3-Clause) for PDFs; if it is not installed, reading a PDF blocks with
`G6_E_CODE_CHECKER_NOT_INSTALLED` and `pip install "g6[docs] @ git+https://github.com/basicisabc1994/g6solver"` instructions.
Text and capability operations need no extra. Paper URLs must use an allow-listed scholarly host; every redirect
rechecks the host and core HTTP checks reject private addresses. No network service is used in examples.

## Examples

```g6-example op=backend_capabilities
{}
```

```g6-example op=extract_spec
{"paper_text":"The method sums input values.","name":"sum_values"}
```

```g6-example op=build
{"spec":{"name":"sum_values","steps":["Sum the values"]},"examples":[{"function":"sum_values","args":[[3,-2,8]],"expected":9}]}
```

```g6-example op=run_checks
{"files":{"implementation.py":"def sum_values(values): return sum(values)"},"examples":[{"function":"sum_values","args":[[3,-2,8]],"expected":9}]}
```

All sensitive examples are dry runs. Execution requires dry_run=false and, over MCP, server-operator allowance.

## The four open problems

- **Learning signals**: replies name checks that failed and their evidence, so a person can group failures by generator
  and prompt. Nothing learns from these events or stores a score.
- **Evaluators the model cannot influence**: G6 selects the fixed checkers; model claims cannot raise a label.
  Parser and numeric checks are independent of prose claims. Executed code still shares the sandbox user's rights;
  this is not protection against deliberately hostile code.
- **Transfer by failure mode**: the shared Artefact and CheckResult protocol gives each backend the same bounds,
  check accounting and refusal rule; passing one backend says nothing about another.
- **Enough held-out cases**: no broad held-out benchmark is claimed. Supply separate examples and properties from
  your domain, including boundary and failure cases, before relying on a result.
