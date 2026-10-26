---
name: g6-knowledge-chunker
description: Use when a document is to be split into passages for indexing or citation and every passage must be traceable to the exact words of its source, or when a citation an agent holds (offsets and text) must be checked against the document it names before it is quoted.
---

# G6 knowledge chunker

Cuts a document into overlapping passages, each one exactly the document's text at its offsets, with an id bound to
the source it came from. It also checks a citation an agent already holds: are the cited words really at those
offsets of that document, and if not, where are they?

## When to use it

- Before indexing or quoting a document: cut it once, keep each passage's id, offsets and source, and cite those.
- When an agent, a store or another tool hands you a citation (start, end, text or id): `check` it against the
  document before you quote it or build on it.
- When offsets come from somewhere that counts differently (a JavaScript client counting UTF-16 units, a tool that
  normalised line endings first): `check` shows the mismatch and where the text really is.

## When not to use it

- To decide whether a passage is true or supports a claim: that is `g6.grounding` (`check_claim`).
- To find out whether a source still says what it said when it was cut: re-read it and use `knowledge_manifest`'s
  `verify_passage`.
- To read a PDF, a web page or other formats: convert it first (`knowledge_converter`, `knowledge_fetcher`); the
  chunker cuts text.

## Ops

- `chunk`: a document (or a text, whose source is then the caller's) cut into passages of at most `size` units (characters, words or sentences; 200 words by default), `overlap` shared between neighbours (a tenth of `size` by default), each with exact offsets and an id bound to its source, all checked before they are returned.
- `check`: whether each cited passage (its offsets, text, id and source) is exactly what the document holds there, and where its text really is when it is not.

## Reading the result

`chunk` answers `qualified-draft` with `G6_E_KNOWLEDGE_CHUNKED` and `passages`: each has `id`, `source` (address,
SHA-256 of the bytes read, when, how it was captured), `document_sha256` (the hash of the text it was cut from),
`start`, `end`, `text` (exactly `document.text[start:end]`) and `trust`, always `untrusted`. Offsets count characters
(code points) in the text exactly as given; carriage returns count. The same words read from two addresses give two
passages, each citing its own; the id changes if the offsets move.

`check` answers `qualified-draft` with `G6_E_KNOWLEDGE_SPANS_MATCH` when every citation matches, or
`blocked-escalated` with `G6_E_KNOWLEDGE_SPAN_MISMATCH`: each entry of `citations` lists its `problems` and
`found_at` (where its text first occurs, or `null`).

Refusals, each `blocked-escalated`: `G6_E_KNOWLEDGE_NO_TEXT` (nothing to cut), `G6_E_KNOWLEDGE_CHUNK_INPUT` (an
overlap not smaller than the size, a document and a text both given), `G6_E_KNOWLEDGE_CHUNK_TOO_MANY` (more passages
than `max_passages`, or passages holding more than four times the text's characters in all, refused before any is
cut), `G6_E_KNOWLEDGE_TOKENIZER` (token windows without a tokenizer, or one that failed or returned
bad spans), `G6_E_KNOWLEDGE_SPAN_MISMATCH` from `chunk` (it cut a passage that failed its own check: nothing is
returned), and `G6_E_KNOWLEDGE_NO_CITATIONS`.

## Alone and composed

Alone, over MCP: `g6_invoke("knowledge_chunker", "chunk", {"text": "...", "uri": "notes.md", "unit": "sentences",
"size": 3, "overlap": 1})`, or with `"document": {...}` (a document as `knowledge_fetcher` or `knowledge_converter`
returns it) in place of the text. `g6_invoke("knowledge_chunker", "check", {"document": {...}, "cited": [{"start":
..., "end": ..., "text": "..."}]})`; a passage as `chunk` returned it is a citation too.

From Python: `from g6.knowledge.knowledge_chunker import chunk, span_problems`, then `chunk(document, unit="words",
size=200)` (a list of `Passage`, or `ChunkError` with its code). Token windows need a tokenizer, a function
from the text to ordered `(start, end)` spans: `chunk(document, unit="tokens", tokenizer=my_tokenizer)` or
`KnowledgeChunkerBlock(tokenizer=my_tokenizer)`.

Composed: `knowledge_fetcher` or `knowledge_converter` gives the document; `knowledge_manifest` records the passages
and later re-checks each against the source as read again; `Passage.to_grounding()` hands a passage to
`g6.grounding.check_claim`.

## Failure modes it catches

- `provenance_missing` (a claim or citation that cannot be traced to the passage it came from): every passage is a
  slice of the text as given, with an id bound to the source's address and bytes, the text's hash and the offsets,
  and is checked (slice, id, source, unit edges, size) before it is returned. `check` catches a citation whose offsets
  are off, even by one character (UTF-16 counting after an emoji, line endings normalised first), whose text or id
  differs, or which cites another source or another version of the text.

## Blind spots

- Sentence ends are found by punctuation and blank lines, so abbreviations ("e.g.") end a sentence too, and text with
  no punctuation is one sentence per paragraph.
- Character windows can cut a word in two; word windows count runs of non-space characters, not tokens a model uses.
- The hashes show the text is the same, not that it is true or current; a source that changes without being re-read
  is not seen here.
- `check` compares a citation with the document you give it; a document that was itself altered before you gave it
  is not detected.
- It declares no failure mode of its own use.

## What it needs

Nothing outside G6: pure Python with pydantic. No model, no network, nothing written to disk. A tokenizer for token
windows is the caller's own.

## Examples

```g6-example op=chunk
{"text": "Refunds are issued within 14 days. Shipping is free. Returns need a receipt.", "uri": "policy.txt", "unit": "sentences", "size": 2, "overlap": 1}
```

The reply is `qualified-draft` with two passages: `0:52` (the first two sentences) and `35:76` (the last two), each
the text at its offsets.

```g6-example op=check
{"text": "Refunds are issued within 14 days. Shipping is free.", "uri": "policy.txt", "cited": [{"start": 36, "end": 53, "text": "Shipping is free."}]}
```

The reply is `blocked-escalated` with `G6_E_KNOWLEDGE_SPAN_MISMATCH`: the citation is one character late, and
`found_at` is `[35, 52]`.

```python
from g6.knowledge._types import Document, Source
from g6.knowledge.knowledge_chunker import chunk, span_problems

text = "Refunds are issued within 14 days.\r\n\r\nShipping is free."
doc = Document(text=text, source=Source.of("https://example.org/policy", text, provenance="fetched"))
passages = chunk(doc, unit="sentences", size=1, overlap=0)
assert [p.text for p in passages] == ["Refunds are issued within 14 days.", "Shipping is free."]
second = passages[1]
assert text[second.start:second.end] == second.text  # offsets index the text as given, carriage returns too
assert span_problems(doc, start=second.start - 2, end=second.end - 2, text=second.text)  # counted without CRs: caught
```

## The four open problems

- **Learning signals**: each reply records the unit, size, overlap, passage count and the document's hash, and
  `check` records each mismatch with where the text really was. Not yet: nothing learns from them.
- **Evaluators the model cannot influence**: the cut and the check are fixed code over the text and its hashes; no
  model is called, and the words of the text never change the result (an instruction-like phrasing is noted in the
  evidence for a person to look at).
- **Transfer by failure mode**: the check is bound to `provenance_missing` and to what every citation has (offsets,
  text, a source), not to one corpus or format.
- **Enough held-out cases**: Not yet: its tests are fixed regressions and a seeded property test over generated texts;
  no corpus of citations from real agents has been measured.