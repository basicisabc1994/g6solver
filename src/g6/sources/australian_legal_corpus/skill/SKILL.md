---
name: g6-australian-legal-corpus
description: Use when an agent must find what Australian legislation or a court decision says, in the text itself with its citation, jurisdiction, link and collection date, rather than from memory; it searches a local copy of the Open Australian Legal Corpus, refuses Victoria, the ACT, the Northern Territory and any jurisdiction it lacks instead of answering with another's law, and says every time that the text is a dated snapshot, not current law and not legal advice.
---

# G6 australian_legal_corpus

Searches the Open Australian Legal Corpus locally (Umar Butler, Isaacus; version 7.1.0, collected 10 March 2025):
about 232,000 acts, regulations, bills and decisions from the Commonwealth, New South Wales, Queensland, Western
Australia, South Australia, Tasmania and Norfolk Island. G6 redistributes none. On request, it downloads the pinned
Hugging Face file, checks its SHA-256 and indexes it; passages retain their source and date.

## When to use it

- To quote Australian legislation or judgments with citations and links.
- To find provisions by words within or across covered jurisdictions.
- To ground an agent's legal claim: `search`, then `get` and read the full document.

## When not to use it

- For current law: check the official register before relying on this snapshot.
- For Victoria, the ACT or the Northern Territory: absent and refused.
- For legal advice or case judgements: it returns text, never opinions.
- Without capacity for the 9.4 GB download or 19 GB free disk: the file is required.

## Ops

- `status`: what is on disk: the pinned revision, the corpus file and when its size and SHA-256 were checked, the index's documents, filters and progress, and the next step; never reaches the network.
- `fetch`: download the pinned `corpus.jsonl` into G6's state folder, its size and SHA-256 checked as it streams; a dry run unless `dry_run` is false, naming the URL, revision, size, licence and free space needed. Sensitive: it reaches the network and writes about 9.4 GB.
- `register`: record a `corpus.jsonl` you downloaded yourself (`path`, absolute), where it is and never copied, if its size and SHA-256 equal the pin.
- `build`: index passages of the file in SQLite FTS5 (`types`, `jurisdictions`, `max_records`, `max_seconds`); legislation only unless asked; bounded per call and resumable: call it again until `complete` is true.
- `search`: the passages with your words (`query`, `jurisdiction`, `types`, `k`, `match` all or any), ranked by BM25, each with its citation, jurisdiction, type, link, version id, date and collection date.
- `get`: one document by `version_id`, at most 100,000 characters per call (`start` reads on), with the same fields.
- `remove`: delete the index (`what` index) or the index, the manifest and the file if G6 downloaded it (`what` all); a dry run unless `dry_run` is false. Sensitive: it deletes stored data.

## Reading the result

Publisher's pin: revision `ef45e3fec41a960919a31149eee6dab9aa39f725` (2026-03-07), `corpus.jsonl`, 9,401,179,433
bytes; SHA-256 in `status.dataset`. Fetch requests 18.8 GB free: the file plus an equal index allowance (estimated;
legislation alone needs far less). Today's action gate holds every download for a person
(`G6_E_LEGAL_CORPUS_GATE_HELD`), returning `person_can_run`: `hf download
isaacus/open-australian-legal-corpus corpus.jsonl --repo-type dataset --revision <revision> --local-dir <folder>`
(huggingface_hub; rerun after interruption), then `register`. Only the exact pinned file is accepted; nothing else
is disclosed about other files. UNC/device paths are refused before filesystem access (`G6_E_LEGAL_CORPUS_INPUT`):
give an absolute local path. Non-sensitive `register`/`build` execute immediately within their bounds and reject
explicit `dry_run`, true or false. Sensitive `remove` stays dry-run first; G6's local deletion bypasses the outward
action gate.

Fetch's four-hour deadline spans redirects and bodies, including empty bodies; hops receive remaining time only.
Replacement follows size/hash checks, file/stream closure and a final deadline check. Failure preserves the
existing destination and attempts each cleanup independently.

`build` resumes from its recorded byte offset, bounded by `max_records` (default 20,000; maximum 250,000) and
`max_seconds` (default 120; maximum 900). The file takes a dozen or more calls. Types: `primary_legislation`,
`secondary_legislation`, `bill`, `decision`; the first two are defaults. Decisions comprise 189,216 of 232,560
documents: opt in through `types` and budget most index space for them. `skipped` counts lines over 32 MiB, invalid
JSON, missing fields, types/jurisdictions/sources outside the README's sets and repeated `version_id`; none stops
the build. Filters cannot change (`G6_E_LEGAL_CORPUS_FILTERS_DIFFER`): remove with `what` index to restart.
Under 1 GB free or on disk-full errors (`G6_E_LEGAL_CORPUS_DISK_SHORT`), free space and resume. Missing FTS5
(`G6_E_LEGAL_CORPUS_FTS5_UNAVAILABLE`) requires a Python whose sqlite3 includes FTS5. Other read/write failures
(`G6_E_LEGAL_CORPUS_STORAGE_FAILED`): check corpus readability and G6 state writability, then retry. All are
`blocked-escalated`; committed batches and offsets survive, unfinished batches are retried.

`search` passes your words as quoted terms, never as search syntax. `jurisdiction` takes a code
(`new_south_wales`), a name or an abbreviation (`NSW`, `Cth`, `Qld`); left empty, every indexed one is searched, and a
query that names Victoria, the ACT or the Northern Territory is refused until you name a covered jurisdiction.
Refusals, all `blocked-escalated`: `G6_E_LEGAL_CORPUS_JURISDICTION_NOT_COVERED` (named, with what is covered),
`G6_E_LEGAL_CORPUS_NO_INDEX` (fetch or register, then build), `G6_E_LEGAL_CORPUS_NOT_INDEXED` (the index was built
without that jurisdiction or type, so a search there would find nothing for the wrong reason),
`G6_E_LEGAL_CORPUS_SEARCH_TOO_BROAD` (over 10 seconds: add words), `G6_E_LEGAL_CORPUS_NO_CORPUS`,
`G6_E_LEGAL_CORPUS_NOT_PINNED_FILE`, `G6_E_LEGAL_CORPUS_FETCH_FAILED` (with the `G6_E_HTTP_*` code; nothing kept),
`G6_E_LEGAL_CORPUS_INDEX_MISMATCH`, `G6_E_LEGAL_CORPUS_NOT_FOUND` and `G6_E_LEGAL_CORPUS_INPUT`.

Answers are `qualified-draft` at best. `G6_E_LEGAL_CORPUS_PASSAGES` and `G6_E_LEGAL_CORPUS_DOCUMENT` say on the card
and in a `corpus_snapshot` evidence entry: when each text was collected (`when_scraped`), that it is not current law,
that amendments, repeals and later decisions are not included, where to confirm it (its `url` and the official
register), that it is not legal advice, and the attribution. A bill is marked as proposed law. Each hit carries its
source's terms: the legislation is CC BY 4.0; the Federal Court's, High Court's and NSW Caselaw's documents have terms
of their own (unaltered reproduction; the High Court's for personal, non-commercial or in-house use). An index that is
not complete says so. `G6_E_LEGAL_CORPUS_NO_MATCH` is not evidence that the law says nothing.

## Alone and composed

Over MCP: `g6_invoke("australian_legal_corpus", "status", {})`, then `fetch` (a dry run; sensitive, so it runs only
when the server's owner lists it in `G6_MCP_SENSITIVE`) or `register`, then `build` until complete, then
`g6_invoke("australian_legal_corpus", "search", {"query": "unfair dismissal small business", "jurisdiction":
"Cth"})`.

From Python: `AustralianLegalCorpusBlock(pin=..., client=..., gate=..., folder=...)`; `pin` is a `Pin` (another
revision or a test corpus), `client` a `g6.core.http.Client` (tests pass a transport double), `gate` stands in for
`g6.action_gating`.

Composed: `g6.grounding` checks a claim against the passages returned; the legal profiles built on this source give
the judgement, after their own review.

## Failure modes it catches

- `answered_from_memory` (a factual question answered from the model's memory instead of a source): with no index,
  or a jurisdiction or type left out of it, it refuses rather than answering empty, and every hit is the source's text
  with its link.
- `stale_context_used` (a dated text used as if current): every passage carries its collection date, and the card says
  it is not current law and that later amendments, repeals and decisions are missing.
- `wrong_jurisdiction` (a law or ruling from another jurisdiction given as the one asked about): a jurisdiction the
  corpus lacks, named or in the query, is refused by name; every hit names its jurisdiction.

## Blind spots

- `prompt_injection`: a document's text can carry instructions. It is untrusted data (instruction-like phrasing is
  named on the card), and nothing here acts on it.
- Lexical search misses a paraphrase: a provision worded differently from your query is not found.
- A snapshot misses amendments: an act repealed or amended after its collection date reads as it was then.
- Coverage gaps: no Victoria, ACT or Northern Territory; decisions only from the High Court, the Federal Court and NSW
  Caselaw; no state tribunals.
- A query naming "Victoria" as a street or a person is refused too until a covered jurisdiction is named.
- The file's hash is checked once, at fetch or register; later only its size and modification time are compared.
- A mapped network drive letter looks like a local path and is not detected by the UNC/device check; registration
  can access that network drive. Use a file on a genuinely local drive. Links and junctions can also lead elsewhere,
  and a concurrent link change can race path validation.

## What it needs

The Open Australian Legal Corpus: an online, free dataset (CC BY 4.0, doi:10.57967/hf/2784), downloaded once, about
9.4 GB, by `fetch` or by you with huggingface_hub's `hf` program. About 19 GB of free disk for the file and an index.
No extra, no key, no model: SQLite FTS5 and `g6.core.http` (standard library).

## Examples

```g6-example op=status
{}
```

What is on disk and the next step; nothing is sent.

```g6-example op=search
{"query": "residential tenancy bond", "jurisdiction": "Victoria"}
```

Refused by name (`G6_E_LEGAL_CORPUS_JURISDICTION_NOT_COVERED`): the corpus has no Victorian law, and none other is
given in its place.

```g6-example op=register
{"path": "D:/downloads/corpus.jsonl"}
```

Accepted only when the file there is the pinned one; anything else is refused, with nothing said about it.

```g6-example op=build
{"max_records": 20000, "max_seconds": 60}
```

One bounded step; with no corpus file on record it is refused (`G6_E_LEGAL_CORPUS_NO_CORPUS`) with the next steps.

```g6-example op=get
{"version_id": "from-a-search-hit", "start": 0}
```

With no index it is refused (`G6_E_LEGAL_CORPUS_NO_INDEX`): nothing is answered without one.

```g6-example op=fetch
{}
```

Sensitive: over MCP it runs only when the server's owner allows it; a dry run even then.

```python
from pathlib import Path

from g6.sources.australian_legal_corpus import PINNED, AustralianLegalCorpusBlock, AustralianLegalCorpusInput

def gate(action, key):  # the action gate, in this example: it holds every download for a person
    return {"kind": "action_gate", "decision": "require_human_review", "reason_codes": [], "message": ""}

block = AustralianLegalCorpusBlock(folder=Path.cwd() / "legal-corpus", gate=gate)
out = block.infer(AustralianLegalCorpusInput(op="fetch")).unwrap()   # a dry run: nothing is sent
assert out["would_fetch"]["revision"] == PINNED.revision and out["would_fetch"]["requested"] is False
assert out["person_can_run"].startswith("hf download isaacus/open-australian-legal-corpus corpus.jsonl")
```

## The four open problems

- **Learning signals**: each reply records the words searched, the filters, the index's progress and every skipped
  line by kind. Not yet: which passages an agent relied on is not recorded, and nothing learns from it.
- **Evaluators the model cannot influence**: the publisher's pinned size and SHA-256, the README's value sets and the
  source fields themselves; no model is called, and a document's words change no refusal.
- **Transfer by failure mode**: bound to `wrong_jurisdiction`, `stale_context_used` and `answered_from_memory`; the
  same pin, index and refusals serve any revision of this corpus (`Pin`).
- **Enough held-out cases**: Not yet: the tests use an invented corpus; there is no frozen set of real legal
  questions with known answers.
