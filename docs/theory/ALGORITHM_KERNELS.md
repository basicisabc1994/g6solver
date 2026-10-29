# G6's algorithm kernels and what their checks cover

**Status:** Early alpha. Individual capabilities have been tested; general application-level improvements have not yet been established.

The bundled ontology indexes 22 algorithm concepts. Each has a fixed kernel in G6, and all 22 run through one entry
point, `g6.algorithm_design.invocation.invoke(algorithm_id, parameters)`; over MCP the same path is
`g6_invoke("algorithm_design", "run_kernel", ...)`, and `list_kernels` names them. Nineteen kernels are in
`checked_kernels.py`; the first three (`interval_earliest_finish`, `subset_sum_dp`, `merge_sort`) are in `kernels.py`
and answer through the same path, as the same `CheckedResult`.

For example:

```python
from g6.algorithm_design.invocation import invoke, list_kernels
print([item.algorithm_id for item in list_kernels()])
result = invoke("edmonds_karp", {
    "n": 4,
    "edges": [(0,1,3),(0,2,2),(1,3,2),(2,3,3),(1,2,1)]
})
assert result.output == 5
print(result.verification, result.witness)
```

**What a check covers.** Each kernel validates its preconditions and produces a witness or a check of that particular
output. Flow/cut equality and shortest-path potential inequalities are independently checkable optimality
certificates. Other kernels use independent bounded oracles, recurrence comparison, or cite established mathematical
theorems. These are **not machine-checked proofs of the Python source for all legal inputs**. Approximation algorithms
keep their approximation qualification; pseudo-polynomial and exponential algorithms keep their input-size
qualifications.

**What a caller may send.** Parameters are plain data: None, booleans, numbers, text, and lists and mappings of them,
at most eight deep. A callable, an object or anything else is refused (`ContractError`) before a kernel runs, so a
model or an agent can choose a kernel and its arguments but never supply code, change a kernel, its checks or its
bounds. An identifier that names no kernel, or input past a kernel's bound, is refused with `KernelUnavailable`, a
`ContractError`. Executing model-generated Python remains outside this path; that needs the confined candidate runner
and independently checked evidence.

The nineteen kernels in `checked_kernels.py`: BFS, Dijkstra, Bellman–Ford, Kahn, weighted-interval DP, maximum flow,
minimum spanning forest, Gale–Shapley, vertex-cover FPT and 2-approximation, forest and tree-decomposition independent
set, greedy set cover, MITM subset sum, 0/1 knapsack, sequence alignment, Huffman coding, Held–Karp TSP and randomized
quicksort.

**Limits**

- The kernels do not cover agent-generated replacement implementations.
- A proof about the source needs separately specified trusted semantics and independently checked proof objects or
  formal refinement.
- Recursion bounds, pathological resource use and kernel-specific performance ceilings are reviewed only for the
  bounds each kernel states.
