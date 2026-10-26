---
name: g6-ctx-contexthub
description: Use when an agent should answer from a versioned documentation registry the person trusts (context-hub's registry.json format), with each document's date, version and time to live kept, so a copy past its time to live is refused rather than used as current, and notes stay apart from the documents.
---

# G6 documentation registry

Keeps a documentation registry you name, in context-hub's format: `registry.json` and each entry's `DOC.md`. Each
document is kept with when it was read, the version the registry names and a time to live. A copy past its time to
live is refused as stale. Notes on an entry are kept apart from its documents, as a caller's unchecked records.

## When to use it

- A coding agent should read a library's documents from a registry you chose, at the version it recommends, and
  know how old its copy is.
- Several sessions share what was read: the registry is kept under G6's state folder, and `search` ranks its entries.
- An agent wants to leave a note on an entry for later: `note` keeps it beside the document, never inside it.

## When not to use it

- For a single page: `knowledge_fetcher` fetches it.
- To decide whether the registry is official or correct: it vouches only for itself. Choose a registry you trust.
- For documents that must be checked against their source later: cut passages (`knowledge_chunker`) and record them
  (`knowledge_manifest`).

## Ops

- `sync`: sync(registry, name, ttl_hours, content, max_docs): read registry.json at the address or folder you name, and each entry's document, through knowledge_fetcher's guarded fetch; keep each document with when it was read, its version and its time to live (at most 168 hours), and the registry's own version and date. Sensitive.
- `search`: search(name, query, tags, language, top_k): the kept entries that best match, by BM25 over name, tags, description and content, each with its documents' dates and whether each is stale.
- `get`: get(name, id, language, version): a kept document, with its source, version, when it was read and its time to live, and apart from it the notes kept on its entry; a stale one is refused, marked stale.
- `note`: note(name, id, text): keep a note on an entry, apart from its documents, as a caller's unchecked record.
- `registries`: registries(): the kept registries, where each came from, when it was read and how many of its documents are stale.

## Reading the result

- `sync`: `G6_E_KNOWLEDGE_REGISTRY_SYNCED`, or `_SYNCED_PARTIAL` when a document could not be read (each in `failed`,
  with the fetch's outcome and whether an earlier copy was kept) or an entry was left out (`left_out`: no id, no
  document, or a path that would leave the registry). `G6_E_KNOWLEDGE_REGISTRY_UNREADABLE`: `registry.json` could not
  be read, and nothing kept changed. `G6_E_KNOWLEDGE_NO_REGISTRY`: none was named. `registry_version` and
  `registry_updated` are the registry's own version and last-updated date as it states them (`""` when it states
  none). `G6_E_KNOWLEDGE_HUB_TOO_LARGE`: the document that would bring what is kept over 50 million characters
  stopped the reading (it is named), and nothing kept changed.
- `get`: `G6_E_KNOWLEDGE_DOC_FOUND` with `document` (its `source`: the address read and the hash of the bytes),
  `version`, `retrieved_at`, `ttl_hours`, `stale_after`, `age` and `notes`. `G6_E_KNOWLEDGE_DOC_STALE`
  (`blocked-escalated`): past its time to live; the document comes back marked `stale` for a person to decide.
  `_DOC_NOT_KEPT`, `_NO_ENTRY`, `_DOC_NEEDS_LANGUAGE`, `_REGISTRY_NOT_SYNCED`, `_HUB_STORE` (a kept file edited).
- `search`: `G6_E_KNOWLEDGE_REGISTRY_SEARCHED` with `results` (each entry, its `score` on `bm25`, and its kept
  documents' dates and `stale`), or `_REGISTRY_NO_MATCH`.

## Alone and composed

Over MCP, `sync` runs only when the person who runs the server lists it in `G6_MCP_SENSITIVE`:
`g6_invoke("ctx_contexthub", "sync", {"registry": "https://docs.example.org/hub", "name": "vendor", "ttl_hours":
24})`. Then `search`, `get` and `note` work on what is kept: `g6_invoke("ctx_contexthub", "get", {"name": "vendor",
"id": "acme/api"})`.

From Python: `from g6.knowledge.ctx_contexthub import hub`; `hub.sync`, `hub.search`, `hub.get` (the document, whether
it is stale and its age), `hub.add_note`, `hub.registries`. A registry is an `http` or `https` address (fetched by
`knowledge_fetcher`'s `fetch` through the component registry) or a folder on this machine (read only inside itself).
A network share or a device path (`\\host\share`, `//host/share`, `\\?\`, `\\.\`) is refused before anything is
opened, since opening one connects to another host: `G6_E_KNOWLEDGE_HUB_INPUT`.

Composed: `knowledge_chunker` cuts a document into passages, `knowledge_indexer` keeps them searchable, and
`g6.grounding` checks a claim against them.

## Failure modes it catches

- `stale_context_used` (outdated or unofficial material used as if it were current): each document is kept with its
  own read time and time to live and refused once past it; a document not read again keeps its earlier date, so it
  goes stale on its own clock however recently the registry itself was read.

## Blind spots

- `prompt_injection`: a document, a note and a registry's descriptions are outside text and can carry instructions.
  They are untrusted data, instruction-like phrasing in a note is named in the evidence, and nothing here acts on it.
- A registry vouches only for itself: nothing here checks a publisher, a signature or whether a document is
  official.
- The time to live is yours, not the publisher's: a page can change within it, and a version string is the
  registry's claim. It is at most a week (168 hours), because a library can release more than once a month and a
  longer guess lets a copy fall a release or more behind before it is read again.
- Only each language's recommended version is kept.

## What it needs

Network access to the registry you name (through `knowledge_fetcher`, standard library), or a folder on this machine.
No key, no outside program, no extra and no model. What is kept goes to `$G6_HOME/knowledge/registries/`.

## Examples

```g6-example op=sync
{"registry": "https://docs.example.org/hub", "name": "vendor", "ttl_hours": 24}
```

Sensitive: over MCP it runs only when the server's owner allows it.

```g6-example op=registries
{}
```

```g6-example op=search
{"name": "vendor", "query": "rate limits", "tags": ["api"]}
```

```g6-example op=get
{"name": "vendor", "id": "acme/api", "language": "python"}
```

```g6-example op=note
{"name": "vendor", "id": "acme/api", "text": "The 2.1 limits apply from November."}
```

Until a registry named `vendor` is synced, `search`, `get` and `note` answer `G6_E_KNOWLEDGE_REGISTRY_NOT_SYNCED`.

```python
import json, tempfile
from pathlib import Path
from g6.knowledge.ctx_contexthub import hub

root = Path(tempfile.mkdtemp())
(root / "acme").mkdir()
(root / "acme" / "DOC.md").write_text("# Acme\n\nRequests are limited to 100 a minute.")
entry = {"id": "acme/api", "name": "Acme API", "description": "Rate limits", "tags": ["api"], "path": "acme"}
(root / "registry.json").write_text(json.dumps({"docs": [entry]}))
hub.sync("example", str(root), ttl_hours=1)
document, stale, age = hub.get("example", "acme/api")
assert not stale and "100 a minute" in document["document"]["text"] and document["ttl_hours"] == 1
```

## The four open problems

- **Learning signals**: each sync records what was read, what failed with the fetch's outcome and what was left
  out; each get records the document's age. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: staleness is the kept read time plus the time to live against the
  clock, and a kept document is checked against its hash when read; no model is called.
- **Transfer by failure mode**: the per-document time to live is bound to `stale_context_used` and holds for every
  registry kept.
- **Enough held-out cases**: Not yet: the tests use fixtures and a transport double, not a frozen set of real
  registries whose documents changed.
