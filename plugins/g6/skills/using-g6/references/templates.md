# One valid instance of each template

Each line below is one template instance that G6's review accepts, as `template_instances` takes it. The names
(`shop.prices.total` and the others) are an example project's: put the dotted names of the functions in your
repository, and the `id` and `requirement_id` of your review. Every binding a template needs is in its line; a
refusal names the binding that is missing or misnamed, and its warning card gives the template's bindings and its
line from here.

- `example.v1`: a stated input gives a stated result (`returns`), error (`raises`) or effect (`effect`).
- `relation.v1`: `relation` is `round_trip` (with `inverse`), `idempotence`, `invariance` (with `transformation`) or
  `monotonicity` (`inputs` in ascending order, and optionally `order` and `strict`); each input is the function's one
  argument.
- `access_rule.v1`: at least one allowed and one refused case; `refusal` is `exception` (name it in `exception`) or
  `falsy_return`.
- `differential.v1`: `mode` is `outputs` or `exceptions`; the calls lie outside the change's scope.
- `forbidden_dependency.v1`: a stated contract (`layers`, highest first, or `forbidden` pairs) needs `package`, or
  `"contract": "repository"` uses the repository's own import-linter contract.
- `state_invariant.v1`: the model is in the model checker's closed grammar; `action` names the Int variable whose
  value says which action led to each step, `actions` maps each value to an operation call, and `observe` reads each
  variable on the real state.

```json
{"id": "E1", "template": "example.v1", "requirement_id": "R1", "intent": "change", "bindings": {"function": "shop.prices.total", "cases": [{"args": [[2, 3]], "returns": 5}]}}
{"id": "E2", "template": "relation.v1", "requirement_id": "R1", "intent": "preserve", "bindings": {"relation": "round_trip", "function": "shop.codec.encode", "inverse": "shop.codec.decode", "inputs": ["a", "b\nc"]}}
{"id": "E3", "template": "access_rule.v1", "requirement_id": "R1", "intent": "change", "bindings": {"operation": "shop.notes.read_note", "refusal": "exception", "exception": "PermissionError", "cases": [{"who": "owner", "resource": "note 1", "args": ["owner", 1], "expected": "allowed"}, {"who": "guest", "resource": "note 1", "args": ["guest", 1], "expected": "refused"}]}}
{"id": "E4", "template": "differential.v1", "requirement_id": "R1", "intent": "preserve", "bindings": {"function": "shop.urls.normalise", "mode": "outputs", "calls": [{"args": ["/a/b"]}, {"args": ["/a/"]}]}}
{"id": "E5", "template": "forbidden_dependency.v1", "requirement_id": "R1", "intent": "preserve", "bindings": {"package": "shop", "contract": {"forbidden": [{"source": "shop.core", "forbidden": "shop.web"}]}}}
{"id": "E6", "template": "state_invariant.v1", "requirement_id": "R1", "intent": "preserve", "bindings": {"constructor": {"call": "shop.wallet.new_wallet"}, "model": {"variables": {"bal": "Int", "act": "Int"}, "init": "(and (= bal 0) (= act 0))", "transition": "(or (and (= next_act 1) (= next_bal (+ bal 5))) (and (= next_act 2) (= next_bal (- bal 3))))", "invariants": {"never_negative": "(>= bal 0)"}}, "action": "act", "actions": {"1": {"call": "shop.wallet.deposit", "args": [5]}, "2": {"call": "shop.wallet.withdraw", "args": [3]}}, "observe": {"bal": {"call": "shop.wallet.balance"}}}}
```
