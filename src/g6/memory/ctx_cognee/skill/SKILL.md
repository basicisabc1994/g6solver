---
name: g6-ctx-cognee
description: Use when facts an agent needs are spread across several texts with nothing linking them, and the person runs a Cognee server, to add the texts to it, search it, and follow the chain of links Cognee's graph draws from a fact in one text to a related fact in another, each result untrusted and cited as far as Cognee allows.
---

# G6 Cognee graph memory

Uses the Cognee server you run on this machine as a graph memory: add text to a dataset, have Cognee extract the
entities and the links between them, search what it holds, and follow the chain of links joining two entities.
G6 talks to Cognee over its HTTP API and never imports, installs or stands in for it.

## When to use it

- Facts about one thing are spread across many notes, tickets or documents, and an agent needs the links between
  them ("who maintains the service that runs in Leeds?").
- You already run Cognee, with the model provider you chose for it.
- To delete one item from a Cognee dataset, check it is no longer listed, and see what the dataset's graph still
  holds of it.

## When not to use it

- Without a Cognee server: every op refuses with the install steps; nothing stands in for it.
- When the text must not leave this machine and Cognee is set up with an online model provider: Cognee sends what
  it is given to that provider.
- For notes found again by their words, with their writer and age: that is `ctx_ace`, which needs nothing outside G6.
- To decide whether a link or an answer is true: Cognee's model drew it; check it against the text (`g6.grounding`).

## Ops

- `status`: whether your Cognee answers, its version and its health (sensitive).
- `datasets`: the datasets your Cognee holds, or the items in one (`dataset_id`) (sensitive).
- `add`: send text to a dataset and have Cognee build its graph; a dry run (the default) sends nothing and says what would be sent (sensitive).
- `search`: what Cognee finds, as untrusted text citing its dataset and chunk; answers its model wrote are marked `model_generated`, and a `*_COMPLETION` search, which has its model write one, is a dry run (the default) unless `dry_run` is false (sensitive).
- `graph`: the entities and links Cognee extracted for a dataset, and the chain of links joining two of them (`link_from`, `link_to`) (sensitive).
- `forget`: delete one item from a dataset, list the dataset again to check it is no longer listed and read what the dataset's graph still holds of it; a dry run (the default) deletes nothing (sensitive).

## Reading the result

Every answer is `qualified-draft` at best. A search hit is `"trust": "untrusted"` with its `text`, `dataset`,
`cites` (Cognee's chunk id and index when it gives them) and `provenance`: `retrieved` for a chunk Cognee stored from
what was added (`CHUNKS`, `CHUNKS_LEXICAL`), `model_generated` for a summary or an answer Cognee's model wrote
(`SUMMARIES` and the `*_COMPLETION` types), which cites no passage. Cognee's Cypher and natural-language search types,
which run a query against its graph store, are refused. An empty result is said not to prove absence: Cognee answers
a search of a dataset it will not let you read with an empty list too.

`graph` returns `nodes`, `edges` (`from`, `relation`, `to`) and `dangling_edges` (links to an entity the graph does not
hold, dropped); with `link_from` and `link_to` (entity labels, any case) it adds `chain`, the shortest run of links
between them, each step `followed` `as written` or `the other way`, or `null` when no chain joins them. A label that
names more than one entity is `G6_E_MEMORY_COGNEE_AMBIGUOUS`, listing their ids: G6 does not choose one.

`add`, `forget` and a `*_COMPLETION` search with `dry_run` true (the default) answer `G6_E_MEMORY_DRY_RUN` with
`would` (where the text would go, how many characters, its SHA-256) and `gate_would`, what the action gate decides on
it now. With `dry_run` false, each is first declared to `g6.action_gating` as an API call, with the checks G6 makes
afterwards as its verification contract; it is sent only on `allow` or `allow_with_notice`, which the gate gives when
your key file holds your Cognee token (`G6_E_KEY_FILE_APPROVED`), and the decision is in the evidence
(`"kind": "action_gate"`). A review or a block is `G6_E_MEMORY_COGNEE_HELD`, `blocked-escalated`, with nothing sent and
the line a person adds to allow it. A plain search (`CHUNKS`, `CHUNKS_LEXICAL`, `SUMMARIES`) is sent as it is.

Sent for real, `add` answers `G6_E_MEMORY_COGNEE_ADDED` only on evidence: the add's reply and the cognify's reply each
hold a finished run (`PipelineRunCompleted` or `PipelineRunAlreadyCompleted`) for that dataset, and the dataset's items
listed afterwards include the item named for the text (`g6-` and the first 16 hex digits of its SHA-256), given back
as `item`, with `new_items` and the run statuses in `cognify`. An empty reply, a run still going, a failed run, a run
for another dataset or a missing item is `G6_E_MEMORY_COGNEE_NOT_BUILT`, `blocked-escalated`, saying what was already
sent. `forget` lists the items again and reads the dataset's graph, and answers `G6_E_MEMORY_COGNEE_FORGOTTEN` with a
receipt: what was checked, and `graph_still_holds`, the nodes that still carry the item's id and those linked to one
(an entity Cognee drew from the item that no longer links to it is not counted). Cognee answering the delete but
still listing the item is `G6_E_MEMORY_COGNEE_FORGET_NOT_DONE`; a listing afterwards that fails or answers 404 is
`G6_E_MEMORY_COGNEE_FORGET_UNCHECKED`, saying the delete was sent: G6 could not check it.

Refusals are `blocked-escalated`: `G6_E_OUTSIDE_PROGRAM_MISSING` (nothing answers; the install steps follow),
`G6_E_OUTSIDE_SERVICE_STATUS` (an error status, or a redirect, which is never followed; a 401 or 403 says how to give
G6 your Cognee token, or that the one in your key file was refused), `G6_E_OUTSIDE_SERVICE_NOT_LOCAL`,
`G6_E_OUTSIDE_SERVICE_TIMEOUT`, `G6_E_MEMORY_COGNEE_REPLY`
(an answer that is not what Cognee's API returns), `G6_E_MEMORY_NOT_FOUND`, `G6_E_MEMORY_NO_TEXT`,
`G6_E_MEMORY_NO_QUERY`, `G6_E_MEMORY_NO_TARGET`, `G6_E_MEMORY_NO_REASON`, `G6_E_MEMORY_COGNEE_HELD`,
`G6_E_MEMORY_COGNEE_NOT_BUILT`, `G6_E_MEMORY_COGNEE_FORGET_NOT_DONE`, `G6_E_MEMORY_COGNEE_FORGET_UNCHECKED` and
`G6_E_MEMORY_COGNEE_AMBIGUOUS`.

## Alone and composed

Every op talks to your Cognee, so every one is sensitive: it runs from Python, and over MCP only when the person who
runs the server lists it (or `ctx_cognee`) in `G6_MCP_SENSITIVE`. Then:
`g6_invoke("ctx_cognee", "add", {"text": "...", "dataset": "ops", "dry_run": false})`, `g6_invoke("ctx_cognee",
"datasets", {})` for the dataset's id, and `g6_invoke("ctx_cognee", "graph", {"dataset_id": "...", "link_from": "...",
"link_to": "..."})`.

From Python: `get_registry().invoke("ctx_cognee", op, params)`. The address is `http://127.0.0.1:8000`, or the local
address in `G6_COGNEE_URL` where G6 runs; nothing off this machine is reached.

Composed: `knowledge_converter` turns a document into text, `add` gives it to Cognee, `graph` finds how two facts are
linked, and `search` with `CHUNKS` returns the stored text behind a link, which `g6.grounding.check_claim` can judge.

## Failure modes it catches

- `unlinked_facts` (facts spread across text with no links between them): `graph` returns the links Cognee's graph
  draws between entities across every text in a dataset, keeps only links whose two ends are entities in that graph,
  and finds the shortest chain joining two entities, following a link either way, so a fact in one text is followed
  to a related fact in another.

## Blind spots

- `provenance_missing`: a link, a summary or a completion is Cognee's model's output and cites no passage; only a
  `CHUNKS` hit carries Cognee's chunk id.
- `fabricated_fact`: Cognee's model may draw a link or write an answer the text does not support, or merge two
  entities with the same name; G6 does not check either against the text.
- What leaves the machine is whatever Cognee is set up to send: the text to its model provider, and usage data unless
  its `TELEMETRY_DISABLED` is set. G6 cannot see Cognee's settings.
- With Cognee's sign-in off, any program on this machine, and plausibly a web page open in your browser through a
  cross-site form, can read and write your datasets; G6 cannot stop that. Each answer's evidence (`sign_in`) says
  whether G6 sent your token or found sign-in off.
- `forget` checks that the item is no longer listed and reports what the graph still holds that carries its id or
  links to a node that does; it cannot tell which entities Cognee drew from that item alone, so it does not say they
  are gone.
- Entities are matched by their label, in any case: when Cognee keeps two with one label, `graph` reports both and
  draws no chain; when it merged two different people with one name into one entity, G6 cannot tell.

## What it needs

Cognee, a program you install and run yourself (Apache-2.0; it requires litellm, which G6 never uses or ships): in
its own Python environment, `pip install cognee`, set its model provider (a local one keeps your text here), and start
its HTTP API on this machine with `HTTP_API_HOST=127.0.0.1` and `TELEMETRY_DISABLED=1` in its environment, keeping its
sign-in on: `python -m cognee.api.client`. Sign in to your Cognee (its `/api/v1/auth/login` route; its API pages are
at `/docs`) and put the token it gives you in your key file, `~/.g6/.env`, as `COGNEE_API_TOKEN=<token>`: G6 reads it
from nowhere else, sends it only to that Cognee, and reads it as your approval of what ctx_cognee sends there. Nothing
else outside G6.

## Examples

```g6-example op=add
{"text": "Ada Lovelace maintains the billing service.", "dataset": "ops"}
```

A dry run: nothing is sent; the reply says what would go to your Cognee. Add `"dry_run": false` to send it.

```g6-example op=datasets
{}
```

```g6-example op=graph
{"dataset_id": "6f1c2e7a-0d4b-4c38-9a51-2b7e8f3d9c10", "link_from": "Ada Lovelace", "link_to": "Leeds data centre"}
```

The reply's `chain` is the run of links joining the two entities, each step `as written` or `the other way`.

```g6-example op=search
{"query": "who maintains the billing service", "search_type": "CHUNKS", "top_k": 5}
```

```python
from g6.core.registry import get_registry
from g6.memory.ctx_cognee import CogneeInput

assert CogneeInput(op="add").dry_run is True          # nothing is sent unless asked
out = get_registry().invoke("ctx_cognee", "status", {}).unwrap()
# with no Cognee answering on this machine, the honest refusal and the install steps:
assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_OUTSIDE_PROGRAM_MISSING"
assert "pip install cognee" in out["reason"]
```

## The four open problems

- **Learning signals**: each reply records what Cognee returned, how many links were dropped, the items listed
  before and after an add or a forget, and the search type. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the checks G6 makes (that a link's ends are in the graph, the chain
  search, the before-and-after listings) are fixed code over Cognee's answers; Cognee's own extraction is a model's,
  and G6 does not judge it.
- **Transfer by failure mode**: bound to `unlinked_facts`, and to what every Cognee dataset has (entities, links,
  items), whatever the subject.
- **Enough held-out cases**: Not yet: its tests run against a stand-in for Cognee's API; it has not been measured on
  a real Cognee or real text.
