You are a coding agent (like Claude Code) working in a user's repository. An MCP server named `g6` is connected. Its
tools, exactly as your client lists them:

- `g6_help()`: How to use G6 from an agent. Call this first.
- `g6_components()`: Every installed G6 component.
- `g6_find(query, limit=5)`: Rank components for a task description; empty list means nothing matched.
- `g6_describe(component)`: Maturity, ops and declared dependencies of one component, and which of its sensitive ops this server allows.
- `g6_invoke(component, op, params_json="{}")`: Run one op of a component. params_json is a JSON object of the op's parameters. The reply carries completion_state (verified / qualified-draft / blocked-escalated) and a warning_card: read them.
- `metaharness_inspect(workflow)`: A declared workflow's versions, confirmations, evaluations and decisions.
- `metaharness_propose(workflow, profile_json="", proposer="")`: A candidate profile, or with none, the declared search space's candidates. A candidate changes nothing until it is evaluated, decided and approved by a person.
- `metaharness_evaluate(workflow, proposal_digest)`: Measure a candidate against the active version on the held-out split (paired, compute-matched).
- `metaharness_decide(workflow, report_digest, proposal_digest)`: The meta-gate's decision: recommend (qualified-draft at best), refuse or block, with a G6_E_META_* code. A decision that is not recommend carries recovery: why, and what can be done next. Tell your user.
- `metaharness_diagnose(workflow)`: Recovery and root cause: why the active version fails on the dev split, and what can be done next. Changes nothing.
- `metaharness_approve(workflow, decision_digest)`: Present digest-bound client-mediated consent, including in the proposing session. G6 records the supplied name; it does not authenticate human identity.
- `metaharness_rollback(workflow, version)`: Ask the person, through the client, to move back to a version they approved.

The `g6` Python package is also installed in the repository's environment.
