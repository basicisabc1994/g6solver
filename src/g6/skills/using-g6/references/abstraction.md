# Abstraction: the separate server's tools

These tools exist only where the person registered G6's second MCP server, `python -P -m g6.abstraction.server`. It is
not part of `g6-mcp`: if your client does not list the tools below, they are absent, and `g6_describe("abstraction")`
gives the Python API instead. The component's own skill says when an abstraction of a design space is worth
searching for, and what its statuses mean.

The order of work, as the server's own instructions give it:

1. `load_dataset` registers a set of evaluated configurations; `describe_domain` and `list_combinators` show what
   there is to build from.
2. `propose_abstraction` takes a specification and answers with the gate's feedback; repeat until it is accepted.
3. `evaluate_abstraction` scores it; `find_counterexamples` and `refine_abstraction` look for where it fails and
   repair it; `tighten_abstraction` tightens one with unused slack.
4. `leaderboard` ranks what stands. `advise_pruning` says whether fresh candidates may be dropped, and its guards
   can refuse.
5. `record_evaluation` feeds each new result back; `revalidate` re-checks an entry that went stale;
   `lift_abstraction` reuses a mechanism on the search over abstractions itself.
6. `diagnose` audits the harness's own decisions and names next calls; `self_debug` runs that audit and its
   remedies for a few rounds.
7. `save_state` and `load_state` keep the library between sessions.

The file tools (`load_dataset`, `save_state`, `load_state`) read or write a file on the person's machine. They are
refused unless the person who runs the server allows them, and every path stays inside the directory that person
names. You can set neither.

A status of `checked` means the gate's own checks passed on the recorded trace. It is not a claim that the
abstraction holds on configurations nobody evaluated.
