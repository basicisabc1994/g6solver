# Program graphs

`g6.distill.graph_records` contains the graph data contracts used by the v3.1 executor. Records forbid extra fields and bind identities to canonical content. `ProgramGraph` checks node identities, rooted DAG structure, ports, vote thresholds, iteration bounds and total declared resources. Cross-record checks in `graph_lowering.validated` bind primitive identities, type digests and effect allowances. Type digests establish compatibility of declared ports; they do not validate payloads against a schema registry.

`g6.distill.graph.lower(graph, primitives, **bindings)` delegates to the adapters in `graph_lowering`. `primitives` maps content ids to `Primitive` records.

| Target | Binding and supported shape |
| --- | --- |
| composition | `entries`: admitted `LibraryEntry` snapshot by id; direct entry leaves under a Vote/Cascade root; no extra primitive scope, preconditions or effects |
| method_plan, compose, procedure | Raise `NotImplementedError`: broad lowering to `MethodPlan`, `compose` and `ProcedureSpec` is deferred until after M1. |

`Composition` is the only implemented lowering target. `native` selects direct execution and is refused by `lower`.

Vote thresholds of one and of the child count lower to `any` and `all`; interior thresholds lower to `k_of_n`. Edge order is child order. The resulting `Composition` uses the existing schema and serialization. Entry execution goes through `execution.run_candidate`, including its scope and isolation rules. No admission decision is made here.

`graph.execute(graph, x, *, primitives, unit_ref, split, request_id, g6_commit, seed=0, ...)` returns an `ExecutionRecord`. `x` is JSON data or an `execution.Observation`. Observations expose before/after files and facts, never their expected label. Splits outside training, dev, monitor and field raise `ExecutionRefused` with code `G6_E_GRAPH_SPLIT` and a message naming the split, before validation or input access.

Native execution supports Vote, Cascade, Guard, Seq and Par. Branch, Loop, CEGIS, probes and subgraphs without admitted-entry bindings refuse until their runtime adapters exist. Par children run serially and return an ordered aggregate; this slice provides no concurrency. Only read effects and state-free/read-only primitives are allowed. `StopRule.abstain_on` is retained as metadata; failure terminals are preserved instead of being remapped to abstention. Guard, Cascade and Vote are lazy: unused nodes have no run row. Vote stops once concern, no_concern or abstain is fixed for all remaining children. Structural credit needs every leaf verdict; missing skipped leaves cause refusal.

Code, solver and component nodes use a caller-owned `kernels` mapping from `impl_ref` to a trusted callable:

```python
def check(primitive, inputs, *, seed, timeout_s):
    # inputs contains unit data and named producer ports.
    return KernelResult(output="concern", cost=CostVector())
```

Kernels must honour the supplied deadline and account for non-time resources other than model calls. Model calls come from the scoped send counter. The executor adds measured CPU and wall time, checks both primitive and node bounds, and checks the total runtime ceiling. A synchronous caller cannot be forcibly interrupted here: late outputs are discarded. Budget excess yields `refused`; an earlier stop deadline yields `timeout`. Failures and timeouts carry no output and cannot be hidden by a parent vote that reaches them. In verdict graphs, abstention always propagates as the `abstain` verdict.

`attempt_meter(what, update_id=None)` exposes a reusable context manager. Its `cost` accepts reported resource use and its `outcome` defaults to `screened`; an escaping exception sets `failed` and still creates `meter.record` before propagating. The resulting `AttemptRecord` retains measured wall time, CPU time, model sends and timestamps. Each started kernel attempt is retained in `ExecutionRecord.attempts`: completed assessments use `screened`, failures, timeouts and binding refusals use `failed`, and resource overruns use `budget_exhausted`. Cache hits and nodes skipped or refused before kernel dispatch have no kernel attempt. Attempt totals and node totals must agree with the execution cost. An outer send counter checks that booked calls equal the sends noted for the execution.

LLM nodes require an explicit `caller` and an `LLMConfig` by primitive id in `llm_configs`. `impl_ref` must equal `model:<model_id>` and `impl_version` must equal the config's `content_id`. The caller owns its actual model, sampling configuration and transport timeout; this binding does not configure a provider. Seed and config enter replay identity. The caller returns a plain verdict, JSON verdict string, or a JSON object containing `verdict`; value graphs accept JSON values. Raw replies are trace data. Text UTF-8 byte counts are charged conservatively as tokens. Each node permits one transport send, including failed sends; noted hidden retries are refused. Custom callers must report transport sends through `core.model_calls`; an unnoted successful reply is refused. A failure before any noted send books zero calls and retains its measured time. The executor imports only `LLMError` and `counting` from the model modules; the caller stays injected.

`ReplayCache` is an explicit, process-local snapshot. It keys on the primitive contract and implementation, node input, upstream implementation fingerprints, config and seed. A node edit invalidates that node and its descendants, including descendants whose upstream value happened to stay the same. Other nodes replay at zero fresh cost. Only successful state-free deterministic/seeded nodes and recorded LLM outputs are cached; failures and abstentions are not cached. Callers must update implementation digests when trusted kernel code changes.

Pass an `ExecutionStore` to retain rows under `$G6_HOME/distill/exec/runs.jsonl`. It uses `ChainLedger`, keyed by run content id, and stores traces under `exec/traces/<digest>.json`. Trace objects are checked on ledger reads. Without a trace-capable store, trace references are omitted. A retained external head can detect tail changes; a local unanchored chain does not authenticate its writer. The supported write model is one writer per store.

`structural_blame(graph, leaf_verdicts)` computes the A0 critical leaf ids for Vote/Cascade graphs. It propagates all single-leaf substitutions together using bit sets, evaluating each combinator once, with no kernels or caller sends. This identifies sensitivity of the recorded outcome; it makes no claim that another primitive can attain the substituted verdict.
