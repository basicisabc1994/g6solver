# G6: Callable algorithm functionals and verification boundary

The bundled ontology indexes 22 algorithm concepts. The fixed host registry
`g6.algorithm_design.invocation` now lists those 22 names, though three
legacy kernels continue to execute through `g6.algorithm_design.kernels.invoke_kernel`.
The other 19 are callable with `invocation.invoke(algorithm_id, parameters)`.

For example:

```python
from g6.algorithm_design.invocation import invoke, list_kernels
print([item.algorithm_id for item in list_kernels()])
receipt = invoke("edmonds_karp", {
    "n": 4,
    "edges": [(0,1,3),(0,2,2),(1,3,2),(2,3,3),(1,2,1)]
})
assert receipt.output == 5
print(receipt.verification, receipt.witness)
```

**Epistemic scope:** Each kernel validates its preconditions and produces a
witness or a check of that particular output. Flow/cut equality and shortest-path
potential inequalities are independently checkable optimality certificates.
Other algorithms use independent bounded oracles, recurrence comparison, or
reference established mathematical theorems. These are **not machine-checked
proofs of the Python source for all legal inputs**. Approximation algorithms
retain their approximation qualification; pseudo-polynomial and exponential
algorithms retain their input-size qualifications.

The autonomous agent sees which fixed kernels are callable in `AgentBrief`.
It may propose identifiers and typed arguments, but cannot change trusted
kernel source, verification code, resource budgets or frozen obligations.
Executing model-generated Python or supplying an arbitrary higher-order
callable remains outside the host registry; that requires the existing isolated
candidate runner plus independent checked evidence.

`verified_kernels.py` provides 19 additional bounded implementations:
BFS, Dijkstra, Bellman–Ford, Kahn, weighted-interval DP, maximum flow,
minimum spanning forest, Gale–Shapley, vertex-cover FPT and 2-approximation,
forest and tree-decomposition independent set, greedy set cover, MITM subset
sum, 0/1 knapsack, sequence alignment, Huffman coding, Held–Karp TSP,
randomized quicksort.

**Limitations / before release**

- Run all repository regression tests and import-linter, not just the new
  isolated kernel tests; GitHub CI must complete.
- The registry does not certify agent-generated replacement implementations.
- Source-contract proof requires separately specified trusted semantics and
  independently checked proof objects or formal refinement.
- Check packaging and installed-wheel resource inclusion for bundled JSON.
- Review recursion bounds, pathological resource use and kernel-specific
  performance ceilings under the actual supported execution environment.
- PR remains draft; no deployment or promotion is authorised.
