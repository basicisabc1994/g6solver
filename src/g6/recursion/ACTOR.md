# Learner actor episodes

`ports.ActorCaller` subclasses `core.llm.OpenAICompatibleCaller`. It defaults to
`gpt-oss:20b` at `http://127.0.0.1:11434/v1`, sends OpenAI function tools and
reasoning effort, and accepts only numeric loopback HTTP endpoints. It reads no
credentials and chooses no other provider. Tests use `FakeLLM` responses or a
loopback HTTP stand-in.

`run_procedure(task, spec, tools=..., actor=..., recorder=..., seed=..., budget=...)`
projects the S1 `TaskSpec` to a frozen `PublicTask`. This copy has no hidden
reference or back-reference to the original task. Packets use `build_packet`
and the existing pruning implementation with an empty retriever. Each packet's
`metadata.provenance` contains `task_id`, `source`, `licence_id`, the original
S1 `content_digest`, `model_id` and `adapter_id` (null for the base model).

Procedure specs are JSON data. `ProcedureSpec.from_content(json_text)`
accepts the content of a versioned `align_prompt_library` spec; the interpreter
does not import or execute procedure code. Unknown fields and operations are
refused. Prompt strings are literal, and template expressions are refused.

```json
{
  "steps": [{"op": "actor", "prompt": "Solve using public observations."}],
  "tools": ["run_tests", "formal", "datalog"],
  "search_width": 1,
  "repair_budget": 1,
  "packet_tokens": 1024,
  "stop_rules": ["final"],
  "reasoning_effort": "low"
}
```

Actor steps continue after tool calls. Explicit tool steps have `op: "tool"`,
`tool` and `arguments`; their order is the spec's order. The `tools` list is
the ordered allow-list shown to the model. `final` stops when an assistant
submits an answer; `failure` stops the current candidate at a failure. Without
the latter, repairs can consume `repair_budget`. Search candidates share the
episode budget and use successive seeds. A completed attempt records a
submission, without assigning a correctness grade.

`EpisodeBudget` caps requests, tool calls, output reservations and tokens per
request. `Attempt.output_tokens` is the charged reservation: each request
consumes its maximum output allowance, even when the response omits usage.
It is not a measured token count. Oversize tasks stop before a request.
Recent observations survive pruning in complete assistant/tool groups.

`ToolPort.standard(task=original_task, repo=absolute_public_repo)` wraps S1's
public-check runner, formal solve/contract, Datalog, CEGIS and static plan checks.
Tools cannot be registered by a procedure. Before public tests, the trusted
adapter checks the original reference location against the repository and the
selected interpreter's readable roots. An opaque nonempty reference, overlapping
roots, or a public projection supplied instead of the original boundary input
refuses execution. The check repeats at invocation. The S1 runner receives a
fresh task containing public checks and an empty reference, with OS isolation
required and network disabled. Keep evaluator data outside all readable roots;
the adapter does not copy, open or move reference content. Repository content
provided to the actor's runner must be public task content.

All model calls in a batch are parsed and their argument schemas checked before
any tool runs. Engine refusals and failed public tests stay failures. Failed
observations enter repair packets. Exceptions from trusted injected handlers
become stable codes without exception text.

`EpisodeRecorder(episode_id=..., path=...)` wraps `ChainLedger`. The optional
path's parent must already exist. Omit the path for an in-memory recorder.
It retains the spec and budgets, packets, exact requests, responses, tool calls,
observations, failure codes and terminal attempt. Returned rows are detached
copies. A supplied path can be reopened for the same episode; this is event
storage, not an automatic resume driver.
