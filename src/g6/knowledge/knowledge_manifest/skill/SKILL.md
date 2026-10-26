---
name: g6-knowledge-manifest
description: Use when the passages an agent cites must be kept with their sources, and before an answer that quotes one is relied on, to check the passage against its source as read again, so a citation to text that has since changed, moved or gone is caught rather than repeated.
---

# G6 knowledge manifest

Keeps what an agent read and the passages it cut from it, and checks a cited passage against its source as read
again. A passage that still matches passes as a qualified draft; one whose source changed, could not be read again,
or that cannot be compared at all, does not.

## When to use it

- After cutting a document into passages (`knowledge_chunker`): `record` them, so each id can be looked up later.
- Before an answer, a report or another agent relies on a citation: re-read its source (`knowledge_fetcher`, or your
  own reader) and `verify_passage` it.
- When a cited page or file may have changed since it was read: the verdict says whether the cited words are still
  there, moved, or gone.

## When not to use it

- To decide whether a passage supports a claim or is true: `g6.grounding` (`check_claim`).
- To judge how old a source is: `g6.grounding.check_staleness` ages a source by its own date; this checks change, not
  age.
- As tamper evidence against someone who can write the manifest file: its hashes are unkeyed, so they catch a stray
  edit, not a careful forger.
- To search passages: that is the index stage's job.

## Ops

- `record`: keep a document and its passages in the manifest under G6's state folder; only passages that are exact spans of the document are kept.
- `get`: a recorded passage and its document's details, checked against what was recorded with them.
- `verify_passage`: compare a cited passage (recorded, or given) with its source as read again: `matches`, `changed` (with where the words are now), `source_unavailable` or `unverifiable`.

## Reading the result

`verify_passage`'s `outcome`:

- `matches` (`qualified-draft`, `G6_E_KNOWLEDGE_MATCHES`): the text re-read is the text the passage was cut from (its
  SHA-256 recomputed here) and the passage is its span. If the source's bytes changed but its text did not, the
  reason says so. A re-read is a document read after the passage's source was (its `retrieved_at` later, to the
  microsecond); the evidence's `reread` records its source as your claim, not as something observed here.
- `changed` (`blocked-escalated`, `G6_E_KNOWLEDGE_CHANGED`): the text is different. `span_intact` says whether the
  cited words are still at the same offsets; `found_at` is where they are now, or `null` when they are gone.
- `source_unavailable` (`blocked-escalated`, `G6_E_KNOWLEDGE_SOURCE_UNAVAILABLE`): re-reading failed (from Python,
  your re-read function raised).
- `unverifiable` (`blocked-escalated`, `G6_E_KNOWLEDGE_UNVERIFIABLE`): nothing was compared: no passage named, an id
  not recorded, nothing re-read, no fresh read (the very document the passage was cut from, or one read no later), a
  document from another address, or a passage that is not the span it names.

`record` answers `G6_E_KNOWLEDGE_RECORDED` with the passage ids, or refuses with `G6_E_KNOWLEDGE_SPAN_MISMATCH` (a
passage not cut from this document: nothing is kept) or `G6_E_KNOWLEDGE_NO_DOCUMENT`. `get` answers
`G6_E_KNOWLEDGE_FOUND`, or `G6_E_KNOWLEDGE_NOT_RECORDED`. `G6_E_KNOWLEDGE_STORE` is a manifest that cannot be
opened, or a record edited after it was kept, which is never served.

## Alone and composed

Alone, over MCP: `g6_invoke("knowledge_manifest", "record", {"document": {...}, "passages": [...]})`, then
`g6_invoke("knowledge_manifest", "verify_passage", {"passage_id": "...", "document": {...}})` with the document as you
read it again. The manifest lives at `$G6_HOME/knowledge/manifest.db`; MCP cannot move it.

From Python: `from g6.knowledge.knowledge_manifest import Store, verify_passage`. `Store(path)` opens another file;
`verify_passage(passage, reread)` takes the re-read document or a function from a `Source` to one, which is how a
re-read that fails becomes `source_unavailable`.

Composed: `knowledge_fetcher` or `knowledge_converter` gives the document, `knowledge_chunker` cuts the passages,
this keeps and re-checks them, and `g6.grounding.check_claim` judges what a matching passage supports.

## Failure modes it catches

- `stale_citation` (a citation to text that has since changed): `verify_passage` compares the cited passage with
  the source as read again (the recomputed hash of its text, then the words at the passage's offsets) and passes only
  an exact match; a changed, moved or missing passage, a failed re-read and anything it could not compare all fail
  closed.

## Blind spots

- Over MCP it compares the document you give it: what the source says now is what your re-read returned, and when it
  was read is the time you give.
- A source that changes and changes back between reads, or a page that serves different text to different readers,
  is not seen.
- The hashes are unkeyed: someone who can rewrite every row consistently can forge a record.
- A converter change (another PDF reader) changes the text and so reads as `changed`, though the source did not.
- It declares no failure mode of its own use.

## What it needs

Nothing outside G6: Python's own SQLite with pydantic. It writes only `$G6_HOME/knowledge/manifest.db`; no model, no
network.

## Examples

```g6-example op=record
{"document": {"text": "Refunds are issued within 14 days.", "source": {"uri": "https://example.org/policy", "sha256": "ff07d2ea9d568b03e9728035735fad8055ac401f27b10dd57a02217c46028ca4", "retrieved_at": "2026-10-21T09:30:00Z", "provenance": "fetched"}}, "passages": [{"id": "7974f9c8c262d55a21512edbe787dcb92293ceb074646a841cc079cf1463b9ea", "source": {"uri": "https://example.org/policy", "sha256": "ff07d2ea9d568b03e9728035735fad8055ac401f27b10dd57a02217c46028ca4", "retrieved_at": "2026-10-21T09:30:00Z", "provenance": "fetched"}, "document_sha256": "ff07d2ea9d568b03e9728035735fad8055ac401f27b10dd57a02217c46028ca4", "start": 0, "end": 34, "text": "Refunds are issued within 14 days."}]}
```

The reply is `qualified-draft` with one passage id.

```g6-example op=get
{"passage_id": "7974f9c8c262d55a21512edbe787dcb92293ceb074646a841cc079cf1463b9ea"}
```

The reply is the passage as recorded, or `G6_E_KNOWLEDGE_NOT_RECORDED` if it was not.

```g6-example op=verify_passage
{"passage_id": "7974f9c8c262d55a21512edbe787dcb92293ceb074646a841cc079cf1463b9ea", "document": {"text": "Refunds are issued within 30 days.", "source": {"uri": "https://example.org/policy", "sha256": "62f074ed15e9f7a4ce41c34dd55aae7679feeb33e7ec11f73c3baeb24fdb97c0", "retrieved_at": "2026-10-22T09:30:00Z", "provenance": "fetched"}}}
```

The page now says 30 days: the reply is `blocked-escalated` with `outcome` `changed` (or `unverifiable`, if the
passage was not recorded first).

```python
from g6.knowledge._types import Document, Source
from g6.knowledge.knowledge_manifest import verify_passage

def read(uri, text):
    return Document(text=text, source=Source.of(uri, text, provenance="fetched"))

url = "https://example.org/policy"
cited = read(url, "Refunds are issued within 14 days. Shipping is free.").passage(35, 52)
assert verify_passage(cited, read(url, "Refunds are issued within 14 days. Shipping is free.")).outcome == "matches"
moved = verify_passage(cited, read(url, "Prices include VAT. Refunds are issued within 14 days. Shipping is free."))
assert moved.outcome == "changed" and moved.found_at == [55, 72]

def offline(source):
    raise OSError(f"{source.uri} did not answer")

assert verify_passage(cited, offline).outcome == "source_unavailable"
```

## The four open problems

- **Learning signals**: each verdict records its outcome, the hashes compared and where the words were found; the
  manifest keeps every source read and when. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the verdict is a comparison of hashes and slices in fixed code; no model
  is called and the text's own words change nothing.
- **Transfer by failure mode**: the check is bound to `stale_citation` and to what every passage has (a source, the
  hash of its text, offsets), whatever the format or source.
- **Enough held-out cases**: Not yet: its tests are fixed regressions; it has not been measured on real pages that
  changed between reads.