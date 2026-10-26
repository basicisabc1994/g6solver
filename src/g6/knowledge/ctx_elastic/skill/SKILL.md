---
name: g6-ctx-elastic
description: Use when an agent must search, read or count documents in an Elasticsearch server the person runs, over fields the index's mapping really holds, so a query that cannot match is refused instead of answered with no hits, a partial answer says so, and every hit comes back as untrusted text with its address on the server.
---

# G6 Elasticsearch reader

Reads the Elasticsearch you run on this machine: searches chosen fields with exact filters, gets one document,
counts and reads the mapping. Before a query is sent, every field and filter is checked against the index's mapping,
because Elasticsearch answers a query on a field it does not hold with no hits rather than an error. Nothing is ever
written to the server.

## When to use it

- An agent needs documents from an Elasticsearch index you run (a handbook, tickets, logs) and must cite them.
- You are not sure which fields an index holds: `mapping` lists them, and `search` refuses a field the index does
  not hold, an exact filter on analysed text and a field inside a nested object, each with what would work.
- To use the index as a `g6.grounding` retriever from Python (`ElasticIndex`).

## When not to use it

- To write, delete, reindex or administer an index: nothing here writes, by design. Use your own tools.
- For a natural-language question turned into a query by a model: the query is built here from your words, fields
  and filters, never from query JSON.
- For passages you hold yourself: `knowledge_indexer` keeps them under G6's state folder.

## Ops

- `search`: search(index, query, fields, filters, top_k): the caller's words over chosen fields (every text field when none is named) with exact filters, each field checked against the mapping first; hits as untrusted passages with es_bm25 scores and the server's own count. Sensitive.
- `get_doc`: get_doc(index, id): one document's _source, as untrusted text with its address on the server. Sensitive.
- `count`: count(index, query, fields, filters): how many documents match (all of them with no query). Sensitive.
- `mapping`: mapping(index): the index's fields and their types, so a search names fields it holds. Sensitive.

## Reading the result

- `G6_E_KNOWLEDGE_ELASTIC_SEARCHED` (`qualified-draft`): `hits` (`rank`, `score`, `score_scale` `es_bm25`,
  `passage`: the chosen fields' text, its `source` the document's address on your server and the hash of its
  `_source`), `total` and `total_relation` (the server's count), `fields` searched and `complete`.
  `G6_E_KNOWLEDGE_ELASTIC_PARTIAL` (`qualified-draft`): some shards failed or the search timed out on the server, so
  the hits and the count are not the whole of the index. `G6_E_KNOWLEDGE_ELASTIC_NO_MATCH` (`qualified-draft`): the
  server answered with no match.
- Refusals, each `blocked-escalated`: `G6_E_OUTSIDE_PROGRAM_MISSING` (nothing answering, with how to run it),
  `G6_E_KNOWLEDGE_ELASTIC_DENIED` (401 or 403), `_NOT_FOUND` (no such index or document), `_REJECTED` (the server
  refused the request), `_SERVER_ERROR`, `_TIMEOUT`, `_REDIRECTED` (never followed), `_PARSE_FAILED` (a reply that
  is not Elasticsearch's, or hits with none of the fields' text), `_COUNT_PARTIAL` (a count whose shards did not all
  answer: what they counted is in the evidence, never as `count`), `G6_E_KNOWLEDGE_FIELD_NOT_IN_MAPPING`,
  `G6_E_KNOWLEDGE_FILTER_ON_TEXT`, `G6_E_KNOWLEDGE_NESTED_FIELD`, `G6_E_KNOWLEDGE_NO_TEXT_FIELDS` and
  `G6_E_KNOWLEDGE_ELASTIC_INPUT` (an index name that is a pattern, a list, a path or a hidden index).

## Alone and composed

Over MCP each op runs only when the person who runs the server lists it in `G6_MCP_SENSITIVE`:
`g6_invoke("ctx_elastic", "mapping", {"index": "handbook"})`, then `g6_invoke("ctx_elastic", "search", {"index":
"handbook", "query": "parental leave", "fields": ["title", "body"], "filters": {"team": "hr"}})`. There is no field
for a host, a key or a query in Elasticsearch's own language.

From Python: `from g6.knowledge.ctx_elastic import ElasticIndex`; `ElasticIndex("handbook", fields=("title",
"body"))` has `find` (a `Searched`) and `search` (the `g6.grounding` retriever); it is also the `elastic` backend of
`g6.knowledge._index.index_backends`, read only. `g6.knowledge.ctx_elastic.elastic` has `search`, `get_doc`, `count`
and `mapping`, each taking `caller=` (a stand-in for `g6.core.outside.call_service` in your own tests).

Composed: `g6.grounding` checks a claim against the hits; `knowledge_manifest` records a hit's passage and re-checks
it later against the document as `get_doc` reads it again.

## Failure modes it catches

- `under_retrieval` (relevant passages that retrieval never finds): a field the mapping does not hold, an exact
  filter on analysed text (which matches only one lower-cased word) and a field inside a nested object are refused
  before the query is sent, with the fields that would work; a reply from shards that did not all answer is partial,
  and a failed request is never "no match".

## Blind spots

- `prompt_injection`: a hit is outside text and can carry instructions. It is untrusted data (its `trust` is always
  `untrusted`), instruction-like phrasing is named on the card, and nothing here acts on it.
- `_score` is Elasticsearch's own relevance for one query (BM25 unless the index says otherwise); it is not a
  measure of truth, and it is comparable only within that query.
- An analyser that does not suit the text (stemming, stop words, another language) can still miss what a person
  would find; the mapping check sees fields and types, not analysers.
- Only a server on this machine is reached; a remote cluster needs a tunnel to a local port. Plain http only.

## What it needs

Elasticsearch, which you run yourself on this machine (its licence is Elastic's own; G6 talks to it over HTTP and
never imports, bundles or starts it): `http://127.0.0.1:9200`, or set `G6_ELASTIC_URL` to the local address it
listens on. A server with security on needs its API key in your key file, `~/.g6/.env`, as
`G6_ELASTIC_API_KEY=<key>`; it is sent to that server only. No extra and no model.

## Examples

```g6-example op=mapping
{"index": "handbook"}
```

```g6-example op=search
{"index": "handbook", "query": "parental leave", "fields": ["title", "body"], "filters": {"team": "hr"}, "top_k": 5}
```

```g6-example op=count
{"index": "handbook", "query": "leave", "filters": {"team": "hr"}}
```

```g6-example op=get_doc
{"index": "handbook", "id": "leave-1"}
```

Each is sensitive: over MCP it runs only when the server's owner allows it.

```python
from g6.core import outside
from g6.knowledge.ctx_elastic import elastic

def server(program, method, path, **_):  # your Elasticsearch, in this example: its index holds title and body
    body = b'{"handbook": {"mappings": {"properties": {"title": {"type": "text"}, "body": {"type": "text"}}}}}'
    return outside.ServiceReply(200, {}, body)

try:
    elastic.search("handbook", "parental leave", fields=["content"], caller=server)
except elastic.ElasticError as refused:  # a field the index does not hold: refused, never "no hits"
    assert refused.code == "G6_E_KNOWLEDGE_FIELD_NOT_IN_MAPPING" and "body (text)" in refused.reason
```

## The four open problems

- **Learning signals**: each reply records the fields searched and their types, the server's shard counts, its
  count and its relation, and every refusal's code. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the field check compares the request with the server's own mapping,
  and the partial flag reads the server's shard counts; no model is called and a hit's words change no outcome.
- **Transfer by failure mode**: the checks are bound to `under_retrieval` and hold for every index; the typed
  refusals follow the family's rule that a failure is never read as empty.
- **Enough held-out cases**: Not yet: the tests run against a local server answering as Elasticsearch does, not a
  frozen set of real cluster replies with relevance judgements.
